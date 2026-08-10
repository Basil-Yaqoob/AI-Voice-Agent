"""The loader's job is to refuse bad data, so these mostly feed it bad data.

The spreadsheet is the part a client edits, which makes it the part that breaks.
Every check here corresponds to a way somebody has broken a spreadsheet: a typo
in a status, a deleted parent row, a column dragged into the wrong place, a
number stored as text.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent.data.loader import ValidationError, allowed_values, validate, write_database
from agent.data.schema_meta import load_schema

SCHEMA = load_schema()


def _minimal() -> dict[str, list[dict[str, Any]]]:
    """One valid row in the tables that need one, empty elsewhere.

    Written positionally against the DDL so a schema change shows up here
    rather than as a mystery insert failure.
    """
    def row(table: str, **values: Any) -> dict[str, Any]:
        return {column: values.get(column) for column in SCHEMA[table].column_names}

    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in SCHEMA}
    tables["dataset_meta"] = [row("dataset_meta", key="synthetic", value="yes")]
    tables["branches"] = [
        row("branches", branch_id="B1", branch_name="DVAGO Test", city="Karachi",
            area="Bahadurabad", address="addr", phone="021-111-38246",
            latitude=24.8, longitude=67.0, opens_at="09:00", closes_at="22:00",
            is_24_7=0, has_cold_chain=1, has_pharmacist=1, supports_pickup=1,
            supports_express=1, delivery_radius_km=8.0, opened_on="2024-01-01",
            status="active")
    ]
    tables["customers"] = [
        row("customers", customer_id="C1", full_name="Ayesha Khan", phone="03001234567",
            city="Karachi", preferred_language="urdu", loyalty_tier="gold",
            loyalty_points=10, wallet_balance_pkr=0.0, is_chronic_patient=0,
            registered_on="2024-01-01", total_orders=1, lifetime_value_pkr=100.0,
            is_blocked=0, do_not_call=0)
    ]
    tables["products"] = [
        row("products", sku="P1", brand_name="Panadol", generic_name="Paracetamol",
            strength="500mg", dosage_form="tablet", pack_size="10 tablets",
            units_per_pack=10, manufacturer="GSK Pakistan", category="Medicines",
            price_pkr=65.0, mrp_pkr=71.0, discount_pct=8.0, requires_rx=0,
            is_controlled=0, is_cold_chain=0, is_active=1, max_qty_per_order=10)
    ]
    tables["orders"] = [
        row("orders", order_id="DV-1", customer_id="C1", branch_id="B1",
            placed_at="2026-08-10T11:00:00", channel="app", fulfilment_type="express_1hr",
            status="delivered", payment_method="cod", payment_status="paid",
            subtotal_pkr=65.0, discount_pkr=0.0, delivery_fee_pkr=149.0,
            total_pkr=214.0, requires_cold_chain=0)
    ]
    return tables


def test_a_minimal_valid_dataset_passes() -> None:
    assert validate(_minimal(), SCHEMA) == []


def test_it_builds(tmp_path: Path) -> None:
    counts = write_database(_minimal(), SCHEMA, tmp_path / "out.db")
    assert counts["customers"] == 1
    assert counts["inventory"] == 0

    connection = sqlite3.connect(tmp_path / "out.db")
    try:
        assert connection.execute("SELECT count(*) FROM orders").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


# ── the ways a spreadsheet breaks ────────────────────────────────────────


def test_a_missing_sheet_is_reported() -> None:
    tables = _minimal()
    del tables["promotions"]
    assert any("no such sheet" in p for p in validate(tables, SCHEMA))


def test_an_unknown_sheet_is_reported() -> None:
    tables = _minimal()
    tables["my_working_notes"] = []
    assert any("not a table in schema.sql" in p for p in validate(tables, SCHEMA))


def test_reordered_columns_are_caught() -> None:
    """The loader writes positionally. A dragged column would corrupt silently."""
    tables = _minimal()
    original = tables["customers"][0]
    tables["customers"] = [dict(reversed(list(original.items())))]
    problems = validate(tables, SCHEMA)
    assert any("columns do not match" in p for p in problems)


def test_a_missing_required_value_is_caught() -> None:
    tables = _minimal()
    tables["customers"][0]["full_name"] = None
    assert any("required but empty" in p for p in validate(tables, SCHEMA))


def test_a_nullable_column_may_be_empty() -> None:
    tables = _minimal()
    tables["customers"][0]["email"] = None
    assert validate(tables, SCHEMA) == []


def test_a_column_with_a_default_may_be_empty() -> None:
    tables = _minimal()
    tables["products"][0]["max_qty_per_order"] = None
    assert validate(tables, SCHEMA) == []


def test_text_in_a_number_column_is_caught() -> None:
    tables = _minimal()
    tables["products"][0]["price_pkr"] = "sixty five"
    assert any("is not a number" in p for p in validate(tables, SCHEMA))


def test_text_in_an_integer_column_is_caught() -> None:
    tables = _minimal()
    tables["products"][0]["units_per_pack"] = "ten"
    assert any("is not an integer" in p for p in validate(tables, SCHEMA))


def test_a_number_stored_as_text_still_passes() -> None:
    """Excel does this constantly and it is not an error."""
    tables = _minimal()
    tables["products"][0]["units_per_pack"] = "10"
    assert validate(tables, SCHEMA) == []


def test_a_value_outside_a_check_constraint_is_caught() -> None:
    tables = _minimal()
    tables["orders"][0]["status"] = "on its way"
    problems = validate(tables, SCHEMA)
    assert any("is not one of" in p and "out_for_delivery" in p for p in problems)


def test_a_dangling_foreign_key_is_caught() -> None:
    """The classic: someone deletes a customer the orders still point at."""
    tables = _minimal()
    tables["customers"] = []
    problems = validate(tables, SCHEMA)
    assert any("does not exist in customers.customer_id" in p for p in problems)


def test_a_duplicate_primary_key_is_caught() -> None:
    tables = _minimal()
    tables["customers"].append(dict(tables["customers"][0]))
    assert any("duplicate primary key" in p for p in validate(tables, SCHEMA))


def test_problems_are_reported_with_a_row_number() -> None:
    tables = _minimal()
    tables["customers"][0]["loyalty_tier"] = "diamond"
    problem = validate(tables, SCHEMA)[0]
    assert "customers row 2" in problem  # row 1 is the header


def test_every_problem_is_reported_not_just_the_first() -> None:
    tables = _minimal()
    tables["customers"][0]["loyalty_tier"] = "diamond"
    tables["customers"][0]["full_name"] = None
    tables["orders"][0]["status"] = "nope"
    assert len(validate(tables, SCHEMA)) >= 3


# ── the build is atomic ──────────────────────────────────────────────────


def test_bad_data_raises_rather_than_writing(tmp_path: Path) -> None:
    tables = _minimal()
    tables["orders"][0]["status"] = "invented"
    target = tmp_path / "out.db"

    with pytest.raises(ValidationError) as caught:
        write_database(tables, SCHEMA, target)
    assert caught.value.problems
    assert not target.exists()


def test_a_failed_build_leaves_the_previous_database_intact(tmp_path: Path) -> None:
    """The afternoon-before-a-demo scenario. Yesterday's database must survive."""
    target = tmp_path / "out.db"
    write_database(_minimal(), SCHEMA, target)
    before = target.read_bytes()

    broken = _minimal()
    broken["customers"] = []  # orders now point at nothing
    with pytest.raises(ValidationError):
        write_database(broken, SCHEMA, target)

    assert target.read_bytes() == before


def test_no_staging_file_is_left_behind(tmp_path: Path) -> None:
    target = tmp_path / "out.db"
    write_database(_minimal(), SCHEMA, target)
    assert list(tmp_path.glob("*.tmp")) == []


# ── the CHECK parser ─────────────────────────────────────────────────────


def test_allowed_values_reads_a_wrapped_check() -> None:
    values = allowed_values(SCHEMA["orders"].column("status"))
    assert values is not None
    assert "out_for_delivery" in values
    assert "refunded" in values  # the last line of a four-line CHECK


def test_allowed_values_drops_the_null_literal() -> None:
    values = allowed_values(SCHEMA["customers"].column("gender"))
    assert values == {"male", "female", "other"}


def test_allowed_values_is_none_for_a_range_check() -> None:
    assert allowed_values(SCHEMA["complaints"].column("csat_score")) is None


def test_allowed_values_is_none_for_an_unconstrained_column() -> None:
    assert allowed_values(SCHEMA["customers"].column("full_name")) is None
