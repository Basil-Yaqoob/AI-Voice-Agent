"""Audio sink fake that records what actually reached the caller."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

__all__ = ["FakeSink"]


class FakeSink:
    """Records played audio and how long stop() took.

    The timing matters: barge-in has a ~100ms budget to silence the line.
    """

    def __init__(self, *, chunk_delay_ms: int = 0, stop_delay_ms: int = 0) -> None:
        self._chunk_delay_ms = chunk_delay_ms
        self._stop_delay_ms = stop_delay_ms

        self.played: list[bytes] = []
        self.stop_calls = 0
        self.stopped_at: float | None = None
        self.last_play_at: float | None = None

        # Lets a barge-in test wait for audio to actually reach the caller
        # instead of sleeping and hoping. Wall-clock sleeps in a test are a
        # promise about how fast the machine is, and a loaded CI box breaks it.
        self.utterances = 0
        self._played_something = asyncio.Event()

    async def play(self, chunks: AsyncIterator[bytes]) -> None:
        async for chunk in chunks:
            self.played.append(chunk)
            self.last_play_at = time.perf_counter()
            self._played_something.set()
            if self._chunk_delay_ms:
                await asyncio.sleep(self._chunk_delay_ms / 1000)
        self.utterances += 1

    async def wait_for_audio(self, *, timeout: float = 5.0) -> None:
        """Block until at least one chunk has been played."""
        await asyncio.wait_for(self._played_something.wait(), timeout)

    async def wait_for_utterances(self, count: int, *, timeout: float = 5.0) -> None:
        """Block until ``count`` complete utterances have finished playing.

        One utterance is one sentence through the chunker, so this is how a
        test says "let the caller hear the first sentence and no more".
        """
        async def _poll() -> None:
            while self.utterances < count:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(_poll(), timeout)

    async def stop(self) -> None:
        self.stop_calls += 1
        self.stopped_at = time.perf_counter()
        if self._stop_delay_ms:
            await asyncio.sleep(self._stop_delay_ms / 1000)

    @property
    def audio(self) -> bytes:
        return b"".join(self.played)
