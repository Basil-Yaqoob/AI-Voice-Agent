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

**PCM is re-cut into fixed 20ms frames, whatever size it arrives in.** This is
not tidiness. ``AudioSource.capture_frame`` rejects a frame that cannot fit
alongside what is already queued, and it reports that as ``RtcError
InvalidState - failed to capture frame`` — which surfaces as a failed turn with
nothing in it pointing at audio. Azure and the realtime API happen to yield
20-40ms chunks, so pushing each chunk as one frame worked by luck; Uplift
streams the raw HTTP body, whose chunks are tens of kilobytes, and the *second*
one is rejected while the first is still playing. Re-cutting removes the
dependency on how a vendor happens to flush its socket.

Fixed frames also make ``capture_frame`` pace itself against real playout,
which is what keeps ``is_playing`` honest and barge-in accurate to one frame.
"""

from __future__ import annotations

import asyncio
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

# One frame of audio. 20ms is the usual WebRTC unit and comfortably below the
# queue, so a frame always has somewhere to go; it is also the granularity at
# which barge-in can cut, which is why it is not larger.
_FRAME_MS = 20


class LiveKitSink:
    """Publishes one audio track and writes PCM into it."""

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._source = rtc.AudioSource(
            sample_rate=sample_rate, num_channels=_CHANNELS, queue_size_ms=_QUEUE_MS
        )
        self._track = rtc.LocalAudioTrack.create_audio_track("agent", self._source)
        self._playing = False
        # One utterance at a time. Two coroutines pushing into one AudioSource
        # — the opening greeting and the first reply, say — corrupts it and
        # raises "InvalidState - failed to capture frame", which surfaces as a
        # failed turn rather than as anything to do with audio.
        self._lock = asyncio.Lock()
        self._frame_bytes = int(sample_rate * _FRAME_MS / 1000) * _BYTES_PER_SAMPLE * _CHANNELS

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
        async with self._lock:
            self._playing = True
            # Local, not instance state: an utterance that ends mid-frame must
            # not prepend its tail to the next one, which would arrive a
            # sentence late and one byte out of phase.
            pending = bytearray()
            try:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    pending += chunk
                    sent = 0
                    while len(pending) - sent >= self._frame_bytes:
                        end = sent + self._frame_bytes
                        await self._source.capture_frame(self._frame(pending[sent:end]))
                        sent = end
                    # One trim per chunk rather than one per frame: repeated
                    # slicing of a 64KB buffer is quadratic, on the hot path.
                    del pending[:sent]
                # The tail is shorter than a frame but still real audio — the
                # last syllable of the sentence. A chunk boundary can land
                # inside a sample, so any stray odd byte goes rather than
                # inverting the sign of the frame it would have started.
                tail = bytes(pending[: len(pending) - (len(pending) % _BYTES_PER_SAMPLE)])
                if tail:
                    await self._source.capture_frame(self._frame(tail))
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

    @property
    def frame_bytes(self) -> int:
        return self._frame_bytes

    def _frame(self, pcm: bytes | bytearray) -> rtc.AudioFrame:
        pcm = bytes(pcm)
        return rtc.AudioFrame(
            data=pcm,
            sample_rate=self._sample_rate,
            num_channels=_CHANNELS,
            samples_per_channel=len(pcm) // (_BYTES_PER_SAMPLE * _CHANNELS),
        )
