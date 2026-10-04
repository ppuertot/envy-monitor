"""envy — monitor web de GPUs NVIDIA.

Backend FastAPI que muestrea `nvidia-smi` periódicamente y emite los datos a
los navegadores por Server-Sent Events (SSE).

Ejecutar:
    uvicorn app:app --host 0.0.0.0 --port 8000

Variables de entorno:
    ENVY_INTERVAL   segundos entre muestras (por defecto 2)
    ENVY_WINDOW     segundos de histórico en las gráficas (por defecto 60)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from nvidia import BACKEND, query_gpus, query_processes

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

INTERVAL = float(os.environ.get("ENVY_INTERVAL", "2"))
WINDOW = float(os.environ.get("ENVY_WINDOW", "60"))


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
                }
                for g in gpus
            ],
        }
        self.history.append(point)

        for queue in list(self.subscribers):
            try:
                queue.put_nowait(sample)
            except asyncio.QueueFull:
                pass

    def snapshot_payload(self) -> dict[str, Any]:
        return {
            "history": list(self.history),
            "sample": self.latest,
            "interval": self.interval,
            "window": self.max_points * self.interval,
            "backend": BACKEND,
        }


monitor = Monitor(INTERVAL, WINDOW)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(monitor.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="envy", lifespan=lifespan)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/snapshot")
async def api_snapshot() -> dict[str, Any]:
    return monitor.snapshot_payload()


@app.get("/api/stream")
async def api_stream(request: Request) -> StreamingResponse:
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
