"""Per-turn latency and cost measurement.

Not optional polish. A voice agent has five sequential stages owned by four
different vendors; without per-stage attribution a 200ms regression is an
afternoon of bisecting. And latency *will* regress.

The second reason this exists is money. If prompt caching silently stops
working the bill triples with no visible symptom, and the only way to notice is
watching ``cache_read_tokens``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

__all__ = ["TurnMetrics", "percentile"]


@dataclass
class TurnMetrics:
    """One turn's timings and token usage.

    ``time.perf_counter`` throughout, never ``time.time``: wall clock can step
    backwards on an NTP correction and produce negative durations that quietly
    poison every percentile.
    """

    turn_id: str
    call_id: str

    speech_end_at: float | None = None
    transcript_final_at: float | None = None
    first_token_at: float | None = None
    first_audio_at: float | None = None

    stages: dict[str, float] = field(default_factory=dict)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tts_characters: int = 0
    audio_seconds: float = 0.0

    interrupted: bool = False
    answer_cache_hit: bool = False
    prefetch_used: bool = False
    retrieval_calls: int = 0
    tool_calls: int = 0
    errors: list[str] = field(default_factory=list)

    model: str = ""
    tts_provider: str = ""

    def mark_speech_end(self) -> None:
        self.speech_end_at = time.perf_counter()

    def mark_transcript_final(self) -> None:
        self.transcript_final_at = time.perf_counter()

    def mark_first_token(self) -> None:
        if self.first_token_at is None:
            self.first_token_at = time.perf_counter()

    def mark_first_audio(self) -> None:
        if self.first_audio_at is None:
            self.first_audio_at = time.perf_counter()

    @property
    def first_audio_ms(self) -> float | None:
        """The headline number: end of caller speech to first syllable out.

        Anchored on speech end, not transcript-final. Anchoring on the
        transcript hides the endpointing slice — the largest tunable one — and
        flatters the figure by roughly 180ms.
        """
        return self._delta(self.speech_end_at, self.first_audio_at)

    @property
    def ttft_ms(self) -> float | None:
        return self._delta(self.transcript_final_at, self.first_token_at)

    @property
    def endpoint_wait_ms(self) -> float | None:
        return self._delta(self.speech_end_at, self.transcript_final_at)

    @staticmethod
    def _delta(start: float | None, end: float | None) -> float | None:
        if start is None or end is None:
            return None
        return (end - start) * 1000

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time a stage, recording even when it raises.

        A failing stage is exactly when its duration is most interesting.
        """
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = (time.perf_counter() - started) * 1000

    def as_log(self) -> dict[str, object]:
        """One structured line per turn.

        ``model`` and ``tts_provider`` are included on every line so latency can
        be sliced by which vendor actually served the turn — necessary when
        A/B-ing providers or after a mid-call TTS failover.
        """
        return {
            "event": "turn_complete",
            "call_id": self.call_id,
            "turn_id": self.turn_id,
            "first_audio_ms": _round(self.first_audio_ms),
            "ttft_ms": _round(self.ttft_ms),
            "endpoint_wait_ms": _round(self.endpoint_wait_ms),
            "stages": {k: round(v, 1) for k, v in self.stages.items()},
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "tts_characters": self.tts_characters,
            "interrupted": self.interrupted,
            "answer_cache_hit": self.answer_cache_hit,
            "prefetch_used": self.prefetch_used,
            "retrieval_calls": self.retrieval_calls,
            "tool_calls": self.tool_calls,
            "errors": self.errors,
            "model": self.model,
            "tts_provider": self.tts_provider,
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile.

    Averages hide the tail, and the tail is where callers live: a p50 of 600ms
    with a p95 of 2.4s is a bad agent that looks fine on a mean.
    """
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    rank = math.ceil(p / 100 * len(ordered))
    index = max(0, min(len(ordered) - 1, rank - 1))
    return ordered[index]
