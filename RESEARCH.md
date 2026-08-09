# Building a production Urdu voice agent that doesn't sound broken

**Technical research · Conversational AI · Pakistan market**

Architecture, vendor selection, regulatory path and full unit economics in PKR — for inbound, outbound and WhatsApp voice on a +92 number, in code-switched Urdu / English.

| | |
|---|---|
| **Compiled** | 8 August 2026 |
| **Sized for** | 5,000–50,000 min/month |
| **FX** | 1 USD = 278.6 PKR mid-market |

> Web version of this document: https://claude.ai/code/artifact/1ba0c6c7-d3bd-4592-9cae-79768e9f72f3

---

## Abstract

Almost every "AI voice agent" demo for the Pakistani market fails on the same three things: the voice sounds wrong in Urdu, the pause before the agent replies is too long, and there is no legal way to put it on a +92 number at scale. Model quality is rarely the problem. The stack is.

This document specifies a stack that clears all three, prices it per minute in rupees, and separates what you can stand up this week for a client demo from what you must have contractually in place before you take real traffic.

The headline conclusion is uncomfortable but useful: **the text-to-speech layer, not the language model, is the hardest constraint in this build**, and **the telephony layer, not the code, is the longest lead time**. Both need decisions before you write the agent.

---

## Findings that change the plan

### Finding 01 — ElevenLabs cannot do real-time Urdu

Flash v2.5 — the low-latency model everyone builds on — covers 32 languages and Urdu is not one of them. Urdu only arrived with Eleven v3, and v3 explicitly cannot run in real time. The most-recommended vendor in this space is unusable for your core voice.

### Finding 02 — Speech-to-text is solved

Deepgram shipped production Urdu on Nova-3 in February 2026 — streaming, right-to-left native, with keyterm prompting so you can bias it toward your product and brand names. This was the blocker a year ago and no longer is.

### Finding 03 — A +92 number needs a licensed partner

PTA rules are explicit that a CVAS data licence cannot carry voice. Voice on a Pakistani number runs through an LL or LDI licensee. You cannot self-serve this from Twilio or Telnyx the way you would a US number.

### Finding 04 — Code-switching is the real spec

Your callers do not speak Urdu. They speak Urdu with English nouns dropped in — *account*, *balance*, *delivery*, *complaint*. A monolingual Urdu pipeline mispronounces exactly the words that carry the meaning.

---

## The latency budget is the architecture

Below roughly **800 ms** from the caller finishing their sentence to the first syllable coming back, a conversation feels natural. At 1.5 s it feels laggy. Past 3 s callers assume the line dropped and start talking over the agent. That single number governs every vendor choice below, so allocate it explicitly rather than discovering it at the end.

A budget that holds in production for a Karachi caller with the agent hosted in Mumbai or Dubai:

| Stage | Budget | Notes |
|---|---:|---|
| Carrier + SIP ingress | 50 ms | Fixed by geography |
| Endpointing / turn detection | 180 ms | **Biggest tunable lever** |
| STT finalisation | 100 ms | Deepgram Nova-3 streaming |
| LLM time-to-first-token | 300 ms | The model budget |
| TTS time-to-first-byte | 110 ms | Azure `ur-PK` |
| Egress + jitter buffer | 60 ms | Fixed |
| **Total** | **800 ms** | Perceptual ceiling |

Three consequences fall straight out of that table:

- **Endpointing is your biggest single lever, and it is free.** 180 ms of the budget is just deciding the caller stopped talking. Tuning this costs nothing and buys more than any vendor swap. Urdu speakers pause mid-sentence more than English speakers do, so a naive VAD threshold will cut them off — tune it on real Pakistani call audio, not defaults.
- **RAG cannot sit on the critical path.** There is no room in the budget for a retrieval round-trip before the first token. Retrieval must run in parallel with endpointing, speculatively on partial transcripts.
- **You must stream everything, end to end.** Partial transcripts into the model, first sentence out of the model into TTS before the rest is generated, first audio chunk to the caller before synthesis finishes. Any component that waits for a complete input blows the budget on its own.

> ⚠️ **Region matters more than you think.** Railway has no India region — Singapore is its closest point of presence, which adds roughly 60–90 ms round trip versus Mumbai or Dubai for a Pakistani caller. That is 8–11% of your entire budget spent on geography. Railway is excellent for the control plane, the dashboard and the RAG indexer. Put the real-time media workers on something with a Mumbai or UAE region.

