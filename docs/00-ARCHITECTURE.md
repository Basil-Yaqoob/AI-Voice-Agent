# 00 — Coding Architecture

The concrete shape of the codebase: modules, boundaries, data flow, and the rules that keep the latency budget intact. Read `01-TECH-DECISIONS.md` first for *why* these choices; this document is *what to build*.

---

## Guiding rules

Four rules govern every file in the repo. Violations are bugs even when tests pass.

1. **Nothing blocks on the hot path.** Any `def` reachable from an audio callback is `async def`, and it never performs synchronous I/O, `time.sleep`, or CPU work over ~5ms. Enforced by `ruff` (ASYNC ruleset) in CI.
2. **Everything streams.** No component waits for a complete input before producing output. STT emits partials, the LLM emits tokens, TTS emits audio chunks, and each hands off at the earliest safe boundary.
3. **Providers sit behind interfaces.** No module imports `anthropic`, `deepgram`, or `azure` except that provider's own adapter. Enforced by an import-linter contract.
4. **Everything on the hot path is timed.** Each stage records its own duration into a per-turn `TurnMetrics` object. You cannot fix latency you are not measuring, and it *will* regress.

---

## Repository layout

```
ai-voice-agent/
├── pyproject.toml              # uv-managed deps, ruff + mypy config
├── uv.lock
├── docker-compose.yml          # Postgres 16 + pgvector, Redis
├── .env.example                # every var documented; never commit .env
├── Makefile                    # make dev / test / lint / eval / demo
│
├── src/agent/
│   ├── __init__.py
│   ├── config.py               # Pydantic Settings — the ONLY place env is read
│   ├── main.py                 # LiveKit worker entrypoint
│   │
│   ├── interfaces/             # Protocols. No vendor imports allowed here.
│   │   ├── stt.py              # STTProvider
│   │   ├── tts.py              # TTSProvider
│   │   ├── llm.py              # LLMProvider
│   │   └── knowledge.py        # KnowledgeStore
│   │
│   ├── providers/              # One subpackage per vendor. Vendor SDK imports live ONLY here.
│   │   ├── stt_deepgram.py
│   │   ├── tts_azure.py
│   │   ├── tts_inworld.py
│   │   ├── llm_claude.py
│   │   └── registry.py         # config string -> provider instance
│   │
│   ├── pipeline/               # The hot path. Guard this directory in review.
│   │   ├── session.py          # per-call state machine
│   │   ├── turn.py             # one user turn, end to end
│   │   ├── chunker.py          # token stream -> speakable sentences
│   │   └── interrupt.py        # barge-in cancellation
│   │
│   ├── language/               # Pure functions over strings. Heavily unit tested.
│   │   ├── normalize.py        # digits/dates/currency -> spoken Urdu
│   │   ├── script.py           # script detection, Urdu/Roman/Latin
│   │   └── prompts.py          # system prompt assembly (cache-stable)
│   │
│   ├── knowledge/
│   │   ├── store.py            # pgvector queries
│   │   ├── ingest.py           # corpus -> chunks -> embeddings (offline)
│   │   ├── cache.py            # Redis semantic answer cache
│   │   └── prefetch.py         # speculative retrieval on partial transcripts
│   │
│   ├── tools/                  # Tool-use functions exposed to the LLM
│   │   ├── registry.py
│   │   └── demo_backend.py     # fake order/balance API for the demo
│   │
│   ├── telemetry/
│   │   ├── metrics.py          # TurnMetrics, latency histograms
│   │   └── transcript.py       # structured per-call transcript log
│   │
│   └── api/
│       ├── server.py           # FastAPI: /token, /health, /metrics
│       └── static/             # demo frontend (index.html, app.js)
│
├── tests/
│   ├── unit/                   # no network, no docker, <5s total
│   ├── integration/            # docker-compose services, faked vendors
│   ├── latency/                # budget assertions
│   ├── fixtures/
│   │   ├── audio/              # short recorded Urdu utterances
│   │   └── golden/             # expected normalisation outputs
│   └── fakes/                  # FakeSTT, FakeTTS, FakeLLM
│
├── eval/
│   ├── run_eval.py             # TTFT + Urdu register scoring across models
│   └── cases.yaml              # code-switched test utterances
│
└── docs/                       # you are here
```

**The directory boundary that matters most:** `pipeline/` is the hot path. Code review for anything in that directory should be stricter than elsewhere — one blocking call there is audible to the caller.

---

## Data flow for one turn

