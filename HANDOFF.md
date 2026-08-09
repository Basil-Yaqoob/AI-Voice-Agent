# Session handoff — 8 August 2026

Written so a fresh session, on a different account or machine, can pick up the code phase with no loss of context.

**Status: research and planning complete. Zero lines of application code written.**
Next action is TASK-01.

---

## How to resume

### Option A — fresh session (recommended)

`CLAUDE.md` loads automatically. Just open Claude Code in `D:\AI Voice Agent` and say:

> Read HANDOFF.md and README.md, then start TASK-01.

This costs almost no context and is the right choice for the code phase.

### Option B — replay the original conversation

The full transcript of the research session is preserved in two places:

- `C:\Users\Basil\.claude\projects\d--AI-Voice-Agent\71fccf24-e53d-4d3d-b469-95999a28d19e.jsonl` (original)
- `.claude/session-transcript-2026-08-08.jsonl` (copy inside this repo, ~2.1 MB — travels with the project)

Claude Code stores transcripts **per project path, not per account**, so `claude --resume` in this folder will still find the original after an account switch on this machine. Only use it if you need the reasoning behind a specific decision — it replays everything and burns a lot of context. All conclusions are already captured in the docs.

---

## What exists

```
D:\AI Voice Agent\
├── CLAUDE.md          auto-loaded project context
├── HANDOFF.md         this file
├── README.md          task board, quick start, cost summary
├── RESEARCH.md        full research: vendors, PTA regulation, PKR costing
├── .claude/
│   ├── urdu-voice-agent-constraints.md    portable copy of session memory
│   └── session-transcript-2026-08-08.jsonl
└── docs/
    ├── 00-ARCHITECTURE.md      module layout, data flow, hot-path rules
    ├── 01-TECH-DECISIONS.md    7 decisions, each with rejected alternatives
    ├── 02-TESTING-STRATEGY.md  how to test an audio-in/audio-out system
    └── TASK-01 … TASK-13       one file per task
```

~4,400 lines of documentation. No `src/`, no `tests/`, no `pyproject.toml` yet.

There is also a web version of the research document:
https://claude.ai/code/artifact/1ba0c6c7-d3bd-4592-9cae-79768e9f72f3

---

## Decisions already made — do not re-open without new evidence

| # | Decision | One-line reason |
|---|---|---|
| 1 | Direct SDK calls, **no LangChain/LangGraph** on the hot path | LiveKit Agents already is the orchestrator; framework overhead unaffordable at 300ms |
| 2 | Claude, `claude-opus-5` default, swappable by config | Urdu register control is the differentiating capability |
| 3 | Python 3.12 + asyncio | Best LiveKit and provider SDK support; workload is I/O-bound |
| 4 | LiveKit Agents over Pipecat / Vapi / Retell | Native SIP, scaling, and it does not constrain the vendor list |
| 5 | Thin interfaces over STT/TTS/LLM | Inworld Urdu is experimental — must be swappable at runtime |
| 6 | Postgres + pgvector, not a managed vector DB | Avoids a network hop we cannot afford |
| 7 | pytest with recorded audio fixtures | Push logic into pure string functions, test those exhaustively |

Full reasoning in `docs/01-TECH-DECISIONS.md`.

### The user's original LLM suggestions and why they were rejected

The user proposed DeepSeek V4 Flash, GPT-4o, or GPT-4o-mini. All three ruled out:

- **DeepSeek V4 Flash** — ~1.10s TTFT, which is 1.4× the *entire* 800ms budget. Cheap ($0.14/$0.28 per MTok) and genuinely good for batch/coding work, just not for real-time voice.
- **GPT-4o** — retired from ChatGPT 13 Feb 2026, fully retired 3 Apr 2026, API-deprecated.
- **GPT-4o-mini** — retired from Azure Foundry 31 Mar 2026, *and* mini-tier models degrade hardest on low-resource languages (published gaps up to 24.3%).
- **GPT-5 mini** — a legitimate non-Anthropic comparator; TASK-12 benchmarks it.