---

## Architecture

The important thing is the **boundary**. Everything upstream of the SIP gateway is regulated and must be contracted with a PTA licensee; everything downstream is yours to build and swap freely. Teams get into trouble by designing the downstream side first and discovering the upstream side has a two-month lead time.

```
   PTA-REGULATED — LICENSEE REQUIRED    │        YOUR INFRASTRUCTURE
                                        │
  ┌──────────┐                          │   ┌──────────────────┐
  │  Caller  │                          │   │ WhatsApp Calling │
  │ +92 mob. │                          │   │ (unregulated)    │
  └────┬─────┘                          │   └────────┬─────────┘
       │ PSTN                           │            │
       ▼                                │            │
  ┌──────────────────┐   SIP trunk      │            │
  │  LL/LDI licensee ├──────────────────┼──┐         │
  │  holds +92 DID   │                  │  │         │
  │  PTCL·Jazz·Multi │                  │  ▼         ▼
  └──────────────────┘                  │ ┌────────────────┐   RTP   ┌──────────────────────────────┐
                                        │ │  SIP gateway   ├────────►│      Agent worker            │
                                        │ │  LiveKit SIP   │         │  ┌─────┐ ┌─────┐ ┌─────┐     │
                                        │ │  Mumbai / UAE  │         │  │ STT ├►│ LLM ├►│ TTS │     │
                                        │ └────────────────┘         │  └─────┘ └─────┘ └─────┘     │
                                        │                            │  Nova-3   Claude  Azure ur-PK │
                                        │                            └───────┬──────────▲───────────┘
                                        │                        prefetch on │          │ off critical
                                        │                            partial │          │ path
                                        │                                    ▼          │
                                        │                            ┌──────────────────┴───┐
                                        │                            │  Retrieval sidecar   │
                                        │                            │  pgvector + cache    │
                                        │                            └──────────────────────┘
```

WhatsApp Calling reaches the same agent worker without crossing the regulated boundary at all — which is why it is the fastest route to a live agent.

---

## Urdu text-to-speech: a much shorter list than you'd expect

This is where the build lives or dies. Every vendor normally recommended for real-time voice agents, checked against current documentation on 8 August 2026:

| Vendor / model | Urdu | Real-time | List price | Assessment |
|---|---|---|---|---|
| **Azure Neural TTS `ur-PK`** | ✅ Yes | ✅ Yes | $16 / 1M ch | Two native Pakistani voices, `UzmaNeural` and `AsadNeural`. Genuine `ur-PK`, not Indian Urdu. Commitment tiers cut this to $7.50/1M. **The default choice.** |
| **Inworld TTS-2** | ⚠️ Experimental | ✅ Yes | $5–15 / 1M ch | Urdu listed but flagged experimental, and only on TTS-2 — not the 1.5 models. Supports voice design and cloning. Cheapest at scale. **Evaluate as primary, keep Azure as fallback.** |
| ElevenLabs Flash v2.5 | ❌ No | ✅ Yes | ~$50 / 1M ch | 32 languages, ~75 ms, Urdu absent. Excellent for the English side of a bilingual deployment; unusable for Urdu. |
| ElevenLabs v3 | ✅ Yes | ❌ No | ~$100 / 1M ch | 70+ languages including Urdu, best-sounding on this list. ElevenLabs state plainly v3 cannot run real time. Use to pre-render fixed prompts and IVR audio, never live turns. |
| Cartesia Sonic 3.5 | ❌ No | ✅ Yes | usage-tiered | Sub-90 ms across 42 languages, Hindi included, Urdu not. Relevant only via the Hindi route below. |
| Google Cloud TTS | ❌ No | — | — | No Urdu voices at all, in any tier, including Chirp 3 HD. Rule it out. |

### A workaround worth knowing — and its catch

Spoken Urdu and spoken Hindi are the same language, Hindustani, split by script and by formal register. That means you can transliterate Urdu into Devanagari and feed it to a first-rate Hindi voice — Cartesia, ElevenLabs Flash and Google all have excellent low-latency Hindi. For everyday service dialogue the output is close to indistinguishable to a Pakistani ear, and it unlocks better voices than `ur-PK` offers.

