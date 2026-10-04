# envy — NVIDIA System Monitor (web)

Versión web del monitor de GPU. Toda la información proviene de **`nvidia-smi`**
(sin dependencias de NVML ni de binarios nativos). Replica las pestañas
**Utilization** y **Processes**, las gráficas de 60 s con paso de 2 s y la
curva verde del original.

## Requisitos

**Uso directo (Python):**

- Python 3.10+ (probado con 3.14)
- `nvidia-smi` accesible en el `PATH`
- Dependencias: `fastapi`, `uvicorn`

```bash
pip install -r requirements.txt
```

**Uso con Docker:**

- Driver NVIDIA en el host (que exista `nvidia-smi`)
- Docker + `nvidia-container-toolkit` (`--gpus all`)
- No hace falta Python en el host.

## Ejecutar

### Directo (Python)

```bash
./run.sh            # http://localhost:8000
./run.sh 9000       # otro puerto
```

o directamente:

```bash
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker build -t envy .
docker run --rm --gpus all -p 8000:8000 envy
```

o con Docker Compose:

```bash
docker compose up -d --build
```

En ambos casos abre `http://localhost:8000`.

> Los procesos que aparecen en la pestaña **Processes** dependen del PID
> namespace: por defecto el contenedor **no** ve los procesos del host y la
> lista sale vacía. Para verlos, añade `--pid=host` (o `pid: host` en Compose).

### Variables de entorno

| Variable        | Por defecto | Descripción                              |
|-----------------|-------------|------------------------------------------|
| `ENVY_INTERVAL` | `2`         | Segundos entre muestras (paso de la gráfica). |
| `ENVY_WINDOW`   | `60`        | Segundos de histórico mostrados.         |

## Arquitectura

```
nvidia-smi ──▶ Monitor (muestrea cada 2 s) ──▶ SSE /api/stream ──▶ navegador (uPlot)
```

- **`nvidia.py`** — ejecuta `nvidia-smi --query-gpu=... --format=csv,noheader,nounits`
  y parsea la tabla de procesos de la salida completa (incluye procesos gráficos
  `G`, que `--query-compute-apps` no reporta).
- **`app.py`** — FastAPI. Un `Monitor` asíncrono muestrea la GPU en un hilo y
  reparte las muestras a los suscriptores por **Server-Sent Events**.
- **`static/`** — front-end sin build step. Gráficas con **uPlot** (vendorizado).

### Endpoints

| Endpoint        | Descripción                                   |
|-----------------|-----------------------------------------------|
| `GET /`         | Página del monitor.                           |
| `GET /api/snapshot` | Estado actual + histórico (JSON).         |
| `GET /api/stream`   | Flujo SSE: `snapshot` inicial + `sample`s. |

## Mapeo a nvidia-smi

| UI                        | Campo `nvidia-smi`            |
|---------------------------|-------------------------------|
| GPU · Utilization         | `utilization.gpu`             |
| GPU · Temperature         | `temperature.gpu`             |
| GPU · Power               | `power.draw`                  |
| GPU · Frequency           | `clocks.current.graphics`     |
| Mem · Total / Used / Free | `memory.total/used/free`      |
| Mem · Utilization         | `memory.used / memory.total` (calculado) |
| Mem · IO Utilization      | `utilization.memory`          |
| Mem · Frequency           | `clocks.current.memory`       |
| `(mín / med / máx)`       | calculado sobre la ventana    |

## Notas

- Si `nvidia-smi` falla o no existe, la UI muestra el error en un banner y
  sigue reconectándose.
- Para varias GPU se genera un bloque de gráficas + estadísticas por cada una.
- Para producción en flota (varios nodos) conviene Prometheus + `dcgm-exporter`
  + Grafana; este proyecto está pensado para una máquina.
