"""Review sheet for the English-to-Urdu-script lexicon.

The four words in the g22 test were verified by ear; the rest of the lexicon is
still a non-speaker's guess. This renders each entry in a natural sentence so the
reviewer hears it in context rather than in isolation, where mispronunciation is
much harder to judge.

    uv run python eval/make_translit_sheet.py
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from agent.config import Settings
from agent.language.translit import ENGLISH_TO_URDU

S = Settings()
OUT = Path(__file__).resolve().parents[1] / "eval" / "out" / "translit-review.html"

# Heard in a sentence, not alone: an isolated word gives the voice no prosodic
# context and makes a bad pronunciation sound merely odd rather than wrong.
FRAMES: dict[str, str] = {
    "order": "آپ کا {} کل آئے گا",
    "balance": "آپ کا {} پانچ ہزار روپے ہے",
    "refund": "آپ کا {} سات دن میں آئے گا",
    "complaint": "میں آپ کی {} درج کر رہا ہوں",
    "receipt": "آپ کی {} بھیج دی ہے",
    "helpline": "ہماری {} چوبیس گھنٹے کھلی ہے",
}
DEFAULT_FRAME = "میں {} چیک کر رہا ہوں"


def synth(text: str) -> bytes:
    cfg = speechsdk.SpeechConfig(
        subscription=S.azure_speech_key.get_secret_value(), region=S.azure_speech_region
    )
    cfg.speech_synthesis_voice_name = S.tts_voice_urdu
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    r = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None).speak_text_async(
        text
    ).get()
    if r.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"failed on {text!r}: {r.reason}")
    return bytes(r.audio_data)


def build() -> None:
    entries = sorted(set(ENGLISH_TO_URDU.items()))
    rows = []
    for i, (english, urdu) in enumerate(entries):
        sentence = FRAMES.get(english, DEFAULT_FRAME).format(urdu)
        print(f"  [{i + 1}/{len(entries)}] {english} -> {urdu}")
        audio = base64.b64encode(synth(sentence)).decode()
        rows.append(
            f'<tr><td class="num">{html.escape(english)}</td>'
            f'<td class="urdu">{html.escape(urdu)}</td>'
            f'<td class="urdu mini-rtl">{html.escape(sentence)}</td>'
            f'<td><audio controls preload="none" src="data:audio/mpeg;base64,{audio}"></audio></td>'
            f'<td><label><input type="radio" name="{english}" value="ok"> right</label>'
            f'<label><input type="radio" name="{english}" value="bad"> wrong</label>'
            f'<input class="fix" name="{english}_fix" placeholder="better spelling"></td></tr>'
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    template = (Path(__file__).parent / "translit_template.html").read_text(encoding="utf-8")
    OUT.write_text(
        template.replace("{{ROWS}}", "\n".join(rows))
        .replace("{{COUNT}}", str(len(entries)))
        .replace("{{VOICE}}", html.escape(S.tts_voice_urdu)),
        encoding="utf-8",
    )
    print(f"\nwrote {OUT} ({OUT.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    build()
