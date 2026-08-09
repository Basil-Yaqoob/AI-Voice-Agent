"""Typed configuration. The only module in the project that reads the environment.

Everything else receives ``Settings`` by injection, which keeps secrets out of the
call graph and makes tests configurable without monkeypatching ``os.environ``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings", "validate_credentials"]


# Verified against the OpenRouter /models endpoint, 9 Aug 2026. Only models with a
# published "-fast" sibling appear here; anything else ignores llm_fast_mode on
# OpenRouter rather than requesting a model id that does not exist.
_OPENROUTER_FAST_VARIANTS: dict[str, str] = {
    "anthropic/claude-opus-5": "anthropic/claude-opus-5-fast",
}


class Settings(BaseSettings):
    """Runtime configuration, loaded from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",  # a typo'd var is a startup crash, not a silent default
    )

    # ---- environment ---------------------------------------------------
    env: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ---- provider selection (runtime-switchable, see DECISION 5) --------
    stt_provider: Literal["deepgram", "fake"] = "deepgram"
    tts_provider: Literal["azure", "inworld", "fake"] = "azure"
    # "claude" talks to the Anthropic API directly; "openrouter" proxies through
    # OpenRouter, which costs an extra network hop against the 300ms TTFT budget.
    # Measure both in TASK-12 before committing to one for production.
    llm_provider: Literal["claude", "openrouter", "fake"] = "claude"

    # ---- credentials -----------------------------------------------------
    anthropic_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    deepgram_api_key: SecretStr = SecretStr("")
    azure_speech_key: SecretStr = SecretStr("")
    # southeastasia is the closest Speech region to Pakistan. A US region adds
    # ~200ms per synthesis, which is most of the 110ms TTS budget.
    azure_speech_region: str = "southeastasia"
    inworld_api_key: SecretStr = SecretStr("")

    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: SecretStr = SecretStr("")
    livekit_api_secret: SecretStr = SecretStr("")

    # ---- model -----------------------------------------------------------
    llm_model: str = "claude-opus-5"
    llm_effort: Literal["low", "medium"] = "low"
    llm_fast_mode: bool = False
    llm_max_tokens: int = Field(default=400, ge=1)

    # ---- language ----------------------------------------------------------
    stt_language: str = "ur"
    tts_voice_urdu: str = "ur-PK-UzmaNeural"
    tts_voice_english: str = "en-US-AriaNeural"

    # ---- latency budget in ms (asserted by tests/latency/) -------------------
    budget_ttft_ms: int = Field(default=300, ge=1)
    budget_tts_ttfb_ms: int = Field(default=110, ge=1)
    budget_total_ms: int = Field(default=800, ge=1)
    endpoint_silence_ms: int = Field(default=180, ge=1)

    # ---- data -----------------------------------------------------------------
    # Non-default ports: a native Postgres/Redis install often already owns 5432
    # or 6379, and the collision surfaces as an auth failure rather than a bind
    # error. See docker-compose.yml.
    database_url: str = "postgresql://agent:localdev@127.0.0.1:55432/voiceagent"
    redis_url: str = "redis://127.0.0.1:56379/0"

    # ---- knowledge --------------------------------------------------------------
    # Local ONNX by default (DECISION 10): embeddings sit on the speculative
    # prefetch path, and a hosted API would put a network hop inside the one
    # path engineered to ~0ms. "hash" is deterministic and for tests only.
    embedding_provider: Literal["fastembed", "hash"] = "fastembed"
    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dimensions: int = Field(default=1024, ge=1)

    retrieval_top_k: int = Field(default=4, ge=1, le=20)
    answer_cache_ttl_s: int = Field(default=3600, ge=0)
    answer_cache_threshold: float = Field(default=0.93, ge=0.0, le=1.0)

    @property
    def resolved_llm_model(self) -> str:
        """Model id in the form the selected provider expects.

        Two provider differences are absorbed here so ``LLM_MODEL`` can stay the
        same across a provider switch instead of silently 404-ing:

        * OpenRouter namespaces models by vendor (``anthropic/claude-opus-5``);
          the Anthropic API does not.
        * OpenRouter exposes fast mode as a *separate model id* (``…-fast``),
          whereas the Anthropic API takes a ``speed="fast"`` request parameter.
          TASK-03 handles the direct-API side; the id swap belongs here.
        """
        model = self.llm_model
        if self.llm_provider != "openrouter":
            return model

        if "/" not in model:
            model = f"anthropic/{model}"
        if self.llm_fast_mode and model in _OPENROUTER_FAST_VARIANTS:
            model = _OPENROUTER_FAST_VARIANTS[model]
        return model

    @field_validator("llm_effort")
    @classmethod
    def _effort_stays_low_on_hot_path(cls, v: str) -> str:
        """Reject effort levels that cannot fit the 300ms TTFT budget.

        The ``Literal`` annotation already blocks these at the type level; this
        validator exists so the failure message explains *why* rather than just
        listing permitted values.
        """
        if v not in ("low", "medium"):
            raise ValueError(
                f"llm_effort={v!r} is unusable on the voice hot path — "
                "'high' and above blow the 300ms TTFT budget. Use 'low' or 'medium'."
            )
        return v


# Which credentials each provider selection actually requires. Keyed on
# (field, value) so a developer working on normalisation needs no Azure key.
_REQUIRED: dict[tuple[str, str], list[str]] = {
    ("stt_provider", "deepgram"): ["deepgram_api_key"],
    ("tts_provider", "azure"): ["azure_speech_key", "azure_speech_region"],
    ("tts_provider", "inworld"): ["inworld_api_key"],
    ("llm_provider", "claude"): ["anthropic_api_key"],
    ("llm_provider", "openrouter"): ["openrouter_api_key", "openrouter_base_url"],
}


def validate_credentials(settings: Settings) -> None:
    """Raise if a selected provider is missing its credentials.

    Called once at worker startup so a missing key fails immediately rather than
    on the first caller's turn.
    """
    missing: list[str] = []
    for (field_name, selected), required in _REQUIRED.items():
        if getattr(settings, field_name) != selected:
            continue
        for key in required:
            value = getattr(settings, key)
            raw = value.get_secret_value() if isinstance(value, SecretStr) else value
            if not raw:
                missing.append(f"{key.upper()} (required by {field_name}={selected})")

    if missing:
        raise RuntimeError(
            "Missing configuration:\n  "
            + "\n  ".join(missing)
            + "\n\nCopy .env.example to .env and fill these in."
        )


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Never call this at module import time — the cache makes it un-overridable in
    tests. Take ``Settings`` as a parameter instead.
    """
    return Settings()
