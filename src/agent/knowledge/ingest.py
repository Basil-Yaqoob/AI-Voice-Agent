"""Build the retrieval index from a corpus directory.

    uv run python scripts/ingest.py [path]

Offline by design. Loading, chunking and embedding are slow, and doing them at
worker startup would delay every deploy and block the worker from taking calls.

Checks the corpus size first: if the whole thing fits in the cached prompt
prefix, retrieval is strictly worse on both latency and cost, and this says so
rather than building an index nobody needs.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from agent.interfaces.knowledge import Chunk, EmbeddingProvider
from agent.knowledge.corpus import plan_for_corpus
from agent.knowledge.keyterms import load_keyterms
from agent.knowledge.store import PgVectorStore

__all__ = ["chunk_text", "ingest", "load_corpus"]


def _read_all(files: list[Path]) -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in files]


DEFAULT_CORPUS = Path("knowledge")

# 200-400 tokens with overlap. Larger chunks bloat the prompt suffix and slow
# time-to-first-token; smaller ones lose the context needed to answer.
CHUNK_CHARS = 1_200
OVERLAP_CHARS = 200

_PARAGRAPH = re.compile(r"\n\s*\n")


def chunk_text(text: str, *, source: str) -> list[Chunk]:
    """Split on paragraph boundaries, packing up to the size limit.

    Paragraph-aware rather than a blind character window: cutting mid-sentence
    produces chunks that retrieve well and read as nonsense when the model
    quotes them back.
    """
    paragraphs = [p.strip() for p in _PARAGRAPH.split(text) if p.strip()]
    chunks: list[Chunk] = []
    buffer = ""

    def flush() -> None:
        if not buffer.strip():
            return
        digest = hashlib.sha256(f"{source}:{buffer}".encode()).hexdigest()[:16]
        chunks.append(
            Chunk(id=digest, text=buffer.strip(), source=source, keywords=_keywords(buffer))
        )

    for paragraph in paragraphs:
        if buffer and len(buffer) + len(paragraph) > CHUNK_CHARS:
            flush()
            buffer = buffer[-OVERLAP_CHARS:] + "\n\n" + paragraph
        else:
            buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
    flush()

    return chunks


def _keywords(text: str) -> list[str]:
    """Product and brand terms present in this chunk, for the keyword arm.

    Drawn from the STT keyterm list so both halves of the pipeline agree on
    what counts as a product name.
    """
    lowered = text.casefold()
    return [term.casefold() for term in load_keyterms() if term.casefold() in lowered]


def load_corpus(corpus_dir: Path) -> tuple[list[Path], str]:
    """Read the corpus off disk.

    Synchronous on purpose, and kept out of the async function below. Blocking
    file I/O is perfectly fine in an offline job, but the ASYNC lint rule that
    forbids it exists to protect the hot path, and carving out an exception
    there would weaken it everywhere.
    """
    files = sorted(p for p in corpus_dir.rglob("*") if p.suffix in {".md", ".txt"})
    return files, "\n\n".join(p.read_text(encoding="utf-8") for p in files)


async def ingest(
    corpus_dir: Path,
    *,
    database_url: str,
    embedder: EmbeddingProvider,
) -> int:
    """Index a corpus directory.

    Takes an embedder rather than building one. ``knowledge`` is a library; if
    it constructed providers it would pull every vendor SDK into its dependency
    graph, which the import-linter contract forbids for good reason.
    Composition happens at the edge, in ``scripts/ingest.py``.
    """
    files, combined = load_corpus(corpus_dir)
    if not files:
        print(f"no .md or .txt files under {corpus_dir}")
        return 0

    plan = plan_for_corpus(combined)
    print(f"corpus: {len(files)} files, ~{plan.corpus_tokens:,} tokens")
    print(f"plan:   {plan.strategy} — {plan.reason}\n")

    if not plan.needs_retrieval:
        print("Retrieval is not required. Put this corpus in the cached prompt")
        print("prefix instead: it is faster and cheaper than an index.")
        return 0

    chunks: list[Chunk] = []
    for path, text in _read_all(files):
        chunks.extend(chunk_text(text, source=path.name))

    store = await PgVectorStore.connect(database_url, embedder)
    try:
        written = await store.upsert(chunks)
        print(f"indexed {written} chunks from {len(files)} files")
        return written
    finally:
        await store.close()
