# TASK-06 — Urdu text normalisation for speech

| | |
|---|---|
| **Depends on** | TASK-01 |
| **Blocks** | 08 |
| **Estimate** | 1.5 days |
| **Level** | Intermediate (needs a native Urdu speaker for review) |

---

## Objective

Convert anything the model writes into text that Urdu TTS pronounces correctly — digits, currency, dates, times, phone numbers, order IDs, percentages — while leaving intentional English words in English.

This is a small module with outsized impact. The research flagged it directly: *amounts, dates, order IDs and phone numbers are where Urdu TTS most often breaks*, and it is the single most common source of "the demo sounded fine until it read the balance." It is also cheap to fix and almost never fixed, which is why competing demos fall over on exactly this.

**Everything here is a pure function over strings.** No network, no async, no state. That makes it the most testable module in the codebase — and it should have the deepest test coverage.

---

## Steps

### 1. Number-to-Urdu-words core

`src/agent/language/numbers.py`. Urdu numerals are irregular below 100 — unlike English, where "twenty-one" composes predictably, Urdu `اکیس` (21) is not a compound of `بیس` and `ایک`. **The 0–99 range must be a lookup table.** Attempting to generate it algorithmically produces subtly wrong words that a native speaker will immediately flag.

```python
_ONES_TO_99: dict[int, str] = {
    0: "صفر", 1: "ایک", 2: "دو", 3: "تین", 4: "چار", 5: "پانچ",
    # ... all 100 entries, verified by a native speaker
    21: "اکیس", 22: "بائیس", 29: "انتیس", 40: "چالیس", 99: "ننانوے",
}

_SCALES: list[tuple[int, str]] = [
    (10_000_000, "کروڑ"),
    (100_000,    "لاکھ"),     # South Asian grouping, NOT million
    (1_000,      "ہزار"),
    (100,        "سو"),
]
```

**Use the South Asian numbering system.** 100,000 is `ایک لاکھ`, not "one hundred thousand". A Pakistani caller hearing "hundred thousand rupees" registers it as foreign and slightly wrong.

### 2. Contextual formatters

Each of these needs its own rule — a single generic number formatter gets them all wrong.

| Input kind | Rule | Example |
|---|---|---|
| Plain integer | Cardinal words | `1500` → `پندرہ سو` |
| Currency | Cardinal + `روپے` | `Rs. 1,500` → `پندرہ سو روپے` |
| **Phone number** | **Digit by digit** | `03001234567` → `صفر تین صفر صفر ...` |
| Order / tracking ID | Digit by digit, letters spelled | `ORD-4471` → `او آر ڈی چار چار سات ایک` |
| Date | Day + month name + year | `2026-08-08` → `آٹھ اگست دو ہزار چھبیس` |
| Time | 12-hour with `بجے` | `14:30` → `دوپہر ڈھائی بجے` |
| Percentage | Cardinal + `فیصد` | `50%` → `پچاس فیصد` |
| Decimal (halves) | Idiomatic form | `3.5` → `ساڑھے تین` |
| Ordinal | Ordinal form | `1st` → `پہلا` |

**Phone numbers are the trap.** `03001234567` read as a cardinal number produces an absurd multi-crore figure that means nothing to the caller. Detect the Pakistani mobile pattern (`03XXXXXXXXX`, 11 digits) explicitly and read it digit by digit with natural grouping pauses.

Half-values deserve special handling because Urdu has dedicated idiomatic words — `ڈیڑھ` (1.5), `ڈھائی` (2.5), `ساڑھے تین` (3.5). Saying `تین اعشاریہ پانچ` is technically correct and sounds like a calculator.

### 3. Preserve intentional English

The code-switching design (TASK-03) deliberately keeps English nouns in English. The normaliser must not undo that work by transliterating them into Urdu script.

```python
def normalize_for_tts(text: str, *, preserve: frozenset[str] = ENGLISH_KEEP) -> str:
    """Pure. No clock, no locale, no network."""
```

Maintain `ENGLISH_KEEP` as the same vocabulary used in the STT keyterm file (TASK-04) — they describe the same set of words and should not drift apart. Consider loading both from one source.

### 4. Order of operations

Apply longest-pattern-first. Naive left-to-right replacement destroys compound patterns:

```
1. Protect preserved English tokens (replace with placeholders)
2. Dates          (before plain numbers — 2026-08-08 contains three integers)
3. Times          (before plain numbers)
4. Phone numbers  (before plain numbers — 11 digits would become a cardinal)
5. Order IDs      (before plain numbers)
6. Currency       (before plain numbers)
7. Percentages
8. Decimals
9. Plain integers
10. Restore preserved tokens
```

Get this order wrong and `2026-08-08` becomes "two thousand twenty-six dash eight dash eight". Encode the ordering as a test, not a comment.

### 5. Handle what the model actually emits

Models produce markdown even when told not to. Strip before synthesis: `**bold**`, `*italic*`, backticks, bullet markers, numbered-list prefixes, stray newlines. A TTS engine will happily read "asterisk asterisk" aloud.

### 6. Native speaker review — do not skip

Automated tests confirm *stability*, not *correctness*. Have a native Urdu speaker review `tests/fixtures/golden/urdu_normalization.yaml` once and sign off. From then on the tests protect that judgement.

Budget one hour of a native speaker's time. It is the cheapest quality investment in the entire project, and without it you are asserting that your own guesses about Urdu number grammar are right.

---

## Acceptance criteria

