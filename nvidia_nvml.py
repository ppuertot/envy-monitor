"""Lectura de métricas NVIDIA vía NVML (paquete `nvidia-ml-py`, módulo `pynvml`).

Devuelve exactamente el mismo esquema que `nvidia_smi.py`, de modo que el resto
de la aplicación (y el frontend) no cambia.

Ventajas frente a invocar `nvidia-smi`: sin fork ni parseo de texto, llamadas en
microsegundos. El precio es una dependencia adicional y el ciclo de vida
`nvmlInit`/`nvmlShutdown`.
"""

from __future__ import annotations

import os
import threading
from typing import Any

try:
    import pynvml
except ImportError as exc:  # pragma: no cover - depende del entorno
    raise ImportError(
        "El backend NVML requiere 'nvidia-ml-py' (pip install nvidia-ml-py)"
    ) from exc

from errors import MonitorBackendError as NvmlError

_BYTES_PER_MIB = 1024 * 1024

_init_lock = threading.Lock()
_initialized = False


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        try:
            pynvml.nvmlInit()
        except pynvml.NVMLError as exc:
            raise NvmlError(f"No se pudo inicializar NVML: {exc}") from exc
        _initialized = True


def _text(value: Any) -> str:
    """NVML devuelve bytes en versiones viejas y str en las nuevas."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _safe(fn, default=None):
    """Ejecuta una consulta NVML devolviendo `default` si no está soportada."""
    try:
        return fn()
    except pynvml.NVMLError:
        return default


def _mib(nbytes: int | None) -> int | None:
    if nbytes is None:
        return None
    return round(nbytes / _BYTES_PER_MIB)


def _proc_name(pid: int) -> str:
    """NVML no devuelve el nombre del proceso: lo leemos de /proc."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
        if raw:
            cmd = raw.replace(b"\x00", b" ").strip().decode("utf-8", "replace")
            if cmd:
                return cmd
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="replace") as fh:
            comm = fh.read().strip()
        if comm:
            return comm
    except OSError:
        pass
    return f"pid {pid}"


def _device(handle, index: int, driver: str) -> dict[str, Any]:
    util = _safe(lambda: pynvml.nvmlDeviceGetUtilizationRates(handle))
    encoder = _safe(lambda: pynvml.nvmlDeviceGetEncoderUtilization(handle))
    decoder = _safe(lambda: pynvml.nvmlDeviceGetDecoderUtilization(handle))
    mem = _safe(lambda: pynvml.nvmlDeviceGetMemoryInfo(handle))
    power_mw = _safe(lambda: pynvml.nvmlDeviceGetPowerUsage(handle))
    limit_mw = _safe(lambda: pynvml.nvmlDeviceGetEnforcedPowerLimit(handle))
    fan = _safe(lambda: pynvml.nvmlDeviceGetFanSpeed(handle))

    return {
        "index": index,
        "name": _text(pynvml.nvmlDeviceGetName(handle)),
        "driver": driver,
        "utilization": {
            "gpu": getattr(util, "gpu", None),
            "memory": getattr(util, "memory", None),
            "encoder": encoder[0] if encoder else None,
            "decoder": decoder[0] if decoder else None,
        },
        "temperature": {
            "gpu": _safe(lambda: pynvml.nvmlDeviceGetTemperature(
                handle, pynvml.NVML_TEMPERATURE_GPU
            )),
            # NVML no expone la temperatura de memoria en la API estándar.
            "memory": None,
        },
        "power": {
            "draw": round(power_mw / 1000, 2) if power_mw is not None else None,
            "limit": round(limit_mw / 1000, 2) if limit_mw is not None else None,
        },
        "clocks": {
            "graphics": _safe(lambda: pynvml.nvmlDeviceGetClockInfo(
                handle, pynvml.NVML_CLOCK_GRAPHICS
            )),
            "sm": _safe(lambda: pynvml.nvmlDeviceGetClockInfo(
                handle, pynvml.NVML_CLOCK_SM
            )),
            "memory": _safe(lambda: pynvml.nvmlDeviceGetClockInfo(
                handle, pynvml.NVML_CLOCK_MEM
            )),
            "video": _safe(lambda: pynvml.nvmlDeviceGetClockInfo(
                handle, pynvml.NVML_CLOCK_VIDEO
            )),
        },
        "memory": {
            "total": _mib(mem.total) if mem else None,
            "used": _mib(mem.used) if mem else None,
            "free": _mib(mem.free) if mem else None,
        },
        "fan": fan,
    }


def query_gpus() -> list[dict[str, Any]]:
    """Devuelve una lista de GPUs con sus métricas actuales (vía NVML)."""
    _ensure_init()
    try:
        driver = _text(pynvml.nvmlSystemGetDriverVersion())
        count = pynvml.nvmlDeviceGetCount()
    except pynvml.NVMLError as exc:
        raise NvmlError(str(exc)) from exc

    gpus: list[dict[str, Any]] = []
    for slot in range(count):
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(slot)
            index = pynvml.nvmlDeviceGetIndex(handle)
        except pynvml.NVMLError as exc:
            raise NvmlError(str(exc)) from exc
        gpus.append(_device(handle, index, driver))
    return gpus


def query_processes() -> list[dict[str, Any]]:
    """Procesos usando GPU: cómputo (C) y gráficos (G), unidos por PID."""
    _ensure_init()
    try:
        count = pynvml.nvmlDeviceGetCount()
    except pynvml.NVMLError as exc:
        raise NvmlError(str(exc)) from exc

    entries: dict[tuple[int, int], dict[str, Any]] = {}
    for slot in range(count):
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(slot)
            index = pynvml.nvmlDeviceGetIndex(handle)
        except pynvml.NVMLError:
            index = slot

        for kind, getter in (
            ("C", pynvml.nvmlDeviceGetComputeRunningProcesses),
            ("G", pynvml.nvmlDeviceGetGraphicsRunningProcesses),
        ):
            try:
                infos = getter(handle)
            except pynvml.NVMLError:
                continue
            for info in infos:
                pid = info.pid
                mem_bytes = getattr(info, "usedGpuMemory", None)
                mem_mib = _mib(mem_bytes) if (mem_bytes or 0) >= 0 else None
                entry = entries.get((index, pid))
                if entry is None:
                    entry = {
                        "gpu": index,
                        "pid": pid,
                        "type": "",
                        "name": _proc_name(pid),
                        "memory": 0,
                    }
                    entries[(index, pid)] = entry
                if kind not in entry["type"]:
                    entry["type"] += kind
                if mem_mib and mem_mib > entry["memory"]:
                    entry["memory"] = mem_mib

    procs = list(entries.values())
    for proc in procs:
        if proc["type"] == "CG":
            proc["type"] = "C+G"
    procs.sort(key=lambda p: (p["memory"], p["pid"]), reverse=True)
    return procs
