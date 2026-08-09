"""Keyterm list loading for Deepgram prompting.

Pure file parsing, no vendor imports — the STT adapter consumes the result.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["MAX_KEYTERMS", "load_keyterms"]

# An over-broad list biases recognition toward listed words even when they were
# not said, which is worse than omitting a term. Deepgram also caps the list.
MAX_KEYTERMS = 100

_DEFAULT_PATH = Path(__file__).with_name("keyterms.txt")


def load_keyterms(path: Path | None = None) -> list[str]:
    """Parse the keyterm file, preserving order and dropping duplicates.

    Order is preserved rather than sorted so the most valuable terms — product
    and brand names — stay at the top if the list is ever truncated.
    """
    source = path or _DEFAULT_PATH
    if not source.exists():
        return []

    seen: set[str] = set()
    terms: list[str] = []
    for raw in source.read_text(encoding="utf-8").splitlines():
        term = raw.strip()
        if not term or term.startswith("#"):
            continue
        folded = term.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        terms.append(term)
    return terms
