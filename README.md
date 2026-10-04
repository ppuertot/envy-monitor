# envy — NVIDIA System Monitor (web)

Versión web del monitor de GPU. La información proviene de **`nvidia-smi`** o de
**NVML** (backend seleccionable, sin binarios nativos). Replica las pestañas
**Utilization** y **Processes**, las gráficas en vivo (60 s con paso de 2 s) y
además guarda histórico en **SQLite** para ver rangos largos (hasta 7 días).

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

## Requisitos

**Uso directo (Python):**

- Python 3.10+ (probado con 3.14)
- `nvidia-smi` accesible en el `PATH`
- Dependencias: `fastapi`, `uvicorn`, `nvidia-ml-py` (esta última opcional)

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
docker run --rm --gpus all -p 8000:8000 -v envy-data:/data envy
```

o con Docker Compose:

```bash
docker compose up -d --build
```

En ambos casos abre `http://localhost:8000`.

> El volumen `/data` guarda el histórico persistido. En Compose es el named
> volume `envy-data`; sin él, el histórico se pierde al recrear el contenedor.

> Requisitos para que Docker vea la GPU (driver, NVIDIA Container Toolkit,
> `--gpus all`, capacidades): ver [`docs/docker-gpu.md`](docs/docker-gpu.md).

> Los procesos que aparecen en la pestaña **Processes** dependen del PID
> namespace: por defecto el contenedor **no** ve los procesos del host y la
> lista sale vacía. Para verlos, añade `--pid=host` (o `pid: host` en Compose).

### Variables de entorno

| Variable        | Por defecto | Descripción                              |
|-----------------|-------------|------------------------------------------|
| `ENVY_INTERVAL` | `2`         | Segundos entre muestras (paso de la gráfica). |
| `ENVY_WINDOW`   | `60`        | Segundos de histórico en memoria (gráfica en vivo). |
| `ENVY_BACKEND`  | `auto`      | Backend de datos: `auto`, `nvml` o `smi`. |
| `ENVY_DB`       | `./envy.db` | Ruta del archivo SQLite del histórico.   |
| `ENVY_RETENTION_DAYS` | `7`   | Días de histórico persistido (poda horaria). |

### Backends de datos

La lectura de la GPU se puede hacer de dos formas, con el **mismo esquema** de
datos (el frontend no cambia):

- **`nvml`** (`nvidia_nvml.py`): NVML en proceso vía `nvidia-ml-py`/`pynvml`.
  Sin fork ni parseo de texto, llamadas en microsegundos.
- **`nvidia-smi`** (`nvidia_smi.py`): invoca `nvidia-smi` como subproceso. Sin
  dependencias extra; ~30 ms por muestra (irrelevante a intervalos de 1-2 s).

`nvidia.py` selecciona el backend según `ENVY_BACKEND`:

- `auto` (por defecto): usa NVML si está instalado, si no `nvidia-smi`.
- `nvml`: fuerza NVML (falla con mensaje claro si falta `nvidia-ml-py`).
- `smi`: fuerza `nvidia-smi`.

El backend activo aparece en el pie de la web ("Datos: …") y en `/api/snapshot`.

> En NVML el nombre del proceso se obtiene de `/proc/<pid>/cmdline` (la API
> estándar no lo incluye); por eso puede venir completo y el frontend lo acorta.

## Persistencia del histórico

Además del búfer en memoria de 60 s, cada muestra se guarda en **SQLite** (una
fila por GPU y muestra). En la web, el selector **Rango** permite elegir
`60 s (live)`, `5 min`, `1 h`, `24 h` o `7 d`. Para rangos largos se consulta
`/api/history`, que **reesamplea** por intervalos (agrupa por *bucket*) en vez de
devolver millones de puntos.

- Ruta del archivo: `ENVY_DB` (por defecto `./envy.db`).
- Retención: `ENVY_RETENTION_DAYS` días (por defecto 7); se poda una vez por hora.
- En Docker vive en el volumen `/data` (`envy-data` en Compose).
- Si no hay persistencia, `/api/history` devuelve series vacías y la web sigue
  funcionando en modo en vivo.

```sql
CREATE TABLE samples (
  ts REAL, gpu INTEGER, name TEXT,
  gpu_util INTEGER, mem_io INTEGER, mem_used INTEGER, mem_total INTEGER,
  temp INTEGER, power REAL, clk_graphics INTEGER, clk_memory INTEGER
);
```

## Arquitectura

```
nvidia-smi / NVML ──▶ Monitor (cada 2 s) ──┬─▶ SSE /api/stream ──▶ navegador (live)
                                           └─▶ SQLite ──▶ /api/history ──▶ navegador (5m–7d)
```

- **`nvidia.py`** — selecciona el backend (`nvml` o `nvidia-smi`).
- **`nvidia_smi.py`** — ejecuta `nvidia-smi --query-gpu=... --format=csv,noheader,nounits`
  y parsea la tabla de procesos de la salida completa (incluye procesos gráficos
  `G`, que `--query-compute-apps` no reporta).
- **`nvidia_nvml.py`** — misma información vía NVML (`nvidia-ml-py`), uniendo
  procesos de cómputo y gráficos por PID.
- **`storage.py`** — histórico en SQLite con consultas reesampleadas y poda por
  retención.
- **`app.py`** — FastAPI. Un `Monitor` asíncrono muestrea la GPU en un hilo y
  reparte las muestras a los suscriptores por **Server-Sent Events**.
- **`static/`** — front-end sin build step. Gráficas con **uPlot** (vendorizado).

### Endpoints

| Endpoint        | Descripción                                   |
|-----------------|-----------------------------------------------|
| `GET /`         | Página del monitor.                           |
| `GET /api/snapshot` | Estado actual + histórico en memoria (JSON). |
| `GET /api/stream`   | Flujo SSE: `snapshot` inicial + `sample`s. |
| `GET /api/history`  | Histórico reesampleado (`seconds`, `points`, `gpu`). |

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

## Licencia

[MIT](LICENSE) © 2026 Pedro Puerto
