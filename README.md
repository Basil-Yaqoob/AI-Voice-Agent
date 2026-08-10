# Urdu Voice Agent

Production conversational AI for Pakistani customers — code-switched Urdu/English, inbound + outbound PSTN + WhatsApp voice, on a +92 number.

**Target:** sub-800ms end-of-speech to first audio, at 5,000–50,000 minutes/month.

---

## Start here

| Read | When |
|---|---|
| **[RESEARCH.md](RESEARCH.md)** | First. Vendor landscape, PTA regulatory path, full PKR costing. Explains *why* the stack is what it is. |
| **[docs/00-ARCHITECTURE.md](docs/00-ARCHITECTURE.md)** | Before writing code. Module layout, data flow, the four hot-path rules. |
| **[docs/01-TECH-DECISIONS.md](docs/01-TECH-DECISIONS.md)** | When you disagree with a choice. Every decision with its rejected alternative. |
| **[docs/02-TESTING-STRATEGY.md](docs/02-TESTING-STRATEGY.md)** | Before writing tests. How to test a system whose input and output are audio. |
| **[docs/03-SWITCHING-VOICES.md](docs/03-SWITCHING-VOICES.md)** | To change which engine speaks. `VOICE_MODE` vs `TTS_PROVIDER`, and why the picker shows what it does. |

---

## The three constraints that shaped everything

1. **ElevenLabs cannot do real-time Urdu.** Flash v2.5 has no Urdu; v3 has Urdu but cannot stream. The entire viable market is Azure `ur-PK` and Inworld TTS-2 (experimental). This is why TTS sits behind a runtime-switchable interface.
2. **A +92 voice number requires a PTA LL/LDI licensee partner.** A CVAS licence cannot carry voice. This is a contracting timeline, not an engineering one — it starts in week 0 and runs underneath everything.
3. **The latency budget is 800ms and it governs every vendor choice.** Anything that cannot fit its slice is disqualified regardless of other merits. This is what ruled out DeepSeek V4 Flash (~1.10s TTFT) and LangGraph on the hot path.

---

## Status — 10 August 2026

All fourteen tasks implemented. **595 tests passing, lint clean** (ruff, mypy
strict, import-linter).

The agent is wired to a client deployment: **DVAGO**, a Pakistani pharmacy
chain. It answers from a live SQLite database of stock, orders, complaints and
refunds — 17 tables, ~6,350 rows, generated from an Excel workbook the client
can edit. See [docs/14-DVAGO-DEMO-DATA.md](docs/14-DVAGO-DEMO-DATA.md).

**All of that data is synthetic**, and four of the five policy documents in
[knowledge/dvago/](knowledge/dvago/) are marked `PLACEHOLDER` — DVAGO's real
return and refund terms could not be read from their site and must be pasted in
before a real caller hears this.

| Verified against live vendors | Result |
|---|---|
| Urdu survives Azure → Deepgram roundtrip | ✅ intact |
| Both `ur-PK` voices | ✅ Uzma, Asad |
| Azure TTS time-to-first-byte | 668 ms from Karachi |
| Prompt caching through OpenRouter | ✅ Opus/Sonnet; ✗ Haiku (below its cache minimum) |
| Urdu numerals reviewed by a native speaker | ✅ 94/100 pre-verified, 6 resolved |
| Transliteration lexicon reviewed | ✅ 48/50, 5 switched to English pronunciation |

| Measured in the pipeline | Result | Budget |
|---|---:|---:|
| Time to first audio | 394 ms | 800 ms |
| Retrieval on the critical path | 0 ms | — |
| Barge-in to silence | <1 ms | 100 ms |

**Two things are deliberately outstanding.** Both are recorded in
[HANDOFF.md](HANDOFF.md):

1. **Endpointing is unvalidated against real speech** — deferred with client
   sign-off; needs a phone-line recording session before production.
2. **Carrier contracting has not started** — the longest lead time in the
   project, and not something engineering can accelerate.

---

## Task board

Build order. Dependencies are listed in each file's header.

### Foundation
| Task | Title | Level | Est. |
|---|---|---|---|
| [01](docs/TASK-01-repo-scaffold.md) | Repository scaffold and toolchain | Beginner | 2–3 h |
| [02](docs/TASK-02-config-and-secrets.md) | Configuration and secrets | Beginner | 2 h |

### Components
| Task | Title | Level | Est. |
|---|---|---|---|
| [03](docs/TASK-03-llm-client.md) | LLM client and prompt assembly | Intermediate | 1 d |
| [04](docs/TASK-04-stt-deepgram.md) | Speech-to-text (Deepgram Nova-3 Urdu) | Intermediate | 1 d |
| [05](docs/TASK-05-tts-provider.md) | Text-to-speech with runtime failover | Intermediate | 1–1.5 d |
| [06](docs/TASK-06-urdu-normalizer.md) | Urdu text normalisation for speech | Intermediate | 1.5 d |
| [07](docs/TASK-07-knowledge-rag.md) | Knowledge layer: corpus, prefetch, cache | Advanced | 2 d |

