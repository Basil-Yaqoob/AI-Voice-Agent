"""The audio sink that puts the agent's voice into the room.

Implements the ``AudioSink`` protocol that ``TurnRunner`` speaks to, so the
pipeline stays unaware of LiveKit. Two things here are worth more attention
than the rest of the file.

**Barge-in has to drop queued audio, not stop sending it.** ``AudioSource``
holds an internal jitter buffer — by default a full second of it. Simply
ceasing to call ``capture_frame`` leaves whatever is already queued to play
out, so the caller carries on hearing an agent they interrupted a second ago,
which is the exact failure barge-in exists to prevent. ``clear_queue()`` is
what actually silences the line, and it is the first thing ``stop()`` does.

**Frames are pushed at their natural size, not buffered up.** The TTS adapter
already yields 20-40ms chunks for a reason: a larger buffer here would improve
nothing and make cancellation coarser, because the smallest unit that can be
dropped is one frame.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from livekit import rtc

from agent.telemetry.logging import get_logger

__all__ = ["LiveKitSink"]

log = get_logger(__name__)

# 16-bit signed mono. Both Azure and Inworld emit this; LiveKit wants it too,
# so nothing on the output path needs converting.
_BYTES_PER_SAMPLE = 2
_CHANNELS = 1

# The source's jitter buffer. Small because everything upstream streams: a big
# buffer only adds latency between clear_queue() and actual silence.
_QUEUE_MS = 200


class LiveKitSink:
    """Publishes one audio track and writes PCM into it."""

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._source = rtc.AudioSource(
            sample_rate=sample_rate, num_channels=_CHANNELS, queue_size_ms=_QUEUE_MS
        )
        self._track = rtc.LocalAudioTrack.create_audio_track("agent", self._source)
        self._playing = False
        # Odd byte counts cannot happen mid-stream, but a chunk boundary can
        # land inside a sample. Carry the stray byte to the next frame rather
        # than dropping it, which would invert the sign of everything after.
        self._remainder = b""

    @property
    def track(self) -> rtc.LocalAudioTrack:
        return self._track

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def publish(self, room: rtc.Room) -> None:
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await room.local_participant.publish_track(self._track, options)

    async def play(self, chunks: AsyncIterator[bytes]) -> None:
        """Stream PCM into the room until the source is exhausted or cancelled."""
        self._playing = True
        self._remainder = b""
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                data = self._remainder + chunk
                usable = len(data) - (len(data) % _BYTES_PER_SAMPLE)
                self._remainder = data[usable:]
                if usable:
                    await self._source.capture_frame(self._frame(data[:usable]))
        finally:
            self._playing = False

    async def stop(self) -> None:
        """Silence the line now. Called on barge-in, so it must be immediate."""
        self._source.clear_queue()

    async def aclose(self) -> None:
        await self._source.aclose()

    @property
    def is_playing(self) -> bool:
        return self._playing

    def _frame(self, pcm: bytes) -> rtc.AudioFrame:
        return rtc.AudioFrame(
            data=pcm,
            sample_rate=self._sample_rate,
            num_channels=_CHANNELS,
            samples_per_channel=len(pcm) // (_BYTES_PER_SAMPLE * _CHANNELS),
        )
