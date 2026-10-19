#!/usr/bin/env bash
# Arranca envy. Uso: ./run.sh [puerto]
# El puerto se puede fijar con ENVY_PORT o como argumento (por defecto 8080).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${ENVY_PORT:-${1:-8080}}"
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
