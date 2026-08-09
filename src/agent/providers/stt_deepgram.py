"""Deepgram Nova-3 adapter. The only module permitted to import ``deepgram``.

Nova-3 gained production Urdu in February 2026: streaming, right-to-left native,
with keyterm prompting. ``interim_results`` is the load-bearing option — without
partials, TASK-07's speculative prefetch has nothing to fire on and retrieval
lands back on the critical path.

Written against **deepgram-sdk 7.x**, whose API differs completely from the 3.x
pattern most tutorials show: ``client.listen.v1.connect()`` is an async context
manager yielding a socket with ``send_media`` / ``recv``, rather than the old
``AsyncLiveClient`` with ``.on(event)`` callbacks.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from typing import Any

from deepgram import AsyncDeepgramClient

from agent.interfaces.stt import Transcript
from agent.telemetry.logging import get_logger

__all__ = ["MIN_CONFIDENCE", "DeepgramSTT"]

log = get_logger(__name__)

# Below this, treat a final as "I did not hear that" and re-ask. A garbled
# transcript produces a confidently wrong answer, which damages trust more than
# an honest re-ask. Tune against the audio fixtures in TASK-09.
MIN_CONFIDENCE = 0.45


class DeepgramSTT:
    """Streaming Urdu transcription with keyterm prompting."""

    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int = 16_000,
        endpointing_ms: int = 180,
        model: str = "nova-3",
    ) -> None:
        self._api_key = api_key
        self._sample_rate = sample_rate
        self._endpointing_ms = endpointing_ms
        self._model = model

    @property
    def name(self) -> str:
        return "deepgram"

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keyterms: Sequence[str] = (),
    ) -> AsyncIterator[Transcript]:
        client = AsyncDeepgramClient(api_key=self._api_key)

        async with client.listen.v1.connect(
            model=self._model,
            language=language,
            encoding="linear16",
            sample_rate=self._sample_rate,
            channels=1,
            interim_results=True,  # required for speculative prefetch
            punctuate=True,
            smart_format=True,
            endpointing=self._endpointing_ms,
            keyterm=list(keyterms) or None,
        ) as socket:
            pump = asyncio.create_task(self._pump(socket, audio))
            try:
                async for message in socket:
                    transcript = _to_transcript(message)
                    if transcript is not None:
                        yield transcript
            finally:
                pump.cancel()
                with suppress(asyncio.CancelledError):
                    await pump

    async def _pump(self, socket: Any, audio: AsyncIterator[bytes]) -> None:
        """Forward caller audio until the source is exhausted or cancelled."""
        try:
            async for frame in audio:
                await socket.send_media(frame)
            await socket.send_close_stream()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("stt_pump_failed", provider="deepgram", detail=str(exc))


def _to_transcript(message: Any) -> Transcript | None:
    """Map one socket message to a Transcript, or None if it carries no speech.

    Deepgram emits metadata, speech-started and utterance-end messages on the same
    socket, plus empty interim results during silence. None of those are turns.
    """
    results = getattr(message, "channel", None)
    if results is None:
        return None

    alternatives = getattr(results, "alternatives", None) or []
    if not alternatives:
        return None

    best = alternatives[0]
    text = (getattr(best, "transcript", "") or "").strip()
    if not text:
        return None

    return Transcript(
        text=text,
        is_final=bool(getattr(message, "is_final", False)),
        confidence=float(getattr(best, "confidence", 1.0) or 0.0),
        speech_final=bool(getattr(message, "speech_final", False)),
    )
