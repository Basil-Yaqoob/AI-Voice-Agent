# 01 — Technology Decisions

Every decision here is recorded with the alternative that was rejected and *why*, so that when someone revisits it in three months they can tell whether the reasoning still holds.

The governing constraint for all of them is the latency budget from `RESEARCH.md`:

```
50ms network + 180ms endpointing + 100ms STT + 300ms LLM TTFT + 110ms TTS TTFB + 60ms egress = 800ms
```

Any technology that cannot fit its slice of that budget is disqualified regardless of other merits.

---

## DECISION 1 — Orchestration: direct SDK calls, not LangChain or LangGraph

**Chosen:** Direct provider SDKs (`anthropic`, `deepgram-sdk`, `azure-cognitiveservices-speech`) driven by the LiveKit Agents pipeline, with our own thin state machine for conversation flow.

**Rejected:** LangChain, LangGraph.

### Why

**1. You already have an orchestrator, and it is LiveKit Agents.**

This is the decisive argument and it is often missed. LiveKit Agents is not a transport library you bolt a framework onto — it already *is* the pipeline. It owns the audio frames, the VAD, the turn detection, the barge-in cancellation, the streaming handoff from STT to LLM to TTS, and the session lifecycle. Putting LangGraph inside that gives you two orchestrators with two ideas about who owns control flow, two places to look when a turn misbehaves, and two sets of async semantics to reconcile.

Pick one orchestrator. In a voice agent, the one that owns the media pipeline wins, because that is where the hard problems are.

**2. Measured framework overhead is not affordable at this budget.**

Published benchmarks put LangGraph executions at 15–18s for queries where a direct implementation runs 7–9s, with latency outliers around 10s attributed to orchestration overhead. One comparison found an alternative framework beating LangGraph by 43.7% on latency. Each node transition carries cost, and the guidance from framework authors themselves is to "count nodes carefully" for latency-sensitive user-facing work.

The honest counterpoint is that in most applications LLM latency dominates and framework overhead disappears into the noise. That counterpoint does not apply here. Our entire LLM allowance is 300ms. There is a LangChain forum thread from a team hitting 6–7 seconds integrating real-time voice with a LangGraph graph while targeting 2–3. Tens of milliseconds of per-node overhead is a material fraction of our budget, not a rounding error.

**3. The thing we most need control over is what frameworks abstract away.**

The single most important mechanic in the pipeline is: *take the first sentence out of the token stream and hand it to TTS before the model has finished generating*. That sentence-boundary detection inside a partial stream, and the cancellation semantics when the caller barges in mid-utterance, are precisely the seams frameworks hide. We need them in the foreground.

**4. Debugging.**

When p95 latency regresses by 200ms, you need to attribute it to a component in one pass. A stack trace through framework internals across an async boundary turns a ten-minute investigation into an afternoon.

### What we lose, honestly

LangGraph gives real things: durable state, checkpointing, retries, human-in-the-loop interrupts, and a visual graph. We are choosing to hand-roll a much smaller version of the first two. If this project later grows a complex multi-step *back-office* workflow — post-call summarisation, ticket routing, batch outbound campaign planning — **LangGraph is a reasonable choice for that, off the hot path.** The rule is:

> **Nothing between the caller's voice and the agent's voice may be a framework we did not write. Everything asynchronous and off-call is fair game.**

---

## DECISION 2 — Language model

You suggested DeepSeek V4 Flash, GPT-4o, or GPT-4o-mini. All three are ruled out, for different reasons. Here is the full evaluation.

### The two criteria that matter

1. **Time to first token.** Budget is 300ms. This is a hard gate — fail it and nothing else counts.
2. **Urdu code-switched register quality.** Urdu is a low-resource language. Published multilingual evaluations show performance gaps of **up to 24.3%** between high-resource and low-resource languages, and the degradation is worst in smaller models. Generating natural code-switched Urdu is *harder* than generating either language alone, because it requires the model to hold Urdu grammar while placing English nouns the way a Pakistani speaker actually would.

### Evaluation

