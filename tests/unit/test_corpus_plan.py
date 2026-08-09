"""TASK-07 step 1: decide whether retrieval is needed at all.

Built first, deliberately. If the corpus fits the cached prefix, retrieval is
strictly worse on both latency and cost, and building it would be speculative
work.
"""

from __future__ import annotations

from agent.knowledge.corpus import (
    CHARS_PER_TOKEN,
    MAX_CACHED_CORPUS_TOKENS,
    estimate_tokens,
    plan_for_corpus,
)
from agent.language.prompts import MIN_CACHEABLE_CHARS


def test_small_corpus_uses_cached_prefix() -> None:
    plan = plan_for_corpus("Acme policy text. " * 300)
    assert plan.strategy == "cached_prefix"
    assert not plan.needs_retrieval


def test_large_corpus_requires_retrieval() -> None:
    corpus = "x" * ((MAX_CACHED_CORPUS_TOKENS + 5_000) * CHARS_PER_TOKEN)
    plan = plan_for_corpus(corpus)
    assert plan.needs_retrieval
    assert "ceiling" in plan.reason


def test_boundary_stays_on_cached_prefix() -> None:
    corpus = "x" * (MAX_CACHED_CORPUS_TOKENS * CHARS_PER_TOKEN)
    assert plan_for_corpus(corpus).strategy == "cached_prefix"


def test_tiny_corpus_warns_it_will_not_cache() -> None:
    """Inlined but below the cache minimum: billed at full input price every
    request, with no error to tell you."""
    plan = plan_for_corpus("Acme sells internet.")
    assert plan.strategy == "cached_prefix"
    assert "NOT cached" in plan.reason


def test_reason_is_always_actionable() -> None:
    for corpus in ("tiny", "medium " * 2_000, "big " * 200_000):
        assert len(plan_for_corpus(corpus).reason) > 20


def test_token_estimate_is_monotonic() -> None:
    assert estimate_tokens("a" * 400) < estimate_tokens("a" * 4_000)


def test_cache_minimum_is_shared_with_prompts() -> None:
    """The threshold must not be duplicated — a drift here silently disagrees
    with what the LLM adapter actually does."""
    assert MIN_CACHEABLE_CHARS > 0
    assert plan_for_corpus("x" * (MIN_CACHEABLE_CHARS - 1)).reason.count("NOT cached") == 1
    assert "NOT cached" not in plan_for_corpus("x" * (MIN_CACHEABLE_CHARS + 1)).reason
