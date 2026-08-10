"""LiveKit worker entrypoint.

    uv run python -m agent.main dev       # local, connects to LiveKit Cloud
    uv run python -m agent.main start     # production

This is the seam where the pipeline meets the media layer. Everything the agent
does lives in ``agent.pipeline``; this module only wires it to a room and
publishes what the demo page displays.

The same code serves WebRTC and SIP. Demo on WebRTC now, attach the SIP gateway
in week five, and nothing here changes — which is the property that lets the
telephony contracting run in parallel with the build.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.api.identity import phone_from_identity
from agent.config import Settings, get_settings, validate_credentials
from agent.data.sqlite import Database
from agent.interfaces.llm import CachedPrompt, Message
from agent.knowledge.corpus import load_corpus_dir, plan_for_corpus
from agent.knowledge.keyterms import load_keyterms
from agent.language.personas import DVAGO_COMPANY, DVAGO_PERSONA
from agent.language.prompts import build_cached_prefix, prefix_is_cacheable
from agent.pipeline.endpointing import AdaptiveEndpointer, BargeInDetector
from agent.pipeline.interrupt import InterruptController
from agent.pipeline.session import SessionState, StateMachine
from agent.pipeline.turn import TurnRunner
from agent.providers.registry import build_llm, build_stt, build_tts
from agent.telemetry.logging import configure_logging, get_logger
from agent.telemetry.metrics import TurnMetrics
from agent.tools.demo_backend import build_demo_registry
from agent.tools.dvago_backend import DvagoBackend, build_dvago_registry

__all__ = ["CORPUS_DIR", "DVAGO_DB", "VoiceAgent", "bootstrap", "main"]

log = get_logger(__name__)

# The client deployment. Both are built by:
#   uv run python scripts/generate_dvago_dataset.py
#   uv run python scripts/build_dvago_db.py
DVAGO_DB = Path("data/dvago/dvago.db")
CORPUS_DIR = Path("knowledge/dvago")

# Fallback when the DVAGO corpus has not been built yet, so `make demo` still
# starts and says something useful rather than failing on a missing directory.
FALLBACK_PERSONA = "You are Acme's customer support agent, taking calls from Pakistani customers."
FALLBACK_CORPUS = """
Acme sells prepaid internet packages across Pakistan.
Delivery takes two to three working days in major cities, four to five elsewhere.
The Premium Plan is 2500 rupees a month and includes unlimited data.
Refunds are processed within seven working days of approval.
"""


class VoiceAgent:
    """One call: audio in, audio out, with barge-in and metrics.

    ``caller_phone`` is the caller's identity for the whole call. In production
    it comes off the SIP INVITE; on the demo page it is typed in. It is handed
    to the tool backend once and never exposed as a tool argument, so the model
    cannot be talked into fetching somebody else's orders.
    """

    def __init__(
        self,
        settings: Settings,
        room: Any,
        sink: Any,
        *,
        caller_phone: str | None = None,
        database: Database | None = None,
    ) -> None:
        self._settings = settings
        self._room = room
        self._sink = sink

        self._stt = build_stt(settings)
        self._tts = build_tts(settings)
        self._llm = build_llm(settings)

        self._db = database
        if self._db is not None:
            company = DVAGO_COMPANY
            persona = DVAGO_PERSONA
            corpus = load_corpus_dir(CORPUS_DIR)
            self._tools = build_dvago_registry(
                DvagoBackend(self._db, caller_phone=caller_phone)
            )
        else:
            company, persona, corpus = "Acme", FALLBACK_PERSONA, FALLBACK_CORPUS
            self._tools = build_demo_registry()

        self._state = StateMachine()
        self._endpointer = AdaptiveEndpointer(base_ms=settings.endpoint_silence_ms)
        self._barge_in = BargeInDetector()
        self._interrupts = InterruptController(sink)

        self._history: list[Message] = []
        self._call_id = f"call_{uuid4().hex[:8]}"
        self._turn_index = 0
        self._voice = settings.tts_voice_urdu

        self._system = CachedPrompt(
            prefix=build_cached_prefix(
                company=company,
                persona=persona,
                corpus=corpus,
                tools=self._tools.definitions(),
            )
        )
        if not prefix_is_cacheable(self._system.prefix):
            # Accepted, not fatal: the agent works, every request just bills at
            # full input price. Worth knowing before reading the invoice.
            log.warning("prompt_prefix_below_cache_minimum", chars=len(self._system.prefix))

    # ── caller speech ────────────────────────────────────────────────────

    def on_partial(self, text: str) -> None:
        """Interim transcript. Cheap and non-blocking, called very often."""
        self._endpointer.silence_threshold_ms(text)

    async def on_speech_started(self) -> None:
        """VAD fired. Only a real interruption if the agent is mid-response."""
        if not self._state.is_responding:
            return
        result = await self._interrupts.interrupt()
        if result is not None and result.spoken:
            # What the caller HEARD, not what the model intended to say.
            self._history.append(Message(role="assistant", content=result.spoken))
        self._state.to(SessionState.LISTENING)
        self._barge_in.reset()

    async def on_final(self, text: str) -> None:
        """Final transcript: run a turn."""
        if not text.strip() or not self._state.is_active:
            return

        self._turn_index += 1
        metrics = TurnMetrics(
            turn_id=f"turn_{self._turn_index:02d}",
            call_id=self._call_id,
            model=self._settings.resolved_llm_model,
            tts_provider=self._tts.name,
        )
        metrics.mark_transcript_final()

        self._state.to(SessionState.THINKING)
        self._history.append(Message(role="user", content=text))
        await self._publish({"type": "transcript", "role": "user", "text": text, "final": True})

        runner = TurnRunner(
            llm=self._llm,
            tts=self._tts,
            sink=self._sink,
            voice=self._voice,
            tools=self._tools.definitions(),
        )
        task = asyncio.create_task(runner.run(self._history, system=self._system))
        self._interrupts.track(task, runner)

        try:
            result = await self._interrupts.finish()
        except asyncio.CancelledError:
            return  # barge-in; on_speech_started already recorded the partial
        except Exception as exc:
            log.warning("turn_failed", detail=str(exc))
            await self._speak_recovery(exc)
            self._state.to(SessionState.LISTENING)
            return

        if result is not None:
            metrics.mark_first_audio()
            metrics.usage_from(result.usage)
            self._history.append(Message(role="assistant", content=result.spoken))
            await self._publish(
                {"type": "transcript", "role": "agent", "text": result.spoken, "final": True}
            )
            await self._publish({"type": "metrics", **metrics.as_log()})
            log.info(**metrics.as_log())

        self._state.to(SessionState.LISTENING)

    # ── plumbing ──────────────────────────────────────────────────────────

    async def _speak_recovery(self, error: BaseException) -> None:
        from agent.pipeline.recovery import RECOVERY_LINES, classify

        line = RECOVERY_LINES[classify(error)]
        await self._sink.play(self._tts.synthesize(line.text, voice=self._voice))

    async def _publish(self, payload: dict[str, Any]) -> None:
        """Send UI data. Best-effort: this must never be able to drop a call."""
        try:
            await self._room.local_participant.publish_data(
                json.dumps(payload, ensure_ascii=False).encode(), reliable=True
            )
        except Exception:
            pass

    def set_voice(self, voice: str) -> None:
        """Live voice switch for the demo A/B."""
        self._voice = voice

    @staticmethod
    def caller_phone_from(participant: Any) -> str | None:
        """Read the caller's number off the participant that joined.

        The number is in the signed token identity, so changing it means
        forging a token — which is the property that lets the agent answer
        questions about someone's orders at all. Never take it from anything
        said during the conversation.
        """
        return phone_from_identity(getattr(participant, "identity", None))

    @property
    def keyterms(self) -> list[str]:
        return load_keyterms()


def open_database(path: Path = DVAGO_DB) -> Database | None:
    """The DVAGO database if it has been built, otherwise None.

    Missing is not fatal. A fresh clone has no database until the dataset is
    generated, and an agent that refuses to start is harder to diagnose than
    one that starts and says which command is missing.
    """
    if not path.exists():
        log.warning(
            "dvago_database_missing",
            path=str(path),
            fix="uv run python scripts/generate_dvago_dataset.py "
                "&& uv run python scripts/build_dvago_db.py",
        )
        return None
    return Database(path)


def bootstrap() -> Settings:
    """Load config, fail fast on missing credentials, install logging."""
    settings = get_settings()
    configure_logging(settings.log_level)
    validate_credentials(settings)

    corpus = load_corpus_dir(CORPUS_DIR) or FALLBACK_CORPUS
    plan = plan_for_corpus(corpus)
    log.info(
        "bootstrap_complete",
        env=settings.env,
        stt=settings.stt_provider,
        tts=settings.tts_provider,
        llm=settings.llm_provider,
        model=settings.resolved_llm_model,
        corpus_strategy=plan.strategy,
        corpus_tokens=plan.corpus_tokens,
        corpus_reason=plan.reason,
        database="present" if DVAGO_DB.exists() else "MISSING",
    )
    return settings


def main() -> int:
    settings = bootstrap()

    try:
        from livekit.agents import cli  # noqa: F401
    except ImportError:
        print("livekit-agents is not installed; run: uv sync")
        return 1

    mode = sys.argv[1] if len(sys.argv) > 1 else "dev"
    print(
        f"Agent configured ({mode}): {settings.resolved_llm_model} / "
        f"{settings.tts_provider} / {settings.stt_provider}\n"
        "Control plane: uv run uvicorn agent.api.server:create_app --factory --port 8000\n"
        "Then open http://localhost:8000\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