| Model                       | TTFT              | Urdu register       | Cost /MTok    | Verdict                                                                                                                                             |
| --------------------------- | ----------------- | ------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| **DeepSeek V4 Flash** | **~1.10 s** | untested            | $0.14 / $0.28 | ❌**Disqualified.** TTFT alone is 1.4× our *entire* 800ms budget. Excellent value for batch and coding work; unusable for real-time voice. |
| **GPT-4o**            | fast              | moderate            | —            | ❌**Retired.** Pulled from ChatGPT 13 Feb 2026, fully retired 3 Apr 2026. API-deprecated. Do not start a 2026 build on it.                    |
| **GPT-4o-mini**       | very fast         | **weak**      | —            | ❌**Retired** (Azure Foundry 31 Mar 2026) *and* mini-tier models degrade hardest on low-resource languages. Wrong on both axes.             |
| GPT-5 mini                  | fast              | moderate            | —            | ⚠️ Legitimate option and the official 4o-mini successor. Benchmark it if you want a non-Anthropic comparator.                                     |
| **Claude Haiku 4.5**  | very fast         | moderate            | $1 / $5       | ✅ Use for routing, intent classification, and simple deflection turns.                                                                             |
| **Claude Sonnet 5**   | fast              | strong              | $3 / $15      | ✅ Best quality-per-millisecond for this workload. Intro pricing $2/$10 to 31 Aug 2026.                                                             |
| **Claude Opus 5**     | moderate          | **strongest** | $5 / $25      | ✅ Default. Best register control. Fast mode available (see below).                                                                                 |

### Decision

**Default to `claude-opus-5`, behind a swappable interface, and let measurement decide.**

Rationale:

- Register control is the differentiating capability in this product, and it is where the largest model earns its cost. The failure mode of a weaker model is not "wrong answer" — it is Urdu that sounds like a translation, which is exactly what makes existing Pakistani voice agents feel bad.
- **Opus 5 fast mode** (`speed: "fast"`, beta flag `fast-mode-2026-02-01`, $10/$50 per MTok) raises output throughput up to 2.5×. This is the only paid lever in the entire stack that directly converts money into latency headroom. If TTFT measurements come in tight, this is the first thing to try — before downgrading the model.
- Opus 5's prompt cache minimum is **512 tokens**, half Opus 4.8's. Our cached system prompt qualifies easily, and cache reads bill at 0.1×.

**Set `LLM_MODEL` in config, not in code.** Task 12 builds an evaluation harness that measures TTFT and scores Urdu register across candidates on *your* prompts. Run it before the client demo and let the numbers pick. Sonnet 5 at intro pricing may well win on quality-per-rupee; Haiku 4.5 may be fine for the FAQ path. The architecture must not care.

### Critical model configuration for voice

Two settings that are easy to get wrong and expensive to discover late:

```python
# CORRECT for voice
thinking={"type": "adaptive"},
output_config={"effort": "low"},     # low | medium — never high on the hot path
```

**Do not disable thinking to save latency.** It is the obvious move and it is a trap. On Claude Opus 5, `thinking: {"type": "disabled"}` has two documented failure modes:

1. The model occasionally writes a **tool call into its visible text** instead of emitting a structured `tool_use` block. The turn completes normally, no error is raised, and the tool never runs. In a voice agent that means the caller is told their balance was checked when nothing happened — a silent correctness bug, not a crash.
2. `<thinking>` tags can leak into the visible response, which then gets read aloud.

Keeping adaptive thinking on at `effort: "low"` is both safer and nearly as fast. Note also that `{"type": "disabled"}` is rejected outright at `effort` of `xhigh` or `max`.

---

## DECISION 3 — Language and runtime: Python 3.12

**Chosen:** Python 3.12, `asyncio` throughout.
**Rejected:** Node.js, Go.

LiveKit Agents' most mature SDK is Python, and every provider SDK we need is first-class there. Node is a defensible alternative with a real LiveKit SDK; Go would be fastest but has the thinnest agent-framework support. The performance difference is irrelevant — this workload is I/O-bound on network calls to STT, LLM and TTS, not CPU-bound. Ecosystem maturity wins.

