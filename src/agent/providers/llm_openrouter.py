"""OpenRouter adapter. The only module permitted to import ``openai``.

OpenRouter speaks the OpenAI chat-completions protocol, so this cannot reuse the
Anthropic client. Three differences from :mod:`agent.providers.llm_claude` matter:

* Fast mode is a **separate model id** (``anthropic/claude-opus-5-fast``), not a
  request parameter. ``Settings.resolved_llm_model`` handles that swap.
* Prompt caching needs an explicit ``cache_control`` breakpoint inside a
  multipart system message; OpenRouter forwards it to Anthropic.
* Streaming usage only arrives if ``stream_options.include_usage`` is set. Without
  it ``cache_read_tokens`` is always zero and the cache alarm misfires.

The proxy hop costs roughly 50-200ms against a 300ms TTFT budget. Benchmark this
against the direct provider in TASK-12 before choosing it for production.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import AsyncOpenAI

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

__all__ = ["OpenRouterProvider"]


class OpenRouterProvider:
    """Streaming chat completion via OpenRouter's OpenAI-compatible API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        max_tokens: int = 400,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "openrouter"

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: CachedPrompt,
        tools: Sequence[ToolDef] = (),
    ) -> AsyncIterator[LLMEvent]:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": _system_content(system)},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
            "stream": True,
            # Without this, streaming responses carry no usage block at all and
            # cache_read_tokens is permanently zero.
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [_tool_payload(t) for t in tools]

        # Accumulated across deltas: OpenAI-protocol tool calls arrive in
        # fragments keyed by index, with arguments streamed as partial JSON.
        pending: dict[int, dict[str, str]] = {}
        usage = Usage()
        stop_reason = "end_turn"

        stream = await self._client.chat.completions.create(**payload)
        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = _usage(chunk.usage)
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                if choice.finish_reason:
                    stop_reason = choice.finish_reason

                delta = choice.delta
                if delta is None:
                    continue
                if delta.content:
                    yield TextDelta(delta.content)
                for fragment in delta.tool_calls or []:
                    slot = pending.setdefault(fragment.index, {"id": "", "name": "", "args": ""})
                    if fragment.id:
                        slot["id"] = fragment.id
                    if fragment.function and fragment.function.name:
                        slot["name"] = fragment.function.name
                    if fragment.function and fragment.function.arguments:
                        slot["args"] += fragment.function.arguments
        finally:
            # Propagates cancellation to the socket on barge-in rather than
            # leaving the request running and billing.
            await stream.close()

        for slot in pending.values():
            if slot["name"]:
                yield ToolCall(
                    id=slot["id"], name=slot["name"], arguments=_parse_args(slot["args"])
                )

        yield Done(usage=usage, stop_reason=stop_reason)


def _system_content(system: CachedPrompt) -> list[dict[str, Any]]:
    """Multipart system message with the cache breakpoint on the prefix only."""
    parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system.prefix,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if system.suffix:
        parts.append({"type": "text", "text": system.suffix})
    return parts


def _tool_payload(tool: ToolDef) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _parse_args(raw: str) -> dict[str, object]:
    """Tool arguments stream as partial JSON and can arrive malformed.

    A raising parse here would drop the whole turn, so surface the fragment to
    the model as an error instead — it can then ask the caller to rephrase.
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_malformed_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _usage(raw: Any) -> Usage:
    """Both cache counters live under ``prompt_tokens_details``.

    Reading only ``cached_tokens`` makes the first turn of every session
    indistinguishable from a broken cache: the write shows up as zero, the read
    is legitimately zero, and the log says the prefix never cached. Verified
    against OpenRouter — a 6,403-token prefix reports ``cache_write_tokens`` on
    the first call and ``cached_tokens`` on the second, and the cost drops
    twelvefold between them.
    """
    details = getattr(raw, "prompt_tokens_details", None)
    return Usage(
        input_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        output_tokens=getattr(raw, "completion_tokens", 0) or 0,
        cache_read_tokens=getattr(details, "cached_tokens", 0) or 0,
        cache_write_tokens=getattr(details, "cache_write_tokens", 0) or 0,
    )
