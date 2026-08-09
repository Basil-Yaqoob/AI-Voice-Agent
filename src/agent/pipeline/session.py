"""Per-call session state.

Modelled as an explicit machine rather than a handful of booleans, because voice
bugs are overwhelmingly state bugs: two turns running at once, TTS still playing
after hangup, a barge-in arriving during the greeting. With booleans those
produce strange audio; with a machine they raise.

Hot path. Everything here is async with no synchronous I/O, and no vendor SDK
may be imported (enforced by .importlinter).
"""

from __future__ import annotations

from enum import Enum

__all__ = ["IllegalTransition", "SessionState", "StateMachine"]


class SessionState(Enum):
    IDLE = "idle"
    GREETING = "greeting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    TERMINATED = "terminated"


class IllegalTransition(RuntimeError):
    """Raised instead of producing inexplicable audio."""

    def __init__(self, frm: SessionState, to: SessionState) -> None:
        super().__init__(f"illegal transition {frm.value} -> {to.value}")
        self.frm = frm
        self.to = to


# Barge-in is why SPEAKING and THINKING both return to LISTENING: the caller can
# interrupt at any point after the agent has started responding.
_ALLOWED: dict[SessionState, frozenset[SessionState]] = {
    SessionState.IDLE: frozenset({SessionState.GREETING, SessionState.LISTENING}),
    SessionState.GREETING: frozenset({SessionState.LISTENING}),
    SessionState.LISTENING: frozenset({SessionState.THINKING}),
    SessionState.THINKING: frozenset({SessionState.SPEAKING, SessionState.LISTENING}),
    SessionState.SPEAKING: frozenset({SessionState.LISTENING, SessionState.THINKING}),
    SessionState.TERMINATED: frozenset(),
}


class StateMachine:
    """Guards the legal call lifecycle."""

    def __init__(self, initial: SessionState = SessionState.IDLE) -> None:
        self._state = initial
        self.history: list[SessionState] = [initial]

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state is not SessionState.TERMINATED

    @property
    def is_responding(self) -> bool:
        """Whether a barge-in is meaningful right now."""
        return self._state in (SessionState.THINKING, SessionState.SPEAKING)

    def to(self, target: SessionState) -> None:
        if target is SessionState.TERMINATED:
            # A caller can hang up at any moment; that is never illegal.
            self._set(target)
            return

        if target not in _ALLOWED[self._state]:
            raise IllegalTransition(self._state, target)
        self._set(target)

    def can(self, target: SessionState) -> bool:
        return target is SessionState.TERMINATED or target in _ALLOWED[self._state]

    def _set(self, target: SessionState) -> None:
        self._state = target
        self.history.append(target)
