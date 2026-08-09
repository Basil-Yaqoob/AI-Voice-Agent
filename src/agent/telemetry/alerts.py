"""Alerts worth waking up for.

The cache alarm comes first deliberately. Every other condition here degrades
the product visibly — callers hear it, someone complains. A broken prompt cache
costs roughly three times the model bill and produces no symptom at all until
the invoice arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent.telemetry.metrics import TurnMetrics, percentile

__all__ = ["Alert", "evaluate_alerts"]

Severity = Literal["warning", "critical"]

_MIN_SAMPLE = 20


@dataclass(frozen=True)
class Alert:
    name: str
    severity: Severity
    detail: str


def evaluate_alerts(
    turns: list[TurnMetrics],
    *,
    budget_total_ms: int = 800,
) -> list[Alert]:
    """Evaluate a recent window of turns. Most severe first."""
    alerts: list[Alert] = []
    if not turns:
        return alerts

    alerts.extend(_cache_alerts(turns))
    alerts.extend(_latency_alerts(turns, budget_total_ms))
    alerts.extend(_behaviour_alerts(turns))

    order = {"critical": 0, "warning": 1}
    return sorted(alerts, key=lambda a: order[a.severity])


def _cache_alerts(turns: list[TurnMetrics]) -> list[Alert]:
    if len(turns) < _MIN_SAMPLE:
        return []

    # Turn one of a session legitimately has no cache reads — it writes the
    # cache. Sustained zeros across a window mean the prefix is drifting.
    if all(t.cache_read_tokens == 0 for t in turns):
        return [
            Alert(
                "cache_not_engaging",
                "critical",
                f"no cache reads across {len(turns)} turns; the model bill is "
                "roughly 3x and nothing else will tell you. Check the system "
                "prompt prefix for a timestamp, session id or reordered tools.",
            )
        ]
    return []


def _latency_alerts(turns: list[TurnMetrics], budget_ms: int) -> list[Alert]:
    samples = [t.first_audio_ms for t in turns if t.first_audio_ms is not None]
    if len(samples) < _MIN_SAMPLE:
        return []

    p95 = percentile(samples, 95)
    if p95 > budget_ms * 2.5:
        return [
            Alert("latency_critical", "critical", f"p95 first-audio {p95:.0f}ms; callers hang up")
        ]
    if p95 > budget_ms * 1.5:
        return [
            Alert("latency_degraded", "warning", f"p95 first-audio {p95:.0f}ms over {budget_ms}ms")
        ]
    return []


def _behaviour_alerts(turns: list[TurnMetrics]) -> list[Alert]:
    alerts: list[Alert] = []
    total = len(turns)
    if total < _MIN_SAMPLE:
        return alerts

    errors = sum(1 for t in turns if t.errors)
    if errors / total > 0.02:
        alerts.append(Alert("error_rate", "warning", f"{errors / total:.0%} of turns errored"))

    interrupts = sum(1 for t in turns if t.interrupted)
    if interrupts / total > 0.25:
        # Either the agent is too verbose, or endpointing is cutting callers
        # off and they are repeating themselves. Both are worth investigating.
        alerts.append(
            Alert(
                "interruption_rate",
                "warning",
                f"{interrupts / total:.0%} of turns interrupted; agent may be too "
                "verbose, or endpointing may be cutting callers off",
            )
        )

    return alerts
