"""Build the native-speaker review sheet for Urdu normalisation.

Reading a YAML file is the wrong way to review speech output. This renders every
golden case to real Azure ``ur-PK`` audio and embeds it in a single self-contained
HTML page, so the reviewer hears exactly what a caller would hear and ticks a box.

    uv run python eval/make_review_sheet.py

Writes ``eval/out/urdu-review.html``. Needs AZURE_SPEECH_KEY.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

import azure.cognitiveservices.speech as speechsdk
import yaml

from agent.config import Settings
from agent.language.normalize import normalize_for_tts

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "golden" / "urdu_normalization.yaml"
OUT = ROOT / "eval" / "out" / "urdu-review.html"

# The 0-99 table disagreed with an independent reference on exactly these six.
# Both spellings are attested; a native speaker picks. Rendering both lets them
# decide by ear rather than by orthography.
DISPUTED: list[tuple[int, str, str]] = [
    (51, "اکاون", "اکیاون"),
    (64, "چونسٹھ", "چوسٹھ"),
    (68, "اڑسٹھ", "اٹھسٹھ"),
    (75, "پچہتر", "پچھتر"),
    (91, "اکانوے", "اکیانوے"),
    (92, "بانوے", "بیانوے"),
]


def synthesize(text: str, settings: Settings) -> bytes:
    """Render one phrase to MP3. MP3 keeps the embedded page small enough to open."""
    config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key.get_secret_value(),
        region=settings.azure_speech_region,
    )
    config.speech_synthesis_voice_name = settings.tts_voice_urdu
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
    result = synth.speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"synthesis failed for {text!r}: {result.reason}")
    return bytes(result.audio_data)


def audio_tag(pcm: bytes) -> str:
    encoded = base64.b64encode(pcm).decode("ascii")
    return f'<audio controls preload="none" src="data:audio/mpeg;base64,{encoded}"></audio>'


def build() -> None:
    settings = Settings()
    if not settings.azure_speech_key.get_secret_value():
        raise SystemExit("AZURE_SPEECH_KEY not set")

    golden: dict[str, Any] = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    cases = golden["cases"]

    rows: list[str] = []
    for index, case in enumerate(cases):
        spoken = normalize_for_tts(case["input"])
        matches = spoken == case["expected"]
        print(f"  [{index + 1}/{len(cases)}] {case['input']}")
        rows.append(
            _row(
                index=f"g{index}",
                source=html.escape(case["input"]),
                spoken=html.escape(spoken),
                note=html.escape(case.get("note", "")),
                audio=audio_tag(synthesize(spoken, settings)),
                warn="" if matches else "code and golden file disagree",
            )
        )

    disputed_rows: list[str] = []
    for number, ours, theirs in DISPUTED:
        print(f"  [disputed] {number}")
        disputed_rows.append(
            f"""<tr>
              <td class="num">{number}</td>
              <td class="urdu">{html.escape(ours)}<div class="mini">option A (current)</div>
                  {audio_tag(synthesize(ours, settings))}</td>
              <td class="urdu">{html.escape(theirs)}<div class="mini">option B</div>
                  {audio_tag(synthesize(theirs, settings))}</td>
              <td><label><input type="radio" name="d{number}" value="A"> A</label>
                  <label><input type="radio" name="d{number}" value="B"> B</label>
                  <label><input type="radio" name="d{number}" value="?"> neither</label></td>
            </tr>"""
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        _PAGE.replace("{{ROWS}}", "\n".join(rows))
        .replace("{{DISPUTED}}", "\n".join(disputed_rows))
        .replace("{{VOICE}}", html.escape(settings.tts_voice_urdu))
        .replace("{{COUNT}}", str(len(cases))),
        encoding="utf-8",
    )
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1_000_000:.1f} MB)")


def _row(*, index: str, source: str, spoken: str, note: str, audio: str, warn: str) -> str:
    flag = f'<div class="warn">{warn}</div>' if warn else ""
    return f"""<tr>
      <td class="src">{source}{f'<div class="mini">{note}</div>' if note else ""}</td>
      <td class="urdu">{spoken}{flag}</td>
      <td>{audio}</td>
      <td class="verdict">
        <label><input type="radio" name="{index}" value="ok"> sounds right</label>
        <label><input type="radio" name="{index}" value="bad"> wrong</label>
        <input class="fix" name="{index}_fix" placeholder="correct wording">
      </td>
    </tr>"""


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Urdu normalisation review</title>
<style>
  :root{--bg:#f5f6f3;--surface:#fff;--ink:#14211e;--muted:#5c6b66;--line:#d3dbd6;
        --primary:#0e5c4a;--accent:#a85f0e;--danger:#9c3123}
  @media(prefers-color-scheme:dark){:root{--bg:#0c1412;--surface:#131e1b;--ink:#e4ebe7;
        --muted:#90a59d;--line:#2a3a35;--primary:#55c4a0;--accent:#dfa043;--danger:#e07e6c}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,sans-serif}
  .wrap{max-width:64rem;margin:0 auto;padding:2rem 1.25rem;display:flex;
        flex-direction:column;gap:2rem}
  h1{font:600 1.9rem/1.2 Georgia,serif;margin:0}
  h2{font:600 1.25rem/1.2 Georgia,serif;margin:0 0 .75rem;padding-bottom:.5rem;
     border-bottom:2px solid var(--ink)}
  .lede{color:var(--muted);max-width:46ch;margin:0}
  .box{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);
       padding:1rem 1.1rem;max-width:60ch}
  table{width:100%;border-collapse:collapse;background:var(--surface);
        border:1px solid var(--line);font-size:.92rem}
  th{background:var(--bg);text-align:left;padding:.55rem .8rem;font:600 .7rem/1.4 ui-monospace,monospace;
     letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--line)}
  td{padding:.7rem .8rem;border-bottom:1px solid var(--line);vertical-align:top}
  .src{font-family:ui-monospace,monospace;font-size:.85rem;max-width:16rem;word-break:break-word}
  .urdu{font-family:'Noto Nastaliq Urdu','Jameel Noori Nastaleeq',serif;direction:rtl;
        font-size:1.15rem;line-height:2.1;min-width:12rem}
  .num{font-family:ui-monospace,monospace;font-weight:700}
  .mini{font:400 .72rem/1.4 system-ui,sans-serif;color:var(--muted);direction:ltr;margin-top:.2rem}
  .warn{font:600 .72rem/1.4 system-ui,sans-serif;color:var(--danger);direction:ltr;margin-top:.3rem}
  .verdict{min-width:15rem}
  .verdict label{display:block;font-size:.85rem;cursor:pointer}
  .fix{width:100%;margin-top:.35rem;padding:.3rem .4rem;border:1px solid var(--line);
       background:var(--bg);color:var(--ink);border-radius:2px;font-size:.85rem}
  audio{width:13rem;height:2rem}
  button{background:var(--primary);color:var(--surface);border:0;padding:.6rem 1.1rem;
         border-radius:3px;font:600 .9rem system-ui;cursor:pointer}
  #out{width:100%;min-height:12rem;margin-top:1rem;font-family:ui-monospace,monospace;
       font-size:.8rem;padding:.7rem;border:1px solid var(--line);background:var(--surface);
       color:var(--ink);display:none}
  .scroll{overflow-x:auto}
</style></head><body><div class="wrap">

<header>
  <p style="font:600 .7rem ui-monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:0 0 .5rem">
    Native-speaker review &middot; voice {{VOICE}}</p>
  <h1>Does this sound right in Urdu?</h1>
  <p class="lede">{{COUNT}} cases plus 6 contested numerals. Press play, decide by ear, tick a box.
     Roughly fifteen minutes.</p>
</header>

<div class="box">
  <strong>Why your ear and not the text.</strong>
  <p style="margin:.5rem 0 0">These strings are read aloud to customers on a phone call, never displayed.
  A spelling that looks fine can still sound wrong, and that is the only thing that matters here.
  The wordings were written by a non-Urdu-speaker and are unverified guesses until you say otherwise.</p>
</div>

<section>
  <h2>1. Contested numerals</h2>
  <p class="lede" style="margin-bottom:.75rem">Both spellings are attested. Pick the one a Karachi
     caller would expect to hear.</p>
  <div class="scroll"><table>
    <thead><tr><th>Number</th><th>Option A</th><th>Option B</th><th>Which</th></tr></thead>
    <tbody>{{DISPUTED}}</tbody>
  </table></div>
</section>

<section>
  <h2>2. Golden cases</h2>
  <p class="lede" style="margin-bottom:.75rem">Left is what the model writes; middle is what the
     agent will say. Flag anything that sounds unnatural, over-formal, or simply wrong.</p>
  <div class="scroll"><table>
    <thead><tr><th>Model writes</th><th>Agent says</th><th>Listen</th><th>Verdict</th></tr></thead>
    <tbody>{{ROWS}}</tbody>
  </table></div>
</section>

<section>
  <h2>3. Send back</h2>
  <p class="lede" style="margin-bottom:.75rem">Your name goes into the golden file so the tests
     record who signed off.</p>
  <p><input class="fix" id="who" style="max-width:22rem" placeholder="Your name"></p>
  <p style="margin-top:.75rem"><button onclick="collect()">Generate results</button></p>
  <textarea id="out" readonly></textarea>
</section>

</div>
<script>
function collect(){
  const lines = ["reviewed_by: " + (document.getElementById('who').value || 'UNSIGNED'),
                 "reviewed_date: " + new Date().toISOString().slice(0,10), "", "numerals:"];
  document.querySelectorAll('input[type=radio]:checked').forEach(r=>{
    if(r.name.startsWith('d')) lines.push("  " + r.name.slice(1) + ": option_" + r.value);
  });
  lines.push("", "cases:");
  document.querySelectorAll('input[type=radio]:checked').forEach(r=>{
    if(!r.name.startsWith('d')){
      const fix = document.querySelector(`input[name="${r.name}_fix"]`);
      lines.push("  " + r.name + ": " + r.value + (fix && fix.value ? "   # " + fix.value : ""));
    }
  });
  const out = document.getElementById('out');
  out.style.display = 'block';
  out.value = lines.join("\\n");
  out.select();
}
</script></body></html>
"""


if __name__ == "__main__":
    build()
