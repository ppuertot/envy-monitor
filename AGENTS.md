# AGENTS.md

Guía para agentes de IA (y personas) que trabajen en este repositorio.

## Qué es

`envy` — monitor web de GPUs NVIDIA. Un backend FastAPI muestrea la GPU y
reparte los datos a la web por **SSE**; el frontend (HTML/JS + **uPlot**
vendorizado) dibuja gráficas en vivo y de histórico largo. Los datos salen de
**`nvidia-smi`** o de **NVML** (backend seleccionable) y el histórico se guarda
en **SQLite**.

Principios del proyecto:

- Sin *build step* en el frontend y sin dependencias pesadas.
- Mismo esquema de datos para ambos backends (el frontend no distingue).
- Todo lo posible con la stdlib de Python.

> **Crédito**: la UI y la idea están inspiradas en
> [`congard/nvidia-system-monitor-qt`](https://github.com/congard/nvidia-system-monitor-qt)
> (MIT). Es una reimplementación web independiente, sin código compartido.

## Estructura

| Archivo | Rol |
|---|---|
| `app.py` | FastAPI: `Monitor`, SSE `/api/stream`, `/api/snapshot`, `/api/history` |
| `nvidia.py` | Selector de backend según `ENVY_BACKEND` (`auto`/`nvml`/`smi`) |
| `nvidia_smi.py` | Backend que invoca `nvidia-smi` como subproceso |
| `nvidia_nvml.py` | Backend NVML (`nvidia-ml-py` / `pynvml`) |
| `errors.py` | `MonitorBackendError` común |
| `storage.py` | Histórico en SQLite (WAL), reesampleado y poda por retención |
| `static/` | Frontend sin build: `index.html`, `app.js`, `style.css`, `vendor/` (uPlot) |
| `Dockerfile`, `docker-compose.yml` | Ejecución en contenedor |
| `run.sh` | Arranque directo (acepta puerto como argumento) |
| `README.md`, `SUGERENCIAS.md`, `docs/docker-gpu.md` | Documentación |
| `LICENSE` | Licencia MIT |

## Entorno

- Python 3.10+ (probado en 3.14).
- `nvidia-smi` en el `PATH`.
- Dependencias: `fastapi`, `uvicorn`, `nvidia-ml-py` (esta última opcional).

```bash
pip install -r requirements.txt
```

En esta máquina ya están instaladas (`fastapi`, `uvicorn`, `nvidia-ml-py`,
`playwright` para pruebas del frontend).

## Ejecutar

```bash
./run.sh                 # http://localhost:8000
./run.sh 9000            # otro puerto
ENVY_BACKEND=smi ./run.sh
```

Docker:

```bash
docker compose up -d --build
```

## Variables de entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `ENVY_INTERVAL` | `2` | Segundos entre muestras |
| `ENVY_WINDOW` | `60` | Segundos de histórico en memoria |
| `ENVY_BACKEND` | `auto` | `auto` \| `nvml` \| `smi` |
| `ENVY_DB` | `./envy.db` | Ruta del SQLite |
| `ENVY_RETENTION_DAYS` | `7` | Días de histórico persistido |

## Verificar cambios

No hay suite de tests formal. Al tocar código, verifica manualmente:

1. **Sintaxis**
   ```bash
   python3 -m py_compile app.py storage.py nvidia*.py errors.py
   ```
2. **Backend** (arranca en un puerto libre y consulta):
   ```bash
   python3 -m uvicorn app:app --port 8020 &
   curl -s localhost:8020/api/snapshot | python3 -m json.tool | head
   curl -s "localhost:8020/api/history?seconds=60&points=30"
   ```
   Comprueba `error: null`, `backend` y que `persistence` sea `true`.
3. **Paridad de backends**: compara `nvidia_smi.query_gpus()` con
   `nvidia_nvml.query_gpus()` (deben coincidir salvo por el instante de muestreo).
4. **Frontend** con Chromium headless (Playwright ya está instalado). Captura
   una pantalla y revisa errores de consola:
   ```python
   from playwright.sync_api import sync_playwright
   with sync_playwright() as p:
       b = p.chromium.launch(args=["--no-sandbox"])
       page = b.new_page(viewport={"width": 1180, "height": 900})
       errs = []
       page.on("pageerror", lambda e: errs.append(str(e)))
       page.goto("http://127.0.0.1:8020/")
       page.wait_for_timeout(6000)
       print("errores:", errs)
       b.close()
   ```
5. **Docker**: `docker compose config -q` y, si aplica,
   `docker compose up -d --build` + `docker compose ps` (debe salir `healthy`).

La BD de desarrollo `envy.db` está en `.gitignore`: no la commitees.

## Convenciones

- **Python**: stdlib + FastAPI. Evita añadir dependencias nuevas; si hace falta,
  justifícalo y actualiza `requirements.txt`.
- **Frontend**: JS "vanilla", sin bundler. uPlot va **vendorizado** en
  `static/vendor/` (no CDN, para que funcione sin red). Un único breakpoint
  (`@media (max-width: 640px)`); las gráficas se dimensionan al tamaño real del
  contenedor (`plotHeight(el)` + `ResizeObserver`), no a un `%` del viewport.
- **Idioma**: comentarios, mensajes y UI en **español**; nombres de código en
  inglés.
- **Contrato de datos**: cualquier backend debe devolver el mismo diccionario
  (`index`, `name`, `driver`, `utilization`, `temperature`, `power`, `clocks`,
  `memory`, `fan`). No cambies el esquema sin actualizar los dos backends y el
  frontend.
- **Escrituras de BD**: siempre a través de `Store` (hilo aparte con
  `asyncio.to_thread`), nunca SQL suelto en `app.py`.

## Cosas a tener en cuenta (gotchas)

- **Selección de backend**: `nvidia.py` resuelve el backend **al importar**.
  Si pruebas forzar otro, hazlo en un proceso nuevo (`ENVY_BACKEND=... python`).
- **NVML no da el nombre del proceso**: se lee de `/proc/<pid>/cmdline`; el
  frontend lo acorta a basename.
- **PID namespace en Docker**: sin `pid: host`, la pestaña *Processes* sale
  vacía. Ya está activado en `docker-compose.yml`.
- **Puerto en Docker**: el contenedor escucha fijo en `8000` (`CMD` y
  `HEALTHCHECK`); súbelo/publícalo con `-p` o Compose.
- **Persistencia**: `prune()` se autolimita a una vez por hora; no lo llames en
  bucle. El esquema es aditivo.
- **uPlot**: si actualizas la versión, reemplaza los archivos de `static/vendor/`
  (no uses CDN). Las gráficas usan `paths.spline()` para la curva suave y el
  verde sale de la variable CSS `--accent`.
- **Tema**: el tema efectivo vive en `document.documentElement.dataset.theme`
  (`light`/`dark`); un script en `<head>` lo resuelve antes de pintar y el
  toggle cicla Auto/Claro/Oscuro (se guarda en `localStorage`).
- **Auto-arranque Docker**: el contenedor usa `restart: unless-stopped`
  (arranca al iniciar el equipo; no si lo paras a mano).

## Flujo de trabajo (Git / PR)

- Commits en **español**, atómicos por funcionalidad.
- Para cambios no triviales: **rama de feature + Pull Request + squash merge**
  (es el flujo usado hasta ahora: `pynvml`, `persistence`, `responsive`,
  `dark-theme`, `visual-tweaks`).
- `main` es la rama por defecto; el repo es público y con licencia **MIT**.

## Backlog

Las ideas pendientes viven en [`SUGERENCIAS.md`](SUGERENCIAS.md) (persistencia
de puerto/host, publicar en GHCR, etc.).
