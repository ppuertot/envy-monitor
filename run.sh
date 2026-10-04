#!/usr/bin/env bash
# Arranca envy. Uso: ./run.sh [puerto]
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8000}"
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
