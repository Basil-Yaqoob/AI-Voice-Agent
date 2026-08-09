"""English vocabulary written in Urdu script, for TTS input only.

Native review, 9 August 2026: the Azure ``ur-PK`` voice mispronounces English
words written in Latin script. Writing them the way an Urdu speaker writes them
(``order`` -> ``آرڈر``) makes the voice say them with a natural Pakistani accent.
The reviewer preferred the transliterated form for every word tested.

**This applies only on the way into TTS.** Everywhere else — the model's output,
conversation history, transcripts, logs, retrieval — keeps the Latin form,
because:

* the code-switching rules instruct the model to *write* ``order``;
* the transcript should show what the model actually produced;
* :mod:`agent.knowledge.store` indexes both scripts already.

So the pipeline reads Latin and speaks Urdu script. Note this converges with the
STT side, where Deepgram returns English words transliterated into Urdu script
too — the Latin form only ever exists between the model and the log.

A curated lexicon rather than a general algorithm. The vocabulary is small,
known, and shared with the Deepgram keyterm list; rule-based transliteration
would be wrong often and wrong invisibly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

__all__ = ["ENGLISH_TO_URDU", "transliterate_for_speech", "unmapped_words"]

# ⚠️ Needs the same native-speaker review the numeral table got.
# Regenerate the listening sheet with: uv run python eval/make_translit_sheet.py
ENGLISH_TO_URDU: dict[str, str] = {
    # ── order and delivery ────────────────────────────────────────────
    "order": "آرڈر",
    "delivery": "ڈیلیوری",
    "deliver": "ڈیلیور",
    "delivered": "ڈیلیور",
    "tracking": "ٹریکنگ",
    "track": "ٹریک",
    "courier": "کورئیر",
    "parcel": "پارسل",
    "package": "پیکج",
    "shipment": "شپمنٹ",
    # ── account and money ──────────────────────────────────────────────
    "account": "اکاؤنٹ",
    "balance": "بیلنس",
    "payment": "پیمنٹ",
    "paid": "پیڈ",
    "refund": "ریفنڈ",
    "invoice": "انوائس",
    "receipt": "رسید",
    "bill": "بل",
    "charges": "چارجز",
    "discount": "ڈسکاؤنٹ",
    # ── service ─────────────────────────────────────────────────────────
    "complaint": "کمپلینٹ",
    "status": "اسٹیٹس",
    "booking": "بکنگ",
    "cancel": "کینسل",
    "cancelled": "کینسل",
    "confirm": "کنفرم",
    "confirmed": "کنفرم",
    "update": "اپڈیٹ",
    "check": "چیک",
    "customer": "کسٹمر",
    "care": "کیئر",
    "service": "سروس",
    "helpline": "ہیلپ لائن",
    "support": "سپورٹ",
    # ── connectivity and products ────────────────────────────────────────
    "internet": "انٹرنیٹ",
    "data": "ڈیٹا",
    "bundle": "بنڈل",
    "recharge": "ری چارج",
    "package": "پیکج",  # noqa: F601 - duplicate key is intentional documentation
    "premium": "پریمیم",
    "plan": "پلان",
    "basic": "بیسک",
    "unlimited": "ان لمیٹڈ",
    "online": "آن لائن",
    "app": "ایپ",
    "email": "ای میل",
    "address": "ایڈریس",
    "number": "نمبر",
    "code": "کوڈ",
    "message": "میسج",
    "call": "کال",
}

_WORD = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")


def transliterate_for_speech(
    text: str,
    *,
    extra: Mapping[str, str] | None = None,
) -> str:
    """Rewrite known English words in Urdu script, ready for TTS.

    ``extra`` carries per-deployment vocabulary — client brand and product names,
    which cannot live in a shared lexicon. Unknown words are left in Latin: the
    voice pronounces them badly, but a wrong guess at the spelling would be worse
    and harder to notice. Use :func:`unmapped_words` to find the gaps.
    """
    lexicon = {**ENGLISH_TO_URDU, **{k.casefold(): v for k, v in (extra or {}).items()}}

    def _swap(match: re.Match[str]) -> str:
        return lexicon.get(match[0].casefold(), match[0])

    return _WORD.sub(_swap, text)


def unmapped_words(text: str, *, extra: Mapping[str, str] | None = None) -> list[str]:
    """English words with no transliteration, so the lexicon can be grown.

    Worth logging in production: every entry here is a word the caller heard
    mispronounced.
    """
    lexicon = {**ENGLISH_TO_URDU, **{k.casefold(): v for k, v in (extra or {}).items()}}
    seen: dict[str, None] = {}
    for match in _WORD.finditer(text):
        if match[0].casefold() not in lexicon:
            seen.setdefault(match[0], None)
    return list(seen)
