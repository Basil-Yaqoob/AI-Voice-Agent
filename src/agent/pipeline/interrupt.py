"""Barge-in.

Ordering is the whole substance of this module. When the caller starts speaking
while the agent is talking, three things must happen inside ~100ms **in this
order**:

1. stop pushing audio — do this *first*, because cancelling the task while
   audio is still buffered leaves the caller hearing an agent they have already
   interrupted;
2. cancel the turn, propagating all the way to the provider connections, since
   an orphaned LLM stream keeps billing and an orphaned TTS stream can emit
   audio into the *next* turn;
3. record what was actually spoken, which :class:`~agent.pipeline.turn.TurnRunner`
   guarantees on its cancellation path.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from agent.pipeline.turn import AudioSink, TurnResult, TurnRunner
from agent.telemetry.logging import get_logger

__all__ = ["InterruptController"]

log = get_logger(__name__)


class InterruptController:
    """Owns the in-flight turn task and cancels it cleanly."""

    def __init__(self, sink: AudioSink) -> None:
        self._sink = sink
        self._task: asyncio.Task[TurnResult] | None = None
        self._runner: TurnRunner | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def track(self, task: asyncio.Task[TurnResult], runner: TurnRunner | None = None) -> None:
        """Follow a running turn.

        ``runner`` is how the spoken record survives cancellation: a cancelled
        task has no return value, so the partial result has to be read off the
        runner instead.
        """
        self._task = task
        self._runner = runner

    async def interrupt(self) -> TurnResult | None:
        """Stop the current turn. Returns what the caller actually heard."""
        if self._task is None:
            return None

        # 1. Audio first. Anything already buffered would otherwise keep playing.
        await self._sink.stop()

        task, self._task = self._task, None

        if task.done():
            # Finished between the caller speaking and us reacting.
            with suppress(asyncio.CancelledError, Exception):
                return task.result()
            return None

        # 2. Cancel and wait, so provider connections are actually closed rather
        # than left to a garbage collector that may never run.
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        # A cancelled task has no result, so read the partial record off the
        # runner. This is what the session stores as history — what the caller
        # actually heard, not what the model intended to say.
        partial = self._runner.result if self._runner is not None else None
        self._runner = None
        if partial is not None:
            partial.interrupted = True
        log.info("barge_in", spoken_chars=len(partial.spoken) if partial else 0)
        return partial

    async def finish(self) -> TurnResult | None:
        """Await normal completion of the turn, if one is running."""
        if self._task is None:
            return None
        task, self._task = self._task, None
        self._runner = None
        return await task
