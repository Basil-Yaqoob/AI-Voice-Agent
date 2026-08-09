# TASK-11 — Latency metrics and observability

| | |
|---|---|
| **Depends on** | TASK-08 |
| **Blocks** | TASK-12, TASK-13 |
| **Estimate** | 1 day |
| **Level** | Intermediate |

---

## Objective

Measure every stage of every turn, so that when latency regresses you can attribute it to a component in one pass instead of bisecting the pipeline.

This is not optional polish. From `00-ARCHITECTURE.md`: *you cannot fix latency you are not measuring, and it will regress.* A voice agent has five sequential stages, each owned by a different vendor; without per-stage attribution, a 200ms regression is an afternoon of guessing.

The second reason this task exists is cost. The research flags a specific silent failure: **if prompt caching stops working, the model bill triples with no visible symptom.** The only way you find out is by watching `cache_read_tokens`.

---

## Steps

### 1. Per-turn metrics object

`src/agent/telemetry/metrics.py`:

```python
@dataclass
class TurnMetrics:
    turn_id: str
    call_id: str

    # absolute marks (perf_counter)
    speech_end_at: float | None = None
    transcript_final_at: float | None = None
    first_token_at: float | None = None
    first_audio_at: float | None = None

    # stage durations (ms)
    stages: dict[str, float] = field(default_factory=dict)

    # cost signals
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tts_characters: int = 0

    # behaviour
    interrupted: bool = False
    cache_hit: bool = False
    prefetch_used: bool = False
    retrieval_calls: int = 0
    tool_calls: int = 0

    @property
    def first_audio_ms(self) -> float | None:
        """The headline number: end of caller speech → first syllable out."""
        if self.speech_end_at is None or self.first_audio_at is None:
            return None
        return (self.first_audio_at - self.speech_end_at) * 1000

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = (time.perf_counter() - start) * 1000
```

Use `time.perf_counter()`, never `time.time()`. Wall-clock can step backwards on NTP adjustment and produce negative durations that quietly poison your percentiles.

### 2. What to record, and why each one

| Metric | Type | Why it matters |
|---|---|---|
| `first_audio_ms` | histogram | **The headline.** Everything else explains this one. |
| `stage_ms{stage}` | histogram | Attribution — which vendor regressed |
| `llm_ttft_ms` | histogram | Largest single budget slice |
| `tts_ttfb_ms` | histogram | Second largest |
| `endpoint_wait_ms` | histogram | The tunable slice (TASK-09) |
| `cache_read_tokens` | counter | **Cost alarm** — sustained zero means caching broke |
| `cache_hit_ratio` | gauge | Derived; should be high after turn one |
| `retrieval_calls_per_turn` | gauge | Prefetch cost control — expect > 1 |
| `interruptions_total` | counter | Rising rate suggests the agent is too verbose |
| `tts_characters` | counter | Drives the TTS bill; watch for a rambling agent |
| `turn_errors_total{stage}` | counter | Reliability by component |

**Percentiles, not averages.** A p50 of 600ms with a p95 of 2.4s is a bad agent that looks fine on a mean. Record p50, p95 and p99; alert on p95.

### 3. Structured per-turn log

One JSON line per turn, with `call_id` bound via `structlog.contextvars` at session start so every line in that call carries it automatically:

```json
{
  "event": "turn_complete",
  "call_id": "call_a3f9",
  "turn_id": "turn_04",
  "first_audio_ms": 612,
  "stages": {"stt_final": 94, "retrieval": 8, "llm_ttft": 287, "tts_ttfb": 108},
  "cache_read_tokens": 4180,
  "cache_hit": false,
  "prefetch_used": true,
  "interrupted": false,
  "model": "claude-opus-5",
  "tts_provider": "azure"
}
```

Include `model` and `tts_provider` on every line. When you A/B providers (TASK-12) or fail over mid-call (TASK-05), you need to slice latency by which one actually served the turn.

### 4. Cost tracking in PKR

The research prices everything in rupees; the telemetry should too, so the number in your dashboard is the number in your budget.

```python
def turn_cost_pkr(m: TurnMetrics, rates: Rates) -> float:
    llm = (
        m.cache_read_tokens  * rates.llm_cache_read_per_token
        + m.input_tokens     * rates.llm_input_per_token
        + m.output_tokens    * rates.llm_output_per_token
    )
    tts = m.tts_characters * rates.tts_per_char
    stt = (m.duration_s / 60) * rates.stt_per_min
    return (llm + tts + stt) * rates.usd_to_pkr
```

Set `usd_to_pkr` to the **landed** rate (~295), not mid-market (~278.6) — bank FX markup plus advance tax on foreign card payments is real money at these volumes, and a dashboard that under-reports is worse than none.

**Track cost per *resolved call*, not per minute.** The research is explicit about this: a verbose agent that takes six minutes to do a two-minute job is a cost problem no vendor discount fixes, and per-minute cost hides it completely.

