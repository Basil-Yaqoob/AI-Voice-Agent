"""The worker: one process, one call at a time, room to pipeline and back.

This is the last mile. Everything below it — turn execution, barge-in, tools,
the database — is built and tested against fakes; this is what hands it real
audio.

The shape of a call:

    caller speaks ─► LiveKit track ─► AudioStream(16kHz) ─► Deepgram
                                                              │
                                              partial/final transcripts
                                                              ▼
                                                         VoiceAgent
                                                              │
                                                     TTS ─► LiveKitSink ─► room

Three things that are easy to get wrong and expensive to debug:

**Resampling is LiveKit's job, not ours.** Browsers publish at 48kHz and
Deepgram wants 16kHz. ``AudioStream`` takes a ``sample_rate`` and does the
conversion in Rust; a hand-rolled resampler in Python would sit on the hot path
and drift.

**The caller's number comes off the participant identity.** Never from the
conversation, never from a tool argument. It is signed into the token, so
faking it means forging the token. On real PSTN the same field carries what the
SIP INVITE asserted.

**STT runs for the life of the call, not the life of a turn.** Deepgram's
stream is opened once and fed continuously, because that is what lets it detect
the caller starting to speak *while the agent is talking* — which is barge-in.
Opening a stream per turn would make interruption impossible to hear.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext

from agent.config import Settings, get_settings
from agent.main import SELECTABLE_VOICES, VoiceAgent, open_database
from agent.telemetry.logging import get_logger
from agent.transport import realtime as realtime_module
from agent.transport.sink import LiveKitSink

__all__ = ["entrypoint"]

log = get_logger(__name__)

# 20ms of audio per frame. Deepgram's own guidance, and small enough that
# end-of-speech detection is not quantised by the frame size.
_FRAME_MS = 20

# How long to wait for the caller's microphone after they join. Generous:
# a browser prompting for permission can easily take several seconds, and
# giving up early drops a caller who was only reaching for 'Allow'.
_TRACK_WAIT_S = 15.0


async def entrypoint(ctx: JobContext) -> None:
    """Handle one call from connect to disconnect."""
    settings = get_settings()
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    caller_phone = VoiceAgent.caller_phone_from(participant)

    log.info(
        "call_started",
        room=ctx.room.name,
        participant=participant.identity,
        caller_known=caller_phone is not None,
    )

    database = open_database()
    if database is not None:
        await database.warm()

    realtime = settings.voice_mode == "realtime"
    rate = realtime_module.SAMPLE_RATE if realtime else _tts_sample_rate(settings)
    sink = LiveKitSink(sample_rate=rate)
    await sink.publish(ctx.room)

    agent = VoiceAgent(
        settings,
        ctx.room,
        sink,
        caller_phone=caller_phone,
        database=database,
    )

    try:
        if realtime:
            await _run_realtime(agent, settings, ctx.room, participant, sink)
        else:
            await _run_call(agent, settings, ctx.room, participant)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("call_failed", room=ctx.room.name)
        raise
    finally:
        agent.end()
        await sink.aclose()
        if database is not None:
            database.close()
        log.info("call_ended", room=ctx.room.name)


async def _run_realtime(
    agent: VoiceAgent,
    settings: Settings,
    room: rtc.Room,
    participant: rtc.Participant,
    sink: LiveKitSink,
) -> None:
    """Speech to speech on one socket. No STT, no TTS, no turn runner.

    The agent object is still built, and still owns the things that are not the
    model's to decide: which persona and corpus describe the business, and a
    tool registry bound to this caller's phone number. Only the audio path
    changes.
    """
    track = await _wait_for_audio_track(room, participant)
    if track is None:
        log.warning("caller_published_no_audio", participant=participant.identity)
        return

    audio = rtc.AudioStream(
        track,
        sample_rate=realtime_module.SAMPLE_RATE,
        num_channels=1,
        frame_size_ms=_FRAME_MS,
    )

    disconnected = asyncio.Event()
    room.on("participant_disconnected", lambda _p: disconnected.set())

    async def frames() -> Any:
        try:
            async for event in audio:
                yield bytes(event.frame.data)
        finally:
            await audio.aclose()

    session = realtime_module.RealtimeSession(
        api_key=settings.realtime_api_key,
        model=settings.realtime_model,
        voice=settings.realtime_voice,
        instructions=agent.instructions,
        tools=agent.tools,
        sink=sink,
        publish=agent.publish,
    )

    # Held so the event loop cannot garbage-collect a switch mid-flight.
    pending: set[asyncio.Task[None]] = set()

    def _on_data(packet: Any) -> None:
        try:
            payload = json.loads(bytes(packet.data).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if payload.get("type") != "set_voice":
            return
        voice = str(payload.get("voice", ""))
        if voice not in SELECTABLE_VOICES:
            log.warning("unknown_voice_ignored", voice=voice[:40])
            return
        task = asyncio.create_task(session.set_voice(voice))
        pending.add(task)
        task.add_done_callback(pending.discard)

    room.on("data_received", _on_data)

    agent.begin()
    log.info(
        "listening_realtime",
        model=settings.realtime_model,
        voice=settings.realtime_voice,
    )

    call = asyncio.create_task(session.run(frames()))
    hangup = asyncio.create_task(disconnected.wait())
    try:
        await asyncio.wait({call, hangup}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (call, hangup):
            task.cancel()
        if call.done() and not call.cancelled():
            call.result()


async def _run_call(
    agent: VoiceAgent,
    settings: Settings,
    room: rtc.Room,
    participant: rtc.Participant,
) -> None:
    """Pump caller audio through STT and drive the agent from the transcripts."""
    stt = agent.stt

    # Joining a room and publishing a microphone are two separate events, and
    # the gap between them is easily a second. Opening the recognition stream
    # against a participant with no track yet yields a stream that ends
    # immediately: Deepgram then closes, the consumer finishes, and the call
    # tears itself down about five seconds in, having heard nothing. Waiting
    # for the track is the whole fix.
    track = await _wait_for_audio_track(room, participant)
    if track is None:
        log.warning("caller_published_no_audio", participant=participant.identity)
        return

    audio = rtc.AudioStream(
        track,
        sample_rate=stt.sample_rate,
        num_channels=1,
        frame_size_ms=_FRAME_MS,
    )

    disconnected = asyncio.Event()
    room.on("participant_disconnected", lambda _p: disconnected.set())

    # The demo page's voice picker sends a data message. Without this listener
    # it sends into the void and the voice never changes, which reads as "the
    # male voice is broken" rather than as a missing handler.
    def _on_data(packet: Any) -> None:
        try:
            payload = json.loads(bytes(packet.data).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if payload.get("type") == "set_voice":
            agent.set_voice(str(payload.get("voice", "")))

    room.on("data_received", _on_data)

    async def frames() -> Any:
        """Raw PCM out of the room, until the caller hangs up."""
        try:
            async for event in audio:
                yield bytes(event.frame.data)
        finally:
            await audio.aclose()

    transcripts = stt.stream(
        frames(),
        language=settings.stt_language,
        keyterms=agent.keyterms,
    )

    # Open the call before a single transcript can arrive: on_final drives the
    # state machine from LISTENING, and a turn starting from IDLE is fatal.
    agent.begin()
    log.info("listening", track=track.sid, rate=stt.sample_rate)

    consume = asyncio.create_task(_consume(agent, transcripts))
    hangup = asyncio.create_task(disconnected.wait())
    try:
        await asyncio.wait({consume, hangup}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (consume, hangup):
            task.cancel()
        # Surface a crash in the consumer rather than swallowing it in cleanup.
        if consume.done() and not consume.cancelled():
            consume.result()


async def _consume(agent: VoiceAgent, transcripts: Any) -> None:
    """Turn a stream of transcript fragments into whole caller turns.

    Deepgram reports two different kinds of finality and they are easy to
    confuse, because the wrong one still produces sensible-looking text:

    * ``is_final`` means this fragment will not be revised. It fires at every
      natural pause — before a number, mid-list, whenever the speaker draws
      breath.
    * ``speech_final`` means the caller has stopped talking.

    Running a turn on ``is_final`` chops one sentence into two or three, and
    the agent answers each piece as though it were a whole question. So the
    fragments accumulate here and only ``speech_final`` dispatches a turn.
    """
    speaking = False
    pending: list[str] = []

    async for transcript in transcripts:
        text = transcript.text.strip()
        if not text and not transcript.speech_final:
            continue

        if not speaking:
            speaking = True
            # Fires the barge-in check. Doing this on the first fragment rather
            # than on a VAD event keeps one source of truth for "the caller has
            # started talking".
            await agent.on_speech_started()

        if transcript.speech_final:
            pending.append(text)
            utterance = " ".join(part for part in pending if part).strip()
            pending.clear()
            speaking = False
            if utterance:
                await agent.on_final(utterance)
        elif transcript.is_final:
            # Settled, but the caller is still going. Hold it.
            pending.append(text)
            agent.on_partial(" ".join(pending))
        else:
            agent.on_partial(" ".join([*pending, text]))


async def _wait_for_audio_track(
    room: rtc.Room, participant: rtc.Participant
) -> rtc.Track | None:
    """The caller's microphone track, once it is actually subscribed.

    Checks what is already published before waiting on the event, because the
    track often arrives before the entrypoint gets this far and an event that
    has already fired never fires again.
    """
    for publication in participant.track_publications.values():
        if publication.track is not None and publication.kind == rtc.TrackKind.KIND_AUDIO:
            return publication.track

    subscribed: asyncio.Future[rtc.Track] = asyncio.get_running_loop().create_future()

    def _on_subscribed(track: rtc.Track, _pub: Any, who: rtc.Participant) -> None:
        if (
            who.identity == participant.identity
            and track.kind == rtc.TrackKind.KIND_AUDIO
            and not subscribed.done()
        ):
            subscribed.set_result(track)

    room.on("track_subscribed", _on_subscribed)
    try:
        async with asyncio.timeout(_TRACK_WAIT_S):
            return await subscribed
    except TimeoutError:
        return None
    finally:
        room.off("track_subscribed", _on_subscribed)


def _tts_sample_rate(settings: Settings) -> int:
    from agent.providers.registry import build_tts

    return build_tts(settings).sample_rate
