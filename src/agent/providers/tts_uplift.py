"""Streaming synthesis via Upliftai, a Pakistani TTS vendor.

The reason to reach for this over Azure or OpenAI is the voices: 82 of them,
recorded for Pakistani speech rather than adapted to it, with names like
"Broadband Support" and "Helpdesk Agent" that describe a call-centre register
rather than a generic narrator.

The reason not to is latency. Measured 10 Aug 2026 from Karachi, time to first
byte was 1,472 to 1,764ms across every voice and format tried — against a
110ms budget, and against 518ms for the OpenAI realtime path doing listening,
thinking *and* speaking. Using this means ``VOICE_MODE=pipeline`` and a turn
somewhere near three seconds. That is a trade about how the voice sounds, and
only a Pakistani ear can settle it.

``PCM_22050_16`` is requested rather than MP3 so nothing has to decode on the
hot path — the bytes go straight to the sink. Note the rate: 22.05kHz, where
Azure emits 24kHz. The sink is built from ``sample_rate`` for exactly this
reason; hard-coding 24000 anywhere would play this voice slightly slow.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from agent.telemetry.logging import get_logger

__all__ = ["UpliftTTS"]

log = get_logger(__name__)

_URL = "https://api.upliftai.org/v1/synthesis/text-to-speech"

# Raw little-endian PCM, so it reaches the sink without a decode step. The
# vendor also offers MP3 and OGG, which would need one.
_FORMAT = "PCM_22050_16"
_SAMPLE_RATE = 22_050

# Generous: first byte measured at 1.5s, and a cold connection is slower still.
# The recovery line is a worse outcome than waiting.
_TIMEOUT_S = 20.0


class UpliftTTS:
    """Streaming Urdu synthesis with Pakistani voices."""

    def __init__(self, *, api_key: str, default_voice: str = "broadband-support") -> None:
        self._api_key = api_key
        self._default_voice = default_voice
        # One client for the process: a new TLS handshake per utterance would
        # add hundreds of milliseconds to a budget that has none to spare.
        self._client = httpx.AsyncClient(timeout=_TIMEOUT_S)

    @property
    def name(self) -> str:
        return "uplift"

    @property
    def sample_rate(self) -> int:
        return _SAMPLE_RATE

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        """Yield raw PCM as it arrives."""
        async with self._client.stream(
            "POST",
            _URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "voiceId": voice or self._default_voice,
                "text": text,
                "outputFormat": _FORMAT,
            },
        ) as response:
            if response.status_code != 200:
                detail = (await response.aread())[:200].decode("utf-8", "replace")
                raise RuntimeError(f"uplift returned {response.status_code}: {detail}")
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()
