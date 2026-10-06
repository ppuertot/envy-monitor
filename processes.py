"""Resolución del nombre de los procesos que usan la GPU.

Por defecto expone **solo el ejecutable** (`/proc/<pid>/comm`), sin argumentos,
para no filtrar líneas de comandos ajenas a través del API. Con
``ENVY_SHOW_CMDLINE=1`` devuelve la **línea de comandos completa**.
"""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}

SHOW_CMDLINE = os.environ.get("ENVY_SHOW_CMDLINE", "").strip().lower() in _TRUTHY


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _basename(text: str | None) -> str:
    first = (text or "").strip().split(" ", 1)[0]
    return first.rsplit("/", 1)[-1] if first else ""


def proc_name(pid: int, fallback: str | None = None) -> str:
    """Nombre a mostrar/exponer para `pid`.

    - ``ENVY_SHOW_CMDLINE`` activo → línea de comandos completa.
    - por defecto → ``/proc/<pid>/comm`` (solo el ejecutable).
    - si no se puede leer ``/proc`` → basename del `fallback` (o ``pid <n>``).
    """
    if SHOW_CMDLINE:
        raw = _read_bytes(f"/proc/{pid}/cmdline")
        if raw:
            cmd = raw.replace(b"\x00", b" ").strip().decode("utf-8", "replace")
            if cmd:
                return cmd

    comm = _read_text(f"/proc/{pid}/comm")
    if comm and comm.strip():
        return comm.strip()

    base = _basename(fallback)
    if base:
        return base
    return f"pid {pid}"
