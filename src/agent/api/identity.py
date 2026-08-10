"""How the worker learns who is calling.

The caller's number is the key to everything personal the agent can say, so
where it comes from matters more than it looks.

* **On PSTN and WhatsApp** it arrives in the SIP INVITE and LiveKit puts it on
  the participant. Nobody chooses it; the network asserts it.
* **On the demo page** there is no network to assert anything, so the page
  picks one and it travels in the LiveKit participant identity.

Either way the worker reads it off the participant, never from something the
model or the conversation supplied. A caller who says "I'm calling about
Ayesha's order" does not become Ayesha.

The identity string is the contract between the control plane and the worker,
which is why both import it from here rather than each formatting it their own
way and drifting.
"""

from __future__ import annotations

from uuid import uuid4

__all__ = [
    "ANONYMOUS_PREFIX",
    "identity_for",
    "is_pakistani_mobile",
    "normalise_mobile",
    "phone_from_identity",
]

ANONYMOUS_PREFIX = "caller-anon-"
_PHONE_PREFIX = "caller-pk-"

# 03XXXXXXXXX. Landlines cannot receive the SMS that DVAGO accounts are keyed
# on, so a number that is not a mobile is treated as no number at all.
_MOBILE_LENGTH = 11


def normalise_mobile(value: str | None) -> str | None:
    """A Pakistani mobile in the local ``03XXXXXXXXX`` form, or None.

    This is the boundary where a number becomes trustworthy, so it is also the
    only place that needs to know the shapes one arrives in. A SIP INVITE
    delivers E.164 — ``+923001234567`` — while the customer table stores the
    local form everybody in Pakistan actually says. Matching one against the
    other without converting looks up nobody and reports the caller as a new
    customer, which is a very quiet way to fail.

    Accepted: ``03001234567``, ``+923001234567``, ``00923001234567``,
    ``923001234567``, ``3001234567``, and any of those with spaces or dashes.
    """
    digits = "".join(c for c in value if c.isdigit()) if value else ""

    if digits.startswith("0092"):
        digits = digits[4:]
    elif digits.startswith("92") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("3"):
        # Caller ID with the trunk prefix stripped, which happens often enough.
        digits = "0" + digits

    if len(digits) == _MOBILE_LENGTH and digits.startswith("03"):
        return digits
    return None


def is_pakistani_mobile(value: str | None) -> bool:
    return normalise_mobile(value) is not None


def identity_for(phone: str | None) -> str:
    """A LiveKit participant identity carrying the caller's number, if known."""
    normalised = normalise_mobile(phone)
    if normalised is None:
        return f"{ANONYMOUS_PREFIX}{uuid4().hex[:6]}"
    return f"{_PHONE_PREFIX}{normalised}"


def phone_from_identity(identity: str | None) -> str | None:
    """The caller's number, or None for an anonymous or malformed identity."""
    if not identity or not identity.startswith(_PHONE_PREFIX):
        return None
    return normalise_mobile(identity[len(_PHONE_PREFIX):])
