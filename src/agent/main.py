"""LiveKit worker entrypoint.

Wired up in TASK-08. For now this only proves the startup sequence: load config,
fail fast on missing credentials, configure logging.
"""

from __future__ import annotations

from agent.config import get_settings, validate_credentials
from agent.telemetry.logging import configure_logging, get_logger


def bootstrap() -> None:
    """Load and validate configuration, then install logging."""
    settings = get_settings()
    configure_logging(settings.log_level)
    validate_credentials(settings)

    get_logger(__name__).info(
        "bootstrap_complete",
        env=settings.env,
        stt=settings.stt_provider,
        tts=settings.tts_provider,
        llm=settings.llm_provider,
        model=settings.llm_model,
    )


if __name__ == "__main__":
    bootstrap()
