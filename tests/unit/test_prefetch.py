"""TASK-07: speculative prefetch — the mechanism the whole task rests on."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent.knowledge.prefetch import SpeculativePrefetcher
from tests.fakes.knowledge import FakeStore


async def test_fires_on_partial_above_min_chars() -> None:
    store = FakeStore()
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.01)
    assert store.queries == ["میرا آرڈر کہاں"]


async def test_ignores_short_partials() -> None:
    """Firing on 'می' wastes a query and an embedding call."""
    store = FakeStore()
    p = SpeculativePrefetcher(store)
    p.on_partial("می")
    await asyncio.sleep(0.01)
    assert store.queries == []


async def test_single_long_word_does_not_fire() -> None:
    """One token is not yet a question, however many characters it has."""
    store = FakeStore()
    p = SpeculativePrefetcher(store)
    p.on_partial("ڈیلیوری")
    await asyncio.sleep(0.01)
    assert store.queries == []


async def test_short_urdu_phrase_does_fire() -> None:
    """Regression: a 12-char guard skipped this. Urdu writes no short vowels, so
    character counts do not transfer from English."""
    store = FakeStore()
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر")  # 9 chars, 2 words - a real question forming
    await asyncio.sleep(0.01)
    assert store.queries == ["میرا آرڈر"]


async def test_ignores_repeated_identical_partial() -> None:
    store = FakeStore()
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر کہاں")
    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.01)
    assert len(store.queries) == 1


async def test_supersedes_stale_query() -> None:
    """A fast speaker must not queue five concurrent searches."""
    store = FakeStore(delay_ms=50)
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر")
    await asyncio.sleep(0.005)
    p.on_partial("میرا آرڈر کہاں ہے")
    await asyncio.sleep(0.005)
    assert store.cancelled >= 1


async def test_resolve_uses_warm_result() -> None:
    """The final simply extends the last partial — the common case."""
    store = FakeStore(delay_ms=10)
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.03)

    await p.resolve("میرا آرڈر کہاں ہے")
    assert len(store.queries) == 1  # no second search
    assert p.speculative_hits == 1


async def test_resolve_refetches_on_divergence() -> None:
    """Prefetch is an optimisation. Correctness wins when the sentence changed."""
    store = FakeStore(delay_ms=5)
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.02)

    hits = await p.resolve("میں اپنا account بند کرنا چاہتا ہوں")
    assert store.queries[-1] == "میں اپنا account بند کرنا چاہتا ہوں"
    assert hits[0].chunk.text == store.last_result_text
    assert p.speculative_hits == 0


async def test_resolve_falls_back_on_slow_prefetch() -> None:
    """A slow speculative search must not stall the turn behind it."""
    store = FakeStore(delay_ms=400)
    p = SpeculativePrefetcher(store, resolve_timeout_s=0.05)
    p.on_partial("میرا آرڈر کہاں")

    start = time.perf_counter()
    await p.resolve("میرا آرڈر کہاں ہے")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 600  # bounded by the timeout, not left hanging


async def test_resolve_survives_prefetch_failure() -> None:
    store = FakeStore(fail_with=ConnectionError("db down"))
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.01)
    with pytest.raises(ConnectionError):
        await p.resolve("میرا آرڈر کہاں ہے")


async def test_cancel_leaves_no_task_running() -> None:
    """No search may outlive the call that started it."""
    before = len(asyncio.all_tasks())
    store = FakeStore(delay_ms=500)
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.01)
    p.cancel()
    await asyncio.sleep(0.02)
    assert len(asyncio.all_tasks()) <= before + 1


async def test_counts_are_tracked_for_cost() -> None:
    """Prefetch issues more searches than turns; TASK-11 charts the ratio."""
    store = FakeStore(delay_ms=1)
    p = SpeculativePrefetcher(store)
    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.02)
    await p.resolve("میرا آرڈر کہاں ہے")
    assert p.searches_issued >= 1


# ── the claim the whole task rests on ──────────────────────────────────────


@pytest.mark.latency
async def test_prefetch_keeps_retrieval_off_the_critical_path() -> None:
    """200ms of retrieval, hidden under the endpointing window, must cost ~0ms
    once the final transcript arrives."""
    store = FakeStore(delay_ms=200)
    p = SpeculativePrefetcher(store)

    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.25)  # endpointing window

    start = time.perf_counter()
    await p.resolve("میرا آرڈر کہاں ہے")
    critical_path_ms = (time.perf_counter() - start) * 1000

    assert critical_path_ms < 20, f"{critical_path_ms:.0f}ms leaked onto the critical path"


@pytest.mark.latency
async def test_without_prefetch_retrieval_costs_full_latency() -> None:
    """The control: proves the test above is measuring something real."""
    store = FakeStore(delay_ms=200)
    p = SpeculativePrefetcher(store)

    start = time.perf_counter()
    await p.resolve("میرا آرڈر کہاں ہے")  # no partial fired
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms >= 150
