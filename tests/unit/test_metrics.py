"""TASK-11: metrics, cost and alerts."""

from __future__ import annotations

import time
from contextlib import suppress

import pytest

from agent.telemetry.alerts import evaluate_alerts
from agent.telemetry.cost import Rates, call_cost_pkr, turn_cost_pkr
from agent.telemetry.metrics import TurnMetrics, percentile


def _turn(**kw: object) -> TurnMetrics:
    m = TurnMetrics(turn_id="t", call_id="c")
    for key, value in kw.items():
        setattr(m, key, value)
    return m


# ── timing ──────────────────────────────────────────────────────────────────


def test_first_audio_measured_from_speech_end() -> None:
    """Anchoring on transcript-final hides the endpointing slice - the largest
    tunable one - and flatters the number by ~180ms."""
    m = TurnMetrics(turn_id="t", call_id="c")
    m.mark_speech_end()
    time.sleep(0.02)
    m.mark_transcript_final()
    time.sleep(0.01)
    m.mark_first_audio()

    assert m.first_audio_ms is not None
    assert m.endpoint_wait_ms is not None
    assert m.first_audio_ms > m.endpoint_wait_ms


def test_incomplete_turn_reports_none_not_zero() -> None:
    """A partial turn must not contribute a bogus 0ms to the percentiles."""
    m = TurnMetrics(turn_id="t", call_id="c")
    assert m.first_audio_ms is None
    m.mark_speech_end()
    assert m.first_audio_ms is None


def test_marks_are_idempotent() -> None:
    """Several sentences arrive; only the first counts as first audio."""
    m = TurnMetrics(turn_id="t", call_id="c")
    m.mark_first_audio()
    first = m.first_audio_at
    time.sleep(0.01)
    m.mark_first_audio()
    assert m.first_audio_at == first


def test_stage_records_duration() -> None:
    m = TurnMetrics(turn_id="t", call_id="c")
    with m.stage("llm"):
        time.sleep(0.01)
    assert m.stages["llm"] >= 9


def test_stage_records_even_when_it_raises() -> None:
    """A failing stage is exactly when its duration matters most."""
    m = TurnMetrics(turn_id="t", call_id="c")
    with suppress(ValueError), m.stage("llm"):
        time.sleep(0.01)
        raise ValueError("provider blew up")
    assert "llm" in m.stages


