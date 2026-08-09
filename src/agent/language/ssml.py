"""Turn normalised text into SSML for Azure.

The invariant this module rests on, established by native review:

    **Urdu script means "say this Urdu-style". Latin script means "say this in
    English".**

:mod:`agent.language.translit` has already rewritten most English vocabulary
into Urdu script, so whatever Latin survives to this point is there deliberately:
either a word the reviewer wants in an English accent (``data``, ``track``), or
a word missing from the lexicon. Both want the same treatment — an SSML language
switch — which is why the rule can be this simple.

Measured 10 Aug 2026: ``<lang xml:lang="en-US">`` does change the rendered audio
inside ``ur-PK-UzmaNeural``. A monolingual voice ignoring the tag was the obvious
worry and it turned out not to be true.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

__all__ = ["build_ssml", "has_latin_run"]

# A run of Latin words, kept together so "Premium Plan" gets one switch rather
# than two — separate tags per word make the voice pause between them.
# U+2019 is the curly apostrophe; models emit it in "customer's" and it must not
# break the run. Written as an escape because a literal one is genuinely
# ambiguous on screen, which is what RUF001 objects to.
_APOS = "'\\u2019"
_LATIN_RUN = re.compile(rf"[A-Za-z][A-Za-z{_APOS}\-]*(?:\s+[A-Za-z][A-Za-z{_APOS}\-]*)*")

_SPEAK_OPEN = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{lang}">'
    '<voice name="{voice}">'
)
_SPEAK_CLOSE = "</voice></speak>"


def has_latin_run(text: str) -> bool:
    return _LATIN_RUN.search(text) is not None


def build_ssml(
    text: str,
    *,
    voice: str,
    lang: str = "ur-PK",
    english_lang: str = "en-US",
) -> str:
    """Wrap ``text`` in SSML, switching language for any Latin-script run.

    Everything is XML-escaped. The model can emit ``&`` or ``<`` and an
    unescaped one would make Azure reject the whole request mid-call — a dropped
    turn rather than a mispronounced word.
    """
    parts: list[str] = []
    cursor = 0

    for match in _LATIN_RUN.finditer(text):
        parts.append(escape(text[cursor : match.start()]))
        parts.append(f'<lang xml:lang="{english_lang}">{escape(match[0])}</lang>')
        cursor = match.end()

    parts.append(escape(text[cursor:]))

    return _SPEAK_OPEN.format(lang=lang, voice=voice) + "".join(parts) + _SPEAK_CLOSE
