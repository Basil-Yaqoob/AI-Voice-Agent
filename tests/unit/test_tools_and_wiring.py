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
    """The keyword arm draws on the same list as STT, so both halves agree."""
    chunks = chunk_text("Panadol is stocked at every branch.", source="p.md")
    assert "panadol" in chunks[0].keywords


# ── worker wiring ──────────────────────────────────────────────────────────────


def test_main_module_is_runnable() -> None:
    """`make demo` must not fail on an import error."""
    import agent.main as main_module

    assert hasattr(main_module, "VoiceAgent")
    assert hasattr(main_module, "bootstrap")


def test_fallback_corpus_is_large_enough_to_cache() -> None:
    """A prefix below the cache minimum bills at full input price on every
    request, with no error to tell you."""
    from agent.knowledge.corpus import plan_for_corpus
    from agent.language.prompts import prefix_is_cacheable
    from agent.main import FALLBACK_CORPUS, FALLBACK_PERSONA

    prefix = build_cached_prefix(
        company="Acme",
        persona=FALLBACK_PERSONA,
        corpus=FALLBACK_CORPUS,
        tools=build_demo_registry().definitions(),
    )
    assert prefix_is_cacheable(prefix), f"fallback prefix is only {len(prefix)} chars"
    assert not plan_for_corpus(FALLBACK_CORPUS).needs_retrieval


def test_dvago_prefix_caches_and_needs_no_retrieval() -> None:
    """The deployment that actually ships.

    Two properties, both worth money. The prefix must clear the cache minimum,
    or every request bills at full input price. And the corpus must stay small
    enough to live in that prefix: retrieval would add a 150-250ms round trip
    the 800ms budget has nowhere to put, to answer questions the prefix already
    answers for a tenth of the token price.
    """
    from pathlib import Path

    from agent.data.sqlite import Database
    from agent.knowledge.corpus import load_corpus_dir, plan_for_corpus
    from agent.language.personas import DVAGO_COMPANY, DVAGO_PERSONA
    from agent.language.prompts import prefix_is_cacheable
    from agent.main import CORPUS_DIR
    from agent.tools.dvago_backend import DvagoBackend, build_dvago_registry

    corpus = load_corpus_dir(CORPUS_DIR)
    assert corpus, f"no corpus at {CORPUS_DIR}"

    plan = plan_for_corpus(corpus)
    assert not plan.needs_retrieval, plan.reason

    def prefix_for(caller: str | None) -> str:
        registry = build_dvago_registry(
            DvagoBackend(Database(Path("unused.db")), caller_phone=caller)
        )
        return build_cached_prefix(
            company=DVAGO_COMPANY,
            persona=DVAGO_PERSONA,
            corpus=corpus,
            tools=registry.definitions(),
        )

    prefix = prefix_for("03001234567")
    assert prefix_is_cacheable(prefix), f"DVAGO prefix is only {len(prefix)} chars"

    # The invariant that keeps the cache warm: identical bytes for every caller
    # and every call. Who is on the phone belongs in the turn suffix. A fixed
    # example order number inside the persona is fine — it never changes; a
    # real caller's number would triple the bill with no visible symptom.
    assert prefix == prefix_for("03219876543") == prefix_for(None)


def test_corpus_loader_excludes_the_maintainers_readme() -> None:
    """The README tells a human to replace the placeholders. The model must not read it."""
    from agent.knowledge.corpus import load_corpus_dir
    from agent.main import CORPUS_DIR

    corpus = load_corpus_dir(CORPUS_DIR)
    assert "Why there is no retrieval" not in corpus


def test_corpus_loading_is_deterministic() -> None:
    """Reordered bytes in the prefix mean a silent cache miss on every request."""
    from agent.knowledge.corpus import load_corpus_dir
    from agent.main import CORPUS_DIR

    assert load_corpus_dir(CORPUS_DIR) == load_corpus_dir(CORPUS_DIR)


def test_prerender_covers_every_recovery_line() -> None:
    """A missing asset means silence on that failure path."""
    from agent.pipeline.recovery import RECOVERY_LINES
    from agent.tools.prerender import GOODBYE, GREETING

    src = Path("src/agent/tools/prerender.py").read_text(encoding="utf-8")
    assert "RECOVERY_LINES.values()" in src
    assert GREETING[0].endswith(".wav") and GOODBYE[0].endswith(".wav")
    assert len(RECOVERY_LINES) >= 4
