"""Media transport: the LiveKit room, its audio tracks, and the worker.

Separate from ``agent.providers`` on purpose. Those adapters wrap AI vendors
that were chosen for what they can do; this wraps the pipe the audio travels
down, and it is the only part of the system that knows a room or a participant
exists. The pipeline underneath speaks in ``bytes`` and knows nothing about
either.

``livekit`` may be imported here and in ``agent.main``, nowhere else — an
import-linter contract enforces it.
"""

from __future__ import annotations

__all__ = ["LiveKitSink", "entrypoint"]


def __getattr__(name: str) -> object:
    # Imported lazily so `agent.transport` stays importable in test
    # environments without the LiveKit SDK, and so pulling in the sink does not
    # drag in the whole agents framework.
    if name == "LiveKitSink":
        from agent.transport.sink import LiveKitSink

        return LiveKitSink
    if name == "entrypoint":
        from agent.transport.worker import entrypoint

        return entrypoint
    raise AttributeError(name)
