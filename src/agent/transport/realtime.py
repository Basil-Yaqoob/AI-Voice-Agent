"""Speech-to-speech via OpenAI's Realtime API.

One socket replaces three vendors. Instead of caller audio → Deepgram → text →
model → text → Azure → audio, the audio goes straight into a model that
listens and speaks, and the round trip drops from roughly 2,500ms to first
spoken word to about 520ms.

Measured 10 Aug 2026: ``gpt-realtime`` first audio 518ms, ``gpt-realtime-mini``
679ms, against ~2,540ms for the pipeline it replaces. Azure's ur-PK voices
alone were costing 926 to 1,426ms of time-to-first-byte against a 110ms budget.

What this costs, and why the pipeline is still here: everything now depends on
one vendor. The provider interfaces exist because real-time Urdu TTS has two
viable suppliers in the entire market, and that argument has not gone away —
``VOICE_MODE=pipeline`` still runs the Deepgram/LLM/Azure path.

Three things this file has to get right that the pipeline got for free:

**Barge-in is a server event, not our VAD.** The API detects the caller
speaking and sends ``input_audio_buffer.speech_started``. That is the moment to
clear the sink, and it must clear rather than merely stop sending — the jitter
buffer would keep playing an agent the caller has already interrupted.

**Tools still run here.** The model asks; the DVAGO registry answers; the same
caller-identity binding applies, because the backend is constructed with the
phone number exactly as before. The model never sees a phone parameter.

**Event names differ between API generations.** The beta shape is retired and
the GA one renamed several events (``response.audio.delta`` became
``response.output_audio.delta``). Matching on a suffix rather than an exact
string keeps this working across the rename in both directions.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from websockets.asyncio.client import connect

from agent.telemetry.logging import get_logger
from agent.tools.registry import ToolRegistry

__all__ = ["RealtimeSession", "RealtimeUnavailable"]


class RealtimeUnavailable(RuntimeError):
    """The socket to OpenAI could not be opened, so there is no agent at all.

    Distinct from any other failure because it is the one the operator can act
    on — it means the network, not the code. The pipeline path (``VOICE_MODE=
    pipeline``) does not depend on this host.
    """

log = get_logger(__name__)

_URL = "wss://api.openai.com/v1/realtime"

# The API speaks 24kHz mono PCM16 in both directions, which is also what the
# Azure adapter emits — so the sink needs no changes.
SAMPLE_RATE = 24_000

# Server-side endpointing. 500ms for the same reason the Deepgram path uses it:
# 180ms treats a pause before a phone number as the end of the sentence.
_SILENCE_MS = 500

# Opening the socket to OpenAI is the one step with nothing behind it: until it
# succeeds the caller is on a live line with no agent on the other end. From
# Pakistan the connection is not reliably fast — observed here, two calls in a
# row died in DNS resolution for api.openai.com and the caller heard ten seconds
# of nothing before the call dropped. So: a short timeout and try again, rather
# than one long wait that spends the caller's patience on a single attempt.
# Three attempts at 4s is inside the window a caller will hold for; the default
# 10s single attempt is not.
_CONNECT_TIMEOUT_S = 4.0
_CONNECT_ATTEMPTS = 3

# Publishing a track and the far end subscribing to it are separate steps, and
# a browser negotiating WebRTC takes a moment. Greeting the instant the track
# exists sends the first syllables into a subscription that is not up yet, and
# the caller hears nothing at all — the failure looks exactly like a greeting
# that never fired.
_GREETING_SETTLE_S = 1.0


class RealtimeSession:
    """One call, held open on a single websocket."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        instructions: str,
        tools: ToolRegistry,
        sink: Any,
        publish: Callable[[dict[str, Any]], Awaitable[None]],
        ringback: Any = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._instructions = instructions
        self._tools = tools
        self._sink = sink
        self._publish = publish
        self._ringback = ringback

        self._ws: Any = None
        self._audio_out: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._player: asyncio.Task[None] | None = None
        self._turns = 0

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def _open(self) -> Any:
        """Open the socket, retrying briefly rather than hanging on one attempt."""
        last: BaseException | None = None
        for attempt in range(1, _CONNECT_ATTEMPTS + 1):
            started = asyncio.get_running_loop().time()
            try:
                return await connect(
                    f"{_URL}?model={self._model}",
                    additional_headers={"Authorization": f"Bearer {self._api_key}"},
                    max_size=None,
                    open_timeout=_CONNECT_TIMEOUT_S,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last = exc
                log.warning(
                    "realtime_connect_failed",
                    attempt=attempt,
                    of=_CONNECT_ATTEMPTS,
                    elapsed_ms=round(
                        (asyncio.get_running_loop().time() - started) * 1000
                    ),
                    detail=f"{type(exc).__name__}: {exc}"[:200],
                )
        raise RealtimeUnavailable(
            f"could not reach {_URL} in {_CONNECT_ATTEMPTS} attempts"
        ) from last

    async def run(self, frames: AsyncIterator[bytes]) -> None:
        """Hold the call open until the caller's audio stream ends."""
        async with await self._open() as ws:
            self._ws = ws
            await self._configure()

            # Ring, rather than start playback, until there is something to
            # play. The playback task takes the sink's lock for the whole call,
            # so it cannot start first — and the second and a half before the
            # model's first audio is exactly the silence the tone is for.
            if self._ringback is not None:
                self._ringback.start()

            # Pump before greeting: _greet waits for the subscription to
            # settle, and awaiting it here would leave the caller's audio
            # unsent for that second.
            pump = asyncio.create_task(self._send_audio(frames))
            recv = asyncio.create_task(self._receive())
            greeting = asyncio.create_task(self._greet())
            try:
                await asyncio.wait({pump, recv}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (pump, recv, greeting):
                    task.cancel()
                if self._ringback is not None:
                    await self._ringback.stop()
                await self._audio_out.put(None)
                if self._player is not None:
                    await asyncio.gather(self._player, return_exceptions=True)
                if recv.done() and not recv.cancelled():
                    recv.result()  # surface a crash rather than hide it

    async def _configure(self) -> None:
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": self._instructions,
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                            "turn_detection": {
                                "type": "server_vad",
                                "silence_duration_ms": _SILENCE_MS,
                                # The model decides when to answer; nothing else
                                # has to notice end-of-speech.
                                "create_response": True,
                                "interrupt_response": True,
                            },
                            # Pinned, not auto-detected. Urdu, Hindi and
                            # Punjabi are close enough in speech that
                            # auto-detection returns Devanagari or Gurmukhi
                            # for perfectly good Urdu — the caller sees their
                            # own sentence in a script they may not read.
                            "transcription": {"model": "whisper-1", "language": "ur"},
                        },
                        "output": {
                            "voice": self._voice,
                            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        },
                    },
                    "tools": [
                        {
                            "type": "function",
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        }
                        for tool in self._tools.definitions()
                    ],
                    "tool_choice": "auto",
                },
            }
        )

    async def _greet(self) -> None:
        """Speak first.

        Without this the agent waits for the caller, and a phone line that
        answers with silence gets "hello? hello?" and then a hang-up. The
        caller should hear who they have reached before they say anything.
        """
        await asyncio.sleep(_GREETING_SETTLE_S)
        await self._send(
            {
                "type": "response.create",
                "response": {
                    "instructions": (
                        "Greet the caller now, before they have said "
                        "anything. One short sentence of natural Urdu: thank "
                        "them for calling دواگو (write the name in Urdu "
                        "script exactly like that, never as Latin 'DVAGO') "
                        "and ask how you can help. Do not call any tool and "
                        "do not mention the caller by name yet."
                    )
                },
            }
        )

    # ── caller audio in ───────────────────────────────────────────────────

    async def _send_audio(self, frames: AsyncIterator[bytes]) -> None:
        async for frame in frames:
            if not frame:
                continue
            await self._send(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(frame).decode("ascii"),
                }
            )

    # ── agent audio out ───────────────────────────────────────────────────

    async def _play(self) -> None:
        """Feed queued PCM to the sink as one continuous utterance.

        A queue rather than writing straight from the receive loop: the sink
        awaits real playout time, and blocking the receive loop on it would
        stall tool calls and barge-in events behind the audio.
        """
        async def chunks() -> AsyncIterator[bytes]:
            while True:
                chunk = await self._audio_out.get()
                if chunk is None:
                    return
                yield chunk

        try:
            await self._sink.play(chunks())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("realtime_playback_failed")

    async def _begin_playback(self) -> None:
        """Hand the sink over from the ringing tone to the model, once.

        Deliberately driven by the first audio delta rather than by a timer:
        the tone should end when there is genuinely something to say, and no
        fixed guess at how long the model takes survives a slow network.
        """
        if self._player is not None:
            return
        if self._ringback is not None:
            await self._ringback.stop()
        self._player = asyncio.create_task(self._play())

    def _drop_queued_audio(self) -> None:
        while not self._audio_out.empty():
            try:
                self._audio_out.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ── server events ─────────────────────────────────────────────────────

    async def _receive(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            event = json.loads(raw)
            kind = event.get("type", "")

            # Suffix matching: the GA rename turned response.audio.delta into
            # response.output_audio.delta and this has to survive both.
            if kind.endswith("audio.delta"):
                self._audio_out.put_nowait(base64.b64decode(event["delta"]))
                await self._begin_playback()

            elif kind == "input_audio_buffer.speech_started":
                # Barge-in. Drop what is queued and silence the line; the
                # server stops generating on its own.
                self._drop_queued_audio()
                if self._ringback is not None:
                    # A caller talking over the ringing tone has answered it.
                    await self._ringback.stop()
                await self._sink.stop()

            elif kind.endswith("input_audio_transcription.completed"):
                await self._say("user", event.get("transcript", ""))

            elif kind.endswith("output_audio_transcript.done") or kind.endswith(
                "audio_transcript.done"
            ):
                self._turns += 1
                await self._say("agent", event.get("transcript", ""))

            elif kind == "response.function_call_arguments.done":
                await self._call_tool(event)

            elif kind == "error":
                log.warning("realtime_error", detail=json.dumps(event.get("error"))[:300])

    async def _call_tool(self, event: dict[str, Any]) -> None:
        name = event.get("name", "")
        try:
            arguments = json.loads(event.get("arguments") or "{}")
        except ValueError:
            arguments = {}

        result = await self._tools.call(name, arguments)
        log.info("tool_called", tool=name, error=result.is_error)

        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": event.get("call_id"),
                    "output": result.content,
                },
            }
        )
        # The model does not resume on its own after a tool result.
        await self._send({"type": "response.create"})

    async def set_voice(self, voice: str) -> None:
        """Switch voice for the rest of the call.

        The API refuses this once the current response has produced audio, so
        a change made while the agent is mid-sentence is rejected and logged
        rather than applied. Pick before speaking, or between turns.
        """
        self._voice = voice
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "audio": {"output": {"voice": voice}},
                },
            }
        )

    # ── plumbing ──────────────────────────────────────────────────────────

    async def _say(self, role: str, text: str) -> None:
        if not text.strip():
            return
        await self._publish(
            {"type": "transcript", "role": role, "text": text, "final": True}
        )

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self._ws is not None
        await self._ws.send(json.dumps(payload))
