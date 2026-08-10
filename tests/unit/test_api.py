"""TASK-10: control plane."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agent.api.server import create_app
from agent.config import Settings

SECRET = "livekit-super-secret"


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "livekit_api_key": SecretStr("APIkey123"),
        "livekit_api_secret": SecretStr(SECRET),
        "livekit_url": "wss://demo.livekit.cloud",
        "stt_provider": "deepgram",
        "tts_provider": "azure",
        "llm_provider": "openrouter",
        "deepgram_api_key": SecretStr("dg"),
        "azure_speech_key": SecretStr("az"),
        "openrouter_api_key": SecretStr("or"),
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(_settings()))


def _decode(segment: str) -> dict[str, object]:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


# ── token ────────────────────────────────────────────────────────────────────


def test_token_is_a_jwt_with_the_right_grants(client: TestClient) -> None:
    body = client.post("/api/token").json()
    header, payload, _sig = body["token"].split(".")
    claims = _decode(payload)

    assert _decode(header)["alg"] == "HS256"
    assert claims["video"]["roomJoin"] is True  # type: ignore[index]
    assert claims["video"]["room"] == body["room"]  # type: ignore[index]


def test_token_signature_verifies(client: TestClient) -> None:
    import hashlib
    import hmac

    token = client.post("/api/token").json()["token"]
    signing_input, signature = token.rsplit(".", 1)
    expected = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    ).rstrip(b"=")
    assert signature == expected.decode()


def test_token_expires(client: TestClient) -> None:
    claims = _decode(client.post("/api/token").json()["token"].split(".")[1])
    lifetime = int(claims["exp"]) - int(claims["nbf"])  # type: ignore[call-overload]
    assert 0 < lifetime <= 31 * 60


def test_response_never_leaks_the_secret(client: TestClient) -> None:
    """A secret copied off a demo screen is a real incident."""
    raw = json.dumps(client.post("/api/token").json())
    assert SECRET not in raw


def test_each_request_gets_its_own_room(client: TestClient) -> None:
    """Two demo sessions must not end up in the same room."""
    rooms = {client.post("/api/token").json()["room"] for _ in range(5)}
    assert len(rooms) == 5


def test_missing_credentials_returns_503() -> None:
    app = create_app(_settings(livekit_api_key=SecretStr(""), livekit_api_secret=SecretStr("")))
    assert TestClient(app).post("/api/token").status_code == 503


# ── health and readiness ──────────────────────────────────────────────────────


def test_health_touches_no_dependency(client: TestClient) -> None:
    """A Postgres blip must not make the process look dead and get it
    restarted mid-call."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "uptime_s" in body


def test_readyz_reports_each_dependency(client: TestClient) -> None:
    checks = client.get("/readyz").json()["checks"]
    assert set(checks) == {"stt", "tts", "llm", "livekit"}
    assert all(v == "ok" for v in checks.values())


def test_readyz_flags_a_missing_credential() -> None:
    app = create_app(_settings(azure_speech_key=SecretStr("")))
    body = TestClient(app).get("/readyz").json()
    assert body["ready"] is False
    assert body["checks"]["tts"] == "missing credentials"


def test_readyz_ignores_unselected_providers() -> None:
    """An Inworld key is irrelevant when TTS_PROVIDER=azure."""
    body = TestClient(create_app(_settings(llm_provider="fake"))).get("/readyz").json()
    assert body["checks"]["llm"] == "not selected"


def test_metrics_endpoint_serves_prometheus(client: TestClient) -> None:
    assert client.get("/metrics").status_code == 200


# ── the demo page ──────────────────────────────────────────────────────────────


def test_index_is_served(client: TestClient) -> None:
    assert "Voice Agent" in client.get("/").text


def test_page_needs_no_build_step() -> None:
    """A demo requiring npm install before a client meeting is a demo that
    fails before a client meeting."""
    static = Path("src/agent/api/static")
    assert (static / "index.html").exists()
    assert (static / "app.js").exists()
    assert not (static / "package.json").exists()


def test_livekit_client_is_vendored_not_cdn() -> None:
    """Conference wifi blocks CDNs."""
    html = Path("src/agent/api/static/index.html").read_text(encoding="utf-8")
    assert "/static/vendor/livekit-client.umd.js" in html
    assert "cdn." not in html and "unpkg" not in html

    vendored = Path("src/agent/api/static/vendor/livekit-client.umd.js")
    assert vendored.exists(), "run: .\\make.cmd vendor"
    assert vendored.stat().st_size > 200_000, "vendored client looks truncated"


def test_echo_cancellation_is_enabled() -> None:
    """Without it the agent hears itself on laptop speakers and interrupts its
    own sentence - the most embarrassing possible demo failure."""
    js = Path("src/agent/api/static/app.js").read_text(encoding="utf-8")
    assert "echoCancellation: true" in js


def test_urdu_renders_right_to_left() -> None:
    html = Path("src/agent/api/static/index.html").read_text(encoding="utf-8")
    assert "direction:rtl" in html.replace(" ", "")
    assert "Noto Nastaliq Urdu" in html


def test_ui_failures_cannot_drop_the_call() -> None:
    """Transcript rendering is best-effort; a malformed frame must be swallowed."""
    js = Path("src/agent/api/static/app.js").read_text(encoding="utf-8")
    assert "try {" in js and "JSON.parse" in js


# ── voice list ───────────────────────────────────────────────────────────────
#
# The page used to hard-code all three vendors' voices and hope the operator
# knew which group was live. Selecting a foreign id does nothing at all, with
# no error in the browser or on the wire, so the picker looked broken. The
# server is the only side that knows the mode, so it says.


def test_config_offers_only_the_running_engine_voices() -> None:
    realtime = TestClient(create_app(_settings(voice_mode="realtime")))
    body = realtime.get("/api/config").json()
    assert body["voice_mode"] == "realtime"
    assert {v["id"] for v in body["voices"]} == {"cedar", "marin", "alloy", "shimmer"}


def test_config_follows_the_tts_provider_in_pipeline_mode() -> None:
    uplift = TestClient(
        create_app(_settings(voice_mode="pipeline", tts_provider="uplift"))
    )
    ids = {v["id"] for v in uplift.get("/api/config").json()["voices"]}
    assert ids == {"broadband-support", "helpdesk-agent", "female-narrator"}
    assert "cedar" not in ids


def test_config_default_voice_is_one_of_the_offered_ones() -> None:
    """Otherwise the picker opens on a value the engine cannot speak."""
    for kw in (
        {"voice_mode": "realtime"},
        {"voice_mode": "pipeline", "tts_provider": "uplift"},
        {"voice_mode": "pipeline", "tts_provider": "azure"},
    ):
        body = TestClient(create_app(_settings(**kw))).get("/api/config").json()
        assert body["default_voice"] in {v["id"] for v in body["voices"]}, kw


def test_config_leaks_no_credentials(client: TestClient) -> None:
    raw = json.dumps(client.get("/api/config").json())
    assert SECRET not in raw
    assert "api_key" not in raw
