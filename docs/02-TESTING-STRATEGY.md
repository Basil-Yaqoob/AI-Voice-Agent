# 02 — Testing Strategy

Voice agents are awkward to test: the input is audio, the output is audio, and the quality bar ("does this sound natural in Urdu?") is not a boolean. This document sets out what we test, at which level, and how to make audio testable.

Each `TASK-*.md` file lists the specific tests for that unit. This document is the shared framework they assume.

---

## The core problem and the core answer

You cannot assert on a waveform. So we push logic *out* of the audio path and into pure functions over strings, and test those exhaustively.

Nearly every real bug in this system is text-level:

- Urdu number normalisation producing the wrong grammatical form
- The sentence chunker splitting inside a decimal or an abbreviation
- A timestamp sneaking into the cached prompt prefix and silently killing cache hits
- Retrieval ranking Urdu chunks below English ones for a code-switched query
- Barge-in recording the intended response instead of the spoken one

None of those need audio to catch. Test them as string functions and the audio layer becomes a thin shell that only integration tests need to touch.

---

## Test pyramid

| Level | Count | Runtime | Network | Runs |
|---|---|---|---|---|
| **Unit** | ~150 | < 5 s | never | every save |
| **Integration** | ~25 | < 60 s | docker only | every push |
| **Latency** | ~8 | < 30 s | fakes w/ delay | every push |
| **Contract** | ~10 | ~2 min | real vendors | nightly + pre-release |
| **Eval** | ~30 cases | ~5 min | real vendors | before demos, on model change |

### Unit — the bulk of the value

No network, no Docker, no sleeping. If a unit test takes more than 50ms, something is wrong with it.

Covers: `language/`, `pipeline/chunker.py`, `pipeline/interrupt.py`, prompt assembly, retrieval ranking, tool schemas, config validation.

### Integration — wiring, against fakes

Real Postgres and Redis from `docker-compose`, but **faked vendors**. Proves the components connect correctly without paying for API calls or depending on someone else's uptime in CI.

Covers: full turn execution, prefetch-then-final-transcript flow, provider failover, knowledge ingest → search round trip.

### Latency — budget assertions as tests

The budget from `RESEARCH.md` encoded as failing tests. Fakes are configured with realistic delays so the *orchestration* overhead is what gets measured — this catches an accidental `await` serialisation that should have been concurrent.

```python
async def test_prefetch_removes_retrieval_from_critical_path():
    """Retrieval must overlap endpointing, not add to it."""
    knowledge = FakeKnowledge(delay_ms=200)
    turn = build_turn(knowledge=knowledge)

    elapsed = await time_turn(turn, partial_lead_ms=250)

    # 200ms retrieval hidden under 250ms of endpointing => contributes ~0
    assert elapsed.retrieval_on_critical_path_ms < 20
```

### Contract — do the vendors still behave?

Hits real APIs. Not in the main CI loop; scheduled nightly and required before a release.

These exist because **the vendor landscape is the risk in this project**. Inworld's Urdu is flagged experimental. Deepgram's Urdu shipped six months ago. A contract test that fails at 3am tells you a vendor changed something before a client does.

```python
@pytest.mark.contract
async def test_azure_still_has_ur_pk_voices():
    voices = await azure_list_voices()
    names = {v.short_name for v in voices}
    assert "ur-PK-UzmaNeural" in names
    assert "ur-PK-AsadNeural" in names
```

### Eval — quality, which is not pass/fail

Scores rather than asserts. Covered fully in `TASK-12`.

---

## Making audio testable

### Golden audio fixtures

Commit a small set of short real recordings to `tests/fixtures/audio/`. Aim for 8–12 clips, 2–5 seconds each, ideally recorded over an actual phone line rather than a laptop mic:

| Fixture | Contents | Catches |
|---|---|---|
| `pure_urdu_short.wav` | Plain Urdu question | Baseline STT |
| `codeswitch_order.wav` | "میرا **order** کہاں ہے" | English noun in Urdu frame |
| `codeswitch_number.wav` | Urdu with an English digit string | The hardest STT case |
| `interrupt_early.wav` | Speech starting 400ms in | Barge-in trigger |
| `long_pause_mid.wav` | 700ms pause mid-sentence | **Endpointing false-positive** |
| `background_noise.wav` | Street noise behind speech | VAD robustness |
| `fast_speaker.wav` | Rapid Urdu | Partial transcript churn |
| `silence.wav` | 3s of nothing | No spurious turn |

`long_pause_mid.wav` is the most valuable file in the set. Urdu speakers pause mid-sentence more than English speakers, and a default VAD threshold will cut them off. That single fixture protects the tuning work from Task 09.

### Fakes over mocks

Every provider interface gets a hand-written fake in `tests/fakes/`, not a `MagicMock`. Fakes are deterministic, support configurable delays for latency tests, and record their calls for assertion.

