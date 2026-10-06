"""envy — monitor web de GPUs NVIDIA.

Backend FastAPI que muestrea `nvidia-smi` periódicamente y emite los datos a
los navegadores por Server-Sent Events (SSE).

Ejecutar:
    uvicorn app:app --host 0.0.0.0 --port 8000

Variables de entorno:
    ENVY_INTERVAL         segundos entre muestras (por defecto 2)
    ENVY_WINDOW           segundos de histórico en memoria (por defecto 60)
    ENVY_DB               ruta del SQLite (por defecto ./envy.db)
    ENVY_RETENTION_DAYS   días de histórico persistido (por defecto 7)
    ENVY_TOKEN            token opcional para /api/* (por defecto: sin auth)
    ENVY_MAX_CLIENTS      tope de clientes SSE (por defecto 20; 0 = sin tope)
    ENVY_SHOW_CMDLINE     exponer la línea de comandos completa (1 lo activa)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from nvidia import BACKEND, query_gpus, query_processes
from storage import Store

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

INTERVAL = float(os.environ.get("ENVY_INTERVAL", "2"))
WINDOW = float(os.environ.get("ENVY_WINDOW", "60"))
DB_PATH = os.environ.get("ENVY_DB", str(BASE_DIR / "envy.db"))
RETENTION_DAYS = float(os.environ.get("ENVY_RETENTION_DAYS", "7"))
TOKEN = (os.environ.get("ENVY_TOKEN") or "").strip() or None
_MAX = os.environ.get("ENVY_MAX_CLIENTS", "20").strip()
MAX_CLIENTS = int(_MAX) if _MAX.isdigit() else 20  # 0 = sin tope

# Rutas que no se registran en el access log (ruido y healthcheck).
_QUIET_PATHS = ("/static/", "/favicon.ico", "/healthz")
_TOKEN_RE = re.compile(r"([?&]token=)[^&\s]*")


class _AccessLogFilter(logging.Filter):
    """Quita ruido y evita registrar el token del query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        path = next(
            (a for a in args if isinstance(a, str) and a.startswith("/")), None
        )
        if path is not None:
            if path.startswith(_QUIET_PATHS):
                return False
            if "token=" in path:
                record.args = tuple(
                    _TOKEN_RE.sub(r"\1***", a) if isinstance(a, str) else a
                    for a in args
                )
        return True


def _install_access_log_filter() -> None:
    logging.getLogger("uvicorn.access").addFilter(_AccessLogFilter())


class Monitor:
    """Muestrea la GPU y reparte los datos a los suscriptores SSE."""

    def __init__(self, interval: float = 2.0, window: float = 60.0) -> None:
        self.interval = interval
        self.window = window
        self.max_points = max(2, int(round(window / interval)))
        self.history: deque[dict[str, Any]] = deque(maxlen=self.max_points)
        self.latest: dict[str, Any] | None = None
        self.processes: list[dict[str, Any]] = []
        self.error: str | None = None
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.store: Store | None = None
        self._tick = 0

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            await self.poll()
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.2, self.interval - elapsed))

    async def poll(self) -> None:
        try:
            gpus = await asyncio.to_thread(query_gpus)
            # Los procesos cambian lento: refrescamos cada 2 muestras.
            if self._tick % 2 == 0:
                self.processes = await asyncio.to_thread(query_processes)
            self.error = None
        except Exception as exc:  # noqa: BLE001 — queremos mostrar cualquier fallo
            gpus = []
            self.error = str(exc)

        self._tick += 1
        ts = time.time()

        sample: dict[str, Any] = {
            "ts": ts,
            "gpus": gpus,
            "processes": self.processes,
            "error": self.error,
            "interval": self.interval,
            "window": self.max_points * self.interval,
            "backend": BACKEND,
        }
        self.latest = sample

        point = {
            "ts": ts,
            "gpus": [
                {
                    "index": g["index"],
                    "name": g["name"],
                    "gpu": g["utilization"]["gpu"],
                    "io": g["utilization"]["memory"],
                    "used": g["memory"]["used"],
                    "total": g["memory"]["total"],
                    "temp": g["temperature"]["gpu"],
                    "power": g["power"]["draw"],
                    "clk_graphics": g["clocks"]["graphics"],
                    "clk_memory": g["clocks"]["memory"],
                }
                for g in gpus
            ],
        }
        self.history.append(point)

        if self.store is not None:
            await asyncio.to_thread(self._persist, point)

        for queue in list(self.subscribers):
            try:
                queue.put_nowait(sample)
            except asyncio.QueueFull:
                pass

    def _persist(self, point: dict[str, Any]) -> None:
        """Escritura + poda; se ejecuta en un hilo aparte."""
        assert self.store is not None
        self.store.write(point)
        self.store.prune()

    def snapshot_payload(self) -> dict[str, Any]:
        return {
            "history": list(self.history),
            "sample": self.latest,
            "interval": self.interval,
            "window": self.max_points * self.interval,
            "backend": BACKEND,
            "persistence": self.store is not None,
            "retention_days": RETENTION_DAYS,
            "auth": TOKEN is not None,
        }


