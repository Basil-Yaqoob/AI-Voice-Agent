"""What the caller hears when something breaks.

Voice has no error page. Silence is interpreted as a dropped call and the caller
hangs up, so **every failure must resolve to audio**.

Recovery lines are pre-rendered at build time rather than synthesised on demand.
Two reasons: they cost nothing at speech time, which matters when the thing that
just failed may be the TTS provider; and they can be rendered with ElevenLabs v3,
the best-sounding Urdu voice available, whose inability to stream is irrelevant
for fixed strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = ["RECOVERY_LINES", "Recovery", "RecoveryLine", "classify"]


class Recovery(Enum):
    """What went wrong, from the caller's point of view."""

    FILLER = "filler"  # something is slow; hold the line
    REASK = "reask"  # we did not catch that
    RETRY = "retry"  # transient failure, trying again
    HANDOFF = "handoff"  # unrecoverable; escalate to a human


@dataclass(frozen=True)
class RecoveryLine:
    kind: Recovery
    text: str
    asset: str

    @property
    def path(self) -> Path:
        return Path("assets/prerendered") / self.asset


RECOVERY_LINES: dict[Recovery, RecoveryLine] = {
    Recovery.FILLER: RecoveryLine(
        Recovery.FILLER,
        "ایک سیکنڈ...",
        "filler_short.wav",
    ),
    Recovery.REASK: RecoveryLine(
        Recovery.REASK,
        "معذرت، دوبارہ بتائیے گا؟",
        "error_retry.wav",
    ),
    Recovery.RETRY: RecoveryLine(
        Recovery.RETRY,
        "ایک لمحہ، میں دوبارہ کوشش کرتا ہوں۔",
        "error_retry_again.wav",
    ),
    Recovery.HANDOFF: RecoveryLine(
        Recovery.HANDOFF,
        "معذرت، میں آپ کو ایک colleague سے connect کر دیتا ہوں۔",
        "handoff.wav",
    ),
}


def classify(error: BaseException, *, attempt: int = 1) -> Recovery:
    """Choose a recovery line for a failure.

    Deliberately coarse. A caller does not care which vendor failed, and a
    finer taxonomy would mean more ways to pick the wrong line under pressure.
    """
    if attempt > 1:
        return Recovery.HANDOFF

    if isinstance(error, TimeoutError):
        return Recovery.RETRY

    if isinstance(error, (ConnectionError, OSError)):
        return Recovery.RETRY

    return Recovery.HANDOFF