**Everything on the hot path must be `async`.** A single blocking call in an audio callback stalls the frame loop and produces audible stutter. This is enforced by lint rule, not by discipline — see Task 01.

---

## DECISION 4 — Media and transport: LiveKit Agents

**Chosen:** LiveKit Agents (open source, Cloud for dev).
**Rejected:** Pipecat, Vapi/Retell, building on raw WebRTC.

- **vs. Pipecat** — Pipecat is an excellent, very developer-friendly framework and its integration library is broader. But LiveKit sits on a WebRTC media server, so session state, reconnection, barge-in and low-latency transport come from the RTC layer rather than being retrofitted, and it wins on native SIP and concurrent-call scaling. Both are good; LiveKit is the better fit given our SIP endpoint.
- **vs. Vapi / Retell** — managed platforms are faster to a demo but they own your vendor list. When the vendor list is the hard problem (only two viable Urdu TTS providers, one of them experimental), you cannot accept a platform that constrains it. You also cannot swap TTS at runtime, which Decision 5 requires.
- **The same agent code serves WebRTC and SIP.** Demo on WebRTC in week 1; attach the SIP gateway in week 5 with no changes to the agent. This is the property that makes the whole build order work.

---

## DECISION 5 — Provider abstraction: thin interfaces over STT, TTS, LLM

**Chosen:** Three narrow protocols, one implementation per vendor, selected by config at runtime.

This is not speculative generality — it is a direct response to a specific risk in the research. Inworld's Urdu is **flagged experimental** and may change in quality, price or availability without production guarantees. Azure `ur-PK` is the fallback. Switching between them must be an environment variable and a process restart, **not a code change and a deploy**, because you may need to do it during a client demo.

Keep the interfaces genuinely thin — `synthesize(text) -> AsyncIterator[bytes]` and nothing more. A fat abstraction that tries to normalise every vendor's feature set is worse than no abstraction.

---

## DECISION 6 — Data layer: Postgres + pgvector, Redis for cache

**Chosen:** Postgres 16 with the `pgvector` extension; Redis for the semantic answer cache and session state.
**Rejected:** Pinecone, Weaviate, Qdrant Cloud.

At our corpus size, a dedicated vector service buys scale we do not need and costs a network hop we cannot afford. One database is also one thing to operate. Revisit above roughly 5 million chunks.

---

## DECISION 7 — Testing: pytest, with recorded audio fixtures

**Chosen:** `pytest` + `pytest-asyncio`, provider calls faked at the interface boundary, golden audio fixtures committed to the repo.

Voice agents are hard to test because the inputs are audio and the outputs are audio. The strategy that works:

- **Unit tests never touch the network.** Every provider interface has a deterministic fake. This is the main reason Decision 5 exists.
- **Text-level assertions for text-level logic.** Urdu normalisation, prompt assembly, sentence chunking and retrieval ranking are all pure functions over strings. Test them exhaustively — they are where the real bugs live.
- **Golden audio fixtures for the pipeline.** A handful of short recorded Urdu utterances in `tests/fixtures/audio/`, fed through the real pipeline in integration tests only.
- **Latency assertions as tests.** `test_ttft_within_budget` is a real test that fails the build. If it is not enforced by CI it will regress.

Full strategy in `02-TESTING-STRATEGY.md`.

---

## DECISION 10 — Embeddings: local ONNX, `intfloat/multilingual-e5-large`

*Recorded during TASK-07 implementation, 9 Aug 2026.*

**Chosen:** `fastembed` running `intfloat/multilingual-e5-large` locally (1024 dims, ~2.24GB).
**Rejected:** a hosted embedding API; `paraphrase-multilingual-MiniLM-L12-v2` (384 dims, 0.22GB).

### Why local rather than hosted

1. **Latency.** Embeddings sit on the speculative-prefetch path — the one path in the system deliberately engineered to ~0ms. A hosted embedding call puts a network round trip inside it and defeats the entire mechanism.
2. **Credentials.** There is no embedding key in this project and no clean route to one: OpenRouter serves no `/embeddings` endpoint, and the Azure key is Speech, not Azure OpenAI.

