"""Inworld TTS-2 adapter.

Urdu (``ur``) is available **only on ``inworld-tts-2``** and is flagged
*experimental* in Inworld's own documentation — the 1.5 models do not carry it,
so the model id is not interchangeable. Because experimental support can shift
without notice, this provider is normally wrapped in
:class:`agent.providers.tts_failover.FailoverTTS` with Azure behind it.

``probe()`` synthesises one short Urdu phrase at startup so a broken experimental
voice surfaces in the logs rather than on the first live call.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator

import httpx

from agent.telemetry.logging import get_logger

__all__ = ["InworldTTS"]

log = get_logger(__name__)

# Urdu is TTS-2 only. Pointing this at a 1.5 model silently does the wrong thing.
_MODEL_ID = "inworld-tts-2"
_PROBE_TEXT = "السلام علیکم"


class InworldTTS:
    """Streaming synthesis via Inworld's realtime TTS API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.inworld.ai/tts/v1",
        sample_rate: int = 24_000,
        timeout_s: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._sample_rate = sample_rate
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return "inworld"

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return

        payload = {
            "text": text,
            "voiceId": voice,
            "modelId": _MODEL_ID,
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": self._sample_rate,
            },
        }
        headers = {
            "Authorization": f"Basic {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/voice:stream",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    chunk = _decode_chunk(line)
                    if chunk:
                        yield chunk

    async def probe(self, voice: str) -> bool:
        """Synthesize one Urdu phrase to confirm the experimental voice works."""
        try:
            async for _ in self.synthesize(_PROBE_TEXT, voice=voice):
                return True
        except Exception as exc:
            log.warning("inworld_probe_failed", voice=voice, detail=str(exc))
            return False
        log.warning("inworld_probe_empty", voice=voice)
        return False


def _decode_chunk(line: str) -> bytes | None:
    """Pull base64 PCM out of one streamed JSON line."""
    line = line.strip()
    if not line:
        return None
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line or line == "[DONE]":
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None

    body = parsed.get("result", parsed)
    encoded = body.get("audioContent") or body.get("audio_content")
    if not encoded:
        return None
    return base64.b64decode(encoded)
