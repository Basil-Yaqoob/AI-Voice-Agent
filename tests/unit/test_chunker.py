"""TASK-08 chunker. Highest density of real bugs in the pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agent.interfaces.llm import Done, LLMEvent, TextDelta, ToolCall
from agent.pipeline.chunker import MAX_CHARS, MIN_CHARS, chunk_sentences, split_sentences


async def _stream(*events: LLMEvent) -> AsyncIterator[LLMEvent]:
    for event in events:
        yield event


async def _from_text(text: str, *, tokens: int = 3) -> list[str]:
    """Feed text in small pieces, as a real token stream arrives."""
    parts = [text[i : i + tokens] for i in range(0, len(text), tokens)]
    events: list[LLMEvent] = [TextDelta(p) for p in parts]
    events.append(Done())
    return [s async for s in chunk_sentences(_stream(*events))]


# ── Urdu punctuation: the silent failure ──────────────────────────────────


async def test_splits_on_urdu_full_stop() -> None:
    """۔ is U+06D4, not '.'. Missing it disables chunking for Urdu entirely -
    no error, the agent just stops pipelining and gets slow."""
    out = await _from_text("آپ کا آرڈر کل آئے گا۔ میں تفصیل بھیج دیتا ہوں۔")
    assert len(out) == 2
    assert out[0].endswith("۔")


async def test_splits_on_urdu_question_mark() -> None:
    out = await _from_text("کیا آپ کا آرڈر آ گیا ہے؟ مجھے بتائیے گا۔")
    assert len(out) == 2
    assert out[0].endswith("؟")


async def test_splits_on_latin_punctuation() -> None:
    """Code-switched output may terminate either way."""
    out = await _from_text("Your order is on the way. It arrives tomorrow morning.")
    assert len(out) == 2


# ── fragments and buffering ────────────────────────────────────────────────


async def test_short_fragment_accumulates() -> None:
    """'جی۔' alone sounds clipped; TTS prosody needs a phrase."""
    out = await _from_text("جی۔ آپ کا آرڈر کل پہنچ جائے گا انشاءاللہ۔")
    assert len(out) == 1
    assert out[0].startswith("جی۔")


def test_min_chars_respected() -> None:
    sentences, _ = split_sentences("ہاں۔ نہ۔")
    assert sentences == []


async def test_flushes_at_max_length_without_punctuation() -> None:
    """A model that forgets punctuation must not buffer the entire turn."""
    out = await _from_text("لفظ " * 120)
    assert len(out) > 1
    assert all(len(s) <= MAX_CHARS + 20 for s in out)


async def test_never_splits_mid_word() -> None:
    out = await _from_text("word " * 100)
    for sentence in out:
        assert not sentence.endswith("wor")
        for token in sentence.split():
            assert token in {"word"}


async def test_final_partial_is_flushed() -> None:
    """A turn ending without punctuation must still be spoken."""
    out = await _from_text("آپ کا آرڈر کل آئے گا")
    assert out == ["آپ کا آرڈر کل آئے گا"]


async def test_nothing_yielded_for_empty_stream() -> None:
    assert [s async for s in chunk_sentences(_stream(Done()))] == []


async def test_whitespace_only_yields_nothing() -> None:
    assert await _from_text("   \n  ") == []


# ── decimals and abbreviations must survive ────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "آپ کا bill Rs. 1,500 ہے اور کل due ہے۔",
        "قیمت 2.5 لاکھ روپے ہے، تفصیل بھیج رہا ہوں۔",
        "ورژن v1.2 پر اپڈیٹ کر دیا گیا ہے جناب۔",
    ],
)
async def test_does_not_split_inside_numbers_or_abbreviations(text: str) -> None:
    out = await _from_text(text)
    assert len(out) == 1, f"split incorrectly: {out}"


def test_abbreviation_guard_is_latin_only() -> None:
    """Urdu terminators never carry this ambiguity, so the guard must not
    suppress them."""
    sentences, _ = split_sentences("آپ کا آرڈر کل آئے گا۔ شکریہ، بہت بہت مہربانی۔")
    assert len(sentences) == 2


def test_ordinary_short_word_before_full_stop_still_splits() -> None:
    """Regression: a length-based abbreviation guard treated 'way' as an
    abbreviation and silently disabled splitting for ordinary English."""
    sentences, _ = split_sentences("Your order is on the way. It arrives tomorrow.")
    assert len(sentences) == 2


@pytest.mark.parametrize("abbrev", ["Rs", "No", "Dr", "Mr"])
def test_known_abbreviations_do_not_split(abbrev: str) -> None:
    sentences, _ = split_sentences(f"آپ کا bill {abbrev}. 1,500 ہے اور کل due ہے۔")
    assert len(sentences) == 1


def test_single_letter_initial_does_not_split() -> None:
    sentences, _ = split_sentences("Contact J. Smith about the delivery today.")
    assert len(sentences) == 1


# ── tool calls ──────────────────────────────────────────────────────────────


async def test_tool_call_never_yielded() -> None:
    """Reading a tool call aloud is the kind of defect that only appears in
    front of a client."""
    out = [
        s
        async for s in chunk_sentences(
            _stream(
                TextDelta("ایک سیکنڈ، میں دیکھتا ہوں۔ "),
                ToolCall(id="t1", name="get_order_status", arguments={"id": "4471"}),
                TextDelta("آپ کا آرڈر راستے میں ہے جناب۔"),
                Done(),
            )
        )
    ]
    assert not any("get_order_status" in s for s in out)
    assert not any("t1" in s for s in out)


async def test_buffer_flushed_before_tool_call() -> None:
    """Speech already generated must not be swallowed by the call."""
    out = [
        s
        async for s in chunk_sentences(
            _stream(
                TextDelta("ایک سیکنڈ انتظار کیجیے"),
                ToolCall(id="t1", name="lookup", arguments={}),
                Done(),
            )
        )
    ]
    assert out == ["ایک سیکنڈ انتظار کیجیے"]


async def test_text_after_tool_call_still_spoken() -> None:
    out = [
        s
        async for s in chunk_sentences(
            _stream(
                ToolCall(id="t1", name="lookup", arguments={}),
                TextDelta("آپ کا balance پانچ ہزار روپے ہے جناب۔"),
                Done(),
            )
        )
    ]
    assert len(out) == 1
    assert "balance" in out[0]


async def test_stops_at_done() -> None:
    """Anything after Done is not part of this turn."""
    out = [
        s
        async for s in chunk_sentences(
            _stream(TextDelta("پہلا جملہ مکمل ہو گیا ہے۔"), Done(), TextDelta("نہیں آنا چاہیے"))
        )
    ]
    assert len(out) == 1


# ── streaming behaviour ──────────────────────────────────────────────────────


async def test_first_sentence_emitted_before_stream_ends() -> None:
    """The point of the whole module: speak sentence one while the model is
    still writing sentence three."""
    emitted: list[str] = []

    async def slow() -> AsyncIterator[LLMEvent]:
        yield TextDelta("پہلا جملہ یہاں مکمل ہوتا ہے۔")
        assert emitted, "nothing spoken before the second sentence began"
        yield TextDelta(" دوسرا جملہ بھی مکمل ہو گیا ہے۔")
        yield Done()

    async for sentence in chunk_sentences(slow()):
        emitted.append(sentence)

    assert len(emitted) == 2


async def test_split_across_token_boundaries() -> None:
    """A terminator can arrive in its own token."""
    out = [
        s
        async for s in chunk_sentences(
            _stream(
                TextDelta("آپ کا آرڈر کل"),
                TextDelta(" آئے گا"),
                TextDelta("۔"),
                TextDelta(" شکریہ بہت زیادہ۔"),
                Done(),
            )
        )
    ]
    assert len(out) == 2


def test_split_sentences_is_pure() -> None:
    text = "پہلا جملہ مکمل ہے۔ دوسرا نامکمل"
    assert split_sentences(text) == split_sentences(text)


def test_split_sentences_returns_remainder() -> None:
    sentences, tail = split_sentences("پہلا جملہ مکمل ہے۔ دوسرا نامکمل")
    assert sentences == ["پہلا جملہ مکمل ہے۔"]
    assert tail.strip() == "دوسرا نامکمل"


def test_min_chars_below_max_chars() -> None:
    assert MIN_CHARS < MAX_CHARS
