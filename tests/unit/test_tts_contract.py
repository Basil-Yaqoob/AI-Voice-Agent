"""TASK-05: TTS streaming contract."""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.fakes import FakeTTS


async def test_yields_chunks_not_one_blob() -> None:
    chunks = [c async for c in FakeTTS(chunks=[b"a", b"b", b"c"]).synthesize("س", voice="v")]
    assert len(chunks) == 3


async def test_empty_text_yields_nothing_and_makes_no_call() -> None:
    tts = FakeTTS()
    assert [c async for c in tts.synthesize("", voice="v")] == []
    assert tts.call_count == 0


async def test_whitespace_only_text_makes_no_call() -> None:
    tts = FakeTTS()
    assert [c async for c in tts.synthesize("  \n ", voice="v")] == []
    assert tts.call_count == 0


def test_sample_rate_declared() -> None:
    assert FakeTTS(sample_rate=24_000).sample_rate == 24_000


async def test_ttfb_delay_respected() -> None:
    tts = FakeTTS(chunks=[b"a"], ttfb_ms=50)
    start = time.perf_counter()
    async for _ in tts.synthesize("سلام", voice="v"):
        break
    assert (time.perf_counter() - start) * 1000 >= 45


async def test_cancellation_stops_synthesis() -> None:
    """Barge-in must stop audio, not let a buffered stream keep playing."""
    tts = FakeTTS(chunks=[b"x"] * 50, chunk_interval_ms=5)

    async def consume() -> None:
        async for _ in tts.synthesize("لمبا جملہ", voice="v"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tts.cancelled is True


async def test_text_and_voice_recorded() -> None:
    tts = FakeTTS()
    async for _ in tts.synthesize("آپ کا order", voice="ur-PK-UzmaNeural"):
        pass
    assert tts.texts == ["آپ کا order"]
    assert tts.voices == ["ur-PK-UzmaNeural"]


async def test_mid_stream_failure_raises_after_partial_output() -> None:
    tts = FakeTTS(chunks=[b"a", b"b", b"c"], fail_after_chunks=2)
    got: list[bytes] = []
    with pytest.raises(RuntimeError):
        async for chunk in tts.synthesize("سلام", voice="v"):
            got.append(chunk)
    assert got == [b"a", b"b"]