The catch is real. Transliteration between an abjad and an abugida is lossy, so it needs a maintained mapping table rather than a naive converter. Perso-Arabic vocabulary in formal registers will come out with Hindi phonology. And the accent is Indian, which some Pakistani customers notice and dislike.

**Treat it as a benchmark to beat and a fallback, not the plan.** Ship on `ur-PK` or Inworld; keep this in your back pocket if voice quality blocks the deal.

---

## Code-switching is a first-class requirement

Your callers will say things like this, and a system tuned for formal Urdu will handle it badly:

> میرا آرڈر ابھی تک ڈیلیور نہیں ہوا، آپ ٹریکنگ چیک کر کے بتا دیں

Urdu grammar, English content words. Four decisions follow:

- **Load Deepgram's keyterm prompting with your English vocabulary.** Nova-3 Urdu supports it, and it is the cheapest accuracy win available. Feed it your product names, brand terms, plan names and the English nouns your callers actually use. Without it, "delivery" and "tracking" inside an Urdu sentence get mangled — and those are the words the intent hinges on.
- **Write the system prompt in the register you want spoken.** Models mirror the register of their instructions. A prompt in formal literary Urdu produces stiff, over-Persianised replies that sound like a news bulletin. Write it the way a good Karachi call-centre agent actually talks, and include worked examples of correct code-switching.
- **Constrain the English that comes back.** Left alone the model will drift into full English sentences whenever the topic gets technical. Instruct it to keep English to the nouns callers themselves use and to stay in Urdu for everything structural.
- **Budget for the numbers problem.** Amounts, dates, order IDs and phone numbers are where Urdu TTS most often breaks — a bare digit string can come out in the wrong language or wrong grammatical form entirely. Normalise numbers to explicit Urdu words in a post-processing pass before they reach TTS, and test this specifically. It is the single most common source of "the demo sounded fine until it read the balance."

---

## RAG, when you have 300 ms to spend

The instinct is to reach for a vector database and a retrieval step before generation. On a voice call that ordering is wrong — a serial retrieve-then-generate loop eats the entire model budget before the model starts. Voice RAG has to be restructured, and for most company knowledge bases it should be partly avoided.

1. **Put the stable corpus in the prompt, not the index.** Company knowledge bases are usually smaller than teams assume. If the whole thing fits in tens of thousands of tokens, cache it as a prompt prefix and skip retrieval entirely for most turns. Cached reads bill at a tenth of input price, so this is cheaper *and* faster than retrieval — it removes a network hop from the critical path. Claude Opus 5 caches from 512 tokens up, which is low enough that even small policy documents qualify.
2. **Prefetch speculatively on partial transcripts.** Fire retrieval on the interim transcript while the caller is still finishing their sentence and while endpointing is still running. By the time you have a final transcript, results are already warm. This turns retrieval from a 200 ms tax into roughly zero.
3. **Cache whole answers, not just chunks.** Support calls are enormously repetitive — a small set of intents will cover the large majority of turns. A semantic cache keyed on normalised intent lets you skip retrieval and generation together for the common path.
4. **Prefer tools over retrieval for anything live.** Balances, order status and ticket state should be tool calls against your real systems, not documents in an index. Retrieval is for policy and product knowledge; anything that changes hourly belongs behind an API.
5. **Keep the store next to the worker.** `pgvector` on the same Postgres you already run is the right default at this volume. A separate managed vector service adds a network hop and monthly cost to buy scale you do not need below a few million chunks.

> **Index in both scripts.** Embed your knowledge base in Urdu *and* English, and index Roman-Urdu spellings of key terms too. A caller asking in code-switched speech produces a transcript that matches neither a purely Urdu nor a purely English chunk well. Dual indexing plus a keyword hybrid over product names fixes most retrieval misses in this setup.

---

## Getting onto a +92 number legally

This cannot be solved by writing better code, and it is the part most likely to delay the launch. Treat it as a parallel workstream starting on day one.

**The licensing position.** PTA's framework separates data value-added services from voice. A CVAS licence — the one a software company would naturally look at — explicitly does not permit carrying voice. Voice over IP in Pakistan runs through Local Loop or Long Distance International licensees, and an LL licensee can offer IP telephony only in conjunction with an LDI licensee. In practice this means **you partner rather than license**: a corporate SIP trunk from PTCL, a mobile operator, or an established local carrier, with the number allocated under their licence.

