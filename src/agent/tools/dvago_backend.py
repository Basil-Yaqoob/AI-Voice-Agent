"""The tools a DVAGO caller's questions turn into.

Each handler runs one or two indexed queries and returns a short line of text.
Text, not JSON: the model has to speak the answer, and prose costs fewer tokens
and one less transformation than a structure it would have to read out anyway.

Three rules this file exists to enforce, none of which can be left to the model:

**Caller identity is bound, never passed.** ``caller_phone`` is fixed when the
backend is built — from the SIP INVITE in production, from the demo page here.
No tool takes a phone number as an argument, so a caller cannot talk the agent
into reading out somebody else's orders by reciting their number. The model
does not get to choose whose data it fetches.

**Allergies are checked in the query.** If the caller is allergic to penicillin
and asks for Augmentin, the warning is the first thing in the tool result. A
model that has to cross-reference two separate tool calls to notice will
usually notice, and "usually" is the wrong standard when the answer is a
medicine.

**Availability means available.** Every stock figure comes from
``v_available_stock``, which subtracts what is already reserved. Reading
``qty_on_hand`` would promise boxes that are sitting in someone else's packed
order.

Replacing this file with calls to the client's real API is the whole
integration. Nothing above it changes.
"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import date, datetime, timedelta

from agent.data.sqlite import Database, Row
from agent.telemetry.logging import get_logger
from agent.tools.registry import ToolRegistry

__all__ = ["DvagoBackend", "build_dvago_registry"]

log = get_logger(__name__)

# What a live API costs. SQLite answers in about a millisecond, and a demo
# where tools are free hides the pause the client's own systems will add — and
# with it the need for a filler line. Set to 0.0 in tests.
SIMULATED_API_LATENCY_S = 0.09

# Beyond this a "nearby branch" is not nearby, whatever the database says.
MAX_NEARBY_KM = 15.0
MAX_BRANCHES_IN_ANSWER = 3

# Families a stated allergy rules out. Deliberately broad — over-warning costs
# a sentence, under-warning costs more than the demo.
ALLERGY_CLASHES: dict[str, tuple[str, ...]] = {
    "penicillin": ("amoxicillin", "clavulanic", "ampicillin", "penicillin", "cephradine",
                   "cefixime", "cephalexin"),
    "cephalosporins": ("cephradine", "cefixime", "cephalexin", "ceftriaxone"),
    "sulfa drugs": ("sulfamethoxazole", "trimethoprim", "sulfadiazine"),
    "aspirin": ("aspirin", "ibuprofen", "naproxen", "diclofenac", "mefenamic", "nimesulide"),
    "nsaids": ("ibuprofen", "naproxen", "diclofenac", "mefenamic", "nimesulide", "aspirin"),
}

_WORD = re.compile(r"[^\w\s؀-ۿ]+")


class DvagoBackend:
    """Query handlers bound to one caller for the length of one call."""

    def __init__(
        self,
        db: Database,
        *,
        caller_phone: str | None = None,
        latency_s: float = SIMULATED_API_LATENCY_S,
    ) -> None:
        self._db = db
        self._caller_phone = _digits(caller_phone) if caller_phone else None
        self._latency_s = latency_s
        self._customer: Row | None = None
        self._customer_loaded = False

    # ── identity ──────────────────────────────────────────────────────────

    async def _caller(self) -> Row | None:
        """The calling customer, looked up once and cached for the call."""
        if self._customer_loaded:
            return self._customer
        self._customer_loaded = True
        if self._caller_phone:
            self._customer = await self._db.fetch_one(
                "SELECT * FROM customers WHERE replace(phone, '-', '') = ?",
                (self._caller_phone,),
            )
        return self._customer

    async def _pause(self) -> None:
        if self._latency_s:
            await asyncio.sleep(self._latency_s)

    # ── catalogue ─────────────────────────────────────────────────────────

    async def find_medicine(self, name: str) -> str:
        """Price, pack and prescription status for a product the caller named."""
        await self._pause()
        matches = await self._match_products(name)
        if not matches:
            return (
                f"No product matching '{name}' in the catalogue. Ask the caller to spell "
                "the brand name, or ask what the medicine is for."
            )

        product = matches[0]
        parts = [
            f"{_label(product)} ({product['generic_name']}), {product['pack_size']},"
            f" {product['price_pkr']:.0f} rupees"
        ]
        if product["discount_pct"]:
            parts.append(
                f"Discounted from {product['mrp_pkr']:.0f} ({product['discount_pct']:.0f}% off)"
            )
        if product["requires_rx"]:
            parts.append("PRESCRIPTION REQUIRED - the caller must upload one before it ships")
        if product["is_controlled"]:
            parts.append(
                f"controlled item, maximum {product['max_qty_per_order']} per order, "
                "original prescription must be shown to the rider"
            )
        if product["is_cold_chain"]:
            parts.append(
                "cold-chain item: stocked only at branches with a fridge and delivered "
                "in a cold box, so express delivery may not be available"
            )

        warning = await self._allergy_warning(product)
        alternatives = [m["brand_name"] for m in matches[1:4]]
        if alternatives:
            parts.append(f"Other matches: {', '.join(alternatives)}")

        return warning + ". ".join(parts) + "."

    async def check_stock(self, medicine: str, area: str = "", city: str = "") -> str:
        """Which nearby branches actually have it, or what to offer instead."""
        await self._pause()
        matches = await self._match_products(medicine)
        if not matches:
            return f"No product matching '{medicine}'. Ask the caller to spell the brand name."
        product = matches[0]

        origin = await self._origin(area, city)
        rows = await self._db.fetch_all(
            """SELECT branch_name, area, city, qty_available, branch_phone, is_24_7,
                      supports_express, latitude, longitude, expiry_date
               FROM v_available_stock
               WHERE sku = ? AND qty_available > 0 AND branch_status = 'active'
                 AND (? = '' OR city = ?)""",
            (product["sku"], origin["city"], origin["city"]),
        )
        nearby = _nearest(rows, origin, limit=MAX_BRANCHES_IN_ANSWER)

        warning = await self._allergy_warning(product)
        label = _label(product)

        if not nearby:
            return warning + await self._out_of_stock(product, label, origin)

        lines = [f"{label} is available at:"]
        for row in nearby:
            distance = f", {row['_km']:.1f} km away" if row.get("_km") is not None else ""
            hours = ", open 24 hours" if row["is_24_7"] else ""
            express = ", one-hour delivery" if row["supports_express"] else ""
            lines.append(
                f"- {row['branch_name']} ({row['area']}){distance}: "
                f"{row['qty_available']} in stock{hours}{express}"
            )
        if product["requires_rx"]:
            lines.append("Prescription required before this can be dispatched.")
        return warning + "\n".join(lines)

    async def _out_of_stock(self, product: Row, label: str, origin: Row) -> str:
        """The answer that matters: not 'no', but 'not here, and here is what else'."""
        elsewhere = await self._db.fetch_all(
            """SELECT city, area, branch_name, qty_available FROM v_available_stock
               WHERE sku = ? AND qty_available > 0 AND branch_status = 'active'
               ORDER BY qty_available DESC LIMIT 2""",
            (product["sku"],),
        )
        substitutes = await self._db.fetch_all(
            """SELECT p.brand_name, p.strength, p.price_pkr, s.equivalence,
                      s.price_diff_pkr, s.pharmacist_note, p.requires_rx
               FROM substitutes s JOIN products p ON p.sku = s.alternative_sku
               WHERE s.sku = ? AND p.is_active = 1""",
            (product["sku"],),
        )

        near = origin["area"] or origin["city"] or "the caller"
        parts = [f"{label} is out of stock near {near}."]

        if elsewhere:
            same_city = [r for r in elsewhere if r["city"] == origin["city"]]
            if same_city:
                where = "; ".join(
                    f"{r['branch_name']} has {r['qty_available']}" for r in same_city
                )
                parts.append(f"Still in stock elsewhere in {origin['city']}: {where}.")
            else:
                # Another city means a courier, which means days rather than an
                # hour. Saying "we have it" without that is how a promise breaks.
                where = "; ".join(
                    f"{r['branch_name']} in {r['city']}" for r in elsewhere
                )
                parts.append(
                    f"Only in stock in another city ({where}), so it would come by courier "
                    "in two to three working days, not the same day."
                )

        if substitutes:
            offers = []
            for s in substitutes[:2]:
                sign = "+" if s["price_diff_pkr"] > 0 else ""
                offers.append(
                    f"{_label(s)} at {s['price_pkr']:.0f} rupees "
                    f"({sign}{s['price_diff_pkr']:.0f} rupees), "
                    f"{s['equivalence'].replace('_', ' ')} - {s['pharmacist_note']}"
                )
            parts.append("Alternatives a pharmacist can offer: " + "; ".join(offers) + ".")
            parts.append(
                "Offer these as options and say a pharmacist will confirm. Do not tell the "
                "caller to switch medicine on your own authority."
            )
        else:
            parts.append("No approved alternative is listed. Offer to transfer to a pharmacist.")
        return " ".join(parts)

    async def find_branch(self, area: str = "", city: str = "") -> str:
        """Nearest branches with hours, phone and whether pickup is possible."""
        await self._pause()
        origin = await self._origin(area, city)
        if not origin["city"]:
            return (
                "No city known for this caller. Ask which city or area they are in - "
                "DVAGO has stores in Karachi, Lahore, Hyderabad, Multan, Bahawalpur, "
                "Kasur, Sahiwal and Burewala, and delivers by courier everywhere else."
            )

        rows = await self._db.fetch_all(
            """SELECT branch_name, area, city, address, landmark, phone, opens_at,
                      closes_at, is_24_7, supports_pickup, supports_express,
                      has_cold_chain, latitude, longitude, status
               FROM branches WHERE city = ? AND status = 'active'""",
            (origin["city"],),
        )
        if not rows:
            return (
                f"No DVAGO store in {origin['city']}. Orders there are delivered by courier "
                "in two to three working days, not one hour."
            )

        nearby = _nearest(rows, origin, limit=MAX_BRANCHES_IN_ANSWER)
        lines = [f"Branches near {origin['area'] or origin['city']}:"]
        for row in nearby:
            distance = f", {row['_km']:.1f} km" if row.get("_km") is not None else ""
            hours = ("open 24 hours" if row["is_24_7"]
                     else f"{row['opens_at']} to {row['closes_at']}")
            extras = []
            if row["supports_express"]:
                extras.append("one-hour delivery")
            if row["supports_pickup"]:
                extras.append("pickup")
            if row["has_cold_chain"]:
                extras.append("insulin and vaccines")
            lines.append(
                f"- {row['branch_name']}, {row['landmark']}{distance}. {hours}. "
                f"Phone {row['phone']}. Offers: {', '.join(extras)}."
            )
        return "\n".join(lines)

    # ── orders ────────────────────────────────────────────────────────────

    async def get_order_status(self, order_id: str = "") -> str:
        """Where an order is, with its delivery and any missing lines."""
        await self._pause()
        customer = await self._caller()

        if order_id:
            order = await self._db.fetch_one(
                "SELECT * FROM orders WHERE upper(replace(order_id,' ','')) = ?",
                (_order_key(order_id),),
            )
            if order is None:
                return (
                    f"No order matching {order_id}. Ask the caller to read the number again - "
                    "it is on the confirmation SMS and starts with DV."
                )
            # Someone else's order. Confirm nothing, not even that it exists.
            if customer and order["customer_id"] != customer["customer_id"]:
                return (
                    "That order number is not on this caller's account. Ask them to confirm "
                    "the number, or offer to look up their most recent order instead."
                )
        else:
            if customer is None:
                return (
                    "The caller is not identified and gave no order number. Ask for the order "
                    "number from their confirmation SMS."
                )
            order = await self._db.fetch_one(
                """SELECT * FROM orders WHERE customer_id = ?
                   ORDER BY placed_at DESC LIMIT 1""",
                (customer["customer_id"],),
            )
            if order is None:
                return "This caller has no orders on record."

        items = await self._db.fetch_all(
            """SELECT p.brand_name, i.quantity, i.item_status
               FROM order_items i JOIN products p ON p.sku = i.sku
               WHERE i.order_id = ?""",
            (order["order_id"],),
        )
        delivery = await self._db.fetch_one(
            "SELECT * FROM deliveries WHERE order_id = ?", (order["order_id"],)
        )

        parts = [
            f"Order {order['order_id']}, placed {_spoken_date(order['placed_at'])}, "
            f"{order['total_pkr']:.0f} rupees, {_payment_phrase(order)}. "
            f"Status: {_status_phrase(order['status'])}."
        ]
        listed = ", ".join(f"{i['quantity']} x {i['brand_name']}" for i in items[:5])
        if listed:
            parts.append(f"Items: {listed}.")
        dropped = [i["brand_name"] for i in items if i["item_status"] == "out_of_stock"]
        if dropped:
            parts.append(
                f"NOT included - out of stock at packing: {', '.join(dropped)}. "
                "Say this before the caller has to ask."
            )

        if order["status"] == "awaiting_prescription":
            parts.append(await self._prescription_note(order))
        elif delivery:
            parts.append(_delivery_phrase(delivery))

        if order["status"] in {"cancelled", "refunded", "returned"}:
            parts.append(await self._refund_note(order))
        if order["cancelled_reason"]:
            parts.append(f"Cancelled because: {order['cancelled_reason']}.")
        return " ".join(parts)

    async def _prescription_note(self, order: Row) -> str:
        if not order["prescription_id"]:
            return (
                "No prescription has been uploaded yet. The caller can upload one in the app "
                "or send it on WhatsApp, and a pharmacist reviews it the same day."
            )
        rx = await self._db.fetch_one(
            "SELECT * FROM prescriptions WHERE prescription_id = ?", (order["prescription_id"],)
        )
        if rx is None:
            return "The attached prescription cannot be found. Offer to transfer to a pharmacist."
        if rx["status"] == "pending_review":
            return (
                f"The prescription from {rx['doctor_name']} is with a pharmacist for review. "
                "Review is usually finished within a few hours, and the order ships straight "
                "after."
            )
        if rx["status"] == "rejected":
            return (
                f"The prescription was rejected: {rx['rejection_reason']}. The caller needs to "
                "upload a clearer or newer one."
            )
        if rx["status"] == "expired":
            return (
                f"The prescription expired on {rx['valid_until']}. A prescription is valid for "
                "ninety days; the caller needs a fresh one."
            )
        return "The prescription is verified. The order should move to packing shortly."

    async def _refund_note(self, order: Row) -> str:
        refund = await self._db.fetch_one(
            "SELECT * FROM refunds WHERE order_id = ? ORDER BY initiated_at DESC LIMIT 1",
            (order["order_id"],),
        )
        if refund is None:
            if order["payment_status"] == "pending":
                return "Nothing was paid on this order, so there is no refund to make."
            return "No refund has been raised yet. Offer to raise one."
        if refund["status"] == "completed":
            return (
                f"Refund {refund['refund_id']} of {refund['amount_pkr']:.0f} rupees was "
                f"completed on {_spoken_date(refund['completed_at'])} to their "
                f"{refund['method'].replace('_', ' ')}."
            )
        if refund["status"] == "rejected":
            return f"Refund {refund['refund_id']} was rejected. Offer to escalate."
        return (
            f"Refund {refund['refund_id']} of {refund['amount_pkr']:.0f} rupees is "
            f"{refund['status']}, expected by {refund['expected_by']} to their "
            f"{refund['method'].replace('_', ' ')}. Bank transfers can take a further "
            "two to three working days to appear."
        )

    # ── the caller ────────────────────────────────────────────────────────

    async def get_customer_profile(self) -> str:
        """Who is calling, what they are allergic to, and what is due."""
        await self._pause()
        customer = await self._caller()
        if customer is None:
            return (
                "This number is not registered. Greet the caller as a new customer and offer "
                "to help without assuming any order history."
            )
        if customer["is_blocked"]:
            return (
                f"{customer['full_name']}'s account is suspended. Do not take an order; "
                "transfer to a supervisor."
            )

        parts = [
            f"{customer['full_name']} ({customer['full_name_ur']}), {customer['city']}, "
            f"{customer['loyalty_tier']} tier with {customer['loyalty_points']} points."
        ]
        if customer["wallet_balance_pkr"]:
            parts.append(f"Wallet balance {customer['wallet_balance_pkr']:.0f} rupees.")
        if customer["allergies"]:
            parts.append(
                f"ALLERGIC TO {customer['allergies'].upper()} - never suggest anything in that "
                "family without a pharmacist."
            )
        if customer["chronic_conditions"]:
            parts.append(f"On long-term treatment for {customer['chronic_conditions']}.")
        if customer["preferred_language"] != "urdu":
            parts.append(f"Prefers {customer['preferred_language']}.")

        due = await self._db.fetch_all(
            """SELECT p.brand_name, s.next_refill_on, s.quantity
               FROM refill_subscriptions s JOIN products p ON p.sku = s.sku
               WHERE s.customer_id = ? AND s.status = 'active' AND s.next_refill_on <= ?
               ORDER BY s.next_refill_on""",
            (customer["customer_id"], str(date.today())),
        )
        if due:
            listed = ", ".join(f"{d['quantity']} x {d['brand_name']} (due {d['next_refill_on']})"
                               for d in due)
            parts.append(
                f"Refill due: {listed}. Mention it once, near the end - do not open with a sale."
            )
        return " ".join(parts)

    # ── aftercare ─────────────────────────────────────────────────────────

    async def get_complaint_status(self, complaint_id: str = "") -> str:
        """An existing ticket, or the caller's most recent open one."""
        await self._pause()
        customer = await self._caller()

        if complaint_id:
            complaint = await self._db.fetch_one(
                "SELECT * FROM complaints WHERE upper(replace(complaint_id,' ','')) = ?",
                (complaint_id.strip().upper().replace(" ", ""),),
            )
            if complaint is None:
                return f"No complaint matching {complaint_id}. Ask the caller to read it again."
            if customer and complaint["customer_id"] != customer["customer_id"]:
                return "That complaint number is not on this caller's account."
        else:
            if customer is None:
                return "The caller is not identified. Ask for their complaint reference number."
            complaint = await self._db.fetch_one(
                """SELECT * FROM complaints WHERE customer_id = ?
                   ORDER BY raised_at DESC LIMIT 1""",
                (customer["customer_id"],),
            )
            if complaint is None:
                return "This caller has no complaints on record. Offer to file one."

        parts = [
            f"Complaint {complaint['complaint_id']} about "
            f"{complaint['category'].replace('_', ' ')}, raised "
            f"{_spoken_date(complaint['raised_at'])}, priority {complaint['priority']}. "
            f"Status: {complaint['status'].replace('_', ' ')}."
        ]
        if complaint["resolution"]:
            parts.append(f"Resolution: {complaint['resolution']}.")
        elif complaint["sla_due_at"]:
            parts.append(
                f"It is with {complaint['assigned_to']} and due by "
                f"{_spoken_date(complaint['sla_due_at'])}."
            )
        if complaint["order_id"]:
            parts.append(f"It concerns order {complaint['order_id']}.")
        return " ".join(parts)

    async def file_complaint(self, category: str, summary: str, order_id: str = "") -> str:
        """Open a ticket on the caller's behalf and hand back the reference."""
        await self._pause()
        customer = await self._caller()
        if customer is None:
            return (
                "Cannot file a complaint for an unidentified caller. Ask them to confirm the "
                "mobile number registered with DVAGO."
            )

        category = category.strip().lower().replace(" ", "_")
        allowed = await self._complaint_categories()
        if category not in allowed:
            category = "other"

        if order_id:
            owns = await self._db.fetch_one(
                "SELECT order_id FROM orders WHERE order_id = ? AND customer_id = ?",
                (_order_key(order_id), customer["customer_id"]),
            )
            order_id = owns["order_id"] if owns else ""

        priority = "critical" if category == "cold_chain_breach" else (
            "high" if category in {"expired_product", "wrong_item", "late_delivery"} else "medium"
        )
        now = datetime.now()
        reference = f"CMP-{now.year}-{now.strftime('%m%d%H%M')}"
        sla_hours = 24 if priority in {"high", "critical"} else 72

        await self._db.execute(
            """INSERT INTO complaints (complaint_id, customer_id, order_id, category,
                   summary, summary_ur, channel, priority, status, raised_at,
                   sla_due_at, assigned_to)
               VALUES (?,?,?,?,?,?,'voice_agent',?, 'open', ?, ?, 'Voice Agent Queue')""",
            (reference, customer["customer_id"], order_id or None, category,
             summary[:300], summary[:300], priority,
             now.isoformat(timespec="seconds"),
             (now + timedelta(hours=sla_hours)).isoformat(timespec="seconds")),
        )
        log.info("complaint_filed", reference=reference, category=category, priority=priority)
        return (
            f"Complaint filed as {reference}, priority {priority}. Someone will contact the "
            f"caller within {sla_hours} hours. Read the reference number back to them."
        )

    async def check_promotion(self, promo_code: str) -> str:
        """Whether a code the caller read out still works, and on what."""
        await self._pause()
        code = promo_code.strip().upper().replace(" ", "")
        promo = await self._db.fetch_one(
            "SELECT * FROM promotions WHERE upper(promo_code) = ?", (code,)
        )
        if promo is None:
            return f"No promotion with the code {promo_code}. Ask the caller to spell it."

        today = str(date.today())
        if not promo["is_active"] or promo["valid_to"] < today:
            return (
                f"{promo['promo_code']} ({promo['title']}) expired on {promo['valid_to']} and "
                "cannot be applied. Offer a current promotion instead."
            )
        if promo["valid_from"] > today:
            return f"{promo['promo_code']} does not start until {promo['valid_from']}."
        if promo["usage_limit"] and promo["times_used"] >= promo["usage_limit"]:
            return f"{promo['promo_code']} has reached its usage limit and is finished."

        parts = [
            f"{promo['promo_code']} is valid until {promo['valid_to']}: "
            f"{promo['description']}."
        ]
        if promo["min_order_pkr"]:
            parts.append(f"Minimum order {promo['min_order_pkr']:.0f} rupees.")
        if promo["bank_partner"]:
            parts.append(f"{promo['bank_partner']} cards only.")
        if promo["first_order_only"]:
            customer = await self._caller()
            if customer and customer["total_orders"] > 0:
                parts.append(
                    "First order only, and this caller has ordered before, so it will not apply."
                )
            else:
                parts.append("First order only.")
        if promo["applies_to_category"]:
            parts.append(f"Applies to {promo['applies_to_category']} only.")
        return " ".join(parts)

    # ── helpers ───────────────────────────────────────────────────────────

    async def _match_products(self, query: str) -> list[Row]:
        """Rank catalogue matches for something a caller said out loud.

        Deepgram will hand over "panadole" or "آگمنٹن", so an equality test on
        ``brand_name`` finds nothing most of the time. ``search_terms`` carries
        the misspellings; ranking runs in Python because the ordering depends on
        *which* field matched, and expressing that in SQL costs more than it
        saves on a table this size.
        """
        cleaned = _WORD.sub(" ", query).strip().lower()
        if not cleaned:
            return []
        like = f"%{cleaned}%"
        rows = await self._db.fetch_all(
            """SELECT * FROM products
               WHERE is_active = 1 AND (
                     lower(brand_name)   LIKE ?
                  OR lower(generic_name) LIKE ?
                  OR lower(search_terms) LIKE ?)
               LIMIT 40""",
            (like, like, like),
        )
        if not rows:
            first = cleaned.split()[0]
            if len(first) >= 4:
                stem = f"%{first[:-1]}%"  # tolerate a missing final letter
                rows = await self._db.fetch_all(
                    """SELECT * FROM products
                       WHERE is_active = 1 AND (
                             lower(brand_name) LIKE ? OR lower(search_terms) LIKE ?)
                       LIMIT 40""",
                    (stem, stem),
                )
        return sorted(rows, key=lambda r: _match_rank(r, cleaned))

    async def _allergy_warning(self, product: Row) -> str:
        customer = await self._caller()
        if customer is None or not customer["allergies"]:
            return ""
        haystack = " ".join(
            str(product[field] or "").lower()
            for field in ("generic_name", "brand_name", "therapeutic_class")
        )
        for allergy in (a.strip().lower() for a in customer["allergies"].split(",")):
            for marker in ALLERGY_CLASHES.get(allergy, (allergy,)):
                if marker in haystack:
                    return (
                        f"⚠ SAFETY: this caller's record says they are allergic to {allergy}, "
                        f"and {product['brand_name']} contains {product['generic_name']}. Say so "
                        "first, do not take an order for it, and offer a pharmacist. "
                    )
        return ""

    async def _origin(self, area: str, city: str) -> Row:
        """Where 'nearby' is measured from: what was said, else the caller's default."""
        area, city = area.strip(), city.strip()
        if area or city:
            match = await self._db.fetch_one(
                """SELECT area, city, latitude, longitude FROM branches
                   WHERE (? = '' OR lower(area) LIKE ?) AND (? = '' OR lower(city) = ?)
                   LIMIT 1""",
                (area, f"%{area.lower()}%", city, city.lower()),
            )
            if match:
                return match
            return {"area": area, "city": city, "latitude": None, "longitude": None}

        customer = await self._caller()
        if customer is None:
            return {"area": "", "city": "", "latitude": None, "longitude": None}
        address = await self._db.fetch_one(
            """SELECT area, city, latitude, longitude FROM addresses
               WHERE customer_id = ? ORDER BY is_default DESC LIMIT 1""",
            (customer["customer_id"],),
        )
        return address or {
            "area": "", "city": customer["city"], "latitude": None, "longitude": None
        }

    async def _complaint_categories(self) -> set[str]:
        from agent.data.schema_meta import load_schema

        check = load_schema()["complaints"].column("category").check or ""
        return {part.strip().strip("'") for part in
                check.split(" IN (", 1)[-1].rstrip(")").split(",")}


