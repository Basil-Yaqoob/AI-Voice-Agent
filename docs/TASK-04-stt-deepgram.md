# TASK-04 — Speech-to-text (Deepgram Nova-3 Urdu)

| | |
|---|---|
| **Depends on** | TASK-02 |
| **Blocks** | 08, 09 |
| **Estimate** | 1 day |
| **Level** | Intermediate |

---

## Objective

Streaming Urdu transcription behind the `STTProvider` interface, emitting **partial** transcripts continuously and a **final** transcript at end of turn.

The partials are not a nicety. They are what makes speculative prefetch possible (TASK-07), which is what keeps retrieval off the critical path. An STT integration that only emits finals silently forfeits ~200ms.

**Keyterm prompting is the highest-value single feature in this task.** Nova-3 Urdu supports it, and without it your callers' English nouns — *order*, *tracking*, *Premium Plan* — get mangled inside Urdu sentences. Those are exactly the words the intent depends on.

---

## Steps

### 1. Interface

`src/agent/interfaces/stt.py`:

```python
from dataclasses import dataclass
from typing import AsyncIterator, Protocol, Sequence

@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool
    confidence: float
    speech_final: bool = False   # Deepgram's own endpoint signal

class STTProvider(Protocol):
    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keyterms: Sequence[str],
    ) -> AsyncIterator[Transcript]: ...
```

### 2. Keyterm file

`src/agent/knowledge/keyterms.txt` — one term per line, loaded at startup:

```
# Product and brand names — highest value entries
Premium Plan
Basic Plan
Acme Pay

# Everyday English nouns Pakistani callers use inside Urdu sentences
order
delivery
tracking
account
balance
package
complaint
refund
invoice
courier
```

Two rules learned the hard way:

- **Cap the list.** Deepgram enforces a limit and, more importantly, an over-broad list biases the model toward these words even when they were not said. 40–60 high-value terms beats 300.
- **Real names, not aspirational ones.** Include what callers actually say. If nobody says "subscription tier", leave it out.

### 3. Deepgram adapter

`src/agent/providers/stt_deepgram.py` — the only file importing `deepgram`.

```python
from deepgram import AsyncLiveClient, LiveOptions, LiveTranscriptionEvents
from agent.interfaces.stt import STTProvider, Transcript

class DeepgramSTT(STTProvider):
    def __init__(self, *, api_key: str, sample_rate: int = 16_000) -> None:
        self._api_key, self._sample_rate = api_key, sample_rate

    async def stream(self, audio, *, language, keyterms):
        queue: asyncio.Queue[Transcript | None] = asyncio.Queue()
        client = AsyncLiveClient(self._api_key)

        options = LiveOptions(
            model="nova-3",
            language=language,          # "ur"
            encoding="linear16",
            sample_rate=self._sample_rate,
            channels=1,
            interim_results=True,       # ← partials; required for prefetch
            punctuate=True,
            smart_format=True,
            endpointing=180,            # ms; tuned in TASK-09
            keyterm=list(keyterms),     # ← the accuracy win
        )

        async def on_transcript(_self, result, **_):
            alt = result.channel.alternatives[0]
            if not alt.transcript:
                return
            await queue.put(Transcript(
                text=alt.transcript,
                is_final=result.is_final,
                confidence=alt.confidence,
                speech_final=getattr(result, "speech_final", False),
            ))

        client.on(LiveTranscriptionEvents.Transcript, on_transcript)
        client.on(LiveTranscriptionEvents.Error, self._on_error)
        await client.start(options)

        pump = asyncio.create_task(self._pump(client, audio, queue))
        try:
            while (item := await queue.get()) is not None:
                yield item
        finally:
            pump.cancel()
            await client.finish()
```

`interim_results=True` is the load-bearing flag. Without it there are no partials, and TASK-07's prefetch has nothing to fire on.

### 4. Reconnection

A dropped WebSocket mid-call is normal on mobile networks, not exceptional. Reconnect once transparently, buffering audio during the gap so the caller's speech is not lost. On a second failure inside the same call, surface it — the pipeline will speak a re-ask prompt (TASK-08 error handling).

