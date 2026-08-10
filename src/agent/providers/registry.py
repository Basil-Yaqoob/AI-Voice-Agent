"""Config string -> provider instance.

This module is the reason DECISION 5 holds in practice: switching TTS vendor is
``TTS_PROVIDER=azure`` and a restart, not a code change and a deploy. That matters
because Inworld's Urdu is experimental and may need swapping mid-demo.
"""

from __future__ import annotations

from agent.config import Settings
from agent.interfaces.knowledge import EmbeddingProvider
from agent.interfaces.llm import LLMProvider
from agent.interfaces.stt import STTProvider
from agent.interfaces.tts import TTSProvider

__all__ = ["build_embedder", "build_llm", "build_stt", "build_tts"]


def build_embedder(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fastembed":
        from agent.knowledge.embeddings import FastEmbedProvider

        return FastEmbedProvider(
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )

    if settings.embedding_provider == "hash":
        from agent.knowledge.embeddings import HashEmbedder

        return HashEmbedder(settings.embedding_dimensions)

    raise ValueError(f"no embedding provider registered for {settings.embedding_provider!r}")


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "claude":
        from agent.providers.llm_claude import ClaudeProvider

        return ClaudeProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.resolved_llm_model,
            effort=settings.llm_effort,
            max_tokens=settings.llm_max_tokens,
            fast_mode=settings.llm_fast_mode,
        )

    if settings.llm_provider == "openrouter":
        from agent.providers.llm_openrouter import OpenRouterProvider

        # resolved_llm_model applies the anthropic/ namespace and, when fast mode
        # is on, swaps to the separate -fast model id OpenRouter publishes.
        return OpenRouterProvider(
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=settings.resolved_llm_model,
            base_url=settings.openrouter_base_url,
            max_tokens=settings.llm_max_tokens,
        )

    raise ValueError(f"no LLM provider registered for {settings.llm_provider!r}")


def build_stt(settings: Settings) -> STTProvider:
    if settings.stt_provider == "deepgram":
        from agent.providers.stt_deepgram import DeepgramSTT

        return DeepgramSTT(
            api_key=settings.deepgram_api_key.get_secret_value(),
            endpointing_ms=settings.endpoint_silence_ms,
        )

    raise ValueError(f"no STT provider registered for {settings.stt_provider!r}")


def build_tts(settings: Settings) -> TTSProvider:
    """Build the configured TTS, wrapping Inworld in failover to Azure.

    Inworld's Urdu is experimental; Azure is the stable fallback. Selecting Azure
    directly returns it unwrapped — there is nothing better to fall back to.
    """
    if settings.tts_provider == "azure":
        return _azure(settings)

    if settings.tts_provider == "uplift":
        # Not wrapped in failover to Azure. The whole reason to select Uplift is
        # that a Pakistani ear preferred it; silently substituting the voice
        # that was rejected would hide the very thing being evaluated.
        from agent.providers.tts_uplift import UpliftTTS

        return UpliftTTS(
            api_key=settings.uplift_api_key.get_secret_value(),
            default_voice=settings.uplift_voice,
        )

    if settings.tts_provider == "inworld":
        from agent.providers.tts_failover import FailoverTTS
        from agent.providers.tts_inworld import InworldTTS

        primary = InworldTTS(api_key=settings.inworld_api_key.get_secret_value())
        return FailoverTTS(primary, _azure(settings))

    raise ValueError(f"no TTS provider registered for {settings.tts_provider!r}")


def _azure(settings: Settings) -> TTSProvider:
    from agent.providers.tts_azure import AzureTTS

    return AzureTTS(
        key=settings.azure_speech_key.get_secret_value(),
        region=settings.azure_speech_region,
    )
