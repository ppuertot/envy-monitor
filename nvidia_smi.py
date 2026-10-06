"""Lectura de métricas NVIDIA vía nvidia-smi.

Toda la información que expone este módulo proviene de `nvidia-smi`
(que a su vez es un wrapper de NVML). No requiere dependencias externas.
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from typing import Any

from errors import MonitorBackendError as NvidiaSmiError
from processes import proc_name


# Campos que pedimos a nvidia-smi. El orden importa: se mapea 1:1 con las
# columnas del CSV de salida.
GPU_FIELDS = [
    "index",
    "name",
    "driver_version",
    "utilization.gpu",
    "utilization.memory",
    "utilization.encoder",
    "utilization.decoder",
    "temperature.gpu",
    "temperature.memory",
    "power.draw",
    "power.limit",
    "clocks.current.graphics",
    "clocks.current.sm",
    "clocks.current.memory",
    "clocks.current.video",
    "memory.total",
    "memory.used",
    "memory.free",
    "fan.speed",
]

# Valores que nvidia-smi usa cuando un dato no está disponible en esa GPU.
_NA_TOKENS = {
    "n/a",
    "[n/a]",
    "[not supported]",
    "[unknown error]",
    "not supported",
    "[insufficient permissions]",
    "",
}

# Fila de la tabla "Processes" de `nvidia-smi`:
# |    0   N/A  N/A            4273      G   /usr/bin/gnome-shell   88MiB |
_PROC_RE = re.compile(
    r"^\|\s*(\d+)\s+(N/A|\d+)\s+(N/A|\d+)\s+(\d+)\s+([CG+]{1,3})\s+(.*?)\s+(\d+)\s*MiB\s*\|"
)


def nvidia_smi_path() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        raise NvidiaSmiError("nvidia-smi no está en el PATH")
    return exe


def _num(value: str | None) -> int | float | None:
    """Convierte un valor de nvidia-smi a int/float, o None si no aplica."""
    if value is None:
        return None
    value = value.strip()
    if value.lower() in _NA_TOKENS:
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    if f.is_integer() and "." not in value:
        return int(f)
    return f


def _run(args: list[str], timeout: float = 10.0) -> str:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        raise NvidiaSmiError(f"nvidia-smi excedió el timeout de {timeout}s") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise NvidiaSmiError(msg or f"nvidia-smi terminó con código {proc.returncode}")
    return proc.stdout


def _normalize(rec: dict[str, str]) -> dict[str, Any]:
    return {
        "index": int(rec["index"]),
        "name": rec["name"].strip(),
        "driver": rec["driver_version"].strip(),
        "utilization": {
            "gpu": _num(rec["utilization.gpu"]),
            "memory": _num(rec["utilization.memory"]),
            "encoder": _num(rec["utilization.encoder"]),
            "decoder": _num(rec["utilization.decoder"]),
        },
        "temperature": {
            "gpu": _num(rec["temperature.gpu"]),
            "memory": _num(rec["temperature.memory"]),
        },
        "power": {
            "draw": _num(rec["power.draw"]),
            "limit": _num(rec["power.limit"]),
        },
        "clocks": {
            "graphics": _num(rec["clocks.current.graphics"]),
            "sm": _num(rec["clocks.current.sm"]),
            "memory": _num(rec["clocks.current.memory"]),
            "video": _num(rec["clocks.current.video"]),
        },
        "memory": {
            "total": _num(rec["memory.total"]),
            "used": _num(rec["memory.used"]),
            "free": _num(rec["memory.free"]),
        },
        "fan": _num(rec["fan.speed"]),
    }


def query_gpus() -> list[dict[str, Any]]:
    """Devuelve una lista de GPUs con sus métricas actuales."""
    exe = nvidia_smi_path()
    out = _run(
        [
            exe,
            f"--query-gpu={','.join(GPU_FIELDS)}",
            "--format=csv,noheader,nounits",
        ]
    )
    gpus: list[dict[str, Any]] = []
    for row in csv.reader(out.splitlines()):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != len(GPU_FIELDS):
            # Fila inesperada: la ignoramos en vez de romper el monitor.
            continue
        rec = dict(zip(GPU_FIELDS, [c.strip() for c in row]))
        gpus.append(_normalize(rec))
    return gpus


def query_processes() -> list[dict[str, Any]]:
    """Devuelve los procesos usando GPU parseando la salida completa de nvidia-smi.

    Incluye procesos gráficos (G) y de cómputo (C), que `--query-compute-apps`
    no reporta.
    """
    exe = nvidia_smi_path()
    out = _run([exe])
    procs: list[dict[str, Any]] = []
    for line in out.splitlines():
        m = _PROC_RE.match(line)
        if not m:
            continue
        gpu, _gi, _ci, pid, ptype, name, mem = m.groups()
        procs.append(
            {
                "gpu": int(gpu),
                "pid": int(pid),
                "type": ptype,
                "name": proc_name(int(pid), fallback=name.strip()),
                "memory": int(mem),
            }
        )
    procs.sort(key=lambda p: p["memory"], reverse=True)
    return procs