Separately, if you or your client operate a call centre in Pakistan, that entity needs registration with PSEB and PTA. Do not skip this because the agents are software rather than people — the registration attaches to the operation, not the headcount.

**Inbound is straightforward. Outbound is not.** Inbound to a number your partner holds carries little regulatory weight beyond the trunk contract. Outbound automated calling lands squarely inside the *Protection from Spam, Unsolicited, Fraudulent and Obnoxious Communication Regulations, 2009*: telemarketer registration, honouring the national Do-Not-Call Register (callers opt in by SMS to shortcode **3627**), and demonstrable consent records. Build DNC scrubbing and per-contact consent state into the dialler from the first version — retrofitting it after a complaint is far more expensive than building it now.

> 🛑 **The thing that gets people shut down.** Terminating international traffic into Pakistan over IP to bypass LDI settlement rates — "grey traffic" — is a serious offence and is actively enforced. This is not a compliance checkbox; it is the difference between a business and a prosecution. If any architecture proposal has calls originating outside Pakistan and landing on Pakistani mobiles without passing an LDI licensee, reject it outright. Get your carrier's licence position in writing before signing.

**Foreign DID providers are a partial answer.** Telnyx does list Pakistan numbers, with KYC, a letter of intent and proof of address dated within three months, at around $43 to start. That may serve for inbound testing. Do not assume it gives you compliant outbound calling to Pakistani mobiles at volume — confirm the routing and the licence chain with the provider before you build on it.

**WhatsApp is the underrated path.** The WhatsApp Business Calling API reaches essentially the whole market — WhatsApp is the default voice channel for a great many Pakistani consumers — and it sidesteps the PSTN question entirely. Inbound calls are free to both parties; outbound bills per minute with volume tiers. Because the caller already has a business relationship in the thread, answer rates beat cold PSTN dialling substantially. Given you want all three channels, **ship WhatsApp and inbound first, and let the outbound PSTN licensing work proceed in parallel.**

---

## What a conversation minute actually costs

Modelled on a realistic minute: the caller speaks about half the time, the agent produces roughly 450 characters of speech, and there are about four conversational turns. Prompt caching is assumed to be working properly, which changes the model line by a factor of three or more.

> ⚠️ **Budget at 295 PKR, not 278.6.** The mid-market rate today is **278.6 PKR/USD**, and the tables below use it so the arithmetic is checkable. Your actual landed cost is higher: bank FX markup on international card payments runs a few percent, and advance income tax on foreign card transactions applies on top — at a different rate depending on filer status. **Budget at roughly 295 PKR/USD and confirm the current tax rate with your bank**, because on a six-figure rupee monthly bill the gap is real money.

### Per-minute components

| Component | Service | USD / min | PKR / min |
|---|---|---:|---:|
| Speech-to-text | Deepgram Nova-3 streaming, Urdu | 0.0077 | 2.15 |
| Text-to-speech | Azure Neural `ur-PK`, 450 ch/min | 0.0072 | 2.01 |
| Media / orchestration | LiveKit agent session | 0.0100 | 2.79 |
| SIP bridging | LiveKit SIP | 0.0035 | 0.98 |
| Retrieval + compute | pgvector, workers, amortised | 0.0015 | 0.42 |
| **Fixed subtotal** | before model and carrier | **0.0299** | **8.33** |
| PK carrier | Local termination, planning range | 0.006–0.011 | 1.50–3.00 |

### Language model, per minute at four turns with caching active

| Model | Input / output per MTok | USD / min | PKR / min | Fit |
|---|---:|---:|---:|---|
| Claude Opus 5 | $5 / $25 | 0.041 | 11.42 | Strongest reasoning and the best code-switching register control. |
| Claude Sonnet 5 | $3 / $15 | 0.024 | 6.69 | Introductory $2/$10 runs to 31 Aug 2026 → ~4.5 PKR/min today. |
| Claude Haiku 4.5 | $1 / $5 | 0.008 | 2.23 | For deflection, routing and FAQ paths. |

Opus 5 fast mode raises output throughput up to 2.5× at premium pricing ($10/$50) — the one paid lever that directly buys latency headroom.

### All-in monthly

Including carrier midpoint and ~$150/month fixed platform charges (LiveKit Ship tier, hosting, DID rental, monitoring). Converted at 278.6 PKR/USD.

