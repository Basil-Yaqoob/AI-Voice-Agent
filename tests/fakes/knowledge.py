"""Fakes for the knowledge layer."""

from __future__ import annotations

import asyncio

from agent.interfaces.knowledge import Chunk, SearchHit

__all__ = ["FakeRedis", "FakeStore"]


class FakeStore:
    """Records queries and can be given a configurable delay."""

    def __init__(self, *, delay_ms: int = 0, fail_with: Exception | None = None) -> None:
        self._delay_ms = delay_ms
        self._fail_with = fail_with
        self.queries: list[str] = []
        self.cancelled = 0

    async def search(self, query: str, *, k: int = 4) -> list[SearchHit]:
        self.queries.append(query)
        if self._fail_with is not None:
            raise self._fail_with
        try:
            if self._delay_ms:
                await asyncio.sleep(self._delay_ms / 1000)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return [
            SearchHit(
                chunk=Chunk(id=f"c{i}", text=f"result {i} for {query}", source="fake"),
                score=1.0 - i * 0.1,
            )
            for i in range(k)
        ]

    @property
    def last_result_text(self) -> str:
        return f"result 0 for {self.queries[-1]}"


class FakeRedis:
    """In-memory stand-in for the bits of redis-py AnswerCache uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.expiries[key] = ex