```python
class FakeLLM(LLMProvider):
    def __init__(self, *, script: list[str], ttft_ms: int = 0,
                 tokens_per_sec: float = 60.0):
        self.script, self.ttft_ms, self.tps = script, ttft_ms, tokens_per_sec
        self.calls: list[LLMCall] = []
        self.cancelled = False

    async def stream(self, messages, *, system, tools):
        self.calls.append(LLMCall(messages, system, tools))
        await asyncio.sleep(self.ttft_ms / 1000)
        try:
            for token in tokenize(self.script[len(self.calls) - 1]):
                await asyncio.sleep(1 / self.tps)
                yield TextDelta(token)
        except asyncio.CancelledError:
            self.cancelled = True     # barge-in tests assert on this
            raise
```

The `cancelled` flag is the point. Barge-in correctness means the LLM stream was *actually* cancelled, not just ignored — a leaked task keeps billing and can emit audio after the caller has moved on.

---

## Testing Urdu specifically

You cannot eyeball Urdu regressions in a diff, so the tests carry the burden.

**1. Table-driven normalisation tests.** Every case as data, not code:

```python
NORMALIZATION_CASES = [
    ("1500",       "پندرہ سو",              "plain integer"),
    ("Rs. 1,500",  "پندرہ سو روپے",         "currency with separator"),
    ("2026-08-08", "آٹھ اگست دو ہزار چھبیس", "ISO date"),
    ("03001234567","صفر تین صفر صفر ...",   "phone: digit-by-digit, not a number"),
    ("50%",        "پچاس فیصد",             "percentage"),
    ("3.5",        "ساڑھے تین",             "decimal → idiomatic form"),
]

@pytest.mark.parametrize("raw,expected,desc", NORMALIZATION_CASES)
def test_urdu_normalization(raw, expected, desc):
    assert normalize_for_tts(raw) == expected, desc
```

Phone numbers are the trap: `03001234567` must be read digit-by-digit, never as a single enormous number. This is a real failure mode in shipped systems.

**2. Round-trip property tests.** Normalised text must contain no ASCII digits and must still be synthesisable:

```python
@given(st.integers(min_value=0, max_value=10_000_000))
def test_no_ascii_digits_survive(n):
    out = normalize_for_tts(str(n))
    assert not any(c.isdigit() and c.isascii() for c in out)
```

**3. Script-mixing assertions.** Given a code-switched input, the output must preserve English tokens that were in the source and must not transliterate them into Urdu script:

```python
def test_english_product_names_survive_normalization():
    out = normalize_for_tts("آپ کا Premium Plan فعال ہے")
    assert "Premium Plan" in out
```

**4. A native speaker reviews the golden file.** Automated tests confirm *stability*, not *correctness*, for Urdu output. Have a native Urdu speaker review `tests/fixtures/golden/urdu_normalization.yaml` once, sign off, and from then on the tests protect that judgement. Budget an hour of a native speaker's time — it is the cheapest quality investment in the project.

---

## Cache-correctness tests

The prompt cache silently costing 3× is a bug class of its own and deserves dedicated tests.

```python
def test_cached_prefix_is_byte_stable():
    """Any drift here triples the model bill and nothing visibly breaks."""
    a = build_cached_prefix(company="Acme", corpus=CORPUS)
    time.sleep(0.01)
    b = build_cached_prefix(company="Acme", corpus=CORPUS)
    assert a == b

def test_cached_prefix_has_no_clock_dependency():
    with freeze_time("2026-01-01"):
        a = build_cached_prefix(company="Acme", corpus=CORPUS)
    with freeze_time("2027-06-15"):
        b = build_cached_prefix(company="Acme", corpus=CORPUS)
    assert a == b

def test_tool_definitions_are_sorted():
    """Unsorted tools reorder between runs and invalidate the prefix."""
    prefix = build_cached_prefix(company="Acme", corpus=CORPUS, tools=SHUFFLED_TOOLS)
    names = extract_tool_names(prefix)
    assert names == sorted(names)
```

And in integration, assert the cache actually engaged:

```python
@pytest.mark.contract
async def test_cache_reads_are_nonzero_on_second_turn():
    session = await start_session()
    await session.turn("سلام")
    usage = (await session.turn("میرا آرڈر کہاں ہے")).usage
    assert usage.cache_read_input_tokens > 0, "cache not engaging — check prefix stability"
```

---

## Running the suite

```bash
make test           # unit + integration; the everyday command
make test-unit      # < 5s, run on save
make test-latency   # budget assertions
make test-contract  # real vendor calls — costs money, needs keys
make eval           # quality scoring across models
```

CI runs unit + integration + latency on every push; contract tests nightly.

---

## Definition of done for any task

A task is complete when all of these hold:

- [ ] Its stated acceptance criteria pass
- [ ] Unit tests written and green, covering the listed cases
- [ ] No new `ruff` or `mypy` findings
- [ ] Nothing on the hot path is synchronous
- [ ] New env vars documented in `.env.example`
- [ ] Any new latency-affecting code has a `tests/latency/` assertion
