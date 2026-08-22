.PHONY: dev migrate test

dev:
	@trap 'kill 0' SIGINT SIGTERM EXIT; \
	( cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 ) & \
	( cd frontend && npm run dev ) & \
	wait

migrate:
	cd backend && .venv/bin/alembic upgrade head

test:
	cd backend && .venv/bin/pytest
