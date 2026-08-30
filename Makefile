.PHONY: install lint typecheck test unit integration chaos up down benchmark

install:
	python -m pip install -e '.[dev]'

lint:
	ruff check .

typecheck:
	mypy backend/app

unit:
	pytest -m 'not integration and not chaos'

integration:
	pytest -m integration

chaos:
	./scripts/chaos-compose.sh

test: lint typecheck unit

up:
	docker compose up --build

down:
	docker compose down -v

benchmark:
	./scripts/run-benchmark.sh
