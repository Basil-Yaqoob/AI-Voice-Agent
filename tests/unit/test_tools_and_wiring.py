"""Tool registry, demo backend, ingest chunking and worker bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.knowledge.ingest import chunk_text
from agent.language.prompts import build_cached_prefix
from agent.tools.demo_backend import build_demo_registry
from agent.tools.registry import ToolRegistry

# ── registry ─────────────────────────────────────────────────────────────────


async def test_demo_registry_exposes_tools() -> None:
    registry = build_demo_registry()
    assert len(registry) >= 3
    assert {t.name for t in registry.definitions()} >= {"get_order_status", "get_balance"}


def test_definitions_are_sorted() -> None:
    """Unsorted tools reorder between processes and silently invalidate the
    cached prompt prefix for every later request."""
    names = [t.name for t in build_demo_registry().definitions()]
    assert names == sorted(names)


def test_sorted_definitions_keep_the_prefix_stable() -> None:
    tools = build_demo_registry().definitions()
    a = build_cached_prefix(company="Acme", persona="p", corpus="c", tools=tools)
    b = build_cached_prefix(company="Acme", persona="p", corpus="c", tools=list(reversed(tools)))
    assert a == b


def test_sync_handler_rejected() -> None:
    """A synchronous handler would block the audio frame pump."""
    registry = ToolRegistry()
    with pytest.raises(TypeError, match="async"):
        registry.register("bad", "d", {}, lambda: "no")  # type: ignore[arg-type]


async def test_known_order_returns_status() -> None:
    result = await build_demo_registry().call("get_order_status", {"order_id": "4471"})
    assert not result.is_error
    assert "TCS" in result.content


async def test_order_prefix_is_tolerated() -> None:
    """Callers say 'ORD-4471'; the model passes it through verbatim."""
    result = await build_demo_registry().call("get_order_status", {"order_id": "ORD-4471"})
    assert not result.is_error


async def test_unknown_order_asks_rather_than_inventing() -> None:
    result = await build_demo_registry().call("get_order_status", {"order_id": "9999"})
    assert "not found" in result.content.lower()


async def test_unknown_tool_is_a_result_not_an_exception() -> None:
    result = await build_demo_registry().call("nope", {})
    assert result.is_error
    assert "No such tool" in result.content


async def test_bad_arguments_return_an_error_result() -> None:
    """The model explains a failed lookup in Urdu better than an exception
    handler can, so errors go back into the conversation."""
    result = await build_demo_registry().call("get_balance", {"wrong": "x"})
    assert result.is_error


async def test_handler_exception_is_contained() -> None:
    registry = ToolRegistry()

    async def boom() -> str:
        raise RuntimeError("backend down")

    registry.register("boom", "d", {"type": "object", "properties": {}}, boom)
    result = await registry.call("boom", {})
    assert result.is_error
    assert "backend down" in result.content


# ── ingest chunking ───────────────────────────────────────────────────────────


def test_chunks_split_on_paragraphs() -> None:
    text = "\n\n".join(f"Paragraph {i}. " * 30 for i in range(6))
    chunks = chunk_text(text, source="policy.md")
    assert len(chunks) > 1
    assert all(c.source == "policy.md" for c in chunks)


def test_chunk_ids_are_stable_and_unique() -> None:
    text = "\n\n".join(f"Para {i}. " * 40 for i in range(5))
    first = chunk_text(text, source="a.md")
    again = chunk_text(text, source="a.md")
    assert [c.id for c in first] == [c.id for c in again]
    assert len({c.id for c in first}) == len(first)


def test_short_corpus_is_one_chunk() -> None:
    assert len(chunk_text("Delivery takes two days.", source="a.md")) == 1


def test_empty_corpus_yields_nothing() -> None:
    assert chunk_text("   \n\n  ", source="a.md") == []


def test_keywords_extracted_for_hybrid_search() -> None:
    chunks = chunk_text("The Premium Plan includes unlimited data.", source="p.md")
    assert "premium plan" in chunks[0].keywords or "premium" in " ".join(chunks[0].keywords)


# ── worker wiring ──────────────────────────────────────────────────────────────


def test_main_module_is_runnable() -> None:
    """`make demo` must not fail on an import error."""
    import agent.main as main_module

    assert hasattr(main_module, "VoiceAgent")
    assert hasattr(main_module, "bootstrap")


def test_demo_corpus_is_large_enough_to_cache() -> None:
    """A prefix below the cache minimum bills at full input price on every
    request, with no error to tell you."""
    from agent.knowledge.corpus import plan_for_corpus
    from agent.main import DEMO_CORPUS, DEMO_PERSONA

    prefix = build_cached_prefix(
        company="Acme",
        persona=DEMO_PERSONA,
        corpus=DEMO_CORPUS,
        tools=build_demo_registry().definitions(),
    )
    from agent.language.prompts import prefix_is_cacheable

    assert prefix_is_cacheable(prefix), f"demo prefix is only {len(prefix)} chars"
    assert not plan_for_corpus(DEMO_CORPUS).needs_retrieval


def test_prerender_covers_every_recovery_line() -> None:
    """A missing asset means silence on that failure path."""
    from agent.pipeline.recovery import RECOVERY_LINES
    from agent.tools.prerender import GOODBYE, GREETING

    src = Path("src/agent/tools/prerender.py").read_text(encoding="utf-8")
    assert "RECOVERY_LINES.values()" in src
    assert GREETING[0].endswith(".wav") and GOODBYE[0].endswith(".wav")
    assert len(RECOVERY_LINES) >= 4
