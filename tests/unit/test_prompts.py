"""TASK-03 prompt assembly.

Written before the implementation. The cached prefix drifting is the most
expensive silent bug in this codebase: cache reads bill at 0.1x of input, so a
prefix that changes between requests triples the model bill with no visible
symptom whatsoever.
"""

from __future__ import annotations

import time

from freezegun import freeze_time

from agent.interfaces.llm import ToolDef
from agent.language.prompts import (
    CODE_SWITCHING_RULES,
    build_cached_prefix,
    build_turn_suffix,
    prefix_is_cacheable,
)

CORPUS = "Acme sells prepaid internet packages across Pakistan."
PERSONA = "You are Acme's customer support agent."

TOOLS = [
    ToolDef("get_order_status", "Look up an order", {"type": "object", "properties": {}}),
    ToolDef("get_balance", "Fetch account balance", {"type": "object", "properties": {}}),
    ToolDef("raise_complaint", "File a complaint", {"type": "object", "properties": {}}),
]


# ── cache stability: the money tests ────────────────────────────────────


def test_prefix_byte_stable() -> None:
    a = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    time.sleep(0.01)
    b = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    assert a == b


def test_prefix_no_clock_dependency() -> None:
    """A timestamp anywhere in the prefix kills caching completely."""
    with freeze_time("2026-01-01"):
        a = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    with freeze_time("2027-06-15"):
        b = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    assert a == b


def test_prefix_tools_sorted() -> None:
    """Tool registries built from a dict reorder between processes. Sorting is
    not cosmetic — reordering invalidates the prefix for every later request."""
    forward = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    shuffled = build_cached_prefix(
        company="Acme", persona=PERSONA, corpus=CORPUS, tools=list(reversed(TOOLS))
    )
    assert forward == shuffled


def test_prefix_excludes_volatile_context() -> None:
    prefix = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    for volatile in ("call_id", "session_id", "timestamp", "2026-", "2027-"):
        assert volatile not in prefix


def test_realistic_corpus_produces_cacheable_prefix() -> None:
    """Opus 5 caches from 512 tokens (~2048 chars). Below that the breakpoint is
    accepted but never hits, and every request bills at full input price."""
    realistic = CORPUS + "\n" + ("Package details and policy text. " * 60)
    prefix = build_cached_prefix(company="Acme", persona=PERSONA, corpus=realistic, tools=TOOLS)
    assert prefix_is_cacheable(prefix)


def test_tiny_corpus_flagged_as_uncacheable() -> None:
    """The guard must actually fire — the base block alone sits just under the
    threshold, so a deployment with a one-line corpus is genuinely at risk."""
    prefix = build_cached_prefix(company="A", persona="Agent.", corpus="Hi.", tools=[])
    assert not prefix_is_cacheable(prefix)


# ── the fresh suffix must carry everything volatile ──────────────────────


def test_suffix_carries_retrieved_chunks() -> None:
    suffix = build_turn_suffix(chunks=["Package X costs 1500 rupees."], caller_name="Ali")
    assert "Package X" in suffix


def test_suffix_varies_while_prefix_does_not() -> None:
    a = build_turn_suffix(chunks=["one"], caller_name="Ali")
    b = build_turn_suffix(chunks=["two"], caller_name="Sara")
    assert a != b


def test_empty_suffix_when_no_context() -> None:
    assert build_turn_suffix(chunks=[], caller_name=None) == ""


# ── code-switching instructions are the product ──────────────────────────


def test_code_switching_rules_in_prefix() -> None:
    prefix = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    assert CODE_SWITCHING_RULES.strip() in prefix


def test_rules_keep_both_negative_examples() -> None:
    """Both failure modes are real and models fall into them from opposite
    directions: over-Persianised Urdu, and dropping out of Urdu entirely."""
    assert CODE_SWITCHING_RULES.count("Bad:") >= 2


def test_rules_name_the_english_nouns_to_preserve() -> None:
    for noun in ("order", "delivery", "tracking", "balance"):
        assert noun in CODE_SWITCHING_RULES


def test_rules_contain_urdu_script_example() -> None:
    """An all-English instruction block produces all-English output."""
    assert any("؀" <= ch <= "ۿ" for ch in CODE_SWITCHING_RULES)


def test_rules_cap_response_length() -> None:
    """Long turns get interrupted and waste tokens; the caller cannot re-read."""
    lowered = CODE_SWITCHING_RULES.lower()
    assert "one or two sentences" in lowered or "brief" in lowered


# ── company and corpus land in the prefix ─────────────────────────────────


def test_corpus_included() -> None:
    prefix = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    assert CORPUS in prefix
    assert "Acme" in prefix


def test_all_tool_names_present() -> None:
    prefix = build_cached_prefix(company="Acme", persona=PERSONA, corpus=CORPUS, tools=TOOLS)
    for tool in TOOLS:
        assert tool.name in prefix