### Integration
| Task | Title | Level | Est. |
|---|---|---|---|
| [08](docs/TASK-08-agent-pipeline.md) | Agent pipeline: the turn loop | Advanced | 2–3 d |
| [09](docs/TASK-09-turn-detection.md) | Turn detection and endpointing tuning | Advanced | 1 d |

### Delivery
| Task | Title | Level | Est. |
|---|---|---|---|
| [10](docs/TASK-10-web-demo-ui.md) | Web demo frontend and control-plane API | Beginner–Int. | 1 d |
| [11](docs/TASK-11-metrics-observability.md) | Latency metrics and observability | Intermediate | 1 d |
| [12](docs/TASK-12-eval-harness.md) | Evaluation harness: pick model and voice | Advanced | 1.5 d |
| [13](docs/TASK-13-demo-runbook.md) | Demo runbook | All | 0.5 d |
| [14](docs/14-DVAGO-DEMO-DATA.md) | DVAGO demo data and live database | Intermediate | 1 d |

**Total: roughly 3 weeks to a client-ready demo.**

Tasks 03–07 are independent of each other once 02 is done — parallelise across people if you have them.

---

## Stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.12 + asyncio, `uv` |
| Media / orchestration | LiveKit Agents (WebRTC → SIP later) |
| Agent flow | Hand-rolled async state machine — **no LangChain/LangGraph on the hot path** |
| STT | Deepgram Nova-3 (`ur`) with keyterm prompting |
| LLM | Claude, `claude-opus-5` default, swappable by config |
| TTS | Azure `ur-PK` ⇄ Inworld TTS-2, runtime failover |
| Knowledge | Postgres 16 + pgvector, Redis answer cache |
| API | FastAPI |
| Demo UI | Vanilla HTML + vendored LiveKit JS — no build step |
| Tests | pytest + pytest-asyncio, ruff, mypy, import-linter |

Reasoning for each in [docs/01-TECH-DECISIONS.md](docs/01-TECH-DECISIONS.md).

---

## Quick start

```bash
uv sync                    # install
cp .env.example .env       # then fill in credentials
make data                  # build the DVAGO spreadsheet + SQLite database
make dev                   # Postgres + Redis
make test                  # unit + integration
make demo                  # run the agent
# open http://localhost:8000
```

### Free tiers cover the whole demo

| Service | Free allowance |
|---|---|
| Deepgram | $200 credit, no card, no expiry (~12,000 min) |
| Azure Speech | 500,000 chars/month, indefinitely (~1,100 min) |
| LiveKit | Build tier |
| Anthropic | Pay-as-you-go — a few dollars for a demo's worth |

---

## Commands

```bash
make dev              # local infra up
make test             # unit + integration
make test-unit        # fast loop, <5s
make test-latency     # budget assertions
make test-contract    # real vendor APIs — costs money
make lint             # ruff + mypy + import-linter
make eval             # model & voice comparison report
make ingest           # rebuild the knowledge index
make prerender        # regenerate fixed audio assets
make dataset          # regenerate the DVAGO spreadsheet (overwrites hand edits)
make db               # load the spreadsheet into SQLite
make data             # dataset + db
make demo             # run the agent worker
```

---

## Cost at a glance

All-in, converted at 278.6 PKR/USD. **Budget at ~295** for bank FX markup and advance tax on foreign card payments.

| Model tier | PKR/min | 5k min/mo | 50k min/mo |
|---|---:|---:|---:|
| Opus 5 | 21.99 | 151,750 | 1,141,300 |
| Sonnet 5 | 17.26 | 128,100 | 904,800 |
| Haiku 4.5 | 12.80 | 105,800 | 681,800 |

Full breakdown in [RESEARCH.md](RESEARCH.md#what-a-conversation-minute-actually-costs).

---

## Things that will bite you

Collected from the task docs — the failures that are silent, expensive, or demo-ending.

- **A timestamp in the cached system prompt** triples the model bill with no visible symptom. Watch `cache_read_tokens`.
- **The Urdu full stop is `۔`, not `.`** Miss it in the sentence chunker and streaming TTS silently stops working for Urdu.
- **Phone numbers read as cardinals** produce a meaningless multi-crore figure. Digit-by-digit, always.
- **Azure's SDK is synchronous.** Call it without `run_in_executor` and every concurrent call stutters.
- **Echo on laptop speakers** makes the agent interrupt itself in a loop. Wear a headset at the demo.
- **Disabling thinking to save latency** silently drops tool calls into visible text. Use `adaptive` + `effort: low`.
- **Recording intended rather than spoken text** after barge-in makes the agent reference things the caller never heard.
- **`PRAGMA foreign_keys` is per connection.** Set it when building the database and it is off again on the next connect — every bad write then passes silently.
- **`make dataset` before a demo** regenerates the population and overwrites hand-edited cells. Use `make db` alone after editing the workbook.
- **Placeholder policy text reads as real.** Four files in `knowledge/dvago/` commit DVAGO to terms they never agreed. Replace them.
- **Carrier licensing takes longer than the entire build.** Start it in week 0.
