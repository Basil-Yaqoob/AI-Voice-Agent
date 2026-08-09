# TASK-13 — Demo runbook

| | |
|---|---|
| **Depends on** | all previous tasks |
| **Blocks** | nothing — this is the delivery |
| **Estimate** | 0.5 day prep + rehearsal |
| **Level** | All |

---

## Objective

Get from a working agent to a client meeting that ends in a yes.

The engineering is done by TASK-12. This task is about the twenty minutes where it either lands or doesn't — and those twenty minutes fail for reasons that have nothing to do with code quality: venue wifi, echo, an unrehearsed hard question, or a client who cannot tell what the system understood.

---

## Pre-demo checklist

### One week before

- [ ] `make eval` run; model and voice chosen from the report, not from preference
- [ ] Native speaker has heard the agent and signed off on the voice
- [ ] Knowledge base loaded with the **client's own content**, not placeholders
- [ ] Cache read tokens confirmed non-zero (TASK-11)
- [ ] p95 first-audio measured and under 800ms from the demo location

### One day before

- [ ] Full run-through on the actual demo laptop
- [ ] Test on the venue wifi if possible; if not, **prepare a phone hotspot as backup**
- [ ] Pre-rendered audio assets present and playing
- [ ] Voice switcher works mid-call
- [ ] `/readyz` green on all dependencies
- [ ] Provider account balances checked — a demo dying on a spent credit is avoidable
- [ ] Fallback recording produced: a 90-second screen capture of a good session

That last item is the single highest-value insurance policy here. If the network fails, you show the recording and keep the meeting. Without it, a wifi problem ends the conversation.

### One hour before

- [ ] Services up, `make dev`, `/readyz` green
- [ ] One live test call end to end
- [ ] **Headset connected** — see echo warning below
- [ ] Browser zoom set so the transcript is readable from across a table
- [ ] Notifications silenced, other tabs closed

---

## The echo problem — read this one

The most common demo failure has nothing to do with the agent: **on laptop speakers, the agent hears its own voice and interrupts itself in a loop.** It looks catastrophically broken.

Three defences, use all of them:

1. **Wear a headset.** Fully eliminates the path. Non-negotiable if you can manage it.
2. **Echo cancellation on** in the browser audio constraints (TASK-10).
3. **If you must use speakers,** turn the volume down and sit close to the mic.

Test this in the actual room before the client arrives. Room acoustics vary enormously and a laptop that behaved in your office can loop in a glass meeting room.

---

## Demo script (12 minutes)

### 1. Frame it before connecting (1 min)

State the constraint you built to, before they hear anything:

> "This runs the full pipeline — speech recognition, reasoning, and speech synthesis — in under 800 milliseconds. That's the threshold where a conversation stops feeling like you're talking to a machine. You'll see the number on screen for every turn."

Setting the bar first means they evaluate against your metric rather than a vague impression.

### 2. Simple turn (2 min)

An easy question in Urdu. Point at the transcript and the latency figure. Let them see the code-switching in text — half the value is in the understanding, and they cannot hear understanding.

### 3. The code-switched turn (2 min)

The realistic register: Urdu grammar, English nouns.

> "میرا order abhi tak deliver nahi hua, tracking check kar dein"

Then say the quiet part out loud:

> "This is how your customers actually talk. Most Urdu voice agents are built for formal Urdu and fall apart here — they mishear exactly the English words that carry the meaning."

This is your differentiator. Name it explicitly; the client has likely seen demos that failed at precisely this point and will recognise the description.

### 4. The number readback (1 min)

Have it read back an amount or an order number. This is where competing systems produce a nonsense multi-crore figure from a phone number. If yours reads it correctly, say so — the client will not know it was hard unless you tell them.

### 5. Barge-in (2 min)

Interrupt mid-sentence. It should stop immediately and respond to the new input.

> "Nothing else demonstrates that this is real-time rather than pre-scripted. Cheap demos cannot do this."

### 6. Voice A/B (2 min)

Switch voices live and let them choose. This converts the weakest part of the stack — a genuinely thin market with only two real options — into a moment where they feel ownership. They will remember choosing their voice.

### 7. Close on the roadmap (2 min)

Be straight about what is demonstrated and what is next:

> "What you've heard is the complete conversational engine, on the web. Putting it on a +92 number is a separate track — Pakistani voice numbers run through a licensed operator, and that's a contracting timeline rather than an engineering one. We start that in parallel from week zero. WhatsApp voice can go live before the phone line does, and reaches most of your customer base."

Being upfront about the telephony lead time builds far more credibility than discovering it in week five. It also positions WhatsApp as an early win rather than a fallback.

---

## Questions you will be asked

Rehearse these. Being caught out on one undoes the demo.

**"Can it handle Punjabi / Pashto / Sindhi?"**
> Not today. Urdu and English are well covered; regional language support for speech recognition and synthesis is much thinner and would need separate evaluation. We can scope it as a phase two.

**"What happens when it doesn't know the answer?"**
> It says so and offers a handoff, rather than inventing something. Demonstrate this — ask an out-of-scope question. A graceful "I don't know" is more reassuring to a buyer than a system that always answers.

**"How much per call?"**
> Roughly PKR 17–22 per minute all-in at current rates, depending on model tier. Show the table from `RESEARCH.md`. Volume commitments on speech synthesis reduce it meaningfully. Be specific — vagueness here reads as evasion.

**"Is this legal in Pakistan?"**
> Inbound and WhatsApp, yes, straightforwardly. Outbound automated calling engages telemarketer registration and the national Do-Not-Call register, and we build that compliance in from the first version rather than retrofitting it. The +92 number itself comes through a licensed operator.

**"Can it sound like a specific person / our brand voice?"**
> Custom voice is possible via voice cloning, which needs a voice artist and lead time. Scope separately.

**"How is this different from what we've already been shown?"**
> Three things: the latency number on screen, code-switching that actually works, and honest telephony timelines. Most demos fail one of the three.

---

## Failure recovery

| Failure | Recovery |
|---|---|
| Wifi drops | Phone hotspot; if that fails, play the recording |
| Agent hears itself | Headset, or drop speaker volume immediately |
| A turn hangs | Reload the page, keep talking — a single hang is survivable if you don't panic |
| Wrong answer | Own it: "that's a knowledge-base gap, not a model problem — here's how we'd fix it" |
| Latency spike | Point at the number, name the cause, move on. Hiding it reads worse than explaining it. |
| Total failure | Recording, then walk the architecture. Never improvise a live fix in front of the client. |

The general rule: **narrate problems rather than hiding them.** A visible, explained failure reads as competence. A frozen screen with a silent presenter reads as a broken product.

---

## Acceptance criteria

- [ ] Full script rehearsed end to end at least twice
- [ ] Rehearsed on the actual demo hardware and, if possible, the venue network
- [ ] Fallback recording produced and playable offline
- [ ] All six anticipated questions answerable without hesitation
- [ ] Cost table printed or on a slide
- [ ] Echo tested in the actual room
- [ ] Hotspot backup configured and tested
- [ ] Provider balances confirmed sufficient
- [ ] Knowledge base contains real client content
- [ ] A native Urdu speaker has heard the final configuration

---

## After the demo

- [ ] Export the session transcripts and latency metrics — send the numbers as follow-up
- [ ] Record which voice they chose, and lock it in
- [ ] Note every question you could not answer well; those are your next tasks
- [ ] If they are proceeding: start the carrier conversation **that week**. It is the long pole and it does not start itself.
