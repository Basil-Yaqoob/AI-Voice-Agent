"""TASK-05: failover behaviour — the highest-value tests in that task.

Inworld's Urdu is experimental, so this path will be exercised for real.
"""

from __future__ import annotations

import pytest

from agent.interfaces.tts import TTSProvider
from agent.providers.tts_failover import FailoverTTS
from tests.fakes import FakeTTS


async def _collect(tts: TTSProvider, text: str = "سلام") -> list[bytes]:
    return [chunk async for chunk in tts.synthesize(text, voice="ur-PK-UzmaNeural")]


def test_fake_satisfies_the_protocol() -> None:
    assert isinstance(FakeTTS(), TTSProvider)


def test_failover_satisfies_the_protocol() -> None:
    assert isinstance(FailoverTTS(FakeTTS(), FakeTTS()), TTSProvider)


async def test_uses_primary_when_healthy() -> None:
    primary = FakeTTS(chunks=[b"p1", b"p2"], name="primary")
    fallback = FakeTTS(chunks=[b"f1"], name="fallback")

    assert await _collect(FailoverTTS(primary, fallback)) == [b"p1", b"p2"]
    assert fallback.call_count == 0


async def test_fails_over_before_first_byte() -> None:
    primary = FakeTTS(fail_before_first_chunk=RuntimeError("503"), name="primary")
    fallback = FakeTTS(chunks=[b"f1", b"f2"], name="fallback")

    assert await _collect(FailoverTTS(primary, fallback)) == [b"f1", b"f2"]
    assert fallback.call_count == 1


async def test_does_not_fail_over_after_first_byte() -> None:
    """Splicing two voices mid-sentence sounds far worse than a clean failure."""
    primary = FakeTTS(chunks=[b"aaa", b"bbb"], fail_after_chunks=1, name="primary")
    fallback = FakeTTS(chunks=[b"zzz"], name="fallback")

    got: list[bytes] = []
    with pytest.raises(RuntimeError):
        async for chunk in FailoverTTS(primary, fallback).synthesize("سلام", voice="v"):
            got.append(chunk)

    assert got == [b"aaa"]
    assert fallback.call_count == 0


async def test_failover_preserves_chunk_order() -> None:
    primary = FakeTTS(fail_before_first_chunk=RuntimeError("down"), name="primary")
    fallback = FakeTTS(chunks=[b"1", b"2", b"3"], name="fallback")
    assert await _collect(FailoverTTS(primary, fallback)) == [b"1", b"2", b"3"]


async def test_both_providers_failing_propagates() -> None:
    primary = FakeTTS(fail_before_first_chunk=RuntimeError("primary down"))
    fallback = FakeTTS(fail_before_first_chunk=RuntimeError("fallback down"))
    with pytest.raises(RuntimeError, match="fallback down"):
        await _collect(FailoverTTS(primary, fallback))


async def test_empty_text_calls_neither_provider() -> None:
    primary, fallback = FakeTTS(), FakeTTS()
    assert await _collect(FailoverTTS(primary, fallback), text="   ") == []
    assert primary.call_count == 0
    assert fallback.call_count == 0


async def test_voice_forwarded_to_fallback() -> None:
    primary = FakeTTS(fail_before_first_chunk=RuntimeError("down"))
    fallback = FakeTTS()
    async for _ in FailoverTTS(primary, fallback).synthesize("سلام", voice="ur-PK-AsadNeural"):
        pass
    assert fallback.voices == ["ur-PK-AsadNeural"]


def test_failover_name_shows_both_providers() -> None:
    """Metrics slice latency by provider; the name must identify the pair."""
    name = FailoverTTS(FakeTTS(name="inworld"), FakeTTS(name="azure")).name
    assert "inworld" in name and "azure" in name
