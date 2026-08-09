"""Text-to-speech provider contract.

Deliberately thin. Normalising SSML, emotion tags and prosody controls across two
vendors with different feature sets would produce a lowest-common-denominator API
exposing neither vendor's strengths — worse than no abstraction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

__all__ = ["TTSProvider"]


@runtime_checkable
class TTSProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def sample_rate(self) -> int: ...

    def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        """Yield raw PCM chunks.

        First chunk must arrive inside the TTS budget (110ms). Chunks should be
        small (~20-40ms of audio): large buffers slow time-to-first-byte and make
        barge-in cancellation coarse, so the caller keeps hearing audio after
        they have interrupted.
        """
        ...
