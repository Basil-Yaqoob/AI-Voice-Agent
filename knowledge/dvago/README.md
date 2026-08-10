# DVAGO knowledge corpus

What the agent knows without asking the database.

## Real versus placeholder

Every file below carries a `Status:` line at the top. There are two kinds:

- **`Status: public`** — taken from dvago.pk, the app store listings or the
  published helpline. Verified as of 10 August 2026.
- **`Status: PLACEHOLDER`** — invented. Modelled on how a Pakistani pharmacy
  chain normally operates, but not DVAGO's actual terms.

**The placeholders must be replaced with the client's real policy text before
any customer hears this agent.** They are plausible, which is exactly what
makes them dangerous: an agent that confidently quotes a seven-day return
window DVAGO does not offer creates a commitment the client has to honour or
argue about. Get the real wording, paste it in, delete the `Status` line.

The fastest way to collect it is to ask DVAGO for the text behind
`dvago.pk/faqs`, `dvago.pk/policies/return-policy` and
`dvago.pk/policies/refund-policy` — those pages render client-side and could
not be read automatically.

## Why this is not in the database

The split is by *rate of change*, not by subject.

| Where | What | Why |
|---|---|---|
| Cached prompt prefix (these files) | Policy, hours, delivery rules, tone | Same for every caller. Cached at 0.1× input cost, zero latency. |
| SQLite, behind a tool | Stock, orders, complaints, prices, refunds | Differs between two calls an hour apart. |
| pgvector retrieval | Nothing, today | The whole corpus fits in the prefix. See below. |

## Why there is no retrieval

`plan_for_corpus()` measures this directory at startup. At roughly 2,000
tokens it is far below the 40,000-token ceiling, so the correct answer is to
put all of it in the cached prefix: a cache read costs a tenth of an input
token and nothing on the critical path, while retrieval would add an embedding
call and a 150–250 ms database round trip that the 800 ms budget has no room
for.

The pgvector machinery already exists and is tested. It becomes the right
answer when DVAGO hands over their full policy library — product monographs,
the complete FAQ set, per-city terms — and this directory grows past the
ceiling. At that point `make ingest` starts mattering and nothing else changes.

Retrieval is the fallback for corpora too large to cache, not the default.
