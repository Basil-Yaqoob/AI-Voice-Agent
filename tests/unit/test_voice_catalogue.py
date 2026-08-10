"""Which voices the picker is allowed to offer.

The bug behind this file: the demo page listed all three vendors' voices at
once and left the operator to know which group was live. Voice ids are not
portable — "cedar" means nothing to Uplift, "broadband-support" means nothing
to OpenAI — and a vendor handed a foreign id either rejects it or ignores it.
Neither is visible from the browser, so the picker looked broken rather than
the selection looking wrong. The server knows the mode; the server decides.
"""

from __future__ import annotations

import pytest

from agent.providers.voices import ALL_VOICE_IDS, catalogue, selectable_for


def test_realtime_offers_only_openai_voices() -> None:
    ids = {v.id for v in selectable_for("realtime", tts_provider="uplift")}
    assert ids == {"cedar", "marin", "alloy", "shimmer"}
    assert "broadband-support" not in ids, (
        "realtime never builds a TTS provider, so an Uplift id is dead on arrival"
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("uplift", {"broadband-support", "helpdesk-agent", "female-narrator"}),
        ("azure", {"ur-PK-UzmaNeural", "ur-PK-AsadNeural"}),
    ],
)
def test_the_pipeline_offers_its_own_provider(provider: str, expected: set[str]) -> None:
    assert {v.id for v in selectable_for("pipeline", provider)} == expected


def test_an_unknown_provider_offers_nothing_rather_than_guessing() -> None:
    """Better a disabled picker than one that silently does nothing."""
    assert selectable_for("pipeline", "some-new-vendor") == ()


def test_no_voice_id_belongs_to_two_vendors() -> None:
    """An overlapping id would make the allow-list ambiguous."""
    ids = [v.id for group in catalogue.values() for v in group]
    assert len(ids) == len(set(ids))


def test_the_allow_list_covers_every_catalogued_voice() -> None:
    """The worker checks against this before handing a value to a vendor SDK."""
    for group in catalogue.values():
        for voice in group:
            assert voice.id in ALL_VOICE_IDS


def test_the_agent_allow_list_is_the_catalogue() -> None:
    """One source of truth: a voice added to the page must reach the worker."""
    from agent.main import SELECTABLE_VOICES

    assert SELECTABLE_VOICES == ALL_VOICE_IDS
