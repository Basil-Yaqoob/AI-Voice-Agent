"""TASK-06: Urdu normalisation. Deepest coverage in the project.

This is where competing demos visibly fail — a phone number read as a
multi-crore cardinal, or a balance in the wrong grammatical form. Everything
under test is a pure function over strings, so there is no excuse for thin
coverage here.
"""

from __future__ import annotations

import timeit
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from agent.language.normalize import normalize_for_tts
from agent.language.numbers import digit_by_digit, number_to_urdu


def _has_ascii_digit(text: str) -> bool:
    return any(ch.isdigit() and ch.isascii() for ch in text)


# ── the lookup table ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "صفر"),
        (1, "ایک"),
        (9, "نو"),
        (10, "دس"),
        (19, "انیس"),
        (21, "اکیس"),
        (29, "انتیس"),
        (40, "چالیس"),
        (48, "اڑتالیس"),
        (69, "انہتر"),
        (90, "نوے"),
        (99, "ننانوے"),
    ],
)
def test_irregular_numerals_come_from_the_table(value: int, expected: str) -> None:
    """Generated Urdu numerals are subtly wrong; these must be looked up."""
    assert number_to_urdu(value) == expected


def test_every_value_zero_to_ninety_nine_is_defined() -> None:
    """A gap surfaces as a KeyError mid-call, which drops the turn."""
    for value in range(100):
        assert number_to_urdu(value)


# ── South Asian grouping ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100, "ایک سو"),
        (1_000, "ایک ہزار"),
        (100_000, "ایک لاکھ"),
        (10_000_000, "ایک کروڑ"),
    ],
)
def test_south_asian_scales(value: int, expected: str) -> None:
    assert number_to_urdu(value) == expected


def test_lakh_not_hundred_thousand() -> None:
    """Western grouping registers as foreign to a Pakistani caller."""
    assert "لاکھ" in number_to_urdu(500_000)
    assert "ہزار" not in number_to_urdu(500_000)


def test_colloquial_hundreds_form() -> None:
    """1500 in speech is 'fifteen hundred', not 'one thousand five hundred'."""
    assert number_to_urdu(1_500) == "پندرہ سو"
    assert number_to_urdu(1_900) == "انیس سو"


def test_shorthand_stops_above_the_confident_band() -> None:
    """Above 2000 the 'X سو' form is mixed usage, so take the unambiguous one."""
    assert number_to_urdu(2_500) == "دو ہزار پانچ سو"


def test_round_thousands_keep_the_thousand_form() -> None:
    assert number_to_urdu(3_000) == "تین ہزار"


def test_compound_with_remainder() -> None:
    assert number_to_urdu(250_000) == "دو لاکھ پچاس ہزار"


# ── the phone number trap ──────────────────────────────────────────────────


def test_pakistani_mobile_read_digit_by_digit() -> None:
    """A cardinal reading here produces a meaningless multi-crore figure — the
    single most jarring failure an Urdu voice agent can make."""
    out = normalize_for_tts("میرا نمبر 03001234567 ہے")
    assert "کروڑ" not in out
    assert "لاکھ" not in out
    assert out.count("صفر") >= 2  # leading 0 and the 0s in 300


def test_international_format_also_digit_by_digit() -> None:
    out = normalize_for_tts("+92 300 1234567")
    assert "کروڑ" not in out
    assert not _has_ascii_digit(out)


def test_digit_by_digit_helper() -> None:
    assert digit_by_digit("102") == "ایک صفر دو"


@pytest.mark.parametrize(
    "raw",
    ["042-35678901", "021 111 123 456", "0512345678", "+92 42 35678901"],
)
def test_landline_numbers_also_digit_by_digit(raw: str) -> None:
    """Review regression: only the mobile shape was matched, so '042-35678901'
    became a multi-crore cardinal with the leading zero dropped."""
    out = normalize_for_tts(f"ہمارا نمبر {raw} ہے")
    assert "کروڑ" not in out
    assert "لاکھ" not in out
    assert not _has_ascii_digit(out)
    assert "صفر" in out  # the leading zero survives


@pytest.mark.parametrize("code", ["ORD-4471", "ORDER-12345", "trk-99", "INV/2026"])
def test_reference_codes_spelled_out(code: str) -> None:
    """Review regression: [A-Z]{2,4} missed ORDER-12345 and lowercase codes,
    which were then read as cardinals."""
    out = normalize_for_tts(f"آپ کا کوڈ {code} ہے")
    assert not _has_ascii_digit(out)
    assert "ہزار" not in out  # not read as a number


def test_currency_with_paisa() -> None:
    """Review regression: 'Rs 1,250.50' leaked a bare ASCII '.' and read the
    paisa as a detached cardinal."""
    out = normalize_for_tts("Rs 1,250.50")
    assert "." not in out
    assert "روپے" in out
    assert "پیسے" in out


def test_currency_without_paisa_says_no_paisa() -> None:
    assert normalize_for_tts("Rs. 1,500") == "پندرہ سو روپے"


def test_currency_with_zero_paisa_omits_them() -> None:
    out = normalize_for_tts("Rs 500.00")
    assert "پیسے" not in out


# ── ordering: the rules must not shred each other ──────────────────────────


