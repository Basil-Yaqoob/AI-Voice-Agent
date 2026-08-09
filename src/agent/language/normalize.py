"""Convert model output into text Urdu TTS pronounces correctly.

Everything here is a pure function over strings: no clock, no locale, no network,
no global state. That makes it the most testable module in the codebase, which
matters because this is where competing demos visibly fail — a phone number read
as a multi-crore cardinal, or a balance in the wrong grammatical form.

**Order of operations is load-bearing.** Longest and most specific patterns run
first. Naive left-to-right replacement turns ``2026-08-08`` into three separate
integers. The ordering is encoded in ``_RULES`` and asserted by tests.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from agent.language.numbers import ORDINALS, digit_by_digit, number_to_urdu
from agent.language.translit import ENGLISH_TO_URDU

__all__ = ["ENGLISH_KEEP", "normalize_for_tts"]

# The English vocabulary the pipeline handles deliberately: shielded from the
# number rules, then spoken in Urdu script. Derived from the transliteration
# lexicon so the two cannot drift apart — a word protected here but missing
# from the lexicon would be read aloud in a mispronounced Latin form.
ENGLISH_KEEP = frozenset(ENGLISH_TO_URDU)

_MONTHS: dict[int, str] = {
    1: "جنوری",
    2: "فروری",
    3: "مارچ",
    4: "اپریل",
    5: "مئی",
    6: "جون",
    7: "جولائی",
    8: "اگست",
    9: "ستمبر",
    10: "اکتوبر",
    11: "نومبر",
    12: "دسمبر",
}

# Urdu has dedicated idiomatic words for these. "تین اعشاریہ پانچ" is technically
# correct and sounds like a calculator reading out a number.
_HALVES: dict[int, str] = {1: "ڈیڑھ", 2: "ڈھائی"}

_LETTER_NAMES: dict[str, str] = {
    "a": "اے",
    "b": "بی",
    "c": "سی",
    "d": "ڈی",
    "e": "ای",
    "f": "ایف",
    "g": "جی",
    "h": "ایچ",
    "i": "آئی",
    "j": "جے",
    "k": "کے",
    "l": "ایل",
    "m": "ایم",
    "n": "این",
    "o": "او",
    "p": "پی",
    "q": "کیو",
    "r": "آر",
    "s": "ایس",
    "t": "ٹی",
    "u": "یو",
    "v": "وی",
    "w": "ڈبلیو",
    "x": "ایکس",
    "y": "وائے",
    "z": "زیڈ",
}


def _placeholder(index: int) -> str:
    """A token no number rule can match.

    Private-use codepoints only: anything containing a digit would be picked
    up by the integer rule and read aloud as an Urdu number.
    """
    return chr(0xE100 + index)


# ── individual rules ──────────────────────────────────────────────────────


def _date(match: re.Match[str]) -> str:
    year, month, day = int(match[1]), int(match[2]), int(match[3])
    if not 1 <= month <= 12:
        return match[0]
    return f"{number_to_urdu(day)} {_MONTHS[month]} {number_to_urdu(year)}"


def _time(match: re.Match[str]) -> str:
    hour24, minute = int(match[1]), int(match[2])
    if hour24 > 23 or minute > 59:
        return match[0]

    period = (
        "صبح"
        if 4 <= hour24 < 12
        else "دوپہر"
        if 12 <= hour24 < 16
        else "شام"
        if 16 <= hour24 < 19
        else "رات"
    )
    hour12 = hour24 % 12 or 12

    if minute == 0:
        spoken = f"{number_to_urdu(hour12)} بجے"
    elif minute == 15:
        spoken = f"سوا {number_to_urdu(hour12)} بجے"
    elif minute == 30:
        spoken = f"{_half_form(hour12)} بجے"
    elif minute == 45:
        spoken = f"پونے {number_to_urdu(hour12 % 12 + 1)} بجے"
    else:
        spoken = f"{number_to_urdu(hour12)} بج کر {number_to_urdu(minute)} منٹ"
    return f"{period} {spoken}"


def _half_form(whole: int) -> str:
    """1.5 -> ڈیڑھ, 2.5 -> ڈھائی, otherwise ساڑھے <n>."""
    return _HALVES.get(whole, f"ساڑھے {number_to_urdu(whole)}")


# A Pakistani number carries 9-12 digits once separators are stripped: mobiles
# are 11 (03001234567), landlines 9-11, UAN 12 (021-111-123-456).
_PHONE_MIN_DIGITS = 9
_PHONE_MAX_DIGITS = 12


def _pk_phone(match: re.Match[str]) -> str:
    """Pakistani phone numbers, digit by digit — mobile, landline or UAN.

    A cardinal reading produces a meaningless multi-crore figure and drops the
    leading zero. This is the most jarring single failure an Urdu voice agent
    can make, and it applies to landlines exactly as much as to mobiles.

    The digit count is validated here rather than in the pattern: Pakistani
    numbers are written with one, two or three separators (0300-1234567,
    021 111 123 456), and a regex strict enough to cover every shape becomes
    unreadable and over-matches ordinary digit runs.
    """
    digits = re.sub(r"\D", "", match[0])
    if not _PHONE_MIN_DIGITS <= len(digits) <= _PHONE_MAX_DIGITS:
        return match[0]  # not a phone number; leave it for the integer rule
    return digit_by_digit(digits)


def _reference(match: re.Match[str]) -> str:
    """Order and tracking codes: letters spelled out, digits read individually."""
    spoken: list[str] = []
    for ch in match[0]:
        if ch.isdigit():
            spoken.append(digit_by_digit(ch))
        elif ch.isalpha():
            spoken.append(_LETTER_NAMES.get(ch.lower(), ch))
    return " ".join(spoken)


def _currency(match: re.Match[str]) -> str:
    """Amounts, including paisa. 'Rs 1,250.50' must not leak a bare '.50'."""
    rupees = int(match[1].replace(",", ""))
    paisa = match[2]
    spoken = f"{number_to_urdu(rupees)} روپے"
    if paisa and int(paisa.ljust(2, "0")) > 0:
        spoken += f" {number_to_urdu(int(paisa.ljust(2, '0')))} پیسے"
    return spoken


def _percent(match: re.Match[str]) -> str:
    return f"{number_to_urdu(int(match[1]))} فیصد"


def _decimal(match: re.Match[str]) -> str:
    whole, frac = int(match[1]), match[2]
    if frac == "5":
        return _half_form(whole)
    return f"{number_to_urdu(whole)} اعشاریہ {digit_by_digit(frac)}"


def _ordinal(match: re.Match[str]) -> str:
    value = int(match[1])
    return ORDINALS.get(value, f"{number_to_urdu(value)}واں")


def _integer(match: re.Match[str]) -> str:
    return number_to_urdu(int(match[0].replace(",", "")))


# Ordering matters. Dates and phone numbers must be consumed before the bare
# integer rule can shred them into pieces.
_RULES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), _date),
    (re.compile(r"\b(\d{1,2}):(\d{2})\b"), _time),
    # Any Pakistani phone shape, digit by digit. Covers mobiles
    # (0300-1234567, +92-300-1234567) and landlines with a 2-5 digit area code
    # (042-35678901, 021 111 123 456). Reading a landline as a cardinal is the
    # same failure as reading a mobile that way.
    (re.compile(r"\+92[\d\s-]{9,16}\d"), _pk_phone),
    (re.compile(r"\b0\d[\d\s-]{7,14}\d\b"), _pk_phone),
    # Reference codes. Deliberately loose on letter count and case: ORD-4471,
    # ORDER-12345 and trk-99 are all codes to spell out, not numbers to read.
    (re.compile(r"\b[A-Za-z]{2,8}[-/]\d{2,12}\b"), _reference),
    (
        re.compile(r"(?:Rs\.?|PKR|روپے)\s*([\d,]+)(?:\.(\d{1,2}))?", re.IGNORECASE),
        _currency,
    ),
    (re.compile(r"\b(\d{1,3})\s*%"), _percent),
    (re.compile(r"\b(\d+)\.(\d+)\b"), _decimal),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE), _ordinal),
    (re.compile(r"\b\d[\d,]*\b"), _integer),
]

_MARKDOWN = [
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"(?<!\w)\*(.+?)\*(?!\w)", re.DOTALL), r"\1"),
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),
    (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), ""),
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
]


def normalize_for_tts(
    text: str,
    *,
    preserve: Iterable[str] = ENGLISH_KEEP,
) -> str:
    """Make model output safe to synthesise.

    Pure and total: never raises, whatever the model emits. This runs on the hot
    path once per sentence chunk, so a crash here would drop the call.
    """
    if not text:
        return ""

    try:
        return _normalize(text, frozenset(w.casefold() for w in preserve))
    except Exception:
        return _strip_markdown(text).strip()


def _normalize(text: str, preserve: frozenset[str]) -> str:
    text = _strip_markdown(text)

    # Protect deliberate English before number rules can touch surrounding text.
    protected: list[str] = []

    def _shield(match: re.Match[str]) -> str:
        word = match[0]
        if word.casefold() not in preserve:
            return word
        protected.append(word)
        return _placeholder(len(protected) - 1)

    text = re.sub(r"\b[A-Za-z][A-Za-z'-]*\b", _shield, text)

    for pattern, handler in _RULES:
        text = pattern.sub(handler, text)

    # Restore as Urdu script, not Latin. Native review 9 Aug 2026: the ur-PK
    # voice mispronounces Latin-script English, and writing the word the way an
    # Urdu speaker writes it gives a natural Pakistani pronunciation. Only the
    # TTS input is rewritten — history and transcripts keep the Latin form.
    for index, word in enumerate(protected):
        spoken = ENGLISH_TO_URDU.get(word.casefold(), word)
        text = text.replace(_placeholder(index), spoken)

    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _strip_markdown(text: str) -> str:
    """TTS will happily read 'asterisk asterisk' aloud."""
    for pattern, replacement in _MARKDOWN:
        text = pattern.sub(replacement, text)
    return text
