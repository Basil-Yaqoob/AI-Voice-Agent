"""The DDL is the source of truth, so it has to stay parseable and documented.

Three things derive from ``schema.sql`` — the spreadsheet, the loader and the
data dictionary. If the parser silently drops a column, all three lose it and
nothing else fails, so the parser is tested harder than its size suggests.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent.data.schema_meta import load_schema, schema_path, schema_sql

EXPECTED_TABLES = {
    "dataset_meta", "branches", "pharmacists", "riders", "products", "substitutes",
    "inventory", "customers", "addresses", "prescriptions", "orders", "order_items",
    "deliveries", "complaints", "refunds", "promotions", "refill_subscriptions",
}


def test_schema_file_exists() -> None:
    assert schema_path().exists()


def test_every_table_is_parsed() -> None:
    assert set(load_schema()) == EXPECTED_TABLES


def test_ddl_executes() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_sql())
        found = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()
    assert EXPECTED_TABLES <= found


def test_parsed_columns_match_sqlite() -> None:
    """The parser and SQLite must agree, or the loader inserts into the wrong slots."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_sql())
        for name, table in load_schema().items():
            actual = [row[1] for row in connection.execute(f"PRAGMA table_info({name})")]
            assert list(table.column_names) == actual, name
    finally:
        connection.close()


def test_every_column_is_documented() -> None:
    """The data dictionary is generated from these comments; a blank one is a hole."""
    undocumented = [
        f"{table.name}.{column.name}"
        for table in load_schema().values()
        for column in table.columns
        if not column.comment
    ]
    assert undocumented == []


def test_foreign_keys_resolve() -> None:
    schema = load_schema()
    for table in schema.values():
        for column in table.columns:
            if not column.references:
                continue
            target_table, target_column = column.references.split(".")
            assert target_table in schema, f"{table.name}.{column.name} -> {target_table}"
            assert target_column in schema[target_table].column_names


def test_check_constraints_are_captured() -> None:
    status = load_schema()["orders"].column("status")
    assert status.check is not None
    assert "out_for_delivery" in status.check
    # The CHECK wraps over four lines in the DDL. If the bracket-depth join
    # regressed to line-by-line parsing, the tail would be lost.
    assert "refunded" in status.check


def test_nullability_and_keys() -> None:
    orders = load_schema()["orders"]
    assert orders.column("order_id").is_primary_key
    assert not orders.column("order_id").nullable
    assert orders.column("delivered_at").nullable
    assert orders.column("customer_id").references == "customers.customer_id"
    assert orders.column("customer_id").key == "FK"


def test_defaults_are_captured() -> None:
    products = load_schema()["products"]
    assert products.column("is_active").default == "1"
    assert products.column("max_qty_per_order").default == "10"


def test_money_columns_are_real() -> None:
    """Rupees, not paisa. A price stored as INTEGER would round 149.50 away."""
    for table in load_schema().values():
        for column in table.columns:
            if column.name.endswith("_pkr"):
                assert column.sql_type == "REAL", f"{table.name}.{column.name}"


def test_indexed_lookup_columns() -> None:
    """Every column a tool filters on needs an index; a scan blows the budget."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_sql())
        indexed = {
            (row[0], column)
            for row in connection.execute(
                "SELECT tbl_name, name FROM sqlite_master WHERE type = 'index'"
            )
            for column in [
                info[2] for info in connection.execute(f"PRAGMA index_info({row[1]})")
            ]
        }
    finally:
        connection.close()

    for table, column in [
        ("customers", "phone"), ("orders", "customer_id"), ("orders", "status"),
        ("order_items", "order_id"), ("deliveries", "order_id"),
        ("complaints", "customer_id"), ("products", "brand_name"),
        ("inventory", "sku"), ("branches", "city"),
    ]:
        assert (table, column) in indexed, f"{table}.{column} is not indexed"


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_every_table_has_a_primary_key(table: str) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_sql())
        info = list(connection.execute(f"PRAGMA table_info({table})"))
    finally:
        connection.close()
    assert any(row[5] for row in info), f"{table} has no primary key"
