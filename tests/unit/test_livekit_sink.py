"""The audio sink that puts the agent's voice into the room.

The interesting behaviour is not that PCM comes out the other end. It is that
barge-in drops queued audio rather than merely stopping the supply, and that
frame boundaries never split a sample — both of which are silent when wrong.
The LiveKit types are faked so this runs without a room.
"""

from __future__ import annotations

import asyncio
import itertools
import sys
import types
from collections.abc import AsyncIterator, Iterator

import pytest


class _FakeSource:
    def __init__(self, sample_rate: int, num_channels: int, queue_size_ms: int = 1000):
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.queue_size_ms = queue_size_ms
        self.frames: list[object] = []
        self.clears = 0
        self.closed = False

    async def capture_frame(self, frame: object) -> None:
        self.frames.append(frame)

    def clear_queue(self) -> None:
        self.clears += 1

    async def aclose(self) -> None:
        self.closed = True


class _FakeFrame:
    def __init__(self, data, sample_rate, num_channels, samples_per_channel, **_):
        self.data = bytes(data)
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.samples_per_channel = samples_per_channel


class _FakeTrack:
    @staticmethod
    def create_audio_track(name: str, source: object) -> object:
        track = _FakeTrack()
        track.name = name  # type: ignore[attr-defined]
        track.source = source  # type: ignore[attr-defined]
        return track


