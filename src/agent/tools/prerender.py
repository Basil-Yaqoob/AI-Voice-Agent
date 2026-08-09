"""Render the fixed audio assets.

    uv run python -m agent.tools.prerender

Greeting, filler and recovery lines are rendered ahead of time rather than
synthesised on demand. Two reasons: they then cost zero latency at the moment
they are needed, and — more importantly — a recovery line must still play when
the thing that just failed *is* the TTS provider.

Rendered with the production ur-PK voice by default so the fixed lines match
the live ones. ElevenLabs v3 sounds better and its inability to stream does not
matter for fixed strings, but mixing voices inside one call is jarring; if you
switch, switch all of them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from agent.config import Settings
from agent.language.ssml import build_ssml
from agent.pipeline.recovery import RECOVERY_LINES

__all__ = ["main"]

ASSETS = Path(__file__).resolve().parents[3] / "assets" / "prerendered"

# Spoken at the start of every call, so it is worth getting right.
GREETING = ("greeting.wav", "السلام علیکم، میں آپ کی کیا مدد کر سکتا ہوں؟")
GOODBYE = ("goodbye.wav", "شکریہ، اللہ حافظ۔")


def _render(text: str, target: Path, settings: Settings) -> None:
    config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key.get_secret_value(),
        region=settings.azure_speech_region,
    )
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )
    synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)

    # Same SSML path as live speech, so a Latin word in a fixed line is
    # pronounced identically to one in a generated sentence.
    ssml = build_ssml(text, voice=settings.tts_voice_urdu)
    result = synth.speak_ssml_async(ssml).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"failed to render {target.name}: {result.reason}")

    target.write_bytes(bytes(result.audio_data))
    print(f"  {target.name:24s} {len(result.audio_data):>8,} bytes  {text}")


def main() -> int:
    settings = Settings()
    if not settings.azure_speech_key.get_secret_value():
        print("AZURE_SPEECH_KEY is not set")
        return 1

    ASSETS.mkdir(parents=True, exist_ok=True)
    print(f"rendering with {settings.tts_voice_urdu}\n")

    for asset, text in (GREETING, GOODBYE):
        _render(text, ASSETS / asset, settings)
    for line in RECOVERY_LINES.values():
        _render(line.text, ASSETS / line.asset, settings)

    print(f"\nwrote {len(RECOVERY_LINES) + 2} files to {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
