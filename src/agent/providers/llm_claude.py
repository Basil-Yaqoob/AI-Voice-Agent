"""Anthropic API adapter. The only module permitted to import ``anthropic``."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import anthropic

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

__all__ = ["ClaudeProvider"]

# Opus 5 fast mode: up to 2.5x output throughput at $10/$50 per MTok. The only
# lever in the whole stack that converts money directly into latency headroom.
_FAST_MODE_BETA = "fast-mode-2026-02-01"


class ClaudeProvider:
    """Streaming chat completion against the Anthropic API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        effort: str = "low",
        max_tokens: int = 400,
        fast_mode: bool = False,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._fast_mode = fast_mode

    @property
    def name(self) -> str:
        return "claude"

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: CachedPrompt,
        tools: Sequence[ToolDef] = (),
    ) -> AsyncIterator[LLMEvent]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": _system_blocks(system),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            # Adaptive thinking at low effort. Do NOT set {"type": "disabled"} to
            # save latency: on Opus 5 that makes the model occasionally write a
            # tool call into visible text instead of emitting a tool_use block.
            # The turn completes, no error is raised, and the tool never runs —
            # so the caller is told their balance was checked when it wasn't.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort},
        }
        if tools:
            kwargs["tools"] = [_tool_payload(t) for t in tools]
        if self._fast_mode:
            kwargs["speed"] = "fast"
            kwargs["betas"] = [_FAST_MODE_BETA]

        create = (
            self._client.beta.messages.stream if self._fast_mode else self._client.messages.stream
        )

        # `async with` guarantees the HTTP connection is closed when the consuming
        # task is cancelled on barge-in. An orphaned stream keeps billing and can
        # emit audio into the next turn.
        async with create(**kwargs) as stream:
            async for event in stream:
                # The SDK event type is a 23-member union; narrowing it properly
                # would mean enumerating every server-tool and thinking variant
                # we do not handle. Duck-typing the two fields we care about is
                # both shorter and more robust to the SDK adding members.
                text = _text_delta_of(event)
                if text:
                    yield TextDelta(text)

            final = await stream.get_final_message()

        for block in final.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            yield ToolCall(
                id=str(getattr(block, "id", "")),
                name=str(getattr(block, "name", "")),
                arguments=dict(getattr(block, "input", {}) or {}),
            )

        yield Done(usage=_usage(final.usage), stop_reason=final.stop_reason or "end_turn")


def _text_delta_of(event: Any) -> str | None:
    """Extract visible text from a stream event, or None if it carries none.

    Thinking deltas are deliberately excluded: they must never reach TTS.
    """
    if getattr(event, "type", None) != "content_block_delta":
        return None
    delta = getattr(event, "delta", None)
    if getattr(delta, "type", None) != "text_delta":
        return None
    text = getattr(delta, "text", "")
    return text or None


def _system_blocks(system: CachedPrompt) -> list[dict[str, Any]]:
    """Prefix carries the cache_control breakpoint; suffix stays outside it."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system.prefix,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if system.suffix:
        blocks.append({"type": "text", "text": system.suffix})
    return blocks


def _tool_payload(tool: ToolDef) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _usage(raw: Any) -> Usage:
    return Usage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
    )