def test_uses_perf_counter_not_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wall clock can step backwards on NTP correction, producing negative
    durations that poison every percentile."""

    def explode() -> float:
        raise AssertionError("time.time() must not be used for measurement")

    monkeypatch.setattr(time, "time", explode)
    m = TurnMetrics(turn_id="t", call_id="c")
    m.mark_speech_end()
    with m.stage("x"):
        pass
    m.mark_first_audio()
    assert m.first_audio_ms is not None


def test_metrics_overhead_is_negligible() -> None:
    """Instrumentation must not become the thing being measured."""
    m = TurnMetrics(turn_id="t", call_id="c")
    start = time.perf_counter()
    for i in range(1000):
        with m.stage(f"s{i % 5}"):
            pass
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 50


def test_log_line_carries_provider_attribution() -> None:
    """Latency must be sliceable by which vendor served the turn - necessary
    when A/B-ing providers or after a mid-call TTS failover."""
    m = _turn(model="claude-opus-5", tts_provider="azure")
    log = m.as_log()
    assert log["model"] == "claude-opus-5"
    assert log["tts_provider"] == "azure"
    assert log["event"] == "turn_complete"


def test_log_line_is_json_safe() -> None:
    import json

    json.dumps(_turn(model="m").as_log())


# ── percentiles ──────────────────────────────────────────────────────────────


def test_percentile_matches_known_values() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 100) == 100


def test_percentile_single_value() -> None:
    assert percentile([42.0], 95) == 42


def test_percentile_rejects_empty() -> None:
    with pytest.raises(ValueError):
        percentile([], 95)


# ── cost ─────────────────────────────────────────────────────────────────────


def test_cost_uses_the_landed_rate_not_mid_market() -> None:
    """A dashboard that under-reports the bill is worse than none."""
    assert Rates().pkr_per_usd >= 290


def test_cache_reads_billed_at_a_tenth_of_input() -> None:
    r = Rates()
    assert r.llm_cache_read_per_mtok == pytest.approx(r.llm_input_per_mtok * 0.1)


def test_caching_reduces_cost_substantially() -> None:
    """The whole reason cache correctness is watched."""
    uncached = turn_cost_pkr(_turn(input_tokens=10_000, output_tokens=100))
    cached = turn_cost_pkr(_turn(input_tokens=1_000, cache_read_tokens=9_000, output_tokens=100))
    assert cached < uncached * 0.5


def test_zero_usage_is_zero_cost() -> None:
    assert turn_cost_pkr(_turn()) == 0.0


def test_call_cost_breaks_down_by_layer() -> None:
    turns = [_turn(input_tokens=500, output_tokens=80, tts_characters=450) for _ in range(4)]
    cost = call_cost_pkr(turns, duration_seconds=120)
    for part in (cost.llm_pkr, cost.stt_pkr, cost.tts_pkr, cost.media_pkr, cost.carrier_pkr):
        assert part > 0
    assert cost.total_pkr == pytest.approx(
        cost.llm_pkr + cost.stt_pkr + cost.tts_pkr + cost.media_pkr + cost.carrier_pkr
    )


def test_a_verbose_agent_costs_more_per_call() -> None:
    """Per-minute cost hides this; per-call is the number that matters."""
    brisk = call_cost_pkr([_turn(tts_characters=450)] * 2, duration_seconds=120)
    rambling = call_cost_pkr([_turn(tts_characters=450)] * 6, duration_seconds=360)
    assert rambling.total_pkr > brisk.total_pkr * 2


def test_per_minute_cost_is_in_the_expected_range() -> None:
    """Sanity against RESEARCH.md: roughly 17-22 PKR/min all-in on Opus 5."""
    turns = [
        _turn(cache_read_tokens=4_000, input_tokens=600, output_tokens=80, tts_characters=450)
    ] * 4
    per_minute = call_cost_pkr(turns, duration_seconds=60).total_pkr
    assert 8 < per_minute < 40, f"{per_minute:.1f} PKR/min is outside the modelled range"


# ── alerts ────────────────────────────────────────────────────────────────────


def test_sustained_zero_cache_reads_is_critical() -> None:
    """The only failure here that costs money silently."""
    alerts = evaluate_alerts([_turn(cache_read_tokens=0) for _ in range(25)])
    assert any(a.name == "cache_not_engaging" and a.severity == "critical" for a in alerts)


def test_healthy_cache_raises_nothing() -> None:
    alerts = evaluate_alerts([_turn(cache_read_tokens=4_000) for _ in range(25)])
    assert not any(a.name == "cache_not_engaging" for a in alerts)


def test_small_sample_does_not_alarm() -> None:
    """Turn one legitimately has no cache reads - it writes the cache."""
    assert evaluate_alerts([_turn(cache_read_tokens=0) for _ in range(3)]) == []


def test_latency_alerts_escalate() -> None:
    def window(ms: float) -> list[TurnMetrics]:
        turns = []
        for _ in range(25):
            m = TurnMetrics(turn_id="t", call_id="c", cache_read_tokens=1)
            m.speech_end_at = 0.0
            m.first_audio_at = ms / 1000
            turns.append(m)
        return turns

    assert not any(a.name.startswith("latency") for a in evaluate_alerts(window(600)))
    assert any(a.name == "latency_degraded" for a in evaluate_alerts(window(1400)))
    assert any(a.name == "latency_critical" for a in evaluate_alerts(window(2500)))


def test_high_interruption_rate_warns() -> None:
    turns = [_turn(cache_read_tokens=1, interrupted=i % 2 == 0) for i in range(25)]
    assert any(a.name == "interruption_rate" for a in evaluate_alerts(turns))


def test_error_rate_warns() -> None:
    turns = [_turn(cache_read_tokens=1, errors=["boom"] if i < 5 else []) for i in range(25)]
    assert any(a.name == "error_rate" for a in evaluate_alerts(turns))


def test_critical_alerts_sort_first() -> None:
    turns = [_turn(cache_read_tokens=0, interrupted=True) for _ in range(25)]
    alerts = evaluate_alerts(turns)
    assert alerts[0].severity == "critical"


def test_no_turns_no_alerts() -> None:
    assert evaluate_alerts([]) == []
