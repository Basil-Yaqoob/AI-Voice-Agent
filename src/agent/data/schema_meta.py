"""Read ``schema.sql`` as data.

The DDL is the single source of truth for the demo database. Three things need
to agree with it — the spreadsheet the client edits, the loader that fills the
database, and the data dictionary a human reads — and the cheapest way to keep
three things in agreement is to derive all of them from one.

So this parses the DDL rather than restating it. The parser is deliberately
small: it understands the subset of SQLite DDL that ``schema.sql`` actually
uses, and raises rather than guessing when it meets anything else. A parser
that silently skips a column it cannot read would let a column go missing from
the loader without a single test failing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = ["Column", "Table", "load_schema", "schema_path", "schema_sql"]

_SCHEMA = Path(__file__).with_name("schema.sql")

# CREATE TABLE <name> ( ... ); — non-greedy to the first ");" at line start.
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?P<name>\w+)\s*\((?P<body>.*?)\n\);",
    re.DOTALL | re.IGNORECASE,
)
_CREATE_VIEW = re.compile(r"CREATE\s+VIEW\s+(?P<name>\w+)\s+AS", re.IGNORECASE)

# A column definition, comment already stripped: name, type, then constraints.
_COLUMN = re.compile(
    r"^(?P<name>[a-z_][a-z0-9_]*)\s+"
    r"(?P<type>TEXT|INTEGER|REAL|BLOB|NUMERIC)\b"
    r"(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

# Lines inside CREATE TABLE that define something other than a column.
_TABLE_CONSTRAINT = re.compile(
    r"^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    nullable: bool
    is_primary_key: bool
    references: str | None  # "table.column"
    check: str | None
    default: str | None
    comment: str

    @property
    def key(self) -> str:
        if self.is_primary_key:
            return "PK"
        if self.references:
            return "FK"
        return ""


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def column(self, name: str) -> Column:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(f"{self.name} has no column {name!r}")


def schema_path() -> Path:
    return _SCHEMA


def schema_sql() -> str:
    return _SCHEMA.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Table]:
    """Parse ``schema.sql`` into tables. Views are ignored — they have no rows."""
    sql = schema_sql()
    tables: dict[str, Table] = {}

    for match in _CREATE_TABLE.finditer(sql):
        name = match.group("name")
        # A view body can contain "\n);" too; skip anything the view regex claims.
        if _CREATE_VIEW.search(sql[: match.start()][-40:]):
            continue
        tables[name] = Table(name=name, columns=_parse_columns(name, match.group("body")))

    if not tables:
        raise ValueError(f"no CREATE TABLE statements found in {_SCHEMA}")
    return tables


def _parse_columns(table: str, body: str) -> tuple[Column, ...]:
    columns: list[Column] = []
    for definition, comment in _logical_lines(body):
        if _TABLE_CONSTRAINT.match(definition):
            continue

        match = _COLUMN.match(definition)
        if match is None:
            raise ValueError(f"{table}: cannot parse column definition {definition!r}")

        rest = match.group("rest").upper()
        columns.append(
            Column(
                name=match.group("name"),
                sql_type=match.group("type").upper(),
                nullable="NOT NULL" not in rest and "PRIMARY KEY" not in rest,
                is_primary_key="PRIMARY KEY" in rest,
                references=_reference(match.group("rest")),
                check=_check(match.group("rest")),
                default=_default(match.group("rest")),
                comment=comment,
            )
        )

    if not columns:
        raise ValueError(f"{table}: no columns parsed")
    return tuple(columns)


def _logical_lines(body: str) -> list[tuple[str, str]]:
    """Split a table body into (definition, comment) pairs.

    A definition can wrap across lines — a long CHECK list usually does — so
    lines are joined until the brackets balance. Doing it by bracket depth
    rather than by how a continuation *looks* means reformatting the DDL
    cannot silently drop a constraint.
    """
    pairs: list[tuple[str, str]] = []
    buffer = ""
    comments: list[str] = []
    depth = 0

    for raw in body.splitlines():
        code, _, comment = raw.partition("--")
        code = code.strip()
        comment = comment.strip()

        if not code:
            # A standalone comment line documents the table, not a column.
            continue

        buffer = f"{buffer} {code}".strip() if buffer else code
        if comment:
            comments.append(comment)
        depth += code.count("(") - code.count(")")

        if depth <= 0 and buffer.endswith(","):
            pairs.append((buffer.rstrip(",").strip(), " ".join(comments)))
            buffer, comments, depth = "", [], 0

    if buffer:
        pairs.append((buffer.rstrip(",").strip(), " ".join(comments)))
    return pairs


def _reference(rest: str) -> str | None:
    match = re.search(r"REFERENCES\s+(\w+)\s*\((\w+)\)", rest, re.IGNORECASE)
    return f"{match.group(1)}.{match.group(2)}" if match else None


def _check(rest: str) -> str | None:
    match = re.search(r"CHECK\s*\((.*)\)", rest, re.IGNORECASE | re.DOTALL)
    return " ".join(match.group(1).split()) if match else None


def _default(rest: str) -> str | None:
    match = re.search(r"DEFAULT\s+('[^']*'|[\w.]+)", rest, re.IGNORECASE)
    return match.group(1).strip("'") if match else None
