# Task runner for Linux/macOS/CI.
# Windows has no `make` by default — make.cmd mirrors these targets.
# Keep the two in sync; tests/unit/test_task_runner.py enforces it.

.PHONY: help dev down vendor test test-unit test-integration test-latency test-contract lint fmt eval ingest prerender demo

help:
	@echo "dev              start Postgres + Redis"
	@echo "down             stop local infra"
	@echo "test             unit + integration"
	@echo "test-unit        fast loop, <5s"
	@echo "test-integration integration only (needs make dev)"
	@echo "test-latency     latency budget assertions"
	@echo "test-contract    real vendor APIs (costs money)"
	@echo "lint             ruff + mypy + import-linter"
	@echo "fmt              format and autofix"
	@echo "eval             model and voice comparison"
	@echo "ingest           rebuild knowledge index"
	@echo "prerender        regenerate fixed audio assets"
	@echo "vendor           re-download the LiveKit browser client"
	@echo "demo             run the agent worker"

dev:
	docker compose up -d
	@echo "Postgres :5432  Redis :6379"

down:
	docker compose down

# -m "not contract" matters: contract tests live under tests/integration/ but hit
# real vendor APIs and cost money. They must never run in the everyday loop.
test:
	uv run pytest tests/unit tests/integration -m "not contract" -v

test-unit:
	uv run pytest tests/unit -q

test-integration:
	uv run pytest tests/integration -m "not contract" -v

test-latency:
	uv run pytest tests/latency -v

test-contract:
	uv run pytest -m contract -v

lint:
	uv run ruff check src tests
	uv run mypy src
	uv run lint-imports

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

eval:
	uv run python eval/run_eval.py

ingest:
	uv run python -m agent.knowledge.ingest

vendor:
	uv run python scripts/vendor_livekit.py

prerender:
	uv run python -m agent.tools.prerender

demo:
	uv run python -m agent.main dev
