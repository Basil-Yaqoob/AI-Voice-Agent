"""Decide whether this deployment needs retrieval at all.

Build this before any vector machinery. Company knowledge bases are usually
smaller than teams assume, and if the whole thing fits in the cached prompt
prefix then retrieval is strictly worse on both axes:

* **Latency** — a cache read costs nothing on the critical path; a retrieval
  round trip costs 150-250ms we have not budgeted.
* **Cost** — cached input bills at 0.1x. Retrieval adds embedding calls and a
  database on top of full-price input tokens for the retrieved chunks.

Retrieval is the fallback for corpora too large to cache, not the default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent.language.prompts import MIN_CACHEABLE_CHARS

__all__ = ["CHARS_PER_TOKEN", "MAX_CACHED_CORPUS_TOKENS", "CorpusPlan", "plan_for_corpus"]

# Rough English-dominant ratio. Urdu script tokenises denser, so this
# *overestimates* the token count for Urdu text, which errs toward retrieval —
# the safe direction, since an over-large prefix is a hard API failure.
CHARS_PER_TOKEN = 4

# Above this the prefix bloats every request and slows time-to-first-token even
# when cached, because the model still has to attend over it.
MAX_CACHED_CORPUS_TOKENS = 40_000


@dataclass(frozen=True)
class CorpusPlan:
    strategy: Literal["cached_prefix", "retrieval"]
    corpus_tokens: int
    reason: str

    @property
    def needs_retrieval(self) -> bool:
        return self.strategy == "retrieval"


def estimate_tokens(corpus: str) -> int:
    return len(corpus) // CHARS_PER_TOKEN


def plan_for_corpus(corpus: str) -> CorpusPlan:
    """Choose between the cached-prefix route and building a retrieval index."""
    tokens = estimate_tokens(corpus)

    if tokens > MAX_CACHED_CORPUS_TOKENS:
        return CorpusPlan(
            strategy="retrieval",
            corpus_tokens=tokens,
            reason=(
                f"corpus is ~{tokens:,} tokens, over the {MAX_CACHED_CORPUS_TOKENS:,} "
                "ceiling; a prefix this large slows TTFT even when cached"
            ),
        )

    if len(corpus) < MIN_CACHEABLE_CHARS:
        # Small enough to inline, but too small for the cache to engage — so it
        # bills at full input price on every single request. Still cheaper than
        # standing up retrieval, but worth saying out loud.
        return CorpusPlan(
            strategy="cached_prefix",
            corpus_tokens=tokens,
            reason=(
                f"corpus is only {len(corpus)} chars, below the "
                f"{MIN_CACHEABLE_CHARS}-char cache minimum: it will be inlined but "
                "NOT cached, so every request pays full input price for it"
            ),
        )

    return CorpusPlan(
        strategy="cached_prefix",
        corpus_tokens=tokens,
        reason=(
            f"corpus is ~{tokens:,} tokens and caches cleanly; retrieval would add "
            "a network hop and cost more"
        ),
    )
