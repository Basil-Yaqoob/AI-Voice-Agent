"""TASK-12: the harness itself.

A broken harness produces confident wrong decisions, which is worse than no
harness at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.eval_harness import Case, LatencyReport, hard_checks

GOOD_URDU = "آپ کا آرڈر کل تک پہنچ جائے گا، میں تفصیل بھیج دیتا ہوں۔"
ENGLISH = "Your order will be delivered tomorrow. I will send you the details."
CODESWITCHED = "آپ کا order کل deliver ہو جائے گا، میں tracking check کر دیتا ہوں۔"


# ── language drift ───────────────────────────────────────────────────────────


def test_urdu_reply_passes() -> None:
    assert hard_checks(Case(must_stay_urdu=True), GOOD_URDU) == []


def test_english_drift_detected() -> None:
    """One of the two failure modes the code-switching rules warn against."""
    failures = hard_checks(Case(must_stay_urdu=True), ENGLISH)
    assert any("drifted out of Urdu" in f for f in failures)


def test_codeswitching_is_not_penalised() -> None:
    """The check must not punish the register we deliberately built."""
    assert hard_checks(Case(must_stay_urdu=True), CODESWITCHED) == []


def test_english_allowed_when_not_required() -> None:
    assert hard_checks(Case(must_stay_urdu=False), ENGLISH) == []


# ── preserved terms ───────────────────────────────────────────────────────────


def test_missing_english_term_detected() -> None:
    case = Case(must_stay_urdu=True, must_preserve_english=["tracking"])
    assert any("tracking" in f for f in hard_checks(case, GOOD_URDU))


def test_preserved_term_match_is_case_insensitive() -> None:
    case = Case(must_preserve_english=["premium"])
    assert hard_checks(case, "آپ کا Premium Plan فعال ہے۔") == []


# ── tools and questions ────────────────────────────────────────────────────────


def test_missing_tool_call_detected() -> None:
    case = Case(expect_tool="get_order_status")
    assert any("get_order_status" in f for f in hard_checks(case, GOOD_URDU, tools_called=[]))


def test_tool_call_satisfied() -> None:
    case = Case(expect_tool="get_order_status")
    assert hard_checks(case, GOOD_URDU, tools_called=["get_order_status"]) == []


def test_missing_clarifying_question_detected() -> None:
    case = Case(expect_question=True)
    assert any("clarifying question" in f for f in hard_checks(case, GOOD_URDU))


def test_urdu_question_mark_counts() -> None:
    """؟ is U+061F, not '?'."""
    assert hard_checks(Case(expect_question=True), "کیا آپ order نمبر بتا سکتے ہیں؟") == []


# ── length and emptiness ───────────────────────────────────────────────────────


def test_long_reply_flagged() -> None:
    assert any("too long" in f for f in hard_checks(Case(), "آپ کا آرڈر " * 100))


def test_empty_reply_is_a_single_clear_failure() -> None:
    assert hard_checks(Case(must_stay_urdu=True), "   ") == ["empty response"]


def test_failures_accumulate() -> None:
    case = Case(must_stay_urdu=True, must_preserve_english=["tracking"], expect_question=True)
    assert len(hard_checks(case, ENGLISH)) >= 3


# ── the latency gate ────────────────────────────────────────────────────────────


def test_gate_uses_p95_not_p50() -> None:
    """DeepSeek V4 Flash at ~1100ms must be rejected before quality is scored."""
    samples = [280.0] * 19 + [1100.0] * 6
    report = LatencyReport("hypothetical", samples)
    assert report.p50 < 300
    assert report.gate_status(budget_ms=300) == "fail"


def test_gate_passes_within_budget() -> None:
    assert LatencyReport("m", [200.0] * 25).gate_status(budget_ms=300) == "pass"


def test_small_sample_is_not_a_verdict() -> None:
    """Five samples is not evidence; saying so beats a confident wrong answer."""
    assert LatencyReport("m", [200.0] * 5).gate_status(budget_ms=300) == "insufficient"


def test_percentiles_are_ordered() -> None:
    report = LatencyReport("m", [float(v) for v in range(1, 101)])
    assert report.p50 <= report.p95 <= report.p99


# ── the case file ────────────────────────────────────────────────────────────────


def _cases(project_root: Path) -> dict:
    return yaml.safe_load((project_root / "eval" / "cases.yaml").read_text(encoding="utf-8"))


def test_case_file_parses_into_cases(project_root: Path) -> None:
    cases = [Case(**c) for c in _cases(project_root)["cases"]]
    assert len(cases) >= 25


def test_case_ids_unique(project_root: Path) -> None:
    ids = [c["id"] for c in _cases(project_root)["cases"]]
    assert len(ids) == len(set(ids))


def test_hard_categories_are_well_represented(project_root: Path) -> None:
    """Every model passes the easy cases; the decision lives in the hard ones."""
    categories = [c["category"] for c in _cases(project_root)["cases"]]
    for required in ("codeswitch", "numbers", "clarification", "refusal", "tool", "register"):
        assert categories.count(required) >= 2, f"only {categories.count(required)} {required}"

    easy = categories.count("faq")
    assert easy < len(categories) / 3, "suite is weighted toward easy cases"


def test_corpus_and_persona_present(project_root: Path) -> None:
    data = _cases(project_root)
    assert data["corpus"].strip()
    assert data["persona"].strip()


def test_cases_referencing_tools_name_them(project_root: Path) -> None:
    for case in _cases(project_root)["cases"]:
        if case["category"] == "tool":
            assert case.get("expect_tool"), f"{case['id']} is a tool case with no expect_tool"


@pytest.mark.parametrize("field_name", ["utterance", "category"])
def test_every_case_has_required_fields(project_root: Path, field_name: str) -> None:
    for case in _cases(project_root)["cases"]:
        assert case.get(field_name), f"{case['id']} missing {field_name}"
