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
from agent.interfaces.stt import STTProvider
from agent.knowledge.corpus import load_corpus_dir, plan_for_corpus
from agent.knowledge.keyterms import load_keyterms
from agent.language.personas import DVAGO_COMPANY, DVAGO_PERSONA
from agent.language.prompts import build_cached_prefix, prefix_is_cacheable
from agent.pipeline.endpointing import AdaptiveEndpointer, BargeInDetector
from agent.pipeline.interrupt import InterruptController
from agent.pipeline.session import SessionState, StateMachine
from agent.pipeline.turn import TurnRunner
from agent.providers.registry import build_llm, build_stt, build_tts
from agent.providers.voices import ALL_VOICE_IDS
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
# The model can ask for tools, read the answers, and ask again — but only so
# many times. Two rounds covers every DVAGO path (look the caller up, then look
# the medicine up); an unbounded loop would let one turn run until the caller
# hangs up.
MAX_TOOL_ROUNDS = 3

# Spoken the moment the call connects, before the caller says anything. Fixed
# text rather than a model call: it has to be immediate, and there is nothing
# for a model to decide about hello. Written in Urdu script including the
# company name, because the voice reads Latin letters one at a time.
GREETING_SETTLE_S = 1.0

GREETING_URDU = (
    "السلام علیکم، دواگو سے رابطہ کرنے کا شکریہ۔ میں آپ کی کیا مدد کر سکتا ہوں؟"
)