| Model tier | PKR / min | 5,000 min/mo | 20,000 min/mo | 50,000 min/mo |
|---|---:|---:|---:|---:|
| Opus 5 | 21.99 | 151,750 | 481,600 | 1,141,300 |
| Sonnet 5 | 17.26 | 128,100 | 387,000 | 904,800 |
| Haiku 4.5 | 12.80 | 105,800 | 297,800 | 681,800 |

### Where the savings are, in order of size

- **Commit on TTS.** Azure's commitment tiers take you from $16 to $7.50 per million characters — more than halving your second-largest variable line. Worth doing as soon as volume is predictable.
- **Route by complexity.** A classifier on turn one that sends simple FAQ traffic to Haiku and escalates the rest to Opus typically cuts the model line by well over half, because most turns in a support call are simple.
- **Audit your cache hit rate before anything else.** If `cache_read_input_tokens` is coming back zero, something in your prefix is changing per request — a timestamp, a session ID, a re-ordered tool list. This one bug can triple the model bill silently, and it is the first thing to check when costs look wrong.
- **Self-host LiveKit once volume justifies it.** The agent-session charge is your largest fixed per-minute line. LiveKit is open source; above roughly 50,000 minutes a month, running it yourself starts to beat the Cloud tier — at the cost of owning the operational burden.

---

## A convincing demo with almost no spend

Free tiers cover a demo comfortably. The one thing worth spending on is voice quality, because that is what the client judges in the first ten seconds.

- **Deepgram** — $200 in credit on signup, no card, no expiry. Roughly 12,000 minutes of transcription. Covers an entire pilot alone.
- **Azure Speech** — 500,000 characters per month free, indefinitely. At 450 characters a minute that is ~1,100 minutes of synthesised speech every month at zero cost.
- **LiveKit** — free Build tier for development and demos.
- **Anthropic** — pay-as-you-go; a demo's worth of turns costs a few dollars at most.
- **Inworld** — free tier for evaluating whether their experimental Urdu beats Azure's voices for your use case.

### Demo on the web, not the phone

Skip telephony for the demo entirely. A browser-based WebRTC widget removes the SIP trunk, the DID, the carrier contract and the PTA question, and it is *lower* latency than a phone call, so the agent shows better than it will on day one of production. Build the demo on LiveKit's WebRTC transport and swap in the SIP gateway later — the agent code does not change.

If the client insists on hearing it over a real phone, buy one cheap international DID and have them dial it. Explain plainly that production will use a +92 number and that the licensing work is a separate, already-scoped track. Do not improvise a Pakistani number for a demo.

### Three things to prepare that decide the meeting

1. **An A/B of the voices.** Record the same three sentences through Azure `ur-PK`, Inworld, and the Hindi-transliteration route. Let the client pick. This converts the weakest part of the stack into a moment where they feel ownership, and you learn which way to build.
2. **A barge-in moment.** Interrupt the agent mid-sentence and have it stop cleanly and respond. Nothing else demonstrates "this is real" as immediately, and it is exactly what cheap demos cannot do.
3. **One deliberately hard turn.** A code-switched question with an English product name and a number in the answer. Rehearse it, because it is where you would otherwise get caught — and handling it visibly is what separates you from the demos the client has already seen.

---

## Build order

Sequenced so the two long-lead items — carrier licensing and voice selection — start immediately and run underneath the engineering work.

| Week | Milestone | Detail |
|---|---|---|
| **0** | Start carrier conversation and voice bake-off | Approach two or three LL/LDI-licensed operators for SIP trunk quotes and written confirmation of their licence position. In parallel, run the three-way voice comparison and get a decision. Neither is blocked by code. |
| **1–2** | Working agent on WebRTC | LiveKit Agents, Deepgram Nova-3 with keyterm prompting, chosen TTS, Claude with a cached system prompt. Endpointing tuned against recorded Pakistani call audio. Target sub-800 ms and measure per component from the start. |
| **3** | Knowledge and tools | Ingest the corpus, dual-index in both scripts, wire tool calls for live data. Add speculative prefetch and the semantic answer cache. Confirm cache hit rates are non-zero. |
| **4** | WhatsApp channel live | First real traffic, on the channel with no regulatory dependency. Everything learned here transfers to the PSTN launch. |
| **5–6** | Inbound PSTN | Terminate the carrier trunk into the SIP gateway once contracted. Add call recording, consent capture, human handoff. Inbound before outbound, always. |
| **7+** | Outbound, gated on compliance | Only after telemarketer registration, DNC scrubbing against 3627, and per-contact consent state are in place and tested. Gated on paperwork, not engineering. |

