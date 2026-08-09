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

    async def play(self, chunks: AsyncIterator[bytes]) -> None:
        async for chunk in chunks:
            self.played.append(chunk)
            self.last_play_at = time.perf_counter()
            if self._chunk_delay_ms:
                await asyncio.sleep(self._chunk_delay_ms / 1000)

    async def stop(self) -> None:
        self.stop_calls += 1
        self.stopped_at = time.perf_counter()
        if self._stop_delay_ms:
            await asyncio.sleep(self._stop_delay_ms / 1000)

    @property
    def audio(self) -> bytes:
        return b"".join(self.played)
