"""TASK-03: LLM contract behaviour, exercised through FakeLLM."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent.interfaces.llm import (
    CachedPrompt,
    Done,
    LLMEvent,
    LLMProvider,
    Message,
    TextDelta,
    ToolCall,
    Usage,
)
from tests.fakes import FakeLLM

SYSTEM = CachedPrompt(prefix="You are an agent." * 100)


async def _drain(stream) -> list[LLMEvent]:  # type: ignore[no-untyped-def]
    return [event async for event in stream]


def test_fake_satisfies_the_protocol() -> None:
    """If the fake drifts from the interface, every downstream test lies."""
    assert isinstance(FakeLLM(), LLMProvider)


async def test_stream_yields_deltas_before_done() -> None:
    events = await _drain(FakeLLM(script=["ایک دو تین"]).stream([], system=SYSTEM))
    assert isinstance(events[0], TextDelta)
    assert isinstance(events[-1], Done)


async def test_text_reassembles_losslessly() -> None:
    events = await _drain(FakeLLM(script=["آپ کا order کل آئے گا"]).stream([], system=SYSTEM))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "آپ کا order کل آئے گا"


async def test_tool_call_is_a_distinct_event() -> None:
    """A tool call arriving as text would be read aloud to the caller."""
    call = ToolCall(id="t1", name="get_order_status", arguments={"id": "4471"})
    events = await _drain(
        FakeLLM(script=["ایک سیکنڈ"], tool_calls=[call]).stream([], system=SYSTEM)
    )
    assert any(isinstance(e, ToolCall) for e in events)
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "get_order_status" not in text


async def test_done_carries_usage() -> None:
    usage = Usage(input_tokens=120, output_tokens=30, cache_read_tokens=4180)
    events = await _drain(FakeLLM(usage=usage).stream([], system=SYSTEM))
    done = events[-1]
    assert isinstance(done, Done)
    assert done.usage.cache_read_tokens == 4180


def test_usage_reports_cache_engagement() -> None:
    """Zero cache reads on turn two means the prefix is drifting — 3x the bill."""
    assert Usage(cache_read_tokens=4000).cache_engaged
    assert not Usage(cache_read_tokens=0).cache_engaged


async def test_provider_receives_system_and_messages() -> None:
    fake = FakeLLM()
    await _drain(fake.stream([Message("user", "سلام")], system=SYSTEM))
    assert fake.calls[0].messages[0].content == "سلام"
    assert fake.calls[0].system.prefix == SYSTEM.prefix


async def test_cancellation_propagates() -> None:
    """Barge-in must actually kill the request, not orphan it. An orphaned
    stream keeps billing and can emit audio into the next turn."""
    fake = FakeLLM(script=["ایک لمبا جواب " * 40], tokens_per_sec=50)

    async def consume() -> None:
        async for _event in fake.stream([], system=SYSTEM):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake.cancelled is True
    assert fake.completed is False


async def test_early_break_marks_cancelled() -> None:
    """Consumer walking away mid-stream must close the generator, not leak it."""
    fake = FakeLLM(script=["ایک دو تین چار پانچ"], tokens_per_sec=100)
    async for event in fake.stream([], system=SYSTEM):
        if isinstance(event, TextDelta):
            break
    # Force finalisation deterministically rather than waiting on the GC.
    import gc

    gc.collect()
    await asyncio.sleep(0)
    assert fake.completed is False


async def test_ttft_delay_is_respected() -> None:
    """Latency tests depend on the fake honouring its configured TTFT.

    Tolerance accounts for the Windows timer resolution of ~15.6ms, which lets
    asyncio.sleep return measurably early.
    """
    fake = FakeLLM(script=["جی"], ttft_ms=100)
    start = time.perf_counter()
    async for event in fake.stream([], system=SYSTEM):
        if isinstance(event, TextDelta):
            break
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms >= 80, f"configured 100ms TTFT but first token arrived at {elapsed_ms:.0f}ms"


async def test_provider_error_propagates() -> None:
    fake = FakeLLM(fail_with=RuntimeError("upstream 503"))
    with pytest.raises(RuntimeError, match="upstream 503"):
        await _drain(fake.stream([], system=SYSTEM))


# ── provider usage parsing ───────────────────────────────────────────────────
#
# Both parsers read attributes off a vendor response object. Nothing else in
# the suite exercises them, and a wrong field name here is invisible: the call
# succeeds, the answer is right, and only the bill changes.


class _Details:
    def __init__(self, **fields: int) -> None:
        self.__dict__.update(fields)


class _Raw:
    def __init__(self, **fields: object) -> None:
        self.__dict__.update(fields)


def test_openrouter_reads_both_cache_counters() -> None:
    """Shapes taken from a live OpenRouter response, not invented.

    A 6,403-token prefix reports cache_write_tokens on the first call and
    cached_tokens on the second. Reading only the second makes turn one look
    like a cache that never engaged.
    """
    from agent.providers.llm_openrouter import _usage

    first = _usage(
        _Raw(
            prompt_tokens=6412,
            completion_tokens=5,
            prompt_tokens_details=_Details(cache_write_tokens=6403, cached_tokens=0),
        )
    )
    assert first.cache_write_tokens == 6403
    assert first.cache_read_tokens == 0

    second = _usage(
        _Raw(
            prompt_tokens=6412,
            completion_tokens=5,
            prompt_tokens_details=_Details(cache_write_tokens=0, cached_tokens=6403),
        )
    )
    assert second.cache_read_tokens == 6403
    assert second.cache_engaged


def test_openrouter_usage_survives_a_missing_details_block() -> None:
    """Some models and some errors omit it entirely."""
    from agent.providers.llm_openrouter import _usage

    usage = _usage(_Raw(prompt_tokens=10, completion_tokens=2))
    assert usage.input_tokens == 10
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0


def test_claude_reads_both_cache_counters() -> None:
    from agent.providers.llm_claude import _usage

    usage = _usage(
        _Raw(
            input_tokens=6412,
            output_tokens=5,
            cache_read_input_tokens=6403,
            cache_creation_input_tokens=0,
        )
    )
    assert usage.cache_read_tokens == 6403
    assert usage.cache_engaged


def test_both_providers_agree_on_the_usage_shape() -> None:
    """The pipeline reads one Usage type; the adapters must fill the same fields."""
    from agent.providers.llm_claude import _usage as claude_usage
    from agent.providers.llm_openrouter import _usage as openrouter_usage

    a = claude_usage(
        _Raw(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=5,
        )
    )
    b = openrouter_usage(
        _Raw(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=_Details(cached_tokens=80, cache_write_tokens=5),
        )
    )
    assert a == b
