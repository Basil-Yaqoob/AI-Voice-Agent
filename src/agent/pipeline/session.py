"""Per-call session state machine.

Implemented in TASK-08. This module is on the hot path: everything reachable from
an audio callback must be async with no synchronous I/O, and no vendor SDK may be
imported here (enforced by .importlinter).
"""

from __future__ import annotations
