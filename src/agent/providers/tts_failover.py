"""Runtime TTS failover.

Exists because of a specific, documented risk: Inworld's Urdu is flagged
*experimental*, so quality, price or availability can change without production
guarantees. Azure ``ur-PK`` is the stable fallback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent.interfaces.tts import TTSProvider
from agent.telemetry.logging import get_logger

__all__ = ["FailoverTTS"]

log = get_logger(__name__)


class FailoverTTS:
    """Try the primary; fall back only if it fails *before* the first byte."""

    def __init__(self, primary: TTSProvider, fallback: TTSProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def name(self) -> str:
        return f"failover({self._primary.name}->{self._fallback.name})"

    @property
    def sample_rate(self) -> int:
        return self._primary.sample_rate

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        produced_audio = False
        try:
            async for chunk in self._primary.synthesize(text, voice=voice):
                produced_audio = True
                yield chunk
            return
        except Exception as exc:
            if produced_audio:
                # Audio already reached the caller. Switching now splices two
                # different voices into one sentence, which sounds far worse
                # than a clean failure — the pipeline speaks a recovery line.
                log.warning("tts_failed_mid_utterance", provider=self._primary.name)
                raise
            log.warning(
                "tts_failover",
                frm=self._primary.name,
                to=self._fallback.name,
                detail=str(exc),
            )

        async for chunk in self._fallback.synthesize(text, voice=voice):
            yield chunk
