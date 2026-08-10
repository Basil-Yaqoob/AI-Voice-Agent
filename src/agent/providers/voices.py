"""Which voices the running configuration can actually use.

Voice ids are not portable and there is no error that says so. "cedar" means
nothing to Azure, "broadband-support" means nothing to OpenAI, and a vendor
handed a foreign id either rejects the call or, worse, ignores the change and
carries on in the old voice — so the picker looks broken rather than the id
looking wrong.

The demo page used to list all three vendors at once and leave the operator to
know which group was live. That is a trap: the first option in the list belongs
to whichever vendor happens to be first, and selecting it while another mode is
running does nothing at all. The server knows the mode, so the server decides
what may be offered.

Kept here rather than in ``agent.main`` because the control plane needs it too,
and ``agent.api`` is forbidden from importing anything that knows about rooms.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ALL_VOICE_IDS", "Voice", "catalogue", "selectable_for"]


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    vendor: str


# Grouped by the thing that has to match for the id to be meaningful: the
# realtime socket, or whichever TTS provider the pipeline was built with.
_REALTIME = (
    Voice("cedar", "Cedar (male)", "OpenAI Realtime"),
    Voice("marin", "Marin (female)", "OpenAI Realtime"),
    Voice("alloy", "Alloy (neutral)", "OpenAI Realtime"),
    Voice("shimmer", "Shimmer (female)", "OpenAI Realtime"),
)

# See docs.upliftai.org/orator_voices for the full 82.
_UPLIFT = (
    Voice("broadband-support", "Broadband Support", "Uplift"),
    Voice("helpdesk-agent", "Helpdesk Agent", "Uplift"),
    Voice("female-narrator", "Female Narrator", "Uplift"),
)

_AZURE = (
    Voice("ur-PK-UzmaNeural", "Uzma (female)", "Azure ur-PK"),
    Voice("ur-PK-AsadNeural", "Asad (male)", "Azure ur-PK"),
)

catalogue: dict[str, tuple[Voice, ...]] = {
    "realtime": _REALTIME,
    "uplift": _UPLIFT,
    "azure": _AZURE,
}

# The worker's allow-list. Deliberately every id rather than only the live
# ones: the value arrives over the data channel and the guard exists to keep
# junk out of a vendor SDK, not to second-guess the operator mid-call.
ALL_VOICE_IDS = frozenset(v.id for group in catalogue.values() for v in group)


def selectable_for(voice_mode: str, tts_provider: str) -> tuple[Voice, ...]:
    """The voices this configuration can switch to, in offer order.

    Realtime owns the voice outright — no TTS provider is even constructed —
    so the pipeline's groups are irrelevant there and offering them would only
    invite the dead selection this module exists to prevent.
    """
    if voice_mode == "realtime":
        return _REALTIME
    return catalogue.get(tts_provider, ())
