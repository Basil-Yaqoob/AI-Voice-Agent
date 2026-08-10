# Switching voice engines

| | |
|---|---|
| **Purpose** | Change which engine speaks, without guessing which `.env` lines matter |
| **Applies to** | Local demo and any deployed worker |
| **Last verified** | 10 Aug 2026, all three paths driven end to end |

## The one thing to understand first

`VOICE_MODE` and `TTS_PROVIDER` are **not alternatives at the same level**. One
gates the other:

- **`VOICE_MODE=realtime`** — OpenAI listens, thinks and speaks over a single
  socket. **No TTS vendor is constructed at all.** `TTS_PROVIDER` is read but
  never used, so leaving it set to `uplift` changes nothing.
- **`VOICE_MODE=pipeline`** — Deepgram → LLM → a TTS vendor, three separate
  suppliers. **Only now** does `TTS_PROVIDER` decide who speaks.

There is no `TTS_PROVIDER=openai`. `build_tts` accepts `azure | uplift |
inworld | fake` only. The OpenAI voices exist solely through the realtime
socket, which is why the question "what do I set `TTS_PROVIDER` to for OpenAI
voices?" has no answer — you set `VOICE_MODE` instead.

The demo page builds its voice list from `/api/config`, which reports what the
**running** mode can actually speak. If the list looks wrong, the mode is not
what you think it is.

## The matrix

| Want | `VOICE_MODE` | `TTS_PROVIDER` | Voices offered in the UI | First audio |
|---|---|---|---|---|
| **OpenAI Realtime** | `realtime` | *ignored* | Cedar, Marin, Alloy, Shimmer | ~520ms |
| **Uplift** (Pakistani) | `pipeline` | `uplift` | Broadband Support, Helpdesk Agent, Female Narrator | ~3s |
| **Azure ur-PK** | `pipeline` | `azure` | Uzma, Asad | ~2.5s |

The latency column is the whole trade. Realtime is roughly five times faster to
first word because it collapses three network round trips into one; the
pipeline exists because real-time Urdu TTS has very few suppliers and the
provider interface is what keeps that switchable.

---

## OpenAI Realtime — what you are running now

```dotenv
VOICE_MODE=realtime
REALTIME_MODEL=gpt-realtime
REALTIME_VOICE=cedar          # cedar | marin | alloy | shimmer
OPENAI_API_KEY=sk-...
```

`TTS_PROVIDER` and `TTS_VOICE_URDU` are inert here — do not bother changing them.

`REALTIME_VOICE` is only the *default*; whatever is chosen in the page's picker
before you press Connect overrides it for that call.

Measured 10 Aug 2026: `gpt-realtime` 518ms to first audio, `gpt-realtime-mini`
679ms.

---

## Uplift — Pakistani voices

```dotenv
VOICE_MODE=pipeline
TTS_PROVIDER=uplift
UPLIFT_API_KEY=...
UPLIFT_VOICE=broadband-support   # the default; the picker overrides per call
```

Three voices are wired into the picker. There are 82 in total — see
`docs.upliftai.org/orator_voices`. To offer more, add them to `_UPLIFT` in
`src/agent/providers/voices.py`; the page and the worker's allow-list both read
from there, so one edit covers both.

Uplift is deliberately **not** wrapped in failover to Azure. The only reason to
select it is that a Pakistani ear preferred it, and silently substituting the
voice that was rejected would hide the very thing being evaluated.

Expect ~3s turns. Uplift's time-to-first-byte alone measured 1,472–1,764ms
against a 110ms budget.

---

## Azure ur-PK

```dotenv
VOICE_MODE=pipeline
TTS_PROVIDER=azure
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=southeastasia
TTS_VOICE_URDU=ur-PK-UzmaNeural   # or ur-PK-AsadNeural
```

`AZURE_SPEECH_REGION` must be `southeastasia`. A US region costs roughly 200ms
for callers in Pakistan.

Measured TTFB 926–1,426ms, also well over the 110ms budget.

---

## Applying a change

The worker reads `.env` **once, at startup**. A running worker keeps answering
with the old settings, which is indistinguishable from the change not working.

```bash
make stop
make demo     # terminal 1 — the agent worker
make serve    # terminal 2 — the demo page on :8000
```

Then hard-refresh **http://localhost:8000** (`Ctrl+Shift+R`) so the page picks
up the new voice list.

Docker (LiveKit, Postgres, Redis) does not need restarting — `make dev` / `make
down` are separate and can stay up between demos.

### Confirming it took effect

```bash
curl -s http://localhost:8000/api/config
```

`voice_mode` and `engine` are the running truth. The worker also logs the voice
it actually built the session with:

```
{"model": "gpt-realtime", "voice": "shimmer", "event": "listening_realtime"}
```

---

## Common pitfalls

- **`VOICE_MODE=uplift` is not a mode.** It reads entirely plausibly and is
  rejected at startup with a message telling you what to set instead. Before
  that guard existed it cost a whole demo: `Settings` raised, the worker died,
  and every call afterwards joined a room no agent was dispatched to — so the
  failure looked like LiveKit rather than one line of `.env`.
- **The voice cannot be changed mid-call on the realtime path.** OpenAI answers
  with `cannot_update_voice — cannot update a conversation's voice if assistant
  audio is present`, and the greeting speaks a second after connecting. Both
  pickers are therefore fixed for the length of a call; hang up to change either.
- **Voice ids are not portable.** "cedar" means nothing to Uplift and
  "broadband-support" means nothing to OpenAI. The token endpoint validates the
  requested voice against the running mode and silently falls back to the
  default rather than passing junk to a vendor.
- **A missing `UPLIFT_API_KEY` is not caught at startup.** `_REQUIRED` in
  `config.py` has entries for Azure, Inworld and the LLM providers but not
  Uplift, so the failure surfaces mid-call as a synthesis error instead of at
  boot. Worth fixing if Uplift ever becomes the default.
- **`REALTIME_API_KEY` falls back to `OPENROUTER_API_KEY`** when `OPENAI_API_KEY`
  is unset. An OpenRouter key against `api.openai.com` fails authentication, and
  the message points at credentials rather than at the fallback.
- **The `contract`-marked tests follow `TTS_PROVIDER`.** The ones named for
  Azure in `tests/integration/test_urdu_roundtrip.py` call `build_tts()` on
  whatever is configured and then pass Azure voice ids, so they fail while
  Uplift is selected. They are excluded from `make test`; only `make
  test-contract` runs them.
