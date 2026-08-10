"""The async SQLite adapter.

The point of this file is not that SELECT works. It is that the adapter keeps
the three promises the hot path depends on: it never blocks the event loop, it
never shares a connection between threads, and foreign keys are actually on.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent.data.sqlite import Database


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    path = tmp_path / "t.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE child (
            id TEXT PRIMARY KEY,
            parent_id TEXT NOT NULL REFERENCES parent (id)
        );
        INSERT INTO parent VALUES ('p1', 'one'), ('p2', 'two');
        """
    )
    connection.commit()
    connection.close()

    database = Database(path, workers=3)
    yield database
    database.close()


async def test_fetch_all_returns_dicts(db: Database) -> None:
    rows = await db.fetch_all("SELECT * FROM parent ORDER BY id")
    assert rows == [{"id": "p1", "name": "one"}, {"id": "p2", "name": "two"}]


async def test_fetch_one_returns_none_when_empty(db: Database) -> None:
    assert await db.fetch_one("SELECT * FROM parent WHERE id = ?", ("nope",)) is None


async def test_parameters_are_bound_not_interpolated(db: Database) -> None:
    rows = await db.fetch_all("SELECT * FROM parent WHERE name = ?", ("'; DROP TABLE parent--",))
    assert rows == []
    assert await db.fetch_one("SELECT count(*) AS n FROM parent") == {"n": 2}


async def test_foreign_keys_are_enforced(db: Database) -> None:
    """PRAGMA foreign_keys is per connection. Forget it and bad writes pass silently."""
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("INSERT INTO child VALUES ('c1', 'ghost')")


async def test_a_failed_write_rolls_back(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("INSERT INTO parent VALUES ('p1', 'duplicate')")
    assert await db.fetch_one("SELECT count(*) AS n FROM parent") == {"n": 2}


async def test_write_then_read(db: Database) -> None:
    assert await db.execute("INSERT INTO parent VALUES ('p3', 'three')") == 1
    assert await db.fetch_one("SELECT name FROM parent WHERE id = ?", ("p3",)) == {
        "name": "three"
    }


async def test_the_event_loop_keeps_running_during_a_query(db: Database) -> None:
    """The whole reason this adapter exists. A blocked loop is a stalled call."""
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        for _ in range(30):
            await asyncio.sleep(0.001)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    # Deliberately expensive: a cross join over enough rows to take real time.
    await db.fetch_all(
        "SELECT count(*) FROM parent a, parent b, parent c, parent d, parent e"
    )
    await beat
    assert ticks == 30


async def test_concurrent_queries_do_not_share_a_connection(db: Database) -> None:
    """sqlite3 objects are not thread safe. Sharing one is a crash, not a slowdown."""
    results = await asyncio.gather(
        *(db.fetch_all("SELECT * FROM parent") for _ in range(24))
    )
    assert all(len(r) == 2 for r in results)


async def test_warm_opens_a_connection_per_worker(db: Database) -> None:
    await db.warm()
    assert len(db._connected) == 3  # the private set is the property under test


async def test_warm_makes_the_first_query_cheap(db: Database) -> None:
    await db.warm()
    loop = asyncio.get_running_loop()
    started = loop.time()
    await db.fetch_one("SELECT 1 AS ok")
    assert (loop.time() - started) < 0.05


async def test_query_timing_uses_a_fine_grained_clock() -> None:
    """Regression: the slow-query timer must not use the event loop clock.

    On Windows ``loop.time()`` is ``time.monotonic()`` at 15.625ms resolution —
    coarser than half the 25ms slow-query threshold. Durations quantise to
    multiples of it, so fast indexed queries report 15.6 or 31.2ms and the log
    fills with warnings about queries that are perfectly fine.
    """
    from agent.data import sqlite as module

    assert module._now is time.perf_counter
    assert time.get_clock_info("perf_counter").resolution <= 1e-6
    assert module.SLOW_QUERY_MS > time.get_clock_info("perf_counter").resolution * 1000


async def test_a_fast_query_is_not_reported_as_slow(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """The bug this guards was invisible except as noise in the log."""
    await db.warm()
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            await db.fetch_all("SELECT * FROM parent WHERE id = ?", ("p1",))
    assert "slow_query" not in caplog.text


async def test_health_reports_true(db: Database) -> None:
    assert await db.health() is True


async def test_health_reports_false_for_a_missing_file(tmp_path: Path) -> None:
    database = Database(tmp_path / "absent.db")
    try:
        assert await database.health() is False
    finally:
        database.close()


async def test_missing_file_says_how_to_build_it(tmp_path: Path) -> None:
    database = Database(tmp_path / "absent.db")
    try:
        with pytest.raises(FileNotFoundError, match="build_dvago_db"):
            await database.fetch_all("SELECT 1")
    finally:
        database.close()


async def test_close_is_idempotent(db: Database) -> None:
    await db.fetch_one("SELECT 1")
    db.close()
    db.close()


async def test_use_after_close_raises(db: Database) -> None:
    db.close()
    with pytest.raises(RuntimeError, match="closed"):
        await db.fetch_one("SELECT 1")


async def test_close_leaves_no_connections_behind(tmp_path: Path) -> None:
    """A leaked connection holds the WAL open and the next build cannot replace the file."""
    path = tmp_path / "t.db"
    sqlite3.connect(path).close()
    database = Database(path, workers=2)
    await asyncio.gather(*(database.fetch_all("SELECT 1") for _ in range(8)))
    database.close()

    live = [t for t in threading.enumerate() if t.name.startswith("sqlite")]
    assert live == []