`fastembed` runs ONNX, so this brings no torch dependency.

### Why the large model, measured

Four Urdu retrieval cases, margin = cosine(query, correct) − cosine(query, distractor):

| Case | MiniLM-L12 | e5-large |
|---|---:|---:|
| Urdu paraphrase | +0.313 | +0.053 |
| Cross-script (Urdu ↔ English) | +0.783 | +0.161 |
| **Code-switched** | **−0.029 ✗** | **+0.026 ✓** |
| Product name | +0.677 | +0.074 |
| **Correct rankings** | **3/4** | **4/4** |

MiniLM discriminates harder where it works, but it **fails the code-switched query** — the defining scenario of this product. Retrieval that cannot handle `میرا order کہاں ہے` is not worth deploying, so the 2.24GB per media-worker container is accepted.

E5's margins look narrow because the model compresses the similarity space into roughly 0.75–0.93. Ranking order is what matters, not absolute values. Note this also means **a similarity threshold tuned on MiniLM numbers would be badly wrong for E5.**

1024 dims matches TASK-07's original DDL — that spec turns out to have been right.

### Two implementation traps

- **E5 is asymmetric.** It is trained with explicit `query: ` and `passage: ` prefixes and degrades measurably without them. `EmbeddingProvider.embed()` therefore takes `kind="query"|"passage"`, and `PgVectorStore` passes the right one on each path. This is not cosmetic.
- **`fastembed` is synchronous and CPU-bound.** It runs through `run_in_executor` — the same trap as the Azure Speech SDK in TASK-05 — with a test asserting the event loop keeps ticking during a batch embed.

`FastEmbedProvider.warmup()` exists so the 2.24GB model loads at worker startup rather than while a caller waits.

---

## DECISION 9 — OpenRouter adapter uses the `openai` SDK; both LLM paths are built

*Recorded during TASK-03 implementation, 9 Aug 2026.*

**Chosen:** add `openai` as a dependency and ship two adapters — `llm_claude.py` (Anthropic SDK, direct) and `llm_openrouter.py` (OpenAI SDK, proxied).
**Rejected:** raw `httpx` for OpenRouter; building only one adapter.

OpenRouter speaks the OpenAI chat-completions protocol, so the Anthropic client cannot be reused. Hand-rolling SSE parsing, tool-call fragment accumulation and cancellation over raw `httpx` is exactly the class of bug that costs a day, and those mechanics are the substance of TASK-03. Both adapters exist so TASK-12 can benchmark proxied against direct rather than assuming.

Three OpenRouter-specific behaviours are absorbed in the adapter and in `Settings.resolved_llm_model`:

- Fast mode is a **separate model id** (`anthropic/claude-opus-5-fast`), not a `speed` parameter.
- Prompt caching needs `cache_control` inside a **multipart** system message.
- Streaming usage only appears if `stream_options.include_usage` is set. Without it `cache_read_tokens` is permanently zero and the TASK-11 cache alarm misfires.

### Measured, 9 Aug 2026 — from a Karachi dev machine via OpenRouter

TTFT to first **visible** token, 5,290-char cached prefix, three consecutive calls:

| Model | TTFT | Cache reads (call 2+) |
|---|---|---|
| `claude-opus-5` | 4.4 – 5.4 s | 1675 ✅ |
| `claude-sonnet-5` | 1.8 – 3.7 s | 1675 ✅ |
| `claude-haiku-4.5` | 1.0 – 2.5 s | **0** ❌ |

**Prompt caching works through OpenRouter** for Opus and Sonnet — confirmed, not assumed. Haiku 4.5 never cached: the prefix is 1,282 tokens on its tokenizer, below that model's cache minimum. A model switch can therefore silently disable caching, which is why `prefix_is_cacheable()` exists and why TASK-11's cache alarm matters.

