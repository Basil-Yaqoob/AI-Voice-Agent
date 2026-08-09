@echo off
REM Windows shim so `make <target>` works without installing GNU make.
REM Mirrors the Makefile. Keep the two in sync - tests/unit/test_task_runner.py enforces it.
REM Must stay ASCII with CRLF line endings; cmd.exe mis-parses UTF-8 and LF.
setlocal

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=help"

if /i "%TARGET%"=="help"             goto help
if /i "%TARGET%"=="dev"              goto dev
if /i "%TARGET%"=="down"             goto down
if /i "%TARGET%"=="test"             goto test
if /i "%TARGET%"=="test-unit"        goto test-unit
if /i "%TARGET%"=="test-integration" goto test-integration
if /i "%TARGET%"=="test-latency"     goto test-latency
if /i "%TARGET%"=="test-contract"    goto test-contract
if /i "%TARGET%"=="lint"             goto lint
if /i "%TARGET%"=="fmt"              goto fmt
if /i "%TARGET%"=="eval"             goto eval
if /i "%TARGET%"=="ingest"           goto ingest
if /i "%TARGET%"=="prerender"        goto prerender
if /i "%TARGET%"=="vendor"           goto vendor
if /i "%TARGET%"=="demo"             goto demo
goto unknown

:help
echo dev              start Postgres + Redis
echo down             stop local infra
echo test             unit + integration
echo test-unit        fast loop
echo test-integration integration only (needs make dev)
echo test-latency     latency budget assertions
echo test-contract    real vendor APIs (costs money)
echo lint             ruff + mypy + import-linter
echo fmt              format and autofix
echo eval             model and voice comparison
echo ingest           rebuild knowledge index
echo prerender        regenerate fixed audio assets
echo vendor           re-download the LiveKit browser client
echo demo             run the agent worker
goto :eof

:dev
docker compose up -d || exit /b 1
echo Postgres :5432  Redis :6379
goto :eof

:down
docker compose down || exit /b 1
goto :eof

:test
REM -m "not contract": contract tests live under tests/integration but hit real
REM vendor APIs and cost money. Never run them in the everyday loop.
uv run pytest tests/unit tests/integration -m "not contract" -v || exit /b 1
goto :eof

:test-unit
uv run pytest tests/unit -q || exit /b 1
goto :eof

:test-integration
uv run pytest tests/integration -m "not contract" -v || exit /b 1
goto :eof

:test-latency
uv run pytest tests/latency -v || exit /b 1
goto :eof

:test-contract
uv run pytest -m contract -v || exit /b 1
goto :eof

:lint
uv run ruff check src tests || exit /b 1
uv run mypy src || exit /b 1
uv run lint-imports || exit /b 1
goto :eof

:fmt
uv run ruff format src tests || exit /b 1
uv run ruff check --fix src tests || exit /b 1
goto :eof

:eval
uv run python eval/run_eval.py || exit /b 1
goto :eof

:ingest
uv run python scripts/ingest.py || exit /b 1
goto :eof

:vendor
uv run python scripts/vendor_livekit.py || exit /b 1
goto :eof

:prerender
uv run python -m agent.tools.prerender || exit /b 1
goto :eof

:demo
uv run python -m agent.main dev || exit /b 1
goto :eof

:unknown
echo make: *** No rule to make target '%TARGET%'.  Stop.
exit /b 2
