"""One conversational turn, end to end.

The hot path proper. Two behaviours here are subtle and are the difference
between an agent that feels real and one that does not:

* **Barge-in records what was *spoken*, not what was intended.** Storing the
  full generated response makes the agent later reference information the
  caller never heard — "as I mentioned, your refund is processing" when it was
  cut off before saying so.
* **Audio stops before the task is cancelled.** Cancelling first leaves
  buffered audio playing, so the caller keeps hearing the agent after
  interrupting, which feels broken.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from agent.interfaces.llm import (
    CachedPrompt,
    Done,
    LLMEvent,
    LLMProvider,
    Message,
    ToolCall,
    ToolDef,
    Usage,
)
from agent.language.normalize import normalize_for_tts
from agent.pipeline.chunker import chunk_sentences
from agent.telemetry.logging import get_logger

__all__ = ["AudioSink", "TurnResult", "TurnRunner"]

log = get_logger(__name__)


class AudioSink(Protocol):
    """Where synthesised audio goes. Implemented by the LiveKit track in TASK-10."""

    async def play(self, chunks: AsyncIterator[bytes]) -> None: ...

    async def stop(self) -> None:
        """Drop anything buffered. Must take effect within ~100ms."""
        ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]: ...


@dataclass
class TurnResult:
    spoken: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    interrupted: bool = False
    completed: bool = False


class TurnRunner:
    """Executes one turn: prompt -> tokens -> sentences -> audio."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        tts: SpeechSynthesizer,
        sink: AudioSink,
        voice: str,
        tools: Sequence[ToolDef] = (),
    ) -> None:
        self._llm = llm
        self._tts = tts
        self._sink = sink
        self._voice = voice
        self._tools = list(tools)

        # Held on the instance, not just returned. On cancellation run() raises
        # and the return value is lost, so a caller reacting to barge-in would
        # never learn what the caller actually heard — which is precisely the
        # thing the session must record.
        self.result = TurnResult()

    async def run(
        self,
        messages: Sequence[Message],
        *,
        system: CachedPrompt,
    ) -> TurnResult:
        """Generate and speak one response.

        On cancellation the result is still accurate: ``spoken`` holds exactly
        what reached the caller, so the session can record truthful history.
        """
        result = self.result = TurnResult()
        spoken: list[str] = []

        try:
            stream = self._llm.stream(messages, system=system, tools=self._tools)
            async for sentence in chunk_sentences(_capture(stream, result)):
                # Normalisation happens per sentence, after chunking: the
                # chunker needs the raw text to find boundaries, and the
                # normaliser needs whole sentences to read numbers correctly.
                await self._sink.play(
                    self._tts.synthesize(normalize_for_tts(sentence), voice=self._voice)
                )
                spoken.append(sentence)
                # Updated per sentence, so the record stays accurate even if the
                # caller interrupts between two of them.
                result.spoken = " ".join(spoken)

            result.completed = True
        except asyncio.CancelledError:
            result.interrupted = True
            raise
        finally:
            # Runs on the cancellation path too, which is the entire point.
            result.spoken = " ".join(spoken)

        return result


async def _capture(
    events: AsyncIterator[LLMEvent],
    result: TurnResult,
) -> AsyncIterator[LLMEvent]:
    """Pass events through, recording tool calls and usage on the way.

    The chunker only cares about text, but the turn needs the rest, and tapping
    the stream avoids consuming it twice.
    """
    async for event in events:
        if isinstance(event, ToolCall):
            result.tool_calls.append(event)
        elif isinstance(event, Done):
            result.usage = event.usage
        yield event
