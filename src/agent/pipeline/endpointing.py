"""Deciding when the caller has finished speaking.

180ms of an 800ms budget, and the only large slice that costs nothing to
improve. It is also the stage most likely to make the agent feel rude, because
the failure mode is interrupting someone mid-thought.

The Urdu-specific problem: **Urdu speakers pause mid-sentence more than English
speakers do**, and every default VAD threshold is tuned on English corpora. A
fixed window is therefore either rude or slow; it cannot be both.

The win comes from Urdu being verb-final. A clause ending in a conjugated verb
(``ہے``, ``گا``, ``چاہیے``) is very likely complete, so the window can be cut to
120ms on the common case, while trailing conjunctions and mid-number pauses
extend it to 600ms. Average latency falls *and* the cut-off rate falls, which a
single threshold can never achieve.
"""

from __future__ import annotations

import re

__all__ = ["AdaptiveEndpointer", "BargeInDetector"]

# A caller who just said "اور" is unambiguously still going. Cutting here is
# the rudest thing the agent can do.
_CONTINUATIONS = frozenset(
    {
        "اور",  # and
        "لیکن",  # but
        "مگر",  # but
        "کیونکہ",  # because
        "پھر",  # then
        "تو",  # so
        "کہ",  # that
        "یا",  # or
        "جب",  # when
        "اگر",  # if
        "میرا",  # my — a noun is coming
        "میری",
        "آپ",  # you
        "ایک",  # one — often precedes a noun
    }
)

# Urdu is SOV: a conjugated verb at the end signals completion. This is where
# the latency is won back.
_TERMINAL_VERBS = frozenset(
    {
        "ہے",
        "ہیں",
        "تھا",
        "تھی",
        "تھے",
        "گا",
        "گی",
        "گے",
        "چاہیے",
        "کریں",
        "کرو",
        "دیں",
        "بتائیں",
        "ہوگا",
        "ہوگی",
        "نہیں",
        "دیجیے",
        "کیجیے",
    }
)

# Callers reading an order or phone number pause between digit groups. Cutting
# inside one forces a full restart, which is deeply annoying.
#
# \d already covers Urdu-Indic digits (U+06F0-U+06F9) — Python matches any
# Unicode decimal digit for str patterns — so an explicit range would be
# redundant as well as unreadable.
_TRAILING_DIGITS = re.compile(r"\d\s*$")


class AdaptiveEndpointer:
    """Chooses a silence threshold from the shape of the partial transcript."""

    BASE_MS = 180
    MIN_MS = 120
    MAX_MS = 600

    def __init__(
        self,
        *,
        base_ms: int = BASE_MS,
        min_ms: int = MIN_MS,
        max_ms: int = MAX_MS,
    ) -> None:
        self._base_ms = base_ms
        self._min_ms = min_ms
        self._max_ms = max_ms

    def silence_threshold_ms(self, partial: str) -> int:
        """How long to wait for more speech, given what has been heard so far.

        Total: never raises, whatever the recogniser emits. This runs on every
        interim transcript, so an exception here would drop the call.
        """
        try:
            return self._decide(partial)
        except Exception:
            return self._base_ms

    def _decide(self, partial: str) -> int:
        text = partial.strip()
        if not text:
            return self._max_ms  # nothing said yet; be patient

        if _TRAILING_DIGITS.search(text):
            return self._max_ms

        words = text.split()
        last = words[-1].strip(".,!?؛،۔؟")

        if last in _CONTINUATIONS:
            return self._max_ms

        if last in _TERMINAL_VERBS:
            return self._min_ms

        return self._base_ms


class BargeInDetector:
    """Suppresses false interruptions while the agent is speaking.

    The mirror image of endpointing: a cough, a car horn, or a television must
    not stop the agent mid-sentence. Requires sustained speech energy rather
    than a single frame, and does not let intermittent bursts accumulate.
    """

    def __init__(self, *, min_speech_ms: int = 150, gap_resets_ms: int = 100) -> None:
        self._min_speech_ms = min_speech_ms
        self._gap_resets_ms = gap_resets_ms
        self._run_ms = 0
        self._silence_ms = 0

    def reset(self) -> None:
        self._run_ms = 0
        self._silence_ms = 0

    def observe(self, *, is_speech: bool, frame_ms: int) -> bool:
        """Feed one VAD frame. True once the run counts as a real interruption."""
        if is_speech:
            self._run_ms += frame_ms
            self._silence_ms = 0
            return self._run_ms >= self._min_speech_ms

        self._silence_ms += frame_ms
        if self._silence_ms >= self._gap_resets_ms:
            # Repeated short bursts are background noise, not a caller trying
            # to speak. Do not let them accumulate into an interruption.
            self._run_ms = 0
        return False
