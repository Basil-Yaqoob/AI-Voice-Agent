"""System prompt assembly, split at the cache breakpoint.

Everything here is a pure function: no clock, no randomness, no global state. The
cached prefix must be byte-identical across every request of a session, because a
prefix that drifts silently triples the model bill — cache reads bill at 0.1x of
input and nothing visibly breaks when they stop happening.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent.interfaces.llm import ToolDef

__all__ = [
    "CODE_SWITCHING_RULES",
    "MIN_CACHEABLE_CHARS",
    "build_cached_prefix",
    "build_turn_suffix",
    "prefix_is_cacheable",
]

# Claude Opus 5 will not cache a prefix below 512 tokens. At roughly 4 characters
# per token for English-dominant text that is ~2048 characters. Below it the
# cache_control breakpoint is accepted by the API but never produces a hit, so
# every request bills at full input price with no error to tell you.
#
# The persona + rules + escalation block alone lands just under this, so a
# deployment with a very small corpus is genuinely at risk. Providers call
# prefix_is_cacheable() at startup and warn.
MIN_CACHEABLE_CHARS = 2048


def prefix_is_cacheable(prefix: str) -> bool:
    """Whether the prefix is long enough for the cache to engage at all."""
    return len(prefix) >= MIN_CACHEABLE_CHARS


# This block is the product. It is what makes the agent sound Pakistani rather
# than translated, and it is why a smaller model is not automatically fine here.
CODE_SWITCHING_RULES = """
## How you speak

You speak the way a good Karachi call-centre agent speaks: Urdu sentence
structure with English words left in English where a real person would leave
them in English. You are warm, brief, and you never lecture.

Keep in English: product names, plan names, and the everyday business nouns
Pakistani customers themselves use - order, delivery, tracking, account,
balance, package, complaint, refund, invoice, courier.

Keep in Urdu: everything structural - verbs, pronouns, connectives, politeness.

Do NOT switch into full English sentences when a topic gets technical. Explain
technical things in Urdu using the English noun.

Match the customer. If they speak more English, use more English. If they speak
pure Urdu, reduce English to the words that have no natural Urdu equivalent.

Good:  آپ کا order کل deliver ہو جائے گا، میں tracking check کر کے بتاتا ہوں۔
Bad:   آپ کا فرمائش کل ترسیل ہو جائے گی۔          (over-Persianised; nobody talks like this)
Bad:   Your order will be delivered tomorrow.      (dropped out of Urdu entirely)

Numbers, amounts and dates are read aloud, so write them as digits and let the
normaliser convert them. Never spell out a phone number as a single figure.

Keep replies to one or two sentences. This is a phone call - the customer
cannot re-read you, and long turns get interrupted.
"""


def build_cached_prefix(
    *,
    company: str,
    persona: str,
    corpus: str,
    tools: Sequence[ToolDef] = (),
) -> str:
    """Assemble the cacheable region of the system prompt.

    Pure and deterministic for identical inputs. Nothing volatile may enter here:
    no timestamps, call ids, caller names or retrieved chunks. Those belong in
    :func:`build_turn_suffix`.
    """
    # Sorting is load-bearing, not cosmetic. Tool registries built from a dict can
    # reorder between processes; that reorders the prefix bytes and invalidates
    # the cache for every subsequent request.
    tool_lines = "\n".join(
        f"- {tool.name}: {tool.description}" for tool in sorted(tools, key=lambda t: t.name)
    )
    tool_block = tool_lines or "- (none)"

    return "\n\n".join(
        [
            persona.strip(),
            CODE_SWITCHING_RULES.strip(),
            f"## What you know about {company}\n{corpus.strip()}",
            f"## Tools you can call\n{tool_block}",
            _ESCALATION_RULES.strip(),
        ]
    )


def build_turn_suffix(
    *,
    chunks: Sequence[str] = (),
    caller_name: str | None = None,
) -> str:
    """Per-turn context. Varies freely — this sits after the cache breakpoint."""
    parts: list[str] = []
    if caller_name:
        parts.append(f"The caller's name is {caller_name}.")
    if chunks:
        joined = "\n\n".join(c.strip() for c in chunks)
        parts.append(f"## Relevant information for this question\n{joined}")
    return "\n\n".join(parts)


_ESCALATION_RULES = """
## When you do not know

Say so plainly in Urdu and offer to connect a colleague. Never invent an order
number, a date, an amount or a policy. A caller who is told "مجھے معلوم نہیں،
میں آپ کو ایک colleague سے connect کر دیتا ہوں" trusts you more than one who is
given a confident wrong answer.

Use a tool whenever the answer depends on live data - balances, order status,
ticket state. Do not guess at values a tool can give you.
"""
