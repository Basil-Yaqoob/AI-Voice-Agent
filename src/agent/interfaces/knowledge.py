"""Knowledge and embedding contracts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

__all__ = ["Chunk", "EmbeddingProvider", "KnowledgeStore", "SearchHit"]


@dataclass(frozen=True)
class Chunk:
    """One indexed passage.

    ``text_en`` exists because a code-switched query matches neither a purely
    Urdu nor a purely English passage well. Indexing both roughly doubles recall
    on realistic caller utterances.

    ``keywords`` drives the keyword half of hybrid search. Pure vector search
    reliably fails on exact product names, which are the queries that matter
    most — and session 2 measured Deepgram returning English product words
    *transliterated into Urdu script*, so those spellings belong here too.
    """

    id: str
    text: str
    source: str
    text_en: str | None = None
    keywords: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    score: float


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(
        self, texts: Sequence[str], *, kind: Literal["query", "passage"] = "passage"
    ) -> list[list[float]]:
        """Embed text.

        ``kind`` is not cosmetic. Asymmetric retrieval models (the E5 family)
        are trained with distinct ``query:`` and ``passage:`` prefixes, and
        omitting them measurably degrades ranking. Providers that do not care
        may ignore it.
        """
        ...


@runtime_checkable
class KnowledgeStore(Protocol):
    async def search(self, query: str, *, k: int = 4) -> list[SearchHit]: ...
