"""TASK-02 logging tests: redaction must hold for anything key-shaped."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from agent.telemetry.logging import REDACTED, configure_logging, get_logger


@pytest.fixture(autouse=True)
def _restore_safe_global_logging() -> Iterator[None]:
    """``configure_logging`` mutates process-global structlog state, and a
    module's ``log = get_logger(__name__)`` does not materialise its actual
    logger until its first ``.info()`` call — which can happen in a test file
    that runs long after this one. Leave a file- or capsys-backed config
    behind here and an unrelated test elsewhere crashes with "I/O operation on
    closed file" the first time it logs anything, for a reason nowhere near
    where the fault actually is. Every test in this file gets real stdout back
    on the way out, regardless of what it configured.
    """
    yield
    configure_logging("INFO")


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


# ── logging to a file, as well as the console ────────────────────────────────
#
# The worker and the control plane run in separate terminals, and a terminal's
# scrollback is not where "what happened on the call three demos ago" gets
# answered. These exercise the real file handle, not the captured-output
# fixture above — the whole point is bytes landing on disk.


def test_a_configured_file_receives_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    configure_logging("INFO", log_file=path)
    get_logger("t").info("call_timeline", call_id="call_1", room="demo-1")

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "call_timeline"
    assert entry["call_id"] == "call_1"


def test_missing_parent_directories_are_created(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "does" / "not" / "exist" / "calls.jsonl"
    configure_logging("INFO", log_file=path)
    get_logger("t").info("ping")

    assert path.exists()


def test_the_console_still_gets_output_when_a_file_is_also_configured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The file is additive. Live debugging in the terminal must not regress."""
    path = tmp_path / "calls.jsonl"
    configure_logging("INFO", log_file=path)
    get_logger("t").info("ping")

    out = capsys.readouterr().out
    assert '"ping"' in out


def test_repeated_calls_append_rather_than_truncate(tmp_path: Path) -> None:
    """A demo runs `make demo` more than once; the second run must not erase
    the first call's timeline."""
    path = tmp_path / "calls.jsonl"
    configure_logging("INFO", log_file=path)
    get_logger("t").info("first_call")

    configure_logging("INFO", log_file=path)
    get_logger("t").info("second_call")

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert events == ["first_call", "second_call"]


def test_an_empty_log_file_setting_disables_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Settings.log_file defaults to a real path; "" is how a test — or an
    operator — turns the file off without touching the console output."""
    configure_logging("INFO", log_file="")
    get_logger("t").info("console_only")

    assert capsys.readouterr().out  # still logs somewhere
    assert not (tmp_path / "calls.jsonl").exists()
