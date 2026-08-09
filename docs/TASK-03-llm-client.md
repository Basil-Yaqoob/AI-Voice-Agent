# TASK-03 — LLM client and prompt assembly

| | |
|---|---|
| **Depends on** | TASK-02 |
| **Blocks** | 07, 08, 12 |
| **Estimate** | 1 day |
| **Level** | Intermediate |

---

## Objective

A streaming LLM client behind the `LLMProvider` interface, plus cache-stable prompt assembly. Two things must be right or the project fails quietly:

1. **First token inside 300ms.** Everything downstream waits on it.
2. **A byte-identical cached prefix.** Cache reads bill at 0.1× of input. A drifting prefix triples the model bill with no visible symptom — it is the most expensive silent bug in this codebase.

---

## Steps

### 1. Interface

`src/agent/interfaces/llm.py` — no vendor imports here (enforced by import-linter):

```python
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol

@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str

@dataclass(frozen=True)
class TextDelta:
    text: str

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]

@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

@dataclass(frozen=True)
class Done:
    usage: Usage
    stop_reason: str

LLMEvent = TextDelta | ToolCall | Done


class LLMProvider(Protocol):
    async def stream(
        self,
        messages: list[Message],
        *,
        system: str,
        tools: list[dict[str, object]],
    ) -> AsyncIterator[LLMEvent]: ...
```

`LLMEvent` is a union, not a string stream. If tool calls are not distinguishable at the event level, the chunker will happily read a tool call aloud to the caller.

### 2. Prompt assembly — the cache-critical part

`src/agent/language/prompts.py`. Must be a **pure function**: no clock, no randomness, no dict iteration order dependence.

```python
def build_cached_prefix(
    *, company: str, persona: str, corpus: str, tools: list[dict[str, object]]
) -> str:
    """Byte-identical across calls for identical inputs. Verified by test."""
    tool_block = "\n".join(
        f"- {t['name']}: {t['description']}"
        for t in sorted(tools, key=lambda t: str(t["name"]))   # sort or cache drifts
    )
    return "\n\n".join([
        persona,
        _CODE_SWITCHING_RULES,
        f"## {company} knowledge\n{corpus}",
        f"## Available tools\n{tool_block}",
    ])
```

The `sorted()` is not cosmetic. Tool registries built from a dict can reorder between processes; that reordering changes the prefix bytes and invalidates the cache for every request afterwards.

**What must never enter the prefix:** timestamps, `call_id`, caller name, account state, time of day, retrieved chunks. All of that goes in the fresh suffix or a mid-conversation system message.

### 3. Code-switching instructions

This block is the product. It is what makes the agent sound Pakistani rather than translated.

```python
_CODE_SWITCHING_RULES = """
## How you speak

You speak the way a good Karachi call-centre agent speaks: Urdu sentence
structure with English words left in English where a real person would leave
them in English. You are warm, brief, and you never lecture.

Keep in English: product names, plan names, and the everyday business nouns
Pakistani customers themselves use — order, delivery, tracking, account,
balance, package, complaint, refund, invoice.

Keep in Urdu: everything structural — verbs, pronouns, connectives, politeness.

Do NOT switch into full English sentences when a topic gets technical. Explain
technical things in Urdu using the English noun.

Match the customer. If they speak more English, use more English. If they speak
pure Urdu, reduce English to the words that have no natural Urdu equivalent.

Good:  آپ کا order کل deliver ہو جائے گا، میں tracking check کر کے بتاتا ہوں۔
Bad:   آپ کا فرمائش کل ترسیل ہو جائے گی۔          (over-Persianised; nobody talks like this)
Bad:   Your order will be delivered tomorrow.      (dropped out of Urdu entirely)

Keep replies to one or two sentences. This is a phone call — the customer
cannot re-read you, and long turns get interrupted.
"""
```

The two `Bad:` examples earn their tokens. Both failure modes are real and models fall into them from opposite directions.

### 4. Claude adapter

`src/agent/providers/llm_claude.py` — the only file that imports `anthropic`.

```python
import anthropic
from agent.interfaces.llm import (Done, LLMProvider, Message,
                                  TextDelta, ToolCall, Usage)

class ClaudeProvider(LLMProvider):
    def __init__(self, *, api_key: str, model: str, effort: str,
                 max_tokens: int, fast_mode: bool = False) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model, self._effort = model, effort
        self._max_tokens, self._fast = max_tokens, fast_mode

    async def stream(self, messages, *, system, tools):
        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},   # ← the cache breakpoint
            }],
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "tools": tools,
            # Adaptive thinking at low effort. Do NOT disable thinking — see below.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort},
        }

        if self._fast:
            # Opus 5 fast mode: up to 2.5x output throughput at $10/$50 per MTok.
            # The only lever that converts money directly into latency headroom.
            create = self._client.beta.messages.stream
            kwargs |= {"speed": "fast", "betas": ["fast-mode-2026-02-01"]}
        else:
            create = self._client.messages.stream

        async with create(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield TextDelta(event.delta.text)

            final = await stream.get_final_message()
            for block in final.content:
                if block.type == "tool_use":
                    yield ToolCall(block.id, block.name, dict(block.input))
            u = final.usage
            yield Done(
                usage=Usage(
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                ),
                stop_reason=final.stop_reason or "end_turn",
            )
```

