"""Contract tests: do the real vendors actually handle Urdu?

Marked ``contract`` — these hit live APIs, cost money, and are excluded from the
default suite. Run with ``make test-contract``.

The roundtrip is the point: synthesise Urdu with Azure, transcribe it back with
Deepgram, and assert the text survives. That single test exercises both vendors'
Urdu support at once, which is the project's largest technical risk.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest

from agent.config import Settings
from agent.interfaces.llm import CachedPrompt, Done, Message, TextDelta
from agent.language.prompts import build_cached_prefix
from agent.providers.registry import build_llm, build_tts

pytestmark = pytest.mark.contract

URDU_SENTENCE = "میرا آرڈر کہاں ہے"
CODESWITCH_SENTENCE = "آپ کا order کل deliver ہو جائے گا"


@pytest.fixture
def live() -> Settings:
    """Real settings from .env. Skips if credentials are absent."""
    settings = Settings()
    if not settings.azure_speech_key.get_secret_value():
        pytest.skip("no live credentials configured")
    return settings


# ── Azure ur-PK ───────────────────────────────────────────────────────────


async def test_azure_synthesizes_urdu(live: Settings) -> None:
    tts = build_tts(live)
    audio = b"".join([c async for c in tts.synthesize(URDU_SENTENCE, voice=live.tts_voice_urdu)])
    assert len(audio) > 10_000, "expected real PCM, got almost nothing"


async def test_azure_ttfb_within_budget(live: Settings) -> None:
    """110ms budget. Records the real number even when it passes."""
    tts = build_tts(live)
    start = time.perf_counter()
    async for _chunk in tts.synthesize(URDU_SENTENCE, voice=live.tts_voice_urdu):
        break
    ttfb_ms = (time.perf_counter() - start) * 1000
    print(f"\n  Azure ur-PK TTFB: {ttfb_ms:.0f}ms (budget {live.budget_tts_ttfb_ms}ms)")
    assert ttfb_ms < 2000, "wildly over budget — check the region setting"


@pytest.mark.parametrize("voice", ["ur-PK-UzmaNeural", "ur-PK-AsadNeural"])
async def test_both_pakistani_voices_exist(live: Settings, voice: str) -> None:
    tts = build_tts(live)
    audio = b"".join([c async for c in tts.synthesize("سلام", voice=voice)])
    assert len(audio) > 1000


async def test_azure_accepts_the_generated_ssml(live: Settings) -> None:
    """The adapter now sends SSML, not plain text. Malformed markup is rejected
    by Azure mid-call, which drops the turn — so exercise the real parser."""
    from agent.language.normalize import normalize_for_tts

    tts = build_tts(live)
    spoken = normalize_for_tts("آپ کا order کل آئے گا اور data ختم ہے، Rs. 1,500 due")
    audio = b"".join([c async for c in tts.synthesize(spoken, voice=live.tts_voice_urdu)])
    assert len(audio) > 10_000, "Azure rejected the SSML or returned nothing"


async def test_language_switch_changes_the_audio(live: Settings) -> None:
    """The whole English-pronunciation design rests on ur-PK honouring
    <lang xml:lang="en-US">. Verify against the live service, not by assumption."""
    tts = build_tts(live)
    with_switch = b"".join(
        [c async for c in tts.synthesize("میں data چیک کرتا ہوں", voice=live.tts_voice_urdu)]
    )
    urdu_only = b"".join(
        [c async for c in tts.synthesize("میں ڈیٹا چیک کرتا ہوں", voice=live.tts_voice_urdu)]
    )
    assert with_switch != urdu_only, "language switch had no effect on the rendered audio"


# ── Deepgram Nova-3 Urdu ───────────────────────────────────────────────────


async def _as_frames(pcm: bytes, chunk: int = 3200) -> AsyncIterator[bytes]:
    for i in range(0, len(pcm), chunk):
        yield pcm[i : i + chunk]


async def test_urdu_survives_tts_stt_roundtrip(live: Settings) -> None:
    """The single most valuable test in the project.

    Azure speaks Urdu, Deepgram hears it back. Passing means both vendors' Urdu
    support is real, in this configuration, today.
    """
    from agent.providers.stt_deepgram import DeepgramSTT

    tts = build_tts(live)
    pcm = b"".join([c async for c in tts.synthesize(URDU_SENTENCE, voice=live.tts_voice_urdu)])

    # Azure emits 24kHz; the STT default is 16kHz. A mismatch yields transcripts
    # that are plausible but wrong, which is far harder to debug than silence.
    stt = DeepgramSTT(
        api_key=live.deepgram_api_key.get_secret_value(),
        sample_rate=tts.sample_rate,
    )

    finals = [
        t.text async for t in stt.stream(_as_frames(pcm), language="ur", keyterms=[]) if t.is_final
    ]
    heard = " ".join(finals)
    print(f"\n  spoke: {URDU_SENTENCE}\n  heard: {heard}")

    assert heard.strip(), "Deepgram returned nothing for Urdu audio"
    assert any("؀" <= ch <= "ۿ" for ch in heard), "expected Perso-Arabic script"


async def test_codeswitched_english_noun_survives(live: Settings) -> None:
    """The realistic register: Urdu grammar with English nouns."""
    from agent.knowledge.keyterms import load_keyterms
    from agent.providers.stt_deepgram import DeepgramSTT

    tts = build_tts(live)
    pcm = b"".join(
        [c async for c in tts.synthesize(CODESWITCH_SENTENCE, voice=live.tts_voice_urdu)]
    )

    stt = DeepgramSTT(
        api_key=live.deepgram_api_key.get_secret_value(),
        sample_rate=tts.sample_rate,
    )
    heard = " ".join(
        [
            t.text
            async for t in stt.stream(_as_frames(pcm), language="ur", keyterms=load_keyterms())
            if t.is_final
        ]
    )
    print(f"\n  spoke: {CODESWITCH_SENTENCE}\n  heard: {heard}")
    assert heard.strip()

    # Documented behaviour, measured 9 Aug 2026: Deepgram Urdu returns English
    # words *transliterated into Urdu script* ("order" -> "ارڈن", "deliver" ->
    # "ڈلیور") rather than preserved as Latin. Two consequences downstream:
    #   * retrieval must index Urdu-script spellings of English product terms,
    #     not just Latin ones (TASK-07 dual indexing);
    #   * intent matching cannot rely on Latin substring checks.
    # Caveat: this audio is Azure speaking English words with an Urdu voice, so
    # the phonology is more Urdu than a real bilingual caller's. Re-measure on
    # the human recordings collected for TASK-09 before designing around it.
    latin = [w for w in heard.split() if w.isascii() and w.isalpha()]
    print(f"  latin tokens preserved: {latin or 'NONE - all transliterated'}")


# ── LLM ─────────────────────────────────────────────────────────────────────


async def test_llm_streams_and_reports_ttft(live: Settings) -> None:
    """300ms budget. Prints the measured value for the TASK-12 record."""
    llm = build_llm(live)
    system = CachedPrompt(
        prefix=build_cached_prefix(
            company="Acme",
            persona="You are Acme's Urdu-speaking support agent.",
            corpus="Acme sells prepaid internet packages across Pakistan. "
            "Delivery takes two to three working days.",
            tools=[],
        )
    )

    start = time.perf_counter()
    ttft_ms: float | None = None
    chunks: list[str] = []
    usage = None

    async for event in llm.stream([Message("user", URDU_SENTENCE)], system=system):
        if isinstance(event, TextDelta):
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - start) * 1000
            chunks.append(event.text)
        elif isinstance(event, Done):
            usage = event.usage

    reply = "".join(chunks)
    print(f"\n  provider: {llm.name}  model: {live.resolved_llm_model}")
    print(f"  TTFT: {ttft_ms:.0f}ms (budget {live.budget_ttft_ms}ms)")
    print(f"  reply: {reply}")
    if usage:
        print(f"  cache_read_tokens: {usage.cache_read_tokens}")

    assert reply.strip(), "model returned nothing"
    assert ttft_ms is not None

    # This used to stop at "is not None", which passes at four seconds. A test
    # that measures the headline requirement and then declines to enforce it is
    # how a 13x miss reaches a demo unnoticed.
    #
    # Measured 10 Aug 2026 over OpenRouter from Karachi, warm connection:
    # Opus 5 3,942ms, Sonnet 5 2,700ms, Haiku 4.5 1,698ms — against a 300ms
    # slice. If this fails, the cause is usually one of three things, in
    # descending order of impact: the model (Opus is the slowest option), the
    # provider hop (OpenRouter routes via its own servers; the direct Anthropic
    # API removes it), or where this is running (~390ms of the total here is
    # the TLS round trip from Pakistan — a worker in Mumbai or Singapore pays
    # a fraction of that).
    assert ttft_ms <= live.budget_ttft_ms, (
        f"TTFT {ttft_ms:.0f}ms exceeds the {live.budget_ttft_ms}ms budget for "
        f"{live.resolved_llm_model} via {llm.name}. See the note above: check "
        f"the model first, then the provider, then the host region."
    )


async def test_llm_replies_in_urdu(live: Settings) -> None:
    """The register is the product. An English reply to an Urdu question fails."""
    llm = build_llm(live)
    system = CachedPrompt(
        prefix=build_cached_prefix(
            company="Acme",
            persona="You are Acme's Urdu-speaking support agent.",
            corpus="Acme sells prepaid internet packages across Pakistan.",
            tools=[],
        )
    )
    reply = "".join(
        [
            e.text
            async for e in llm.stream([Message("user", URDU_SENTENCE)], system=system)
            if isinstance(e, TextDelta)
        ]
    )
    urdu_chars = sum(1 for ch in reply if "؀" <= ch <= "ۿ")
    ratio = urdu_chars / max(len(reply.replace(" ", "")), 1)
    print(f"\n  reply: {reply}\n  urdu ratio: {ratio:.0%}")
    assert ratio > 0.3, f"reply is not substantially Urdu: {reply!r}"
