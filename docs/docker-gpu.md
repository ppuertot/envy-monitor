# Requisitos de GPU en Docker

Qué hace falta para que un contenedor pueda ver la GPU (y por tanto ejecutar
`nvidia-smi` / NVML). Resumen operativo para desplegar `envy` con Docker.

## 1. Driver NVIDIA en el host

Lo primero es que la GPU funcione **fuera** de Docker:

```bash
nvidia-smi        # debe listar la GPU
```

`envy` no necesita el CUDA Toolkit: solo el driver (para `nvidia-smi`/NVML).

## 2. NVIDIA Container Toolkit

Instalación según la distro:

- **Fedora / RHEL**:
  ```bash
  sudo dnf install nvidia-container-toolkit
  ```
- **Ubuntu / Debian**: añadir el repositorio de NVIDIA y luego
  ```bash
  sudo apt-get install nvidia-container-toolkit
  ```

## 3. Configurar el runtime de Docker (una sola vez)

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Esto añade el runtime `nvidia` en `/etc/docker/daemon.json`:

```json
{
  "runtimes": {
    "nvidia": {
      "args": [],
      "path": "nvidia-container-runtime"
    }
  }
}
```

Comprobar que Docker lo reconoce:

```bash
docker info | grep -A1 Runtimes    # debe aparecer "nvidia"
```

## 4. Dar acceso a la GPU al contenedor

**docker run:**

```bash
docker run --rm --gpus all -p 8000:8000 -v envy-data:/data envy
```

**docker-compose.yml (Compose v2.30+):**

```yaml
services:
  envy:
    gpus: all
```

**Compose antiguo** (sin `gpus:`):

```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [utility]
```

## Capacidades de driver

Se controlan con `NVIDIA_DRIVER_CAPABILITIES`:

- `utility` — **suficiente para `nvidia-smi` y NVML** (lo que usa `envy`).
- `compute,utility` — para aplicaciones CUDA.

La imagen de `envy` ya fija `NVIDIA_DRIVER_CAPABILITIES=utility`.

## Notas

- **`nvidia-smi` lo aporta el toolkit**, no la imagen: se monta desde el host.
  Por eso la imagen parte de `python:3.12-slim` sin CUDA ni driver.
- **`--pid=host` no tiene que ver con la GPU.** Es solo para que la pestaña
  *Processes* vea los procesos del host (aislamiento del PID namespace).
- **Docker rootless** requiere pasos adicionales (el toolkit y el acceso a
  `/dev/nvidia*` no funcionan igual). Con Docker normal no hay problema.

## Verificación rápida

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Y para `envy`:

```bash
curl -s http://localhost:8000/api/snapshot | grep -o '"error":[^,]*'
# "error": null   -> la GPU se está leyendo bien desde el contenedor
```
