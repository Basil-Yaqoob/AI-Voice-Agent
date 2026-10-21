"""Structured JSON logging with credential redaction.

Two requirements drive the design: machine-parseable output for aggregation, and
a redaction processor so an accidental ``logger.info("cfg", **settings.model_dump())``
cannot leak credentials into a log pipeline.

A third requirement was added once latency debugging needed it: the worker and
the control plane run in separate terminal windows, and a terminal's scrollback
is not a place to ask "what happened on the call three demos ago". Logging to a
file as well as the console means every call's timeline survives past the
window that showed it.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, TextIO, cast

import structlog

__all__ = ["REDACTED", "configure_logging", "get_logger"]

REDACTED = "***REDACTED***"

# Substring match on the key name. Deliberately broad — a false positive costs a
# redacted debug line, a false negative costs a leaked credential.
_SENSITIVE = ("key", "secret", "token", "password", "authorization", "credential")


def _redact(
    _logger: Any,
    _method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Replace the value of any key-shaped field.

    Only covers structured fields. An f-string that interpolates a secret into the
    message is not reachable from here — ``SecretStr`` in config.py covers that.
    """
    for key in list(event_dict):
        if any(marker in key.lower() for marker in _SENSITIVE):
            event_dict[key] = REDACTED
    return event_dict


class _Tee:
    """Write to more than one stream, so the terminal keeps its live view.

    The worker and the control plane are separate processes and both open the
    same file in append mode; two processes writing whole lines to one file is
    the ordinary safe case for this, not something that needs a lock — the
    call volume here is a handful of lines per second, never mid-line at once.
    """

    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> None:
        for stream in self._streams:
            stream.write(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def configure_logging(level: str = "INFO", log_file: str | Path | None = None) -> None:
    """Install the processor chain. Idempotent — safe to call more than once.

    ``log_file`` is additive: the console still gets everything it always did,
    so nothing about live debugging changes. Opened in append mode so a demo
    run this morning is still in the file this afternoon — this is meant to
    answer "what happened across every call today", not just the last one.
    """
    target: TextIO = sys.stdout
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # _Tee only needs write()/flush(), which is all PrintLoggerFactory
        # asks of "file" — but it is not a TextIO, so the type checker needs
        # telling rather than discovering that at runtime.
        target = cast(TextIO, _Tee(sys.stdout, path.open("a", encoding="utf-8")))

    structlog.configure(
        processors=[
            # Binds call_id once at session start; every later line carries it.
            structlog.contextvars.merge_contextvars,
            _redact,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=target),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