@pytest.fixture(autouse=True)
def fake_livekit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stand in for livekit.rtc so the sink can be exercised without a room."""
    rtc = types.SimpleNamespace(
        AudioSource=_FakeSource,
        AudioFrame=_FakeFrame,
        LocalAudioTrack=_FakeTrack,
        TrackPublishOptions=lambda **kw: kw,
        TrackSource=types.SimpleNamespace(SOURCE_MICROPHONE="mic"),
    )
    livekit = types.ModuleType("livekit")
    livekit.rtc = rtc  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "livekit", livekit)
    monkeypatch.setitem(sys.modules, "livekit.rtc", rtc)  # type: ignore[arg-type]
    for name in [m for m in sys.modules if m.startswith("agent.transport")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield


def _sink(rate: int = 24_000):
    from agent.transport.sink import LiveKitSink

    return LiveKitSink(sample_rate=rate)


async def _chunks(*items: bytes) -> AsyncIterator[bytes]:
    for item in items:
        yield item


async def test_pcm_reaches_the_source() -> None:
    sink = _sink()
    await sink.play(_chunks(b"\x01\x02" * 100, b"\x03\x04" * 100))
    assert len(sink._source.frames) == 2
    assert sink._source.frames[0].sample_rate == 24_000


async def test_samples_per_channel_matches_the_byte_count() -> None:
    """Wrong here and the room plays the right bytes at the wrong speed."""
    sink = _sink()
    await sink.play(_chunks(b"\x00\x01" * 480))  # 480 samples of 16-bit mono
    assert sink._source.frames[0].samples_per_channel == 480


async def test_an_odd_chunk_boundary_does_not_split_a_sample() -> None:
    """A chunk can end mid-sample. Dropping the stray byte inverts every
    sample after it, which sounds like loud static rather than like a bug."""
    sink = _sink()
    await sink.play(_chunks(b"\x01\x02\x03", b"\x04\x05\x06"))

    # Every byte arrives, in order: the odd byte at the end of chunk one is
    # carried into chunk two rather than dropped or reordered.
    written = b"".join(f.data for f in sink._source.frames)
    assert written == b"\x01\x02\x03\x04\x05\x06"

    # And no individual frame ever contains half a sample.
    assert all(len(f.data) % 2 == 0 for f in sink._source.frames)


async def test_empty_chunks_are_skipped() -> None:
    sink = _sink()
    await sink.play(_chunks(b"", b"\x01\x02", b""))
    assert len(sink._source.frames) == 1


async def test_stop_clears_the_queue() -> None:
    """The whole point of barge-in.

    Ceasing to supply audio is not enough: the source holds a jitter buffer and
    would keep playing an agent the caller has already interrupted.
    """
    sink = _sink()
    await sink.play(_chunks(b"\x01\x02" * 100))
    await sink.stop()
    assert sink._source.clears == 1


async def test_stop_is_safe_before_anything_played() -> None:
    sink = _sink()
    await sink.stop()
    assert sink._source.clears == 1


async def test_the_queue_is_small_enough_for_barge_in() -> None:
    """A one-second buffer means a one-second gap between stop() and silence."""
    sink = _sink()
    assert sink._source.queue_size_ms <= 250


async def test_is_playing_tracks_the_stream() -> None:
    sink = _sink()
    assert not sink.is_playing

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow() -> AsyncIterator[bytes]:
        yield b"\x01\x02" * 10
        started.set()
        await release.wait()

    task = asyncio.create_task(sink.play(slow()))
    await started.wait()
    assert sink.is_playing
    release.set()
    await task
    assert not sink.is_playing


async def test_is_playing_clears_after_cancellation() -> None:
    """Barge-in cancels play(). A sink stuck 'playing' never accepts the next turn."""
    sink = _sink()
    started = asyncio.Event()

    async def forever() -> AsyncIterator[bytes]:
        yield b"\x01\x02" * 10
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(sink.play(forever()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not sink.is_playing


async def test_remainder_does_not_leak_between_utterances() -> None:
    """A stray byte from one reply must not prepend itself to the next."""
    sink = _sink()
    await sink.play(_chunks(b"\x01\x02\x03"))
    await sink.play(_chunks(b"\x04\x05"))
    assert sink._source.frames[-1].data == b"\x04\x05"


async def test_sample_rate_is_reported_for_the_track() -> None:
    assert _sink(16_000).sample_rate == 16_000


async def test_aclose_closes_the_source() -> None:
    sink = _sink()
    await sink.aclose()
    assert sink._source.closed


async def test_it_satisfies_the_audio_sink_protocol() -> None:
    """TurnRunner only ever sees this shape."""
    sink = _sink()
    assert callable(sink.play)
    assert callable(sink.stop)
    assert asyncio.iscoroutinefunction(sink.play)
    assert asyncio.iscoroutinefunction(sink.stop)


# ── call lifecycle ───────────────────────────────────────────────────────────
#
# The bug these guard: the worker never opened the call, so the first real
# transcript hit on_final while the state machine was still IDLE and raised
# IllegalTransition. Every component worked; the call died on the first thing
# the caller said. Nothing caught it because no test drove a transcript through
# a VoiceAgent that had not already been moved to LISTENING by hand.


def _agent(monkeypatch: pytest.MonkeyPatch):
    from agent.config import Settings
    from agent.main import VoiceAgent

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test")
    return VoiceAgent(Settings(), room=object(), sink=_sink())


def test_a_fresh_agent_starts_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.pipeline.session import SessionState

    assert _agent(monkeypatch).state is SessionState.IDLE


def test_begin_opens_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.pipeline.session import SessionState

    agent = _agent(monkeypatch)
    agent.begin()
    assert agent.state is SessionState.LISTENING


def test_begin_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconnects and retries must not blow up a live call."""
    from agent.pipeline.session import SessionState

    agent = _agent(monkeypatch)
    agent.begin()
    agent.begin()
    assert agent.state is SessionState.LISTENING


def test_end_terminates_from_any_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.pipeline.session import SessionState

    agent = _agent(monkeypatch)
    agent.end()
    assert agent.state is SessionState.TERMINATED

    opened = _agent(monkeypatch)
    opened.begin()
    opened.end()
    assert opened.state is SessionState.TERMINATED


