"""The tools a caller's questions become.

Built against a small purpose-made database rather than the generated dataset,
so these tests run on a fresh clone and stay readable: when one fails you can
see the row that caused it a few lines above.

The properties that matter most here are not "does the query work" but the
three the file was written to enforce — identity is bound, allergies are
checked in the query, and availability means available.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent.data.schema_meta import schema_sql
from agent.data.sqlite import Database
from agent.tools.dvago_backend import DvagoBackend, build_dvago_registry

AYESHA = "03001234567"
BILAL = "03219876543"
STRANGER = "03009999999"


def _seed(connection: sqlite3.Connection) -> None:
    connection.executescript(schema_sql())
    connection.executemany(
        """INSERT INTO branches (branch_id, branch_name, city, area, address, landmark,
               phone, latitude, longitude, opens_at, closes_at, is_24_7, has_cold_chain,
               has_pharmacist, supports_pickup, supports_express, delivery_radius_km,
               opened_on, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("B1", "DVAGO Bahadurabad", "Karachi", "Bahadurabad", "addr", "roundabout",
             "021-111-38246", 24.8760, 67.0700, "00:00", "23:59", 1, 1, 1, 1, 1, 8.0,
             "2024-01-01", "active"),
            ("B2", "DVAGO Clifton", "Karachi", "Clifton", "addr", "Clifton Centre",
             "021-111-38246", 24.8138, 67.0300, "09:00", "23:00", 0, 1, 1, 1, 1, 6.0,
             "2024-01-01", "active"),
            ("B3", "DVAGO Gulberg", "Lahore", "Gulberg", "addr", "Liberty",
             "042-111-38246", 31.5100, 74.3450, "09:00", "22:00", 0, 0, 1, 1, 1, 5.0,
             "2024-01-01", "active"),
            ("B4", "DVAGO Closed", "Karachi", "Malir", "addr", "halt",
             "021-111-38246", 24.8900, 67.2000, "09:00", "22:00", 0, 0, 1, 1, 0, 3.0,
             "2024-01-01", "temporarily_closed"),
        ],
    )
    connection.executemany(
        """INSERT INTO products (sku, brand_name, generic_name, strength, dosage_form,
               pack_size, units_per_pack, manufacturer, category, subcategory,
               therapeutic_class, price_pkr, mrp_pkr, discount_pct, requires_rx,
               is_controlled, is_cold_chain, is_active, drap_reg_no, shelf_life_months,
               storage_note, max_qty_per_order, search_terms)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("P1", "Panadol", "Paracetamol", "500mg", "tablet", "10 tablets", 10,
             "GSK Pakistan", "Medicines", "Pain Relief", "analgesic", 65.0, 71.0, 8.0,
             0, 0, 0, 1, "DRAP-1", 36, "below 30C", 10, "panadole|paracetamol"),
            ("P2", "Augmentin", "Amoxicillin + Clavulanic Acid", "625mg", "tablet",
             "6 tablets", 6, "GSK Pakistan", "Medicines", "Antibiotics", "antibiotic",
             780.0, 780.0, 0.0, 1, 0, 0, 1, "DRAP-2", 24, "below 30C", 5,
             "agmentin|augmenten"),
            ("P3", "Nan Pro 1", "Infant Formula", "-", "powder", "400g tin", 1,
             "Nestle Pakistan", "Baby & Mother Care", "Infant Formula", "formula",
             2680.0, 2680.0, 0.0, 0, 0, 0, 1, "DRAP-3", 24, "below 30C", 10, "nan pro"),
            ("P4", "Lactogen 1", "Infant Formula", "-", "powder", "400g tin", 1,
             "Nestle Pakistan", "Baby & Mother Care", "Infant Formula", "formula",
             1890.0, 1890.0, 0.0, 0, 0, 0, 1, "DRAP-4", 24, "below 30C", 10, "lactogen"),
            ("P5", "Lantus SoloStar", "Insulin Glargine", "100 IU/ml", "injection",
             "1 pen", 1, "Sanofi", "Medicines", "Diabetes", "insulin", 4350.0, 4350.0,
             0.0, 1, 0, 1, 1, "DRAP-5", 12, "2-8C", 5, "lantus|insulin"),
            ("P6", "Xanax", "Alprazolam", "0.25mg", "tablet", "30 tablets", 30,
             "Pfizer", "Medicines", "Neuro & Psych", "benzodiazepine", 480.0, 480.0,
             0.0, 1, 1, 0, 1, "DRAP-6", 36, "below 30C", 2, "xanax"),
            ("P7", "Delisted Syrup", "Nothing", "-", "syrup", "60ml", 1, "X",
             "Medicines", "Cough & Cold", "cough syrup", 100.0, 100.0, 0.0, 0, 0, 0,
             0, "DRAP-7", 24, "below 30C", 10, "delisted"),
        ],
    )
    connection.executemany(
        "INSERT INTO substitutes VALUES (?,?,?,?,?)",
        [("P3", "P4", "same_class", -790.0, "Confirm with the paediatrician")],
    )
    connection.executemany(
        """INSERT INTO inventory (branch_id, sku, qty_on_hand, qty_reserved,
               reorder_level, batch_no, expiry_date, last_restocked_on, shelf_location)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            ("B1", "P1", 340, 12, 10, "B1", "2027-01-01", "2026-08-01", "A1"),
            ("B2", "P1", 40, 0, 10, "B2", "2027-01-01", "2026-08-01", "A1"),
            ("B1", "P2", 0, 0, 10, "B3", "2027-01-01", "2026-08-01", "A1"),
            ("B2", "P2", 24, 2, 10, "B4", "2027-01-01", "2026-08-01", "A1"),
            ("B1", "P3", 0, 0, 10, "B5", "2027-01-01", "2026-08-01", "A1"),
            ("B2", "P3", 0, 0, 10, "B6", "2027-01-01", "2026-08-01", "A1"),
            ("B3", "P3", 90, 0, 10, "B7", "2027-01-01", "2026-08-01", "A1"),
            ("B1", "P4", 30, 0, 10, "B8", "2027-01-01", "2026-08-01", "A1"),
            ("B1", "P5", 6, 0, 5, "B9", "2027-01-01", "2026-08-01", "A1"),
            # Everything on the shelf is already promised to someone else.
            ("B1", "P6", 5, 5, 2, "B10", "2027-01-01", "2026-08-01", "A1"),
            ("B4", "P1", 999, 0, 10, "B11", "2027-01-01", "2026-08-01", "A1"),
        ],
    )
    connection.executemany(
        """INSERT INTO customers (customer_id, full_name, full_name_ur, phone, city,
               preferred_language, loyalty_tier, loyalty_points, wallet_balance_pkr,
               is_chronic_patient, chronic_conditions, allergies, registered_on,
               total_orders, lifetime_value_pkr, is_blocked, do_not_call)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("C1", "Ayesha Khan", "عائشہ خان", AYESHA, "Karachi", "urdu", "gold", 4820,
             1250.0, 1, "type 2 diabetes", "penicillin", "2024-01-01", 47, 186400.0, 0, 0),
            ("C2", "Muhammad Bilal", "محمد بلال", BILAL, "Lahore", "mixed", "silver",
             1340, 0.0, 0, None, None, "2024-06-01", 18, 52300.0, 0, 0),
            ("C3", "Blocked Person", "بلاک", "03001110000", "Karachi", "urdu", "bronze",
             0, 0.0, 0, None, None, "2025-01-01", 1, 100.0, 1, 0),
        ],
    )
    connection.executemany(
        """INSERT INTO addresses (address_id, customer_id, label, address_line, area,
               city, nearest_landmark, latitude, longitude, is_default, delivery_note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("A1", "C1", "home", "House 42", "Bahadurabad", "Karachi", "roundabout",
             24.8755, 67.0710, 1, None),
            ("A2", "C2", "home", "House 118", "Gulberg", "Lahore", "Liberty",
             31.5105, 74.3455, 1, None),
        ],
    )
    connection.executemany(
        """INSERT INTO prescriptions (prescription_id, customer_id, uploaded_at,
               doctor_name, hospital, issued_on, valid_until, status, rejection_reason)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            ("RX1", "C2", "2026-08-10T10:00:00", "Dr. Imtiaz Bhatti", "Services",
             "2026-08-08", "2026-11-06", "pending_review", None),
            ("RX2", "C1", "2026-07-01T10:00:00", "Dr. Salman Ahmed", "AKUH",
             "2026-06-01", "2026-08-30", "rejected", "Doctor stamp missing"),
        ],
    )
    connection.executemany(
        """INSERT INTO orders (order_id, customer_id, branch_id, address_id,
               prescription_id, placed_at, channel, fulfilment_type, status,
               payment_method, payment_status, subtotal_pkr, discount_pkr,
               delivery_fee_pkr, total_pkr, promo_code, promised_by, delivered_at,
               cancelled_reason, requires_cold_chain, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("DV-1", "C1", "B1", "A1", None, "2026-08-10T11:20:00", "app", "express_1hr",
             "out_for_delivery", "cod", "pending", 1855.0, 0.0, 149.0, 2004.0, None,
             "2026-08-10T12:20:00", None, None, 0, None),
            ("DV-2", "C1", "B1", "A1", None, "2026-08-04T09:00:00", "app", "express_1hr",
             "cancelled", "card_online", "refunded", 2680.0, 0.0, 149.0, 2829.0, None,
             None, None, "Item out of stock after the order was placed", 0, None),
            ("DV-3", "C2", "B3", "A2", "RX1", "2026-08-10T10:15:00", "web", "same_day",
             "awaiting_prescription", "card_online", "paid", 1360.0, 0.0, 99.0, 1459.0,
             None, None, None, None, 0, None),
        ],
    )
    connection.executemany(
        """INSERT INTO order_items (order_item_id, order_id, sku, quantity,
               unit_price_pkr, line_total_pkr, substituted_from, item_status)
           VALUES (?,?,?,?,?,?,?,?)""",
        [
            ("DV-1-1", "DV-1", "P1", 2, 65.0, 130.0, None, "fulfilled"),
            ("DV-1-2", "DV-1", "P4", 1, 1890.0, 1890.0, None, "out_of_stock"),
            ("DV-3-1", "DV-3", "P1", 1, 65.0, 65.0, None, "fulfilled"),
        ],
    )
    connection.executemany(
        """INSERT INTO deliveries (delivery_id, order_id, rider_id, courier,
               tracking_number, status, dispatched_at, eta, delivered_at,
               attempt_count, failure_reason, delivery_otp, cold_box_used, proof_ref)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [("DLV-1", "DV-1", None, "DVAGO Rider", None, "out_for_delivery",
          "2026-08-10T11:52:00", "2026-08-10T12:20:00", None, 0, None, "7391", 0, None)],
    )
    connection.executemany(
        """INSERT INTO complaints (complaint_id, customer_id, order_id, category,
               summary, summary_ur, channel, priority, status, raised_at, sla_due_at,
               assigned_to, resolved_at, resolution, csat_score)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [("CMP-1", "C1", "DV-2", "refund_delay", "Refund not received",
          "refund نہیں آیا", "phone", "medium", "in_progress", "2026-08-07T11:00:00",
          "2026-08-12T11:00:00", "Finance - Refunds", None, None, None)],
    )
    connection.executemany(
        """INSERT INTO refunds (refund_id, order_id, complaint_id, amount_pkr, method,
               status, initiated_at, completed_at, expected_by, reference_no, reason)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [("REF-1", "DV-2", "CMP-1", 2829.0, "original_payment", "processing",
          "2026-08-05T09:00:00", None, "2026-08-12", "RRN-1", "Cancelled by branch")],
    )
    connection.executemany(
        """INSERT INTO promotions (promo_code, title, description, discount_type,
               discount_value, min_order_pkr, max_discount_pkr, valid_from, valid_to,
               applies_to_category, bank_partner, first_order_only, usage_limit,
               times_used, is_active)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("DVAGO10", "10% off", "10% off medicines", "percent", 10.0, 1000.0, 500.0,
             "2026-01-01", "2099-01-01", "Medicines", None, 0, None, 10, 1),
            ("HBL20", "20% off", "20% off with HBL cards", "percent", 20.0, 2500.0,
             1000.0, "2026-01-01", "2026-07-27", None, "HBL", 0, 50000, 49988, 0),
            ("FIRST15", "First order", "15% off the first order", "percent", 15.0,
             1500.0, 750.0, "2026-01-01", "2099-01-01", None, None, 1, None, 5, 1),
            ("MAXED", "Used up", "Finished promotion", "flat", 200.0, 0.0, None,
             "2026-01-01", "2099-01-01", None, None, 0, 10, 10, 1),
        ],
    )
    connection.executemany(
        """INSERT INTO refill_subscriptions (subscription_id, customer_id, sku,
               quantity, frequency_days, next_refill_on, last_ordered_on, status,
               reminder_channel, created_on)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [("SUB-1", "C1", "P1", 3, 30, "2020-01-01", "2019-12-01", "active",
          "whatsapp", "2019-01-01")],
    )
    connection.commit()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    path = tmp_path / "dvago.db"
    connection = sqlite3.connect(path)
    try:
        _seed(connection)
    finally:
        connection.close()
    database = Database(path, workers=2)
    yield database
    database.close()


@pytest.fixture
def ayesha(db: Database) -> DvagoBackend:
    return DvagoBackend(db, caller_phone=AYESHA, latency_s=0.0)


@pytest.fixture
def bilal(db: Database) -> DvagoBackend:
    return DvagoBackend(db, caller_phone=BILAL, latency_s=0.0)


@pytest.fixture
def stranger(db: Database) -> DvagoBackend:
    return DvagoBackend(db, caller_phone=STRANGER, latency_s=0.0)


# ── identity is bound, not passed ────────────────────────────────────────


async def test_no_tool_accepts_a_phone_number(ayesha: DvagoBackend) -> None:
    """The whole privacy design rests on this. A phone argument would undo it."""
    for definition in build_dvago_registry(ayesha).definitions():
        properties = definition.input_schema.get("properties", {})
        assert not any("phone" in name.lower() for name in properties), definition.name


async def test_another_callers_order_is_refused(bilal: DvagoBackend) -> None:
    result = await bilal.get_order_status("DV-1")
    assert "not on this caller's account" in result
    # Nothing about the order leaks, not even that it is out for delivery.
    assert "out for delivery" not in result
    assert "1855" not in result


async def test_another_callers_complaint_is_refused(bilal: DvagoBackend) -> None:
    assert "not on this caller's account" in await bilal.get_complaint_status("CMP-1")


async def test_unknown_caller_is_not_guessed_at(stranger: DvagoBackend) -> None:
    assert "not registered" in await stranger.get_customer_profile()


async def test_unknown_caller_cannot_file_a_complaint(stranger: DvagoBackend) -> None:
    assert "Cannot file" in await stranger.file_complaint("other", "test")


async def test_blocked_account_is_flagged(db: Database) -> None:
    backend = DvagoBackend(db, caller_phone="03001110000", latency_s=0.0)
    result = await backend.get_customer_profile()
    assert "suspended" in result
    assert "supervisor" in result


# ── allergies are checked in the query ───────────────────────────────────


async def test_allergy_warning_leads_the_result(ayesha: DvagoBackend) -> None:
    result = await ayesha.find_medicine("augmentin")
    assert result.startswith("⚠ SAFETY")
    assert "penicillin" in result
    assert "do not take an order" in result


async def test_allergy_warning_also_fires_on_a_stock_check(ayesha: DvagoBackend) -> None:
    assert (await ayesha.check_stock("augmentin")).startswith("⚠ SAFETY")


async def test_no_warning_for_an_unrelated_medicine(ayesha: DvagoBackend) -> None:
    assert not (await ayesha.find_medicine("panadol")).startswith("⚠")


async def test_no_warning_for_a_caller_with_no_allergies(bilal: DvagoBackend) -> None:
    assert not (await bilal.find_medicine("augmentin")).startswith("⚠")


# ── availability means available ─────────────────────────────────────────


async def test_reserved_stock_is_not_offered(ayesha: DvagoBackend) -> None:
    """Five on the shelf, five already promised. That is zero to this caller."""
    result = await ayesha.check_stock("xanax")
    assert "out of stock" in result


async def test_closed_branches_are_not_offered(ayesha: DvagoBackend) -> None:
    result = await ayesha.check_stock("panadol")
    assert "DVAGO Closed" not in result


async def test_out_of_stock_offers_substitutes(ayesha: DvagoBackend) -> None:
    result = await ayesha.check_stock("nan pro")
    assert "out of stock" in result
    assert "Lactogen 1" in result
    assert "Confirm with the paediatrician" in result
    assert "Do not tell the caller to switch medicine" in result


async def test_stock_in_another_city_says_courier(ayesha: DvagoBackend) -> None:
    """Nan Pro is in Lahore only. Promising same-day from there is a broken promise."""
    result = await ayesha.check_stock("nan pro")
    assert "another city" in result
    assert "two to three working days" in result


async def test_nearest_branch_comes_first(ayesha: DvagoBackend) -> None:
    result = await ayesha.check_stock("panadol")
    assert result.index("Bahadurabad") < result.index("Clifton")


# ── catalogue matching ───────────────────────────────────────────────────


@pytest.mark.parametrize("spoken", ["panadol", "Panadol", "panadole", "paracetamol",
                                    "PANADOL 500", "panado"])
async def test_misheard_names_still_match(ayesha: DvagoBackend, spoken: str) -> None:
    assert "Panadol" in await ayesha.find_medicine(spoken)


async def test_unknown_product_asks_rather_than_inventing(ayesha: DvagoBackend) -> None:
    result = await ayesha.find_medicine("zzzznotreal")
    assert "No product matching" in result
    assert "spell" in result


async def test_delisted_products_are_not_offered(ayesha: DvagoBackend) -> None:
    assert "No product matching" in await ayesha.find_medicine("delisted")


async def test_prescription_requirement_is_stated(bilal: DvagoBackend) -> None:
    assert "PRESCRIPTION REQUIRED" in await bilal.find_medicine("augmentin")


async def test_controlled_items_state_their_cap(bilal: DvagoBackend) -> None:
    result = await bilal.find_medicine("xanax")
    assert "controlled" in result
    assert "maximum 2 per order" in result


async def test_cold_chain_is_flagged(bilal: DvagoBackend) -> None:
    assert "cold-chain" in await bilal.find_medicine("lantus")


async def test_dash_strength_is_not_read_out(ayesha: DvagoBackend) -> None:
    """Non-drug lines carry '-' as strength; 'Nan Pro 1 -' sounds like a stumble."""
    assert "Nan Pro 1 -" not in await ayesha.check_stock("nan pro")


# ── orders ───────────────────────────────────────────────────────────────


async def test_latest_order_when_no_number_given(ayesha: DvagoBackend) -> None:
    result = await ayesha.get_order_status()
    assert "DV-1" in result
    assert "out for delivery" in result


async def test_dropped_items_are_volunteered(ayesha: DvagoBackend) -> None:
    result = await ayesha.get_order_status("DV-1")
    assert "NOT included" in result
    assert "Lactogen 1" in result


async def test_the_otp_is_never_returned(ayesha: DvagoBackend) -> None:
    """It is in the row. It is not ours to read out."""
    assert "7391" not in await ayesha.get_order_status("DV-1")


async def test_pending_prescription_explains_itself(bilal: DvagoBackend) -> None:
    result = await bilal.get_order_status()
    assert "pharmacist for review" in result
    assert "Dr. Imtiaz Bhatti" in result


async def test_refund_state_comes_from_the_record(ayesha: DvagoBackend) -> None:
    result = await ayesha.get_order_status("DV-2")
    assert "REF-1" in result
    assert "2026-08-12" in result
    assert "processing" in result


async def test_unknown_order_number_asks_again(ayesha: DvagoBackend) -> None:
    assert "No order matching" in await ayesha.get_order_status("DV-999")


async def test_order_lookup_tolerates_spacing(ayesha: DvagoBackend) -> None:
    assert "out for delivery" in await ayesha.get_order_status(" dv-1 ")


# ── branches ─────────────────────────────────────────────────────────────


async def test_nearest_branches_from_the_saved_address(ayesha: DvagoBackend) -> None:
    result = await ayesha.find_branch()
    assert "DVAGO Bahadurabad" in result
    assert "open 24 hours" in result
    assert "021-111-38246" in result


async def test_courier_only_city_is_not_promised_an_hour(ayesha: DvagoBackend) -> None:
    result = await ayesha.find_branch(city="Peshawar")
    assert "No DVAGO store" in result
    assert "two to three working days" in result


async def test_closed_branch_is_not_listed(ayesha: DvagoBackend) -> None:
    assert "DVAGO Closed" not in await ayesha.find_branch(city="Karachi")


# ── complaints and promotions ────────────────────────────────────────────


async def test_existing_complaint_is_found_before_filing_a_new_one(
    ayesha: DvagoBackend,
) -> None:
    result = await ayesha.get_complaint_status()
    assert "CMP-1" in result
    assert "in progress" in result


async def test_filing_a_complaint_writes_a_row(ayesha: DvagoBackend, db: Database) -> None:
    result = await ayesha.file_complaint("late_delivery", "Order abhi tak nahi aaya", "DV-1")
    assert "priority high" in result
    assert "24 hours" in result

    row = await db.fetch_one(
        "SELECT * FROM complaints WHERE channel = 'voice_agent'"
    )
    assert row is not None
    assert row["customer_id"] == "C1"
    assert row["order_id"] == "DV-1"
    assert row["status"] == "open"
    assert row["sla_due_at"] > row["raised_at"]


async def test_an_invented_category_falls_back_rather_than_failing(
    ayesha: DvagoBackend, db: Database
) -> None:
    """The model will invent a category eventually. A CHECK violation mid-call is worse."""
    await ayesha.file_complaint("the rider was rude to my mother", "summary")
    row = await db.fetch_one("SELECT category FROM complaints WHERE channel = 'voice_agent'")
    assert row is not None
    assert row["category"] == "other"


async def test_a_complaint_cannot_be_attached_to_someone_elses_order(
    bilal: DvagoBackend, db: Database
) -> None:
    await bilal.file_complaint("late_delivery", "summary", "DV-1")
    row = await db.fetch_one("SELECT order_id FROM complaints WHERE channel = 'voice_agent'")
    assert row is not None
    assert row["order_id"] is None


async def test_expired_promotion_is_refused(ayesha: DvagoBackend) -> None:
    result = await ayesha.check_promotion("hbl20")
    assert "expired" in result
    assert "cannot be applied" in result


async def test_exhausted_promotion_is_refused(ayesha: DvagoBackend) -> None:
    assert "usage limit" in await ayesha.check_promotion("MAXED")


async def test_first_order_promo_checks_the_caller(ayesha: DvagoBackend) -> None:
    result = await ayesha.check_promotion("FIRST15")
    assert "has ordered before" in result


async def test_unknown_promo_asks_rather_than_guessing(ayesha: DvagoBackend) -> None:
    assert "No promotion" in await ayesha.check_promotion("NONSENSE")


# ── profile ──────────────────────────────────────────────────────────────


async def test_profile_states_the_allergy_loudly(ayesha: DvagoBackend) -> None:
    result = await ayesha.get_customer_profile()
    assert "ALLERGIC TO PENICILLIN" in result
    assert "عائشہ خان" in result
    assert "gold tier" in result


async def test_overdue_refill_is_surfaced_but_told_to_wait(ayesha: DvagoBackend) -> None:
    result = await ayesha.get_customer_profile()
    assert "Refill due" in result
    assert "do not open with a sale" in result


# ── the registry ─────────────────────────────────────────────────────────


async def test_registry_shape(ayesha: DvagoBackend) -> None:
    registry = build_dvago_registry(ayesha)
    names = [d.name for d in registry.definitions()]
    assert names == sorted(names), "tool order must be stable or the prompt cache misses"
    assert len(names) == 8


async def test_registry_dispatch_and_errors(ayesha: DvagoBackend) -> None:
    good = await build_dvago_registry(ayesha).call("check_stock", {"medicine": "panadol"})
    assert not good.is_error

    bad = await build_dvago_registry(ayesha).call("find_medicine", {"wrong": "x"})
    assert bad.is_error
    assert "Invalid arguments" in bad.content


async def test_every_tool_survives_empty_arguments(ayesha: DvagoBackend) -> None:
    """A model that calls a tool with nothing must get a sentence, not a traceback."""
    registry = build_dvago_registry(ayesha)
    for definition in registry.definitions():
        if definition.input_schema.get("required"):
            continue
        result = await registry.call(definition.name, {})
        assert result.content
