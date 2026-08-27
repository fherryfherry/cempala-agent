#!/usr/bin/env bash
# One-shot dev runner: sets up backend/frontend on first run, then starts both.
# Equivalent to doing `make migrate && make dev` by hand, minus remembering the steps.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

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

echo "==> Applying database migrations"
make migrate

echo "==> Starting backend (:8000) and frontend (:3000) — Ctrl+C stops both"
make dev