> ### Do not disable thinking to save latency
>
> It is the obvious optimisation and it is a trap. On Claude Opus 5, `thinking: {"type": "disabled"}` has two documented failure modes:
>
> 1. The model sometimes writes a **tool call into visible text** instead of emitting a `tool_use` block. The turn completes normally, no error is raised, and the tool never runs — so the caller is told their balance was checked when nothing happened. A silent correctness bug in a voice product is much worse than 80ms.
> 2. `<thinking>` tags can leak into the response and get read aloud.
>
> `adaptive` + `effort: "low"` is both safer and nearly as fast. Also note `{"type": "disabled"}` is rejected outright at effort `xhigh`/`max`.

### 5. Cancellation

Barge-in must genuinely cancel the stream, not abandon it. An orphaned stream keeps billing and can emit audio after the caller has moved on. The `async with` above handles this correctly when the consuming task is cancelled — but it must be *tested*, not assumed. See the fake's `cancelled` flag in `02-TESTING-STRATEGY.md`.

### 6. Verify the cache actually engages

Add a startup log line and a contract test. On turn two of any session, `cache_read_tokens` must be greater than zero. If it is not, something in the prefix is drifting and you are paying 10× for input tokens.

---

## Acceptance criteria

- [ ] `ClaudeProvider` implements `LLMProvider` and passes `mypy --strict`
- [ ] Streaming yields `TextDelta` events before the full response completes
- [ ] `build_cached_prefix()` returns byte-identical output across repeated calls, frozen clocks, and shuffled tool input
- [ ] Second turn of a live session reports `cache_read_tokens > 0`
- [ ] Cancelling the consuming task cancels the upstream request
- [ ] Tool calls arrive as `ToolCall` events, never as `TextDelta` text
- [ ] `LLM_FAST_MODE=true` sends `speed="fast"` with the beta flag
- [ ] `anthropic` is imported in exactly one file
- [ ] Measured TTFT against the real API is recorded in the task PR

---

## Unit tests

`tests/unit/test_prompts.py`:

| Test | Asserts |
|---|---|
| `test_prefix_byte_stable` | Two calls, identical inputs → identical bytes |
| `test_prefix_no_clock_dependency` | `freeze_time` at two dates → identical output |
| `test_prefix_tools_sorted` | Shuffled tool list → same prefix |
| `test_prefix_excludes_volatile` | `call_id`/timestamp never appear |
| `test_code_switching_rules_present` | Both `Bad:` examples survive edits |

`tests/unit/test_llm_client.py` (against `FakeLLM`):

| Test | Asserts |
|---|---|
| `test_stream_yields_deltas_before_done` | Incremental, not batched |
| `test_tool_call_is_distinct_event` | Tool call not emitted as text |
| `test_done_carries_usage` | Token accounting present |
| `test_cancellation_propagates` | `fake.cancelled is True` |
| `test_max_tokens_respected` | Config threaded through |

```python
async def test_cancellation_propagates() -> None:
    """Barge-in must actually kill the request, not orphan it."""
    fake = FakeLLM(script=["ایک لمبا جواب " * 50], tokens_per_sec=20)
    task = asyncio.create_task(drain(fake.stream([], system="", tools=[])))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.cancelled is True
```

`tests/latency/test_llm_budget.py`:

```python
@pytest.mark.latency
async def test_first_delta_within_ttft_budget(settings) -> None:
    fake = FakeLLM(script=["جی بالکل"], ttft_ms=250)
    start = time.perf_counter()
    async for ev in fake.stream([], system="", tools=[]):
        if isinstance(ev, TextDelta):
            break
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < settings.budget_ttft_ms
```

---

## Common pitfalls

- **A timestamp in the system prompt.** Kills caching completely. The byte-stability test is the guard; write it first.
- **Unsorted tool definitions.** Same effect, harder to spot, and it only manifests across process restarts.
- **Awaiting the full response before chunking.** Discards the single biggest latency win in the pipeline.
- **Disabling thinking.** See the callout above — silently drops tool calls.
- **`max_tokens` too high.** A spoken turn is one or two sentences. 400 is generous; 4000 invites a rambling agent that gets interrupted and wastes tokens.