```
 caller speaks
      │
      ▼
┌─────────────────┐  audio frames (20ms)
│  LiveKit room   │────────────────────────────┐
└─────────────────┘                            │
                                               ▼
                                   ┌───────────────────────┐
                                   │  STTProvider.stream() │
                                   └───────┬───────────────┘
                          partial transcript│      │final transcript
                    ┌──────────────────────-┘      │
                    ▼                              │
        ┌───────────────────────┐                  │
        │ prefetch.speculative()│  ← runs DURING   │
        │  pgvector + cache     │    endpointing;  │
        └───────────┬───────────┘    result warm   │
                    │                by the time   │
                    │                final arrives │
                    └──────────────┐    ┌──────────┘
                                   ▼    ▼
                            ┌──────────────────┐
                            │  turn.execute()  │
                            │  assemble prompt │  ← cached prefix + fresh suffix
                            └────────┬─────────┘
                                     ▼
                          ┌────────────────────┐
                          │ LLMProvider.stream │
                          └────────┬───────────┘
                                   │ tokens
                                   ▼
                          ┌────────────────────┐
                          │  chunker.sentences │  ← emits at first sentence boundary,
                          └────────┬───────────┘     does NOT wait for full response
                                   ▼
                          ┌────────────────────┐
                          │ normalize.for_tts  │  ← digits -> Urdu words
                          └────────┬───────────┘
                                   ▼
                          ┌────────────────────┐
                          │ TTSProvider.stream │
                          └────────┬───────────┘
                                   │ audio chunks
                                   ▼
                            ┌─────────────┐
                            │ LiveKit out │──► caller hears first syllable
                            └─────────────┘

 at any point: caller speaks again ──► interrupt.cancel() ──► TTS stops mid-chunk,
                                                              LLM stream cancelled,
                                                              partial response recorded
```

### Three mechanics that carry the whole design

**Speculative prefetch.** Retrieval starts on the *partial* transcript while the caller is still talking and while endpointing is still counting down. By the time the final transcript arrives, results are already in memory. This is what removes retrieval from the critical path — without it, RAG costs you 150–250ms you do not have.

