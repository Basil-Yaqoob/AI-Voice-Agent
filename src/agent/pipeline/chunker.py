"""Token stream to speakable sentences.

The single largest latency win in the pipeline. TTS starts on sentence one while
the model is still writing sentence three, which turns a serial
generate-then-speak into a pipeline and removes most of the model's output time
from what the caller experiences.

Three things here are load-bearing, and each is a real defect if missed:

* **The Urdu full stop is ``۔`` (U+06D4), not ``.``** Miss it and the chunker
  never fires on Urdu text. Nothing errors; the agent just gets slow.
* **Tool calls must never reach TTS.** Reading one aloud is unmistakable, and
  it is the kind of thing that only shows up in front of a client.
* **Fragments below a few words sound clipped.** TTS prosody needs a phrase to
  work with; "جی۔" alone comes out robotic.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from agent.interfaces.llm import Done, LLMEvent, ToolCall

__all__ = ["MAX_CHARS", "MIN_CHARS", "chunk_sentences", "split_sentences"]

# ۔ U+06D4 Urdu full stop, ؟ U+061F Urdu question mark. Latin marks appear too:
# the model writes code-switched text and may terminate either way.
_TERMINATORS = "۔؟!?."

# Below this, accumulate rather than speak. Tuned for phrases, not words.
MIN_CHARS = 15
# Above this with no terminator in sight, flush at a word break so a model that
# forgets punctuation cannot buffer the whole turn.
MAX_CHARS = 200

# "Rs. 1,500" and "No. 5" must not split at the full stop. Urdu terminators
# never carry this ambiguity, so the guard is Latin-only.
#
# An explicit list, not a length heuristic: "...on the way." ends in a
# three-letter word and must still split. Guessing by length silently disables
# sentence breaks for a large share of ordinary English.
_ABBREVIATIONS = (
    "rs",
    "no",
    "dr",
    "mr",
    "mrs",
    "ms",
    "st",
    "vs",
    "etc",
    "approx",
    "inc",
    "ltd",
)
_ABBREVIATION = re.compile(
    r"(?:^|\s)(?:" + "|".join(_ABBREVIATIONS) + r"|[A-Za-z])$", re.IGNORECASE
)


def _is_boundary(buffer: str, index: int) -> bool:
    """Whether the terminator at ``index`` ends a speakable unit."""
    char = buffer[index]
    following = buffer[index + 1 : index + 2]

    # A terminator mid-token is not a boundary: decimals, URLs, "v1.2".
    if following and not following.isspace():
        return False

    if char == ".":
        head = buffer[:index]
        if _ABBREVIATION.search(head):
            return False

    return True


def split_sentences(text: str) -> tuple[list[str], str]:
    """Split off complete sentences, returning them and the unfinished tail.

    Pure and synchronous so the boundary rules can be tested exhaustively
    without driving an async stream.
    """
    sentences: list[str] = []
    start = 0

    for index, char in enumerate(text):
        if char not in _TERMINATORS or not _is_boundary(text, index):
            continue
        candidate = text[start : index + 1].strip()
        if len(candidate) < MIN_CHARS:
            continue  # too short to speak well; keep accumulating
        sentences.append(candidate)
        start = index + 1

    return sentences, text[start:]


def _force_flush(buffer: str) -> tuple[str | None, str]:
    """Emit at a word break when the model has run on without punctuation."""
    if len(buffer) <= MAX_CHARS:
        return None, buffer
    cut = buffer.rfind(" ", 0, MAX_CHARS)
    if cut < MIN_CHARS:
        return None, buffer  # one enormous token; wait rather than split a word
    return buffer[:cut].strip(), buffer[cut:]


async def chunk_sentences(events: AsyncIterator[LLMEvent]) -> AsyncIterator[str]:
    """Yield speakable sentences from a model event stream.

    Text between tool calls is flushed before the call so partial speech is not
    lost, and the call itself is dropped rather than spoken.
    """
    buffer = ""

    async for event in events:
        if isinstance(event, ToolCall):
            tail = buffer.strip()
            if tail:
                yield tail
            buffer = ""
            continue

        if isinstance(event, Done):
            break

        buffer += event.text

        sentences, buffer = split_sentences(buffer)
        for sentence in sentences:
            yield sentence

        flushed, buffer = _force_flush(buffer)
        if flushed:
            yield flushed

    tail = buffer.strip()
    if tail:
        yield tail
