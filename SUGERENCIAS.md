# Sugerencias / próximos pasos

Ideas pendientes para `envy`. No implementadas todavía.

## Despliegue / operación

- **Autenticación básica**: usuario/contraseña (o token) en el backend FastAPI,
  útil si se expone fuera de `localhost`.
- **Servicio `systemd`**: unidad de usuario/sistema para arrancar `envy` al
  inicio y reiniciarlo si falla.
- **Contenedor Docker**: ~~imagen mínima con acceso a `nvidia-smi`~~ **hecho**
  (`Dockerfile` + `docker-compose.yml`, usa `--gpus all`).
- **Publicar imagen en GHCR** (`ghcr.io`): **pendiente**. El token actual de `gh`
  no tiene scopes de paquetes (`user/packages` → 403); requiere
  `gh auth refresh -s write:packages,read:packages,delete:packages`. Después:
  `docker tag … ghcr.io/ppuertot/envy-monitor:latest && docker push …`.
  Alternativa **ya verificada**: Docker Hub (`docker.io/ppuertot/envy-monitor`),
  las credenciales actuales sí permiten `push`.
- **Puerto/host parametrizables y unificados**: hoy el puerto está disperso
  (`run.sh [puerto]` por argumento; `app.py`, `Dockerfile` y `docker-compose.yml`
  con `8000` fijo). Unificar en variables (p. ej. `ENVY_PORT` / `ENVY_HOST`)
  respetadas por las tres vías, incluidos el `CMD` y el `HEALTHCHECK` del
  contenedor y el mapeo de `ports` en Compose.
- **Configuración por archivo/CLI**: además de `ENVY_INTERVAL` / `ENVY_WINDOW`,
  permitir elegir puerto, bind, GPUs a mostrar y tema.

## Funcionalidad

- **Selección de GPU**: elegir qué GPU(s) monitorear cuando hay varias.
- **Exportar métricas**: endpoint Prometheus (`/metrics`) y/o exportación CSV
  del histórico.
- **Alertas**: umbrales de temperatura/potencia/memoria con aviso en la UI.
- **Más series en las gráficas**: encoder/decoder, uso real de VRAM vs.
  controlador de memoria, potencia y temperatura superpuestas.
- **Detalle de procesos**: filtro/búsqueda, agrupar por usuario, matar proceso
  (requiere permisos), historial de memoria por proceso.
- **Persistencia**: ~~guardar histórico más allá de la ventana en memoria
  (SQLite o similar) para ver tendencias largas~~ **hecho** (`storage.py`,
  `/api/history`, selector de rango en la web, retención y volumen Docker).

## Rendimiento / arquitectura

- **Migrar a NVML en proceso** (`pynvml` / `nvidia-ml-py`) en vez de invocar
  `nvidia-smi` como subproceso: ~~menos overhead, sin parseo de texto~~
  **hecho** en la rama `pynvml` (backend `nvml` seleccionable con
  `ENVY_BACKEND`).
- **WebSocket** en lugar de SSE si se necesita enviar configuración desde el
  cliente (elegir intervalo, GPU, pausar).
- **Multi-nodo**: si crece a varias máquinas, evaluar Prometheus +
  `dcgm-exporter` + Grafana en lugar de una app por host.

## UI / UX

- **Menús `File` / `Help`** funcionales (exportar, acerca de, atajos) en la
  barra superior, hoy son solo texto.
- **Tema oscuro**: ~~y respeto por `prefers-color-scheme`~~ **hecho**
  (automático según el sistema + toggle manual Auto/Claro/Oscuro).
- **Responsive**: adaptar la tabla y las gráficas a pantallas pequeñas.
- **Indicador de reconexión** más visible (tiempo desde la última muestra).