**Every model is far over the 300ms budget, and thinking is not the reason.** Disabling reasoning changed nothing (Haiku 1466ms off vs 986ms on — noise; Sonnet 2921 vs 2419). The cost is the network path: Pakistan → OpenRouter (US) → Anthropic → back, two intercontinental round trips plus an extra TLS handshake.

These are **not** production numbers — production runs the worker in Mumbai/UAE calling the provider directly. But they establish two things now rather than in week six:

1. **OpenRouter is a development convenience, not a production transport** for this workload. Keep `LLM_PROVIDER=openrouter` for building; benchmark `claude` from the deployment region before committing.
2. TASK-12 must measure **from the region the agent will run in**. A benchmark from a dev laptop measures the laptop's ISP.

---

## DECISION 8 — Task runner: Makefile plus a Windows `.cmd` shim

*Recorded during TASK-01 implementation, 9 Aug 2026.*

**Chosen:** Keep the `Makefile` as written, and add `make.cmd` mirroring its targets.
**Rejected:** installing GNU make on Windows; adding a task-runner dependency such as `poethepoet` or `just`.

Every command in the task docs is written as `make <target>`, but **`make` is not present on Windows by default** and the primary development machine for this project is Windows. Three options existed:

1. Require developers to install GNU make — an extra setup step, and one that varies by install method (choco / scoop / MSYS2).
2. Add a Python task runner as a dependency — cross-platform and clean, but it is a new dependency and it makes every documented `make lint` wrong.
3. Ship a `.cmd` shim so `make lint` works verbatim on Windows.

Option 3 wins because it adds no dependency, keeps every command in the docs literally correct on all platforms, and leaves the Makefile authoritative for CI and Linux/macOS.

**The cost is duplication**, so `tests/unit/test_task_runner.py` asserts the two expose an identical target set in both directions. Adding a target to one and not the other fails the build.

Two Windows details that cost real debugging time and are easy to reintroduce:

- **`make.cmd` must be CRLF-encoded ASCII.** `cmd.exe` mis-parses LF-only batch files and non-ASCII characters, producing stray `'M' is not recognized as an internal or external command` errors on every invocation while still appearing to work. `.gitattributes` pins `*.cmd` to CRLF.
- **Batch has no `goto <label> || fallback`.** Dispatch with an explicit `if /i "%TARGET%"=="x" goto x` chain and a final `goto unknown`.

---

## DECISION 11 — Client business data: SQLite behind a tool, not Postgres, not RAG

*Recorded during TASK-14, 10 Aug 2026.*

**Chosen:** stdlib `sqlite3` on a thread pool, fed from an Excel workbook the client edits.
**Rejected:** putting the business data in the existing Postgres; putting it in the retrieval index; adding `aiosqlite`.

Three separate calls, so three separate reasons.

**Why a database at all, rather than retrieval.** The rule already written into `agent/tools/registry.py` decides it: retrieval is for knowledge, tools are for anything that changes. Stock, order status and refund state differ between two calls an hour apart. Embed them and the agent quotes yesterday's stock with total confidence, which is worse than not knowing. The split is by rate of change, not by subject.

**Why not the Postgres we already run.** It is there for pgvector, and it is a container the client has to be running. The demo database is a file: it clones with the repo, needs no `make dev`, and cannot be the reason a client demo fails in a meeting room with bad wifi. It is also the layer most likely to be deleted — the real integration replaces `dvago_backend.py` with DVAGO's own APIs — so the cheaper it is to remove, the better. An import-linter contract keeps `agent.data` from learning anything about DVAGO so that removal stays clean.

**Why not `aiosqlite`.** `sqlite3` is synchronous and hot-path rule 1 forbids blocking, so it needs a thread either way. `aiosqlite` is that thread plus a dependency; `run_in_executor` is that thread and the same pattern already used for the Azure Speech SDK, which is synchronous for the same reason. No new dependency, one fewer thing to explain.

**Why Excel as the source.** The client can open it, and a spreadsheet is the format a pharmacy already keeps stock lists in. `src/agent/data/schema.sql` stays authoritative — the workbook, the loader and the data dictionary all derive from it, and tests fail the build if they diverge. `openpyxl` is added as a **dev dependency only**: it generates and loads the workbook offline and is never imported by the running agent.