The user accepted this and asked me to pick. Default is `claude-opus-5`, but it is `LLM_MODEL` in config and TASK-12 exists specifically to let measurement override the default.

---

## Verified facts (checked against primary docs, 8 Aug 2026)

Re-verify the TTS row before committing money — that market moves fastest.

| Vendor | Urdu | Real-time | Price |
|---|---|---|---|
| Deepgram Nova-3 | ✅ `ur`, since Feb 2026, keyterm prompting | ✅ streaming | $0.0077/min |
| Deepgram Flux | ❌ not in the 10 languages | ✅ | $0.0078/min |
| Azure Neural TTS | ✅ `ur-PK` Uzma + Asad | ✅ | $16/1M ch, $7.50 committed |
| Inworld TTS-2 | ⚠️ experimental, TTS-2 only | ✅ | $5–15/1M ch |
| ElevenLabs Flash v2.5 | ❌ | ✅ | ~$50/1M ch |
| ElevenLabs v3 | ✅ | ❌ | ~$100/1M ch |
| Cartesia Sonic 3.5 | ❌ (Hindi yes) | ✅ | tiered |
| Google Cloud TTS | ❌ none, any tier | — | — |

Other verified numbers: LiveKit agent session $0.01/min, SIP $0.003–0.004/min. Railway has no India region. USD/PKR 278.6 mid-market on 8 Aug 2026. Deepgram gives $200 free credit with no expiry; Azure gives 500k chars/month free indefinitely.

---

## Open items — not resolved, need action

| Item | Owner | Notes |
|---|---|---|
| **Carrier contracting** | business | Approach 2–3 LL/LDI licensees for SIP quotes + **written licence position**. Longest lead time in the project. Start immediately; it does not start itself. |
| **Real PK carrier rates** | business | The PKR 1.50–3.00/min in RESEARCH.md is a planning estimate, not a quote. |
| **Voice sign-off** | client | Run the A/B (Azure Uzma / Azure Asad / Inworld / Hindi-transliteration) and get a written choice before pipeline work. |
| **Native Urdu speaker** | hiring | Needed for TASK-06 golden-file review (~1 hour) and TASK-12 quality scoring. Cheapest quality investment in the project. |
| **Phone-line audio corpus** | data | TASK-09 needs 30–50 labelled utterances recorded over a real phone line, not a laptop mic. |
| **Urdu STT accuracy** | unknown | No public WER benchmarks comparing Deepgram/Whisper/Azure for Urdu exist. Must be measured on your own audio. |
| **Client identity** | unknown | Never specified. Knowledge base content and tool integrations depend on it. |
| **Exact corpus size** | unknown | Determines whether TASK-07 needs RAG at all — under ~40k tokens, the cached-prefix route wins on both latency and cost. |

---

## Recommended next steps

1. **TASK-01** — repo scaffold (2–3 h). Verify the import-linter guard actually fails when you add a forbidden import; an unverified guardrail is not a guardrail.
2. **TASK-02** — config and secrets (2 h).
3. Then **03–07 in parallel** if you have more than one person — they are independent once 02 is done.
4. **TASK-08** integrates them; it is the hardest task and needs 03–07 finished.

Roughly three weeks to a client-ready demo, single developer.

In parallel from day one, and not blocked by any code: start the **carrier conversation** and the **voice bake-off**.

---

## Things I would tell you in person

- **The docs are opinionated on purpose.** Where I made a judgement call, the rejected alternative is written down. If you disagree with one, you have what you need to overturn it — but overturn it explicitly rather than drifting.
- **TASK-06 (Urdu normalisation) is small and disproportionately important.** It is where competing demos visibly fail — a phone number read as a multi-crore cardinal. It is pure string functions, so it is also the easiest thing in the project to test properly.
- **The 800ms budget is the spine.** Every time you are tempted to add a layer, price it against the budget first. That single discipline is what separates this from the demos the client has already been shown.
- **Do not let the carrier work drift.** It is the only item here that software effort cannot accelerate, and it is the most likely cause of a missed launch date.
