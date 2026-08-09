"""Azure Neural TTS adapter. The only module permitted to import the Speech SDK.

Azure is the default Urdu voice because it is the only *stable* production option
in the market: genuine ``ur-PK`` voices (Uzma, Asad), not Indian Urdu. Inworld's
Urdu is flagged experimental, so this is also the failover target.

Two things here are easy to get wrong and expensive to discover late:

* **The Azure Speech SDK is synchronous.** Calling it directly inside an async
  function blocks the event loop, stalls the audio frame pump, and produces
  audible stutter for every concurrent call on the worker. Everything goes
  through ``run_in_executor``.
* **Region must be near Pakistan.** ``southeastasia`` (Singapore); a US region
  adds roughly 200ms per synthesis, nearly double the entire 110ms TTS budget.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import azure.cognitiveservices.speech as speechsdk

from agent.telemetry.logging import get_logger

__all__ = ["AzureTTS"]

log = get_logger(__name__)

_SENTINEL: object = object()


class AzureTTS:
    """Streaming synthesis via Azure Neural TTS."""

    def __init__(
        self,
        *,
        key: str,
        region: str = "southeastasia",
        sample_rate: int = 24_000,
    ) -> None:
        self._key = key
        self._region = region
        self._sample_rate = sample_rate
        if not region.startswith(("southeastasia", "centralindia", "uaenorth")):
            log.warning(
                "tts_region_far_from_pakistan",
                region=region,
                impact_ms=200,
                advice="use southeastasia; a US region eats the whole TTS budget",
            )

    @property
    def name(self) -> str:
        return "azure"

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return  # never pay for an empty request

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        config = speechsdk.SpeechConfig(subscription=self._key, region=self._region)
        config.speech_synthesis_voice_name = voice
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm
        )
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)

        def _emit(item: Any) -> None:
            # SDK callbacks fire on SDK threads, not the event loop.
            loop.call_soon_threadsafe(queue.put_nowait, item)

        synthesizer.synthesizing.connect(lambda evt: _emit(bytes(evt.result.audio_data)))
        synthesizer.synthesis_completed.connect(lambda _evt: _emit(_SENTINEL))
        synthesizer.synthesis_canceled.connect(
            lambda evt: _emit(_Failure(getattr(evt.result, "reason", "canceled")))
        )

        # The blocking .get() must never run on the event loop.
        task = loop.run_in_executor(None, lambda: synthesizer.speak_text_async(text).get())

        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    return
                if isinstance(item, _Failure):
                    raise RuntimeError(f"azure synthesis failed: {item.reason}")
                yield item
        finally:
            task.cancel()


class _Failure:
    __slots__ = ("reason",)

    def __init__(self, reason: object) -> None:
        self.reason = reason