def test_date_survives_the_integer_rule() -> None:
    """Wrong ordering turns 2026-08-08 into three separate numbers."""
    out = normalize_for_tts("آپ کا آرڈر 2026-08-08 کو آئے گا")
    assert "اگست" in out
    assert "دو ہزار چھبیس" in out


def test_time_survives_the_integer_rule() -> None:
    out = normalize_for_tts("14:30 بجے")
    assert "ڈھائی" in out


def test_currency_consumed_before_decimal() -> None:
    """'Rs. 1,500' contains a '.' that is not a decimal point."""
    out = normalize_for_tts("Rs. 1,500")
    assert out == "پندرہ سو روپے"


def test_reference_code_spelled_out() -> None:
    out = normalize_for_tts("ORD-4471")
    assert not _has_ascii_digit(out)
    assert "او" in out  # letter O


# ── times ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "must_contain"),
    [
        ("09:00", "صبح"),
        ("14:30", "دوپہر"),
        ("17:00", "شام"),
        ("22:00", "رات"),
    ],
)
def test_time_period_words(raw: str, must_contain: str) -> None:
    assert must_contain in normalize_for_tts(raw)


def test_half_past_two_is_dhai() -> None:
    assert "ڈھائی" in normalize_for_tts("14:30")


def test_half_past_one_is_derh() -> None:
    assert "ڈیڑھ" in normalize_for_tts("13:30")


def test_quarter_past_and_to() -> None:
    assert "سوا" in normalize_for_tts("13:15")
    assert "پونے" in normalize_for_tts("20:45")


# ── idiomatic halves ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.5", "ڈیڑھ"), ("2.5", "ڈھائی"), ("3.5", "ساڑھے تین")],
)
def test_idiomatic_halves(raw: str, expected: str) -> None:
    """'تین اعشاریہ پانچ' is correct and sounds like a calculator."""
    assert normalize_for_tts(raw) == expected


# ── preserving deliberate English ───────────────────────────────────────────


def test_english_preserved_in_latin_script() -> None:
    assert "order" in normalize_for_tts("آپ کا order کل آئے گا")


def test_multiword_english_preserved() -> None:
    out = normalize_for_tts("آپ کا Premium Plan فعال ہے")
    assert "Premium" in out and "Plan" in out


def test_english_and_number_together() -> None:
    out = normalize_for_tts("آپ کا balance 2500 روپے ہے")
    assert "balance" in out
    assert not _has_ascii_digit(out)


def test_unlisted_english_is_left_alone() -> None:
    """The normaliser converts numbers; it is not a translator."""
    assert "Wednesday" in normalize_for_tts("Wednesday کو")


# ── markdown ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    ["**زور** دیں", "*زور* دیں", "`code` دیں", "- زور دیں", "1. زور دیں", "## زور دیں"],
)
def test_markdown_stripped(raw: str) -> None:
    """TTS will happily read 'asterisk asterisk' aloud."""
    out = normalize_for_tts(raw)
    for artefact in ("**", "`", "##"):
        assert artefact not in out


# ── totality and performance ────────────────────────────────────────────────


@given(st.integers(min_value=0, max_value=10_000_000))
def test_no_ascii_digits_survive(value: int) -> None:
    assert not _has_ascii_digit(normalize_for_tts(str(value)))


@given(st.integers(min_value=0, max_value=10_000_000))
def test_output_nonempty_and_stripped(value: int) -> None:
    out = normalize_for_tts(str(value))
    assert out and out == out.strip()


@given(st.text())
def test_never_raises_on_arbitrary_input(text: str) -> None:
    """The model can emit anything, and this runs on the hot path. A crash here
    drops the call."""
    normalize_for_tts(text)


def test_empty_input() -> None:
    assert normalize_for_tts("") == ""


def test_is_pure_no_hidden_state() -> None:
    text = "آپ کا order 1500 روپے"
    assert normalize_for_tts(text) == normalize_for_tts(text)


def test_normalization_is_fast() -> None:
    """Runs once per sentence chunk on the hot path."""
    text = "آپ کا order ORD-4471 کل 2026-08-08 کو Rs. 2,500 میں deliver ہوگا" * 3
    seconds = timeit.timeit(lambda: normalize_for_tts(text), number=200) / 200
    assert seconds < 0.001, f"{seconds * 1000:.2f}ms per call is too slow"


# ── golden file ──────────────────────────────────────────────────────────────


def _golden(project_root: Path) -> dict[str, object]:
    path = project_root / "tests" / "fixtures" / "golden" / "urdu_normalization.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_against_golden_file(project_root: Path) -> None:
    """Locks in the reviewed output. Changing it requires re-review."""
    cases = _golden(project_root)["cases"]
    failures = [
        f"{c['input']!r}: expected {c['expected']!r}, got {normalize_for_tts(c['input'])!r}"
        for c in cases  # type: ignore[union-attr]
        if normalize_for_tts(c["input"]) != c["expected"]
    ]
    assert not failures, "\n".join(failures)


def test_golden_file_review_status_is_visible(project_root: Path) -> None:
    """Not a failure — a standing reminder in the test output.

    Automated tests prove stability, not correctness. Until a native speaker has
    signed off, every expectation in that file is a non-speaker's guess.
    """
    data = _golden(project_root)
    if not data.get("reviewed_by"):
        pytest.skip("golden file awaiting native-speaker review (expected for now)")