# ── formatting ───────────────────────────────────────────────────────────


def _label(product: Row) -> str:
    """Brand plus strength — but only when the strength means something.

    Non-drug lines carry "-" as their strength, and "Nan Pro 1 -" read aloud
    sounds like the agent lost its place.
    """
    strength = (product["strength"] or "").strip()
    if strength in {"", "-"}:
        return str(product["brand_name"])
    return f"{product['brand_name']} {strength}"


def _match_rank(row: Row, query: str) -> tuple[int, int]:
    brand = (row["brand_name"] or "").lower()
    generic = (row["generic_name"] or "").lower()
    if brand == query:
        rank = 0
    elif brand.startswith(query):
        rank = 1
    elif query in brand:
        rank = 2
    elif generic == query or generic.startswith(query):
        rank = 3
    elif query in generic:
        rank = 4
    else:
        rank = 5  # matched only on a search term
    return rank, len(brand)


def _nearest(rows: list[Row], origin: Row, *, limit: int) -> list[Row]:
    """Closest first when coordinates exist, same-area first when they do not."""
    lat, lng = origin.get("latitude"), origin.get("longitude")
    if lat is None or lng is None:
        area = (origin.get("area") or "").lower()
        ranked = sorted(rows, key=lambda r: 0 if (r.get("area") or "").lower() == area else 1)
        return [dict(r, _km=None) for r in ranked[:limit]]

    scored = []
    for row in rows:
        if row.get("latitude") is None or row.get("longitude") is None:
            continue
        km = _haversine_km(lat, lng, row["latitude"], row["longitude"])
        scored.append(dict(row, _km=km))
    scored.sort(key=lambda r: r["_km"])
    within = [r for r in scored if r["_km"] <= MAX_NEARBY_KM]
    # If nothing is genuinely close, the nearest one is still the useful answer.
    return (within or scored[:1])[:limit]


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return radius * 2 * math.asin(math.sqrt(a))


