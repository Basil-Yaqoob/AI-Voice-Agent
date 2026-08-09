"""TASK-07: semantic answer cache, including the security-relevant gate."""

from __future__ import annotations

import pytest

from agent.knowledge.cache import AnswerCache, normalize_query
from tests.fakes.knowledge import FakeRedis


@pytest.fixture
def cache() -> AnswerCache:
    return AnswerCache(FakeRedis(), ttl_s=60)


# ── the security test ──────────────────────────────────────────────────────


async def test_account_specific_answers_never_cached(cache: AnswerCache) -> None:
    """A caller hearing another caller's balance is an incident, not a bug."""
    stored = await cache.maybe_store(
        query="میرا balance کیا ہے",
        answer="آپ کا balance پندرہ سو روپے ہے",
        intent="account_balance",
    )
    assert stored is False
    assert await cache.get("میرا balance کیا ہے") is None


@pytest.mark.parametrize(
    "intent", ["account_balance", "order_status", "personal_details", "unknown_new_intent"]
)
async def test_gate_fails_closed(cache: AnswerCache, intent: str) -> None:
    """An allowlist, so an intent added later is excluded until reviewed."""
    assert await cache.maybe_store(query="q", answer="a", intent=intent) is False


@pytest.mark.parametrize("intent", ["faq", "policy", "pricing", "opening_hours"])
async def test_allowlisted_intents_cached(cache: AnswerCache, intent: str) -> None:
    assert await cache.maybe_store(query="q", answer="a", intent=intent) is True


# ── hit behaviour ────────────────────────────────────────────────────────────


async def test_exact_repeat_hits(cache: AnswerCache) -> None:
    await cache.maybe_store(query="ڈیلیوری کتنے دن میں", answer="دو سے تین دن", intent="faq")
    hit = await cache.get("ڈیلیوری کتنے دن میں")
    assert hit is not None
    assert hit.text == "دو سے تین دن"


async def test_punctuation_and_case_folded(cache: AnswerCache) -> None:
    await cache.maybe_store(query="Delivery kitne din", answer="دو سے تین دن", intent="faq")
    assert await cache.get("delivery kitne din?") is not None


async def test_urdu_punctuation_folded(cache: AnswerCache) -> None:
    """The Urdu full stop and question mark must fold like ASCII ones."""
    await cache.maybe_store(query="ڈیلیوری کتنے دن میں", answer="دو دن", intent="faq")
    assert await cache.get("ڈیلیوری کتنے دن میں؟") is not None


async def test_different_question_misses(cache: AnswerCache) -> None:
    await cache.maybe_store(query="ڈیلیوری کتنے دن میں", answer="دو دن", intent="faq")
    assert await cache.get("refund کیسے ملے گا") is None


async def test_miss_on_empty_cache(cache: AnswerCache) -> None:
    assert await cache.get("کچھ بھی") is None


async def test_ttl_applied() -> None:
    redis = FakeRedis()
    cache = AnswerCache(redis, ttl_s=123)
    await cache.maybe_store(query="q", answer="a", intent="faq")
    assert set(redis.expiries.values()) == {123}


async def test_corrupt_payload_is_a_miss_not_a_crash() -> None:
    """A malformed cache entry must never drop the call."""
    redis = FakeRedis()
    cache = AnswerCache(redis)
    redis.store[cache._key("q")] = "not json"
    assert await cache.get("q") is None


async def test_hit_ratio_tracked(cache: AnswerCache) -> None:
    await cache.maybe_store(query="q", answer="a", intent="faq")
    await cache.get("q")
    await cache.get("other")
    assert cache.hit_ratio == pytest.approx(0.5)


# ── normalisation ────────────────────────────────────────────────────────────


def test_normalize_collapses_whitespace() -> None:
    assert normalize_query("  میرا   order  ") == "میرا order"


def test_normalize_is_idempotent() -> None:
    once = normalize_query("میرا Order کہاں ہے؟")
    assert normalize_query(once) == once
