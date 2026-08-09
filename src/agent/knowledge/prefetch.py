"""Speculative retrieval, overlapped with endpointing.

The core idea of TASK-07. Retrieval fires on the **partial** transcript while
the caller is still speaking and while endpointing is still counting down its
180ms. By the time the final transcript arrives the results are already in
memory, so retrieval contributes ~0ms to the critical path instead of 150-250ms
we have not budgeted.

Two invariants:

* **Prefetch is an optimisation, never a correctness dependency.** If the
  caller's sentence changed meaning at the end, the speculative result is
  discarded and a fresh search runs. Being fast is worthless if the answer is
  to the wrong question.
* **Supersede, do not queue.** Each new partial cancels the in-flight search.
  Otherwise a fast speaker triggers a pile-up and you pay for all of it.
"""

from __future__ import annotations

import asyncio

from agent.interfaces.knowledge import KnowledgeStore, SearchHit
from agent.telemetry.logging import get_logger

__all__ = ["SpeculativePrefetcher"]

log = get_logger(__name__)


class SpeculativePrefetcher:
    """Overlaps retrieval with endpointing."""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        k: int = 4,
        min_chars: int = 8,
        min_words: int = 2,
        similarity_floor: float = 0.6,
        resolve_timeout_s: float = 0.15,
    ) -> None:
        self._store = store
        self._k = k
        # Character counts do not transfer across scripts: Urdu writes no short
        # vowels and uses ligatures, so "میرا آرڈر" is 9 characters where the
        # English "my order" is 8 and a 12-char guard would skip a far longer
        # Urdu utterance than intended. Word count is the script-neutral signal;
        # the character floor only screens out single stray tokens.
        self._min_chars = min_chars
        self._min_words = min_words
        self._similarity_floor = similarity_floor
        self._resolve_timeout_s = resolve_timeout_s

        self._task: asyncio.Task[list[SearchHit]] | None = None
        self._query = ""

        # Prefetch means more retrieval calls than turns. That is the trade —
        # embedding calls are cheap, latency is not. TASK-11 charts the ratio.
        self.searches_issued = 0
        self.speculative_hits = 0

    def on_partial(self, text: str) -> None:
        """Called on every interim transcript. Cheap, non-blocking, fire-and-forget."""
        candidate = text.strip()
        if candidate == self._query:
            return
        if len(candidate) < self._min_chars or len(candidate.split()) < self._min_words:
            return

        self._cancel_inflight()
        self._query = candidate
        self.searches_issued += 1
        self._task = asyncio.create_task(self._store.search(candidate, k=self._k))

    async def resolve(self, final_text: str) -> list[SearchHit]:
        """Called with the final transcript; uses the warm result if it still fits."""
        final = final_text.strip()

        if self._task is not None and _close_enough(self._query, final, self._similarity_floor):
            try:
                hits = await asyncio.wait_for(asyncio.shield(self._task), self._resolve_timeout_s)
            except (TimeoutError, asyncio.CancelledError, Exception):
                log.debug("prefetch_unusable", query=self._query)
            else:
                self.speculative_hits += 1
                self._reset()
                return hits

        # Speculation missed, timed out or failed. Correctness wins over speed.
        self._cancel_inflight()
        self._reset()
        self.searches_issued += 1
        return await self._store.search(final, k=self._k)

    def cancel(self) -> None:
        """Call at session teardown so no search outlives the call."""
        self._cancel_inflight()
        self._reset()

    def _cancel_inflight(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _reset(self) -> None:
        self._task = None
        self._query = ""


def _close_enough(speculative: str, final: str, floor: float) -> bool:
    """Whether the final transcript is still the question we speculated on.

    Deliberately conservative: a wrong-but-fast answer is worse than a slow one.
    The common case is the final simply extending the last partial by a word or
    two, which is what the prefix check catches.
    """
    if not speculative or not final:
        return False
    if final.startswith(speculative):
        return True

    spec_words = speculative.split()
    final_words = final.split()
    if not spec_words or not final_words:
        return False

    shared = sum(1 for a, b in zip(spec_words, final_words, strict=False) if a == b)
    return shared / max(len(spec_words), len(final_words)) >= floor
