"""Deterministic LLM fake with a cancellation flag.

The ``cancelled`` attribute is the point of this class. Barge-in correctness means
the upstream stream was *actually* cancelled, not merely ignored — a leaked task
keeps billing and can emit audio after the caller has moved on.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from agent.interfaces.llm import (
    CachedPrompt,
    Done,
    LLMEvent,
    Message,
    TextDelta,
    ToolCall,
    ToolDef,
    Usage,
)

__all__ = ["FakeLLM", "LLMCall"]


@dataclass
class LLMCall:
    messages: list[Message]
    system: CachedPrompt
    tools: list[ToolDef] = field(default_factory=list)


class FakeLLM:
    """Replays a scripted response, token by token, at a configurable rate."""

    def __init__(
        self,
        *,
        script: Sequence[str] = ("جی بالکل۔",),
        ttft_ms: int = 0,
        tokens_per_sec: float = 200.0,
        tool_calls: Sequence[ToolCall] = (),
        usage: Usage | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self._script = list(script)
        self._ttft_ms = ttft_ms
        self._tps = tokens_per_sec
        self._tool_calls = list(tool_calls)
        self._usage = usage or Usage(input_tokens=100, output_tokens=20, cache_read_tokens=4000)
        self._fail_with = fail_with

        self.calls: list[LLMCall] = []
        self.cancelled = False
        self.completed = False

    @property
    def name(self) -> str:
        return "fake"

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: CachedPrompt,
        tools: Sequence[ToolDef] = (),
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append(LLMCall(list(messages), system, list(tools)))
        if self._fail_with is not None:
            raise self._fail_with

        index = min(len(self.calls) - 1, len(self._script) - 1)
        text = self._script[index] if self._script else ""

        try:
            if self._ttft_ms:
                await asyncio.sleep(self._ttft_ms / 1000)

            for token in _tokenize(text):
                yield TextDelta(token)
                if self._tps > 0:
                    await asyncio.sleep(1 / self._tps)

            for call in self._tool_calls:
                yield call

            self.completed = True
            yield Done(usage=self._usage, stop_reason="end_turn")
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        except GeneratorExit:
            self.cancelled = True
            raise


def _tokenize(text: str) -> list[str]:
    """Split on whitespace, keeping the space so reassembly is lossless."""
    if not text:
        return []
    parts = text.split(" ")
    return [p if i == len(parts) - 1 else p + " " for i, p in enumerate(parts)]
