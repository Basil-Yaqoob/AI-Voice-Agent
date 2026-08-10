# Loyalty, promotions and repeat prescriptions

Status: mixed. The monthly refill service and "up to 15% off medicines" are public
(dvago.pk). Tier names, point values and wallet rules are PLACEHOLDER — replace
with DVAGO's real programme.

## Refill reminders

DVAGO offers a monthly refill service with reminders for repeat prescriptions.
The demo database tracks these per customer: which medicine, how often, and
when the next one is due.

Handle it like a pharmacist, not a salesperson. If a refill is due, mention it
**once, near the end of the call**, after the caller's actual question has been
answered. Opening with it turns a support call into a cold sale, and a diabetic
customer chasing a late insulin delivery will not thank anyone for it.

If the caller says no, note it and move on.

## Loyalty tiers

Status: PLACEHOLDER.

| Tier | Reached at |
|---|---|
| Bronze | Everyone |
| Silver | 5 orders |
| Gold | 12 orders |
| Platinum | 30 orders |

Points accrue on spend and can be redeemed at checkout. Tier does not change
what a medicine costs — DRAP sets those prices — it affects promotions and
delivery perks.

## Wallet

Store credit, usable at checkout. Refunds on cash-on-delivery orders can be
paid into it, which is faster than a bank transfer. Offer it as an option, do
not impose it.

## Promotions

Discounts on medicines run up to 15% (public). Card promotions are tied to a
specific bank, which is normal in Pakistan — a code that works on an HBL card
does nothing on a Meezan one.

Always check a code rather than guessing. `check_promotion` reports whether it
is live, what it applies to, the minimum basket, the bank if any, and whether
it is first-order-only. Expired codes are a common complaint and the wrong
answer here costs more than the discount.