Keep the retry budget tight: one silent retry, then escalate. Three retries with backoff means the caller sits in silence for seconds, which is worse than being asked to repeat themselves.

### 5. Confidence gating

Low-confidence finals should be treated as "I did not hear that" rather than fed to the LLM. A garbled transcript produces a confidently wrong answer, which is worse for trust than an honest re-ask.

Start with a threshold around `0.45` and tune against the audio fixtures. Route below-threshold finals to a re-ask rather than to the model.

### 6. Verify Urdu against real audio

Before wiring anything downstream, run the fixture clips through the real API and eyeball the transcripts with a native speaker. Confirm specifically:

- Urdu returns in Perso-Arabic script (not transliterated)
- English nouns inside Urdu sentences survive as English
- Keyterms are recognised (compare with and without the keyterm list)

Record the with/without keyterm comparison in the PR. That measurement justifies maintaining the list.

---

## Acceptance criteria

- [ ] `DeepgramSTT` implements `STTProvider`, passes `mypy --strict`
- [ ] Partial transcripts arrive **during** speech, not only at the end
- [ ] Finals are marked `is_final=True`
- [ ] Urdu returns in Perso-Arabic script
- [ ] Code-switched audio preserves English words as English
- [ ] Keyterms measurably improve recognition of product names — with/without comparison recorded
- [ ] A dropped connection reconnects once without losing buffered audio
- [ ] Below-threshold confidence routes to re-ask, not to the LLM
- [ ] `deepgram` is imported in exactly one file
- [ ] All 8 audio fixtures produce sensible transcripts (native-speaker reviewed)

---

## Unit tests

`tests/unit/test_stt_contract.py` — against `FakeSTT`, no network:

| Test | Asserts |
|---|---|
| `test_partials_precede_final` | Ordering guarantee holds |
| `test_final_flagged_correctly` | Exactly one `is_final=True` per turn |
| `test_empty_transcript_suppressed` | Blank results never yielded |
| `test_low_confidence_flagged` | Gate applies at the right threshold |
| `test_stream_closes_cleanly` | No task leak on generator close |
| `test_cancellation_closes_socket` | `fake.closed is True` after cancel |

`tests/unit/test_keyterms.py`:

| Test | Asserts |
|---|---|
| `test_keyterms_load` | File parses; comments and blanks skipped |
| `test_keyterms_within_limit` | Count under provider cap |
| `test_keyterms_deduplicated` | No repeats (they waste the budget) |
| `test_keyterms_no_empty_strings` | Blank entries would break the API call |

`tests/integration/test_stt_fixtures.py` — marked `contract`, real API:

```python
@pytest.mark.contract
@pytest.mark.parametrize("fixture,must_contain", [
    ("codeswitch_order.wav", ["order"]),
    ("codeswitch_number.wav", ["tracking"]),
    ("pure_urdu_short.wav", ["آرڈر"]),
])
async def test_real_urdu_transcription(fixture, must_contain, stt) -> None:
    text = await transcribe_fixture(stt, fixture)
    for token in must_contain:
        assert token.lower() in text.lower(), f"{token!r} missing from {text!r}"


@pytest.mark.contract
async def test_keyterms_improve_product_recognition(stt) -> None:
    """Justifies maintaining the keyterm list. Record the delta in the PR."""
    without = await transcribe_fixture(stt, "product_name.wav", keyterms=[])
    with_kt  = await transcribe_fixture(stt, "product_name.wav",
                                        keyterms=["Premium Plan"])
    assert "premium plan" in with_kt.lower()
    assert with_kt != without
```

---

## Common pitfalls

- **`interim_results` left off.** No partials, prefetch dead, ~200ms lost with no error to tell you.
- **Sample-rate mismatch.** LiveKit delivers 48kHz; Deepgram is configured for 16kHz here. Resample or configure to match — a mismatch produces transcripts that are *plausible but wrong*, which is far harder to debug than silence.
- **Feeding low-confidence finals to the LLM.** Produces confident answers to misheard questions.
- **Over-stuffing keyterms.** Biases recognition toward listed words even when unsaid.
- **Assuming Deepgram's `endpointing` replaces your own turn detection.** It is one input to TASK-09, not the whole answer.
