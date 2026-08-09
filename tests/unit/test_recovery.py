"""Every failure must reach the caller as audio, never as silence."""

from __future__ import annotations

import pytest

from agent.language.script import contains_urdu
from agent.pipeline.recovery import RECOVERY_LINES, Recovery, classify


def test_every_recovery_kind_has_a_line() -> None:
    """A missing entry means silence on that failure path, and silence reads as
    a dropped call."""
    missing = [kind for kind in Recovery if kind not in RECOVERY_LINES]
    assert not missing, f"no recovery line for: {missing}"


@pytest.mark.parametrize("kind", list(Recovery))
def test_lines_are_in_urdu(kind: Recovery) -> None:
    assert contains_urdu(RECOVERY_LINES[kind].text)


@pytest.mark.parametrize("kind", list(Recovery))
def test_lines_are_short(kind: Recovery) -> None:
    """A caller waiting on a failure will not sit through a paragraph."""
    assert len(RECOVERY_LINES[kind].text) < 80


@pytest.mark.parametrize("kind", list(Recovery))
def test_asset_paths_are_wav(kind: Recovery) -> None:
    assert RECOVERY_LINES[kind].path.suffix == ".wav"


def test_asset_names_are_unique() -> None:
    assets = [line.asset for line in RECOVERY_LINES.values()]
    assert len(assets) == len(set(assets))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), Recovery.RETRY),
        (ConnectionError(), Recovery.RETRY),
        (OSError(), Recovery.RETRY),
        (ValueError("bad"), Recovery.HANDOFF),
        (RuntimeError("provider 503"), Recovery.HANDOFF),
    ],
)
def test_first_attempt_classification(error: BaseException, expected: Recovery) -> None:
    assert classify(error, attempt=1) is expected


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), ValueError()])
def test_second_attempt_always_hands_off(error: BaseException) -> None:
    """One silent retry, then a human. Three retries with backoff leaves the
    caller in silence for seconds, which is worse than being handed over."""
    assert classify(error, attempt=2) is Recovery.HANDOFF


def test_handoff_line_offers_a_person() -> None:
    assert "colleague" in RECOVERY_LINES[Recovery.HANDOFF].text
