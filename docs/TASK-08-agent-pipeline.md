# TASK-08 — Agent pipeline: the turn loop

| | |
|---|---|
| **Depends on** | 03, 04, 05, 06, 07 |
| **Blocks** | 09, 10, 11, 13 |
| **Estimate** | 2–3 days |
| **Level** | Advanced |

---

## Objective

Wire every component into a working conversational turn: audio in → transcript → prompt → tokens → sentences → normalised text → audio out, with barge-in cancellation throughout.

This is the hot path. Every rule in `00-ARCHITECTURE.md` applies with full force here, and this directory should get stricter code review than anywhere else in the repo — one blocking call is audible to the caller.

---

## Steps

### 1. Sentence chunker — the biggest latency win in the build

`src/agent/pipeline/chunker.py`. Consumes the LLM token stream and emits the first **speakable** unit as soon as one exists, so TTS starts on sentence one while the model is still writing sentence three.

```python
_TERMINATORS = "۔.!?؟\n"      # Urdu full stop ۔ and question mark ؟ included
_MIN_CHARS = 15
_MAX_CHARS = 200

async def chunk_sentences(events: AsyncIterator[LLMEvent]) -> AsyncIterator[str]:
    buf = ""
    async for ev in events:
        if isinstance(ev, ToolCall):
            if buf.strip():
                yield buf.strip()      # flush before the tool runs
                buf = ""
            continue                    # NEVER speak a tool call
        if isinstance(ev, Done):
            break
        buf += ev.text

        while (idx := _find_boundary(buf)) is not None:
            candidate, rest = buf[: idx + 1], buf[idx + 1 :]
            if len(candidate.strip()) < _MIN_CHARS and rest:
                break                   # too short to speak well; keep accumulating
            yield candidate.strip()
            buf = rest

        if len(buf) > _MAX_CHARS:       # no punctuation in sight — flush at a word break
            cut = buf.rfind(" ", 0, _MAX_CHARS)
            if cut > _MIN_CHARS:
                yield buf[:cut].strip()
                buf = buf[cut:]

    if buf.strip():
        yield buf.strip()
```

Three subtleties that are each a real bug if missed:

- **Urdu punctuation.** The Urdu full stop is `۔` (U+06D4), not `.`. Miss it and the chunker never fires on Urdu text, silently reverting to one big buffered utterance and forfeiting the whole optimisation.
- **`_MIN_CHARS`.** TTS prosody is bad on tiny fragments — "جی۔" alone sounds clipped and robotic. Accumulate instead.
- **Tool calls must never reach TTS.** Flush the buffer, then skip. A tool call read aloud is an unmistakable demo-killer.

Do not split inside numbers or abbreviations — `Rs. 1,500` contains a `.` that is not a sentence boundary. `_find_boundary` must check that the character after the terminator is whitespace or end-of-string.

### 2. Turn execution

`src/agent/pipeline/turn.py`. One user turn, end to end:

```python
async def execute_turn(self, final_text: str, metrics: TurnMetrics) -> None:
    with metrics.stage("retrieval"):
        chunks = await self._prefetcher.resolve(final_text)   # usually ~0ms

    if cached := await self._answer_cache.get(final_text):
        await self._speak(cached.text, metrics)               # skip the LLM entirely
        return

    self._history.append(Message(role="user", content=final_text))
    messages = self._history + [_chunks_as_context(chunks)]

    spoken: list[str] = []
    try:
        stream = self._llm.stream(messages, system=self._cached_prefix,
                                  tools=self._tools)
        async for sentence in chunk_sentences(stream):
            metrics.mark_first_token_if_unset()
            text = normalize_for_tts(sentence)
            await self._speak(text, metrics)
            spoken.append(sentence)
    except asyncio.CancelledError:
        # Barge-in. Record only what the caller actually HEARD.
        raise
    finally:
        if spoken:
            self._history.append(Message(role="assistant", content=" ".join(spoken)))
```

**The `finally` block is the important part and it is routinely got wrong.** On barge-in, conversation history must record what the caller *heard*, not what the model *intended to say*. Store the full intended response and the agent will later reference information the caller never received — "as I mentioned, your refund is processing" when it was cut off before saying that.

### 3. Barge-in

`src/agent/pipeline/interrupt.py`. When VAD detects speech while the agent is speaking, three things must happen within ~100ms:

```python
async def on_user_speech_started(self) -> None:
    if not self._agent_speaking:
        return
    await self._audio_out.stop()          # 1. stop pushing audio immediately
    if self._turn_task:
        self._turn_task.cancel()          # 2. cancel LLM + TTS
        with suppress(asyncio.CancelledError):
            await self._turn_task
    self._agent_speaking = False          # 3. history recorded by turn's finally
```

Order matters. Stop the audio output *first* — cancelling the task first still leaves buffered audio playing, so the caller keeps hearing the agent after interrupting, which feels broken.

Cancellation must propagate all the way to the provider connections. An orphaned LLM stream keeps billing; an orphaned TTS stream can emit audio into the *next* turn.

**Guard against false barge-in.** A cough, background TV, or `long_pause_mid.wav`-style hesitation must not interrupt. Require a minimum speech duration (~150ms) before treating VAD as a genuine interruption.

### 4. Session state machine

`src/agent/pipeline/session.py`:

```
     ┌─────────┐  caller connects
     │  IDLE   │──────────────────┐
     └─────────┘                  ▼
                          ┌──────────────┐  greeting (pre-rendered, 0ms)
                          │   GREETING   │
                          └──────┬───────┘
                                 ▼
   final transcript      ┌──────────────┐
        ┌────────────────│  LISTENING   │◄───────────┐
        │                └──────────────┘            │
        ▼                       ▲                    │
 ┌──────────────┐  barge-in     │                    │ done speaking
 │  THINKING    │───────────────┘                    │
 └──────┬───────┘                                    │
        │ first sentence                             │
        ▼                                            │
 ┌──────────────┐   barge-in                         │
 │   SPEAKING   │────────────────────────────────────┘
 └──────┬───────┘
        │ caller hangs up / handoff
        ▼
 ┌──────────────┐
 │  TERMINATED  │
 └──────────────┘
```

Model this explicitly rather than with a scattering of booleans. Voice bugs are overwhelmingly state bugs — two turns running concurrently, TTS playing after hangup, a barge-in arriving during `GREETING`. An explicit machine makes illegal transitions raise instead of producing weird audio.

### 5. Tool execution

Tool calls run **during** the turn, and the model needs their results to continue. Keep them fast — anything over ~500ms needs a spoken filler ("ایک سیکنڈ...", pre-rendered, zero latency) so the caller does not sit in silence.

Tool errors return to the model as tool results with the error text, never as exceptions. The model then explains the problem in Urdu, which is exactly the behaviour you want.

### 6. Error handling

Every failure resolves to something the caller can hear:

| Failure | Behaviour |
|---|---|
| No first token within 2s | Play pre-rendered filler, retry once, then handoff line |
| STT disconnects | One silent reconnect; second failure → re-ask prompt |
| TTS primary fails pre-first-byte | Failover (TASK-05), continue silently |
| TTS fails mid-utterance | Stop, play recovery line, continue conversation |
| Tool raises | Return error as tool result; model explains it |
| Knowledge store down | Continue on cached corpus only |
| Unrecoverable | Pre-rendered handoff line, then transfer or callback |

Never let an exception reach the caller as silence. Silence is interpreted as a dropped call and the caller hangs up.

---

## Acceptance criteria

- [ ] A full turn completes: audio in → audio out, in Urdu
- [ ] **First audio within 800ms** of end-of-speech (p50, measured over 20 turns)
- [ ] TTS begins on sentence one before the LLM finishes generating
- [ ] Urdu `۔` and `؟` trigger chunking
- [ ] Tool calls never reach TTS
- [ ] Barge-in stops audio within 100ms
- [ ] Post-barge-in history contains only what was spoken
- [ ] False barge-in suppressed on `long_pause_mid.wav`
- [ ] Cancellation reaches provider connections (no orphaned tasks — assert task count)
- [ ] Illegal state transitions raise rather than misbehave
- [ ] Every failure in the table produces audible output, never silence
- [ ] Multi-turn conversation maintains coherent history
- [ ] `ruff` ASYNC clean across `pipeline/`

---

## Unit tests

`tests/unit/test_chunker.py` — highest density of real bugs:

| Test | Asserts |
|---|---|
| `test_splits_on_urdu_full_stop` | `۔` recognised |
| `test_splits_on_urdu_question_mark` | `؟` recognised |
| `test_splits_on_latin_punctuation` | Mixed-script output works |
| `test_short_fragment_accumulates` | No "جی۔" alone |
| `test_does_not_split_decimal` | `Rs. 1,500` intact |
| `test_does_not_split_abbreviation` | `ORD-4471` intact |
| `test_flushes_at_max_length` | No unbounded buffering |
| `test_tool_call_never_yielded` | **Demo-killer guard** |
| `test_flushes_buffer_before_tool_call` | Partial speech not lost |
| `test_final_partial_flushed` | Nothing dropped at stream end |

```python
async def test_splits_on_urdu_full_stop() -> None:
    """۔ (U+06D4), not '.'. Missing this silently disables chunking for Urdu."""
    stream = fake_stream("پہلا جملہ۔ دوسرا جملہ۔")
    out = [s async for s in chunk_sentences(stream)]
    assert len(out) == 2
    assert out[0] == "پہلا جملہ۔"


async def test_tool_call_never_yielded() -> None:
    stream = fake_events([
        TextDelta("ایک سیکنڈ۔ "),
        ToolCall("t1", "get_order_status", {"id": "4471"}),
        TextDelta("آپ کا آرڈر راستے میں ہے۔"),
    ])
    out = [s async for s in chunk_sentences(stream)]
    assert not any("get_order_status" in s for s in out)
    assert out[0] == "ایک سیکنڈ۔"
```

`tests/unit/test_barge_in.py`:

| Test | Asserts |
|---|---|
| `test_stops_audio_within_100ms` | Timing enforced |
| `test_cancels_llm_stream` | `fake_llm.cancelled is True` |
| `test_history_records_spoken_only` | **The subtle one** |
| `test_short_noise_does_not_interrupt` | 150ms minimum honoured |
| `test_no_task_leak` | Task count returns to baseline |

```python
async def test_history_records_spoken_only() -> None:
    """Recording the intended response makes the agent reference unheard info."""
    llm = FakeLLM(script=["پہلا حصہ۔ دوسرا حصہ۔ تیسرا حصہ۔"], tokens_per_sec=30)
    session = build_session(llm=llm)

    task = asyncio.create_task(session.execute_turn("سوال", TurnMetrics()))
    await asyncio.sleep(0.08)          # ~first sentence spoken
    await session.on_user_speech_started()

    assistant = [m for m in session.history if m.role == "assistant"][-1]
    assert "پہلا حصہ" in assistant.content
    assert "تیسرا حصہ" not in assistant.content
```

`tests/unit/test_session_state.py`:

| Test | Asserts |
|---|---|
| `test_valid_transitions_allowed` | Happy path |
| `test_illegal_transition_raises` | e.g. SPEAKING → GREETING |
| `test_barge_in_during_greeting_handled` | Common edge case |
| `test_hangup_from_any_state` | Always terminable |

`tests/latency/test_turn_budget.py`:

```python
@pytest.mark.latency
async def test_full_turn_within_budget(settings) -> None:
    """The headline number. Fakes carry realistic per-stage delays."""
    session = build_session(
        stt=FakeSTT(final_delay_ms=100),
        llm=FakeLLM(script=["جی، آپ کا آرڈر کل آئے گا۔"], ttft_ms=280),
        tts=FakeTTS(ttfb_ms=100),
        knowledge=FakeStore(delay_ms=180),   # hidden by prefetch
    )
    metrics = await run_turn(session, "codeswitch_order.wav")
    assert metrics.first_audio_ms < settings.budget_total_ms


@pytest.mark.latency
async def test_tts_starts_before_llm_finishes() -> None:
    """Proves the chunker is actually pipelining rather than buffering."""
    llm = FakeLLM(script=["پہلا جملہ۔ " * 10], tokens_per_sec=20)
    tts = FakeTTS(ttfb_ms=10)
    session = build_session(llm=llm, tts=tts)

    await session.execute_turn("سوال", TurnMetrics())
    assert tts.first_call_at < llm.stream_completed_at
```

---

## Common pitfalls

- **Forgetting `۔`.** The chunker silently stops working for Urdu — no error, just a slow agent.
- **Speaking tool calls.** Instantly kills a demo.
- **Recording intended rather than spoken text.** Produces confusing references to things the caller never heard.
- **Cancelling the task before stopping audio output.** Buffered audio keeps playing after the interrupt.
- **Booleans instead of a state machine.** Concurrent turns and post-hangup audio.
- **Letting exceptions become silence.** Callers hang up.
- **Any blocking call in this directory.** Audible stutter for every concurrent caller.
