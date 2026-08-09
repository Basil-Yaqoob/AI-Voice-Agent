"""Speech-to-text provider contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["STTProvider", "Transcript"]


@dataclass(frozen=True)
class Transcript:
    """One recognition result.

    Partials matter as much as finals: speculative retrieval (TASK-07) fires on
    them while the caller is still speaking, which is what keeps retrieval off
    the critical path.
    """

    text: str
    is_final: bool
    confidence: float = 1.0
    speech_final: bool = False  # provider's own end-of-utterance signal


@runtime_checkable
class STTProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def sample_rate(self) -> int: ...

    def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keyterms: Sequence[str] = (),
    ) -> AsyncIterator[Transcript]:
        """Yield interim transcripts during speech and exactly one final per turn."""
        ...
