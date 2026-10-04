"""Selección del backend de métricas NVIDIA.

Expone `query_gpus()` y `query_processes()` con el mismo contrato que usan
`app.py` y el frontend, pero delega en uno de estos backends:

- ``nvidia_nvml``  → NVML en proceso (requiere `nvidia-ml-py`).
- ``nvidia_smi``   → invoca `nvidia-smi` como subproceso (sin dependencias).

El backend se elige con la variable de entorno ``ENVY_BACKEND``:

- ``auto`` (por defecto): NVML si está disponible, si no `nvidia-smi`.
- ``nvml``: fuerza NVML (falla si no está instalado).
- ``smi``: fuerza `nvidia-smi`.
"""

from __future__ import annotations

import os

# Se sobrescriben en `_select()`.
query_gpus = None
query_processes = None
BACKEND = ""


def _select() -> None:
    global query_gpus, query_processes, BACKEND

    requested = os.environ.get("ENVY_BACKEND", "auto").strip().lower()

    if requested in ("auto", "nvml", "pynvml"):
        try:
            from nvidia_nvml import query_gpus as _qg, query_processes as _qp

            query_gpus, query_processes, BACKEND = _qg, _qp, "nvml"
            return
        except ImportError:
            if requested in ("nvml", "pynvml"):
                raise RuntimeError(
                    "ENVY_BACKEND=nvml pero no se pudo importar pynvml. "
                    "Instala 'nvidia-ml-py'."
                ) from None

    from nvidia_smi import query_gpus as _qg, query_processes as _qp

    query_gpus, query_processes, BACKEND = _qg, _qp, "nvidia-smi"


_select()
