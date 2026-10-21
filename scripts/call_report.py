"""Print one call's latency story from logs/calls.jsonl in a readable shape.

    uv run python scripts/call_report.py                  # the most recent call
    uv run python scripts/call_report.py demo-a1b2c3d4     # by room
    uv run python scripts/call_report.py call_9f8e7d6c     # by call_id

The log has three kinds of line for one call, written by two different
processes and correlated by ``room`` (the browser never sees ``call_id`` — it
only knows the room LiveKit gave it):

* ``call_timeline``  — the worker's own setup checkpoints, elapsed ms since
  its entrypoint started (dispatch, participant join, track subscription,
  greeting).
* ``client_timeline`` — the browser's checkpoints, elapsed ms since the
  Connect button was pressed. This is the only place the pre-dispatch gap
  (token fetch, room negotiation) is visible at all.
* ``turn_complete`` — one line per caller turn, same shape on both paths
  (``tts_provider`` reads ``"realtime"`` on the realtime path).

Nothing here is exact cross-process alignment — that would need clock sync
this project has no reason to build. It is exact *within* each source, which
is what answers "which of the eight setup steps ate the five seconds".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_LOG = Path("logs/calls.jsonl")


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"no log file at {path} — has a call been made since LOG_FILE was set?")
        raise SystemExit(1)
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.loads(line))
        except ValueError:
            continue  # a line another process wrote mid-write; skip rather than crash
    return lines


def _matches(entry: dict[str, Any], needle: str) -> bool:
    return needle in str(entry.get("room", "")) or needle in str(entry.get("call_id", ""))


def _most_recent_room(lines: list[dict[str, Any]]) -> str | None:
    for entry in reversed(lines):
        room = entry.get("room")
        if room:
            return str(room)
    return None


def _print_marks(title: str, marks: list[dict[str, Any]]) -> None:
    if not marks:
        return
    print(f"\n{title}")
    print("-" * len(title))
    for m in marks:
        print(f"  {m['at_ms']:>8.1f}ms  {m['label']}")


def _print_turns(turns: list[dict[str, Any]]) -> None:
    if not turns:
        return
    print("\nTurns")
    print("-----")
    for t in turns:
        flag = " (interrupted)" if t.get("interrupted") else ""
        print(
            f"  {t.get('turn_id', '?'):<10} first_audio={t.get('first_audio_ms')}ms"
            f"  endpoint_wait={t.get('endpoint_wait_ms')}ms"
            f"  ttft={t.get('ttft_ms')}ms"
            f"  tools={t.get('tool_calls', 0)}"
            f"  provider={t.get('tts_provider', '?')}{flag}"
        )


def main() -> int:
    path = DEFAULT_LOG
    needle = sys.argv[1] if len(sys.argv) > 1 else None

    lines = _load(path)
    if not lines:
        print(f"{path} is empty — has a call been made since LOG_FILE was set?")
        return 1

    if needle is None:
        needle = _most_recent_room(lines)
        if needle is None:
            print("no call with a room in the log yet")
            return 1

    hits = [e for e in lines if _matches(e, needle)]
    if not hits:
        print(f"nothing in {path} matches {needle!r}")
        return 1

    # turn_complete carries call_id but not room; client_timeline carries room
    # but not call_id. Only call_timeline has both, so resolve through it —
    # otherwise a search by room silently drops every turn line, and a search
    # by call_id silently drops the browser timeline.
    call_id = next((e["call_id"] for e in hits if e.get("call_id")), None)
    room = next((e["room"] for e in hits if e.get("room")), needle)
    matched = [
        e
        for e in lines
        if (call_id and e.get("call_id") == call_id) or (room and e.get("room") == room)
    ]
    print(f"Call {call_id or '(unknown)'}  room={room}")

    timeline = next((e for e in matched if e.get("event") == "call_timeline"), None)
    client = next((e for e in matched if e.get("event") == "client_timeline"), None)
    turns = [e for e in matched if e.get("event") == "turn_complete"]

    if client is not None:
        _print_marks("Browser (since Connect pressed)", client.get("marks", []))
    if timeline is not None:
        _print_marks(
            f"Worker (since job dispatched, total {timeline.get('total_ms')}ms)",
            timeline.get("marks", []),
        )
    _print_turns(turns)

    if timeline is None and client is None and not turns:
        print("matched lines carried none of the timing events — is this an old log entry?")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
