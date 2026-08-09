---
name: urdu-voice-agent-constraints
description: Hard constraints and target profile for the Pakistani Urdu voice agent project (greenfield as of Aug 2026)
metadata: 
  node_type: memory
  type: project
  originSessionId: 71fccf24-e53d-4d3d-b469-95999a28d19e
  modified: 2026-08-08T17:25:53.225Z
---

Building a production AI voice agent for Pakistani customers. Target profile agreed 2026-08-08: **inbound + outbound PSTN + WhatsApp voice**, **5,000–50,000 minutes/month**, **code-switched Urdu+English** (not formal Urdu), and a **local +92 number is non-negotiable**.

Two constraints drive every design decision and are easy to forget:

1. **ElevenLabs cannot do real-time Urdu.** Flash v2.5 (the low-latency model) has no Urdu; Eleven v3 has Urdu but explicitly cannot run real-time. Cartesia Sonic 3.5 and Google Cloud TTS have no Urdu at all. Only **Azure Neural TTS `ur-PK`** (UzmaNeural/AsadNeural) and **Inworld TTS-2** (Urdu flagged *experimental*) are viable. Deepgram Nova-3 handles Urdu STT fine (since Feb 2026, with keyterm prompting) — STT is not the problem, TTS is.

2. **A +92 voice number requires a PTA LL or LDI licensee partner.** A CVAS licence explicitly cannot carry voice, so this cannot be self-served from Twilio/Telnyx. Carrier contracting is the longest lead time in the project. Outbound additionally engages the 2009 spam regulations, telemarketer registration and the DNC register (shortcode 3627).

**Why:** both constraints are invisible until you check primary docs, and both invalidate the stack most tutorials recommend. Discovering either mid-build wastes weeks.

**How to apply:** keep TTS behind a runtime-switchable interface with Azure as fallback; sequence WhatsApp and inbound ahead of outbound PSTN so channels ship while licensing proceeds. Full research, architecture and PKR costing: https://claude.ai/code/artifact/1ba0c6c7-d3bd-4592-9cae-79768e9f72f3

Cost planning uses ~295 PKR/USD landed (not the ~278.6 mid-market rate) to absorb bank FX markup and advance tax on foreign card payments. See [[pkr-cost-planning-rate]].
