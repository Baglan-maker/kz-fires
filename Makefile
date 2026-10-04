PY := .venv/bin/python

.PHONY: setup db-up db-down migrate test lint fmt ingest retro

setup:
	python3.12 -m venv .venv
	$(PY) -m pip install -q -U pip
	$(PY) -m pip install -q -e '.[dev]'

db-up:
	docker compose up -d --wait db
	$(MAKE) migrate

db-down:
	docker compose down

migrate:
	$(PY) -m alembic upgrade head

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

ingest:
	$(PY) -m kzfires.cli ingest

retro:
	$(PY) -m kzfires.cli retro