---

## Risk register

### Carrier licensing takes longer than the whole build
The most probable cause of a missed launch date. Corporate SIP trunk provisioning in Pakistan involves KYC, contracting and number allocation under someone else's licence, and none of it moves at software speed.
**Mitigation:** start in week 0, approach more than one operator, and design so WhatsApp and web carry real traffic while PSTN is pending. Never let the launch depend on a single carrier relationship.

### The client rejects the Urdu voice
You have two production-grade options and one workaround, which is a genuinely thin market. If the client's ear rejects all three, there is no fourth vendor.
**Mitigation:** get voice sign-off in writing in week 1, before any pipeline work. If none pass, the remaining route is a custom voice — Inworld supports voice design and cloning — which needs a voice artist, budget and lead time scoped up front.

### Inworld's Urdu is experimental, and experimental can change
Their documentation explicitly separates generally-available languages from experimental ones. Experimental support can shift quality, pricing or availability without production guarantees.
**Mitigation:** keep Azure `ur-PK` wired as a runtime-switchable fallback rather than a code change. Abstract the TTS call behind one interface from day one.

### Numbers and dates come out wrong in speech
Reliably the most-reported quality bug in Urdu voice systems, and it surfaces in exactly the moments that matter — reading back a balance, a date, an order number.
**Mitigation:** a dedicated normalisation pass converting digits to explicit Urdu words before synthesis, with its own test suite. Build it in week 2, not after the first complaint.

### Costs come in far above model
Usually one of three causes: prompt caching silently not working, the agent talking far more than 450 characters a minute, or calls running longer than planned because the agent handles turns poorly.
**Mitigation:** track cost per *resolved call*, not per minute, from the first day of real traffic. A verbose agent that takes six minutes to do a two-minute job is a cost problem no vendor discount fixes.

### Regulatory exposure on outbound
Automated outbound into Pakistani mobiles engages the 2009 spam regulations, telemarketer registration and the DNC register. Enforcement risk sits with the operating entity.
**Mitigation:** written allocation of compliance responsibility in the contract, DNC scrubbing before every dial, per-contact consent records, and an agent that identifies itself as automated at the start of every outbound call.

---

## Sources & verification

All vendor capabilities and prices were checked against primary documentation on 8 August 2026. Language support in this space changes fast — re-verify the TTS table before committing, as that is where the market is moving quickest. Carrier rates and the PKR figures derived from them are planning estimates pending real quotes.

1. Deepgram — [Urdu on Nova-3](https://deepgram.com/learn/speech-to-text-for-hebrew-persian-urdu-on-nova-3); [models & languages](https://developers.deepgram.com/docs/models-languages-overview); [pricing](https://deepgram.com/pricing)
2. ElevenLabs — [model language support](https://elevenlabs.io/docs/models); [API pricing](https://elevenlabs.io/pricing/api)
3. Microsoft — [Azure Speech language and voice support](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support)
4. Inworld — [TTS language support tiers](https://docs.inworld.ai/tts/capabilities/multilingual); [pricing](https://inworld.ai/pricing)
5. Cartesia — [Sonic 3.5 language list](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest)
6. Google — [supported voices and languages](https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types)
7. LiveKit — [pricing](https://livekit.com/pricing); [SIP trunk setup](https://docs.livekit.io/telephony/start/sip-trunk-setup/)
8. PTA — [wireline licensing framework](https://www.pta.gov.pk/category/licensing-721279833-2023-05-30); [CVAS licensee list](https://www.pta.gov.pk/assets/media/2026-01-19-List-of-CVAS-Licensees-in-Pakistan-dated-15-01-26.pdf)
9. Telnyx — [Pakistan DID requirements](https://support.telnyx.com/en/articles/11843465-pakistan-did-requirements)
10. Meta — [WhatsApp Business Calling API pricing](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/pricing/)
11. FX reference — [USD/PKR 2026 history](https://www.exchangerates.org.uk/USD-PKR-spot-exchange-rates-history-2026.html)
