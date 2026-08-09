# TASK-09 — Turn detection and endpointing tuning

| | |
|---|---|
| **Depends on** | TASK-04, TASK-08 |
| **Blocks** | 13 |
| **Estimate** | 1 day (plus data collection) |
| **Level** | Advanced |

---

## Objective

Decide when the caller has finished speaking — accurately, for Urdu.

This gets a dedicated task because **endpointing is 180ms of an 800ms budget and it is the only large slice that costs nothing to improve.** No vendor swap buys as much. It is also the stage most likely to make the agent feel rude, because the failure mode is interrupting the caller mid-thought.

The Urdu-specific problem: **Urdu speakers pause mid-sentence more than English speakers do.** Default VAD thresholds are tuned on English corpora and will cut Pakistani callers off. Tuning against real Pakistani call audio is the whole job.

---

## Steps

### 1. Understand the three signals available

| Signal | Source | Latency | Nature |
|---|---|---|---|
| **VAD** | Silero, local | ~10ms | Acoustic only — is there speech energy? |
| **Deepgram `endpointing`** | provider | included in STT | Acoustic + light linguistic |
| **Semantic completeness** | separate model | 30–80ms | Is this sentence *finished*? |

VAD alone is a silence timer and cannot distinguish "thinking pause" from "done". Combining acoustic silence with a semantic check is what lets you cut the silence window without cutting callers off.

### 2. Collect real audio first — do not tune on synthetic data

Everything else in this task depends on having representative recordings. Aim for 30–50 utterances, ideally over an actual phone line rather than a laptop microphone (codec and packet loss change VAD behaviour materially).

Cover deliberately:

- Natural mid-sentence hesitation (the critical case)
- Thinking pauses before numbers — "میرا آرڈر نمبر ہے... چار چار سات ایک"
- Fast speakers with no pauses
- Callers on speakerphone, in traffic, with TV in the background
- Trailing "umm" equivalents — `اممم`, `اچھا`

Label each with the true end-of-speech timestamp. That labelled set is your evaluation harness.

### 3. Baseline measurement

Before changing anything, measure the default configuration on the labelled set and record two numbers:

- **Cut-off rate** — proportion of utterances where the agent triggered before the caller finished. This is the number that makes an agent feel rude.
- **Median added latency** — how long after true end-of-speech the turn fires.

You are trading these against each other. Write the baseline down; without it you cannot tell whether tuning helped.

### 4. Adaptive silence threshold

A single fixed threshold is wrong because different moments in a sentence carry different completion likelihood.

```python
class AdaptiveEndpointer:
    BASE_MS = 180
    MIN_MS = 120
    MAX_MS = 600

    def silence_threshold_ms(self, partial: str) -> int:
        if not partial.strip():
            return self.MAX_MS                    # nothing said yet — be patient

        # Trailing conjunction/filler => caller is mid-thought. Wait longer.
        if _ends_with_continuation(partial):
            return self.MAX_MS

        # Mid-number => almost certainly not finished.
        if _ends_mid_number(partial):
            return self.MAX_MS

        # Grammatically complete Urdu sentence => can cut early.
        if _looks_complete(partial):
            return self.MIN_MS

        return self.BASE_MS
```

The Urdu-specific rules that carry most of the value:

- **Trailing continuation words** — `اور`, `لیکن`, `کیونکہ`, `پھر`, `تو`, `کہ`. A caller who just said `اور` is unambiguously still going. Extend the window.
- **Verb-final completion.** Urdu is SOV, so a sentence ending in a conjugated verb (`ہے`, `ہیں`, `تھا`, `گا`, `چاہیے`, `کریں`) is very likely complete. Shorten the window — this is where you *win back* latency.
- **Mid-number detection.** Callers reading an order or phone number pause between digit groups. Never cut inside one.

That verb-final rule is the reason this task pays for itself: it lets you drop to 120ms on the common case while extending to 600ms only where needed, so the *average* improves while the cut-off rate falls.

### 5. Optional semantic turn detection

If rule-based tuning plateaus, add a small classifier scoring "is this utterance complete?" on each partial. LiveKit ships a turn-detector plugin for this.

Only add it if the measured cut-off rate justifies 30–80ms of extra latency. Measure before adopting — rules plus good thresholds get most teams where they need to be.

### 6. False barge-in suppression

The mirror-image problem: the caller coughs, or a TV plays, and the agent stops mid-sentence. Require a minimum ~150ms of continuous speech energy before treating VAD as a genuine interruption. Tune against the `background_noise.wav` fixture.

### 7. Lock in with a regression harness

`eval/endpointing_eval.py` — replays every labelled clip and reports cut-off rate and median latency. Run it whenever thresholds change; commit the results.

