# TASK-12 — Evaluation harness: pick the model and the voice with data

| | |
|---|---|
| **Depends on** | TASK-03, TASK-05, TASK-11 |
| **Blocks** | TASK-13 |
| **Estimate** | 1.5 days |
| **Level** | Advanced |

---

## Objective

Answer two questions with measurements instead of opinions:

1. **Which LLM?** `01-TECH-DECISIONS.md` defaults to `claude-opus-5` and explicitly defers to measurement. This harness produces that measurement.
2. **Which voice?** The research recommends an A/B of Azure `ur-PK`, Inworld, and the Hindi-transliteration route, with the client choosing. This builds the comparison.

Both questions have a latency axis and a quality axis, and quality in Urdu cannot be scored by an assertion. The harness produces numbers for latency and structured judgements for quality — it does not pretend the second is automatable.

---

## Steps

### 1. Test case corpus

`eval/cases.yaml` — 25–30 cases covering what actually happens on calls:

```yaml
cases:
  - id: simple_status
    utterance: "میرا آرڈر کہاں ہے"
    category: faq
    expect_tool: get_order_status
    must_stay_urdu: true

  - id: codeswitch_heavy
    utterance: "میرا order abhi tak deliver nahi hua, tracking check kar dein"
    category: codeswitch
    notes: "Roman Urdu + English nouns — the realistic phone register"
    must_preserve_english: [order, tracking, deliver]

  - id: number_readback
    utterance: "میرا balance بتائیں"
    category: numbers
    notes: "Answer contains an amount — checks Urdu number normalisation"
    must_not_contain_ascii_digits: true

  - id: phone_number
    utterance: "میرا نمبر 03001234567 ہے"
    category: numbers
    notes: "Must be read digit-by-digit, never as a cardinal"

  - id: ambiguous
    utterance: "یہ کام نہیں کر رہا"
    category: clarification
    notes: "Should ask a clarifying question, not guess"

  - id: out_of_scope
    utterance: "موسم کیسا ہے"
    category: refusal
    notes: "Should decline gracefully in Urdu and redirect"
```

Include the awkward categories. Anyone can pass `simple_status`; the model choice is decided by `codeswitch_heavy`, `ambiguous` and `number_readback`.

### 2. Latency benchmark

`eval/run_eval.py`. Latency is fully automatable and the results are unambiguous.

```python
async def bench_llm(model: str, cases: list[Case], *, runs: int = 5) -> LatencyReport:
    ttfts: list[float] = []
    for case in cases:
        for _ in range(runs):
            start = time.perf_counter()
            async for ev in provider.stream(...):
                if isinstance(ev, TextDelta):
                    ttfts.append((time.perf_counter() - start) * 1000)
                    break
            await asyncio.sleep(0.2)     # avoid rate-limit skew
    return LatencyReport(
        model=model,
        p50=statistics.median(ttfts),
        p95=percentile(ttfts, 95),
        p99=percentile(ttfts, 99),
    )
```

Three things that make the numbers trustworthy:

- **Report p95, not just p50.** A model with a 250ms median and a 900ms p95 fails one call in twenty. That is a bad agent.
- **Run from where the agent will run.** Benchmarking from a Karachi laptop measures your home internet, not production. Run from the target region.
- **Space the requests.** Back-to-back calls hit rate limiting and inflate the tail with retries.

**A hard gate before any quality scoring:** if p95 TTFT exceeds the 300ms budget, the model is out regardless of how good its Urdu is. This is what ruled out DeepSeek V4 Flash at ~1.10s.

### 3. Urdu quality scoring

Quality is a judgement. The harness structures the judgement; it does not replace it.

**Rubric** — score each response 1–5 on four axes:

| Axis | 1 | 5 |
|---|---|---|
| **Register** | Formal/literary, sounds like a news bulletin | Sounds like a good Karachi call-centre agent |
| **Code-switching** | All-Urdu or drifts into all-English | English nouns exactly where a real speaker would put them |
| **Brevity** | Rambling paragraph | One or two sentences, phone-appropriate |
| **Correctness** | Wrong or invented | Accurate, tools used when needed |

**Automated pre-filters** run first and catch the objective failures cheaply:

```python
def hard_checks(case: Case, response: str) -> list[str]:
    fails = []
    if case.must_stay_urdu and _urdu_char_ratio(response) < 0.5:
        fails.append("drifted out of Urdu")
    for term in case.must_preserve_english:
        if term.lower() not in response.lower():
            fails.append(f"lost English term: {term}")
    if case.must_not_contain_ascii_digits and any(c.isdigit() and c.isascii() for c in response):
        fails.append("ASCII digits reached output")
    if len(response) > 400:
        fails.append("too long for a spoken turn")
    return fails
```

**Blind human scoring** for what remains. Emit a CSV with responses shuffled and model names stripped, so the reviewer scores the output, not the brand. Two reviewers if you can get them; report agreement.

An LLM judge is a reasonable *pre-screen* to rank candidates before human review, but do not let it be the final word on Urdu register — that is precisely the judgement models are weakest at, and it is the thing your product is being bought for.

### 4. Voice A/B pack

Directly serves the demo prep in `RESEARCH.md`. Render the same three sentences through every candidate:

