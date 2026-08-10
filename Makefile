# Task runner for Linux/macOS/CI.
# Windows has no `make` by default — make.cmd mirrors these targets.
# Keep the two in sync; tests/unit/test_task_runner.py enforces it.

.PHONY: help dev down vendor test test-unit test-integration test-latency test-contract lint fmt eval ingest prerender dataset db data demo serve stop

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
	@echo "dataset          regenerate the DVAGO spreadsheet"
	@echo "db               load the spreadsheet into SQLite"
	@echo "data             dataset + db"
	@echo "vendor           re-download the LiveKit browser client"
	@echo "demo             run the agent worker"
	@echo "serve            run the control plane on :8000 (the demo page)"
	@echo "stop             stop the worker and the control plane"

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
	uv run python scripts/ingest.py

vendor:
	uv run python scripts/vendor_livekit.py

prerender:
	uv run python -m agent.tools.prerender

# Regenerating overwrites hand-edited cells. Edit the workbook and run `make db`
# alone; only run `dataset` when you want a fresh population.
dataset:
	uv run python scripts/generate_dvago_dataset.py

db:
	uv run python scripts/build_dvago_db.py

data: dataset db

demo:
	uv run python -m agent.main dev

serve:
	uv run uvicorn agent.api.server:create_app --factory --port 8000

# Leading '-' on each line: pkill exits 1 when nothing matched, and "already
# stopped" is a success for this target, not a failure.
stop:
	-pkill -f "agent.main dev"
	-pkill -f "agent.api.server"
