"""Tools the model can call.

Anything that depends on live data — a balance, an order status, a ticket —
must be a tool call, not a fact retrieved from a document. Retrieval is for
policy and product knowledge; anything that changes hourly belongs behind an
API, or the agent will confidently quote yesterday's number.

Tool definitions are sorted by name before entering the cached prompt prefix.
A registry built from a dict can reorder between processes, and that reordering
silently invalidates the cache for every subsequent request.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent.interfaces.llm import ToolDef
from agent.telemetry.logging import get_logger

__all__ = ["ToolRegistry", "ToolResult"]

log = get_logger(__name__)

Handler = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class ToolResult:
    name: str
    content: str
    is_error: bool = False


class ToolRegistry:
    """Holds tool definitions and dispatches calls."""

    def __init__(self) -> None:
        self._defs: dict[str, ToolDef] = {}
        self._handlers: dict[str, Handler] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Handler,
    ) -> None:
        if not inspect.iscoroutinefunction(handler):
            # A synchronous handler would block the audio frame pump.
            raise TypeError(f"tool {name!r} handler must be async")
        self._defs[name] = ToolDef(name=name, description=description, input_schema=input_schema)
        self._handlers[name] = handler

    def definitions(self) -> list[ToolDef]:
        """Sorted, so the cached prompt prefix stays byte-stable."""
        return [self._defs[name] for name in sorted(self._defs)]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Invoke a tool, returning errors as results rather than raising.

        The model explains a failed lookup to the caller in Urdu far better
        than an exception handler can, so errors go back into the conversation
        instead of unwinding the turn.
        """
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(name, f"No such tool: {name}", is_error=True)

        try:
            return ToolResult(name, await handler(**arguments))
        except TypeError as exc:
            return ToolResult(name, f"Invalid arguments: {exc}", is_error=True)
        except Exception as exc:
            log.warning("tool_failed", tool=name, detail=str(exc))
            return ToolResult(name, f"Lookup failed: {exc}", is_error=True)

    def __len__(self) -> int:
        return len(self._defs)