- [ ] `normalize_for_tts()` is pure — no clock, locale, network, or global state
- [ ] All 0–99 numerals from a verified lookup table, not generated
- [ ] South Asian grouping (`لاکھ`, `کروڑ`), not Western
- [ ] Pakistani mobile numbers read digit by digit
- [ ] Dates, times, currency, percentages each use their correct form
- [ ] Half-values use idiomatic forms (`ڈیڑھ`, `ڈھائی`, `ساڑھے`)
- [ ] Preserved English tokens survive unchanged in Latin script
- [ ] No ASCII digits remain in any output
- [ ] Markdown artefacts stripped
- [ ] Golden file reviewed and signed off by a native speaker (record who, in the PR)
- [ ] Runs in < 1ms for a typical 200-character utterance
- [ ] 100% branch coverage on `numbers.py`

---

## Unit tests

This module gets the deepest coverage in the project. `tests/unit/test_normalize.py`:

### Table-driven cases

```python
CASES = [
    # (input, expected, description)
    ("0",            "صفر",                      "zero"),
    ("21",           "اکیس",                     "irregular teen-adjacent"),
    ("100",          "ایک سو",                   "hundred"),
    ("1500",         "پندرہ سو",                 "colloquial hundreds form"),
    ("100000",       "ایک لاکھ",                 "South Asian lakh, not 100 thousand"),
    ("10000000",     "ایک کروڑ",                 "crore"),
    ("Rs. 1,500",    "پندرہ سو روپے",            "currency, comma separator"),
    ("50%",          "پچاس فیصد",                "percentage"),
    ("1.5",          "ڈیڑھ",                     "idiomatic half"),
    ("2.5",          "ڈھائی",                    "idiomatic half"),
    ("3.5",          "ساڑھے تین",                "idiomatic half"),
    ("2026-08-08",   "آٹھ اگست دو ہزار چھبیس",   "ISO date"),
    ("14:30",        "دوپہر ڈھائی بجے",          "24h time to spoken"),
]

@pytest.mark.parametrize("raw,expected,desc", CASES)
def test_normalization_cases(raw: str, expected: str, desc: str) -> None:
    assert normalize_for_tts(raw) == expected, desc
```

### The high-value specific tests

```python
def test_pakistani_mobile_read_digit_by_digit() -> None:
    """A cardinal reading here produces a meaningless multi-crore number."""
    out = normalize_for_tts("03001234567")
    assert "کروڑ" not in out and "لاکھ" not in out
    assert out.count("صفر") >= 2        # leading 0 and the 0 in 300


def test_ordering_dates_before_integers() -> None:
    """Wrong order turns a date into three separate numbers."""
    out = normalize_for_tts("آپ کا آرڈر 2026-08-08 کو آئے گا")
    assert "اگست" in out
    assert "دو ہزار چھبیس" in out


def test_english_preserved_in_latin_script() -> None:
    out = normalize_for_tts("آپ کا Premium Plan فعال ہے")
    assert "Premium Plan" in out


def test_mixed_content_end_to_end() -> None:
    out = normalize_for_tts("آپ کا order ORD-4471 کل, Rs. 2,500 کا")
    assert "order" in out                    # preserved English
    assert not any(c.isdigit() and c.isascii() for c in out)
    assert "روپے" in out
```

### Property-based

```python
@given(st.integers(min_value=0, max_value=10_000_000))
def test_no_ascii_digits_survive(n: int) -> None:
    assert not any(c.isdigit() and c.isascii() for c in normalize_for_tts(str(n)))


@given(st.integers(min_value=0, max_value=10_000_000))
def test_output_is_nonempty_and_stripped(n: int) -> None:
    out = normalize_for_tts(str(n))
    assert out and out == out.strip()


@given(st.text())
def test_never_raises_on_arbitrary_input(s: str) -> None:
    """The model can emit anything; this runs on the hot path and must not throw."""
    normalize_for_tts(s)
```

### Golden file regression

```python
def test_against_golden_file() -> None:
    """Locks in native-speaker-reviewed output. Changing this file needs re-review."""
    golden = yaml.safe_load(Path("tests/fixtures/golden/urdu_normalization.yaml").read_text(encoding="utf-8"))
    for case in golden["cases"]:
        assert normalize_for_tts(case["input"]) == case["expected"], case["input"]
```

### Performance

```python
def test_normalization_is_fast() -> None:
    """Runs on the hot path, once per sentence chunk."""
    text = "آپ کا order ORD-4471 کل 2026-08-08 کو Rs. 2,500 میں deliver ہوگا" * 3
    elapsed = timeit.timeit(lambda: normalize_for_tts(text), number=100) / 100
    assert elapsed < 0.001, f"{elapsed * 1000:.2f}ms per call is too slow"
```

---

## Common pitfalls

- **Generating 0–99 algorithmically.** Urdu numerals are irregular; the output is subtly wrong in ways only a native speaker catches.
- **Western number grouping.** `لاکھ` and `کروڑ` are what Pakistani callers expect.
- **Reading phone numbers as cardinals.** The most jarring possible failure.
- **`تین اعشاریہ پانچ` instead of `ساڑھے تین`.** Technically right, sounds like a machine.
- **Wrong operation order.** Dates and phone numbers must be handled before plain integers.
- **Raising on unexpected input.** This is on the hot path; a crash here drops the call.
- **Shipping without native-speaker review.** You are otherwise trusting your own guesses about a language's number grammar.
