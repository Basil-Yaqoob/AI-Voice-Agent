"""Deterministic TTS fake with configurable TTFB and mid-stream failure."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

__all__ = ["FakeTTS"]


class FakeTTS:
    """Yields scripted audio chunks; can fail before or after the first byte."""

    def __init__(
        self,
        *,
        chunks: Sequence[bytes] = (b"aaa", b"bbb", b"ccc"),
        ttfb_ms: int = 0,
        chunk_interval_ms: int = 0,
        fail_before_first_chunk: Exception | None = None,
        fail_after_chunks: int | None = None,
        sample_rate: int = 24_000,
        name: str = "fake",
    ) -> None:
        self._chunks = list(chunks)
        self._ttfb_ms = ttfb_ms
        self._chunk_interval_ms = chunk_interval_ms
        self._fail_before = fail_before_first_chunk
        self._fail_after = fail_after_chunks
        self._sample_rate = sample_rate
        self._name = name

        self.call_count = 0
        self.texts: list[str] = []
        self.voices: list[str] = []
        self.cancelled = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return

        self.call_count += 1
        self.texts.append(text)
        self.voices.append(voice)

        if self._fail_before is not None:
            raise self._fail_before

        try:
            if self._ttfb_ms:
                await asyncio.sleep(self._ttfb_ms / 1000)

            for index, chunk in enumerate(self._chunks):
                if self._fail_after is not None and index >= self._fail_after:
                    raise RuntimeError("synthesis failed mid-utterance")
                yield chunk
                if self._chunk_interval_ms:
                    await asyncio.sleep(self._chunk_interval_ms / 1000)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        except GeneratorExit:
            self.cancelled = True
            raise
