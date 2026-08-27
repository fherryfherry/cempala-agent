.PHONY: dev migrate test

PORT_BACKEND ?= 8000
PORT_FRONTEND ?= 3000

dev:
	@trap 'kill 0' SIGINT SIGTERM EXIT; \
	( cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $(PORT_BACKEND) ) & \
	( cd frontend && npm run dev -- --port $(PORT_FRONTEND) ) & \
	wait

migrate:
	cd backend && .venv/bin/alembic upgrade head

test:
	cd backend && .venv/bin/pytest