def _status_phrase(status: str) -> str:
    return {
        "placed": "placed, not yet confirmed by the branch",
        "awaiting_prescription": "held until a pharmacist verifies the prescription",
        "confirmed": "confirmed and queued for packing",
        "packed": "packed and waiting for a rider",
        "dispatched": "dispatched from the branch",
        "out_for_delivery": "out for delivery now",
        "delivered": "delivered",
        "cancelled": "cancelled",
        "returned": "returned",
        "refunded": "refunded",
    }.get(status, status.replace("_", " "))


def _payment_phrase(order: Row) -> str:
    method = {
        "cod": "cash on delivery", "card_online": "paid by card",
        "wallet": "paid from wallet", "easypaisa": "paid by Easypaisa",
        "jazzcash": "paid by JazzCash", "bank_transfer": "paid by bank transfer",
    }.get(order["payment_method"], str(order["payment_method"]))
    if order["payment_method"] == "cod" and order["payment_status"] == "pending":
        return f"{method}, to be collected by the rider"
    return method


def _delivery_phrase(delivery: Row) -> str:
    """Never includes the OTP. It is in the row and it is not ours to read out."""
    if delivery["status"] == "delivered":
        return f"Delivered {_spoken_date(delivery['delivered_at'])}."

    parts = []
    if delivery["courier"] and delivery["courier"] != "DVAGO Rider":
        parts.append(f"With {delivery['courier']}")
        if delivery["tracking_number"]:
            parts.append(f"tracking number {delivery['tracking_number']}")
    elif delivery["rider_id"]:
        parts.append("With a DVAGO rider")
    if delivery["eta"]:
        parts.append(f"expected {_spoken_date(delivery['eta'], with_time=True)}")
    if delivery["attempt_count"] and delivery["attempt_count"] > 1:
        parts.append(
            f"{delivery['attempt_count']} delivery attempts so far"
            + (f" - {delivery['failure_reason']}" if delivery["failure_reason"] else "")
        )
    return ", ".join(parts) + "." if parts else ""


