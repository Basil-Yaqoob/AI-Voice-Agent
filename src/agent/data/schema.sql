-- DVAGO demo operational database.
--
-- SYNTHETIC DATA. Every row is generated. DVAGO is a real company; none of
-- these customers, orders, complaints or stock figures are theirs. The shapes
-- are what matters: this is what the client's real systems get swapped in
-- behind, one query at a time, without the agent changing.
--
-- This file is the single source of truth. The spreadsheet, the loader and the
-- data dictionary are all derived from it, so the trailing comment on each
-- column is documentation with a test behind it, not decoration.
--
-- Design rules:
--
-- * This database holds what *changes*. Policy and product knowledge live in
--   knowledge/dvago/*.md and reach the model through the cached prompt prefix.
--   Anything that can differ between two calls an hour apart belongs here,
--   behind a tool, or the agent will confidently quote yesterday's stock.
-- * Money is REAL rupees, not paisa. Pakistani pharmacy prices carry at most
--   two decimals and the Urdu normaliser reads them correctly.
-- * Dates are ISO-8601 TEXT. SQLite has no date type and ISO strings sort
--   correctly, which is the only property these queries need.
-- * Booleans are INTEGER 0/1.
-- * Every column a tool filters on is indexed. A tool call sits inside the
--   300ms LLM slice of the latency budget; a table scan there is a bug.

PRAGMA foreign_keys = ON;

-- ── Provenance ───────────────────────────────────────────────────────────

CREATE TABLE dataset_meta (
    key                 TEXT PRIMARY KEY,           -- metadata key, e.g. generated_at
    value               TEXT NOT NULL               -- metadata value as text
);

-- ── Network ──────────────────────────────────────────────────────────────

CREATE TABLE branches (
    branch_id           TEXT PRIMARY KEY,           -- DVG-KHI-001
    branch_name         TEXT NOT NULL,              -- display name, e.g. DVAGO Bahadurabad
    city                TEXT NOT NULL,              -- one of the eight retail cities
    area                TEXT NOT NULL,              -- neighbourhood a caller actually names
    address             TEXT NOT NULL,              -- street address for pickup
    landmark            TEXT,                       -- how Pakistani addresses are really given
    phone               TEXT NOT NULL,              -- branch landline, read digit by digit
    latitude            REAL NOT NULL,              -- for nearest-branch distance
    longitude           REAL NOT NULL,              -- for nearest-branch distance
    opens_at            TEXT NOT NULL,              -- HH:MM 24-hour local time
    closes_at           TEXT NOT NULL,              -- HH:MM 24-hour local time
    is_24_7             INTEGER NOT NULL DEFAULT 0, -- 1 if the branch never closes
    has_cold_chain      INTEGER NOT NULL DEFAULT 0, -- can hold insulin and vaccines at 2-8C
    has_pharmacist      INTEGER NOT NULL DEFAULT 1, -- can verify a prescription on site
    supports_pickup     INTEGER NOT NULL DEFAULT 1, -- customer may collect in person
    supports_express    INTEGER NOT NULL DEFAULT 0, -- inside the one-hour delivery zone
    delivery_radius_km  REAL NOT NULL,              -- how far this branch will deliver
    opened_on           TEXT NOT NULL,              -- ISO date the branch opened
    status              TEXT NOT NULL DEFAULT 'active' -- active, temporarily_closed or closed
        CHECK (status IN ('active', 'temporarily_closed', 'closed'))
);
CREATE INDEX idx_branches_city_area ON branches (city, area);
CREATE INDEX idx_branches_status ON branches (status);

CREATE TABLE pharmacists (
    pharmacist_id       TEXT PRIMARY KEY,           -- PH-0001
    full_name           TEXT NOT NULL,              -- name the agent offers to transfer to
    licence_no          TEXT NOT NULL UNIQUE,       -- Pharmacy Council registration number
    branch_id           TEXT NOT NULL REFERENCES branches (branch_id), -- home branch
    shift_start         TEXT NOT NULL,              -- HH:MM, start of today's shift
    shift_end           TEXT NOT NULL,              -- HH:MM, end of today's shift
    languages           TEXT NOT NULL,              -- comma list, e.g. Urdu,English,Sindhi
    is_available        INTEGER NOT NULL DEFAULT 1, -- 1 if free to take a transfer now
    speciality          TEXT                        -- e.g. diabetes care, paediatrics
);
CREATE INDEX idx_pharmacists_branch ON pharmacists (branch_id, is_available);

CREATE TABLE riders (
    rider_id            TEXT PRIMARY KEY,           -- RDR-0001
    full_name           TEXT NOT NULL,              -- named to the customer on dispatch
    phone               TEXT NOT NULL,              -- rider mobile, shared after dispatch
    vehicle_type        TEXT NOT NULL               -- bike, car or van
        CHECK (vehicle_type IN ('bike', 'car', 'van')),
    vehicle_reg         TEXT,                       -- registration plate
    branch_id           TEXT NOT NULL REFERENCES branches (branch_id), -- branch dispatched from
    has_cold_box        INTEGER NOT NULL DEFAULT 0, -- required for insulin and vaccines
    rating              REAL NOT NULL DEFAULT 5.0,  -- rolling customer rating out of 5
    active_deliveries   INTEGER NOT NULL DEFAULT 0, -- current load, drives ETA honesty
    is_on_shift         INTEGER NOT NULL DEFAULT 1  -- 1 if working right now
);
CREATE INDEX idx_riders_branch ON riders (branch_id, is_on_shift);

-- ── Catalogue ────────────────────────────────────────────────────────────

CREATE TABLE products (
    sku                 TEXT PRIMARY KEY,           -- DV-MED-00042
    brand_name          TEXT NOT NULL,              -- what the caller says: "Panadol"
    generic_name        TEXT,                       -- what the pharmacist matches on
    strength            TEXT,                       -- 500mg, 625mg, 100 IU/ml
    dosage_form         TEXT NOT NULL,              -- tablet, syrup, injection, cream
    pack_size           TEXT NOT NULL,              -- 10 tablets, 120ml bottle
    units_per_pack      INTEGER,                    -- numeric count for dosage arithmetic
    manufacturer        TEXT NOT NULL,              -- licence holder in Pakistan
    category            TEXT NOT NULL,              -- top-level catalogue category
    subcategory         TEXT,                       -- second-level category
    therapeutic_class   TEXT,                       -- analgesic, antibiotic, insulin
    price_pkr           REAL NOT NULL,              -- what the caller pays today
    mrp_pkr             REAL NOT NULL,              -- printed price; discount is mrp - price
    discount_pct        REAL NOT NULL DEFAULT 0,    -- derived, stored so the agent needn't compute
    requires_rx         INTEGER NOT NULL DEFAULT 0, -- prescription must be uploaded first
    is_controlled       INTEGER NOT NULL DEFAULT 0, -- narcotic or psychotropic schedule
    is_cold_chain       INTEGER NOT NULL DEFAULT 0, -- 2-8C; only cold-chain branches stock it
    is_active           INTEGER NOT NULL DEFAULT 1, -- 0 if delisted; never offer these
    drap_reg_no         TEXT,                       -- DRAP registration number
    shelf_life_months   INTEGER,                    -- manufacturer shelf life
    storage_note        TEXT,                       -- storage instruction read to the caller
    max_qty_per_order   INTEGER NOT NULL DEFAULT 10,-- per-order cap, tighter on controlled items
    search_terms        TEXT                        -- pipe-separated misspellings and Urdu spellings
);
CREATE INDEX idx_products_brand ON products (brand_name);
CREATE INDEX idx_products_generic ON products (generic_name);
CREATE INDEX idx_products_category ON products (category, subcategory);
CREATE INDEX idx_products_rx ON products (requires_rx);

-- Same-generic alternatives, so "we don't have it" can become "we have this".
-- Directional: A -> B is not implied by B -> A, because the recommendation
-- depends on which one is out of stock.
CREATE TABLE substitutes (
    sku                 TEXT NOT NULL REFERENCES products (sku), -- the item that is unavailable
    alternative_sku     TEXT NOT NULL REFERENCES products (sku), -- what to offer instead
    equivalence         TEXT NOT NULL               -- how close the alternative really is
        CHECK (equivalence IN ('same_generic', 'same_class', 'different_strength')),
    price_diff_pkr      REAL NOT NULL,              -- alternative price minus original price
    pharmacist_note     TEXT,                       -- caveat the agent must pass on
    PRIMARY KEY (sku, alternative_sku)
);

CREATE TABLE inventory (
    branch_id           TEXT NOT NULL REFERENCES branches (branch_id), -- stock location
    sku                 TEXT NOT NULL REFERENCES products (sku),       -- item held
    qty_on_hand         INTEGER NOT NULL,           -- physical count on the shelf
    qty_reserved        INTEGER NOT NULL DEFAULT 0, -- already promised to carts and packed orders
    reorder_level       INTEGER NOT NULL DEFAULT 5, -- below this the branch reorders
    batch_no            TEXT,                       -- nearest-expiry batch identifier
    expiry_date         TEXT,                       -- ISO date the nearest batch expires
    last_restocked_on   TEXT,                       -- ISO date of last delivery in
    shelf_location      TEXT,                       -- aisle and rack, for pickup staff
    PRIMARY KEY (branch_id, sku)
);
CREATE INDEX idx_inventory_sku ON inventory (sku, qty_on_hand);

-- qty_on_hand minus what is already promised to someone else. Every stock
-- question the agent answers must go through this, never the raw column, or
-- it will promise a box that is sitting in a packed order.
CREATE VIEW v_available_stock AS
SELECT
    i.branch_id,
    i.sku,
    MAX(i.qty_on_hand - i.qty_reserved, 0) AS qty_available,
    i.expiry_date,
    b.city,
    b.area,
    b.branch_name,
    b.phone            AS branch_phone,
    b.latitude,
    b.longitude,
    b.is_24_7,
    b.has_cold_chain,
    b.supports_express,
    b.status           AS branch_status
FROM inventory i
JOIN branches b ON b.branch_id = i.branch_id;

-- ── People ───────────────────────────────────────────────────────────────

CREATE TABLE customers (
    customer_id         TEXT PRIMARY KEY,           -- CUS-000123
    full_name           TEXT NOT NULL,              -- Latin spelling, for the record
    full_name_ur        TEXT,                       -- Urdu spelling, so the greeting sounds right
    phone               TEXT NOT NULL UNIQUE,       -- the call's identity key; 03XXXXXXXXX
    alt_phone           TEXT,                       -- secondary contact
    email               TEXT,                       -- for receipts, rarely spoken
    city                TEXT NOT NULL,              -- billing city
    date_of_birth       TEXT,                       -- ISO date; age gates some medicines
    gender              TEXT                        -- male, female or other
        CHECK (gender IN ('male', 'female', 'other', NULL)),
    preferred_language  TEXT NOT NULL DEFAULT 'urdu'-- urdu, english or mixed
        CHECK (preferred_language IN ('urdu', 'english', 'mixed')),
    loyalty_tier        TEXT NOT NULL DEFAULT 'bronze' -- bronze, silver, gold or platinum
        CHECK (loyalty_tier IN ('bronze', 'silver', 'gold', 'platinum')),
    loyalty_points      INTEGER NOT NULL DEFAULT 0, -- redeemable points balance
    wallet_balance_pkr  REAL NOT NULL DEFAULT 0,    -- store credit, usable at checkout
    is_chronic_patient  INTEGER NOT NULL DEFAULT 0, -- on long-term therapy; refill candidate
    chronic_conditions  TEXT,                       -- comma list, e.g. diabetes,hypertension
    allergies           TEXT,                       -- MUST be checked before suggesting anything
    registered_on       TEXT NOT NULL,              -- ISO date the account was created
    total_orders        INTEGER NOT NULL DEFAULT 0, -- lifetime order count
    lifetime_value_pkr  REAL NOT NULL DEFAULT 0,    -- lifetime spend in rupees
    is_blocked          INTEGER NOT NULL DEFAULT 0, -- 1 if the account is suspended
    do_not_call         INTEGER NOT NULL DEFAULT 0  -- outbound campaigns must honour this
);
CREATE INDEX idx_customers_phone ON customers (phone);
CREATE INDEX idx_customers_city ON customers (city);

CREATE TABLE addresses (
    address_id          TEXT PRIMARY KEY,           -- ADR-000456
    customer_id         TEXT NOT NULL REFERENCES customers (customer_id), -- owner
    label               TEXT NOT NULL,              -- home, office, mother's house
    address_line        TEXT NOT NULL,              -- house and street
    area                TEXT NOT NULL,              -- neighbourhood, matched against branches
    city                TEXT NOT NULL,              -- delivery city
    nearest_landmark    TEXT,                       -- what the rider is actually told
    latitude            REAL,                       -- geocoded, may be missing
    longitude           REAL,                       -- geocoded, may be missing
    is_default          INTEGER NOT NULL DEFAULT 0, -- 1 for the address orders default to
    delivery_note       TEXT                        -- gate code, floor, call-on-arrival
);
CREATE INDEX idx_addresses_customer ON addresses (customer_id);

-- ── Prescriptions ────────────────────────────────────────────────────────

CREATE TABLE prescriptions (
    prescription_id     TEXT PRIMARY KEY,           -- RX-2026-00318
    customer_id         TEXT NOT NULL REFERENCES customers (customer_id), -- who uploaded it
    uploaded_at         TEXT NOT NULL,              -- ISO timestamp of upload
    doctor_name         TEXT,                       -- prescriber as written
    doctor_pmdc_no      TEXT,                       -- PM&DC registration of the prescriber
    hospital            TEXT,                       -- issuing hospital or clinic
    issued_on           TEXT,                       -- ISO date on the prescription
    valid_until         TEXT,                       -- ISO date after which it is not accepted
    status              TEXT NOT NULL               -- pending_review, verified, rejected, expired
        CHECK (status IN ('pending_review', 'verified', 'rejected', 'expired')),
    verified_by         TEXT REFERENCES pharmacists (pharmacist_id), -- pharmacist who cleared it
    verified_at         TEXT,                       -- ISO timestamp of verification
    rejection_reason    TEXT,                       -- why it was refused, quoted to the caller
    image_ref           TEXT                        -- storage key of the scan
);
CREATE INDEX idx_prescriptions_customer ON prescriptions (customer_id, status);

-- ── Orders ───────────────────────────────────────────────────────────────

CREATE TABLE orders (
    order_id            TEXT PRIMARY KEY,           -- DV-2026-104471
    customer_id         TEXT NOT NULL REFERENCES customers (customer_id), -- who ordered
    branch_id           TEXT REFERENCES branches (branch_id),             -- fulfilling branch
    address_id          TEXT REFERENCES addresses (address_id),           -- delivery destination
    prescription_id     TEXT REFERENCES prescriptions (prescription_id),  -- attached Rx if needed
    placed_at           TEXT NOT NULL,              -- ISO timestamp the order was created
    channel             TEXT NOT NULL               -- app, web, phone, walk_in or whatsapp
        CHECK (channel IN ('app', 'web', 'phone', 'walk_in', 'whatsapp')),
    fulfilment_type     TEXT NOT NULL               -- express_1hr, same_day, next_day, courier, pickup
        CHECK (fulfilment_type IN ('express_1hr', 'same_day', 'next_day', 'courier', 'pickup')),
    status              TEXT NOT NULL               -- lifecycle state; drives what the agent says
        CHECK (status IN ('placed', 'awaiting_prescription', 'confirmed', 'packed',
                          'dispatched', 'out_for_delivery', 'delivered',
                          'cancelled', 'returned', 'refunded')),
    payment_method      TEXT NOT NULL               -- cod, card_online, wallet, easypaisa, jazzcash, bank_transfer
        CHECK (payment_method IN ('cod', 'card_online', 'wallet', 'easypaisa',
                                  'jazzcash', 'bank_transfer')),
    payment_status      TEXT NOT NULL               -- pending, paid, failed, refunded, partially_refunded
        CHECK (payment_status IN ('pending', 'paid', 'failed', 'refunded', 'partially_refunded')),
    subtotal_pkr        REAL NOT NULL,              -- sum of line totals before discount
    discount_pkr        REAL NOT NULL DEFAULT 0,    -- promo and loyalty discount applied
    delivery_fee_pkr    REAL NOT NULL DEFAULT 0,    -- 0 above the free-delivery threshold
    total_pkr           REAL NOT NULL,              -- what the customer actually pays
    promo_code          TEXT,                       -- code used, if any
    promised_by         TEXT,                       -- ISO timestamp the customer was promised
    delivered_at        TEXT,                       -- ISO timestamp of successful delivery
    cancelled_reason    TEXT,                       -- why it was cancelled, quoted to the caller
    requires_cold_chain INTEGER NOT NULL DEFAULT 0, -- 1 if any line item needs a cold box
    notes               TEXT                        -- free-text operational note
);
CREATE INDEX idx_orders_customer ON orders (customer_id, placed_at DESC);
CREATE INDEX idx_orders_status ON orders (status);
CREATE INDEX idx_orders_placed ON orders (placed_at DESC);

CREATE TABLE order_items (
    order_item_id       TEXT PRIMARY KEY,           -- DV-2026-104471-1
    order_id            TEXT NOT NULL REFERENCES orders (order_id), -- parent order
    sku                 TEXT NOT NULL REFERENCES products (sku),    -- item ordered
    quantity            INTEGER NOT NULL,           -- packs ordered
    unit_price_pkr      REAL NOT NULL,              -- price AT ORDER TIME, not today's price
    line_total_pkr      REAL NOT NULL,              -- quantity times unit price
    substituted_from    TEXT REFERENCES products (sku), -- original SKU if swapped at packing
    item_status         TEXT NOT NULL DEFAULT 'fulfilled' -- fulfilled, out_of_stock, substituted, returned
        CHECK (item_status IN ('fulfilled', 'out_of_stock', 'substituted', 'returned'))
);
CREATE INDEX idx_order_items_order ON order_items (order_id);

CREATE TABLE deliveries (
    delivery_id         TEXT PRIMARY KEY,           -- DLV-104471
    order_id            TEXT NOT NULL UNIQUE REFERENCES orders (order_id), -- one delivery per order
    rider_id            TEXT REFERENCES riders (rider_id), -- own rider, null for courier
    courier             TEXT,                       -- DVAGO Rider, TCS, Leopards, M&P, Trax
    tracking_number     TEXT,                       -- courier tracking reference
    status              TEXT NOT NULL               -- awaiting_pickup through delivered or failed
        CHECK (status IN ('awaiting_pickup', 'picked_up', 'in_transit',
                          'out_for_delivery', 'delivered', 'failed', 'returned')),
    dispatched_at       TEXT,                       -- ISO timestamp it left the branch
    eta                 TEXT,                       -- ISO timestamp currently expected
    delivered_at        TEXT,                       -- ISO timestamp handed over
    attempt_count       INTEGER NOT NULL DEFAULT 0, -- failed attempts so far
    failure_reason      TEXT,                       -- why the last attempt failed
    delivery_otp        TEXT,                       -- handover code; never read this aloud
    cold_box_used       INTEGER NOT NULL DEFAULT 0, -- 1 if carried in a cold box
    proof_ref           TEXT                        -- storage key of the delivery photo
);
CREATE INDEX idx_deliveries_order ON deliveries (order_id);
CREATE INDEX idx_deliveries_status ON deliveries (status);

-- ── Aftercare ────────────────────────────────────────────────────────────

CREATE TABLE complaints (
    complaint_id        TEXT PRIMARY KEY,           -- CMP-2026-0042
    customer_id         TEXT NOT NULL REFERENCES customers (customer_id), -- complainant
    order_id            TEXT REFERENCES orders (order_id), -- order complained about, if any
    category            TEXT NOT NULL               -- routing key; drives SLA and team
        CHECK (category IN ('late_delivery', 'wrong_item', 'missing_item',
                            'damaged_packaging', 'expired_product', 'refund_delay',
                            'rider_behaviour', 'price_dispute', 'prescription_rejected',
                            'cold_chain_breach', 'app_issue', 'out_of_stock_after_order',
                            'other')),
    summary             TEXT NOT NULL,              -- one line, English, for the ticket queue
    summary_ur          TEXT,                       -- the caller's own words in Urdu
    channel             TEXT NOT NULL               -- where it came from; voice_agent is this system
        CHECK (channel IN ('phone', 'app', 'whatsapp', 'email', 'walk_in', 'voice_agent')),
    priority            TEXT NOT NULL               -- low, medium, high or critical
        CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    status              TEXT NOT NULL               -- open, in_progress, awaiting_customer, resolved, closed
        CHECK (status IN ('open', 'in_progress', 'awaiting_customer', 'resolved', 'closed')),
    raised_at           TEXT NOT NULL,              -- ISO timestamp the ticket was opened
    sla_due_at          TEXT,                       -- ISO timestamp resolution is promised by
    assigned_to         TEXT,                       -- team or agent handling it
    resolved_at         TEXT,                       -- ISO timestamp of resolution
    resolution          TEXT,                       -- what was actually done
    csat_score          INTEGER CHECK (csat_score BETWEEN 1 AND 5) -- post-resolution rating
);
CREATE INDEX idx_complaints_customer ON complaints (customer_id, raised_at DESC);
CREATE INDEX idx_complaints_order ON complaints (order_id);
CREATE INDEX idx_complaints_status ON complaints (status);

CREATE TABLE refunds (
    refund_id           TEXT PRIMARY KEY,           -- REF-2026-0071
    order_id            TEXT NOT NULL REFERENCES orders (order_id), -- order being refunded
    complaint_id        TEXT REFERENCES complaints (complaint_id),  -- ticket that triggered it
    amount_pkr          REAL NOT NULL,              -- rupees to be returned
    method              TEXT NOT NULL               -- original_payment, wallet, bank_transfer, cash
        CHECK (method IN ('original_payment', 'wallet', 'bank_transfer', 'cash')),
    status              TEXT NOT NULL               -- requested, approved, processing, completed, rejected
        CHECK (status IN ('requested', 'approved', 'processing', 'completed', 'rejected')),
    initiated_at        TEXT NOT NULL,              -- ISO timestamp the refund was raised
    completed_at        TEXT,                       -- ISO timestamp the money landed
    expected_by         TEXT,                       -- the date the agent is allowed to quote
    reference_no        TEXT,                       -- bank or gateway reference
    reason              TEXT                        -- why the refund was granted
);
CREATE INDEX idx_refunds_order ON refunds (order_id);

-- ── Commercial ───────────────────────────────────────────────────────────

CREATE TABLE promotions (
    promo_code          TEXT PRIMARY KEY,           -- code the caller reads out, e.g. DVAGO15
    title               TEXT NOT NULL,              -- short name of the offer
    description         TEXT NOT NULL,              -- terms in one sentence
    discount_type       TEXT NOT NULL               -- percent, flat or free_delivery
        CHECK (discount_type IN ('percent', 'flat', 'free_delivery')),
    discount_value      REAL NOT NULL,              -- percentage points or rupees, per type
    min_order_pkr       REAL NOT NULL DEFAULT 0,    -- basket must reach this to qualify
    max_discount_pkr    REAL,                       -- cap on a percentage discount
    valid_from          TEXT NOT NULL,              -- ISO date the offer opens
    valid_to            TEXT NOT NULL,              -- ISO date the offer closes
    applies_to_category TEXT,                       -- null means the whole catalogue
    bank_partner        TEXT,                       -- card promos in Pakistan are bank-tied
    first_order_only    INTEGER NOT NULL DEFAULT 0, -- 1 if new customers only
    usage_limit         INTEGER,                    -- total redemptions allowed
    times_used          INTEGER NOT NULL DEFAULT 0, -- redemptions so far
    is_active           INTEGER NOT NULL DEFAULT 1  -- 0 disables regardless of dates
);
CREATE INDEX idx_promotions_active ON promotions (is_active, valid_to);

CREATE TABLE refill_subscriptions (
    subscription_id     TEXT PRIMARY KEY,           -- SUB-000045
    customer_id         TEXT NOT NULL REFERENCES customers (customer_id), -- subscriber
    sku                 TEXT NOT NULL REFERENCES products (sku),          -- item on repeat
    quantity            INTEGER NOT NULL DEFAULT 1, -- packs per refill
    frequency_days      INTEGER NOT NULL,           -- days between refills
    next_refill_on      TEXT NOT NULL,              -- ISO date the next one is due
    last_ordered_on     TEXT,                       -- ISO date of the previous refill
    status              TEXT NOT NULL               -- active, paused or cancelled
        CHECK (status IN ('active', 'paused', 'cancelled')),
    reminder_channel    TEXT NOT NULL DEFAULT 'sms' -- sms, whatsapp, call or app_push
        CHECK (reminder_channel IN ('sms', 'whatsapp', 'call', 'app_push')),
    created_on          TEXT NOT NULL               -- ISO date the subscription started
);
CREATE INDEX idx_refills_customer ON refill_subscriptions (customer_id, status);
CREATE INDEX idx_refills_due ON refill_subscriptions (next_refill_on, status);
