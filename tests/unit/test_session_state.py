"""TASK-08 session state machine.

Voice bugs are overwhelmingly state bugs. With booleans they produce strange
audio; the point of the machine is that they raise instead.
"""

from __future__ import annotations

import pytest

from agent.pipeline.session import IllegalTransition, SessionState, StateMachine

S = SessionState


def test_starts_idle() -> None:
    assert StateMachine().state is S.IDLE


def test_happy_path() -> None:
    m = StateMachine()
    for target in (S.GREETING, S.LISTENING, S.THINKING, S.SPEAKING, S.LISTENING):
        m.to(target)
    assert m.state is S.LISTENING


def test_barge_in_from_speaking() -> None:
    m = StateMachine()
    m.to(S.LISTENING)
    m.to(S.THINKING)
    m.to(S.SPEAKING)
    m.to(S.LISTENING)  # caller interrupted
    assert m.state is S.LISTENING


def test_barge_in_from_thinking() -> None:
    """The caller can interrupt before the agent has said anything."""
    m = StateMachine()
    m.to(S.LISTENING)
    m.to(S.THINKING)
    m.to(S.LISTENING)
    assert m.state is S.LISTENING


def test_follow_up_turn_without_returning_to_listening() -> None:
    """A tool call means the agent thinks again mid-response."""
    m = StateMachine()
    m.to(S.LISTENING)
    m.to(S.THINKING)
    m.to(S.SPEAKING)
    m.to(S.THINKING)
    assert m.state is S.THINKING


@pytest.mark.parametrize(
    ("path", "illegal"),
    [
        ([], S.SPEAKING),  # cannot speak before listening
        ([], S.THINKING),
        ([S.GREETING], S.SPEAKING),
        ([S.LISTENING], S.SPEAKING),  # must think first
        ([S.LISTENING, S.THINKING, S.SPEAKING], S.GREETING),  # no second greeting
    ],
)
def test_illegal_transitions_raise(path: list[SessionState], illegal: SessionState) -> None:
    m = StateMachine()
    for step in path:
        m.to(step)
    with pytest.raises(IllegalTransition):
        m.to(illegal)


def test_illegal_transition_names_both_states() -> None:
    m = StateMachine()
    with pytest.raises(IllegalTransition) as exc:
        m.to(S.SPEAKING)
    assert "idle" in str(exc.value) and "speaking" in str(exc.value)


@pytest.mark.parametrize(
    "path",
    [
        [],
        [S.GREETING],
        [S.LISTENING],
        [S.LISTENING, S.THINKING],
        [S.LISTENING, S.THINKING, S.SPEAKING],
    ],
)
def test_hangup_allowed_from_any_state(path: list[SessionState]) -> None:
    """A caller can hang up at any moment; that is never a programming error."""
    m = StateMachine()
    for step in path:
        m.to(step)
    m.to(S.TERMINATED)
    assert not m.is_active


def test_nothing_follows_termination() -> None:
    m = StateMachine()
    m.to(S.TERMINATED)
    with pytest.raises(IllegalTransition):
        m.to(S.LISTENING)


def test_is_responding_only_while_thinking_or_speaking() -> None:
    """Gates whether a barge-in is meaningful."""
    m = StateMachine()
    assert not m.is_responding
    m.to(S.LISTENING)
    assert not m.is_responding
    m.to(S.THINKING)
    assert m.is_responding
    m.to(S.SPEAKING)
    assert m.is_responding


def test_can_predicts_to() -> None:
    m = StateMachine()
    for target in SessionState:
        predicted = m.can(target)
        try:
            StateMachine().to(target)
        except IllegalTransition:
            assert not predicted, f"can() said {target} was allowed"
        else:
            assert predicted, f"can() said {target} was forbidden"


def test_history_records_the_path() -> None:
    m = StateMachine()
    m.to(S.GREETING)
    m.to(S.LISTENING)
    assert m.history == [S.IDLE, S.GREETING, S.LISTENING]
