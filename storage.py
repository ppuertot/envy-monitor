"""Persistencia del histórico de métricas en SQLite.

Guarda una fila por GPU y muestra, y expone consultas reesampleadas
(agrupadas por intervalos) para dibujar tendencias largas sin devolver
millones de puntos.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts           REAL    NOT NULL,
    gpu          INTEGER NOT NULL,
    name         TEXT,
    gpu_util     INTEGER,
    mem_io       INTEGER,
    mem_used     INTEGER,
    mem_total    INTEGER,
    temp         INTEGER,
    power        REAL,
    clk_graphics INTEGER,
    clk_memory   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples_gpu_ts ON samples (gpu, ts);
"""

_INSERT = """
INSERT INTO samples
    (ts, gpu, name, gpu_util, mem_io, mem_used, mem_total,
     temp, power, clk_graphics, clk_memory)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# Reesampleado: una fila por "bucket" temporal.
_HISTORY = """
SELECT CAST((ts - ?) / ? AS INTEGER) AS bucket,
       AVG(gpu_util)                          AS gpu_util,
       AVG(mem_io)                            AS mem_io,
       AVG(temp)                              AS temp,
       AVG(power)                             AS power,
       100.0 * AVG(mem_used) / NULLIF(AVG(mem_total), 0) AS used_pct
FROM samples
WHERE gpu = ? AND ts >= ? AND ts <= ?
GROUP BY bucket
ORDER BY bucket
"""

_PRUNE_INTERVAL = 3600.0  # no comprobar más de una vez por hora


class Store:
    """Envoltorio mínimo de SQLite para el histórico de muestras."""

    def __init__(self, path: str | Path, retention_days: float = 7.0) -> None:
        self.path = str(path)
        self.retention_seconds = max(0.0, retention_days) * 86400
        self._lock = threading.Lock()
        self._last_prune = 0.0
        Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- escritura -----------------------------------------------------
    def write(self, point: dict[str, Any]) -> None:
        ts = point["ts"]
        rows = [
            (
                ts,
                gpu.get("index"),
                gpu.get("name"),
                gpu.get("gpu"),
                gpu.get("io"),
                gpu.get("used"),
                gpu.get("total"),
                gpu.get("temp"),
                gpu.get("power"),
                gpu.get("clk_graphics"),
                gpu.get("clk_memory"),
            )
            for gpu in point.get("gpus", [])
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(_INSERT, rows)
            self._conn.commit()

    def prune(self, now: float | None = None) -> int:
        """Borra muestras más viejas que la retención (máx. 1 vez/hora)."""
        now = now or time.time()
        if now - self._last_prune < _PRUNE_INTERVAL:
            return 0
        self._last_prune = now
        if self.retention_seconds <= 0:
            return 0
        cutoff = now - self.retention_seconds
        with self._lock:
            cur = self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    # -- lectura -------------------------------------------------------
    def gpus(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT gpu, name FROM samples WHERE ts = "
                "(SELECT MAX(ts) FROM samples s2 WHERE s2.gpu = samples.gpu) "
                "GROUP BY gpu ORDER BY gpu"
            )
            return [{"index": row[0], "name": row[1]} for row in cur.fetchall()]

    def history(
        self, gpu: int, start: float, end: float, step: float
    ) -> list[dict[str, Any]]:
        step = max(1.0, float(step))
        with self._lock:
            cur = self._conn.execute(_HISTORY, (start, step, gpu, start, end))
            rows = cur.fetchall()
        series: list[dict[str, Any]] = []
        for bucket, gpu_util, mem_io, temp, power, used_pct in rows:
            series.append(
                {
                    "ts": start + (bucket + 0.5) * step,
                    "gpu": None if gpu_util is None else round(gpu_util),
                    "io": None if mem_io is None else round(mem_io),
                    "temp": None if temp is None else round(temp),
                    "power": None if power is None else round(power, 2),
                    "used": None if used_pct is None else round(used_pct, 2),
                }
            )
        return series

    def history_range(
        self, start: float, end: float, step: float
    ) -> dict[str, list[dict[str, Any]]]:
        """Histórico de todas las GPU presentes en el rango."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT gpu FROM samples WHERE ts >= ? AND ts <= ? ORDER BY gpu",
                (start, end),
            )
            indexes = [row[0] for row in cur.fetchall()]
        return {str(i): self.history(i, start, end, step) for i in indexes}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
