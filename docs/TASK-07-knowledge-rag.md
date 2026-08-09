# TASK-07 — Knowledge layer: cached corpus, prefetch, answer cache

| | |
|---|---|
| **Depends on** | TASK-02, TASK-03 |
| **Blocks** | 08 |
| **Estimate** | 2 days |
| **Level** | Advanced |

---

## Objective

Give the agent the company's knowledge **without spending latency on it**. The budget allocates 300ms to the LLM and zero to retrieval, so a conventional retrieve-then-generate loop is disqualified before you start.

Three mechanisms, in the order you should reach for them:

1. **Cached corpus** — most turns need no retrieval at all
2. **Speculative prefetch** — retrieval overlaps endpointing, costing ~0ms on the critical path
3. **Semantic answer cache** — repeated intents skip retrieval *and* generation

The instinct to reach straight for a vector database is the wrong first move here. Retrieval is the fallback, not the default.

---

## Steps

### 1. Try the corpus-in-prompt route first

Company knowledge bases are usually smaller than teams assume. If the whole thing fits in tens of thousands of tokens, put it in the cached prompt prefix and skip retrieval entirely for most turns.

This is faster *and* cheaper than RAG: cache reads bill at 0.1× of input, and you remove a network hop from the critical path. Claude Opus 5 caches from 512 tokens up, so even a modest policy document qualifies.

```python
def should_use_cached_corpus(corpus_tokens: int) -> bool:
    # Below ~40k the cached-prefix route wins on both latency and cost.
    return corpus_tokens < 40_000
```

Measure your actual corpus before building retrieval. Many deployments never need step 2.

### 2. Chunking and dual-script indexing

For corpora too large to cache whole:

```python
@dataclass(frozen=True)
class Chunk:
    id: str
    text: str            # original language
    text_en: str | None  # English translation if source was Urdu
    embedding: list[float]
    source: str
    keywords: list[str]  # product names for hybrid search
```

**Index in both scripts.** A code-switched query ("میرا `order` کہاں ہے") matches neither a purely Urdu chunk nor a purely English one well. Embedding each chunk in both languages and querying both roughly doubles recall on realistic caller utterances.

Add a keyword index over product names and run **hybrid search** — dense vectors plus keyword matching. Pure vector search reliably fails on exact product names, which are precisely the queries that matter most.

Chunk at 200–400 tokens with ~50 token overlap. Larger chunks bloat the prompt suffix and slow TTFT; smaller ones lose the context needed to answer.