async def test_a_transcript_before_begin_is_the_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reaching on_final from IDLE must stay an error, not be quietly allowed.

    The state machine is right to refuse; the fix was for the transport to open
    the call. Softening this would let a turn start from a state the agent was
    never put into.
    """
    from agent.pipeline.session import IllegalTransition

    agent = _agent(monkeypatch)
    with pytest.raises(IllegalTransition):
        await agent.on_final("پیناڈول دستیاب ہے")


def test_the_worker_opens_the_call_before_listening() -> None:
    """Belt and braces: the ordering in the worker is the actual fix."""
    from pathlib import Path

    source = Path("src/agent/transport/worker.py").read_text(encoding="utf-8")
    assert "agent.begin()" in source, "the worker must open the call"
    assert source.index("agent.begin()") < source.index("_consume(agent"), (
        "begin() must run before any transcript can be consumed"
    )


async def test_two_turns_in_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug the user hit: answers once or twice, then stops hearing you.

    on_final moved the state to THINKING and nothing moved it back, so the
    second utterance attempted THINKING -> THINKING, raised, and killed the
    call. Every existing test ran exactly one turn.
    """
    from agent.pipeline.session import SessionState

    agent = _agent(monkeypatch)
    agent.begin()

    async def noop_turn(*_a, **_k):
        return None

    # Drive the state exactly as a completed turn does, without the network.
    for _ in range(3):
        assert agent.state is SessionState.LISTENING
        agent._state.to(SessionState.THINKING)
        agent._return_to_listening()

    assert agent.state is SessionState.LISTENING


def test_return_to_listening_is_safe_from_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    """LISTENING -> LISTENING is illegal too, so the guard matters after barge-in."""
    from agent.pipeline.session import SessionState

    agent = _agent(monkeypatch)
    agent.begin()
    agent._return_to_listening()
    assert agent.state is SessionState.LISTENING


def test_return_to_listening_leaves_a_terminated_call_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.pipeline.session import SessionState

    agent = _agent(monkeypatch)
    agent.begin()
    agent.end()
    agent._return_to_listening()
    assert agent.state is SessionState.TERMINATED


def test_only_known_voices_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The value arrives over the data channel and goes straight to the vendor."""
    agent = _agent(monkeypatch)
    before = agent._voice

    agent.set_voice("ur-PK-AsadNeural")
    assert agent._voice == "ur-PK-AsadNeural"

    agent.set_voice("nonsense-voice")
    assert agent._voice == "ur-PK-AsadNeural", "junk must not reach the TTS adapter"

    agent.set_voice("")
    assert agent._voice == "ur-PK-AsadNeural"
    assert before != "nonsense-voice"


def test_the_worker_listens_for_the_voice_picker() -> None:
    """The page sends set_voice; without a listener the picker does nothing."""
    from pathlib import Path

    source = Path("src/agent/transport/worker.py").read_text(encoding="utf-8")
    assert "data_received" in source
    assert "set_voice" in source


async def test_concurrent_playback_is_serialised() -> None:
    """Two coroutines pushing into one AudioSource corrupts it.

    The opening greeting and the first reply can overlap — the greeting runs
    off the critical path so it does not hold up the microphone — and without
    a lock LiveKit raises "InvalidState - failed to capture frame", which
    surfaces as a failed turn with nothing pointing at audio.
    """
    sink = _sink()
    order: list[str] = []

    async def slow(tag: bytes) -> AsyncIterator[bytes]:
        for _ in range(3):
            await asyncio.sleep(0)
            yield tag * 2

    async def run(tag: bytes, name: str) -> None:
        await sink.play(slow(tag))
        order.append(name)

    await asyncio.gather(run(b"\x01", "first"), run(b"\x02", "second"))

    # All of one utterance's frames land before any of the other's: the tag
    # sequence must change exactly once rather than alternate.
    tags = [f.data[:1] for f in sink._source.frames]
    switches = sum(1 for a, b in itertools.pairwise(tags) if a != b)
    assert switches == 1, f"utterances interleaved: {tags}"
    assert len(order) == 2
