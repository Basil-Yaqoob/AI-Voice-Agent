"""Script detection for code-switched text.

Session 2 measured Deepgram returning English words transliterated into Urdu
script (``order`` -> ``ارڈن``), so "is this token English?" cannot be answered by
looking for Latin characters alone. These helpers keep that assumption in one
place rather than scattered through the pipeline.
"""

from __future__ import annotations

__all__ = [
    "URDU_RANGE",
    "contains_urdu",
    "is_latin_token",
    "urdu_ratio",
]

# U+0600-U+06FF Arabic, which covers the Perso-Arabic letters Urdu uses, plus
# U+0750-U+077F Arabic Supplement and U+FB50-U+FDFF presentation forms.
URDU_RANGE = ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF))


def _is_urdu_char(ch: str) -> bool:
    point = ord(ch)
    return any(lo <= point <= hi for lo, hi in URDU_RANGE)


def contains_urdu(text: str) -> bool:
    return any(_is_urdu_char(ch) for ch in text)


def urdu_ratio(text: str) -> float:
    """Share of letter characters written in Perso-Arabic script.

    Used to detect an agent reply that has drifted out of Urdu entirely — one of
    the two failure modes the code-switching rules warn against.
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if _is_urdu_char(ch)) / len(letters)


def is_latin_token(token: str) -> bool:
    """Whether a whitespace-delimited token is a plain Latin-script word."""
    stripped = token.strip(".,!?;:()\"'۔،؟؛")
    return bool(stripped) and stripped.isascii() and any(ch.isalpha() for ch in stripped)
