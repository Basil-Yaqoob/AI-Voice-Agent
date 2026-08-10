# TASK-14 — DVAGO demo data and live database

| | |
|---|---|
| **Level** | Intermediate |
| **Estimate** | 1 day |
| **Depends on** | TASK-08 (pipeline), TASK-10 (demo UI) |
| **Status** | Built, 10 August 2026 |

Gives the agent a client-shaped business to answer questions about: a
spreadsheet the client can edit, a SQLite database built from it, and eight
tools that read it live.

---

## The one thing to be clear about

**Every row is synthetic.** DVAGO is a real company. None of these customers,
orders, complaints, refunds or stock figures are theirs. Brands, manufacturers,
cities and areas are real so the demo is recognisable — a caller asking for
"Panadol" and getting "I don't know that product" tells you nothing about
whether the agent works.

The database says so about itself (`dataset_meta.synthetic = 'yes'`), the
workbook says so on its first sheet, and a test asserts it stays that way.

The policy corpus is a separate warning. Four of the five files in
`knowledge/dvago/` are partly or wholly **placeholder** — DVAGO's real return
and refund policies render client-side and could not be read. They are marked
`Status: PLACEHOLDER`. Replace them before a real caller hears this, or the
agent will confidently commit the client to terms they never agreed to.

---

## Where the answers come from

The split is by **rate of change**, not by subject.

| Layer | Holds | Why there |
|---|---|---|
| **Cached prompt prefix** | Policy, delivery rules, hours, persona, tool list | Same for every caller. Cache reads bill at 0.1× and cost nothing on the critical path. |
| **SQLite, behind a tool** | Stock, orders, prices, complaints, refunds, promos | Differs between two calls an hour apart. |
| **pgvector retrieval** | Nothing, today | The corpus fits in the prefix. Retrieval would be strictly worse. |

Measured: the corpus is ~2,570 tokens and the assembled prefix ~3,940 — well
inside the 40,000-token ceiling and comfortably over the 512-token cache
minimum. `plan_for_corpus()` reports `cached_prefix` at startup and logs why.

**So this deployment does not use RAG.** That is the right answer, not a
shortcut: a retrieval round trip costs 150–250 ms that the 800 ms budget has
nowhere to put, to answer questions the cached prefix already answers for a
tenth of the token price. The pgvector machinery stays built and tested for
when DVAGO hands over their full policy library and the corpus outgrows the
prefix. At that point `make ingest` starts mattering and nothing else changes.

---

## The pipeline

```
scripts/dvago_catalogue.py     seed data: real brands, areas, prices
            │
            ▼
scripts/generate_dvago_dataset.py    →  data/dvago/DVAGO_demo_dataset.xlsx
            │                               (17 sheets, ~6,350 rows)
            │                            ← the client edits this
            ▼
scripts/build_dvago_db.py            →  data/dvago/dvago.db
            │                               validates, then writes
            ▼
agent.data.sqlite.Database           →  async, thread-pooled
            │
            ▼
agent.tools.dvago_backend            →  the eight tools
```

```bash
make dataset    # regenerate the spreadsheet   (overwrites hand edits)
make db         # load the spreadsheet         (safe; run after editing)
make data       # both
```

`src/agent/data/schema.sql` is the single source of truth. The spreadsheet, the
loader and the data dictionary all derive from it, and tests enforce the
agreement — adding a column to the workbook without adding it to the DDL fails
the build rather than silently corrupting a load.

### Deterministic, but never stale

The seed is fixed, so the same `--as-of` date always produces byte-identical
content. Dates are generated *relative* to that date, so regenerating tomorrow
keeps every row and identifier the same while "out for delivery" stays out for
delivery. A demo that returns different data on each run cannot be rehearsed.

---

## The schema

Seventeen tables, 216 columns, every column documented in the DDL and surfaced
in the workbook's `_data_dictionary` sheet with its type, key, constraint,
default, description and an example value.

| Group | Tables |
|---|---|
| Network | `branches`, `pharmacists`, `riders` |
| Catalogue | `products`, `substitutes`, `inventory` |
| People | `customers`, `addresses` |
| Orders | `prescriptions`, `orders`, `order_items`, `deliveries` |
| Aftercare | `complaints`, `refunds` |
| Commercial | `promotions`, `refill_subscriptions` |
| Provenance | `dataset_meta` |

One view carries real weight:

```sql
CREATE VIEW v_available_stock AS
SELECT ..., MAX(i.qty_on_hand - i.qty_reserved, 0) AS qty_available ...
```

Every stock answer goes through it. Reading `qty_on_hand` directly would
promise the caller a box that is sitting in someone else's packed order.

---

## The eight tools

Eight, and the count is deliberate: each definition is bytes in the cached
prefix and one more thing for the model to choose wrongly between.

| Tool | Answers |
|---|---|
| `find_medicine` | "Do you have X, what does it cost?" |
| `check_stock` | "Is it available near me?" — with substitutes when it is not |
| `find_branch` | "Where is your nearest store, are you open?" |
| `get_order_status` | "Where is my order?" — delivery, ETA and dropped items folded in |
| `get_customer_profile` | Who is calling, allergies, refills due |
| `get_complaint_status` | "What happened to my complaint?" |
| `file_complaint` | Opens a real ticket, returns the reference |
| `check_promotion` | "Does this code still work?" |

Delivery tracking is inside `get_order_status` because "where is my order" is
one question, not two.

### Three properties that are not left to the model

**Caller identity is bound, never passed.** `caller_phone` is fixed when the
backend is constructed — from the SIP INVITE in production. No tool takes a
phone number as an argument, so a caller cannot talk the agent into reading out
someone else's orders by reciting their number, and an order looked up by
number is refused if it belongs to a different account. A test asserts no tool
schema ever grows a phone parameter.

