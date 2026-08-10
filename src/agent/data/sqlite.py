"""Async access to the demo database.

``sqlite3`` is synchronous, and hot-path rule 1 says nothing reachable from an
audio callback may block. A tool call *is* reachable from one: the model asks
for stock while the caller is on the line and the frame pump has to keep
running. So every query goes to a thread pool, the same treatment the Azure
Speech SDK gets for the same reason.

Three details that are easy to get wrong:

* **Connections are per thread.** ``sqlite3`` objects are not safe to share
  across threads. Rather than pass ``check_same_thread=False`` and serialise
  everything behind one lock — a lock on the hot path is a queue with better
  manners — each worker thread opens its own connection lazily.
* **``PRAGMA foreign_keys`` is per connection, not per database.** Set it once
  at build time and it is off again on the next connect. It is set here on
  every connection, which is the only thing that makes a bad write fail loudly.
* **WAL, so a write never blocks a read.** Filing a complaint mid-call must not
  stall a stock lookup on another call.

This adapter knows nothing about DVAGO. The queries live in
``agent.tools.dvago_backend``, so swapping SQLite for the client's real API
means replacing that file and leaving this one alone.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from agent.telemetry.logging import get_logger

__all__ = ["Database", "Row"]

log = get_logger(__name__)

Row = dict[str, Any]
_R = TypeVar("_R")

# Small on purpose. SQLite serialises writers anyway, and a large pool would
# just hold more connections open to lose the same contention.
_DEFAULT_WORKERS = 4

# A query slower than this is a missing index, not a slow disk. Tool calls sit
# inside the 300ms LLM slice of the latency budget and have nowhere to hide.
SLOW_QUERY_MS = 25.0

# Must be perf_counter, never loop.time(). On Windows the event loop clock is
# time.monotonic(), whose resolution is 15.625ms — coarser than half this
# threshold. Every duration quantises to a multiple of it, so a 1ms query that
# straddles a tick boundary reports 15.6ms and two of them report 31.2ms. The
# symptom is a log full of slow_query warnings naming perfectly good indexed
# queries, and a day spent optimising them. perf_counter is sub-microsecond on
# every platform we run on.
_now = time.perf_counter


class Database:
    """A thread-pooled, read-mostly SQLite connection."""

    def __init__(self, path: Path | str, *, workers: int = _DEFAULT_WORKERS) -> None:
        self._path = Path(path)
        self._local = threading.local()
        self._workers = workers
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="sqlite"
        )
        # Which threads hold a connection, so close() knows how many to reclaim.
        self._connected: set[int] = set()
        self._connected_lock = threading.Lock()
        self._closed = False

    @property
    def path(self) -> Path:
        return self._path

    def _connection(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if connection is not None:
            return connection

        if not self._path.exists():
            raise FileNotFoundError(
                f"no database at {self._path}. Build it with:\n"
                "  uv run python scripts/generate_dvago_dataset.py\n"
                "  uv run python scripts/build_dvago_db.py"
            )

        # Serialised, and it has to be. Switching a database to WAL takes an
        # exclusive lock that `busy_timeout` does not cover — SQLite returns
        # SQLITE_BUSY immediately rather than invoking the busy handler — so
        # threads racing to connect, which is exactly what warm() does, would
        # collide. Connecting happens once per thread and never on the hot
        # path, so a lock here costs nothing worth measuring.
        with self._connected_lock:
            connection = sqlite3.connect(self._path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 2000")
            # Per connection, not per database. Set it at build time and it is
            # off again here, and every bad write passes silently.
            connection.execute("PRAGMA foreign_keys = ON")
            # Persistent in the file header: the first connection sets it, the
            # rest find it already set and the pragma becomes a no-op.
            connection.execute("PRAGMA journal_mode = WAL")
            self._local.conn = connection
            self._connected.add(threading.get_ident())
        return connection

    async def warm(self) -> None:
        """Open every pool thread's connection before the first call arrives.

        Connecting costs roughly a hundred milliseconds — file open, WAL
        journal, three pragmas. Paying that inside the first tool call would
        spend a third of the entire turn budget on setup, and it would show up
        as a slow-query warning that points at an innocent SELECT.
        """
        loop = asyncio.get_running_loop()
        barrier = threading.Barrier(self._workers, timeout=5.0)

        def _open() -> None:
            self._connection()
            try:
                barrier.wait()  # hold the thread so a sibling cannot take this slot
            except threading.BrokenBarrierError:
                pass

        await asyncio.gather(
            *(loop.run_in_executor(self._pool, _open) for _ in range(self._workers))
        )

    # ── queries ───────────────────────────────────────────────────────────

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        return await self._run(self._fetch_all, sql, params)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> Row | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run a write. Returns the number of rows affected."""
        return await self._run(self._execute, sql, params)

    async def _run(
        self,
        fn: Callable[[str, tuple[Any, ...]], _R],
        sql: str,
        params: tuple[Any, ...],
    ) -> _R:
        if self._closed:
            raise RuntimeError("database is closed")
        loop = asyncio.get_running_loop()
        started = _now()
        try:
            return await loop.run_in_executor(self._pool, fn, sql, params)
        finally:
            elapsed_ms = (_now() - started) * 1000
            if elapsed_ms > SLOW_QUERY_MS:
                log.warning(
                    "slow_query", ms=round(elapsed_ms, 1), sql=" ".join(sql.split())[:120]
                )

    def _fetch_all(self, sql: str, params: tuple[Any, ...]) -> list[Row]:
        cursor = self._connection().execute(sql, params)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _execute(self, sql: str, params: tuple[Any, ...]) -> int:
        connection = self._connection()
        with connection:  # commits on success, rolls back on exception
            cursor = connection.execute(sql, params)
            try:
                return cursor.rowcount
            finally:
                cursor.close()

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def health(self) -> bool:
        """Whether the database answers. Used by /readyz."""
        try:
            await self.fetch_one("SELECT 1 AS ok")
        except Exception as exc:
            log.warning("database_unhealthy", detail=str(exc))
            return False
        return True

    def close(self) -> None:
        """Shut the pool down, closing each thread's connection from that thread.

        A connection may only be closed by the thread that opened it, so the
        close has to happen inside the pool. Submitting one task per connected
        thread is not enough on its own — a thread that finishes early would
        take a second task and another connection would leak — so each task
        waits on a barrier until every one of them is running. That pins
        exactly one task to each thread.
        """
        if self._closed:
            return
        self._closed = True

        with self._connected_lock:
            count = len(self._connected)

        if count:
            barrier = threading.Barrier(count, timeout=5.0)
            futures = [self._pool.submit(self._close_local, barrier) for _ in range(count)]
            for future in futures:
                future.exception()  # surface nothing; shutdown must not raise

        self._pool.shutdown(wait=True)

    def _close_local(self, barrier: threading.Barrier) -> None:
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            # Timed out waiting for a sibling. Close what this thread holds
            # anyway; the pool shutdown below reclaims whatever is left.
            pass
        connection: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if connection is not None:
            connection.close()
            self._local.conn = None
