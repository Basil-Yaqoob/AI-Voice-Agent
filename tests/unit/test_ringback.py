"""The ringing tone that covers the wait before the agent's first word.

What matters here is not that a sine wave comes out. It is the handover: the
tone holds the sink's playback lock, so if it does not release it promptly the
greeting it was covering for never plays at all — a fix that would be strictly
worse than the silence it replaced.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from agent.transport.ringback import _MAX_S, Ringback, _cadence


class _FakeSink:
    """Enough of LiveKitSink to be rung into, without a room."""

    def __init__(self, sample_rate: int = 24_000) -> None:
        self.sample_rate = sample_rate
        self.frame_bytes = int(sample_rate * 20 / 1000) * 2
        self.written = bytearray()
        self.clears = 0
        self._lock = asyncio.Lock()

    async def play(self, chunks) -> None:  # type: ignore[no-untyped-def]
        async with self._lock:
            async for chunk in chunks:
                self.written += chunk
                # The real sink blocks against playout. Standing in for that
                # keeps the tone from generating a megabyte of audio in the
                # microsecond before the test stops it.
                await asyncio.sleep(0.001)

    async def stop(self) -> None:
        self.clears += 1

    async def held(self) -> bool:
        return self._lock.locked()


async def _until(predicate, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    """Wait for a condition without pinning a wall-clock guess to it."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_the_line_is_not_silent_while_the_caller_waits() -> None:
    sink = _FakeSink()
    ring = Ringback(sink)

    ring.start()
    await _until(lambda: len(sink.written) > sink.frame_bytes * 4)
    await ring.stop()

    assert any(sink.written), "the tone was all zeroes — the caller heard nothing"


@pytest.mark.asyncio
async def test_stopping_hands_the_sink_back() -> None:
    """The greeting cannot play while the tone still holds the lock."""
    sink = _FakeSink()
    ring = Ringback(sink)

    ring.start()
    await _until(lambda: sink._lock.locked())
    await ring.stop()

    assert not sink._lock.locked()
    assert not ring.is_ringing


@pytest.mark.asyncio
async def test_stopping_drops_the_tone_already_queued() -> None:
    """Otherwise the greeting starts behind a fifth of a second of ring."""
    sink = _FakeSink()
    ring = Ringback(sink)

    ring.start()
    await _until(lambda: len(sink.written) > 0)
    await ring.stop()

    assert sink.clears == 1


@pytest.mark.asyncio
async def test_stopping_a_tone_that_never_started_is_not_an_error() -> None:
    """on_speech_started calls this on every turn, ringing or not."""
    sink = _FakeSink()
    ring = Ringback(sink)

    await ring.stop()
    await ring.stop()

    assert sink.clears == 0, "a call that never rang must not clear a live utterance"


@pytest.mark.asyncio
async def test_ringing_twice_does_not_open_two_tones() -> None:
    """Two tasks in one AudioSource is the corruption LiveKitSink locks against."""
    sink = _FakeSink()
    ring = Ringback(sink)

    ring.start()
    ring.start()
    await _until(lambda: len(sink.written) > 0)
    await ring.stop()

    assert not sink._lock.locked(), "a second tone was left holding the sink"


@pytest.mark.asyncio
async def test_a_broken_sink_does_not_take_the_call_with_it() -> None:
    class _Broken(_FakeSink):
        async def play(self, chunks) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("no track published")

    ring = Ringback(_Broken())
    ring.start()
    await ring.stop()  # the tone is a courtesy; its failure is not the call's


def test_the_cadence_loops_without_a_click() -> None:
    """A loop point off a zero crossing is an audible tick every three seconds."""
    pcm = _cadence(24_000)
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)

    assert samples[0] == 0
    assert samples[-1] == 0


def test_the_cadence_is_a_whole_number_of_frames() -> None:
    """Frames are sliced straight out of it, so a remainder would split a sample."""
    for rate in (16_000, 22_050, 24_000):
        frame_bytes = int(rate * 20 / 1000) * 2
        assert len(_cadence(rate)) % frame_bytes == 0


def test_the_tone_reads_as_ringing_not_as_a_hold_signal() -> None:
    """One second on, two off — what a Pakistani caller hears before a pickup."""
    rate = 16_000
    samples = struct.unpack_from(f"<{len(_cadence(rate)) // 2}h", _cadence(rate))

    assert any(samples[:rate]), "the first second should be tone"
    assert not any(samples[rate:]), "the remaining two seconds should be silence"


def test_the_tone_gives_up_before_the_caller_does() -> None:
    """A ring that never ends hides a greeting that never arrived."""
    assert _MAX_S <= 30.0
