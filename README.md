# envy — NVIDIA System Monitor (web)

Versión web del monitor de GPU. La información proviene de **`nvidia-smi`** o de
**NVML** (backend seleccionable, sin binarios nativos). Replica las pestañas
**Utilization** y **Processes**, las gráficas en vivo (60 s con paso de 2 s) y
además guarda histórico en **SQLite** para ver rangos largos (hasta 7 días).
Incluye **tema claro/oscuro** y **diseño responsive** (escritorio y móvil).

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

**Usar la imagen publicada** (no hace falta el código fuente ni construir nada):

```bash
docker pull ppuertot/envy-monitor:latest

docker run -d --name envy --gpus all --pid=host --restart unless-stopped \
  -p 8000:8000 -v envy-data:/data ppuertot/envy-monitor:latest
```

Imagen: [`docker.io/ppuertot/envy-monitor`](https://hub.docker.com/r/ppuertot/envy-monitor)

**En servidores, con Compose y la imagen publicada:**
[`docker-compose.server.yml`](docker-compose.server.yml) usa la imagen de
Docker Hub (sin build), publica el puerto **8080** y reutiliza el volumen
`envy-data` como externo (así `docker compose down -v` no borra el histórico).
Se copia a un directorio propio del servidor:

```bash
mkdir -p ~/envy && cp docker-compose.server.yml ~/envy/docker-compose.yml
docker volume create envy-data      # solo la primera vez
cd ~/envy && docker compose up -d   # abre http://<servidor>:8080

# actualizar a la última imagen
docker compose pull && docker compose up -d
```

**Construir desde el repositorio:**

**Opción A — Docker Compose (recomendado):**

```bash
docker compose up -d --build
```

**Opción B — `docker run` (sin `docker-compose.yml`):** equivalente a Compose.

```bash
# 1) construir la imagen
docker build -t envy:latest .

# 2) arrancar el contenedor
docker run -d --name envy --gpus all --pid=host --restart unless-stopped \
  -p 8000:8000 -v envy-data:/data \
  -e ENVY_DB=/data/envy.db -e ENVY_INTERVAL=2 -e ENVY_WINDOW=60 -e ENVY_RETENTION_DAYS=7 \
  envy:latest
```

En todos los casos abre `http://localhost:8000`.

> Los `-e ENVY_*` son opcionales: el `Dockerfile` ya fija `ENVY_DB=/data/envy.db` y
> el resto tiene esos mismos valores por defecto (ver *Variables de entorno*).
> La versión mínima sería:
> ```bash
> docker run -d --name envy --gpus all --pid=host -p 8000:8000 \
>   -v envy-data:/data --restart unless-stopped envy:latest
> ```
> El volumen `envy-data` se crea solo si no existe.
> Para ver los procesos del host en la pestaña *Processes* hace falta `--pid=host`
> (ya incluido arriba).

> El volumen `/data` guarda el histórico persistido. En Compose es el named
> volume `envy-data`; sin él, el histórico se pierde al recrear el contenedor.

> En Compose el contenedor usa `restart: unless-stopped`: **arranca solo** al
> iniciar el equipo (con Docker habilitado) y al recrear el contenedor. Si lo
> paras a mano, permanece detenido hasta que lo vuelvas a arrancar.

> Requisitos para que Docker vea la GPU (driver, NVIDIA Container Toolkit,
> `--gpus all`, capacidades): ver [`docs/docker-gpu.md`](docs/docker-gpu.md).

> **Publicar una nueva versión** (mantenedores):
> ```bash
> docker build -t ppuertot/envy-monitor:latest .
> docker push ppuertot/envy-monitor:latest
> ```

### Variables de entorno

| Variable        | Por defecto | Descripción                              |
|-----------------|-------------|------------------------------------------|
| `ENVY_INTERVAL` | `2`         | Segundos entre muestras (paso de la gráfica). |
| `ENVY_WINDOW`   | `60`        | Segundos de histórico en memoria (gráfica en vivo). |
| `ENVY_BACKEND`  | `auto`      | Backend de datos: `auto`, `nvml` o `smi`. |
| `ENVY_DB`       | `./envy.db` | Ruta del archivo SQLite del histórico.   |
| `ENVY_RETENTION_DAYS` | `7`   | Días de histórico persistido (poda horaria). |
| `ENVY_TOKEN`    | —           | Token opcional para proteger `/api/*` (ver *Seguridad*). |
| `ENVY_MAX_CLIENTS` | `20`     | Tope de clientes SSE simultáneos (`0` = sin tope). |
| `ENVY_SHOW_CMDLINE` | —       | `1` expone la línea de comandos completa de los procesos. |

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

> El nombre del proceso se lee de `/proc/<pid>`. Por defecto se expone **solo el
> ejecutable** (`comm`); con `ENVY_SHOW_CMDLINE=1` se devuelve la línea de
> comandos completa (el frontend la acorta a *basename*).

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

## Seguridad

Pensado para **red interna o VPN**: el puerto publica la web **sin cifrado**, así
que **no lo expongas a Internet** sin añadir autenticación delante (proxy inverso)
o el token de abajo.

- **`ENVY_TOKEN` (opcional).** Si se define, protege `/api/*`. Se acepta en la
  cabecera `Authorization: Bearer <token>` o como `?token=<token>` (necesario
  para `EventSource`). Abre la web una vez con
  `http://host:8080/?token=<token>` y queda guardado en el navegador
  (`localStorage`) para las siguientes visitas. Sin definir, no hay auth
  (comportamiento anterior).
- **`ENVY_MAX_CLIENTS`** (por defecto `20`, `0` = sin tope): limita las
  conexiones **SSE** simultáneas.
- **`ENVY_SHOW_CMDLINE`** (por defecto apagado): por defecto el API solo expone
  el **nombre del ejecutable** de cada proceso; con `=1` expone la **línea de
  comandos completa** (puede filtrar argumentos de otros programas).
- **`pid: host`** es necesario para resolver los nombres vía `/proc/<pid>`; es la
  concesión más grande del contenedor.

### Cómo establecer el token

Genera uno fuerte:

```bash
openssl rand -hex 32
```

- **Directo (Python):** `ENVY_TOKEN="mi-token" ./run.sh`
- **`docker run`:** añade `-e ENVY_TOKEN="mi-token"`.
- **Docker Compose:** define `ENVY_TOKEN=mi-token` en un archivo **`.env`** junto
  al compose (está en `.gitignore`, no se sube al repo); el compose ya lo lee con
  `ENVY_TOKEN: "${ENVY_TOKEN:-}"`.
- **Servidores** (`docker-compose.server.yml`): igual, vía `.env`.

Después abre la web **una vez** con `http://host:8080/?token=mi-token`; el token
queda guardado en el navegador. Sin `ENVY_TOKEN`, no hay autenticación.

Si no quieres publicar el puerto, usa `-p 127.0.0.1:8000:8000` y accede por
**túnel SSH** en lugar de exponerlo a la red.

## Notas

- Si el backend (`nvidia-smi` o NVML) falla o no está disponible, la UI muestra
  el error en un banner y sigue reconectándose.
- Para varias GPU se genera un bloque de gráficas + estadísticas por cada una.
- Para producción en flota (varios nodos) conviene Prometheus + `dcgm-exporter`
  + Grafana; este proyecto está pensado para una máquina.
- Tema claro/oscuro: sigue `prefers-color-scheme` del sistema y hay un toggle
  manual (Auto / Claro / Oscuro) que se recuerda en el navegador.
- **Responsive**: un único breakpoint en **640px** (título compacto, pestañas
  con *wrap*, tabla de procesos en tarjetas). Las gráficas se dimensionan al
  ancho/alto real de su contenedor, no a un tamaño fijo.

## Créditos

La interfaz y la idea de este monitor están inspiradas en
[**nvidia-system-monitor-qt**](https://github.com/congard/nvidia-system-monitor-qt)
de [@congard](https://github.com/congard) ("Task Manager for Linux for Nvidia
graphics cards", licencia MIT).

`envy` es una reimplementación **web independiente**: no comparte código con ese
proyecto; solo toma su diseño y su enfoque como referencia.

## Licencia

[MIT](LICENSE) © 2026 Pedro Puerto
