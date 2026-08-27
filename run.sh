#!/usr/bin/env bash
# One-shot dev runner: sets up backend/frontend on first run, then starts both.
# Equivalent to doing `make migrate && make dev` by hand, minus remembering the steps.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PORT_BACKEND=8000
PORT_FRONTEND=3000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend=*) PORT_BACKEND="${1#*=}" ;;
    --frontend=*) PORT_FRONTEND="${1#*=}" ;;
    --backend) PORT_BACKEND="$2"; shift ;;
    --frontend) PORT_FRONTEND="$2"; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
  shift
done

if [ ! -d backend/.venv ]; then
  echo "==> First run: creating backend/.venv"
  if command -v uv >/dev/null 2>&1; then
    (cd backend && uv venv --python 3.12 .venv && uv pip install -e ".[dev]")
  else
    (cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]")
  fi
fi

if [ ! -d frontend/node_modules ]; then
  echo "==> First run: installing frontend dependencies"
  (cd frontend && npm install)
fi

for port in "$PORT_BACKEND" "$PORT_FRONTEND"; do
  pids=$(lsof -ti tcp:"$port" || true)
  if [ -n "$pids" ]; then
    echo "==> Port $port in use (PID $pids) — killing"
    kill $pids
  fi
done

echo "==> Applying database migrations"
make migrate

echo "==> Starting backend (:$PORT_BACKEND) and frontend (:$PORT_FRONTEND) — Ctrl+C stops both"
make dev PORT_BACKEND="$PORT_BACKEND" PORT_FRONTEND="$PORT_FRONTEND"
