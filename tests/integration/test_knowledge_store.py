"""TASK-07: pgvector store against real Postgres.

Needs `.\\make.cmd dev`. Uses HashEmbedder so the SQL, the HNSW index and the
hybrid ranking are all exercised without committing to an embedding model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest

from agent.interfaces.knowledge import Chunk
from agent.knowledge.embeddings import HashEmbedder
from agent.knowledge.store import PgVectorStore

DSN = "postgresql://agent:localdev@127.0.0.1:55432/voiceagent"

CHUNKS = [
    Chunk(
        id="c1",
        text="ڈیلیوری میں دو سے تین working days لگتے ہیں۔",
        text_en="Delivery takes two to three working days.",
        source="policy",
        keywords=["delivery", "ڈیلیوری", "order", "ارڈن"],
    ),
    Chunk(
        id="c2",
        text="Premium Plan میں unlimited data شامل ہے۔",
        text_en="The Premium Plan includes unlimited data.",
        source="products",
        keywords=["premium", "plan", "پریمیم", "data"],
    ),
    Chunk(
        id="c3",
        text="Refund سات دن کے اندر process ہوتا ہے۔",
        text_en="Refunds are processed within seven days.",
        source="policy",
        keywords=["refund", "ریفنڈ"],
    ),
]


@pytest.fixture
async def store() -> AsyncIterator[PgVectorStore]:
    try:
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2, timeout=3)
    except Exception:
        pytest.skip("Postgres not reachable - start it with .\\make.cmd dev")

    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS chunks")
    await pool.close()

    s = await PgVectorStore.connect(DSN, HashEmbedder())
    try:
        yield s
    finally:
        await s.close()


async def test_schema_creates_with_hnsw_index(store: PgVectorStore) -> None:
    async with store._pool.acquire() as conn:
        indexes = await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks'"
        )
    defs = " ".join(r["indexdef"] for r in indexes)
    assert "hnsw" in defs.lower(), "HNSW index missing - recall and latency both suffer"
    assert "gin" in defs.lower(), "keyword GIN index missing - hybrid search degrades"


async def test_upsert_then_count(store: PgVectorStore) -> None:
    assert await store.upsert(CHUNKS) == 3
    assert await store.count() == 3


async def test_upsert_is_idempotent(store: PgVectorStore) -> None:
    await store.upsert(CHUNKS)
    await store.upsert(CHUNKS)
    assert await store.count() == 3


async def test_search_returns_ranked_hits(store: PgVectorStore) -> None:
    await store.upsert(CHUNKS)
    hits = await store.search("delivery working days", k=3)
    assert hits
    assert hits[0].chunk.id == "c1"
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)


async def test_keyword_boost_finds_exact_product_name(store: PgVectorStore) -> None:
    """Dense retrieval is weakest exactly on product-name queries, which are the
    ones that matter most."""
    await store.upsert(CHUNKS)
    hits = await store.search("Premium Plan", k=3)
    assert hits[0].chunk.id == "c2"


async def test_urdu_script_keyword_matches(store: PgVectorStore) -> None:
    """Deepgram returns English product words transliterated into Urdu script,
    so the Urdu-script spellings must be indexed too."""
    await store.upsert(CHUNKS)
    hits = await store.search("ریفنڈ کب ملے گا", k=3)
    assert hits[0].chunk.id == "c3"


async def test_transliterated_order_matches_delivery_chunk(store: PgVectorStore) -> None:
    """The measured failure case: caller says 'order', Deepgram writes 'ارڈن'."""
    await store.upsert(CHUNKS)
    hits = await store.search("میرا ارڈن کہاں ہے", k=3)
    assert hits[0].chunk.id == "c1"


async def test_keyword_boost_is_not_silently_zero(store: PgVectorStore) -> None:
    """Regression: Postgres inferred the boost parameter as integer from the
    surrounding int multiplication and truncated 0.35 to 0, turning hybrid
    search into pure vector search with no error anywhere."""
    from agent.knowledge.store import _KEYWORD_BOOST, _tokenize

    await store.upsert(CHUNKS)

    async with store._pool.acquire() as conn:
        boosted = await conn.fetchval(
            """
            SELECT $2::float8 * LEAST(
                CARDINALITY(ARRAY(SELECT UNNEST(keywords)
                                  INTERSECT SELECT UNNEST($1::text[]))), 2)
            FROM chunks WHERE id = 'c3'
            """,
            _tokenize("ریفنڈ کب ملے گا"),
            _KEYWORD_BOOST,
        )

    assert boosted == pytest.approx(_KEYWORD_BOOST), (
        f"keyword boost evaluated to {boosted}, expected {_KEYWORD_BOOST} "
        "- the float cast has been lost"
    )


async def test_search_respects_k(store: PgVectorStore) -> None:
    await store.upsert(CHUNKS)
    assert len(await store.search("delivery", k=2)) == 2


async def test_empty_store_returns_no_hits(store: PgVectorStore) -> None:
    assert await store.search("anything", k=4) == []
