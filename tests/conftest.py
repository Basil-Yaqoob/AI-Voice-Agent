"""Shared fixtures. Unit tests must never require real credentials."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from agent.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings() -> Settings:
    """All-fake providers with dummy credentials."""
    return Settings(
        env="local",
        stt_provider="fake",
        tts_provider="fake",
        llm_provider="fake",
        anthropic_api_key=SecretStr("test"),
        deepgram_api_key=SecretStr("test"),
        azure_speech_key=SecretStr("test"),
        livekit_api_key=SecretStr("test"),
        livekit_api_secret=SecretStr("test"),
    )


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip real credentials from the environment.

    Without this, a developer with a populated .env gets different test results
    from CI — and `test_defaults_load` would silently assert nothing.
    """
    for name in (
        "ENV",
        "LOG_LEVEL",
        "STT_PROVIDER",
        "TTS_PROVIDER",
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "DEEPGRAM_API_KEY",
        "AZURE_SPEECH_KEY",
        "AZURE_SPEECH_REGION",
        "INWORLD_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LLM_MODEL",
        "LLM_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)
