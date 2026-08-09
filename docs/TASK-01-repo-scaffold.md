# TASK-01 — Repository scaffold and toolchain

| | |
|---|---|
| **Depends on** | nothing — start here |
| **Blocks** | everything |
| **Estimate** | 2–3 hours |
| **Level** | Beginner |

---

## Objective

A repository that runs, lints, type-checks and tests on a clean machine with two commands. No agent logic yet — this task exists so that every later task lands in a place where mistakes are caught automatically.

The specific mistake we are buying protection against: **a synchronous call on the audio hot path**. It produces audible stutter, it is invisible in code review, and `ruff`'s async ruleset catches it for free. Setting that up now costs an hour; finding it by ear in week 6 costs a day.

---

## Steps

### 1. Install `uv` and initialise

```bash
# Windows PowerShell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

cd "D:\AI Voice Agent"
uv init --name ai-voice-agent --python 3.12
```

We use `uv` over pip/poetry for resolution speed and a real lockfile. Dependency installs happen constantly during this build.

### 2. Declare dependencies

```bash
uv add "livekit-agents[deepgram,azure,anthropic,silero]~=1.0"
uv add anthropic deepgram-sdk azure-cognitiveservices-speech
uv add fastapi uvicorn[standard] pydantic-settings
uv add asyncpg pgvector redis
uv add structlog prometheus-client

uv add --dev pytest pytest-asyncio pytest-cov hypothesis freezegun
uv add --dev ruff mypy import-linter
```

### 3. Configure the toolchain

`pyproject.toml` — the async rules are the load-bearing part:

```toml
[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = [
    "E", "F", "I",      # pycodestyle, pyflakes, isort
    "ASYNC",            # ← blocking calls in async functions
    "B",                # bugbear
    "UP",               # pyupgrade
    "RUF",
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["ASYNC"]   # fakes may sleep deliberately

[tool.mypy]
python_version = "3.12"
strict = true
warn_unreachable = true

[[tool.mypy.overrides]]
module = ["livekit.*", "deepgram.*", "azure.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "contract: hits real vendor APIs (costs money, needs keys)",
    "latency: asserts against the latency budget",
]
```

### 4. Lock the provider-import boundary

This enforces Decision 3 from `01-TECH-DECISIONS.md` mechanically. Create `.importlinter`:

```ini
[importlinter]
root_package = agent

[importlinter:contract:providers-are-isolated]
name = Vendor SDKs may only be imported inside their own adapter
type = forbidden
source_modules =
    agent.pipeline
    agent.language
    agent.knowledge
    agent.interfaces
forbidden_modules =
    anthropic
    deepgram
    azure

[importlinter:contract:interfaces-are-pure]
name = Interfaces must not depend on implementations
type = forbidden
source_modules = agent.interfaces
forbidden_modules = agent.providers
```

Without this, "providers sit behind interfaces" degrades into a convention someone breaks under deadline pressure — which is exactly when you most need to swap Inworld out for Azure.

### 5. Directory skeleton

Create the tree from `00-ARCHITECTURE.md`, with `__init__.py` in each package and empty placeholder modules. Add `tests/unit/`, `tests/integration/`, `tests/latency/`, `tests/fakes/`, `tests/fixtures/audio/`, `tests/fixtures/golden/`.

### 6. Local infrastructure

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: localdev
      POSTGRES_DB: voiceagent
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent"]
      interval: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 5

volumes:
  pgdata:
```

Use the `pgvector/pgvector` image rather than plain `postgres` — it ships the extension, saving a build step.

### 7. Makefile

```makefile
.PHONY: dev test test-unit test-latency test-contract lint fmt eval demo

dev:            ## start local infra
	docker compose up -d
	@echo "Postgres :5432  Redis :6379"

test:           ## unit + integration
	uv run pytest tests/unit tests/integration -v

test-unit:      ## fast loop
	uv run pytest tests/unit -q

test-latency:
	uv run pytest tests/latency -v

test-contract:  ## real vendors, costs money
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

demo:
	uv run python -m agent.main dev
```

### 8. Smoke test

`tests/unit/test_scaffold.py`:

```python
import agent

def test_package_imports() -> None:
    assert agent is not None

async def test_async_test_runner_works() -> None:
    import asyncio
    await asyncio.sleep(0)
```

The second test looks trivial but it verifies `asyncio_mode = "auto"` is actually configured — otherwise every async test in the project silently passes without running its body, which is a genuinely nasty failure mode.

### 9. `.gitignore` and `.env.example`

```gitignore
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
htmlcov/
*.wav.tmp
```

Create an empty `.env.example` — Task 02 fills it in.

---

## Acceptance criteria

- [ ] `uv sync` completes from clean checkout with no errors
- [ ] `make dev` brings up Postgres and Redis; both report healthy within 30s
- [ ] `make test` passes with the two smoke tests green
- [ ] `make lint` passes clean: `ruff`, `mypy --strict`, and `lint-imports`
- [ ] `make fmt` is idempotent — running twice produces no diff
- [ ] Adding `import anthropic` to `src/agent/pipeline/session.py` makes `make lint` **fail** *(verify this deliberately, then revert — an unverified guardrail is not a guardrail)*
- [ ] `uv.lock` is committed
- [ ] `.env` is gitignored and `.env.example` exists
- [ ] `README.md` documents setup in under 10 lines

---

## Unit tests

| Test | Asserts |
|---|---|
| `test_package_imports` | Package is importable; `PYTHONPATH` is right |
| `test_async_test_runner_works` | `asyncio_mode=auto` is live — async test bodies actually execute |
| `test_all_packages_have_init` | Walks `src/agent`, asserts every directory has `__init__.py` |

```python
def test_all_packages_have_init() -> None:
    """A missing __init__.py fails only on some import paths — catch it here."""
    root = Path("src/agent")
    for d in root.rglob("*"):
        if d.is_dir() and not d.name.startswith(("_", ".")):
            assert (d / "__init__.py").exists(), f"missing __init__.py in {d}"
```

---

## Common pitfalls

- **`asyncio_mode` not set to `auto`.** Async tests appear to pass while never executing. The smoke test above is the guard.
- **Installing `livekit-agents` without extras.** The plugin packages (`[deepgram,azure,anthropic,silero]`) are separate; omitting them produces confusing import errors in Task 04.
- **Skipping the import-linter verification.** Unverified, it usually turns out to be misconfigured and silently passing.
- **Plain `postgres` image.** Missing the `vector` extension surfaces as a cryptic SQL error in Task 07.
