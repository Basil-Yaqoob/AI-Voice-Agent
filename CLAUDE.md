# CLAUDE.md

Auto-loaded context for this project. Keep it tight — it costs tokens on every session.

## What this is

A production Urdu/English voice agent for Pakistani customers. **All fourteen tasks built as of 10 Aug 2026** — 648 tests passing, lint clean.

Deployed against a client: **DVAGO**, a pharmacy chain. Persona in `agent/language/personas.py`, policy corpus in `knowledge/dvago/`, business data in `data/dvago/dvago.db` behind eight tools in `agent/tools/dvago_backend.py`.

**Read `README.md` first** for current status; `HANDOFF.md` for the deferred items that still need a human.

## Target profile (decided, do not re-litigate)

- **Channels:** inbound PSTN + outbound PSTN + WhatsApp voice
- **Volume:** 5,000–50,000 min/month
- **Language:** code-switched Urdu + English (not formal Urdu)
- **Number:** local +92 required, non-negotiable
- **Latency:** sub-800ms end-of-speech to first audio

## The three constraints that shaped every decision

1. **ElevenLabs cannot do real-time Urdu.** Flash v2.5 has no Urdu; v3 has Urdu but cannot stream. The entire viable market is **Azure `ur-PK`** and **Inworld TTS-2** (Urdu flagged *experimental*). Cartesia and Google Cloud TTS have no Urdu at all. This is why TTS sits behind a runtime-switchable interface.
2. **A +92 voice number needs a PTA LL/LDI licensee partner.** A CVAS licence explicitly cannot carry voice. Contracting timeline, not engineering — starts week 0, runs underneath everything.
3. **800ms budget governs every vendor choice.** `50 network + 180 endpointing + 100 STT + 300 LLM TTFT + 110 TTS TTFB + 60 egress`. Anything that cannot fit its slice is disqualified. This ruled out DeepSeek V4 Flash (~1.10s TTFT) and LangGraph on the hot path.

## Stack (decided — see docs/01-TECH-DECISIONS.md for rejected alternatives)

| Layer | Choice |
|---|---|
| Runtime | Python 3.12 + asyncio, `uv` |
| Orchestration | LiveKit Agents. **No LangChain/LangGraph on the hot path** |
| STT | Deepgram Nova-3 (`ur`) + keyterm prompting |
| LLM | Claude, `claude-opus-5` default, swappable via `LLM_MODEL` |
| TTS | Azure `ur-PK` ⇄ Inworld TTS-2, runtime failover |
| Knowledge | Postgres 16 + pgvector, Redis answer cache. **Unused by DVAGO — its corpus fits the cached prefix** |
| Client business data | SQLite on a thread pool, built from an Excel workbook (`make data`) |
| API / UI | FastAPI + vanilla HTML with vendored LiveKit JS (no build step) |
| Tests | pytest, pytest-asyncio, ruff, mypy, import-linter |

## Hot-path rules (violations are bugs even when tests pass)

1. Nothing blocks. Anything reachable from an audio callback is `async` with no sync I/O.
2. Everything streams. No component waits for a complete input.
3. Providers sit behind interfaces. Vendor SDKs import **only** in their own adapter (enforced by import-linter).
4. Everything on the hot path is timed into `TurnMetrics`.

`src/agent/pipeline/` is the hot path — review it more strictly than anything else.

## Model configuration for voice

```python
thinking={"type": "adaptive"}
output_config={"effort": "low"}    # low | medium — never high
```

**Do not disable thinking to save latency.** On Opus 5 it causes tool calls to be written into visible text instead of `tool_use` blocks — silent correctness bug, no error raised.

## Gotchas that will cost you a day

- The Urdu full stop is **`۔` (U+06D4)**, not `.`. Miss it in the sentence chunker and streaming TTS silently stops working for Urdu.
- Any timestamp/session-id in the cached system prompt kills prompt caching → **3× the model bill, no visible symptom.** Watch `cache_read_tokens`.
- Pakistani phone numbers must be read **digit-by-digit**, never as a cardinal.
- Azure Speech SDK is **synchronous** — needs `run_in_executor` or every concurrent call stutters.
- Azure region must be `southeastasia`, not a US region (~200ms difference).
- After barge-in, record what was **spoken**, not what was intended.
- Railway has no India region — fine for control plane, never for media workers.
- `PRAGMA foreign_keys` is **per connection**. Set it only at build time and every bad write passes silently.
- The caller's phone number comes off the signed participant identity, **never** from a tool argument or the conversation. No tool takes a phone parameter; a test enforces it.
- Four files in `knowledge/dvago/` are `Status: PLACEHOLDER` invented policy. They read as real — replace before any real caller.

## Conventions

- British English in prose and docs.
- Cost figures in PKR. Mid-market 278.6 PKR/USD for arithmetic; **budget at ~295** landed.
- Every task doc has: header table → steps → acceptance criteria → unit tests → common pitfalls. Match that shape for new ones.
- Do not add dependencies without recording the decision in `docs/01-TECH-DECISIONS.md`.
