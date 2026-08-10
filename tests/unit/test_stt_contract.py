"""TASK-04: STT contract and keyterm loading."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agent.interfaces.stt import STTProvider, Transcript
from agent.knowledge.keyterms import MAX_KEYTERMS, load_keyterms
from agent.providers.stt_deepgram import MIN_CONFIDENCE
from tests.fakes import FakeSTT


async def _silence(frames: int = 5) -> AsyncIterator[bytes]:
    for _ in range(frames):
        yield b"\x00" * 640


async def _collect(stt: FakeSTT) -> list[Transcript]:
    return [t async for t in stt.stream(_silence(), language="ur")]


# ── contract ─────────────────────────────────────────────────────────────


def test_fake_satisfies_the_protocol() -> None:
    assert isinstance(FakeSTT(), STTProvider)


async def test_partials_precede_final() -> None:
    """Prefetch depends on this ordering: partials must arrive during speech."""
    results = await _collect(FakeSTT(final_text="میرا آرڈر کہاں ہے"))
    assert not results[0].is_final
    assert results[-1].is_final


async def test_exactly_one_final_per_turn() -> None:
    results = await _collect(FakeSTT())
    assert sum(1 for r in results if r.is_final) == 1


async def test_final_carries_speech_final_flag() -> None:
    results = await _collect(FakeSTT())
    assert results[-1].speech_final is True


async def test_partials_are_progressive() -> None:
    results = await _collect(FakeSTT(final_text="ایک دو تین چار"))
    partials = [r.text for r in results if not r.is_final]
    assert partials == sorted(partials, key=len)


async def test_no_empty_transcripts() -> None:
    """Real recognisers emit blank interims during silence; never surface them."""
    assert all(r.text.strip() for r in await _collect(FakeSTT()))


async def test_language_and_keyterms_forwarded() -> None:
    stt = FakeSTT()
    async for _ in stt.stream(_silence(), language="ur", keyterms=["order", "tracking"]):
        pass
    assert stt.language == "ur"
    assert stt.keyterms == ["order", "tracking"]


async def test_audio_is_consumed() -> None:
    stt = FakeSTT(partial_interval_ms=1)
    async for _ in stt.stream(_silence(frames=8), language="ur"):
        pass
    assert stt.frames_received > 0


async def test_stream_closes_cleanly() -> None:
    stt = FakeSTT()
    await _collect(stt)
    assert stt.closed is True


async def test_provider_error_propagates() -> None:
    stt = FakeSTT(fail_with=ConnectionError("socket closed"))
    with pytest.raises(ConnectionError):
        await _collect(stt)


def test_confidence_threshold_is_sane() -> None:
    """Below this a final is treated as 'I did not hear that' and re-asked;
    feeding a garbled transcript to the LLM yields a confidently wrong answer."""
    assert 0.0 < MIN_CONFIDENCE < 1.0


# ── keyterms ──────────────────────────────────────────────────────────────


def test_keyterms_load() -> None:
    terms = load_keyterms()
    assert terms
    assert "order" in terms
    assert "tracking" in terms


def test_keyterms_skip_comments_and_blanks() -> None:
    terms = load_keyterms()
    assert not any(t.startswith("#") for t in terms)
    assert all(t.strip() for t in terms)


def test_keyterms_deduplicated() -> None:
    """Repeats waste a capped budget."""
    terms = [t.casefold() for t in load_keyterms()]
    assert len(terms) == len(set(terms))


def test_keyterms_within_limit() -> None:
    """An over-broad list biases recognition toward words that were not said."""
    assert len(load_keyterms()) <= MAX_KEYTERMS


def test_keyterms_preserve_order() -> None:
    """Brand names lead so they survive truncation.

    A generic noun like "order" is mostly recovered by context anyway. A brand
    name is not: nothing in the sentence tells Deepgram that "banadol" was
    meant to be Panadol.
    """
    terms = load_keyterms()
    assert terms.index("DVAGO") < terms.index("order")
    assert terms.index("Panadol") < terms.index("prescription")


def test_missing_keyterm_file_returns_empty(tmp_path: Path) -> None:
    """A missing file must degrade to no prompting, not crash the worker."""
    assert load_keyterms(tmp_path / "nope.txt") == []


def test_keyterms_include_multiword_product_names() -> None:
    assert "Nan Pro" in load_keyterms()
    assert "cash on delivery" in load_keyterms()