### 5. Alerts worth having

| Condition | Severity | Meaning |
|---|---|---|
| `cache_read_tokens == 0` over 20 turns | **critical** | Caching broke; bill is ~3× |
| p95 `first_audio_ms` > 1200ms for 5 min | warning | Degrading |
| p95 `first_audio_ms` > 2000ms for 2 min | critical | Callers are hanging up |
| `turn_errors_total` rate > 2% | warning | Vendor trouble |
| `interruptions_total` rate > 25% | warning | Agent too verbose, or endpointing is cutting people off |
| `tts_failover_total` > 0 | warning | Primary TTS unhealthy |

The cache alarm is first for a reason: it is the only one that costs money silently rather than visibly degrading the product.

### 6. Prometheus endpoint

Expose `/metrics` on the FastAPI app. Keep it on the control plane, not the media worker path.

---

## Acceptance criteria

- [ ] Every turn produces a complete `TurnMetrics`
- [ ] `first_audio_ms` measured from end-of-speech, not from transcript-final
- [ ] All five stages timed independently
- [ ] `perf_counter` used throughout — no wall-clock arithmetic
- [ ] `call_id` bound once and present on every log line for that call
- [ ] Token usage recorded including cache read/write
- [ ] Per-turn PKR cost computed at the landed FX rate
- [ ] Cost per resolved call available, not just per minute
- [ ] `/metrics` exposes histograms with p50/p95/p99
- [ ] Cache-zero alarm fires in a test that disables caching
- [ ] Metrics collection adds **< 1ms** per turn
- [ ] A metrics failure never affects the call

---

## Unit tests

`tests/unit/test_metrics.py`:

| Test | Asserts |
|---|---|
| `test_first_audio_measured_from_speech_end` | Correct anchor point |
| `test_stage_context_manager_records` | Duration captured |
| `test_stage_records_on_exception` | `finally` semantics hold |
| `test_incomplete_turn_returns_none` | No bogus numbers from partial turns |
| `test_uses_perf_counter` | No wall-clock dependency |
| `test_overhead_under_1ms` | Instrumentation is not the bottleneck |

```python
def test_stage_records_on_exception() -> None:
    """A failing stage still needs its timing — that's when you most want it."""
    m = TurnMetrics(turn_id="t1", call_id="c1")
    with suppress(ValueError), m.stage("llm"):
        time.sleep(0.01)
        raise ValueError("provider blew up")
    assert m.stages["llm"] >= 10


def test_uses_perf_counter(monkeypatch) -> None:
    """Wall-clock can step backwards on NTP sync and poison percentiles."""
    monkeypatch.setattr(time, "time", lambda: (_ for _ in ()).throw(
        AssertionError("time.time() must not be used")))
    m = TurnMetrics(turn_id="t1", call_id="c1")
    with m.stage("x"):
        pass
```

`tests/unit/test_cost.py`:

| Test | Asserts |
|---|---|
| `test_cost_uses_landed_fx_rate` | 295, not 278.6 |
| `test_cache_reads_billed_at_tenth` | Discount applied |
| `test_zero_usage_is_zero_cost` | No spurious floor |
| `test_cost_per_resolved_call` | Multi-turn aggregation correct |

`tests/unit/test_alerts.py`:

```python
def test_zero_cache_reads_triggers_alarm() -> None:
    """The silent 3x cost bug. This alarm is the only thing that catches it."""
    turns = [TurnMetrics(turn_id=str(i), call_id="c1", cache_read_tokens=0)
             for i in range(20)]
    alerts = evaluate_alerts(turns)
    assert any(a.name == "cache_not_engaging" and a.severity == "critical"
               for a in alerts)


def test_cache_reads_present_no_alarm() -> None:
    turns = [TurnMetrics(turn_id=str(i), call_id="c1", cache_read_tokens=4000)
             for i in range(20)]
    assert not any(a.name == "cache_not_engaging" for a in evaluate_alerts(turns))
```

`tests/unit/test_telemetry_isolation.py`:

```python
async def test_metrics_failure_does_not_break_turn() -> None:
    """Observability must never be able to drop a call."""
    session = build_session(metrics_sink=FailingSink())
    result = await session.execute_turn("سلام", TurnMetrics("t1", "c1"))
    assert result.completed is True
```

---

## Common pitfalls

- **Measuring from transcript-final instead of speech-end.** Hides the entire endpointing slice — the largest tunable one — and flatters your numbers by ~180ms.
- **`time.time()` instead of `perf_counter`.** NTP steps produce negative durations.
- **Averages instead of percentiles.** p95 is where callers live.
- **Not watching `cache_read_tokens`.** The only silent cost bug in the system.
- **Cost per minute only.** Hides a verbose agent taking three times as long.
- **Mid-market FX in the dashboard.** Under-reports the real bill by ~6%.
- **Metrics on the media worker's critical path.** Keep aggregation and export off the audio thread.
