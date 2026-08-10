# Ordering and delivery

Status: mixed. Delivery promise, free-delivery threshold and payment methods are
public (dvago.pk). Delivery fees, cut-off times and slot names are PLACEHOLDER —
replace with DVAGO's real figures before this agent takes a real call.

## How an order is placed

Browse the app or website, add to cart, check out with name, contact and
address, choose payment, and confirm. Prescription medicines need a
prescription uploaded before the order can ship.

Orders also arrive by phone, on WhatsApp, and at the counter.

## Delivery options

| Option | Where | Time |
|---|---|---|
| Express | Branches inside the one-hour zone | Within 1 hour (public) |
| Same day | Store cities, ordered before the evening cut-off | Same day |
| Next day | Store cities, ordered late | Next working day |
| Courier | Everywhere else in Pakistan | 2–3 working days |
| Pickup | Any branch supporting collection | When the caller arrives |

Only branches flagged for express can promise the hour. Never promise express
delivery without checking — `check_stock` and `find_branch` both report it.

## Delivery charges

Status: PLACEHOLDER except the free-delivery threshold.

- **Free delivery on orders above 5,000 rupees** (public).
- Express: 149 rupees.
- Same day and next day: 99 rupees.
- Courier to a non-store city: 249 rupees.
- Pickup: free.

## Payment

Cash on delivery and online card payment are both accepted (public). The demo
database also carries Easypaisa, JazzCash, wallet balance and bank transfer.

Discounts on medicines run up to 15%, and card promotions are usually tied to a
particular bank. Never assert that a promo code works — check it. Expired codes
are a common source of complaints.

## Cold-chain orders

Insulin, vaccines and other 2–8 °C items ship only from branches with a fridge,
in a cold box, with a rider carrying one. That can rule out express delivery
even when the item is in stock nearby. Say so before the caller finds out at
the door.

## What the agent must not do

- Promise a delivery time without checking the branch supports it.
- Quote stock from memory. Stock is a tool call, always.
- Take an order for a prescription medicine without saying a prescription is
  needed first.
