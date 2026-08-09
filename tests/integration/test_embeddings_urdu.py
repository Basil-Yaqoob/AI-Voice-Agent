"""Does the local embedding model actually understand Urdu?

Not a contract test — no API, no cost. But the first run downloads ~0.22GB, so
it is marked slow and skipped when the model is not already cached.

The question this answers is the one that decides whether retrieval works at
all: does the model place a paraphrase closer than an unrelated sentence, in
Urdu and across a code-switched pair?
"""

from __future__ import annotations

import math
import os

import pytest

from agent.knowledge.embeddings import DEFAULT_DIMENSIONS, FastEmbedProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EMBEDDING_TESTS") != "1",
    reason="downloads ~2.24GB (e5-large); set RUN_EMBEDDING_TESTS=1 to run",
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@pytest.fixture(scope="module")
def embedder() -> FastEmbedProvider:
    return FastEmbedProvider()


async def test_dimensions_match_the_schema(embedder: FastEmbedProvider) -> None:
    vectors = await embedder.embed(["test"])
    assert len(vectors[0]) == DEFAULT_DIMENSIONS


async def test_urdu_paraphrase_beats_unrelated(embedder: FastEmbedProvider) -> None:
    """The core requirement: semantic proximity in Urdu, not just tokens."""
    query, paraphrase, unrelated = await embedder.embed(
        [
            "میرا آرڈر کہاں ہے",
            "میری ڈیلیوری کب پہنچے گی",
            "موسم آج کیسا ہے",
        ]
    )
    near = _cosine(query, paraphrase)
    far = _cosine(query, unrelated)
    print(f"\n  urdu paraphrase={near:.3f}  unrelated={far:.3f}")
    assert near > far, "model cannot separate related from unrelated Urdu"


async def test_cross_script_alignment(embedder: FastEmbedProvider) -> None:
    """An Urdu query should sit closer to its English translation than to an
    unrelated English sentence — this is what makes dual-script indexing work."""
    urdu, translation, unrelated = await embedder.embed(
        [
            "میرا آرڈر کہاں ہے",
            "Where is my order",
            "The weather is nice today",
        ]
    )
    near = _cosine(urdu, translation)
    far = _cosine(urdu, unrelated)
    print(f"\n  cross-script translation={near:.3f}  unrelated={far:.3f}")
    assert near > far, "cross-script alignment failed; dual indexing will not help"


async def test_codeswitched_query_matches_urdu_chunk(embedder: FastEmbedProvider) -> None:
    """The realistic caller utterance."""
    query, related, unrelated = await embedder.embed(
        [
            "میرا order کہاں ہے",
            "ڈیلیوری میں دو سے تین دن لگتے ہیں",
            "ریفنڈ سات دن میں process ہوتا ہے",
        ]
    )
    assert _cosine(query, related) > _cosine(query, unrelated)


async def test_embedding_is_deterministic(embedder: FastEmbedProvider) -> None:
    first = await embedder.embed(["میرا آرڈر کہاں ہے"])
    second = await embedder.embed(["میرا آرڈر کہاں ہے"])
    assert _cosine(first[0], second[0]) == pytest.approx(1.0, abs=1e-6)


async def test_batch_matches_individual(embedder: FastEmbedProvider) -> None:
    batch = await embedder.embed(["ایک", "دو"])
    single_a = await embedder.embed(["ایک"])
    assert _cosine(batch[0], single_a[0]) == pytest.approx(1.0, abs=1e-6)


async def test_empty_input_returns_empty(embedder: FastEmbedProvider) -> None:
    assert await embedder.embed([]) == []


async def test_does_not_block_the_event_loop(embedder: FastEmbedProvider) -> None:
    """Embedding is CPU-bound and synchronous underneath. Running it on the loop
    would stall the audio frame pump — the same trap as the Azure Speech SDK."""
    import asyncio

    await embedder.warmup()
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    await embedder.embed([f"جملہ نمبر {i}" for i in range(40)])
    beat.cancel()

    assert ticks > 3, f"event loop stalled during embedding (only {ticks} ticks)"
