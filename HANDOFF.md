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
    ├── 01-TECH-DECISIONS.md    11 decisions, each with rejected alternatives
    ├── 02-TESTING-STRATEGY.md  how to test an audio-in/audio-out system
    └── TASK-01 … TASK-14       one file per task
```

**Updated 10 Aug 2026:** the code exists now. `src/agent/` holds the full
pipeline, `tests/` holds 595 passing tests, and the agent is wired to the DVAGO
deployment — persona, policy corpus, eight tools and a SQLite business database
in `data/dvago/`. `make data` builds the database; `make demo` runs the agent.
The current status table lives in `README.md`; the rest of this file is the
8 Aug planning snapshot plus the deferred items below, which are still open.

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

## ⚠️ Deferred with client sign-off — must be done before production

### Endpointing is unvalidated against real speech

**Status:** deliberately deferred 10 Aug 2026. No recorded Pakistani call audio
exists yet; the client relationship is not confirmed, so collecting it was not
worth the effort. **This is a known gap, not an oversight.**

`AdaptiveEndpointer` (TASK-09) passes its unit tests, and its Urdu rules —
verb-final clauses cut early, trailing conjunctions wait — are linguistically
sound. But **the cut-off rate has never been measured against a real caller.**
That number decides whether the agent feels rude, and it is the single most
likely reason a demo lands badly despite good latency figures.

**What is still required, before real traffic:**

1. **30–50 labelled utterances, recorded over an actual phone line.** Not a
   laptop microphone: codec compression and packet loss change VAD behaviour
   materially, and thresholds tuned on clean audio do not transfer.
2. Coverage must include mid-sentence hesitation, pauses before numbers
   ("میرا آرڈر نمبر ہے... چار چار سات ایک"), fast speakers, speakerphone and
   street noise, and trailing fillers (`اممم`, `اچھا`).
3. Each clip labelled with the true end-of-speech timestamp.
4. Run `eval/endpointing_eval.py` (to be written with the corpus) and record a
   **baseline before tuning** — without it, threshold changes become
   superstition.
5. Acceptance targets from TASK-09: **cut-off rate below 3%**, median added
   latency at or under 180ms.

**Also still missing:** `tests/fixtures/audio/` is empty. The eight golden audio
fixtures described in `02-TESTING-STRATEGY.md` — especially
`long_pause_mid.wav`, the most valuable file in the set — depend on the same
recording session.

Until this is done, treat the 180ms endpointing slice of the latency budget as
an estimate rather than a measurement.

### DVAGO's policy text is placeholder

**Status:** unavoidable as of 10 Aug 2026, and the highest-risk open item.

`knowledge/dvago/` holds what the agent knows without asking the database. One
file is drawn from DVAGO's public site and is marked `Status: public`. **Four
are partly or wholly invented and marked `Status: PLACEHOLDER`** — most
seriously `04-returns-refunds-complaints.md`, which is placeholder end to end.

DVAGO publishes a return policy, a refund policy and an FAQ set at
`dvago.pk/policies` and `dvago.pk/faqs`, but those pages render client-side and
the text could not be read automatically. What is there instead is a plausible
Pakistani pharmacy policy, which is precisely the problem: it reads as real.

**An agent that quotes a 48-hour return window DVAGO does not offer creates a
commitment somebody then has to honour or argue about.** The placeholders are
fine for a rehearsal and unacceptable in front of a customer.

**To close it:** ask DVAGO for the text behind those three pages, paste it in,
delete the `Status:` line. Half an hour of work, entirely blocked on the client.

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
| **Real DVAGO policy text** | client | See below. The four placeholder files are the highest-risk item on this list that code can fix. |
| **~~Client identity~~** | ✅ resolved 10 Aug 2026 | DVAGO, a Pakistani pharmacy chain. Knowledge corpus, tools and demo database all built against it — TASK-14. |
| **~~Exact corpus size~~** | ✅ resolved 10 Aug 2026 | ~2,570 tokens; assembled prefix ~3,940. Far below the 40k ceiling, so **this deployment needs no RAG** — the cached prefix wins on both latency and cost. pgvector stays built for when the client hands over their full policy library. |

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

---

## Fixed 10 Aug 2026 — three bugs found by running both modes end to end

Both `VOICE_MODE=realtime` and `VOICE_MODE=pipeline` + `TTS_PROVIDER=uplift`
are now verified end to end: greeting, caller transcript, tool call, and a
correct answer quoting 328 packs from the database.

**1. Uplift replies died with `InvalidState - failed to capture frame`.**
Not the vendor, the voice id or the sample rate, and not the concurrency it
looked like — serialising `play()` behind a lock changed nothing. `LiveKitSink`
pushed each chunk the provider yielded as one `AudioFrame`, and
`AudioSource.capture_frame` **rejects a frame that cannot fit beside what is
already queued**. Azure and the realtime API happen to yield 20-40ms chunks, so
this worked by luck for two years of the project's life; Uplift streams the raw
HTTP body, whose chunks are tens of kilobytes, and the *second* one is refused
while the first is still playing. That is why the short greeting survived and
the longer reply did not. The sink now re-cuts everything into 20ms frames, so
no vendor's socket-flushing behaviour can reach LiveKit. Reproduced and fixed
against the real `AudioSource`, not a fake.

**2. Realtime calls died in `getaddrinfo` for `api.openai.com`.** Two of three
of the user's calls, with the caller sitting on a connected line hearing
nothing for the ten-second default timeout before being hung up on. The link
from Pakistan is not reliably fast. Now three attempts at 4s, and a distinct
`RealtimeUnavailable` that the worker reports to the page — a connected call
with no agent behind it is otherwise indistinguishable from a working one.
**If this recurs often, `VOICE_MODE=pipeline` does not touch that host.**

**3. Deepgram closed the socket with 1011 whenever audio stopped for ~10s**,
which ended the call. A browser applying silence suppression sends nothing
while the caller is quiet, and a caller reading a number off a box is quiet for
longer than that. `DeepgramSTT` now sends a KeepAlive on an idle socket.

Also: the demo page listed all three vendors' voices at once and left the
operator to know which group was live. Selecting a foreign voice id does
nothing — no error in the browser, none on the wire — so the picker looked
broken. The page now builds its list from `/api/config`, which offers only what
the running mode can speak.
