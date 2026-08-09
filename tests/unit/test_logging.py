"""TASK-02 logging tests: redaction must hold for anything key-shaped."""

from __future__ import annotations

import json

import pytest
import structlog

from agent.telemetry.logging import REDACTED, configure_logging, get_logger


@pytest.fixture
def captured() -> list[dict[str, object]]:
    """Capture structlog output as parsed dicts, with the real processor chain."""
    configure_logging("DEBUG")
    entries: list[dict[str, object]] = []

    class _Capture:
        def msg(self, rendered: str) -> None:
            entries.append(json.loads(rendered))

        log = msg
        debug = msg
        info = msg
        warning = msg
        error = msg
        critical = msg

    structlog.configure(
        processors=structlog.get_config()["processors"],
        logger_factory=lambda *_a, **_k: _Capture(),
        wrapper_class=structlog.make_filtering_bound_logger(10),
        cache_logger_on_first_use=False,
    )
    return entries


def test_log_redaction(captured: list[dict[str, object]]) -> None:
    get_logger("t").info("cfg", api_key="sk-real-value")
    assert captured[0]["api_key"] == REDACTED


@pytest.mark.parametrize(
    "field",
    ["api_key", "anthropic_api_key", "SECRET", "access_token", "password", "authorization"],
)
def test_all_sensitive_shapes_redacted(captured: list[dict[str, object]], field: str) -> None:
    get_logger("t").info("cfg", **{field: "leaked"})
    assert captured[0][field] == REDACTED


def test_non_sensitive_fields_survive(captured: list[dict[str, object]]) -> None:
    """Over-redaction would make logs useless — only key-shaped names go."""
    get_logger("t").info("turn", model="claude-opus-5", first_audio_ms=612)
    assert captured[0]["model"] == "claude-opus-5"
    assert captured[0]["first_audio_ms"] == 612


def test_output_is_valid_json(captured: list[dict[str, object]]) -> None:
    get_logger("t").info("event", stage="llm")
    assert captured[0]["event"] == "event"
    assert captured[0]["level"] == "info"
    assert "timestamp" in captured[0]


def test_contextvars_merge_into_every_line(captured: list[dict[str, object]]) -> None:
    """call_id bound once must appear on all later lines — this is how you debug
    a single bad call out of a day's traffic."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(call_id="call_a3f9")
    try:
        get_logger("t").info("first")
        get_logger("t").info("second")
    finally:
        structlog.contextvars.clear_contextvars()
    assert [e["call_id"] for e in captured] == ["call_a3f9", "call_a3f9"]


def test_configure_logging_is_idempotent() -> None:
    configure_logging("INFO")
    configure_logging("INFO")
    get_logger("t").info("still works")
