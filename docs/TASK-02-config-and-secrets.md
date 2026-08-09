# TASK-02 — Configuration and secrets

| | |
|---|---|
| **Depends on** | TASK-01 |
| **Blocks** | 03, 04, 05, 07, 08 |
| **Estimate** | 2 hours |
| **Level** | Beginner |

---

## Objective

One typed, validated configuration object that every other module receives by injection. No module outside `config.py` may touch `os.environ`.

This is worth a dedicated task because of Decision 5: **you must be able to swap TTS providers with an environment variable and a restart, not a code change and a deploy.** Inworld's Urdu is flagged experimental. If it degrades during a client demo, the recovery is `TTS_PROVIDER=azure` and a restart. That only works if provider selection is config from the very first commit.

---

## Steps

### 1. Settings model

`src/agent/config.py`:

```python
from functools import lru_cache
from typing import Literal
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="forbid"
    )

    # ---- environment -------------------------------------------------
    env: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ---- provider selection (runtime-switchable) ---------------------
    stt_provider: Literal["deepgram", "fake"] = "deepgram"
    tts_provider: Literal["azure", "inworld", "fake"] = "azure"
    llm_provider: Literal["claude", "fake"] = "claude"

    # ---- credentials -------------------------------------------------
    anthropic_api_key: SecretStr = SecretStr("")
    deepgram_api_key: SecretStr = SecretStr("")
    azure_speech_key: SecretStr = SecretStr("")
    azure_speech_region: str = "southeastasia"
    inworld_api_key: SecretStr = SecretStr("")

    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: SecretStr = SecretStr("")
    livekit_api_secret: SecretStr = SecretStr("")

    # ---- model configuration ------------------------------------------
    llm_model: str = "claude-opus-5"
    llm_effort: Literal["low", "medium"] = "low"
    llm_fast_mode: bool = False
    llm_max_tokens: int = 400

    # ---- language ------------------------------------------------------
    stt_language: str = "ur"
    tts_voice_urdu: str = "ur-PK-UzmaNeural"
    tts_voice_english: str = "en-US-AriaNeural"

    # ---- latency budget (ms) — asserted in tests/latency/ ---------------
    budget_ttft_ms: int = 300
    budget_tts_ttfb_ms: int = 110
    budget_total_ms: int = 800
    endpoint_silence_ms: int = 180

    # ---- data ------------------------------------------------------------
    database_url: str = "postgresql://agent:localdev@localhost:5432/voiceagent"
    redis_url: str = "redis://localhost:6379/0"

    # ---- knowledge --------------------------------------------------------
    retrieval_top_k: int = Field(default=4, ge=1, le=20)
    answer_cache_ttl_s: int = 3600
    answer_cache_threshold: float = Field(default=0.93, ge=0.0, le=1.0)

    @field_validator("llm_effort")
    @classmethod
    def _effort_stays_low_on_hot_path(cls, v: str) -> str:
        # 'high'/'xhigh'/'max' blow the 300ms TTFT budget. Guarded here so the
        # mistake is impossible rather than merely discouraged.
        if v not in ("low", "medium"):
            raise ValueError(f"effort={v!r} is unusable on the voice hot path")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Notes on specific choices:

- **`extra="forbid"`** — a typo'd env var (`TTS_PROVIDR=azure`) becomes a startup crash instead of a silent fallback to the default. On a demo day that difference matters.
- **`SecretStr`** — keeps keys out of `repr()`, log lines and exception tracebacks. Read with `.get_secret_value()` only at the point of use.
- **`azure_speech_region` defaults to `southeastasia`** — the closest Azure Speech region to Pakistan. `eastus` would silently add ~200ms per synthesis, which is most of your TTS budget.

### 2. Fail fast on missing credentials

A missing key must fail at startup, not on the first caller's turn. But only validate the keys the *selected* providers actually need — a developer working on normalisation should not need an Azure key.

```python
_REQUIRED: dict[tuple[str, str], list[str]] = {
    ("stt_provider", "deepgram"): ["deepgram_api_key"],
    ("tts_provider", "azure"):    ["azure_speech_key", "azure_speech_region"],
    ("tts_provider", "inworld"):  ["inworld_api_key"],
    ("llm_provider", "claude"):   ["anthropic_api_key"],
}


def validate_credentials(s: Settings) -> None:
    missing: list[str] = []
    for (field, value), required in _REQUIRED.items():
        if getattr(s, field) != value:
            continue
        for key in required:
            v = getattr(s, key)
            raw = v.get_secret_value() if isinstance(v, SecretStr) else v
            if not raw:
                missing.append(f"{key.upper()} (required by {field}={value})")
    if missing:
        raise RuntimeError(
            "Missing configuration:\n  " + "\n  ".join(missing)
            + "\n\nCopy .env.example to .env and fill these in."
        )
```

Call it once in `main.py` at worker startup.

### 3. Structured logging with redaction

`src/agent/telemetry/logging.py`. Two requirements: JSON output for machine parsing, and a processor that redacts anything key-shaped — because an accidental `logger.info("settings", **settings.model_dump())` must not leak credentials to a log aggregator.

```python
import structlog

_SENSITIVE = ("key", "secret", "token", "password", "authorization")

def _redact(logger, method, event_dict):
    for k in list(event_dict):
        if any(s in k.lower() for s in _SENSITIVE):
            event_dict[k] = "***REDACTED***"
    return event_dict

def configure_logging(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _redact,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )
```

`merge_contextvars` matters more than it looks: bind `call_id` once at session start and every subsequent log line in that call carries it automatically. Debugging a single bad call out of a day's traffic depends on this.

### 4. `.env.example`

Document every variable with its purpose and where to get it. This file is the setup instructions for the next developer.

```bash
# ── Environment ────────────────────────────────────────────────────
ENV=local
LOG_LEVEL=INFO

