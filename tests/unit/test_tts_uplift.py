"""The Upliftai adapter.

Mostly a check that the contract the rest of the pipeline relies on holds: raw
PCM out, the right sample rate reported, and a vendor error surfaced rather
than swallowed into silence on a live call.
"""

from __future__ import annotations

import pytest

from agent.interfaces.tts import TTSProvider
from agent.providers.tts_uplift import UpliftTTS


class _Response:
    def __init__(self, status: int, chunks: list[bytes]) -> None:
        self.status_code = status
        self._chunks = chunks

    async def aread(self) -> bytes:
        return b'{"message":"bad voice"}'

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Client:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls: list[dict] = []

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._response

    async def aclose(self) -> None:
        pass


def _tts(response: _Response) -> tuple[UpliftTTS, _Client]:
    tts = UpliftTTS(api_key="k")
    client = _Client(response)
    tts._client = client  # type: ignore[assignment]
    return tts, client


def test_it_satisfies_the_provider_protocol() -> None:
    assert isinstance(UpliftTTS(api_key="k"), TTSProvider)


def test_the_sample_rate_is_not_azures() -> None:
    """22.05kHz, where Azure emits 24kHz.

    The sink is built from this value. Anything that assumes 24000 plays this
    voice about 9% slow, which sounds like a drunk pharmacist rather than a bug.
    """
    assert UpliftTTS(api_key="k").sample_rate == 22_050


async def test_pcm_is_streamed_through_unchanged() -> None:
    tts, _ = _tts(_Response(200, [b"\x01\x02", b"\x03\x04"]))
    out = b"".join([c async for c in tts.synthesize("سلام", voice="broadband-support")])
    assert out == b"\x01\x02\x03\x04"


async def test_empty_chunks_are_not_yielded() -> None:
    tts, _ = _tts(_Response(200, [b"", b"\x01\x02", b""]))
    chunks = [c async for c in tts.synthesize("سلام", voice="v")]
    assert chunks == [b"\x01\x02"]


async def test_it_asks_for_raw_pcm_not_mp3() -> None:
    """MP3 would need decoding on the hot path."""
    tts, client = _tts(_Response(200, [b"\x01\x02"]))
    _ = [c async for c in tts.synthesize("سلام", voice="broadband-support")]

    body = client.calls[0]["json"]
    assert body["outputFormat"] == "PCM_22050_16"
    assert body["voiceId"] == "broadband-support"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer k"


async def test_the_default_voice_is_used_when_none_is_given() -> None:
    tts = UpliftTTS(api_key="k", default_voice="helpdesk-agent")
    client = _Client(_Response(200, [b"\x01\x02"]))
    tts._client = client  # type: ignore[assignment]
    _ = [c async for c in tts.synthesize("سلام", voice="")]
    assert client.calls[0]["json"]["voiceId"] == "helpdesk-agent"


async def test_a_vendor_error_is_raised_not_swallowed() -> None:
    """Silence on a live call is worse than the recovery line."""
    tts, _ = _tts(_Response(400, []))
    with pytest.raises(RuntimeError, match="400"):
        _ = [c async for c in tts.synthesize("سلام", voice="nope")]


def test_the_registry_builds_it_unwrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not wrapped in failover to Azure.

    Uplift is selected because someone preferred the voice; falling back to the
    voice they rejected would hide exactly what is being evaluated.
    """
    from agent.config import Settings
    from agent.providers.registry import build_tts

    monkeypatch.setenv("TTS_PROVIDER", "uplift")
    monkeypatch.setenv("UPLIFT_API_KEY", "k")
    provider = build_tts(Settings())
    assert provider.name == "uplift"


# ── the default voice must match the selected vendor ─────────────────────────


def test_default_voice_follows_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Voice ids are not portable between vendors.

    The agent used to start every call on "ur-PK-UzmaNeural" whatever the
    provider, so selecting Uplift produced "Invalid voiceId" on the first
    sentence — heard by the caller as silence, because the failure was inside
    TTS rather than anywhere visible.
    """
    from agent.config import Settings

    monkeypatch.setenv("TTS_PROVIDER", "uplift")
    monkeypatch.setenv("UPLIFT_VOICE", "broadband-support")
    assert Settings().default_voice == "broadband-support"

    monkeypatch.setenv("TTS_PROVIDER", "azure")
    assert Settings().default_voice == "ur-PK-UzmaNeural"


def test_the_agent_starts_on_the_providers_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.config import Settings
    from agent.main import VoiceAgent

    monkeypatch.setenv("TTS_PROVIDER", "uplift")
    monkeypatch.setenv("UPLIFT_API_KEY", "k")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    agent = VoiceAgent(Settings(), room=object(), sink=object())
    assert agent._voice == "broadband-support"
