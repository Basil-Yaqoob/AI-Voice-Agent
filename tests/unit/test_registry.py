"""Provider registry: config string -> instance.

This is what makes DECISION 5 real — swapping TTS vendor must be an env var and a
restart, not a code change and a deploy, because Inworld's Urdu is experimental
and may need swapping during a client demo.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from agent.config import Settings
from agent.interfaces.llm import LLMProvider
from agent.interfaces.stt import STTProvider
from agent.interfaces.tts import TTSProvider
from agent.providers.registry import build_llm, build_stt, build_tts


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "anthropic_api_key": SecretStr("k"),
        "openrouter_api_key": SecretStr("k"),
        "deepgram_api_key": SecretStr("k"),
        "azure_speech_key": SecretStr("k"),
        "inworld_api_key": SecretStr("k"),
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ── LLM ───────────────────────────────────────────────────────────────────


def test_builds_claude_provider() -> None:
    provider = build_llm(_settings(llm_provider="claude"))
    assert isinstance(provider, LLMProvider)
    assert provider.name == "claude"


def test_builds_openrouter_provider() -> None:
    provider = build_llm(_settings(llm_provider="openrouter"))
    assert isinstance(provider, LLMProvider)
    assert provider.name == "openrouter"


def test_openrouter_gets_the_resolved_model_id() -> None:
    """The registry must pass resolved_llm_model, not the raw LLM_MODEL, or the
    request 404s on a model id OpenRouter does not publish."""
    settings = _settings(llm_provider="openrouter", llm_model="claude-opus-5")
    assert settings.resolved_llm_model == "anthropic/claude-opus-5"
    assert build_llm(settings).name == "openrouter"


def test_unregistered_llm_provider_raises() -> None:
    with pytest.raises(ValueError, match="fake"):
        build_llm(_settings(llm_provider="fake"))


# ── STT ────────────────────────────────────────────────────────────────────


def test_builds_deepgram_provider() -> None:
    provider = build_stt(_settings(stt_provider="deepgram"))
    assert isinstance(provider, STTProvider)
    assert provider.name == "deepgram"


def test_stt_receives_endpointing_from_config() -> None:
    """TASK-09 tunes this; it must not be hardcoded in the adapter."""
    provider = build_stt(_settings(stt_provider="deepgram", endpoint_silence_ms=250))
    assert provider._endpointing_ms == 250  # type: ignore[attr-defined]


def test_unregistered_stt_provider_raises() -> None:
    with pytest.raises(ValueError, match="fake"):
        build_stt(_settings(stt_provider="fake"))


# ── TTS ────────────────────────────────────────────────────────────────────


def test_azure_returned_unwrapped() -> None:
    """Nothing better to fall back to, so no failover wrapper."""
    provider = build_tts(_settings(tts_provider="azure"))
    assert isinstance(provider, TTSProvider)
    assert provider.name == "azure"


def test_inworld_is_wrapped_in_failover_to_azure() -> None:
    """Experimental Urdu must never be the last line of defence."""
    provider = build_tts(_settings(tts_provider="inworld"))
    assert "inworld" in provider.name
    assert "azure" in provider.name


def test_azure_region_threaded_through() -> None:
    provider = build_tts(_settings(tts_provider="azure", azure_speech_region="southeastasia"))
    assert provider._region == "southeastasia"  # type: ignore[attr-defined]


def test_unregistered_tts_provider_raises() -> None:
    with pytest.raises(ValueError, match="fake"):
        build_tts(_settings(tts_provider="fake"))
