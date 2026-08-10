"""Generate the DVAGO demo dataset as an Excel workbook — one sheet per table.

    uv run python scripts/generate_dvago_dataset.py [--as-of YYYY-MM-DD] [--out PATH]

SYNTHETIC DATA about a real company. Nothing here came from DVAGO. The brands,
manufacturers, cities and areas are real so the demo is recognisable; every
customer, order, complaint, refund and stock figure is invented.

Two properties matter more than volume:

**Deterministic given a date.** The seed is fixed, so the same ``--as-of``
always produces byte-identical content. Rerun it tomorrow and every row is the
same except the dates, which shift so the demo never goes stale. A demo that
returns different data each run cannot be rehearsed, and an unrehearsed demo is
where the surprises happen.

**Anchored.** Roughly a dozen records are hand-written rather than generated,
with memorable identifiers and guaranteed properties, so specific calls can be
practised to the word. The generated bulk exists so those anchors sit in a
realistic population rather than in an empty table — a stock query that returns
the only two rows in the database proves nothing.

The anchors, and what each one exists to demonstrate, are in
``docs/14-DVAGO-DEMO-DATA.md``.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dvago_catalogue import (  # noqa: E402
    BRANCHES,
    COURIER_ONLY_CITIES,
    COURIERS,
    DOCTORS,
    FIRST_NAMES_F,
    FIRST_NAMES_M,
    LAST_NAMES,
    MOBILE_PREFIXES,
    PRODUCTS,
)

from agent.data.schema_meta import load_schema  # noqa: E402

SEED = 20260810
DEFAULT_OUT = Path("data/dvago/DVAGO_demo_dataset.xlsx")

# City codes for branch ids. Three letters, IATA-ish, because a branch id a
# human can read out on the phone is worth more than a tidy integer.
CITY_CODE = {
    "Karachi": "KHI", "Lahore": "LHE", "Hyderabad": "HYD", "Multan": "MUX",
    "Bahawalpur": "BHV", "Kasur": "KSR", "Sahiwal": "SWL", "Burewala": "BWL",
}

FREE_DELIVERY_THRESHOLD_PKR = 5000.0
EXPRESS_FEE_PKR = 149.0
STANDARD_FEE_PKR = 99.0
COURIER_FEE_PKR = 249.0


# ── Anchors ──────────────────────────────────────────────────────────────
# Hand-written so a demo can be rehearsed line by line. Every one exists to
# make a specific thing happen on a call; see the docs for the script.

ANCHOR_CUSTOMERS = [
    # The main demo caller. Gold tier, diabetic, and allergic to penicillin —
    # which makes Augmentin the one thing the agent must refuse to recommend.
    dict(
        customer_id="CUS-000001", full_name="Ayesha Khan", full_name_ur="عائشہ خان",
        phone="03001234567", alt_phone="02134567890", email="ayesha.khan@example.pk",
        city="Karachi", date_of_birth="1986-04-12", gender="female",
        preferred_language="urdu", loyalty_tier="gold", loyalty_points=4820,
        wallet_balance_pkr=1250.0, is_chronic_patient=1,
        chronic_conditions="type 2 diabetes,hypertension",
        allergies="penicillin", total_orders=47, lifetime_value_pkr=186400.0,
    ),
    # Prescription flow: his order is stuck awaiting pharmacist review.
    dict(
        customer_id="CUS-000002", full_name="Muhammad Bilal", full_name_ur="محمد بلال",
        phone="03219876543", alt_phone=None, email="m.bilal@example.pk",
        city="Lahore", date_of_birth="1979-11-03", gender="male",
        preferred_language="mixed", loyalty_tier="silver", loyalty_points=1340,
        wallet_balance_pkr=0.0, is_chronic_patient=1, chronic_conditions="hypothyroidism",
        allergies=None, total_orders=18, lifetime_value_pkr=52300.0,
    ),
    # Refund flow: cancelled order, refund still processing.
    dict(
        customer_id="CUS-000003", full_name="Fatima Siddiqui", full_name_ur="فاطمہ صدیقی",
        phone="03331122334", alt_phone=None, email=None,
        city="Karachi", date_of_birth="1994-07-22", gender="female",
        preferred_language="urdu", loyalty_tier="bronze", loyalty_points=180,
        wallet_balance_pkr=0.0, is_chronic_patient=0, chronic_conditions=None,
        allergies="sulfa drugs", total_orders=3, lifetime_value_pkr=6800.0,
    ),
    # Courier-only city. Anyone promising this caller one-hour delivery is wrong.
    dict(
        customer_id="CUS-000004", full_name="Owais Rehman", full_name_ur="اویس رحمان",
        phone="03451239876", alt_phone=None, email="owais.r@example.pk",
        city="Peshawar", date_of_birth="1990-01-30", gender="male",
        preferred_language="mixed", loyalty_tier="bronze", loyalty_points=60,
        wallet_balance_pkr=0.0, is_chronic_patient=0, chronic_conditions=None,
        allergies=None, total_orders=2, lifetime_value_pkr=4100.0,
    ),
    # Unknown-caller path is exercised by 03009999999, which is deliberately
    # absent from this table. The agent must ask rather than assume.
]

ANCHOR_ADDRESSES = [
    dict(address_id="ADR-000001", customer_id="CUS-000001", label="home",
         address_line="House 42, Street 7, Block 3", area="Bahadurabad", city="Karachi",
         nearest_landmark="Near Dhoraji Colony roundabout", latitude=24.8755,
         longitude=67.0710, is_default=1, delivery_note="Call on arrival, first floor"),
    dict(address_id="ADR-000002", customer_id="CUS-000001", label="office",
         address_line="4th Floor, Business Avenue", area="Shahra-e-Faisal", city="Karachi",
         nearest_landmark="Opposite Awami Markaz", latitude=24.8648,
         longitude=67.0805, is_default=0, delivery_note="Reception, ask for Ayesha"),
    dict(address_id="ADR-000003", customer_id="CUS-000002", label="home",
         address_line="House 118-A, Block G", area="Johar Town", city="Lahore",
         nearest_landmark="Near Emporium Mall", latitude=31.4655, longitude=74.2712,
         is_default=1, delivery_note=None),
    dict(address_id="ADR-000004", customer_id="CUS-000003", label="home",
         address_line="Flat 302, Rose Apartments", area="Gulshan-e-Iqbal", city="Karachi",
         nearest_landmark="Near Bait-ul-Mukarram Masjid", latitude=24.9210,
         longitude=67.0895, is_default=1, delivery_note="Ring bell twice"),
    dict(address_id="ADR-000005", customer_id="CUS-000004", label="home",
         address_line="House 9, Sector B, Hayatabad Phase 3", area="Hayatabad",
         city="Peshawar", nearest_landmark="Near Phase 3 Chowk", latitude=33.9950,
         longitude=71.4400, is_default=1, delivery_note=None),
]

# Stock anchors: (brand, branch area, qty_on_hand, qty_reserved).
# These override whatever the generator would otherwise have produced.
#
# Augmentin is the demo that matters: out of stock at the caller's own branch,
# in stock a mile away. That is the answer a real pharmacy call turns on, and
# the one a naive bot gets wrong by saying only "no".
ANCHOR_STOCK: list[tuple[str, str, int, int]] = [
    ("Augmentin", "Bahadurabad", 0, 0),
    ("Augmentin", "Clifton", 24, 2),
    ("Augmentin", "Gulshan-e-Iqbal", 11, 0),
    ("Panadol", "Bahadurabad", 340, 12),        # always available, the easy yes
    ("Ventolin Inhaler", "Bahadurabad", 2, 1),  # one left after reservations
    ("Lantus SoloStar", "Bahadurabad", 6, 0),   # cold chain, stocked here
    ("Lantus SoloStar", "Tariq Road", 0, 0),    # no cold chain at this branch
    ("Glucophage", "Bahadurabad", 88, 4),
]

# Genuinely unavailable across a whole city, which is the only way to reach the
# substitute path. Zeroing one branch is not enough: the caller's second-nearest
# store is a kilometre away and the agent will quite correctly offer it.
ANCHOR_OUT_OF_STOCK_CITY: list[tuple[str, str]] = [
    ("Nan Pro 1", "Karachi"),  # infant formula -> Lactogen / S-26, with a caveat
]


def anchor_orders(as_of: date) -> list[dict[str, Any]]:
    """Orders whose dates track the run date so the demo never goes stale."""
    today = as_of
    return [
        # Live delivery. "میرا آرڈر کہاں ہے" — rider assigned, ETA within the hour.
        dict(
            order_id="DV-2026-104471", customer_id="CUS-000001", branch_id="DVG-KHI-001",
            address_id="ADR-000001", prescription_id=None,
            placed_at=f"{today}T11:20:00", channel="app", fulfilment_type="express_1hr",
            status="out_for_delivery", payment_method="cod", payment_status="pending",
            subtotal_pkr=1855.0, discount_pkr=185.5, delivery_fee_pkr=149.0,
            total_pkr=1818.5, promo_code="DVAGO10", promised_by=f"{today}T12:20:00",
            delivered_at=None, cancelled_reason=None, requires_cold_chain=0,
            notes="Express window",
            _items=[("Panadol", 2), ("Softin", 1), ("Surbex Z", 1), ("Peditral", 2)],
        ),
        # Delivered two days ago, and late — the complaint below hangs off it.
        dict(
            order_id="DV-2026-103980", customer_id="CUS-000001", branch_id="DVG-KHI-001",
            address_id="ADR-000001", prescription_id="RX-2026-00204",
            placed_at=f"{today - timedelta(days=3)}T09:05:00", channel="app",
            fulfilment_type="same_day", status="delivered", payment_method="card_online",
            payment_status="paid", subtotal_pkr=4795.0, discount_pkr=479.5,
            delivery_fee_pkr=99.0, total_pkr=4414.5, promo_code="DVAGO10",
            promised_by=f"{today - timedelta(days=3)}T18:00:00",
            delivered_at=f"{today - timedelta(days=2)}T14:30:00", cancelled_reason=None,
            requires_cold_chain=1, notes="Delivered late, cold box used",
            _items=[("Glucophage", 3), ("Lantus SoloStar", 1)],
        ),
        # Blocked on prescription review. The agent must explain *why*, not
        # just report a status code.
        dict(
            order_id="DV-2026-104502", customer_id="CUS-000002", branch_id="DVG-LHE-003",
            address_id="ADR-000003", prescription_id="RX-2026-00318",
            placed_at=f"{today}T10:15:00", channel="web", fulfilment_type="same_day",
            status="awaiting_prescription", payment_method="card_online",
            payment_status="paid", subtotal_pkr=1360.0, discount_pkr=0.0,
            delivery_fee_pkr=99.0, total_pkr=1459.0, promo_code=None,
            promised_by=f"{today}T20:00:00", delivered_at=None, cancelled_reason=None,
            requires_cold_chain=0, notes="Awaiting pharmacist verification",
            _items=[("Thyronorm", 2), ("CaC-1000 Plus", 1)],
        ),
        # Cancelled, refund in flight. Tests "where is my money".
        dict(
            order_id="DV-2026-104388", customer_id="CUS-000003", branch_id="DVG-KHI-005",
            address_id="ADR-000004", prescription_id=None,
            placed_at=f"{today - timedelta(days=6)}T16:40:00", channel="app",
            fulfilment_type="express_1hr", status="cancelled", payment_method="card_online",
            payment_status="refunded", subtotal_pkr=2680.0, discount_pkr=0.0,
            delivery_fee_pkr=149.0, total_pkr=2829.0, promo_code=None,
            promised_by=f"{today - timedelta(days=6)}T17:40:00", delivered_at=None,
            cancelled_reason="Item out of stock after order was placed",
            requires_cold_chain=0, notes="Cancelled by branch",
            _items=[("Nan Pro 1", 1)],
        ),
        # Courier to a non-store city: two to three days, not one hour.
        dict(
            order_id="DV-2026-104455", customer_id="CUS-000004", branch_id="DVG-LHE-001",
            address_id="ADR-000005", prescription_id=None,
            placed_at=f"{today - timedelta(days=1)}T13:00:00", channel="app",
            fulfilment_type="courier", status="dispatched", payment_method="easypaisa",
            payment_status="paid", subtotal_pkr=6240.0, discount_pkr=0.0,
            delivery_fee_pkr=0.0, total_pkr=6240.0, promo_code=None,
            promised_by=f"{today + timedelta(days=2)}T18:00:00", delivered_at=None,
            cancelled_reason=None, requires_cold_chain=0,
            notes="Courier to Peshawar, free delivery above 5000",
            _items=[("Ensure Vanilla", 1), ("Surbex Z", 2), ("Omron HEM-7121", 0)],
        ),
    ]


def anchor_complaints(as_of: date) -> list[dict[str, Any]]:
    return [
        dict(
            complaint_id="CMP-2026-0042", customer_id="CUS-000001",
            order_id="DV-2026-103980", category="late_delivery",
            summary="Same-day order arrived a day late; insulin was in the box",
            summary_ur="آرڈر ایک دن لیٹ آیا، انسولین بھی اسی میں تھی",
            channel="phone", priority="high", status="in_progress",
            raised_at=f"{as_of - timedelta(days=2)}T15:10:00",
            sla_due_at=f"{as_of + timedelta(days=1)}T15:10:00",
            assigned_to="Karachi Ops - Tier 2", resolved_at=None, resolution=None,
            csat_score=None,
        ),
        dict(
            complaint_id="CMP-2026-0038", customer_id="CUS-000003",
            order_id="DV-2026-104388", category="refund_delay",
            summary="Refund for cancelled order not received after five days",
            summary_ur="کینسل آرڈر کا refund پانچ دن بعد بھی نہیں ملا",
            channel="app", priority="medium", status="awaiting_customer",
            raised_at=f"{as_of - timedelta(days=3)}T11:00:00",
            sla_due_at=f"{as_of + timedelta(days=2)}T11:00:00",
            assigned_to="Finance - Refunds", resolved_at=None,
            resolution=None, csat_score=None,
        ),
        dict(
            complaint_id="CMP-2026-0031", customer_id="CUS-000001",
            order_id=None, category="app_issue",
            summary="Could not upload prescription image from the app",
            summary_ur="ایپ سے prescription upload نہیں ہو رہی تھی",
            channel="whatsapp", priority="low", status="resolved",
            raised_at=f"{as_of - timedelta(days=21)}T19:25:00",
            sla_due_at=f"{as_of - timedelta(days=19)}T19:25:00",
            assigned_to="Tech Support", resolved_at=f"{as_of - timedelta(days=20)}T10:00:00",
            resolution="Guided customer through app update; upload succeeded", csat_score=4,
        ),
    ]


def anchor_prescriptions(as_of: date) -> list[dict[str, Any]]:
    return [
        dict(
            prescription_id="RX-2026-00318", customer_id="CUS-000002",
            uploaded_at=f"{as_of}T10:12:00", doctor_name="Dr. Imtiaz Bhatti",
            doctor_pmdc_no="PMDC-41822-P", hospital="Services Hospital Lahore",
            issued_on=str(as_of - timedelta(days=2)),
            valid_until=str(as_of + timedelta(days=88)), status="pending_review",
            verified_by=None, verified_at=None, rejection_reason=None,
            image_ref="rx/2026/08/00318.jpg",
        ),
        dict(
            prescription_id="RX-2026-00204", customer_id="CUS-000001",
            uploaded_at=f"{as_of - timedelta(days=3)}T08:50:00",
            doctor_name="Dr. Salman Ahmed", doctor_pmdc_no="PMDC-30914-K",
            hospital="Aga Khan University Hospital",
            issued_on=str(as_of - timedelta(days=30)),
            valid_until=str(as_of + timedelta(days=60)), status="verified",
            verified_by="PH-0001", verified_at=f"{as_of - timedelta(days=3)}T09:02:00",
            rejection_reason=None, image_ref="rx/2026/08/00204.jpg",
        ),
        dict(
            prescription_id="RX-2026-00155", customer_id="CUS-000003",
            uploaded_at=f"{as_of - timedelta(days=40)}T14:00:00",
            doctor_name="Dr. Nida Shah", doctor_pmdc_no="PMDC-52210-S",
            hospital="Indus Hospital", issued_on=str(as_of - timedelta(days=41)),
            valid_until=str(as_of - timedelta(days=10)), status="rejected",
            verified_by="PH-0002", verified_at=f"{as_of - timedelta(days=39)}T09:30:00",
            rejection_reason="Prescriber signature not legible; doctor stamp missing",
            image_ref="rx/2026/07/00155.jpg",
        ),
    ]


def anchor_refunds(as_of: date) -> list[dict[str, Any]]:
    return [
        dict(
            refund_id="REF-2026-0071", order_id="DV-2026-104388",
            complaint_id="CMP-2026-0038", amount_pkr=2829.0, method="original_payment",
            status="processing", initiated_at=f"{as_of - timedelta(days=5)}T09:00:00",
            completed_at=None, expected_by=str(as_of + timedelta(days=2)),
            reference_no="RRN-884213907", reason="Order cancelled by branch, item unavailable",
        ),
    ]


ANCHOR_PROMOTIONS = [
    dict(promo_code="DVAGO10", title="10% off medicines",
         description="10% off all medicines, capped at 500 rupees",
         discount_type="percent", discount_value=10.0, min_order_pkr=1000.0,
         max_discount_pkr=500.0, applies_to_category="Medicines", bank_partner=None,
         first_order_only=0, usage_limit=None, times_used=8412, is_active=1,
         _from_days=-120, _to_days=45),
    dict(promo_code="FIRST15", title="15% off your first order",
         description="15% off the first order on the app, capped at 750 rupees",
         discount_type="percent", discount_value=15.0, min_order_pkr=1500.0,
         max_discount_pkr=750.0, applies_to_category=None, bank_partner=None,
         first_order_only=1, usage_limit=None, times_used=21044, is_active=1,
         _from_days=-365, _to_days=180),
    dict(promo_code="HBL20", title="20% off with HBL cards",
         description="20% off with HBL credit and debit cards, capped at 1000 rupees",
         discount_type="percent", discount_value=20.0, min_order_pkr=2500.0,
         max_discount_pkr=1000.0, applies_to_category=None, bank_partner="HBL",
         first_order_only=0, usage_limit=50000, times_used=49988, is_active=0,
         _from_days=-200, _to_days=-14),  # expired: the agent must say no
    dict(promo_code="FREESHIP", title="Free delivery",
         description="Free delivery on orders above 3000 rupees",
         discount_type="free_delivery", discount_value=0.0, min_order_pkr=3000.0,
         max_discount_pkr=None, applies_to_category=None, bank_partner=None,
         first_order_only=0, usage_limit=None, times_used=15290, is_active=1,
         _from_days=-60, _to_days=30),
    dict(promo_code="BABY500", title="500 off baby care",
         description="500 rupees off baby and mother care above 4000 rupees",
         discount_type="flat", discount_value=500.0, min_order_pkr=4000.0,
         max_discount_pkr=None, applies_to_category="Baby & Mother Care",
         bank_partner=None, first_order_only=0, usage_limit=20000, times_used=6120,
         is_active=1, _from_days=-30, _to_days=60),
    dict(promo_code="MEEZAN15", title="15% off with Meezan cards",
         description="15% off with Meezan Bank cards, capped at 800 rupees",
         discount_type="percent", discount_value=15.0, min_order_pkr=2000.0,
         max_discount_pkr=800.0, applies_to_category=None, bank_partner="Meezan Bank",
         first_order_only=0, usage_limit=30000, times_used=11407, is_active=1,
         _from_days=-45, _to_days=75),
]

# Same-generic swaps, written by hand because getting one wrong is a clinical
# error, not a data-quality one.
SUBSTITUTE_PAIRS: list[tuple[str, str, str, str]] = [
    ("Nan Pro 1", "Lactogen 1", "same_class",
     "Both stage 1 infant formulas; confirm with the paediatrician before switching"),
    ("Nan Pro 1", "S-26 Gold Stage 1", "same_class",
     "Both stage 1 infant formulas; confirm with the paediatrician before switching"),
    ("Panadol", "Disprin", "same_class", "Different molecule; not suitable on an empty stomach"),
    ("Zyrtec", "Softin", "same_generic", "Identical cetirizine 10mg"),
    ("Softin", "Zyrtec", "same_generic", "Identical cetirizine 10mg"),
    ("Risek", "Nexum", "same_class", "Esomeprazole instead of omeprazole; same class"),
    ("Nexum", "Risek", "same_class", "Omeprazole instead of esomeprazole; same class"),
    ("Thyronorm", "Eltroxin", "different_strength",
     "Eltroxin here is 100mcg against Thyronorm 50mcg; dose must be confirmed"),
    ("Amoxil", "Velosef", "same_class", "Both beta-lactams; not safe if penicillin-allergic"),
    ("Brufen", "Synflex", "same_class", "Both NSAIDs; avoid together"),
    ("Glucophage", "Glucophage XR", "different_strength",
     "XR is 1000mg extended release against 500mg immediate release"),
    ("Lantus SoloStar", "NovoMix 30 FlexPen", "same_class",
     "Different insulin profile entirely; prescriber must approve"),
    ("Ensure Vanilla", "Glucerna", "same_class", "Glucerna is the diabetic formulation"),
    ("Pampers Baby Dry Large", "Canbebe Medium", "different_strength",
     "Different size; confirm the child's weight"),
    ("Ventolin Inhaler", "Ventolin Respirator Solution", "different_strength",
     "Solution needs a nebuliser; not interchangeable with an inhaler"),
]


# ── Generation ───────────────────────────────────────────────────────────


def build(as_of: date) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(SEED)
    sheets: dict[str, list[dict[str, Any]]] = {}

    branches = _branches(rng, as_of)
    sheets["branches"] = branches
    by_area = {b["area"]: b["branch_id"] for b in branches}

    sheets["pharmacists"] = _pharmacists(rng, branches)
    sheets["riders"] = _riders(rng, branches)

    products = _products(rng)
    sheets["products"] = products
    sku_of = {p["brand_name"]: p["sku"] for p in products}
    product_of = {p["sku"]: p for p in products}

    sheets["substitutes"] = _substitutes(sku_of, product_of)
    sheets["inventory"] = _inventory(rng, branches, products, by_area, sku_of, as_of)

    customers = _customers(rng, as_of)
    sheets["customers"] = customers
    sheets["addresses"] = _addresses(rng, customers, branches)

    sheets["prescriptions"] = _prescriptions(rng, customers, as_of)
    orders, items = _orders(rng, customers, sheets["addresses"], branches, products,
                            sku_of, product_of, sheets["prescriptions"], as_of)
    sheets["orders"] = orders
    sheets["order_items"] = items
    sheets["deliveries"] = _deliveries(rng, orders, sheets["riders"], as_of)

    complaints = _complaints(rng, orders, as_of)
    sheets["complaints"] = complaints
    sheets["refunds"] = _refunds(rng, orders, complaints, as_of)
    sheets["promotions"] = _promotions(as_of)
    sheets["refill_subscriptions"] = _refills(rng, customers, products, sku_of, as_of)

    sheets["dataset_meta"] = [
        {"key": "dataset", "value": "DVAGO voice-agent demo"},
        {"key": "generated_at", "value": datetime.now().isoformat(timespec="seconds")},
        {"key": "as_of", "value": str(as_of)},
        {"key": "seed", "value": str(SEED)},
        {"key": "synthetic", "value": "yes"},
        {"key": "notice", "value": "Generated sample data. Not DVAGO's real records."},
        {"key": "row_total", "value": str(sum(len(v) for v in sheets.values()))},
    ]
    return sheets


def _branches(rng: random.Random, as_of: date) -> list[dict[str, Any]]:
    rows = []
    counters: dict[str, int] = {}
    for city, area, name, landmark, is_24_7, cold, express, lat, lng in BRANCHES:
        code = CITY_CODE[city]
        counters[code] = counters.get(code, 0) + 1
        rows.append(dict(
            branch_id=f"DVG-{code}-{counters[code]:03d}", branch_name=name, city=city,
            area=area, address=f"{landmark}, {area}, {city}", landmark=landmark,
            phone=f"021-111-38246" if city == "Karachi" else f"042-111-38246",
            latitude=lat, longitude=lng,
            opens_at="00:00" if is_24_7 else "09:00",
            closes_at="23:59" if is_24_7 else rng.choice(["22:00", "23:00", "00:00"]),
            is_24_7=is_24_7, has_cold_chain=cold, has_pharmacist=1,
            supports_pickup=1, supports_express=express,
            delivery_radius_km=round(rng.uniform(4.0, 12.0), 1) if express else 3.5,
            opened_on=str(as_of - timedelta(days=rng.randint(200, 1600))),
            status="active",
        ))
    # One closed branch, so "is your X branch open" has a real negative answer.
    rows[-1]["status"] = "temporarily_closed"
    return rows


def _pharmacists(rng: random.Random, branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    specialities = ["diabetes care", "paediatrics", "cardiology", "general", "oncology support"]
    for i, branch in enumerate(branches[:20], start=1):
        first = rng.choice(FIRST_NAMES_M + FIRST_NAMES_F)[0]
        last = rng.choice(LAST_NAMES)[0]
        langs = "Urdu,English"
        if branch["city"] in {"Karachi", "Hyderabad"} and rng.random() < 0.4:
            langs += ",Sindhi"
        if branch["city"] in {"Lahore", "Kasur", "Sahiwal"} and rng.random() < 0.5:
            langs += ",Punjabi"
        rows.append(dict(
            pharmacist_id=f"PH-{i:04d}", full_name=f"{first} {last}",
            licence_no=f"PPC-{40000 + i * 37}", branch_id=branch["branch_id"],
            shift_start=rng.choice(["09:00", "10:00", "14:00"]),
            shift_end=rng.choice(["18:00", "20:00", "22:00"]),
            languages=langs, is_available=1 if rng.random() < 0.75 else 0,
            speciality=rng.choice(specialities),
        ))
    return rows


def _riders(rng: random.Random, branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    n = 0
    for branch in branches:
        for _ in range(2 if branch["supports_express"] else 1):
            n += 1
            first, _ = rng.choice(FIRST_NAMES_M)
            last, _ = rng.choice(LAST_NAMES)
            rows.append(dict(
                rider_id=f"RDR-{n:04d}", full_name=f"{first} {last}",
                phone=_mobile(rng), vehicle_type=rng.choices(
                    ["bike", "car", "van"], weights=[88, 8, 4])[0],
                vehicle_reg=f"K{rng.randint(1000, 9999)}-{rng.randint(10, 99)}",
                branch_id=branch["branch_id"],
                has_cold_box=1 if branch["has_cold_chain"] and rng.random() < 0.6 else 0,
                rating=round(rng.uniform(4.1, 5.0), 1),
                active_deliveries=rng.randint(0, 4),
                is_on_shift=1 if rng.random() < 0.8 else 0,
            ))
    # The anchored order's rider, named so the demo can be rehearsed.
    rows[0].update(full_name="Imran Shah", phone="03004455661", rider_id="RDR-0001",
                   has_cold_box=1, is_on_shift=1, active_deliveries=2, rating=4.8)
    return rows


def _products(rng: random.Random) -> list[dict[str, Any]]:
    rows = []
    for i, p in enumerate(PRODUCTS, start=1):
        (brand, generic, strength, form, pack, units, maker, cat, subcat,
         tclass, price, rx, controlled, cold, terms) = p
        # DVAGO discounts medicines harder than consumer goods, which is what
        # "up to 15% off medicines" means in practice.
        pct = rng.choice([0, 0, 5, 8, 10, 12, 15]) if cat == "Medicines" else \
            rng.choice([0, 0, 0, 3, 5, 7])
        mrp = round(price / (1 - pct / 100), 0) if pct else float(price)
        prefix = {"Medicines": "MED", "Nutrition & Supplements": "NUT",
                  "Baby & Mother Care": "BBY", "Devices & Support": "DEV",
                  "Personal Care": "PCR", "Beauty Care": "BTY",
                  "Foods & Beverages": "FNB"}[cat]
        rows.append(dict(
            sku=f"DV-{prefix}-{i:05d}", brand_name=brand, generic_name=generic,
            strength=strength, dosage_form=form, pack_size=pack, units_per_pack=units,
            manufacturer=maker, category=cat, subcategory=subcat,
            therapeutic_class=tclass, price_pkr=float(price), mrp_pkr=float(mrp),
            discount_pct=float(pct), requires_rx=rx, is_controlled=controlled,
            is_cold_chain=cold, is_active=1,
            drap_reg_no=f"DRAP-{10000 + i * 7:06d}",
            shelf_life_months=12 if cold else rng.choice([24, 24, 36, 36, 60]),
            storage_note="Store at 2-8°C. Do not freeze." if cold else
            "Store below 30°C, away from direct sunlight.",
            max_qty_per_order=2 if controlled else (5 if rx else 10),
            search_terms=terms,
        ))
    return rows


def _substitutes(
    sku_of: dict[str, str], product_of: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for brand, alt_brand, equivalence, note in SUBSTITUTE_PAIRS:
        sku, alt = sku_of[brand], sku_of[alt_brand]
        rows.append(dict(
            sku=sku, alternative_sku=alt, equivalence=equivalence,
            price_diff_pkr=round(product_of[alt]["price_pkr"] - product_of[sku]["price_pkr"], 2),
            pharmacist_note=note,
        ))
    return rows


def _inventory(
    rng: random.Random,
    branches: list[dict[str, Any]],
    products: list[dict[str, Any]],
    by_area: dict[str, str],
    sku_of: dict[str, str],
    as_of: date,
) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}

    for branch in branches:
        for product in products:
            # Cold-chain items only exist where there is a fridge. This is the
            # constraint that makes "available in Karachi" the wrong answer.
            if product["is_cold_chain"] and not branch["has_cold_chain"]:
                continue
            # Small branches carry the fast movers, not the long tail.
            breadth = 0.95 if branch["supports_express"] else 0.55
            if product["category"] != "Medicines":
                breadth -= 0.2
            if rng.random() > breadth:
                continue

            qty = rng.choices(
                [0, rng.randint(1, 4), rng.randint(5, 30), rng.randint(31, 200)],
                weights=[6, 14, 50, 30],
            )[0]
            reserved = rng.randint(0, min(3, qty)) if qty else 0
            months = product["shelf_life_months"]
            rows[(branch["branch_id"], product["sku"])] = dict(
                branch_id=branch["branch_id"], sku=product["sku"], qty_on_hand=qty,
                qty_reserved=reserved,
                reorder_level=rng.choice([5, 10, 20]),
                batch_no=f"B{rng.randint(100000, 999999)}",
                expiry_date=str(as_of + timedelta(days=rng.randint(60, int(months * 30)))),
                last_restocked_on=str(as_of - timedelta(days=rng.randint(0, 45))),
                shelf_location=f"A{rng.randint(1, 12)}-R{rng.randint(1, 6)}",
            )

    for brand, city in ANCHOR_OUT_OF_STOCK_CITY:
        sku = sku_of[brand]
        for branch in branches:
            if branch["city"] == city and (branch["branch_id"], sku) in rows:
                rows[(branch["branch_id"], sku)].update(qty_on_hand=0, qty_reserved=0)

    for brand, area, qty, reserved in ANCHOR_STOCK:
        key = (by_area[area], sku_of[brand])
        row = rows.get(key)
        if row is None:
            row = dict(
                branch_id=by_area[area], sku=sku_of[brand], reorder_level=10,
                batch_no=f"B{rng.randint(100000, 999999)}",
                expiry_date=str(as_of + timedelta(days=420)),
                last_restocked_on=str(as_of - timedelta(days=7)),
                shelf_location="A1-R1",
            )
            rows[key] = row
        row["qty_on_hand"] = qty
        row["qty_reserved"] = reserved

    # One near-expiry batch, so "how fresh is it" has an honest answer.
    first_key = next(iter(rows))
    rows[first_key]["expiry_date"] = str(as_of + timedelta(days=25))

    return list(rows.values())


def _customers(rng: random.Random, as_of: date) -> list[dict[str, Any]]:
    rows = [dict(a, registered_on=str(as_of - timedelta(days=rng.randint(400, 1500))),
                 is_blocked=0, do_not_call=0) for a in ANCHOR_CUSTOMERS]

    store_cities = [c for c in CITY_CODE]
    used_phones = {r["phone"] for r in rows}
    chronic_sets = ["type 2 diabetes", "hypertension", "hypothyroidism",
                    "asthma", "type 2 diabetes,hypertension", "high cholesterol"]
    allergy_sets = [None, None, None, None, "penicillin", "sulfa drugs",
                    "aspirin", "cephalosporins"]

    for i in range(5, 141):
        female = rng.random() < 0.52
        first, first_ur = rng.choice(FIRST_NAMES_F if female else FIRST_NAMES_M)
        last, last_ur = rng.choice(LAST_NAMES)
        phone = _mobile(rng)
        while phone in used_phones:
            phone = _mobile(rng)
        used_phones.add(phone)

        chronic = rng.random() < 0.28
        orders_n = rng.choices([0, 1, 2, 5, 12, 30], weights=[8, 18, 26, 26, 16, 6])[0]
        tier = ("platinum" if orders_n >= 30 else "gold" if orders_n >= 12
                else "silver" if orders_n >= 5 else "bronze")
        rows.append(dict(
            customer_id=f"CUS-{i:06d}", full_name=f"{first} {last}",
            full_name_ur=f"{first_ur} {last_ur}", phone=phone,
            alt_phone=_mobile(rng) if rng.random() < 0.15 else None,
            email=f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@example.pk"
            if rng.random() < 0.6 else None,
            city=rng.choices(store_cities + COURIER_ONLY_CITIES,
                             weights=[34, 20, 5, 5, 3, 2, 2, 2] + [2] * 12)[0],
            date_of_birth=str(date(rng.randint(1955, 2005), rng.randint(1, 12),
                                   rng.randint(1, 28))),
            gender="female" if female else "male",
            preferred_language=rng.choices(["urdu", "mixed", "english"],
                                           weights=[52, 40, 8])[0],
            loyalty_tier=tier, loyalty_points=orders_n * rng.randint(60, 140),
            wallet_balance_pkr=float(rng.choice([0, 0, 0, 250, 500, 1000])),
            is_chronic_patient=1 if chronic else 0,
            chronic_conditions=rng.choice(chronic_sets) if chronic else None,
            allergies=rng.choice(allergy_sets),
            registered_on=str(as_of - timedelta(days=rng.randint(10, 1500))),
            total_orders=orders_n,
            lifetime_value_pkr=round(orders_n * rng.uniform(1200, 5200), 2),
            is_blocked=1 if rng.random() < 0.015 else 0,
            do_not_call=1 if rng.random() < 0.06 else 0,
        ))
    return rows


def _addresses(
    rng: random.Random, customers: list[dict[str, Any]], branches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = [dict(a) for a in ANCHOR_ADDRESSES]
    areas_by_city: dict[str, list[tuple[str, float, float]]] = {}
    for b in branches:
        areas_by_city.setdefault(b["city"], []).append(
            (b["area"], b["latitude"], b["longitude"]))

    n = len(rows)
    labels = ["home", "home", "home", "office", "mother's house", "in-laws"]
    for customer in customers[len(ANCHOR_CUSTOMERS):]:
        for k in range(rng.choices([1, 2, 3], weights=[68, 26, 6])[0]):
            n += 1
            city = customer["city"]
            if city in areas_by_city:
                area, lat, lng = rng.choice(areas_by_city[city])
                lat = round(lat + rng.uniform(-0.02, 0.02), 6)
                lng = round(lng + rng.uniform(-0.02, 0.02), 6)
            else:
                area, lat, lng = f"{city} City", None, None
            rows.append(dict(
                address_id=f"ADR-{n:06d}", customer_id=customer["customer_id"],
                label=labels[k] if k < len(labels) else "other",
                address_line=f"House {rng.randint(1, 900)}, Street {rng.randint(1, 40)}",
                area=area, city=city,
                nearest_landmark=rng.choice(
                    ["Near the main market", "Opposite the masjid", "Near the park",
                     "Behind the petrol pump", "Next to the bakery"]),
                latitude=lat, longitude=lng, is_default=1 if k == 0 else 0,
                delivery_note=rng.choice(
                    [None, None, "Call on arrival", "Leave with the guard",
                     "Second floor, no lift"]),
            ))
    return rows


def _prescriptions(
    rng: random.Random, customers: list[dict[str, Any]], as_of: date
) -> list[dict[str, Any]]:
    rows = anchor_prescriptions(as_of)
    n = 400
    for customer in customers:
        if rng.random() > 0.35:
            continue
        n += 1
        doctor, hospital = rng.choice(DOCTORS)
        issued = as_of - timedelta(days=rng.randint(1, 180))
        status = rng.choices(
            ["verified", "pending_review", "rejected", "expired"],
            weights=[68, 10, 8, 14])[0]
        verified = status in {"verified", "rejected"}
        rows.append(dict(
            prescription_id=f"RX-2026-{n:05d}", customer_id=customer["customer_id"],
            uploaded_at=f"{issued + timedelta(days=rng.randint(0, 3))}T"
                        f"{rng.randint(9, 21):02d}:{rng.randint(0, 59):02d}:00",
            doctor_name=doctor, doctor_pmdc_no=f"PMDC-{rng.randint(10000, 99999)}-"
                                               f"{rng.choice('KPSB')}",
            hospital=hospital, issued_on=str(issued),
            valid_until=str(issued + timedelta(days=90)), status=status,
            verified_by=f"PH-{rng.randint(1, 20):04d}" if verified else None,
            verified_at=f"{issued + timedelta(days=1)}T10:00:00" if verified else None,
            rejection_reason="Prescription older than 90 days" if status == "rejected"
            else None,
            image_ref=f"rx/2026/{rng.randint(1, 8):02d}/{n:05d}.jpg",
        ))
    return rows


def _orders(
    rng: random.Random,
    customers: list[dict[str, Any]],
    addresses: list[dict[str, Any]],
    branches: list[dict[str, Any]],
    products: list[dict[str, Any]],
    sku_of: dict[str, str],
    product_of: dict[str, dict[str, Any]],
    prescriptions: list[dict[str, Any]],
    as_of: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    orders: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for anchor in anchor_orders(as_of):
        order = {k: v for k, v in anchor.items() if not k.startswith("_")}
        orders.append(order)
        for idx, (brand, qty) in enumerate(anchor["_items"], start=1):
            product = product_of[sku_of[brand]]
            # A zero quantity in the anchor means the line was cancelled out of
            # the order, which is how "one item is missing" gets demonstrated.
            items.append(dict(
                order_item_id=f"{order['order_id']}-{idx}", order_id=order["order_id"],
                sku=product["sku"], quantity=max(qty, 1),
                unit_price_pkr=product["price_pkr"],
                line_total_pkr=round(product["price_pkr"] * max(qty, 1), 2),
                substituted_from=None,
                item_status="out_of_stock" if qty == 0 else "fulfilled",
            ))

    addr_by_customer: dict[str, list[dict[str, Any]]] = {}
    for a in addresses:
        addr_by_customer.setdefault(a["customer_id"], []).append(a)
    branch_by_city: dict[str, list[dict[str, Any]]] = {}
    for b in branches:
        branch_by_city.setdefault(b["city"], []).append(b)
    rx_by_customer: dict[str, list[dict[str, Any]]] = {}
    for p in prescriptions:
        rx_by_customer.setdefault(p["customer_id"], []).append(p)

    sellable = [p for p in products if p["is_active"]]
    seq = 100000
    for customer in customers:
        for _ in range(min(customer["total_orders"], 8)):
            seq += 1
            order_id = f"DV-2026-{seq:06d}"
            placed = as_of - timedelta(days=rng.randint(0, 180))
            addrs = addr_by_customer.get(customer["customer_id"])
            if not addrs:
                continue
            address = rng.choice(addrs)
            in_store_city = address["city"] in branch_by_city
            branch = (rng.choice(branch_by_city[address["city"]]) if in_store_city
                      else rng.choice(branch_by_city["Karachi"]))

            basket = rng.sample(sellable, rng.randint(1, 5))
            needs_rx = any(p["requires_rx"] for p in basket)
            cold = any(p["is_cold_chain"] for p in basket)
            if cold and not branch["has_cold_chain"]:
                basket = [p for p in basket if not p["is_cold_chain"]] or [sellable[0]]
                cold = False

            subtotal = 0.0
            for idx, product in enumerate(basket, start=1):
                qty = rng.choices([1, 2, 3], weights=[68, 24, 8])[0]
                line = round(product["price_pkr"] * qty, 2)
                subtotal += line
                items.append(dict(
                    order_item_id=f"{order_id}-{idx}", order_id=order_id,
                    sku=product["sku"], quantity=qty,
                    unit_price_pkr=product["price_pkr"], line_total_pkr=line,
                    substituted_from=None, item_status="fulfilled",
                ))

            fulfilment = ("courier" if not in_store_city
                          else rng.choices(["express_1hr", "same_day", "next_day", "pickup"],
                                           weights=[46, 30, 16, 8])[0])
            fee = (0.0 if subtotal >= FREE_DELIVERY_THRESHOLD_PKR else
                   0.0 if fulfilment == "pickup" else
                   COURIER_FEE_PKR if fulfilment == "courier" else
                   EXPRESS_FEE_PKR if fulfilment == "express_1hr" else STANDARD_FEE_PKR)
            promo = rng.choice([None, None, None, "DVAGO10", "FREESHIP", "FIRST15"])
            discount = round(min(subtotal * 0.10, 500), 2) if promo == "DVAGO10" else 0.0
            if promo == "FREESHIP" and subtotal >= 3000:
                fee = 0.0

            age_days = (as_of - placed).days
            if age_days > 7:
                status = rng.choices(["delivered", "cancelled", "returned", "refunded"],
                                     weights=[88, 7, 3, 2])[0]
            else:
                status = rng.choices(
                    ["delivered", "out_for_delivery", "dispatched", "packed",
                     "confirmed", "placed", "awaiting_prescription", "cancelled"],
                    weights=[52, 8, 8, 8, 8, 5, 6 if needs_rx else 0, 5])[0]

            method = rng.choices(
                ["cod", "card_online", "easypaisa", "jazzcash", "wallet", "bank_transfer"],
                weights=[46, 30, 10, 8, 4, 2])[0]
            pay_status = ("refunded" if status == "refunded" else
                          "pending" if method == "cod" and status != "delivered" else
                          "paid")

            rx_rows = [r for r in rx_by_customer.get(customer["customer_id"], [])
                       if r["status"] == "verified"]
            orders.append(dict(
                order_id=order_id, customer_id=customer["customer_id"],
                branch_id=branch["branch_id"], address_id=address["address_id"],
                prescription_id=rx_rows[0]["prescription_id"] if needs_rx and rx_rows else None,
                placed_at=f"{placed}T{rng.randint(8, 22):02d}:{rng.randint(0, 59):02d}:00",
                channel=rng.choices(["app", "web", "phone", "whatsapp", "walk_in"],
                                    weights=[62, 18, 10, 6, 4])[0],
                fulfilment_type=fulfilment, status=status, payment_method=method,
                payment_status=pay_status, subtotal_pkr=round(subtotal, 2),
                discount_pkr=discount, delivery_fee_pkr=fee,
                total_pkr=round(subtotal - discount + fee, 2), promo_code=promo,
                promised_by=f"{placed}T{min(rng.randint(10, 23), 23):02d}:00:00",
                delivered_at=f"{placed + timedelta(days=rng.randint(0, 2))}T"
                             f"{rng.randint(10, 21):02d}:00:00"
                if status == "delivered" else None,
                cancelled_reason="Customer requested cancellation"
                if status == "cancelled" else None,
                requires_cold_chain=1 if cold else 0, notes=None,
            ))
    return orders, items


def _deliveries(
    rng: random.Random,
    orders: list[dict[str, Any]],
    riders: list[dict[str, Any]],
    as_of: date,
) -> list[dict[str, Any]]:
    riders_by_branch: dict[str, list[dict[str, Any]]] = {}
    for r in riders:
        riders_by_branch.setdefault(r["branch_id"], []).append(r)

    status_map = {
        "packed": "awaiting_pickup", "dispatched": "in_transit",
        "out_for_delivery": "out_for_delivery", "delivered": "delivered",
        "returned": "returned",
    }
    rows = []
    for order in orders:
        mapped = status_map.get(order["status"])
        if mapped is None or order["fulfilment_type"] == "pickup":
            continue
        own_rider = order["fulfilment_type"] != "courier"
        pool = riders_by_branch.get(order["branch_id"], [])
        rider = rng.choice(pool) if own_rider and pool else None
        placed = datetime.fromisoformat(order["placed_at"])
        dispatched = placed + timedelta(minutes=rng.randint(25, 180))

        rows.append(dict(
            delivery_id=f"DLV-{order['order_id'].split('-')[-1]}", order_id=order["order_id"],
            rider_id=rider["rider_id"] if rider else None,
            courier="DVAGO Rider" if own_rider else rng.choice(COURIERS[1:]),
            tracking_number=None if own_rider else f"{rng.randint(10**11, 10**12 - 1)}",
            status=mapped,
            dispatched_at=dispatched.isoformat(timespec="seconds")
            if mapped != "awaiting_pickup" else None,
            eta=(dispatched + timedelta(minutes=rng.randint(20, 90))).isoformat(
                timespec="seconds"),
            delivered_at=order["delivered_at"], attempt_count=1 if mapped == "delivered"
            else rng.choices([0, 1, 2], weights=[70, 25, 5])[0],
            failure_reason=None, delivery_otp=f"{rng.randint(1000, 9999)}",
            cold_box_used=order["requires_cold_chain"],
            proof_ref=f"pod/{order['order_id']}.jpg" if mapped == "delivered" else None,
        ))

    # Anchor: the live order's rider is named and the ETA is inside the hour.
    for row in rows:
        if row["order_id"] == "DV-2026-104471":
            row.update(rider_id="RDR-0001", courier="DVAGO Rider", status="out_for_delivery",
                       dispatched_at=f"{as_of}T11:52:00", eta=f"{as_of}T12:20:00",
                       attempt_count=0, cold_box_used=0, delivery_otp="7391")
        if row["order_id"] == "DV-2026-104455":
            row.update(courier="TCS", tracking_number="784512339061", rider_id=None,
                       status="in_transit", eta=f"{as_of + timedelta(days=2)}T18:00:00")
        if row["order_id"] == "DV-2026-103980":
            row.update(rider_id="RDR-0002", cold_box_used=1, attempt_count=2,
                       failure_reason="First attempt failed: nobody at the address")
    return rows


def _complaints(
    rng: random.Random, orders: list[dict[str, Any]], as_of: date
) -> list[dict[str, Any]]:
    rows = anchor_complaints(as_of)
    categories = ["late_delivery", "wrong_item", "missing_item", "damaged_packaging",
                  "expired_product", "refund_delay", "rider_behaviour", "price_dispute",
                  "prescription_rejected", "cold_chain_breach", "app_issue",
                  "out_of_stock_after_order", "other"]
    summaries = {
        "late_delivery": ("Order arrived outside the promised window",
                          "آرڈر وقت پر نہیں پہنچا"),
        "wrong_item": ("Received a different brand than ordered", "غلط دوائی ملی ہے"),
        "missing_item": ("One item missing from the delivered order",
                         "ایک item آرڈر میں نہیں تھا"),
        "damaged_packaging": ("Syrup bottle leaked in transit", "بوتل لیک ہو گئی تھی"),
        "expired_product": ("Batch expiry earlier than expected",
                            "دوائی کی expiry بہت قریب تھی"),
        "refund_delay": ("Refund not received within the quoted period",
                         "refund ابھی تک نہیں آیا"),
        "rider_behaviour": ("Rider was not willing to wait", "rider نے انتظار نہیں کیا"),
        "price_dispute": ("Charged more than the app showed",
                          "app پر قیمت کم دکھائی گئی تھی"),
        "prescription_rejected": ("Prescription rejected without a clear reason",
                                  "prescription بغیر وجہ reject ہو گئی"),
        "cold_chain_breach": ("Insulin arrived without a cold box",
                              "انسولین cold box کے بغیر آئی"),
        "app_issue": ("App crashes at checkout", "app checkout پر بند ہو جاتی ہے"),
        "out_of_stock_after_order": ("Item cancelled after the order was confirmed",
                                     "آرڈر کے بعد item cancel کر دیا گیا"),
        "other": ("General query about the loyalty programme",
                  "loyalty points کے بارے میں سوال"),
    }
    n = 0
    for order in orders:
        if rng.random() > 0.14:
            continue
        n += 1
        category = rng.choice(categories)
        summary, summary_ur = summaries[category]
        raised = datetime.fromisoformat(order["placed_at"]) + timedelta(
            days=rng.randint(0, 4), hours=rng.randint(1, 10))
        if raised.date() > as_of:
            raised = datetime.combine(as_of, raised.time())
        age = (as_of - raised.date()).days
        status = (rng.choices(["resolved", "closed", "in_progress"], weights=[62, 28, 10])[0]
                  if age > 5 else
                  rng.choices(["open", "in_progress", "awaiting_customer", "resolved"],
                              weights=[30, 34, 16, 20])[0])
        done = status in {"resolved", "closed"}
        priority = ("critical" if category == "cold_chain_breach" else
                    "high" if category in {"expired_product", "wrong_item", "late_delivery"}
                    else rng.choice(["low", "medium", "medium"]))
        rows.append(dict(
            complaint_id=f"CMP-2026-{1000 + n:04d}", customer_id=order["customer_id"],
            order_id=order["order_id"], category=category, summary=summary,
            summary_ur=summary_ur,
            channel=rng.choices(["phone", "app", "whatsapp", "email", "voice_agent"],
                                weights=[38, 30, 18, 6, 8])[0],
            priority=priority, status=status,
            raised_at=raised.isoformat(timespec="seconds"),
            sla_due_at=(raised + timedelta(hours=24 if priority in {"high", "critical"}
                                           else 72)).isoformat(timespec="seconds"),
            assigned_to=rng.choice(["Karachi Ops - Tier 1", "Karachi Ops - Tier 2",
                                    "Lahore Ops", "Finance - Refunds", "Tech Support",
                                    "Pharmacy Desk"]),
            resolved_at=(raised + timedelta(days=rng.randint(1, 4))).isoformat(
                timespec="seconds") if done else None,
            resolution=rng.choice(["Replacement dispatched", "Refund approved",
                                   "Explained and customer satisfied",
                                   "Loyalty points credited as goodwill"]) if done else None,
            csat_score=rng.choices([5, 4, 3, 2, 1], weights=[38, 30, 16, 10, 6])[0]
            if done else None,
        ))
    return rows


def _refunds(
    rng: random.Random,
    orders: list[dict[str, Any]],
    complaints: list[dict[str, Any]],
    as_of: date,
) -> list[dict[str, Any]]:
    rows = anchor_refunds(as_of)
    complaint_by_order = {c["order_id"]: c["complaint_id"] for c in complaints
                          if c["order_id"]}
    n = 0
    for order in orders:
        if order["order_id"] == "DV-2026-104388":
            continue
        if order["status"] not in {"refunded", "returned", "cancelled"}:
            continue
        if order["payment_method"] == "cod" and order["payment_status"] == "pending":
            continue  # nothing was ever paid, so nothing to refund
        n += 1
        initiated = datetime.fromisoformat(order["placed_at"]) + timedelta(
            days=rng.randint(1, 5))
        status = rng.choices(["completed", "processing", "approved", "requested", "rejected"],
                             weights=[62, 14, 10, 8, 6])[0]
        rows.append(dict(
            refund_id=f"REF-2026-{2000 + n:04d}", order_id=order["order_id"],
            complaint_id=complaint_by_order.get(order["order_id"]),
            amount_pkr=order["total_pkr"],
            method=rng.choices(["original_payment", "wallet", "bank_transfer"],
                               weights=[72, 20, 8])[0],
            status=status, initiated_at=initiated.isoformat(timespec="seconds"),
            completed_at=(initiated + timedelta(days=rng.randint(2, 9))).isoformat(
                timespec="seconds") if status == "completed" else None,
            expected_by=str(initiated.date() + timedelta(days=7)),
            reference_no=f"RRN-{rng.randint(100000000, 999999999)}",
            reason=order["cancelled_reason"] or "Returned by customer",
        ))
    return rows


def _promotions(as_of: date) -> list[dict[str, Any]]:
    rows = []
    for p in ANCHOR_PROMOTIONS:
        row = {k: v for k, v in p.items() if not k.startswith("_")}
        row["valid_from"] = str(as_of + timedelta(days=p["_from_days"]))
        row["valid_to"] = str(as_of + timedelta(days=p["_to_days"]))
        rows.append(row)
    return rows


def _refills(
    rng: random.Random,
    customers: list[dict[str, Any]],
    products: list[dict[str, Any]],
    sku_of: dict[str, str],
    as_of: date,
) -> list[dict[str, Any]]:
    chronic_skus = [sku_of[b] for b in
                    ["Glucophage", "Amaryl", "Lipitor", "Concor", "Norvasc", "Thyronorm",
                     "Lantus SoloStar", "Tenormin", "Accu-Chek Active Strips"]]
    rows = [dict(
        subscription_id="SUB-000001", customer_id="CUS-000001",
        sku=sku_of["Glucophage"], quantity=3, frequency_days=30,
        next_refill_on=str(as_of + timedelta(days=4)),
        last_ordered_on=str(as_of - timedelta(days=26)), status="active",
        reminder_channel="whatsapp", created_on=str(as_of - timedelta(days=400)),
    ), dict(
        subscription_id="SUB-000002", customer_id="CUS-000002",
        sku=sku_of["Thyronorm"], quantity=1, frequency_days=90,
        next_refill_on=str(as_of - timedelta(days=3)),  # overdue: proactive prompt
        last_ordered_on=str(as_of - timedelta(days=93)), status="active",
        reminder_channel="sms", created_on=str(as_of - timedelta(days=560)),
    )]

    n = 2
    for customer in customers[len(ANCHOR_CUSTOMERS):]:
        if not customer["is_chronic_patient"] or rng.random() > 0.55:
            continue
        n += 1
        freq = rng.choice([30, 30, 60, 90])
        last = as_of - timedelta(days=rng.randint(0, freq))
        rows.append(dict(
            subscription_id=f"SUB-{n:06d}", customer_id=customer["customer_id"],
            sku=rng.choice(chronic_skus), quantity=rng.choice([1, 1, 2, 3]),
            frequency_days=freq, next_refill_on=str(last + timedelta(days=freq)),
            last_ordered_on=str(last),
            status=rng.choices(["active", "paused", "cancelled"], weights=[80, 12, 8])[0],
            reminder_channel=rng.choices(["sms", "whatsapp", "call", "app_push"],
                                         weights=[42, 34, 8, 16])[0],
            created_on=str(as_of - timedelta(days=rng.randint(60, 900))),
        ))
    return rows


def _mobile(rng: random.Random) -> str:
    return f"{rng.choice(MOBILE_PREFIXES)}{rng.randint(1000000, 9999999)}"


# ── Workbook ─────────────────────────────────────────────────────────────


def write_workbook(sheets: dict[str, list[dict[str, Any]]], out: Path, as_of: date) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    schema = load_schema()
    workbook = Workbook()
    workbook.remove(workbook.active)

    _write_readme(workbook, sheets, as_of)
    _write_dictionary(workbook, schema, sheets)

    header_fill = PatternFill("solid", fgColor="1F4E5F")
    header_font = Font(bold=True, color="FFFFFF")
    key_fill = PatternFill("solid", fgColor="DCE9EF")

    # Sheet order follows the schema so the workbook reads in dependency order:
    # you can load it top to bottom without a foreign key ever pointing forward.
    for name in schema:
        rows = sheets[name]
        columns = list(schema[name].column_names)
        sheet = workbook.create_sheet(name)
        sheet.append(columns)

        for row in rows:
            sheet.append([row.get(c) for c in columns])

        for idx, column in enumerate(columns, start=1):
            cell = sheet.cell(row=1, column=idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="left")
            width = max(len(column) + 2, min(
                max((len(str(r.get(column, ""))) for r in rows[:200]), default=10) + 2, 42))
            sheet.column_dimensions[get_column_letter(idx)].width = width
            if schema[name].column(column).is_primary_key:
                for r in range(2, min(len(rows) + 2, 5000)):
                    sheet.cell(row=r, column=idx).fill = key_fill

        sheet.freeze_panes = "A2"
        if rows:
            sheet.auto_filter.ref = (
                f"A1:{get_column_letter(len(columns))}{len(rows) + 1}")

    out.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(out)


def _write_readme(workbook: Any, sheets: dict[str, list[dict[str, Any]]], as_of: date) -> None:
    from openpyxl.styles import Alignment, Font

    sheet = workbook.create_sheet("_README")
    lines = [
        ("DVAGO voice-agent demo dataset", True),
        ("", False),
        ("SYNTHETIC DATA. Generated for a voice-agent demo. DVAGO is a real", False),
        ("company; none of these customers, orders, complaints, refunds or", False),
        ("stock figures are theirs. Brands, manufacturers, cities and areas are", False),
        ("real so the demo is recognisable. Prices are indicative mid-2026", False),
        ("retail and move with DRAP notifications.", False),
        ("", False),
        (f"as_of date: {as_of}   (all dates are relative to this)", False),
        (f"seed: {SEED}   (same seed + same as_of = identical workbook)", False),
        ("", False),
        ("How to use it", True),
        ("", False),
        ("One sheet per table. Row 1 is the column header and matches", False),
        ("src/agent/data/schema.sql exactly — a test enforces that, so adding", False),
        ("a column here without adding it there will fail the build.", False),
        ("", False),
        ("Edit the cells, then rebuild the database:", False),
        ("    uv run python scripts/build_dvago_db.py", False),
        ("", False),
        ("The builder validates types, CHECK constraints and every foreign key", False),
        ("before writing, and refuses to produce a database it cannot vouch for.", False),
        ("", False),
        ("_data_dictionary describes every column: type, key, constraint,", False),
        ("default and what it means. It is generated from the DDL.", False),
        ("", False),
        ("Contents", True),
        ("", False),
    ]
    for text, bold in lines:
        sheet.append([text])
        if bold:
            sheet.cell(row=sheet.max_row, column=1).font = Font(bold=True, size=12)

    sheet.append(["table", "rows"])
    sheet.cell(row=sheet.max_row, column=1).font = Font(bold=True)
    sheet.cell(row=sheet.max_row, column=2).font = Font(bold=True)
    for name, rows in sheets.items():
        sheet.append([name, len(rows)])
    sheet.append(["TOTAL", sum(len(r) for r in sheets.values())])
    sheet.cell(row=sheet.max_row, column=1).font = Font(bold=True)
    sheet.cell(row=sheet.max_row, column=2).font = Font(bold=True)

    sheet.column_dimensions["A"].width = 74
    sheet.column_dimensions["B"].width = 12
    for row in sheet.iter_rows():
        row[0].alignment = Alignment(vertical="top")


def _write_dictionary(
    workbook: Any, schema: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]
) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    sheet = workbook.create_sheet("_data_dictionary")
    headers = ["table", "column", "type", "key", "nullable", "references",
               "allowed values", "default", "description", "example"]
    sheet.append(headers)

    for table in schema.values():
        rows = sheets.get(table.name, [])
        sample = rows[0] if rows else {}
        for column in table.columns:
            example = sample.get(column.name)
            allowed = ""
            if column.check and " IN (" in column.check:
                allowed = column.check.split(" IN (", 1)[1].rstrip(")").replace("'", "")
            elif column.check:
                allowed = column.check
            sheet.append([
                table.name, column.name, column.sql_type, column.key,
                "yes" if column.nullable else "no", column.references or "",
                allowed, column.default or "", column.comment,
                "" if example is None else str(example)[:60],
            ])

    fill = PatternFill("solid", fgColor="1F4E5F")
    for idx in range(1, len(headers) + 1):
        cell = sheet.cell(row=1, column=idx)
        cell.fill = fill
        cell.font = Font(bold=True, color="FFFFFF")
    for letter, width in zip(
        "ABCDEFGHIJ", [22, 22, 9, 6, 9, 26, 52, 14, 56, 34], strict=True
    ):
        sheet.column_dimensions[letter].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = f"A1:J{sheet.max_row}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(),
                        help="anchor date; all generated dates are relative to it")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    sheets = build(args.as_of)
    write_workbook(sheets, args.out, args.as_of)

    total = sum(len(r) for r in sheets.values())
    print(f"wrote {args.out}  ({len(sheets)} sheets, {total:,} rows, as of {args.as_of})")
    for name, rows in sheets.items():
        print(f"  {name:24} {len(rows):>6,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
