"""Structured JSON logging with credential redaction.

Two requirements drive the design: machine-parseable output for aggregation, and
a redaction processor so an accidental ``logger.info("cfg", **settings.model_dump())``
cannot leak credentials into a log pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

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


def configure_logging(level: str = "INFO") -> None:
    """Install the processor chain. Idempotent — safe to call more than once."""
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
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