### 3. pgvector store

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chunks (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    text_en     TEXT,
    embedding   vector(1024) NOT NULL,
    source      TEXT NOT NULL,
    keywords    TEXT[]        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX chunks_embedding_idx ON chunks
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX chunks_keywords_idx ON chunks USING gin (keywords);
```

Use **HNSW**, not IVFFlat. HNSW gives better recall at low latency and does not need a training step or a populated table before index creation.

### 4. Speculative prefetch — the mechanism that makes this work

`src/agent/knowledge/prefetch.py`. This is the core idea of the whole task.

Retrieval fires on the **partial** transcript while the caller is still speaking and while endpointing is still counting down its 180ms. By the time the final transcript arrives, results are already in memory.

```python
class SpeculativePrefetcher:
    """Overlap retrieval with endpointing so it costs ~0ms on the critical path."""

    def __init__(self, store: KnowledgeStore, *, min_chars: int = 12) -> None:
        self._store, self._min_chars = store, min_chars
        self._task: asyncio.Task[list[Chunk]] | None = None
        self._query: str = ""

    def on_partial(self, text: str) -> None:
        """Called on every interim transcript. Cheap, non-blocking, fire-and-forget."""
        if len(text) < self._min_chars or text == self._query:
            return
        if self._task and not self._task.done():
            self._task.cancel()          # supersede the stale query
        self._query = text
        self._task = asyncio.create_task(self._store.search(text))

    async def resolve(self, final_text: str, *, timeout_s: float = 0.15) -> list[Chunk]:
        """Called with the final transcript. Uses the warm result if it still fits."""
        if self._task and self._is_close_enough(final_text):
            try:
                return await asyncio.wait_for(asyncio.shield(self._task), timeout_s)
            except (TimeoutError, asyncio.CancelledError):
                pass                      # fall through to a fresh search
        return await self._store.search(final_text)
```

Three properties that matter:

- **Prefetch is an optimisation, never a correctness dependency.** If the caller's sentence changed meaning at the end, discard the speculative result and search again. The `_is_close_enough` check decides; be conservative.
- **Supersede, don't queue.** Each new partial cancels the previous in-flight search. Otherwise a fast speaker queues five searches and you pay for all of them.
- **`min_chars` guard.** Firing on "می" wastes a query and an embedding call.

**Cost note:** prefetch means more retrieval calls than turns. That is the trade — embedding calls are cheap, latency is not. Cap it with the `min_chars` guard and supersession, then measure the ratio in TASK-11.

### 5. Semantic answer cache

`src/agent/knowledge/cache.py`, backed by Redis. Support calls are enormously repetitive — a small set of intents covers most turns.

```python
async def get(self, query: str) -> CachedAnswer | None:
    emb = await self._embed(query)
    hit = await self._nearest(emb)                  # cosine over recent cached queries
    if hit and hit.similarity >= self._threshold:   # default 0.93
        return hit.answer
    return None
```

The threshold matters. Too low and callers get answers to questions they did not ask, which is much worse than a slow answer. Start at `0.93` and only lower it with measured evidence.

**Never cache anything caller-specific.** Balances, order status, personal details must never be served from a shared cache. Gate on intent type: cache policy and product answers, never account data. A caller hearing someone else's balance is a security incident, not a bug.

### 6. Offline ingestion

`src/agent/knowledge/ingest.py`, run as `make ingest` — not at agent startup. Document loading, chunking, translation and embedding are slow and belong in a job, not in the request path.

---

## Acceptance criteria

- [ ] Corpus under 40k tokens goes into the cached prefix; retrieval skipped
- [ ] Chunks stored with both Urdu and English embeddings
- [ ] Hybrid search returns exact product-name matches that pure vector search misses
- [ ] HNSW index in use; search p95 under 40ms locally
- [ ] Prefetch fires on partials and supersedes stale in-flight queries
- [ ] **Retrieval contributes < 20ms to the critical path** when prefetch has ≥ 150ms of lead
- [ ] Prefetch failure or mismatch falls back to synchronous search with correct results
- [ ] Answer cache hits skip retrieval and generation entirely
- [ ] Caller-specific content is never cached (asserted by test)
- [ ] `make ingest` rebuilds the index reproducibly
- [ ] Retrieval-calls-per-turn ratio logged for cost tracking

---

## Unit tests

`tests/unit/test_prefetch.py` — the most important tests here:

| Test | Asserts |
|---|---|
| `test_fires_on_partial_above_min_chars` | Search starts during speech |
| `test_ignores_short_partials` | No query on "می" |
| `test_supersedes_stale_query` | Previous task cancelled |
| `test_resolve_uses_warm_result` | No second search when query matches |
| `test_resolve_refetches_on_divergence` | Correctness over speed |
| `test_resolve_falls_back_on_timeout` | Slow prefetch does not stall the turn |
| `test_cancelled_prefetch_does_not_leak` | No orphaned tasks after session end |

```python
async def test_resolve_refetches_on_divergence() -> None:
    """Prefetch is an optimisation. Correctness wins when the sentence changed."""
    store = FakeStore(delay_ms=10)
    p = SpeculativePrefetcher(store)

    p.on_partial("میرا آرڈر")
    await asyncio.sleep(0.02)

    result = await p.resolve("میں account بند کرنا چاہتا ہوں")   # different intent

    assert store.queries[-1] == "میں account بند کرنا چاہتا ہوں"
    assert result == store.last_result
```

`tests/unit/test_answer_cache.py`:

| Test | Asserts |
|---|---|
| `test_exact_repeat_hits` | Same question served from cache |
| `test_paraphrase_hits_above_threshold` | Semantic matching works |
| `test_below_threshold_misses` | Conservative default holds |
| `test_account_specific_never_cached` | **Security-relevant** |
| `test_ttl_expiry` | Stale answers drop out |

```python
async def test_account_specific_never_cached(cache) -> None:
    """A caller hearing another caller's balance is an incident, not a bug."""
    await cache.maybe_store(
        query="میرا balance کیا ہے",
        answer="آپ کا balance پانچ ہزار روپے ہے",
        intent="account_balance",
    )
    assert await cache.get("میرا balance کیا ہے") is None
```

`tests/latency/test_retrieval_off_critical_path.py`:

```python
@pytest.mark.latency
async def test_prefetch_hides_retrieval_latency() -> None:
    """The claim this whole task rests on."""
    store = FakeStore(delay_ms=200)
    p = SpeculativePrefetcher(store)

    p.on_partial("میرا آرڈر کہاں")
    await asyncio.sleep(0.25)                     # endpointing window

    start = time.perf_counter()
    await p.resolve("میرا آرڈر کہاں ہے")
    critical_path_ms = (time.perf_counter() - start) * 1000

    assert critical_path_ms < 20, f"{critical_path_ms:.0f}ms leaked onto critical path"
```

`tests/integration/test_knowledge_store.py`:

| Test | Asserts |
|---|---|
| `test_ingest_then_search_roundtrip` | End-to-end against real Postgres |
| `test_dual_script_recall` | Urdu query finds English-sourced chunk |
| `test_hybrid_beats_vector_on_product_names` | Justifies hybrid complexity |
| `test_hnsw_index_used` | `EXPLAIN` shows index scan, not seq scan |

---

## Common pitfalls

- **Building RAG before measuring the corpus.** Many corpora fit in the cached prefix, which is faster and cheaper. Measure first.
- **Serial retrieve-then-generate.** Costs 150–250ms you have not budgeted. Prefetch or nothing.
- **Prefetch as a correctness dependency.** It will sometimes be wrong; always be able to fall back.
- **Not superseding in-flight queries.** A fast speaker triggers a pile-up of concurrent searches.
- **Pure vector search for product names.** Exact-match queries are where dense retrieval is weakest. Hybrid.
- **Caching account data.** Security incident.
- **IVFFlat over HNSW.** Needs training and a populated table; worse recall at this scale.
- **Ingesting at startup.** Slows every deploy and blocks the worker from taking calls.