# ── Provider selection ─────────────────────────────────────────────
# Switch TTS at runtime if the primary degrades. No code change needed.
STT_PROVIDER=deepgram      # deepgram | fake
TTS_PROVIDER=azure         # azure | inworld | fake
LLM_PROVIDER=claude        # claude | fake

# ── Credentials ────────────────────────────────────────────────────
# console.anthropic.com
ANTHROPIC_API_KEY=
# console.deepgram.com — $200 free credit, no card, no expiry
DEEPGRAM_API_KEY=
# portal.azure.com > Speech service — 500k chars/month free forever
AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=southeastasia   # closest to PK; do NOT use eastus
# platform.inworld.ai (optional — only if TTS_PROVIDER=inworld)
INWORLD_API_KEY=

# cloud.livekit.io — free Build tier
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# ── Model ──────────────────────────────────────────────────────────
LLM_MODEL=claude-opus-5
LLM_EFFORT=low             # low | medium — higher blows the TTFT budget
LLM_FAST_MODE=false        # Opus 5 fast mode: ~2.5x output speed at $10/$50 per MTok
LLM_MAX_TOKENS=400         # a spoken turn is short; caps runaway responses

# ── Language ───────────────────────────────────────────────────────
STT_LANGUAGE=ur
TTS_VOICE_URDU=ur-PK-UzmaNeural     # or ur-PK-AsadNeural
TTS_VOICE_ENGLISH=en-US-AriaNeural

# ── Latency budget (ms) ────────────────────────────────────────────
BUDGET_TTFT_MS=300
BUDGET_TTS_TTFB_MS=110
BUDGET_TOTAL_MS=800
ENDPOINT_SILENCE_MS=180    # tune against real PK call audio in TASK-09

# ── Data ───────────────────────────────────────────────────────────
DATABASE_URL=postgresql://agent:localdev@localhost:5432/voiceagent
REDIS_URL=redis://localhost:6379/0

# ── Knowledge ──────────────────────────────────────────────────────
RETRIEVAL_TOP_K=4
ANSWER_CACHE_TTL_S=3600
ANSWER_CACHE_THRESHOLD=0.93
```

### 5. Test settings factory

`tests/conftest.py`:

```python
@pytest.fixture
def settings() -> Settings:
    """All-fake settings. Unit tests must never need real credentials."""
    return Settings(
        env="local",
        stt_provider="fake", tts_provider="fake", llm_provider="fake",
        anthropic_api_key=SecretStr("test"),
        deepgram_api_key=SecretStr("test"),
        azure_speech_key=SecretStr("test"),
        livekit_api_key=SecretStr("test"),
        livekit_api_secret=SecretStr("test"),
    )
```

---

## Acceptance criteria

- [ ] `get_settings()` loads from `.env` and returns a fully typed object
- [ ] A typo'd env var name raises a validation error at startup (`extra="forbid"`)
- [ ] `LLM_EFFORT=high` is rejected with a clear message
- [ ] Selecting a provider without its credentials produces a startup error naming the exact missing variable
- [ ] Selecting `fake` providers requires no credentials at all
- [ ] `str(settings)` and `repr(settings)` contain no secret values
- [ ] `logger.info("cfg", api_key="sk-real")` emits `***REDACTED***`
- [ ] Every field in `Settings` appears in `.env.example` with a comment
- [ ] `grep -rn "os.environ\|os.getenv" src/ --include=*.py` returns only `config.py`
- [ ] `mypy --strict src/agent/config.py` passes

---

## Unit tests

`tests/unit/test_config.py`:

| Test | Asserts |
|---|---|
| `test_defaults_load` | Settings construct with no env set |
| `test_unknown_env_var_rejected` | `extra="forbid"` catches typos |
| `test_effort_high_rejected` | Validator blocks hot-path-unsafe effort |
| `test_missing_credential_names_the_variable` | Error message contains `DEEPGRAM_API_KEY` |
| `test_fake_providers_need_no_credentials` | All-fake config validates clean |
| `test_secrets_not_in_repr` | No secret substring in `repr()` |
| `test_secrets_not_in_model_dump` | Serialised form is safe to log |
| `test_log_redaction` | Key-shaped fields become `***REDACTED***` |
| `test_env_example_covers_all_fields` | Parses `.env.example`, diffs against `Settings.model_fields` |
| `test_retrieval_top_k_bounds` | `ge`/`le` constraints enforced |

Two worth writing out — they catch the failures that actually bite:

```python
def test_secrets_not_in_repr() -> None:
    s = Settings(anthropic_api_key=SecretStr("sk-ant-SUPERSECRET"))
    assert "SUPERSECRET" not in repr(s)
    assert "SUPERSECRET" not in str(s)
    assert "SUPERSECRET" not in str(s.model_dump())


def test_env_example_covers_all_fields() -> None:
    """Drift here means the next developer hits an undocumented required var."""
    documented = {
        line.split("=", 1)[0].strip().lower()
        for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    missing = set(Settings.model_fields) - documented
    assert not missing, f"undocumented settings: {sorted(missing)}"
```

---

## Common pitfalls

- **Calling `get_settings()` at module import time.** It caches, so tests can never override it. Take settings as a function or constructor argument.
- **Defaulting `AZURE_SPEECH_REGION` to a US region.** ~200ms of avoidable TTS latency — most of your 110ms budget, spent on geography.
- **`extra="ignore"` instead of `"forbid"`.** Typos then fail silently, and you debug the wrong layer.
- **Logging the settings object during startup debugging.** The redaction processor covers structured fields; it will not save you from `logger.info(f"{settings}")` inside an f-string. Rely on `SecretStr`, which covers that too.
