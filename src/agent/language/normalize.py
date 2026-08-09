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

__all__ = ["ENGLISH_KEEP", "normalize_for_tts"]

# English tokens deliberately left in English. Kept aligned with the Deepgram
# keyterm list — they describe the same vocabulary and must not drift apart.
ENGLISH_KEEP = frozenset(
    {
        "order",
        "delivery",
        "deliver",
        "tracking",
        "track",
        "account",
        "balance",
        "package",
        "complaint",
        "refund",
        "invoice",
        "courier",
        "payment",
        "receipt",
        "address",
        "booking",
        "cancel",
        "confirm",
        "status",
        "internet",
        "data",
        "bundle",
        "recharge",
        "bill",
        "premium",
        "plan",
        "basic",
        "check",
        "update",
        "email",
        "app",
        "online",
        "customer",
        "care",
        "service",
        "number",
    }
)

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


def _pk_mobile(match: re.Match[str]) -> str:
    """Pakistani mobile numbers, digit by digit.

    A cardinal reading here produces a meaningless multi-crore figure. This is
    the most jarring single failure an Urdu voice agent can make.
    """
    return digit_by_digit(re.sub(r"\D", "", match[0]))


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
    amount = int(match[1].replace(",", ""))
    return f"{number_to_urdu(amount)} روپے"


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
    # PK mobile is 0 + 3-digit operator prefix + 7 digits = 11 total (03001234567).
    (re.compile(r"\b0\d{3}[- ]?\d{7}\b"), _pk_mobile),
    (re.compile(r"\+92[- ]?\d{3}[- ]?\d{7}\b"), _pk_mobile),
    (re.compile(r"\b[A-Z]{2,4}-\d{3,10}\b"), _reference),
    (re.compile(r"(?:Rs\.?|PKR|روپے)\s*([\d,]+)", re.IGNORECASE), _currency),
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

    for index, word in enumerate(protected):
        text = text.replace(_placeholder(index), word)

    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _strip_markdown(text: str) -> str:
    """TTS will happily read 'asterisk asterisk' aloud."""
    for pattern, replacement in _MARKDOWN:
        text = pattern.sub(replacement, text)
    return text