# What the demo page's voice picker is allowed to select. An allow-list rather
# than a free string: the value arrives over the room data channel and is
# handed straight to the TTS vendor, where a bad id fails mid-sentence. The
# catalogue lives in providers.voices because the control plane needs it too.
SELECTABLE_VOICES = ALL_VOICE_IDS

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
        self._voice = settings.default_voice

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

    # ── call lifecycle ───────────────────────────────────────────────────

    def begin(self) -> None:
        """Open the call. Must be called before any transcript arrives.

        The state machine starts IDLE and only IDLE -> GREETING -> LISTENING or
        IDLE -> LISTENING are legal, so a transcript that arrives while still
        IDLE raises IllegalTransition out of ``on_final`` and kills the call.
        That is the machine doing its job — a turn beginning from a state the
        agent was never put into is exactly the "inexplicable audio" it exists
        to prevent — but the transport has to open the call for it.
        """
        if self._state.state is SessionState.IDLE:
            self._state.to(SessionState.LISTENING)

    async def greet(self) -> None:
        """Say hello before the caller has to.

        A line that answers with silence gets "hello? hello?" and then a hang
        up — the caller cannot tell a connected call from a dead one. The
        realtime path gets this from the API; the pipeline has to do it here.

        Failure is not fatal: a caller who hears no greeting can still be
        helped, so a broken TTS at this moment must not end the call.
        """
        # See the note in transport/realtime.py: the far end has to finish
        # subscribing or the opening syllables are played to nobody.
        await asyncio.sleep(GREETING_SETTLE_S)
        try:
            await self._sink.play(
                self._tts.synthesize(GREETING_URDU, voice=self._voice)
            )
        except Exception as exc:
            log.warning("greeting_failed", detail=str(exc)[:200])
            return
        self._history.append(Message(role="assistant", content=GREETING_URDU))
        await self._publish(
            {"type": "transcript", "role": "agent", "text": GREETING_URDU, "final": True}
        )

    def end(self) -> None:
        """Close the call. Legal from any state."""
        self._state.to(SessionState.TERMINATED)

    @property
    def state(self) -> SessionState:
        return self._state.state

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

        # A turn is not one model call. Asked "Panadol hai?", the model answers
        # with tool_calls and no text at all — correctly, because it cannot know
        # the stock until something looks it up. Speaking that turn plays
        # silence. So: run, execute whatever it asked for, feed the results
        # back, run again, and speak the pass that finally produces words.
        result = None
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                runner = TurnRunner(
                    llm=self._llm,
                    tts=self._tts,
                    sink=self._sink,
                    voice=self._voice,
                    tools=self._tools.definitions(),
                )
                task = asyncio.create_task(runner.run(self._history, system=self._system))
                self._interrupts.track(task, runner)
                result = await self._interrupts.finish()

                if result is None or not result.tool_calls:
                    break

                metrics.tool_calls += len(result.tool_calls)
                await self._run_tools(result)
        except asyncio.CancelledError:
            return  # barge-in; on_speech_started already recorded the partial
        except Exception as exc:
            log.warning("turn_failed", detail=str(exc))
            await self._speak_recovery(exc)
            return
        finally:
            # Every exit from a turn lands back on LISTENING. Without this the
            # state stays THINKING, the next thing the caller says attempts
            # THINKING -> THINKING, which is illegal, and the exception kills
            # the call. The symptom is an agent that answers once or twice and
            # then stops hearing you.
            self._return_to_listening()

        if result is not None:
            metrics.mark_first_audio()
            metrics.usage_from(result.usage)
            self._history.append(Message(role="assistant", content=result.spoken))
            await self._publish(
                {"type": "transcript", "role": "agent", "text": result.spoken, "final": True}
            )
            await self._publish({"type": "metrics", **metrics.as_log()})
            log.info(**metrics.as_log())

    def _return_to_listening(self) -> None:
        """Ready for the next turn, from wherever this one ended.

        Guarded rather than a bare ``to(LISTENING)``: after a barge-in
        ``on_speech_started`` has already moved the state, and LISTENING ->
        LISTENING is illegal too. A terminated call stays terminated.
        """
        if self._state.state in (SessionState.LISTENING, SessionState.TERMINATED):
            return
        self._state.to(SessionState.LISTENING)

    async def _run_tools(self, result: Any) -> None:
        """Execute the tools the model asked for and put the answers in history.

        Concurrently, because a stock lookup and a profile lookup have no reason
        to queue behind each other inside the turn budget.

        The results go back as a user message rather than as native tool_result
        blocks: ``Message`` carries a role and a string, and widening it to
        model-specific block types would push vendor shapes into the pipeline
        that the provider interface exists to keep out. The model reads them
        perfectly well this way.
        """
        outputs = await asyncio.gather(
            *(self._tools.call(call.name, call.arguments) for call in result.tool_calls)
        )
        for call, output in zip(result.tool_calls, outputs, strict=True):
            log.info("tool_called", tool=call.name, error=output.is_error)

        if result.spoken:
            # A filler line the caller already heard ("one moment") belongs in
            # history before the results it was covering for.
            self._history.append(Message(role="assistant", content=result.spoken))

        self._history.append(
            Message(
                role="user",
                content="[tool results]\n"
                + "\n".join(output.content for output in outputs),
            )
        )

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
        """Live voice switch for the demo A/B.

        Validated because this arrives over the room's data channel. An
        unrecognised id is not rejected loudly — it would fail inside the TTS
        adapter mid-sentence, which on a live call means silence — it is simply
        ignored and logged.
        """
        if voice not in SELECTABLE_VOICES:
            log.warning("unknown_voice_ignored", voice=voice[:40])
            return
        self._voice = voice
        log.info("voice_changed", voice=voice)

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

    @property
    def instructions(self) -> str:
        """The system prompt: persona, policy corpus and how to behave."""
        return self._system.prefix

    @property
    def tools(self) -> Any:
        """The tool registry, already bound to this caller."""
        return self._tools

    async def publish(self, payload: dict[str, Any]) -> None:
        """Send a UI message to the room. Used by the realtime session."""
        await self._publish(payload)

    @property
    def stt(self) -> STTProvider:
        """The transport opens the recognition stream, so it needs the provider.

        Exposed rather than rebuilt in the worker: two instances would mean two
        Deepgram connections per call, one of them silently idle.
        """
        return self._stt


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
        from livekit.agents import WorkerOptions, cli
    except ImportError:
        print("livekit-agents is not installed; run: uv sync")
        return 1

    from agent.transport.worker import entrypoint

    print(
        f"Agent: {settings.resolved_llm_model} / {settings.tts_provider} / "
        f"{settings.stt_provider}\n"
        f"Joining rooms on {settings.livekit_url}\n"
        "Control plane: uv run uvicorn agent.api.server:create_app --factory --port 8000\n"
        "Then open http://localhost:8000\n"
    )

    # Passed explicitly rather than left to the process environment, which is
    # where the LiveKit worker looks by default. This project's configuration
    # comes from .env through pydantic-settings, and that never exports the
    # values — so a perfectly valid .env yields "ws_url is required".
    #
    # cli.run_app owns argv and the process lifecycle: signal handling, worker
    # registration, graceful drain on shutdown. It exits rather than returning,
    # which is why nothing follows it.
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            ws_url=settings.livekit_url,
            api_key=settings.livekit_api_key.get_secret_value(),
            api_secret=settings.livekit_api_secret.get_secret_value(),
            # Keep one job process warm. The default in dev is zero, so the
            # first caller pays a fresh interpreter start — imports, provider
            # construction, the database open — before the agent even joins.
            # That lands squarely on the first impression.
            num_idle_processes=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
