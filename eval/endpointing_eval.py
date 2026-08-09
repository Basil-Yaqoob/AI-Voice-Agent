"""Measure endpointing against real recorded calls.

    uv run python eval/endpointing_eval.py

⚠️ **Blocked on a recording session.** Deferred 10 Aug 2026 with client sign-off:
no Pakistani call audio exists yet. The harness is written so the measurement is
a single command once the corpus arrives, and so the requirement cannot quietly
be forgotten.

Until this runs, treat the 180ms endpointing slice of the latency budget as an
estimate rather than a measurement, and expect the cut-off rate to be the reason
a demo lands badly despite good latency figures.

What the corpus needs — from TASK-09:

* 30-50 utterances recorded **over an actual phone line**, not a laptop
  microphone. Codec compression and packet loss change VAD behaviour, and
  thresholds tuned on clean audio do not transfer.
* Deliberate coverage: mid-sentence hesitation, pauses before numbers
  ("میرا آرڈر نمبر ہے... چار چار سات ایک"), fast speakers, speakerphone,
  street noise, and trailing fillers (اممم, اچھا).
* A ``labels.yaml`` giving each clip's true end-of-speech timestamp.

Acceptance targets: **cut-off rate below 3%**, median added latency <= 180ms.
Record a baseline *before* tuning; without one, threshold changes are
superstition.
"""

from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent.pipeline.endpointing import AdaptiveEndpointer

CORPUS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "audio"
LABELS = CORPUS / "labels.yaml"

MAX_CUTOFF_RATE = 0.03
MAX_MEDIAN_ADDED_MS = 180


@dataclass(frozen=True)
class Labelled:
    clip: str
    transcript_partials: list[str]
    true_end_ms: int


def load_labels() -> list[Labelled]:
    if not LABELS.exists():
        return []
    data = yaml.safe_load(LABELS.read_text(encoding="utf-8")) or {}
    return [Labelled(**entry) for entry in data.get("clips", [])]


def evaluate(clips: list[Labelled]) -> tuple[float, float]:
    """Return (cut-off rate, median added latency ms).

    Replays the labelled partials through the endpointer rather than the audio
    itself: the acoustic VAD is the provider's, and what is being validated
    here is *our* threshold policy on top of it.
    """
    endpointer = AdaptiveEndpointer()
    cut_offs = 0
    added: list[float] = []

    for clip in clips:
        # Every partial before the last represents speech still in progress;
        # a threshold shorter than the gap that follows would cut the caller off.
        for partial in clip.transcript_partials[:-1]:
            if endpointer.silence_threshold_ms(partial) <= AdaptiveEndpointer.MIN_MS:
                cut_offs += 1
                break
        added.append(endpointer.silence_threshold_ms(clip.transcript_partials[-1]))

    return cut_offs / len(clips), statistics.median(added)


def main() -> int:
    clips = load_labels()
    if not clips:
        print(__doc__)
        print(f"\nNo labelled corpus at {LABELS}. Nothing measured.")
        return 1

    rate, median_ms = evaluate(clips)
    print(f"clips:              {len(clips)}")
    print(f"cut-off rate:       {rate:.1%}  (target < {MAX_CUTOFF_RATE:.0%})")
    print(f"median added wait:  {median_ms:.0f}ms  (target <= {MAX_MEDIAN_ADDED_MS}ms)")

    passed = rate < MAX_CUTOFF_RATE and median_ms <= MAX_MEDIAN_ADDED_MS
    print("\nPASS" if passed else "\nFAIL - tune the thresholds in endpointing.py")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
