# Prescriptions and controlled medicines

Status: mixed. The upload requirement and the pharmacist review are public
(dvago.pk/prescription). Validity period, review turnaround and the controlled-item
rules below are PLACEHOLDER — confirm against DVAGO's actual SOP and Pakistani
regulation before this agent takes a real call.

## The rule

Prescription medicines cannot be dispatched until a prescription has been
uploaded and a pharmacist has verified it. The agent says this **before** the
caller has picked a payment method, not after.

Uploads go through the app, the website, or WhatsApp.

## Review

A pharmacist checks each upload. The order sits at `awaiting_prescription`
until then and moves to packing straight after verification. Typical review is
a few hours during working hours.

Rejections happen for real reasons and the reason is on the record — an
illegible signature, a missing doctor's stamp, an expired prescription. Read
the actual reason out. "It was rejected" without a reason means the caller
uploads the same image again.

A prescription is treated as valid for **90 days** from the date of issue.

## Controlled medicines

Some items are on a narcotic or psychotropic schedule. For these:

- Quantity per order is capped, usually at two packs.
- The original prescription must be shown to the rider at handover, not just
  uploaded.
- They are never left with a guard, a neighbour, or at reception.

The demo catalogue flags these; `find_medicine` reports it.

## What the agent must not do

- Suggest that a prescription requirement can be worked around.
- Recommend switching to a different medicine. The agent may *read out* the
  listed alternatives with the pharmacist's note attached, and must say a
  pharmacist will confirm. It never says "take this instead".
- Advise on dosage, timing, interactions or suitability. That goes to a
  pharmacist, 9am to 6pm.
- Ignore an allergy on the caller's record. The tool result leads with the
  warning; say it first, and do not take the order.
