"""Scoring logic for the model evaluation (TASK-12).

Lives in ``src`` rather than ``eval/`` so it is type-checked and unit-tested
like everything else. A broken harness produces confident wrong decisions,
which is worse than having no harness at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agent.language.script import urdu_ratio
from agent.telemetry.metrics import percentile

__all__ = ["Case", "LatencyReport", "hard_checks"]

# Below this share of Urdu letters, the reply has drifted out of the language -
# one of the two failure modes the code-switching rules warn against.
_MIN_URDU_RATIO = 0.35

# A spoken turn is one or two sentences. Beyond this the caller interrupts.
_MAX_REPLY_CHARS = 400


@dataclass(frozen=True)
class Case:
    id: str = "case"
    utterance: str = ""
    category: str = "faq"
    must_stay_urdu: bool = False
    must_preserve_english: list[str] = field(default_factory=list)
    expect_tool: str | None = None
    expect_question: bool = False
    note: str = ""


def hard_checks(case: Case, response: str, *, tools_called: list[str] | None = None) -> list[str]:
    """Objective failures, checked before any human or model judgement.

    Cheap to run and unambiguous. Anything subjective — whether the register
    sounds like a real Karachi agent — is deliberately left to a person.
    """
    failures: list[str] = []
    tools_called = tools_called or []

    if not response.strip():
        return ["empty response"]

    if case.must_stay_urdu and urdu_ratio(response) < _MIN_URDU_RATIO:
        failures.append(f"drifted out of Urdu (ratio {urdu_ratio(response):.0%})")

    for term in case.must_preserve_english:
        if term.casefold() not in response.casefold():
            failures.append(f"lost English term: {term}")

    if case.expect_tool and case.expect_tool not in tools_called:
        failures.append(f"did not call {case.expect_tool}")

    if case.expect_question and not any(mark in response for mark in ("؟", "?")):
        failures.append("expected a clarifying question")

    if len(response) > _MAX_REPLY_CHARS:
        failures.append(f"too long for a spoken turn ({len(response)} chars)")

    return failures


@dataclass(frozen=True)
class LatencyReport:
    model: str
    samples: list[float]

    @property
    def p50(self) -> float:
        return percentile(self.samples, 50)

    @property
    def p95(self) -> float:
        return percentile(self.samples, 95)

    @property
    def p99(self) -> float:
        return percentile(self.samples, 99)

    def gate_status(self, *, budget_ms: int) -> Literal["pass", "fail", "insufficient"]:
        """Whether the model is usable at all.

        Gated on **p95, not p50**. A model with a 250ms median and a 900ms p95
        fails one call in twenty, which is a bad agent however good the median
        looks. This is the gate that disqualified DeepSeek V4 Flash at ~1.10s
        before any quality scoring was worth doing.
        """
        if len(self.samples) < 20:
            return "insufficient"
        return "pass" if self.p95 <= budget_ms else "fail"
