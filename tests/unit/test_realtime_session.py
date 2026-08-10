"""The speech-to-speech session.

No socket here: the interesting behaviour is what the session does with the
events it receives, and that is testable by feeding it the JSON the API sends.
The two that matter are barge-in dropping queued audio, and tool calls being
answered and then explicitly resumed.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from agent.tools.registry import ToolRegistry
from agent.transport.realtime import SAMPLE_RATE, RealtimeSession


class _Sink:
    def __init__(self) -> None:
        self.stops = 0
        self.played: list[bytes] = []

    async def play(self, chunks: Any) -> None:
        async for chunk in chunks:
            self.played.append(chunk)

    async def stop(self) -> None:
        self.stops += 1


class _WS:
    """Replays a scripted server transcript and records what we send."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        async def gen():
            for event in self._events:
                yield json.dumps(event)

        return gen()


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def check_stock(medicine: str = "") -> str:
        return f"{medicine} is in stock at Bahadurabad."

    registry.register(
        "check_stock",
        "Check stock",
        {"type": "object", "properties": {"medicine": {"type": "string"}}},
        check_stock,
    )
    return registry


def _session(ws: _WS, sink: _Sink, published: list[dict[str, Any]]) -> RealtimeSession:
    async def publish(payload: dict[str, Any]) -> None:
        published.append(payload)

    session = RealtimeSession(
        api_key="k",
        model="gpt-realtime",
        voice="cedar",
        instructions="be helpful",
        tools=_registry(),
        sink=sink,
        publish=publish,
    )
    session._ws = ws
    return session


def _audio(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


async def test_session_config_declares_tools_and_voice() -> None:
    ws = _WS([])
    session = _session(ws, _Sink(), [])
    await session._configure()

    sent = ws.sent[0]["session"]
    assert sent["audio"]["output"]["voice"] == "cedar"
    assert sent["audio"]["output"]["format"]["rate"] == SAMPLE_RATE
    assert [t["name"] for t in sent["tools"]] == ["check_stock"]
    assert sent["audio"]["input"]["turn_detection"]["type"] == "server_vad"


async def test_endpointing_is_not_the_180ms_that_cut_callers_off() -> None:
    ws = _WS([])
    await _session(ws, _Sink(), [])._configure()
    silence = ws.sent[0]["session"]["audio"]["input"]["turn_detection"][
        "silence_duration_ms"
    ]
    assert silence >= 400, "a pause before a phone number is not end of turn"


async def test_audio_deltas_reach_the_sink() -> None:
    ws = _WS([{"type": "response.output_audio.delta", "delta": _audio(b"\x01\x02")}])
    sink = _Sink()
    session = _session(ws, sink, [])
    player = asyncio.create_task(session._play())
    await session._receive()
    await session._audio_out.put(None)
    await player
    assert sink.played == [b"\x01\x02"]


async def test_the_old_event_name_still_works() -> None:
    """The GA rename moved response.audio.delta to response.output_audio.delta."""
    ws = _WS([{"type": "response.audio.delta", "delta": _audio(b"\x03\x04")}])
    sink = _Sink()
    session = _session(ws, sink, [])
    player = asyncio.create_task(session._play())
    await session._receive()
    await session._audio_out.put(None)
    await player
    assert sink.played == [b"\x03\x04"]


async def test_barge_in_drops_queued_audio_and_silences_the_line() -> None:
    """Stopping the supply is not enough; the queue would keep playing."""
    ws = _WS(
        [
            {"type": "response.output_audio.delta", "delta": _audio(b"\x01" * 100)},
            {"type": "response.output_audio.delta", "delta": _audio(b"\x02" * 100)},
            {"type": "input_audio_buffer.speech_started"},
        ]
    )
    sink = _Sink()
    session = _session(ws, sink, [])
    await session._receive()

    assert sink.stops == 1
    assert session._audio_out.empty(), "queued audio survived the interruption"


async def test_a_tool_call_is_answered_and_the_model_resumed() -> None:
    """Without response.create the model waits forever and the caller hears nothing."""
    ws = _WS(
        [
            {
                "type": "response.function_call_arguments.done",
                "name": "check_stock",
                "call_id": "call_1",
                "arguments": '{"medicine": "Panadol"}',
            }
        ]
    )
    session = _session(ws, _Sink(), [])
    await session._receive()

    kinds = [m["type"] for m in ws.sent]
    assert kinds == ["conversation.item.create", "response.create"]
    output = ws.sent[0]["item"]
    assert output["call_id"] == "call_1"
    assert "Bahadurabad" in output["output"]


async def test_malformed_tool_arguments_do_not_kill_the_call() -> None:
    ws = _WS(
        [
            {
                "type": "response.function_call_arguments.done",
                "name": "check_stock",
                "call_id": "c2",
                "arguments": "{not json",
            }
        ]
    )
    session = _session(ws, _Sink(), [])
    await session._receive()
    assert [m["type"] for m in ws.sent] == ["conversation.item.create", "response.create"]


async def test_transcripts_are_published_for_the_demo_page() -> None:
    ws = _WS(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "پیناڈول دستیاب ہے؟",
            },
            {
                "type": "response.output_audio_transcript.done",
                "transcript": "جی، دستیاب ہے۔",
            },
        ]
    )
    published: list[dict[str, Any]] = []
    session = _session(ws, _Sink(), published)
    await session._receive()

    assert [(p["role"], p["text"]) for p in published] == [
        ("user", "پیناڈول دستیاب ہے؟"),
        ("agent", "جی، دستیاب ہے۔"),
    ]


async def test_empty_transcripts_are_not_published() -> None:
    ws = _WS([{"type": "response.output_audio_transcript.done", "transcript": "  "}])
    published: list[dict[str, Any]] = []
    await _session(ws, _Sink(), published)._receive()
    assert published == []


async def test_caller_audio_is_base64_framed() -> None:
    ws = _WS([])
    session = _session(ws, _Sink(), [])

    async def frames():
        yield b"\x01\x02"
        yield b""  # silence gaps must not be sent as empty appends
        yield b"\x03\x04"

    await session._send_audio(frames())
    assert [m["type"] for m in ws.sent] == ["input_audio_buffer.append"] * 2
    assert base64.b64decode(ws.sent[0]["audio"]) == b"\x01\x02"


async def test_an_error_event_is_logged_not_raised() -> None:
    """A vendor error mid-call must not take the call down with it."""
    ws = _WS([{"type": "error", "error": {"message": "rate limited"}}])
    await _session(ws, _Sink(), [])._receive()


@pytest.mark.parametrize("mode", ["realtime", "pipeline"])
def test_both_voice_modes_are_selectable(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pipeline stays reachable: realtime is one vendor for the whole call."""
    from agent.config import Settings

    monkeypatch.setenv("VOICE_MODE", mode)
    assert Settings().voice_mode == mode
