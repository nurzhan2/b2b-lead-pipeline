PY ?= python

.PHONY: db run test test-core test-workers lint format

db:
	docker compose up -d

run:
	mvn spring-boot:run

test: test-core test-workers

test-core:
	mvn -B verify

test-workers:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:
	$(PY) -m ruff format .
	$(PY) -m ruff check . --fix
