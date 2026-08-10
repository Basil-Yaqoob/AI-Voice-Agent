"""Validate tabular rows against ``schema.sql`` and build the database.

Knows nothing about Excel. It takes ``{table: [row, ...]}`` and the DDL, and
that is the whole interface — which keeps ``openpyxl`` a development-only
dependency, out of the running agent, and makes every rule here testable
against plain dictionaries.

The spreadsheet upstream is what a client edits. Someone will set a status to a
value the CHECK constraint has never heard of, or delete the customer a hundred
orders point at, and they will do it the afternoon before a demo. So this
validates everything *before* writing anything, reports every problem at once
rather than the first, and leaves the existing database untouched if it finds
any. "FOREIGN KEY constraint failed" with no row number is not a useful thing
to hand someone an hour before a meeting.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from agent.data.schema_meta import Column, Table, schema_sql

__all__ = ["ValidationError", "allowed_values", "validate", "write_database"]


class ValidationError(Exception):
    """Carries every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"{len(problems)} problem(s) in the source data")
        self.problems = problems


def allowed_values(column: Column) -> set[str] | None:
    """The value set of a ``CHECK (col IN (...))``, or None for other checks."""
    if not column.check or f"{column.name} IN (" not in column.check:
        return None
    inner = column.check.split(f"{column.name} IN (", 1)[1]
    inner = inner[: inner.rfind(")")]
    values = {part.strip().strip("'") for part in inner.split(",")}
    values.discard("NULL")  # nullability is checked separately
    return values


def validate(
    tables: dict[str, list[dict[str, Any]]], schema: dict[str, Table]
) -> list[str]:
    """Every problem in the data, as readable sentences with row references."""
    problems: list[str] = []

    problems += [
        f"schema.sql defines table {name!r} but the source has no such sheet"
        for name in sorted(set(schema) - set(tables))
    ]
    problems += [
        f"source has sheet {name!r} which is not a table in schema.sql"
        for name in sorted(set(tables) - set(schema))
    ]

    # Primary keys first, so foreign keys can be resolved against them.
    keys: dict[str, set[Any]] = {}
    for name, table in schema.items():
        primary = [c.name for c in table.columns if c.is_primary_key]
        if len(primary) == 1:
            # Composite keys are declared as table constraints and are left to
            # SQLite; a single-column key is what a foreign key can point at.
            keys[name] = {row.get(primary[0]) for row in tables.get(name, [])}

    for name, table in schema.items():
        rows = tables.get(name)
        if rows is None:
            continue

        expected = list(table.column_names)
        if rows and list(rows[0]) != expected:
            problems.append(
                f"{name}: columns do not match schema.sql\n"
                f"    expected: {expected}\n"
                f"    found:    {list(rows[0])}"
            )
            continue

        seen: dict[str, set[Any]] = defaultdict(set)
        for index, row in enumerate(rows, start=2):  # row 1 is the header
            for column in table.columns:
                problems += _check_cell(name, index, column, row.get(column.name), keys)
                if not column.is_primary_key:
                    continue
                value = row.get(column.name)
                if value in seen[column.name]:
                    problems.append(f"{name} row {index}: duplicate primary key {value!r}")
                seen[column.name].add(value)

    return problems


def _check_cell(
    table: str, row_no: int, column: Column, value: Any, keys: dict[str, set[Any]]
) -> list[str]:
    where = f"{table} row {row_no}, column {column.name}"

    if value is None or value == "":
        if column.nullable or column.default is not None:
            return []
        return [f"{where}: required but empty"]

    if column.sql_type == "INTEGER":
        try:
            value = int(value)
        except (TypeError, ValueError):
            return [f"{where}: {value!r} is not an integer"]
    elif column.sql_type == "REAL":
        try:
            value = float(value)
        except (TypeError, ValueError):
            return [f"{where}: {value!r} is not a number"]

    problems: list[str] = []
    allowed = allowed_values(column)
    if allowed is not None and str(value) not in allowed:
        problems.append(f"{where}: {value!r} is not one of {sorted(allowed)}")

    if column.references:
        target_table, _ = column.references.split(".")
        known = keys.get(target_table)
        if known is not None and value not in known:
            problems.append(f"{where}: {value!r} does not exist in {column.references}")

    return problems


def write_database(
    tables: dict[str, list[dict[str, Any]]],
    schema: dict[str, Table],
    db_path: Path,
) -> dict[str, int]:
    """Build into a staging file, then move it into place.

    A half-written database is worse than none: the agent would start, connect,
    and answer some questions correctly and others not at all. Staging means a
    failed build leaves yesterday's working database exactly where it was.

    Raises :class:`ValidationError` if the data does not pass, without touching
    ``db_path``.
    """
    problems = validate(tables, schema)
    if problems:
        raise ValidationError(problems)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    staging = db_path.with_suffix(".db.tmp")
    staging.unlink(missing_ok=True)

    connection = sqlite3.connect(staging)
    counts: dict[str, int] = {}
    try:
        connection.executescript(schema_sql())
        connection.execute("PRAGMA foreign_keys = ON")

        for name, table in schema.items():
            rows = tables.get(name, [])
            columns = list(table.column_names)
            placeholders = ", ".join("?" * len(columns))
            connection.executemany(
                f"INSERT INTO {name} ({', '.join(columns)}) VALUES ({placeholders})",
                [
                    tuple(_coerce(table.column(c), row.get(c)) for c in columns)
                    for row in rows
                ],
            )
            counts[name] = len(rows)

        # Catches anything the pre-flight missed — before the file moves into
        # place rather than after.
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValidationError([f"foreign key violation: {v}" for v in violations[:20]])

        connection.execute("ANALYZE")
        connection.commit()
    except BaseException:
        connection.close()
        staging.unlink(missing_ok=True)
        raise
    else:
        connection.close()

    db_path.unlink(missing_ok=True)
    staging.replace(db_path)
    return counts


def _coerce(column: Column, value: Any) -> Any:
    if value is None or value == "":
        return None
    if column.sql_type == "INTEGER":
        return int(value)
    if column.sql_type == "REAL":
        return float(value)
    return str(value)
