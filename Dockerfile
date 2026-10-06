# envy — imagen del monitor. Requiere nvidia-container-toolkit en el host.
FROM python:3.12-slim

# La app no necesita CUDA, solo la capacidad "utility" (nvidia-smi).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=utility

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py nvidia.py nvidia_smi.py nvidia_nvml.py errors.py processes.py storage.py ./
COPY static ./static

# Usuario sin privilegios y directorio de datos
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin envy \
    && mkdir -p /data \
    && chown -R envy:envy /app /data

# Histórico persistido (montar un volumen en /data para que sobreviva)
ENV ENVY_DB=/data/envy.db
VOLUME ["/data"]

USER envy

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; t=os.environ.get('ENVY_TOKEN'); u='http://127.0.0.1:8000/api/snapshot'+('?token='+t if t else ''); sys.exit(0 if urllib.request.urlopen(u).status == 200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
