"""TASK-09 turn detection.

180ms of an 800ms budget, and the only large slice that costs nothing to
improve. Also the stage most likely to make the agent feel rude.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agent.pipeline.endpointing import AdaptiveEndpointer, BargeInDetector

E = AdaptiveEndpointer


@pytest.fixture
def ep() -> AdaptiveEndpointer:
    return AdaptiveEndpointer()


def test_empty_partial_is_patient(ep: AdaptiveEndpointer) -> None:
    """Nothing said yet: there is no reason to be in a hurry."""
    assert ep.silence_threshold_ms("") == E.MAX_MS
    assert ep.silence_threshold_ms("   ") == E.MAX_MS


@pytest.mark.parametrize(
    "partial",
    [
        "میرا آرڈر آیا اور",
        "یہ ٹھیک ہے لیکن",
        "میں پوچھنا چاہتا تھا کہ",
        "مجھے بتائیں کیونکہ",
        "پہلے یہ کریں پھر",
        "آپ دیکھیں یا",
    ],
)
def test_trailing_conjunction_waits_longer(ep: AdaptiveEndpointer, partial: str) -> None:
    """A caller who just said 'اور' is unambiguously still going. Cutting here
    is the rudest thing the agent can do."""
    assert ep.silence_threshold_ms(partial) == E.MAX_MS


@pytest.mark.parametrize(
    "partial",
    [
        "میرا آرڈر کہاں ہے",
        "مجھے refund چاہیے",
        "یہ کام ہو گیا ہے",
        "کل تک آ جائے گا",
        "وہ لوگ آ چکے ہیں",
        "مجھے نہیں",
    ],
)
def test_terminal_verb_cuts_early(ep: AdaptiveEndpointer, partial: str) -> None:
    """Urdu is verb-final. This is where the latency is won back: 120ms on the
    common case instead of 180ms, without raising the cut-off rate."""
    assert ep.silence_threshold_ms(partial) == E.MIN_MS


@pytest.mark.parametrize(
    "partial",
    ["میرا نمبر 0300", "آرڈر نمبر 4471", "میرا نمبر ۰۳۰۰"],
)
def test_mid_number_waits(ep: AdaptiveEndpointer, partial: str) -> None:
    """Callers reading a number pause between digit groups. Cutting there forces
    a full restart, which is deeply annoying."""
    assert ep.silence_threshold_ms(partial) == E.MAX_MS


def test_ordinary_partial_uses_the_base_window(ep: AdaptiveEndpointer) -> None:
    assert ep.silence_threshold_ms("میں ایک بات") == E.BASE_MS


def test_punctuation_does_not_hide_a_terminal_verb(ep: AdaptiveEndpointer) -> None:
    assert ep.silence_threshold_ms("میرا آرڈر کہاں ہے؟") == E.MIN_MS


def test_conjunction_beats_verb_when_it_comes_last(ep: AdaptiveEndpointer) -> None:
    """Only the final token decides; 'ہے اور' is still mid-thought."""
    assert ep.silence_threshold_ms("یہ ٹھیک ہے اور") == E.MAX_MS


@given(st.text())
def test_threshold_always_within_bounds(text: str) -> None:
    assert E.MIN_MS <= AdaptiveEndpointer().silence_threshold_ms(text) <= E.MAX_MS


@given(st.text())
def test_never_raises_on_arbitrary_text(text: str) -> None:
    """Runs on every interim transcript; an exception here drops the call."""
    AdaptiveEndpointer().silence_threshold_ms(text)


def test_bounds_are_configurable() -> None:
    tuned = AdaptiveEndpointer(base_ms=200, min_ms=100, max_ms=500)
    assert tuned.silence_threshold_ms("میں ایک بات") == 200
    assert tuned.silence_threshold_ms("یہ ہو گیا ہے") == 100
    assert tuned.silence_threshold_ms("") == 500


def test_adaptive_beats_a_fixed_window_on_average() -> None:
    """The claim the task rests on: a fixed threshold is either rude or slow,
    and adaptation improves both at once."""
    ep = AdaptiveEndpointer()
    complete = ["میرا آرڈر کہاں ہے", "یہ کام ہو گیا ہے", "کل تک آ جائے گا"]
    incomplete = ["میرا آرڈر آیا اور", "میں چاہتا تھا کہ", "میرا نمبر 0300"]

    assert all(ep.silence_threshold_ms(p) < E.BASE_MS for p in complete)
    assert all(ep.silence_threshold_ms(p) > E.BASE_MS for p in incomplete)


# ── false barge-in suppression ──────────────────────────────────────────────


def test_short_burst_does_not_interrupt() -> None:
    """A cough must not stop the agent mid-sentence."""
    d = BargeInDetector(min_speech_ms=150)
    assert not any(d.observe(is_speech=True, frame_ms=20) for _ in range(4))  # 80ms


def test_sustained_speech_interrupts() -> None:
    d = BargeInDetector(min_speech_ms=150)
    fired = [d.observe(is_speech=True, frame_ms=20) for _ in range(10)]
    assert any(fired)


def test_intermittent_noise_does_not_accumulate() -> None:
    """Repeated short bursts are a television, not a caller."""
    d = BargeInDetector(min_speech_ms=150, gap_resets_ms=100)
    fired = False
    for _ in range(10):
        for _ in range(3):  # 60ms of speech
            fired |= d.observe(is_speech=True, frame_ms=20)
        for _ in range(6):  # 120ms of silence resets the run
            fired |= d.observe(is_speech=False, frame_ms=20)
    assert not fired


def test_brief_gap_does_not_reset() -> None:
    """Natural micro-pauses inside a word must not restart the count."""
    d = BargeInDetector(min_speech_ms=150, gap_resets_ms=100)
    for _ in range(4):
        d.observe(is_speech=True, frame_ms=20)  # 80ms
    d.observe(is_speech=False, frame_ms=20)  # 20ms gap, below the reset
    fired = [d.observe(is_speech=True, frame_ms=20) for _ in range(4)]
    assert any(fired)


def test_reset_clears_state() -> None:
    d = BargeInDetector(min_speech_ms=150)
    for _ in range(5):
        d.observe(is_speech=True, frame_ms=20)
    d.reset()
    assert not d.observe(is_speech=True, frame_ms=20)


def test_silence_alone_never_fires() -> None:
    d = BargeInDetector()
    assert not any(d.observe(is_speech=False, frame_ms=20) for _ in range(50))