monitor = Monitor(INTERVAL, WINDOW)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _install_access_log_filter()
    monitor.store = Store(DB_PATH, RETENTION_DAYS)
    task = asyncio.create_task(monitor.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        monitor.store.close()


app = FastAPI(title="envy", lifespan=lifespan)


@app.middleware("http")
async def _auth(request: Request, call_next):
    """Si ENVY_TOKEN está definido, protege /api/* (cabecera o ?token=)."""
    if TOKEN and request.url.path.startswith("/api/"):
        header = request.headers.get("authorization", "")
        supplied = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not supplied:
            supplied = request.query_params.get("token", "")
        if not supplied or not secrets.compare_digest(supplied, TOKEN):
            return JSONResponse({"error": "no autorizado"}, status_code=401)
    return await call_next(request)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Chequeo de salud sin auth: 503 si la última muestra es vieja."""
    latest = monitor.latest
    if latest is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    age = time.time() - float(latest["ts"])
    limit = max(5.0, monitor.interval * 3)
    ok = age <= limit
    return JSONResponse(
        {
            "status": "ok" if ok else "stale",
            "age": round(age, 1),
            "backend": BACKEND,
        },
        status_code=200 if ok else 503,
    )


@app.get("/api/snapshot")
async def api_snapshot() -> dict[str, Any]:
    return monitor.snapshot_payload()


@app.get("/api/history")
async def api_history(
    seconds: float = 300.0, points: int = 300, gpu: int | None = None
) -> dict[str, Any]:
    """Histórico persistido, reesampleado a ~`points` buckets.

    Si se omite `gpu`, devuelve una serie por cada GPU del rango.
    """
    now = time.time()
    start = now - max(1.0, seconds)
    points = max(2, min(points, 3000))
    step = max(1.0, (now - start) / points)

    if monitor.store is None:
        series: dict[str, Any] = {}
    elif gpu is None:
        series = await asyncio.to_thread(
            monitor.store.history_range, start, now, step
        )
    else:
        series = {str(gpu): await asyncio.to_thread(
            monitor.store.history, gpu, start, now, step
        )}

    return {"from": start, "to": now, "step": step, "series": series}


@app.get("/api/stream")
async def api_stream(request: Request):
    if MAX_CLIENTS and len(monitor.subscribers) >= MAX_CLIENTS:
        return JSONResponse(
            {"error": "demasiados clientes"}, status_code=503
        )

    async def generator():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        monitor.subscribers.add(queue)
        try:
            yield _sse("snapshot", monitor.snapshot_payload())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    sample = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse("sample", sample)
        finally:
            monitor.subscribers.discard(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