Two SQLite specifics that cost debugging time and will be reintroduced by anyone who does not know:

- **`PRAGMA foreign_keys` is per connection, not per database.** Set it when building the file and it is off again on the next connect, and every bad write passes silently.
- **`PRAGMA journal_mode = WAL` takes an exclusive lock that `busy_timeout` does not cover.** SQLite returns `SQLITE_BUSY` immediately rather than invoking the busy handler, so threads racing to connect collide. Connection setup is serialised behind a lock — it happens once per thread and never on the hot path.

---

## Final stack

| Layer                 | Choice                                              | Why in one line                                             |
| --------------------- | --------------------------------------------------- | ----------------------------------------------------------- |
| Runtime               | Python 3.12 + asyncio                               | Best LiveKit and provider SDK support                       |
| Package manager       | `uv`                                              | Fast, lockfile-based, reproducible                          |
| Media / orchestration | LiveKit Agents                                      | Owns transport, VAD, barge-in; same code for WebRTC and SIP |
| Agent flow            | Hand-rolled async state machine                     | No framework between caller voice and agent voice           |
| STT                   | Deepgram Nova-3 (`ur`) + keyterm prompting        | Only production Urdu streaming STT                          |
| LLM                   | Claude,`claude-opus-5` default, swappable         | Best Urdu register; fast mode buys latency                  |
| TTS                   | Azure`ur-PK` ⇄ Inworld TTS-2, runtime-switchable | The only two viable real-time Urdu voices                   |
| Knowledge             | Postgres 16 + pgvector                              | One database, no extra hop                                  |
| Cache                 | Redis                                               | Semantic answer cache, session state                        |
| Control plane API     | FastAPI                                             | Token minting, health, admin                                |
| Demo frontend         | Vanilla HTML + LiveKit JS SDK                       | No build step to break during a client demo                 |
| Tests                 | pytest + pytest-asyncio                             | Async-native                                                |
| Lint / types          | ruff + mypy                                         | Catches blocking calls on the hot path                      |
| Local infra           | Docker Compose                                      | Postgres + Redis with one command                           |
| Task runner           | Makefile +`make.cmd` shim                         | `make` is absent on Windows; no new dependency              |
| Client business data  | SQLite on a thread pool, built from Excel           | Clones with the repo; the layer most likely to be replaced  |

---

## Rejected-alternatives quick reference

| Rejected                              | Instead                        | Reason                                                           |
| ------------------------------------- | ------------------------------ | ---------------------------------------------------------------- |
| LangChain / LangGraph on the hot path | Direct SDKs + LiveKit pipeline | Framework overhead unaffordable at 300ms; duplicate orchestrator |
| DeepSeek V4 Flash                     | Claude                         | ~1.10s TTFT exceeds the whole latency budget                     |
| GPT-4o / GPT-4o-mini                  | Claude (or GPT-5 mini)         | Retired Feb–Apr 2026; mini tier weak on low-resource languages  |
| Disabling thinking for speed          | `adaptive` + `effort: low` | Disabled thinking silently drops tool calls into visible text    |
| Pipecat                               | LiveKit Agents                 | Weaker native SIP and scaling story                              |
| Vapi / Retell                         | LiveKit Agents                 | Cannot control or hot-swap the TTS vendor                        |
| Pinecone / Weaviate                   | pgvector                       | Extra network hop we cannot afford                               |
| ElevenLabs for Urdu                   | Azure / Inworld                | Flash v2.5 has no Urdu; v3 cannot run real-time                  |
| Railway for media workers             | Mumbai/UAE region host         | No India region; +60–90ms is 8–11% of budget                   |
| Business data in the retrieval index  | SQLite behind a tool           | Stock and order state change hourly; embedded, they go stale silently |
| Postgres for the demo business data   | SQLite file                    | Clones with the repo; no container to fail in a client meeting   |
| `aiosqlite`                           | `run_in_executor`              | Same thread, one more dependency; matches the Azure SDK pattern  |
