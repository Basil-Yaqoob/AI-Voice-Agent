"""Who the agent thinks is calling.

This is the smallest module in the project and the one with the most riding on
it: it decides whose orders the agent is willing to read out. Everything the
tool backend does to protect a caller's data assumes the number arrived here
and nowhere else.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.api.identity import (
    ANONYMOUS_PREFIX,
    identity_for,
    is_pakistani_mobile,
    normalise_mobile,
    phone_from_identity,
)
from agent.main import VoiceAgent


@pytest.mark.parametrize(
    "spoken",
    [
        "03001234567",       # what a customer says
        "0300 1234567",
        "0300-123-4567",
        "+923001234567",     # what a SIP INVITE delivers
        "+92 300 1234567",
        "00923001234567",
        "923001234567",
        "3001234567",        # trunk prefix stripped by the carrier
    ],
)
def test_every_shape_a_number_arrives_in_normalises(spoken: str) -> None:
    """The customer table stores the local form. Everything must land on it."""
    assert normalise_mobile(spoken) == "03001234567"
    assert phone_from_identity(identity_for(spoken)) == "03001234567"


@pytest.mark.parametrize(
    "value",
    [None, "", "021111382 46", "0212345678", "030012345678",
     "not a phone", "04211138246", "02134567890"],
)
def test_anything_that_is_not_a_mobile_is_anonymous(value: str | None) -> None:
    assert not is_pakistani_mobile(value)
    identity = identity_for(value)
    assert identity.startswith(ANONYMOUS_PREFIX)
    assert phone_from_identity(identity) is None


def test_anonymous_identities_are_unique() -> None:
    """Two unknown callers must not collide into one conversation."""
    assert identity_for(None) != identity_for(None)


@pytest.mark.parametrize(
    "identity",
    [None, "", "caller-abc123", "caller-pk-", "caller-pk-notdigits",
     "caller-pk-0212345678", "pk-03001234567"],
)
def test_malformed_identities_yield_no_phone(identity: str | None) -> None:
    assert phone_from_identity(identity) is None


def test_the_worker_reads_the_number_off_the_participant() -> None:
    class Participant:
        identity = identity_for("03001234567")

    assert VoiceAgent.caller_phone_from(Participant()) == "03001234567"


def test_a_participant_without_an_identity_is_anonymous() -> None:
    assert VoiceAgent.caller_phone_from(object()) is None


# ── the control plane ────────────────────────────────────────────────────


def _client() -> TestClient:
    from agent.api.server import create_app

    return TestClient(create_app())


def test_token_carries_the_requested_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")

    response = _client().post("/api/token", json={"caller_phone": "03001234567"})
    assert response.status_code == 200
    assert phone_from_identity(response.json()["identity"]) == "03001234567"


def test_token_without_a_caller_is_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")

    for payload in ({}, {"caller_phone": None}, {"caller_phone": ""}):
        response = _client().post("/api/token", json=payload)
        assert response.status_code == 200
        assert phone_from_identity(response.json()["identity"]) is None


def test_a_junk_number_does_not_become_an_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A number the page did not offer must not be trusted just because it was sent."""
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")

    response = _client().post("/api/token", json={"caller_phone": "'; DROP TABLE--"})
    assert response.status_code == 200
    assert phone_from_identity(response.json()["identity"]) is None


def test_the_number_is_inside_the_signed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not a side channel: forging it means forging the JWT."""
    import base64
    import json

    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")

    body = _client().post("/api/token", json={"caller_phone": "03219876543"}).json()
    payload = body["token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["sub"] == body["identity"]
    assert "03219876543" in claims["sub"]