```python
VOICE_CANDIDATES = [
    ("azure_uzma",   AzureTTS,   "ur-PK-UzmaNeural"),
    ("azure_asad",   AzureTTS,   "ur-PK-AsadNeural"),
    ("inworld_ur",   InworldTTS, "<voice-id>"),
    ("hindi_translit", CartesiaTTS, "<hindi-voice>"),   # via Devanagari transliteration
]

SENTENCES = [
    "السلام علیکم، میں آپ کی کیا مدد کر سکتا ہوں؟",
    "آپ کا order کل deliver ہو جائے گا، میں tracking check کر دیتا ہوں۔",
    "آپ کا balance پندرہ سو روپے ہے۔",     # exercises number normalisation
]
```

Output to `eval/out/voices/<candidate>/<n>.wav` plus an HTML page with all candidates side by side and blinded labels.

The third sentence is deliberate: it is the number-readback case, which is where Urdu TTS most often falls over. A voice that handles the greeting beautifully and mangles the balance is not usable.

### 5. Report

`make eval` writes `eval/out/report.md`:

```markdown
## LLM candidates
| Model | TTFT p50 | TTFT p95 | Gate | Register | Code-switch | Brevity | Cost/min PKR |
|---|---|---|---|---|---|---|---|
| claude-opus-5   | 268 | 341 | ⚠ p95 over | 4.6 | 4.7 | 4.2 | 11.42 |
| claude-sonnet-5 | 201 | 262 | ✅ pass    | 4.3 | 4.4 | 4.4 |  6.69 |
| claude-haiku-4-5| 142 | 189 | ✅ pass    | 3.4 | 3.6 | 4.5 |  2.23 |

## Recommendation
<one paragraph, with the tradeoff named explicitly>
```

Report cost in PKR alongside quality. The decision is a tradeoff and the table should make it visible rather than burying it.

### 6. Run it before every demo

Model behaviour and vendor performance both drift. Re-run before each client demo and on any model change; commit the report so you have a history.

---

## Acceptance criteria

- [ ] 25+ cases covering faq, code-switch, numbers, clarification, refusal, tools
- [ ] `make eval` runs end to end and writes a report
- [ ] TTFT p50/p95/p99 reported per model, from the target region
- [ ] p95 > 300ms marked as a **gate failure**, independent of quality
- [ ] Automated hard checks run before human scoring
- [ ] Blind CSV emitted with model names stripped and rows shuffled
- [ ] Voice A/B renders all candidates on the same three sentences
- [ ] Number-readback sentence included in the voice pack
- [ ] Cost per minute in PKR alongside quality scores
- [ ] Report committed to `eval/out/` with a date
- [ ] Native speaker has scored at least one full round

---

## Unit tests

The harness itself needs tests — a broken harness produces confident wrong decisions, which is worse than no harness.

`tests/unit/test_eval_scoring.py`:

| Test | Asserts |
|---|---|
| `test_urdu_ratio_detects_english_drift` | All-English response flagged |
| `test_urdu_ratio_accepts_codeswitch` | Healthy mix **not** flagged |
| `test_preserved_english_check` | Missing term detected |
| `test_ascii_digit_check` | Catches normalisation escapes |
| `test_length_check` | Rambling response flagged |
| `test_hard_checks_compose` | Multiple failures all reported |

```python
def test_urdu_ratio_accepts_codeswitch() -> None:
    """The check must not punish the register we're deliberately building."""
    good = "آپ کا order کل deliver ہو جائے گا، میں tracking check کر دیتا ہوں۔"
    assert hard_checks(Case(must_stay_urdu=True), good) == []


def test_urdu_ratio_detects_english_drift() -> None:
    bad = "Your order will be delivered tomorrow. Let me check the tracking for you."
    assert "drifted out of Urdu" in hard_checks(Case(must_stay_urdu=True), bad)
```

`tests/unit/test_eval_stats.py`:

| Test | Asserts |
|---|---|
| `test_percentile_correct` | p95 matches known input |
| `test_gate_fails_on_p95_over_budget` | Gate logic, not just p50 |
| `test_gate_passes_within_budget` | No false gate |
| `test_insufficient_samples_flagged` | < 20 samples → warning, not a number |

```python
def test_gate_fails_on_p95_over_budget() -> None:
    """DeepSeek V4 Flash at ~1100ms must be rejected before quality is even scored."""
    report = LatencyReport(model="hypothetical", p50=280, p95=1100, p99=1400)
    assert report.gate_status(budget_ms=300) == "fail"
```

`tests/unit/test_blind_export.py`:

| Test | Asserts |
|---|---|
| `test_model_names_stripped` | No model string in CSV |
| `test_rows_shuffled` | Order not grouped by model |
| `test_mapping_written_separately` | Un-blinding possible after scoring |

---

## Common pitfalls

- **Only measuring p50.** A good median with a bad tail is a bad agent.
- **Benchmarking from the wrong region.** Measures your ISP, not production.
- **Back-to-back requests.** Rate limiting inflates the tail and you draw the wrong conclusion.
- **Non-blind quality scoring.** Brand bias is real and large.
- **An LLM judge as the final word on Urdu register.** That judgement is exactly what you are buying a human speaker for.
- **Only easy cases.** Every model passes `simple_status`; the decision lives in the hard ones.
- **Running it once.** Vendors and models drift; re-run before every demo.
- **Omitting the number-readback sentence from the voice pack.** It is where Urdu TTS most often fails.
