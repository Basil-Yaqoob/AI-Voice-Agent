"""The 800ms budget, encoded as tests.

Fakes carry realistic per-stage delays, so what is actually being measured is
the *orchestration*: an accidental serialisation of two things that should
overlap shows up here even though every component is individually fast.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from agent.config import Settings
from agent.interfaces.llm import CachedPrompt
from agent.knowledge.prefetch import SpeculativePrefetcher
from agent.pipeline.endpointing import AdaptiveEndpointer
from agent.pipeline.turn import TurnRunner
from tests.fakes import FakeLLM, FakeTTS
from tests.fakes.audio import FakeSink
from tests.fakes.knowledge import FakeStore

pytestmark = pytest.mark.latency

SYSTEM = CachedPrompt(prefix="You are an agent." * 100)
VOICE = "ur-PK-UzmaNeural"

# The budget from RESEARCH.md:
#   50 network + 180 endpointing + 100 STT + 300 LLM TTFT + 110 TTS TTFB + 60 egress
BUDGET_MS = 800


async def test_time_to_first_audio_within_budget(settings: Settings) -> None:
    """End of caller speech to first syllable out."""
    llm = FakeLLM(script=["آپ کا آرڈر کل آئے گا جناب۔"], ttft_ms=280, tokens_per_sec=0)
    tts = FakeTTS(ttfb_ms=100)
    sink = FakeSink()

    first_audio: list[float] = []

    class Timed(FakeSink):
        async def play(self, chunks):  # type: ignore[no-untyped-def]
            async for chunk in chunks:
                if not first_audio:
                    first_audio.append(time.perf_counter())
                self.played.append(chunk)

    timed = Timed()
    start = time.perf_counter()
    await TurnRunner(llm=llm, tts=tts, sink=timed, voice=VOICE).run([], system=SYSTEM)

    ttfa_ms = (first_audio[0] - start) * 1000
    print(f"\n  time to first audio: {ttfa_ms:.0f}ms (LLM 280 + TTS 100 of {BUDGET_MS} budget)")
    # 380ms of provider latency plus orchestration must still leave room for the
    # 180ms endpointing and ~110ms of network the fakes do not simulate.
    assert ttfa_ms < BUDGET_MS - settings.endpoint_silence_ms
    assert sink.stop_calls == 0


async def test_retrieval_stays_off_the_critical_path() -> None:
    """200ms of retrieval hidden under the endpointing window must cost ~0ms."""
    store = FakeStore(delay_ms=200)
    prefetcher = SpeculativePrefetcher(store)

    prefetcher.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.25)

    start = time.perf_counter()
    await prefetcher.resolve("میرا آرڈر کہاں ہے")
    leaked_ms = (time.perf_counter() - start) * 1000
    print(f"\n  retrieval on critical path: {leaked_ms:.0f}ms of 200ms")
    assert leaked_ms < 20


async def test_endpointing_stays_within_its_slice(settings: Settings) -> None:
    """The adaptive window must not exceed the budgeted slice on the common
    case, or the whole allocation shifts."""
    ep = AdaptiveEndpointer()
    common = ["میرا آرڈر کہاں ہے", "یہ کام ہو گیا ہے", "مجھے refund چاہیے"]
    assert all(ep.silence_threshold_ms(p) <= settings.endpoint_silence_ms for p in common)


async def test_sentences_pipeline_rather_than_serialise() -> None:
    """Three sentences must not cost three full TTS round trips before the
    first one is heard."""
    llm = FakeLLM(
        script=["پہلا جملہ مکمل ہے۔ دوسرا جملہ مکمل ہے۔ تیسرا جملہ مکمل ہے۔"],
        ttft_ms=50,
        tokens_per_sec=0,
    )
    tts = FakeTTS(ttfb_ms=80)
    first_audio: list[float] = []

    class Timed(FakeSink):
        async def play(self, chunks):  # type: ignore[no-untyped-def]
            async for chunk in chunks:
                if not first_audio:
                    first_audio.append(time.perf_counter())
                self.played.append(chunk)

    start = time.perf_counter()
    await TurnRunner(llm=llm, tts=tts, sink=Timed(), voice=VOICE).run([], system=SYSTEM)
    total_ms = (time.perf_counter() - start) * 1000
    ttfa_ms = (first_audio[0] - start) * 1000

    print(f"\n  first audio {ttfa_ms:.0f}ms, whole turn {total_ms:.0f}ms")
    assert ttfa_ms < 200, "first audio waited on later sentences"
    assert total_ms > ttfa_ms, "sanity: the turn outlasts its first sentence"


async def test_barge_in_silences_the_line_quickly() -> None:
    """A caller who interrupts must stop hearing the agent almost immediately."""
    from agent.pipeline.interrupt import InterruptController

    llm = FakeLLM(script=["ایک لمبا جواب " * 30], tokens_per_sec=60)
    sink = FakeSink(chunk_delay_ms=5)
    runner = TurnRunner(llm=llm, tts=FakeTTS(), sink=sink, voice=VOICE)
    controller = InterruptController(sink)

    controller.track(asyncio.create_task(runner.run([], system=SYSTEM)), runner)
    await asyncio.sleep(0.05)

    start = time.perf_counter()
    await controller.interrupt()
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"\n  barge-in to silence: {elapsed_ms:.0f}ms")
    assert elapsed_ms < 100
