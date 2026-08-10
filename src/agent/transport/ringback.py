"""A ringing tone for the gap between answering and speaking.

The agent does not greet the instant the call connects. The far end has to
finish subscribing to the track (about a second), and on the pipeline path the
greeting still has to be synthesised — Uplift's time-to-first-byte alone is
1.5s. Until the first syllable arrives the caller hears nothing at all, which
on a phone line is indistinguishable from a call that failed to connect. Two
or three seconds of that is enough for someone to hang up and try again.

So fill it with the one sound that means "this is working, wait" — the tone the
caller was already expecting to hear before anyone picked up.

Two things this must not do:

**It must not delay the greeting.** The tone is generated locally and stopped
the moment real audio exists, and stopping clears the sink queue so the
greeting does not start behind a queue's worth of leftover tone.

**It must not outlive its reason.** ``_MAX_S`` caps it, so a greeting that
never arrives leaves the caller with silence and a dead line rather than a
phone ringing forever at someone who has already been answered.
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import AsyncIterator
from functools import cache
from typing import Any

from agent.telemetry.logging import get_logger

__all__ = ["Ringback"]

log = get_logger(__name__)

# Single 400Hz tone on a 1s-on / 2s-off cadence — what a Pakistani caller
# hears from PTCL and the mobile networks. A dual-frequency tone would be more
# faithful to some exchanges, but this is being played *after* the call is
# answered and it only has to read as "ringing", not fool anyone.
_FREQ_HZ = 400.0
_ON_S = 1.0
_OFF_S = 2.0

# Quiet on purpose. This is a pure sine straight into the caller's ear with no
# handset shaping in front of it, and at full scale that is unpleasant.
_AMPLITUDE = 0.16

# Never ring longer than a caller would plausibly hold. If the greeting has not
# arrived by now something is broken, and a tone that continues forever hides
# it — the demo would look like it is still connecting when it has failed.
_MAX_S = 20.0


class Ringback:
    """Plays a ringing tone into a sink until told to stop.

    Holds the sink's playback lock for as long as it rings, so ``stop()`` must
    be awaited before anything else tries to speak. Every caller here does that
    already — the tone exists precisely to cover the wait for that audio.
    """

    def __init__(self, sink: Any) -> None:
        self._sink = sink
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_ringing(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Begin ringing. Cheap, synchronous, and safe to call twice."""
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._ring())

    async def stop(self) -> None:
        """Silence the tone and release the sink. Safe to call when not ringing.

        Clearing the sink queue matters: the tone is paced against real playout,
        so up to a queue's worth of it is already buffered ahead of the caller's
        ear when the greeting becomes ready. Without the clear the greeting
        starts a fifth of a second late, behind a fragment of ring.
        """
        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        await asyncio.gather(task, return_exceptions=True)
        await self._sink.stop()

    async def _ring(self) -> None:
        """Playback failing here must never take the call with it.

        The tone is a courtesy. If the sink refuses it, the caller waits in
        silence exactly as they did before this existed — which is worse, but
        it is not a dropped call.
        """
        try:
            await self._sink.play(self._frames())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("ringback_failed", detail=str(exc)[:200])

    async def _frames(self) -> AsyncIterator[bytes]:
        """One cadence, looped, a frame at a time.

        Checked against the stop event every frame rather than every cadence:
        the greeting can become ready during the 2s silence, and a caller who
        waits two seconds for a reply that was already rendered would be worse
        off than with no tone at all.
        """
        loop = asyncio.get_running_loop()
        cadence = _cadence(self._sink.sample_rate)
        size = self._sink.frame_bytes
        deadline = loop.time() + _MAX_S
        at = 0

        while not self._stop.is_set() and loop.time() < deadline:
            # The sink paces itself against real playout, so this loop normally
            # blocks on the frame it just handed over. Yielding anyway means
            # the tone cannot monopolise the loop if it is ever pointed at a
            # sink that accepts frames instantly — which is what a fake does.
            await asyncio.sleep(0)

            end = at + size
            if end <= len(cadence):
                yield cadence[at:end]
            else:
                # The cadence divides evenly into 20ms frames at every rate the
                # providers use, so this is defensive rather than load-bearing.
                # It stays because a frame stitched from a wrapped slice is
                # still a whole number of samples, and a short one is not.
                wrapped = end - len(cadence)
                yield cadence[at:] + cadence[:wrapped]
                end = wrapped
            at = end % len(cadence)


@cache
def _cadence(sample_rate: int) -> bytes:
    """One full ring-and-pause as 16-bit signed mono PCM.

    Cached, not built per call. Three seconds at 24kHz is 72,000 sine
    evaluations, and this runs on the event loop at the moment a call connects
    — the one moment already under budget pressure. There are at most three
    sample rates in the process and the buffer is 144KB each.

    The tone holds a whole number of cycles — 400Hz for exactly one second — so
    the loop point lands on a zero crossing and there is no click where the
    cadence repeats.
    """
    on = int(sample_rate * _ON_S)
    off = int(sample_rate * _OFF_S)
    peak = _AMPLITUDE * 32767.0
    step = 2.0 * math.pi * _FREQ_HZ / sample_rate

    samples = [int(peak * math.sin(step * n)) for n in range(on)]
    samples.extend([0] * off)
    return struct.pack(f"<{len(samples)}h", *samples)