**Allergies are checked in the query.** If the caller's record says penicillin
and they ask for Augmentin, the tool result *opens* with the warning. A model
asked to cross-reference two separate tool calls will usually notice, and
"usually" is the wrong standard when the answer is a medicine.

**The delivery OTP is never returned.** It is in the row. It is not ours to
read out.

### What the tools are careful about

- Out of stock returns *what to do next* — nearer branches, then substitutes
  with the pharmacist's caveat attached, and an explicit instruction not to
  recommend a switch on the agent's own authority.
- Stock in another city says "courier, two to three working days", never "we
  have it".
- Cold-chain items are only ever offered from branches with a fridge.
- A category the model invents falls back to `other` rather than failing a
  CHECK constraint mid-call.
- Misheard brand names still match: `products.search_terms` carries the
  misspellings and Urdu spellings a transcript actually produces.

---

## Anchored demo paths

Roughly a dozen records are hand-written with memorable identifiers so specific
calls can be rehearsed to the word. The generated bulk exists so those anchors
sit in a realistic population — a stock query returning the only two rows in
the database proves nothing.

| Say this | Caller | What it demonstrates |
|---|---|---|
| "Panadol hai?" | 0300-1234567 | The easy yes: in stock at their own branch |
| "Augmentin chahiye" | 0300-1234567 | **Allergy warning fires**, then nearby stock |
| "Nan Pro milega?" | 0300-1234567 | Out across Karachi → substitutes with a caveat |
| "Mera order kahan hai?" | 0300-1234567 | `DV-2026-104471`, out for delivery, named rider, ETA |
| "Insulin hai?" | 0300-1234567 | Cold chain: only fridge branches offered |
| "Mera order kyun ruka hai?" | 0321-9876543 | Prescription pending review, with the doctor's name |
| "Refund kab aayega?" | 0333-1122334 | `REF-2026-0071` processing, expected date from the record |
| "HBL20 lagao" | any | Expired promo, refused with the date |
| "Peshawar deliver karte ho?" | 0345-1239876 | Courier city: days, not an hour |
| anything | 0300-9999999 | Unregistered number → new-customer path |

`0300-9999999` is deliberately absent from `customers` and a test keeps it that
way.

---

## Latency

Tool calls sit inside the 300 ms LLM slice, so they were measured rather than
assumed:

| Tool | Warm |
|---|---:|
| `get_customer_profile` | 0.6 ms |
| `check_stock` | 1.7 ms |
| `get_order_status` | 1.8 ms |
| `check_promotion` | 0.3 ms |

Two things make that true. `Database.warm()` opens a connection on every pool
thread at startup — connecting costs ~100 ms of file open, WAL journal and
pragmas, and paying it inside the first tool call would spend a third of the
turn budget on setup. And every column a tool filters on is indexed; a query
slower than 25 ms logs a `slow_query` warning naming the SQL.

`SIMULATED_API_LATENCY_S = 0.09` is added on top on purpose. The client's real
systems will not answer in a millisecond, and a demo where tools are free hides
both the pause and the need for a filler line.

---

## Acceptance criteria

- [x] Workbook with one sheet per table, plus `_README` and `_data_dictionary`
- [x] ~6,350 rows across 17 tables, deterministic given `--as-of`
- [x] Loader validates types, CHECK constraints, primary keys and every foreign
      key, reports all problems at once, and refuses to write on any
- [x] Build is atomic — staged then moved, so a failure leaves the old database
- [x] Async database access; the event loop is never blocked
- [x] Eight tools, all under 2 ms warm
- [x] Caller identity bound; another caller's records are refused
- [x] Allergy warnings originate in the query, not the prompt
- [x] Corpus measured and confirmed to fit the cached prefix
- [x] Deepgram keyterms carry the drug names callers actually say

## Tests

`tests/unit/test_dvago_schema.py` (18) — the DDL parses, executes, agrees with
SQLite column-for-column, documents every column, and indexes every lookup.

`tests/unit/test_sqlite_adapter.py` (16) — the loop keeps ticking during a
query, connections are never shared, foreign keys are on, failed writes roll
back, close leaves no threads behind.

`tests/unit/test_dvago_backend.py` (60) — the three safety properties, plus
misheard names, reserved stock, closed branches, courier cities, the OTP, and
every tool surviving empty arguments.

`tests/unit/test_dvago_dataset.py` (23) — skipped without the artifacts;
otherwise referential integrity, arithmetic, and each anchor being where the
runbook says.

## Common pitfalls

- **Running `make dataset` before a demo.** It regenerates the population and
  overwrites hand edits. Run `make db` alone after editing the workbook.
- **Reading `qty_on_hand`.** Use `v_available_stock`. Reserved stock is not
  available stock.
- **Setting `PRAGMA foreign_keys` at build time only.** It is per connection.
  Set it at build and it is off again on the next connect, and every bad write
  passes silently.
- **`PRAGMA journal_mode = WAL` from several threads at once.** It takes an
  exclusive lock that `busy_timeout` does not cover — SQLite returns
  `SQLITE_BUSY` immediately instead of waiting. Connection setup is serialised
  for exactly this reason.
- **Adding a phone parameter to a tool.** It undoes the whole identity design.
- **Shipping the placeholder policies.** They read as real. That is the problem.

## Handing over to the client's real systems

Replace `agent/tools/dvago_backend.py` with calls to DVAGO's own APIs, one
handler at a time. The tool names, schemas and result shapes stay; the agent,
the prompt and the cached prefix do not change. `agent/data/` can then be
deleted, and an import-linter contract already guarantees it knows nothing
about DVAGO to begin with.