The prefetch may be wrong (the caller's sentence changed meaning at the end). That is fine and expected: discard it and fall back to a synchronous retrieve. Prefetch is an optimisation, never a correctness dependency.

**Sentence chunking.** The chunker consumes the LLM token stream and emits the first *speakable* unit as soon as it sees a sentence boundary — Urdu `۔`, Latin `.`/`?`/`!`, or a length threshold. This is the single largest latency win in the pipeline: TTS starts on sentence one while the model is still writing sentence three.

The subtlety is that a sentence boundary is not always a *speakable* boundary. Do not emit a fragment shorter than ~15 characters (TTS prosody suffers badly on tiny fragments), and never split inside a number or an abbreviation.

**Barge-in cancellation.** When VAD fires while the agent is speaking, three things must happen within ~100ms: stop pushing TTS audio, cancel the in-flight LLM stream, and record what the agent *actually said* (not what it intended to say) into conversation history. That third one is routinely forgotten and causes the agent to reference things the caller never heard.

---

## Interface contracts

Keep these thin. A fat abstraction normalising every vendor's feature set is worse than none.

```python
# interfaces/stt.py
class STTProvider(Protocol):
    async def stream(
        self, audio: AsyncIterator[bytes], *, language: str, keyterms: Sequence[str]
    ) -> AsyncIterator[Transcript]: ...
    # Transcript = (text: str, is_final: bool, confidence: float)

# interfaces/tts.py
class TTSProvider(Protocol):
    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]: ...
    @property
    def sample_rate(self) -> int: ...

# interfaces/llm.py
class LLMProvider(Protocol):
    async def stream(
        self, messages: list[Message], *, system: CachedPrompt, tools: list[ToolDef]
    ) -> AsyncIterator[LLMEvent]: ...
    # LLMEvent = TextDelta | ToolCall | Done(usage)

# interfaces/knowledge.py
class KnowledgeStore(Protocol):
    async def search(self, query: str, *, k: int = 4) -> list[Chunk]: ...
```

`LLMEvent` being a union rather than a bare string is deliberate: tool calls must be distinguishable from text at the stream level, or the chunker will read a tool call aloud.

---

## Configuration

One module reads the environment: `config.py`, a Pydantic `Settings` class. Everything else takes config by injection. This makes tests trivially configurable and keeps secrets out of the call graph.

```python
class Settings(BaseSettings):
    # Providers — switchable at runtime, no code change (see Decision 5)
    stt_provider: Literal["deepgram", "fake"] = "deepgram"
    tts_provider: Literal["azure", "inworld", "fake"] = "azure"
    llm_provider: Literal["claude", "fake"] = "claude"

    llm_model: str = "claude-opus-5"
    llm_effort: Literal["low", "medium"] = "low"     # never high on the hot path
    llm_fast_mode: bool = False                       # Opus 5 fast mode; buys TTFT for money

    tts_voice_urdu: str = "ur-PK-UzmaNeural"
    stt_language: str = "ur"
    stt_keyterms: list[str] = []                      # loaded from knowledge/keyterms.txt

    # Latency budget — asserted by tests in tests/latency/
    budget_ttft_ms: int = 300
    budget_tts_ttfb_ms: int = 110
    budget_total_ms: int = 800

    endpoint_silence_ms: int = 180                    # tune on real PK call audio
```

**Never read `os.environ` outside `config.py`.** It is the difference between a testable system and one that only runs on your laptop.

---

## Prompt assembly and caching

Cache correctness is worth real money — a broken prefix silently triples the model bill. The rule is absolute:

> **The system prompt is byte-identical on every request of a session. No timestamps, no session IDs, no user names, no re-ordered tool lists.**

Structure:

```
┌─────────────────────────────────────────┐
│ CACHED PREFIX  (identical every request)│
│  1. Persona + register instructions     │
│  2. Code-switching rules + examples     │
│  3. Static company knowledge corpus     │
│  4. Tool definitions (sorted by name)   │  ← sort them, or ordering drift breaks cache
├─────────────────────────────────────────┤  ← cache_control breakpoint here
│ FRESH SUFFIX   (varies per turn)        │
│  5. Retrieved chunks for this turn      │
│  6. Conversation history                │
│  7. Current user utterance              │
└─────────────────────────────────────────┘
```

Dynamic context that *feels* like it belongs in the system prompt — caller name, account state, time of day — goes in the fresh suffix or as a mid-conversation `{"role": "system"}` message, never in the cached prefix.

`prompts.py` must expose `build_cached_prefix()` as a pure function with no clock or randomness access, so a unit test can assert byte-stability across calls. That test is not optional; see Task 03.

---

## Error handling

Voice has no error page. Every failure resolves to something the caller can hear.

| Failure | Response |
|---|---|
| STT drops mid-stream | Reconnect once silently; on second failure speak a re-ask prompt |
| LLM times out (>2s no first token) | Speak a pre-rendered filler ("ایک سیکنڈ..."), retry once, then escalate |
| TTS provider fails | Fail over to the secondary provider mid-call; log and continue |
| Tool call errors | Return the error as a tool result — let the model explain it in Urdu |
| Knowledge store unreachable | Continue without retrieval; the cached corpus covers most turns |
| Total pipeline failure | Speak a pre-rendered handoff line, transfer to human or take a callback |

**Pre-render your filler and failure audio at build time with ElevenLabs v3.** v3 is the best-sounding Urdu voice available and its inability to run real-time does not matter for fixed strings. This is the one place the research's rejected vendor is genuinely the right tool.

---

## Deployment topology

```
┌──────────────────────────────┐   ┌──────────────────────────────┐
│  Mumbai / UAE region         │   │  Railway (any region)        │
│                              │   │                              │
│  • LiveKit agent workers     │   │  • FastAPI control plane     │
│  • SIP gateway               │   │  • Corpus ingest jobs        │
│  • Redis (session + cache)   │   │  • Admin dashboard           │
│  • Postgres + pgvector       │   │  • Eval harness runs         │
│                              │   │                              │
│  LATENCY-CRITICAL            │   │  LATENCY-IRRELEVANT          │
└──────────────────────────────┘   └──────────────────────────────┘
```

Railway has no India region and Singapore adds 60–90ms round trip — 8–11% of the entire budget. Railway is a fine home for everything that is not in the audio path; it must not host the workers.

For the demo (Tasks 01–13), everything runs locally against LiveKit Cloud's free Build tier. Region only becomes a decision at Task 13.

---

## Scaling notes

Not needed for the demo; recorded so the demo does not paint you into a corner.

- **Workers are stateless.** All per-call state lives in the `Session` object in memory and in Redis. Scale horizontally on concurrent calls; LiveKit handles dispatch.
- **Rule of thumb:** roughly 1 vCPU per 4–6 concurrent calls, since the work is I/O-bound. Measure before sizing.
- **Warm pools matter.** Cold-starting a worker mid-call is not recoverable. Keep a minimum instance count above expected peak concurrency.
- **Provider rate limits bind before your CPU does.** ElevenLabs-style tiers cap concurrency explicitly; Deepgram, Azure and Anthropic all have per-account limits. Check them against target concurrency at 20k minutes/month, not at launch.
