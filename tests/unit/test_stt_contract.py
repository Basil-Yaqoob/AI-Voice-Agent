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


# ── holding the socket open ──────────────────────────────────────────────
#
# The bug these guard: Deepgram closes with 1011 after roughly ten seconds
# with no audio and no message, and that closure propagates out of stream()
# and ends the call. Audio genuinely stops — a browser with silence
# suppression sends nothing while the caller is quiet, and a caller reading a
# number off a box is quiet for longer than ten seconds. It presented as "the
# call drops a few seconds after the agent answers".


async def test_a_silent_caller_does_not_lose_the_socket() -> None:
    import asyncio

    from agent.providers.stt_deepgram import DeepgramSTT, _LastSend

    class _Socket:
        def __init__(self) -> None:
            self.keep_alives = 0

        async def send_media(self, _frame: bytes) -> None: ...

        async def send_keep_alive(self) -> None:
            self.keep_alives += 1

    stt = DeepgramSTT(api_key="k")
    socket = _Socket()
    sent = _LastSend()

    task = asyncio.create_task(stt._keep_alive(socket, sent))
    # Pretend the last write was long ago rather than waiting it out.
    sent._at -= 3600
    await asyncio.sleep(0.05)
    task.cancel()

    assert socket.keep_alives >= 1, "a quiet caller must not close the socket"


async def test_a_talking_caller_pays_nothing_for_the_keepalive() -> None:
    """It only fires on an idle socket; audio is itself a keepalive."""
    import asyncio

    from agent.providers.stt_deepgram import DeepgramSTT, _LastSend

    class _Socket:
        def __init__(self) -> None:
            self.keep_alives = 0

        async def send_keep_alive(self) -> None:
            self.keep_alives += 1

    stt = DeepgramSTT(api_key="k")
    socket = _Socket()
    sent = _LastSend()  # just written to

    task = asyncio.create_task(stt._keep_alive(socket, sent))
    await asyncio.sleep(0.05)
    task.cancel()

    assert socket.keep_alives == 0


def test_the_keepalive_beats_the_deepgram_timeout() -> None:
    """Deepgram gives about ten seconds. Anything close to that is a race."""
    from agent.providers.stt_deepgram import _KEEPALIVE_S

    assert _KEEPALIVE_S <= 5.0
