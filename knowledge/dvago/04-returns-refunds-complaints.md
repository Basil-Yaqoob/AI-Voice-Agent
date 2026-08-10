# Returns, refunds and complaints

Status: **PLACEHOLDER — the whole file.** DVAGO publishes a return policy and a
refund policy at dvago.pk/policies, but those pages render client-side and the
text could not be read automatically. Everything below is a plausible pharmacy
policy, not DVAGO's. **Replace it before this agent takes a real call.** An
agent quoting a return window the client does not offer creates a commitment
somebody has to honour or argue about.

## Returns

- Report a problem within **48 hours** of delivery.
- Returnable: wrong item, damaged packaging, an item missing from the box, a
  product close to expiry, a cold-chain item that arrived warm.
- Not returnable once accepted, on pharmacy safety grounds: opened medicines,
  refrigerated items that left the cold chain in the customer's care, and
  anything on a controlled schedule.
- The rider collects the return; the customer does not post it back.

## Refunds

- Card and wallet payments go back to the original method.
- Cash-on-delivery orders are refunded to the DVAGO wallet or by bank transfer.
- Expect **5 to 7 working days**, and a bank transfer can take two or three
  more to appear on a statement.
- Never quote a refund date from memory. `get_order_status` returns the
  recorded expected date; use that one.

## Complaints

Anything the caller is unhappy about becomes a ticket, and the reference number
gets read back to them. Priority follows the category:

| Category | Priority | Response |
|---|---|---|
| Cold-chain breach | Critical | 24 hours |
| Expired product, wrong item, late delivery | High | 24 hours |
| Everything else | Medium | 72 hours |

Check for an existing ticket before filing a new one — a caller chasing a
complaint does not want a second reference number, they want the first one
resolved.

## Escalating to a person

Transfer to a colleague when the caller asks for one, when the question is
clinical, when the account is suspended, or when the agent has said "I don't
know" once already. Say plainly that a colleague will take over. A caller told
"مجھے معلوم نہیں، میں آپ کو ایک colleague سے connect کر دیتا ہوں" trusts the
service more than one given a confident wrong answer.