def _spoken_date(value: str | None, *, with_time: bool = False) -> str:
    """A date the agent can say, relative where a person would say it that way."""
    if not value:
        return "an unknown date"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value

    delta = (moment.date() - date.today()).days
    when = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(delta)
    if when is None:
        when = (f"{-delta} days ago" if delta < 0 else f"in {delta} days") \
            if -7 <= delta <= 7 else moment.strftime("%d %B")
    if with_time:
        return f"{when} at {moment.strftime('%H:%M')}"
    return when


def _digits(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def _order_key(order_id: str) -> str:
    return order_id.strip().upper().replace(" ", "")


# ── registry ─────────────────────────────────────────────────────────────


def build_dvago_registry(backend: DvagoBackend) -> ToolRegistry:
    """Wire the handlers up as tools the model can call.

    Eight tools, and the count is deliberate. Every definition is bytes in the
    cached prefix and one more thing for the model to choose wrongly between;
    the split here follows what callers actually ask rather than what the
    database happens to store. Delivery tracking is folded into
    ``get_order_status`` because "where is my order" is one question, not two.
    No tool takes a phone number, because the caller is already known.
    """
    registry = ToolRegistry()

    registry.register(
        "find_medicine",
        "Look up a medicine or product in the catalogue: price, pack size, whether a "
        "prescription is needed, and whether it is a cold-chain item. Use this when the "
        "caller asks what something costs or whether DVAGO sells it.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Brand or generic name as the caller said it"}
            },
            "required": ["name"],
        },
        backend.find_medicine,
    )

    registry.register(
        "check_stock",
        "Check which nearby branches actually have a medicine in stock right now. Returns "
        "alternatives and further-away branches when it is out of stock locally. Use this "
        "for 'do you have it', 'is it available near me', or before promising delivery.",
        {
            "type": "object",
            "properties": {
                "medicine": {"type": "string", "description": "Brand or generic name"},
                "area": {"type": "string",
                         "description": "Area the caller named, e.g. Bahadurabad. Optional; "
                                        "their saved address is used if omitted."},
                "city": {"type": "string", "description": "City, e.g. Karachi. Optional."},
            },
            "required": ["medicine"],
        },
        backend.check_stock,
    )

    registry.register(
        "find_branch",
        "Find the nearest DVAGO stores with opening hours, phone number, and whether they "
        "do one-hour delivery, pickup, or cold-chain items. Use this for 'where is your "
        "nearest branch' or 'are you open now'.",
        {
            "type": "object",
            "properties": {
                "area": {"type": "string", "description": "Area the caller named. Optional."},
                "city": {"type": "string", "description": "City. Optional."},
            },
            "required": [],
        },
        backend.find_branch,
    )

    registry.register(
        "get_order_status",
        "Get the status of an order with its delivery, ETA and any items dropped at "
        "packing. With no order number it returns the caller's most recent order. Use this "
        "for 'where is my order' and 'has it shipped'.",
        {
            "type": "object",
            "properties": {
                "order_id": {"type": "string",
                             "description": "Order number such as DV-2026-104471. Optional."}
            },
            "required": [],
        },
        backend.get_order_status,
    )

    registry.register(
        "get_customer_profile",
        "Look up who is calling: name, loyalty tier, allergies, long-term conditions and "
        "any refill that is due. Call this once at the start of a call. It takes no "
        "arguments - the caller's number comes from the call itself.",
        {"type": "object", "properties": {}, "required": []},
        backend.get_customer_profile,
    )

    registry.register(
        "get_complaint_status",
        "Check an existing complaint or, with no reference, the caller's most recent one. "
        "Use this for 'what happened to my complaint' before offering to file a new one.",
        {
            "type": "object",
            "properties": {
                "complaint_id": {"type": "string",
                                 "description": "Reference such as CMP-2026-0042. Optional."}
            },
            "required": [],
        },
        backend.get_complaint_status,
    )

    registry.register(
        "file_complaint",
        "File a new complaint for the caller and return the reference number. Only call "
        "this once the caller has confirmed the details - it creates a real ticket.",
        {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "One of: late_delivery, wrong_item, missing_item, "
                                   "damaged_packaging, expired_product, refund_delay, "
                                   "rider_behaviour, price_dispute, prescription_rejected, "
                                   "cold_chain_breach, app_issue, out_of_stock_after_order, other",
                },
                "summary": {"type": "string",
                            "description": "One line in the caller's own words"},
                "order_id": {"type": "string",
                             "description": "Related order number, if there is one"},
            },
            "required": ["category", "summary"],
        },
        backend.file_complaint,
    )

    registry.register(
        "check_promotion",
        "Check whether a promo code the caller read out is still valid, and what it applies "
        "to. Use this rather than guessing - expired codes are a common complaint.",
        {
            "type": "object",
            "properties": {
                "promo_code": {"type": "string", "description": "Code such as DVAGO10"}
            },
            "required": ["promo_code"],
        },
        backend.check_promotion,
    )

    return registry
