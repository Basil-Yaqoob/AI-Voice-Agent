"""Urdu cardinal numbers.

Two rules govern this module and both matter:

1. **0-99 is a lookup table, never generated.** Urdu numerals are irregular in a
   way English is not: 21 is ``اکیس``, which is not a compound of ``بیس`` and
   ``ایک``. Every attempt to generate this range produces words that are subtly
   wrong in a way only a native speaker catches.
2. **South Asian grouping.** 100,000 is ``ایک لاکھ``, not "one hundred thousand".
   A Pakistani caller hearing the Western form registers it as foreign.

⚠️ The table below needs native-speaker sign-off before it is trusted in front
of a customer. See ``tests/fixtures/golden/urdu_normalization.yaml``.
"""

from __future__ import annotations

__all__ = ["ORDINALS", "digit_by_digit", "number_to_urdu"]

# ── 0-99, the irregular range ────────────────────────────────────────────
_UNITS: dict[int, str] = {
    0: "صفر",
    1: "ایک",
    2: "دو",
    3: "تین",
    4: "چار",
    5: "پانچ",
    6: "چھ",
    7: "سات",
    8: "آٹھ",
    9: "نو",
    10: "دس",
    11: "گیارہ",
    12: "بارہ",
    13: "تیرہ",
    14: "چودہ",
    15: "پندرہ",
    16: "سولہ",
    17: "سترہ",
    18: "اٹھارہ",
    19: "انیس",
    20: "بیس",
    21: "اکیس",
    22: "بائیس",
    23: "تئیس",
    24: "چوبیس",
    25: "پچیس",
    26: "چھبیس",
    27: "ستائیس",
    28: "اٹھائیس",
    29: "انتیس",
    30: "تیس",
    31: "اکتیس",
    32: "بتیس",
    33: "تینتیس",
    34: "چونتیس",
    35: "پینتیس",
    36: "چھتیس",
    37: "سینتیس",
    38: "اڑتیس",
    39: "انتالیس",
    40: "چالیس",
    41: "اکتالیس",
    42: "بیالیس",
    43: "تینتالیس",
    44: "چوالیس",
    45: "پینتالیس",
    46: "چھیالیس",
    47: "سینتالیس",
    48: "اڑتالیس",
    49: "انچاس",
    50: "پچاس",
    51: "اکاون",
    52: "باون",
    53: "ترپن",
    54: "چون",
    55: "پچپن",
    56: "چھپن",
    57: "ستاون",
    58: "اٹھاون",
    59: "انسٹھ",
    60: "ساٹھ",
    61: "اکسٹھ",
    62: "باسٹھ",
    63: "تریسٹھ",
    64: "چونسٹھ",
    65: "پینسٹھ",
    66: "چھیاسٹھ",
    67: "سڑسٹھ",
    68: "اڑسٹھ",
    69: "انہتر",
    70: "ستر",
    71: "اکہتر",
    72: "بہتر",
    73: "تہتر",
    74: "چوہتر",
    75: "پچہتر",
    76: "چھہتر",
    77: "ستتر",
    78: "اٹھہتر",
    79: "اناسی",
    80: "اسی",
    81: "اکیاسی",
    82: "بیاسی",
    83: "تراسی",
    84: "چوراسی",
    85: "پچاسی",
    86: "چھیاسی",
    87: "ستاسی",
    88: "اٹھاسی",
    89: "نواسی",
    90: "نوے",
    91: "اکانوے",
    92: "بانوے",
    93: "ترانوے",
    94: "چورانوے",
    95: "پچانوے",
    96: "چھیانوے",
    97: "ستانوے",
    98: "اٹھانوے",
    99: "ننانوے",
}

# South Asian scales, largest first. Not thousand/million/billion.
_SCALES: list[tuple[int, str]] = [
    (10_000_000, "کروڑ"),
    (100_000, "لاکھ"),
    (1_000, "ہزار"),
    (100, "سو"),
]

# Spoken shorthand: 1500 is "پندرہ سو" (fifteen hundred), not "ایک ہزار پانچ سو".
#
# Deliberately narrow. In the 1100-1900 band the "X سو" form is unambiguous and
# standard in speech (prices, years). Above 2000 usage is mixed — 2500 is more
# often "دو ہزار پانچ سو" than "پچیس سو" — so the conservative long form wins
# there. Widening this range is a native-speaker call, not a developer one.
_HUNDREDS_SHORTHAND_MIN = 1_100
_HUNDREDS_SHORTHAND_MAX = 1_900

ORDINALS: dict[int, str] = {
    1: "پہلا",
    2: "دوسرا",
    3: "تیسرا",
    4: "چوتھا",
    5: "پانچواں",
    6: "چھٹا",
    7: "ساتواں",
    8: "آٹھواں",
    9: "نواں",
    10: "دسواں",
}


def number_to_urdu(value: int) -> str:
    """Render a non-negative integer as spoken Urdu words."""
    if value < 0:
        return f"منفی {number_to_urdu(-value)}"
    if value in _UNITS:
        return _UNITS[value]

    if (
        _HUNDREDS_SHORTHAND_MIN <= value <= _HUNDREDS_SHORTHAND_MAX
        and value % 100 == 0
        and value % 1000 != 0
    ):
        # 1500 -> پندرہ سو, 2300 -> تیئس سو
        return f"{_UNITS[value // 100]} سو"

    for scale_value, scale_word in _SCALES:
        if value >= scale_value:
            count, remainder = divmod(value, scale_value)
            head = f"{number_to_urdu(count)} {scale_word}"
            return head if remainder == 0 else f"{head} {number_to_urdu(remainder)}"

    return _UNITS[value]  # unreachable for non-negative ints


def digit_by_digit(digits: str) -> str:
    """Read each digit separately.

    Required for phone numbers and reference codes. Reading ``03001234567`` as a
    cardinal produces a meaningless multi-crore figure — the single most jarring
    failure mode in an Urdu voice agent.
    """
    return " ".join(_UNITS[int(ch)] for ch in digits if ch.isdigit())
