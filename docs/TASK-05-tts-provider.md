# TASK-05 — Text-to-speech with runtime failover

| | |
|---|---|
| **Depends on** | TASK-02 |
| **Blocks** | 08, 13 |
| **Estimate** | 1–1.5 days |
| **Level** | Intermediate |

---

## Objective

Streaming Urdu speech synthesis behind `TTSProvider`, with **two independent implementations** (Azure `ur-PK` and Inworld TTS-2) that can be swapped by config, plus mid-call failover.

This task carries the project's biggest vendor risk. The research found only two viable real-time Urdu voices in the entire market, and **Inworld's Urdu is flagged experimental** — quality, price or availability can change without production guarantees. Azure is the stable fallback. If the primary degrades during a client demo, recovery must be `TTS_PROVIDER=azure` and a restart, not a code change.

---

## Steps

### 1. Interface

`src/agent/interfaces/tts.py`:

```python
from typing import AsyncIterator, Protocol

class TTSProvider(Protocol):
    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        """Yield PCM audio chunks. First chunk must arrive within the TTFB budget."""
        ...

    @property
    def sample_rate(self) -> int: ...

    @property
    def name(self) -> str: ...
```

Keep it this thin. A fat abstraction trying to normalise SSML, emotion tags and prosody controls across two vendors with different feature sets is worse than no abstraction — you would end up with a lowest-common-denominator API that exposes neither vendor's strengths.

### 2. Azure adapter (primary)

`src/agent/providers/tts_azure.py`:

```python
import azure.cognitiveservices.speech as speechsdk
from agent.interfaces.tts import TTSProvider

class AzureTTS(TTSProvider):
    def __init__(self, *, key: str, region: str, sample_rate: int = 24_000) -> None:
        self._cfg = speechsdk.SpeechConfig(subscription=key, region=region)
        self._cfg.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm
        )
        self._sample_rate = sample_rate

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def name(self) -> str:
        return "azure"

    async def synthesize(self, text, *, voice):
        self._cfg.speech_synthesis_voice_name = voice   # ur-PK-UzmaNeural
        synth = speechsdk.SpeechSynthesizer(speech_config=self._cfg, audio_config=None)

        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        synth.synthesizing.connect(
            lambda evt: queue.put_nowait(bytes(evt.result.audio_data))
        )
        synth.synthesis_completed.connect(lambda _: queue.put_nowait(None))
        synth.synthesis_canceled.connect(lambda _: queue.put_nowait(None))

        # The Azure SDK is sync — never call it directly on the event loop.
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, synth.speak_text_async(text).get)

        while (chunk := await queue.get()) is not None:
            yield chunk
```

**The `run_in_executor` is mandatory.** The Azure Speech SDK is synchronous; calling it directly inside an async function blocks the event loop, stalls the audio frame pump, and produces audible stutter for every concurrent call on that worker. `ruff`'s ASYNC ruleset (TASK-01) catches the naive version.

**Region:** use `southeastasia`, not a US region. From Pakistan the difference is roughly 200ms per synthesis — nearly double your entire 110ms TTFB budget.

### 3. Inworld adapter (candidate primary)

Same interface, WebSocket streaming, `inworld-tts-2` model. Note Urdu (`ur`) is only on TTS-2 — not on `tts-1.5-max` or `tts-1.5-mini`, so the model ID is not interchangeable.

Because Urdu is experimental there, add a startup capability probe: synthesize one short Urdu phrase at boot and log the outcome. If it fails, log loudly and fall back to Azure rather than discovering the problem on the first live call.

### 4. Failover wrapper

`src/agent/providers/tts_failover.py`:

```python
class FailoverTTS(TTSProvider):
    """Try primary; on failure BEFORE first byte, transparently use fallback."""

    def __init__(self, primary: TTSProvider, fallback: TTSProvider) -> None:
        self._primary, self._fallback = primary, fallback

    async def synthesize(self, text, *, voice):
        first = True
        try:
            async for chunk in self._primary.synthesize(text, voice=voice):
                first = False
                yield chunk
            return
        except Exception:
            if not first:
                # Audio already reached the caller — switching mid-utterance
                # would splice two different voices together. Fail this
                # utterance instead; the pipeline speaks a recovery line.
                raise
            log.warning("tts_failover", frm=self._primary.name, to=self._fallback.name)

        async for chunk in self._fallback.synthesize(text, voice=voice):
            yield chunk
```

The `if not first` guard is the subtle part. Once audio has reached the caller, switching providers mid-utterance splices two different voices into one sentence, which sounds far worse than a clean error. Fail over only before the first byte.

### 5. Pre-rendered audio with ElevenLabs v3

Fixed strings — greeting, hold filler, error line, goodbye — should be **pre-rendered at build time** and served from disk. This gives you:

- **Zero latency** on the most common utterances in the call
- **The best available Urdu voice.** ElevenLabs v3 supports Urdu and sounds better than either real-time option. Its inability to stream does not matter for fixed strings. This is the one place the research's rejected vendor is exactly the right tool.