---

## Acceptance criteria

- [ ] 30+ labelled real utterances collected, ideally over a phone line
- [ ] Baseline cut-off rate and median latency recorded before tuning
- [ ] **Cut-off rate below 3%** on the labelled set
- [ ] **Median added latency at or under 180ms**
- [ ] Trailing conjunctions (`اور`, `لیکن`, `کیونکہ`) extend the window
- [ ] Verb-final complete sentences shorten it
- [ ] Mid-number pauses never trigger a turn
- [ ] `long_pause_mid.wav` does **not** cut the caller off
- [ ] `background_noise.wav` does not cause a false barge-in
- [ ] `silence.wav` produces no turn at all
- [ ] Endpointing eval is reproducible via `make eval-endpointing`
- [ ] Before/after numbers recorded in the PR

---

## Unit tests

`tests/unit/test_endpointer.py`:

| Test | Asserts |
|---|---|
| `test_empty_partial_uses_max_window` | Patient before speech |
| `test_trailing_conjunction_extends` | `اور` → MAX_MS |
| `test_complete_sentence_shortens` | Verb-final → MIN_MS |
| `test_mid_number_extends` | Never cuts inside digits |
| `test_threshold_always_within_bounds` | MIN ≤ x ≤ MAX for any input |
| `test_never_raises_on_arbitrary_text` | Hot path safety |

```python
@pytest.mark.parametrize("partial", [
    "میرا آرڈر آیا اور",
    "یہ ٹھیک ہے لیکن",
    "میں پوچھنا چاہتا تھا کہ",
])
def test_trailing_conjunction_extends(partial: str) -> None:
    """A caller who just said 'اور' is still talking. Cutting here feels rude."""
    assert AdaptiveEndpointer().silence_threshold_ms(partial) == AdaptiveEndpointer.MAX_MS


@pytest.mark.parametrize("partial", [
    "میرا آرڈر کہاں ہے",
    "مجھے refund چاہیے",
    "یہ کام ہو گیا ہے",
])
def test_complete_sentence_shortens(partial: str) -> None:
    """Urdu is SOV — a conjugated final verb signals completion. This wins latency."""
    assert AdaptiveEndpointer().silence_threshold_ms(partial) == AdaptiveEndpointer.MIN_MS


@given(st.text())
def test_threshold_always_within_bounds(s: str) -> None:
    ms = AdaptiveEndpointer().silence_threshold_ms(s)
    assert AdaptiveEndpointer.MIN_MS <= ms <= AdaptiveEndpointer.MAX_MS
```

`tests/unit/test_barge_in_suppression.py`:

| Test | Asserts |
|---|---|
| `test_short_burst_ignored` | 80ms of energy → no interrupt |
| `test_sustained_speech_interrupts` | 200ms → interrupt |
| `test_intermittent_noise_ignored` | Repeated short bursts do not accumulate |

`tests/integration/test_endpointing_fixtures.py`:

```python
@pytest.mark.parametrize("fixture,should_cut_off", [
    ("long_pause_mid.wav",  False),   # THE critical case
    ("pure_urdu_short.wav", False),
    ("fast_speaker.wav",    False),
    ("silence.wav",         False),   # no turn at all
])
async def test_endpointing_on_real_audio(fixture: str, should_cut_off: bool) -> None:
    result = await run_endpointing(fixture)
    assert result.cut_off_mid_utterance == should_cut_off


async def test_aggregate_cutoff_rate_under_threshold() -> None:
    """The headline quality metric for this task."""
    results = [await run_endpointing(c) for c in load_labelled_corpus()]
    rate = sum(r.cut_off_mid_utterance for r in results) / len(results)
    assert rate < 0.03, f"cut-off rate {rate:.1%} — callers will find this rude"


async def test_median_added_latency_within_budget(settings) -> None:
    results = [await run_endpointing(c) for c in load_labelled_corpus()]
    median = statistics.median(r.added_latency_ms for r in results)
    assert median <= settings.endpoint_silence_ms
```

---

## Common pitfalls

- **Tuning on synthetic or laptop-mic audio.** Phone codecs and packet loss change VAD behaviour; thresholds tuned on clean audio do not transfer.
- **One fixed threshold.** Either rude or slow; adaptive gets both.
- **Ignoring Urdu's verb-final structure.** This is where the latency win lives — without it you can only trade one metric for the other.
- **Cutting inside a number.** Callers reading an order number pause between groups; interrupting there forces a full restart and is deeply annoying.
- **No baseline measurement.** You cannot tell whether tuning helped, and threshold changes become superstition.
- **Adding a semantic model before exhausting rules.** Costs 30–80ms that rules often make unnecessary.
