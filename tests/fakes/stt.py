"""Deterministic STT fake.

Emits partials before the final so tests can exercise speculative prefetch — the
ordering guarantee real callers depend on.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from agent.interfaces.stt import Transcript

__all__ = ["FakeSTT"]


class FakeSTT:
    """Replays scripted transcripts, optionally deriving partials from the final."""

    def __init__(
        self,
        *,
        final_text: str = "میرا آرڈر کہاں ہے",
        partials: Sequence[str] | None = None,
        partial_interval_ms: int = 20,
        final_delay_ms: int = 0,
        confidence: float = 0.95,
        fail_with: Exception | None = None,
    ) -> None:
        self._final_text = final_text
        self._partials = list(partials) if partials is not None else _derive(final_text)
        self._partial_interval_ms = partial_interval_ms
        self._final_delay_ms = final_delay_ms
        self._confidence = confidence
        self._fail_with = fail_with

        self.keyterms: list[str] = []
        self.language: str | None = None
        self.closed = False
        self.frames_received = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def sample_rate(self) -> int:
        return 16_000

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keyterms: Sequence[str] = (),
    ) -> AsyncIterator[Transcript]:
        self.language = language
        self.keyterms = list(keyterms)
        if self._fail_with is not None:
            raise self._fail_with

        async def _drain() -> None:
            async for _frame in audio:
                self.frames_received += 1

        drain = asyncio.create_task(_drain())
        try:
            for partial in self._partials:
                yield Transcript(text=partial, is_final=False, confidence=self._confidence)
                if self._partial_interval_ms:
                    await asyncio.sleep(self._partial_interval_ms / 1000)

            if self._final_delay_ms:
                await asyncio.sleep(self._final_delay_ms / 1000)

            yield Transcript(
                text=self._final_text,
                is_final=True,
                confidence=self._confidence,
                speech_final=True,
            )
        finally:
            self.closed = True
            drain.cancel()


def _derive(final_text: str) -> list[str]:
    """Progressive word-by-word partials, as a real recogniser would emit."""
    words = final_text.split()
    return [" ".join(words[: i + 1]) for i in range(max(len(words) - 1, 0))]
