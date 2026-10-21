"""Control plane: token minting, health, metrics.

Deliberately not on the media path. This is the latency-irrelevant half of the
deployment topology and can live anywhere; the workers cannot.
"""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.api.identity import identity_for
from agent.config import Settings, get_settings
from agent.telemetry.logging import configure_logging, get_logger

__all__ = ["ClientTimingRequest", "TokenRequest", "TokenResponse", "create_app"]

log = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Short enough that a token copied off a shared screen expires quickly, long
# enough for an unhurried demo.
TOKEN_TTL = timedelta(minutes=30)


class ClientTimingRequest(BaseModel):
    """The browser's own half of the call timeline.

    The worker's clock only starts once LiveKit dispatches the job to it,
    which is already several steps after the caller pressed Connect — token
    fetch, room negotiation, LiveKit finding a worker. Nothing server-side can
    see that gap; only the browser was there for it.
    """

    room: str
    marks: list[dict[str, Any]]


class TokenRequest(BaseModel):
    """What the demo page sends.

    ``caller_phone`` stands in for the number a SIP INVITE would assert. It
    decides whose orders the agent can see, so it is minted into the token
    here, server-side, and never taken from anything the browser can change
    afterwards.
    """

    caller_phone: str | None = None
    # Chosen before the call, not during it. OpenAI refuses a voice change once
    # the session has produced any audio ("cannot_update_voice"), and the agent
    # greets within a second of connecting — so by the time the picker is
    # reachable the voice is already fixed for the rest of the call.
    voice: str | None = None


class TokenResponse(BaseModel):
    token: str
    url: str
    room: str
    identity: str


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Urdu Voice Agent", docs_url=None, redoc_url=None)
    started = time.monotonic()

    def _settings() -> Settings:
        return settings or get_settings()

    # The worker calls this too (main.bootstrap). Both processes append to the
    # same file, which is the point: a call's setup timeline (worker) and its
    # browser timeline (this process) land in one place instead of two
    # terminal windows that scroll independently.
    _s = _settings()
    configure_logging(_s.log_level, _s.log_file)

    @app.post("/api/token", response_model=TokenResponse)
    async def mint_token(request: TokenRequest | None = None) -> TokenResponse:
        """Short-lived LiveKit join token.

        The browser must never see the API secret; that is the whole reason
        this endpoint exists rather than embedding credentials in the page.

        The caller's number is baked into the signed identity rather than sent
        separately, so the worker can trust it: changing it means forging a
        token. In production it comes from the SIP INVITE and this parameter
        goes away.
        """
        s = _settings()
        secret = s.livekit_api_secret.get_secret_value()
        key = s.livekit_api_key.get_secret_value()
        if not (key and secret):
            raise HTTPException(503, "LiveKit credentials are not configured")

        from agent.providers.voices import selectable_for

        room = f"demo-{uuid4().hex[:8]}"
        identity = identity_for(request.caller_phone if request else None)

        # Validated here rather than trusted from the browser: it is signed
        # into the token and handed to a vendor SDK on the other side.
        allowed = {v.id for v in selectable_for(s.voice_mode, s.tts_provider)}
        wanted = request.voice if request else None
        voice = wanted if wanted in allowed else None
        if wanted and voice is None:
            log.warning("token_voice_rejected", voice=wanted[:40], mode=s.voice_mode)

        return TokenResponse(
            token=_jwt(key, secret, room, identity, voice),
            url=s.livekit_url,
            room=room,
            identity=identity,
        )

    @app.get("/api/config")
    async def config() -> dict[str, object]:
        """What the page needs to render itself honestly.

        Chiefly the voice list. Offering all three vendors at once and letting
        the operator work out which group is live means the picker silently
        does nothing most of the time — a foreign voice id is either rejected
        by the vendor or ignored, and neither is visible from the browser.
        """
        from agent.providers.voices import selectable_for

        s = _settings()
        voices = selectable_for(s.voice_mode, s.tts_provider)
        return {
            "voice_mode": s.voice_mode,
            "engine": s.realtime_model if s.voice_mode == "realtime" else s.tts_provider,
            "default_voice": s.realtime_voice if s.voice_mode == "realtime" else s.default_voice,
            "voices": [{"id": v.id, "label": v.label, "vendor": v.vendor} for v in voices],
        }

    @app.post("/api/call-timing")
    async def call_timing(request: ClientTimingRequest) -> dict[str, str]:
        """Where the browser's half of the call timeline lands.

        Best-effort by design — sent once, from the disconnect handler, after
        the thing being measured is already over. There is nothing to retry
        and nothing a dropped request should do to the call.
        """
        log.info("client_timeline", room=request.room, marks=request.marks)
        return {"status": "logged"}

    @app.get("/health")
    async def health() -> dict[str, object]:
        """Liveness only.

        Touches no dependency, so a Postgres blip cannot make the process look
        dead and get it restarted mid-call.
        """
        return {"status": "ok", "uptime_s": round(time.monotonic() - started, 1)}

    @app.get("/readyz")
    async def readyz() -> dict[str, object]:
        """Dependency check. This is the one to look at before a demo."""
        s = _settings()
        checks: dict[str, str] = {
            "stt": _configured(s.deepgram_api_key.get_secret_value(), s.stt_provider, "deepgram"),
            "tts": _configured(s.azure_speech_key.get_secret_value(), s.tts_provider, "azure"),
            "llm": _llm_check(s),
            "livekit": "ok" if s.livekit_api_key.get_secret_value() else "missing credentials",
        }
        ready = all(v == "ok" or v == "not selected" for v in checks.values())
        return {"ready": ready, "checks": checks}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        from prometheus_client import REGISTRY, generate_latest

        return generate_latest(REGISTRY).decode()

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


def _configured(secret: str, selected: str, needs: str) -> str:
    if selected != needs:
        return "not selected"
    return "ok" if secret else "missing credentials"


def _llm_check(s: Settings) -> str:
    if s.llm_provider == "claude":
        return "ok" if s.anthropic_api_key.get_secret_value() else "missing credentials"
    if s.llm_provider == "openrouter":
        return "ok" if s.openrouter_api_key.get_secret_value() else "missing credentials"
    return "not selected"


def _jwt(
    api_key: str, api_secret: str, room: str, identity: str, voice: str | None = None
) -> str:
    """Mint a LiveKit access token.

    Hand-rolled rather than pulled from the LiveKit SDK: it is a plain HS256 JWT
    with a documented claim shape, and this keeps the control plane free of a
    dependency it otherwise would not need.
    """
    import base64
    import hashlib
    import hmac
    import json

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": api_key,
        "sub": identity,
        "nbf": now - 5,  # small skew allowance
        "exp": now + int(TOKEN_TTL.total_seconds()),
        "video": {"room": room, "roomJoin": True, "canPublish": True, "canSubscribe": True},
    }
    if voice:
        # Rides on the participant, so the worker can read it off the identity
        # it already trusts — the same route the caller's number takes.
        payload["metadata"] = json.dumps({"voice": voice})

    def segment(data: dict[str, Any]) -> bytes:
        raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    signing_input = segment(header) + b"." + segment(payload)
    signature = hmac.new(api_secret.encode(), signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()
