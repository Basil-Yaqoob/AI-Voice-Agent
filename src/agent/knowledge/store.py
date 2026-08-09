"""pgvector-backed knowledge store with dual-script indexing and hybrid search.

Two design points come from measurements rather than theory:

* **Dual-script indexing.** A code-switched query matches neither a purely Urdu
  nor a purely English passage well. Each chunk is embedded in both.
* **Hybrid search.** Session 2 measured Deepgram returning English product words
  *transliterated into Urdu script* (``order`` -> ``ارڈن``). Dense vectors are
  weakest exactly on exact-name queries, and the transliteration makes Latin
  substring matching useless, so the keyword half indexes Urdu-script spellings
  of product terms alongside the Latin ones.

The embedding backend is injected. See ``agent.knowledge.embeddings`` — the
choice of model is a deployment decision, not a hard-coded one.
"""

from __future__ import annotations

from collections.abc import Sequence

import asyncpg
from pgvector.asyncpg import register_vector

from agent.interfaces.knowledge import Chunk, EmbeddingProvider, SearchHit
from agent.telemetry.logging import get_logger

__all__ = ["PgVectorStore", "schema_sql"]

log = get_logger(__name__)

# Weight of the keyword signal when blending with cosine similarity. Product-name
# queries are the ones dense retrieval most often misses, so the boost is
# deliberately large.
_KEYWORD_BOOST = 0.35


def schema_sql(dimensions: int) -> str:
    """DDL for the chunk table.

    HNSW rather than IVFFlat: better recall at low latency, and it needs no
    training pass or pre-populated table before the index can be created.
    """
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS chunks (
        id          TEXT PRIMARY KEY,
        text        TEXT NOT NULL,
        text_en     TEXT,
        source      TEXT NOT NULL,
        keywords    TEXT[] NOT NULL DEFAULT '{{}}',
        embedding   vector({dimensions}) NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS chunks_embedding_idx
        ON chunks USING hnsw (embedding vector_cosine_ops);

    CREATE INDEX IF NOT EXISTS chunks_keywords_idx
        ON chunks USING gin (keywords);
    """


class PgVectorStore:
    """Async pgvector store. One database, no extra network hop."""

    def __init__(self, pool: asyncpg.Pool, embedder: EmbeddingProvider) -> None:
        self._pool = pool
        self._embedder = embedder

    @staticmethod
    async def connect(dsn: str, embedder: EmbeddingProvider) -> PgVectorStore:
        """Connect, creating the extension and schema if the database is empty.

        Order matters. ``register_vector`` teaches asyncpg the ``vector`` type by
        looking it up in the catalogue, so it fails with "unknown type:
        public.vector" on a database where the extension does not exist yet. The
        extension must therefore be created on a plain connection first, before
        any pooled connection runs the codec registration.
        """
        bootstrap = await asyncpg.connect(dsn)
        try:
            await bootstrap.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await bootstrap.close()

        pool = await asyncpg.create_pool(dsn, init=register_vector, min_size=1, max_size=8)
        assert pool is not None
        store = PgVectorStore(pool, embedder)
        await store.ensure_schema()
        return store

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(schema_sql(self._embedder.dimensions))

    async def upsert(self, chunks: Sequence[Chunk]) -> int:
        """Embed and store. Offline path — never called during a call."""
        if not chunks:
            return 0

        # Index the Urdu and English forms together so a code-switched query has
        # something to match in either script.
        texts = [f"{c.text}\n{c.text_en}" if c.text_en else c.text for c in chunks]
        vectors = await self._embedder.embed(texts, kind="passage")

        # Keywords are casefolded on write to match _tokenize() on read.
        # Postgres INTERSECT is case-sensitive, so a capitalised keyword like
        # "Premium Plan" would never match the lowercased query tokens and the
        # boost would silently do nothing.
        folded = [[kw.casefold() for kw in c.keywords] for c in chunks]

        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO chunks (id, text, text_en, source, keywords, embedding)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO UPDATE SET
                    text = EXCLUDED.text,
                    text_en = EXCLUDED.text_en,
                    source = EXCLUDED.source,
                    keywords = EXCLUDED.keywords,
                    embedding = EXCLUDED.embedding
                """,
                [
                    (c.id, c.text, c.text_en, c.source, kws, v)
                    for c, kws, v in zip(chunks, folded, vectors, strict=True)
                ],
            )
        return len(chunks)

    async def search(self, query: str, *, k: int = 4) -> list[SearchHit]:
        """Hybrid search: cosine similarity blended with a keyword-overlap boost."""
        # kind="query" matters for asymmetric models like E5: the query and
        # passage prefixes differ, and using the wrong one degrades ranking.
        vector = (await self._embedder.embed([query], kind="query"))[0]
        tokens = _tokenize(query)

        # Two-stage retrieve-then-rerank. A blended expression in ORDER BY is not
        # a distance operator, so Postgres cannot use the HNSW index for it and
        # falls back to a sequential scan over every chunk — verified with
        # EXPLAIN. Instead: gather candidates using both indexes (HNSW for the
        # vector arm, GIN for the keyword arm), then blend over that small set.
        #
        # The keyword arm is a union rather than a filter on the vector results,
        # so a chunk whose product name matches exactly is still reachable even
        # when it falls outside the top-N by distance.
        oversample = max(k * 5, 20)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH by_vector AS (
                    SELECT id FROM chunks
                    ORDER BY embedding <=> $1     -- bare distance: uses HNSW
                    LIMIT $5
                ),
                by_keyword AS (
                    SELECT id FROM chunks
                    WHERE keywords && $2::text[]  -- array overlap: uses GIN
                    LIMIT $5
                ),
                candidates AS (
                    SELECT id FROM by_vector
                    UNION
                    SELECT id FROM by_keyword
                )
                SELECT c.id, c.text, c.text_en, c.source, c.keywords,
                       1 - (c.embedding <=> $1) AS similarity,
                       CARDINALITY(ARRAY(SELECT UNNEST(c.keywords)
                                         INTERSECT SELECT UNNEST($2::text[])))
                           AS keyword_matches
                FROM chunks c
                JOIN candidates USING (id)
                -- $3 MUST be cast. Without it Postgres infers the parameter type
                -- from the surrounding integer multiplication, truncates 0.35 to
                -- 0, and the keyword boost silently becomes a no-op.
                ORDER BY (1 - (c.embedding <=> $1))
                    + $3::float8 * LEAST(
                        CARDINALITY(ARRAY(SELECT UNNEST(c.keywords)
                                          INTERSECT SELECT UNNEST($2::text[]))),
                        2)
                    DESC
                LIMIT $4
                """,
                vector,
                tokens,
                _KEYWORD_BOOST,
                k,
                oversample,
            )

        return [
            SearchHit(
                chunk=Chunk(
                    id=r["id"],
                    text=r["text"],
                    text_en=r["text_en"],
                    source=r["source"],
                    keywords=list(r["keywords"]),
                ),
                score=float(r["similarity"]) + _KEYWORD_BOOST * min(r["keyword_matches"], 2),
            )
            for r in rows
        ]

    async def count(self) -> int:
        async with self._pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM chunks"))

    async def close(self) -> None:
        await self._pool.close()


def _tokenize(query: str) -> list[str]:
    """Lowercased word tokens for the keyword half of the hybrid search."""
    return [t.strip(".,!?;:۔،؟؛").casefold() for t in query.split() if t.strip()]
