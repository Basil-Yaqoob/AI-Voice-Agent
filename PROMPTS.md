# Session prompts for the code phase

Copy-paste prompts for building this project across ~6 Claude Code sessions.

**Strategy:** batch by dependency layer. Not all 13 tasks in one session (context exhaustion, and errors compound into TASK-08). Not one task per session either (wastes quota re-establishing context).

Run the checkpoint between every session.

---

## Session 1 — Foundation (TASK-01, TASK-02)

```
Read CLAUDE.md, HANDOFF.md, docs/00-ARCHITECTURE.md and docs/02-TESTING-STRATEGY.md.

Then implement TASK-01 and TASK-02 in full, following docs/TASK-01-repo-scaffold.md
and docs/TASK-02-config-and-secrets.md exactly.

Requirements:
- Work through every acceptance criterion in both files. Do not skip any.
- Write the unit tests listed in each file, and run them. Show me the output.
- For TASK-01, deliberately verify the import-linter guard: add `import anthropic`
  to a pipeline module, confirm `make lint` FAILS, then revert. An unverified
  guardrail is not a guardrail.
- Run `make lint` and `make test` at the end and show me real output, not a summary.

If a spec detail is genuinely ambiguous, make the call and tell me what you assumed.
If something in the docs is wrong or won't work, say so before implementing it.
```

---

## Session 2 — Provider adapters (TASK-03, TASK-04, TASK-05)

```
Read CLAUDE.md and docs/00-ARCHITECTURE.md. TASK-01 and TASK-02 are complete.

Implement TASK-03, TASK-04 and TASK-05 from their docs. Write all three together
so the interface patterns stay consistent across them.

Priorities in order:
1. The interfaces in src/agent/interfaces/ are the contract. Get them right first,
   then write adapters against them.
2. Prompt-cache stability (TASK-03) is the highest-value thing here. Write the
   byte-stability tests BEFORE the implementation.
3. The Azure SDK is synchronous — it must run in an executor. Include the
   concurrency test from TASK-05 that catches a blocked event loop.
4. Also write the fakes in tests/fakes/ (FakeSTT, FakeTTS, FakeLLM) — later tasks
   depend on them. FakeLLM needs the `cancelled` flag for barge-in tests.

Do not write contract tests that hit real APIs yet; I'll run those separately.

Run `make lint` and `make test` at the end, show real output.
```

---

## Session 3 — Language and knowledge (TASK-06, TASK-07)

```
Read CLAUDE.md. TASK-01 through TASK-05 are complete.

Implement TASK-06 and TASK-07 from their docs.

TASK-06 is mostly pure string functions and gets the deepest test coverage in the
project. Note specifically:
- The 0-99 Urdu numerals MUST be a lookup table, not generated. Urdu numerals are
  irregular and generated ones are subtly wrong.
- South Asian grouping (لاکھ, کروڑ), not Western.
- Pakistani mobile numbers read digit-by-digit, never as a cardinal.
- Produce tests/fixtures/golden/urdu_normalization.yaml. Flag clearly in your
  summary that it needs native-speaker review before I trust it.

TASK-07: build the corpus-size check first. If the corpus fits in the cached
prefix, most of the retrieval machinery is unnecessary — say so rather than
building it speculatively. The speculative prefetch is the part that matters;
include the latency test proving retrieval stays off the critical path.

Run `make lint` and `make test`, show real output.
```

---

## Session 4 — The pipeline (TASK-08, TASK-09)

**Give this one its own session. It is the hardest task and it integrates everything.**

```
Read CLAUDE.md, docs/00-ARCHITECTURE.md and docs/02-TESTING-STRATEGY.md.
TASK-01 through TASK-07 are complete.

Implement TASK-08 and TASK-09. This is the hot path — the four rules in
00-ARCHITECTURE.md apply strictly here.

The three things most likely to go wrong, in order:
1. The Urdu full stop is ۔ (U+06D4), not '.'. If the chunker misses it, streaming
   TTS silently stops working for Urdu with no error. Test this first.
2. Tool calls must never reach TTS.
3. After barge-in, conversation history records what was SPOKEN, not what the model
   intended to say.

Model the session as an explicit state machine, not booleans. Illegal transitions
should raise.

TASK-09 needs real labelled audio I don't have yet. Build the adaptive endpointer
and its unit tests (the Urdu conjunction and verb-final rules are testable as pure
functions). Stub the corpus-based evaluation and tell me what audio to collect.

Run `make lint`, `make test` and `make test-latency`, show real output.
```

---

## Session 5 — Demo surface (TASK-10, TASK-11)

```
Read CLAUDE.md. TASK-01 through TASK-09 are complete.

Implement TASK-10 and TASK-11.

TASK-10: no build step. Vendor livekit-client.umd.js locally — do not load from a
CDN. Echo cancellation must be on in the audio constraints; without it the agent
interrupts itself on laptop speakers, which is the classic demo-day failure.
Urdu must render RTL in a Nastaliq font stack.

TASK-11: the cache-zero alarm is the most important thing here — it's the only
alarm that catches a silent 3x cost increase. Use perf_counter throughout, never
time.time(). Cost in PKR at the landed rate (295), not mid-market.

Both: telemetry and UI publishing must never be able to drop a call. Include the
isolation tests.

Run `make lint` and `make test`, show real output.
```

---

## Session 6 — Eval and demo prep (TASK-12, TASK-13)

```
Read CLAUDE.md. TASK-01 through TASK-11 are complete.

Implement TASK-12 and produce the TASK-13 checklist.

TASK-12: the harness itself needs tests — a broken harness produces confident wrong
decisions. Include the p95 gate logic (a model failing p95 > 300ms is out regardless
of quality; this is what ruled out DeepSeek V4 Flash). Blind CSV export with model
names stripped and rows shuffled.

Build eval/cases.yaml with at least 25 cases weighted toward the hard categories:
code-switching, number readback, ambiguity, out-of-scope. Everyone passes the easy
ones; the model decision lives in the hard ones.

Then run `make eval` against whatever credentials I have configured and give me the
report with a recommendation.
```

---

## Checkpoint — run between every session

```bash
make lint          # ruff + mypy + import-linter, all clean
make test          # unit + integration green
git add -A && git commit -m "TASK-0X: <what landed>"
```

If lint or tests fail, fix before starting the next session. A bad batch that gets
built on is far more expensive than one that gets caught.

---

## Notes on prompting

- **Role framing ("act as a senior engineer") adds very little.** The acceptance
  criteria in the task docs do that work. Point at the doc instead.
- **Always demand real test output.** "Tests pass" without output is the most common
  way work gets reported as done when it isn't.
- **Give explicit permission to push back.** The line about saying so if the docs are
  wrong matters — some of these specs were written without code in front of them, and
  a session that silently implements a bad spec is worse than one that argues.
- **Say what you don't have.** Missing credentials, missing audio, missing corpus.
  Otherwise you get plausible-looking stubs presented as working code.
```
