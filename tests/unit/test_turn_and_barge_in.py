"""TASK-08 turn execution and barge-in."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent.interfaces.llm import CachedPrompt, Message, ToolCall
from agent.pipeline.interrupt import InterruptController
from agent.pipeline.turn import TurnRunner
from tests.fakes import FakeLLM, FakeTTS
from tests.fakes.audio import FakeSink

SYSTEM = CachedPrompt(prefix="You are an agent." * 100)
VOICE = "ur-PK-UzmaNeural"

THREE_SENTENCES = "پہلا حصہ مکمل ہو گیا۔ دوسرا حصہ بھی مکمل ہے۔ تیسرا حصہ آخری ہے۔"


def _runner(llm: FakeLLM, sink: FakeSink, tts: FakeTTS | None = None) -> TurnRunner:
    return TurnRunner(llm=llm, tts=tts or FakeTTS(), sink=sink, voice=VOICE)


# ── normal completion ──────────────────────────────────────────────────────


async def test_turn_speaks_every_sentence() -> None:
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=0)
    tts = FakeTTS()
    result = await _runner(llm, FakeSink(), tts).run([Message("user", "سلام")], system=SYSTEM)
    assert result.completed
    assert len(tts.texts) == 3


async def test_audio_reaches_the_sink() -> None:
    sink = FakeSink()
    llm = FakeLLM(script=["آپ کا آرڈر کل آئے گا جناب۔"], tokens_per_sec=0)
    await _runner(llm, sink).run([], system=SYSTEM)
    assert sink.audio


async def test_text_is_normalised_before_synthesis() -> None:
    """Numbers must be words by the time TTS sees them."""
    tts = FakeTTS()
    llm = FakeLLM(script=["آپ کا balance 1500 روپے ہے جناب۔"], tokens_per_sec=0)
    await _runner(llm, FakeSink(), tts).run([], system=SYSTEM)
    spoken = " ".join(tts.texts)
    assert "1500" not in spoken
    assert "پندرہ سو" in spoken


async def test_usage_captured() -> None:
    llm = FakeLLM(script=["جی بالکل ہو جائے گا۔"], tokens_per_sec=0)
    result = await _runner(llm, FakeSink()).run([], system=SYSTEM)
    assert result.usage.cache_read_tokens > 0


async def test_tool_calls_captured_but_never_spoken() -> None:
    call = ToolCall(id="t1", name="get_order_status", arguments={"id": "4471"})
    llm = FakeLLM(script=["ایک سیکنڈ انتظار کیجیے۔"], tool_calls=[call], tokens_per_sec=0)
    tts = FakeTTS()
    result = await _runner(llm, FakeSink(), tts).run([], system=SYSTEM)
    assert [c.name for c in result.tool_calls] == ["get_order_status"]
    assert not any("get_order_status" in t for t in tts.texts)


# ── barge-in: the subtle part ───────────────────────────────────────────────


async def test_history_records_spoken_not_intended() -> None:
    """The subtle one. Recording the full generated response makes the agent
    later reference information the caller never heard."""
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=80)
    sink = FakeSink()
    runner = _runner(llm, sink)
    controller = InterruptController(sink)

    controller.track(asyncio.create_task(runner.run([], system=SYSTEM)), runner)
    await asyncio.sleep(0.09)  # long enough for sentence one, not all three
    partial = await controller.interrupt()

    assert partial is not None, "the spoken record was lost on cancellation"
    assert partial.interrupted
    assert "پہلا حصہ" in partial.spoken
    assert "تیسرا حصہ" not in partial.spoken, "recorded speech the caller never heard"


async def test_spoken_record_survives_cancellation() -> None:
    """Regression: run() raises on cancel, so the return value is gone. Without
    reading it off the runner the session records nothing at all."""
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=80)
    sink = FakeSink()
    runner = _runner(llm, sink)

    task = asyncio.create_task(runner.run([], system=SYSTEM))
    await asyncio.sleep(0.09)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runner.result.spoken, "runner lost the partial record"


async def test_controller_stops_audio_before_cancelling() -> None:
    """Cancelling first leaves buffered audio playing, so the caller keeps
    hearing an agent they have already interrupted."""
    sink = FakeSink(chunk_delay_ms=8)
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=60)
    runner = _runner(llm, sink)
    controller = InterruptController(sink)

    controller.track(asyncio.create_task(runner.run([], system=SYSTEM)))
    await asyncio.sleep(0.05)
    await controller.interrupt()

    assert sink.stop_calls == 1
    assert sink.stopped_at is not None
    # stop() landed no later than the final chunk that got through
    assert sink.last_play_at is None or sink.stopped_at >= sink.last_play_at - 0.05


async def test_interrupt_cancels_the_llm_stream() -> None:
    """An orphaned stream keeps billing and can emit audio into the next turn."""
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=40)
    sink = FakeSink()
    controller = InterruptController(sink)

    controller.track(asyncio.create_task(_runner(llm, sink).run([], system=SYSTEM)))
    await asyncio.sleep(0.05)
    await controller.interrupt()

    assert llm.cancelled is True
    assert llm.completed is False


async def test_interrupt_within_budget() -> None:
    """~100ms to silence the line."""
    sink = FakeSink(chunk_delay_ms=5)
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=60)
    controller = InterruptController(sink)
    controller.track(asyncio.create_task(_runner(llm, sink).run([], system=SYSTEM)))
    await asyncio.sleep(0.04)

    start = time.perf_counter()
    await controller.interrupt()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 100, f"barge-in took {elapsed_ms:.0f}ms"


async def test_no_task_leak_after_interrupt() -> None:
    before = len(asyncio.all_tasks())
    sink = FakeSink()
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=30)
    controller = InterruptController(sink)
    controller.track(asyncio.create_task(_runner(llm, sink).run([], system=SYSTEM)))
    await asyncio.sleep(0.03)
    await controller.interrupt()
    await asyncio.sleep(0.02)
    assert len(asyncio.all_tasks()) <= before + 1


async def test_interrupt_with_no_turn_running_is_safe() -> None:
    assert await InterruptController(FakeSink()).interrupt() is None


async def test_interrupt_after_completion_returns_the_result() -> None:
    """The turn may finish between the caller speaking and us reacting."""
    llm = FakeLLM(script=["مختصر جواب یہاں ہے۔"], tokens_per_sec=0)
    sink = FakeSink()
    controller = InterruptController(sink)
    task = asyncio.create_task(_runner(llm, sink).run([], system=SYSTEM))
    controller.track(task)
    await task

    result = await controller.interrupt()
    assert result is not None
    assert result.completed


async def test_finish_returns_the_result() -> None:
    llm = FakeLLM(script=["جی بالکل ہو جائے گا۔"], tokens_per_sec=0)
    sink = FakeSink()
    controller = InterruptController(sink)
    controller.track(asyncio.create_task(_runner(llm, sink).run([], system=SYSTEM)))
    result = await controller.finish()
    assert result is not None and result.completed


async def test_is_running_tracks_the_task() -> None:
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=40)
    sink = FakeSink()
    controller = InterruptController(sink)
    assert not controller.is_running
    controller.track(asyncio.create_task(_runner(llm, sink).run([], system=SYSTEM)))
    assert controller.is_running
    await controller.interrupt()
    assert not controller.is_running


# ── failure handling ─────────────────────────────────────────────────────────


async def test_llm_failure_propagates_for_the_session_to_voice() -> None:
    """The session speaks a recovery line; the turn must not swallow the error."""
    llm = FakeLLM(fail_with=RuntimeError("provider 503"))
    with pytest.raises(RuntimeError, match="503"):
        await _runner(llm, FakeSink()).run([], system=SYSTEM)


async def test_tts_failure_propagates() -> None:
    llm = FakeLLM(script=["آپ کا آرڈر کل آئے گا جناب۔"], tokens_per_sec=0)
    tts = FakeTTS(fail_before_first_chunk=RuntimeError("tts down"))
    with pytest.raises(RuntimeError, match="tts down"):
        await _runner(llm, FakeSink(), tts).run([], system=SYSTEM)


# ── streaming: the latency claim ─────────────────────────────────────────────


@pytest.mark.latency
async def test_tts_starts_before_the_model_finishes() -> None:
    """Proves the pipeline is pipelining rather than buffering the whole turn."""
    llm = FakeLLM(script=[THREE_SENTENCES], tokens_per_sec=25)
    tts = FakeTTS()
    sink = FakeSink()

    first_audio: list[float] = []

    class Watching(FakeSink):
        async def play(self, chunks):  # type: ignore[no-untyped-def]
            if not first_audio:
                first_audio.append(time.perf_counter())
            await super().play(chunks)

    watching = Watching()
    start = time.perf_counter()
    await TurnRunner(llm=llm, tts=tts, sink=watching, voice=VOICE).run([], system=SYSTEM)
    total = time.perf_counter() - start

    assert first_audio, "no audio was played"
    assert first_audio[0] - start < total * 0.7, "audio waited for the full response"
    assert sink.stop_calls == 0