```
assets/prerendered/
├── greeting.wav        "السلام علیکم، میں آپ کی کیا مدد کر سکتا ہوں؟"
├── filler_short.wav    "ایک سیکنڈ..."
├── error_retry.wav     "معذرت، دوبارہ بتائیے گا؟"
└── goodbye.wav         "شکریہ، اللہ حافظ۔"
```

Commit a `make prerender` target so regenerating them is reproducible.

> **Voice consistency caveat:** a v3-rendered greeting followed by an Azure-rendered reply is two different voices in one call. Either accept it (callers largely tolerate it for greetings, which read as recorded), or render your fillers with the same provider you use live. Decide deliberately and note the decision — do not discover it in the demo.

### 6. Chunk sizing

Yield audio in small chunks (~20–40ms) rather than large buffers. Large chunks increase time-to-first-byte and make barge-in cancellation coarse — the caller keeps hearing audio for the length of the buffer after they interrupt.

---

## Acceptance criteria

- [ ] `AzureTTS` and `InworldTTS` both implement `TTSProvider`, pass `mypy --strict`
- [ ] First audio chunk arrives within `budget_tts_ttfb_ms` (110ms) for a short Urdu sentence, measured against the real API
- [ ] Azure SDK calls run in an executor — no event-loop blocking (verify under concurrent load)
- [ ] `TTS_PROVIDER=azure|inworld` switches provider with no code change
- [ ] Failover activates when primary fails **before** first byte
- [ ] Failover does **not** activate mid-utterance
- [ ] Both `ur-PK-UzmaNeural` and `ur-PK-AsadNeural` synthesize correctly
- [ ] Code-switched text renders English words intelligibly, not as Urdu-phonetic garble
- [ ] Pre-rendered assets exist and `make prerender` regenerates them
- [ ] Inworld capability probe runs at startup and logs the result
- [ ] `azure.cognitiveservices` imported in exactly one file; same for Inworld

---

## Unit tests

`tests/unit/test_tts_failover.py` — the highest-value tests in this task:

| Test | Asserts |
|---|---|
| `test_uses_primary_when_healthy` | Fallback never called |
| `test_fails_over_before_first_byte` | Fallback output returned, warning logged |
| `test_does_not_fail_over_after_first_byte` | Raises instead of splicing voices |
| `test_failover_preserves_chunk_order` | No reordering across the switch |
| `test_both_providers_fail_propagates` | Error surfaces for pipeline handling |

```python
async def test_does_not_fail_over_after_first_byte() -> None:
    """Splicing two voices mid-sentence sounds worse than a clean failure."""
    primary = FakeTTS(chunks=[b"aaa"], fail_after_chunks=1)
    fallback = FakeTTS(chunks=[b"bbb"])
    tts = FailoverTTS(primary, fallback)

    got: list[bytes] = []
    with pytest.raises(Exception):
        async for c in tts.synthesize("سلام", voice="v"):
            got.append(c)

    assert got == [b"aaa"]
    assert fallback.call_count == 0
```

`tests/unit/test_tts_contract.py` (both adapters, faked transport):

| Test | Asserts |
|---|---|
| `test_yields_chunks_not_one_blob` | Streaming, not buffered |
| `test_chunk_size_bounded` | Chunks ≤ 40ms of audio |
| `test_sample_rate_declared` | Property matches actual output |
| `test_empty_text_yields_nothing` | No spurious API call |
| `test_cancellation_stops_synthesis` | Barge-in works |

`tests/latency/test_tts_budget.py`:

```python
@pytest.mark.contract
@pytest.mark.latency
async def test_azure_ttfb_within_budget(azure_tts, settings) -> None:
    start = time.perf_counter()
    async for _ in azure_tts.synthesize("جی بالکل", voice=settings.tts_voice_urdu):
        break
    ttfb_ms = (time.perf_counter() - start) * 1000
    assert ttfb_ms < settings.budget_tts_ttfb_ms, f"TTFB {ttfb_ms:.0f}ms over budget"
```

`tests/integration/test_tts_concurrency.py`:

```python
@pytest.mark.contract
async def test_concurrent_synthesis_does_not_block_loop(azure_tts) -> None:
    """Catches a missing run_in_executor — the classic Azure SDK mistake."""
    ticks = 0
    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    await asyncio.gather(*(drain(azure_tts.synthesize("سلام", voice="ur-PK-UzmaNeural"))
                           for _ in range(5)))
    hb.cancel()
    assert ticks > 20, "event loop was blocked during synthesis"
```

---

## Common pitfalls

- **Calling the Azure SDK synchronously.** Blocks the loop, stutters every concurrent call. The heartbeat test above is the guard.
- **A US Azure region.** ~200ms added per synthesis, roughly double the whole TTFB budget.
- **Failing over mid-utterance.** Two voices in one sentence.
- **Using `inworld-tts-1.5-*` for Urdu.** Urdu is TTS-2 only; the 1.5 models silently do the wrong thing.
- **Large audio buffers.** Slower first byte and mushy barge-in.
- **Forgetting v3 is not real-time.** It is for pre-rendered assets only. Never call it on a live turn.
