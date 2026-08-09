"""Semantic answer cache.

Support calls are enormously repetitive — a small set of intents covers most
turns. A hit here skips retrieval *and* generation, which is the only cache in
the stack that removes the LLM call entirely.

**Caller-specific answers are never cached.** Balances, order status and personal
details must not be served from a shared store. A caller hearing someone else's
balance is a security incident, not a bug, so the gate is an allowlist of intents
rather than a denylist.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from agent.telemetry.logging import get_logger

__all__ = ["CACHEABLE_INTENTS", "AnswerCache", "CachedAnswer", "normalize_query"]

log = get_logger(__name__)

# Allowlist, deliberately. Anything not named here is treated as caller-specific
# and never stored, so a new intent added later fails closed.
CACHEABLE_INTENTS: frozenset[str] = frozenset(
    {
        "faq",
        "policy",
        "product_info",
        "pricing",
        "opening_hours",
        "coverage_area",
        "how_to",
    }
)

_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[.,!?;:۔،؟؛]+")


@dataclass(frozen=True)
class CachedAnswer:
    text: str
    intent: str


def normalize_query(query: str) -> str:
    """Fold trivial variation so 'میرا order کہاں ہے؟' and '... ہے' share a key."""
    folded = _PUNCT.sub(" ", query.strip().casefold())
    return _WHITESPACE.sub(" ", folded).strip()


class AnswerCache:
    """Redis-backed exact-match cache over normalised queries.

    Exact matching on a normalised key, not vector similarity. A near-miss here
    answers a question the caller did not ask, which is far worse than the
    latency it saves — the same reason the similarity threshold in the task doc
    is set conservatively high.
    """

    def __init__(self, redis: object, *, ttl_s: int = 3600, namespace: str = "ans") -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._namespace = namespace

        self.hits = 0
        self.misses = 0

    def _key(self, query: str) -> str:
        digest = hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()[:32]
        return f"{self._namespace}:{digest}"

    async def get(self, query: str) -> CachedAnswer | None:
        raw = await self._redis.get(self._key(query))  # type: ignore[attr-defined]
        if raw is None:
            self.misses += 1
            return None
        try:
            payload = json.loads(raw)
            answer = CachedAnswer(text=payload["text"], intent=payload["intent"])
        except (json.JSONDecodeError, KeyError, TypeError):
            self.misses += 1
            return None
        self.hits += 1
        return answer

    async def maybe_store(self, *, query: str, answer: str, intent: str) -> bool:
        """Store only if the intent is on the allowlist. Returns whether it was."""
        if intent not in CACHEABLE_INTENTS:
            log.debug("answer_cache_skipped", intent=intent, reason="caller_specific")
            return False
        payload = json.dumps({"text": answer, "intent": intent}, ensure_ascii=False)
        await self._redis.set(self._key(query), payload, ex=self._ttl_s)  # type: ignore[attr-defined]
        return True

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
