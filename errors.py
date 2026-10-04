"""Errores comunes a los backends de métricas (nvidia-smi y NVML)."""

from __future__ import annotations


class MonitorBackendError(RuntimeError):
    """No se pudieron obtener (o inicializar) las métricas de la GPU."""
