"""LLM provider contract.

No vendor SDK may be imported here (enforced by .importlinter). Adapters live in
``agent.providers`` and are selected by config at runtime.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

__all__ = [
    "CachedPrompt",
    "Done",
    "LLMEvent",
    "LLMProvider",
    "Message",
    "TextDelta",
    "ToolCall",
    "ToolDef",
    "Usage",
]


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class CachedPrompt:
    """A system prompt split at the cache breakpoint.

    ``prefix`` must be byte-identical across every request of a session — any
    drift silently triples the model bill. ``suffix`` carries per-turn context
    (retrieved chunks, caller state) that must never enter the cached region.
    """

    prefix: str
    suffix: str = ""


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def cache_engaged(self) -> bool:
        """False on turn two of a session means the prefix is drifting."""
        return self.cache_read_tokens > 0


@dataclass(frozen=True)
class Done:
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"


# A union, not a bare token stream: if tool calls are not distinguishable at the
# event level the sentence chunker will read one aloud to the caller.
LLMEvent = TextDelta | ToolCall | Done


@runtime_checkable
class LLMProvider(Protocol):
    """Streaming chat completion.

    Implementations must yield the first ``TextDelta`` as early as possible — the
    budget allows 300ms — and must propagate cancellation to the underlying
    connection so barge-in genuinely stops the request rather than orphaning it.
    """

    @property
    def name(self) -> str: ...

    def stream(
        self,
        messages: Sequence[Message],
        *,
        system: CachedPrompt,
        tools: Sequence[ToolDef] = (),
    ) -> AsyncIterator[LLMEvent]: ...
