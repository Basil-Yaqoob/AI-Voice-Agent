# TASK-10 — Web demo frontend and control-plane API

| | |
|---|---|
| **Depends on** | TASK-08 |
| **Blocks** | TASK-13 |
| **Estimate** | 1 day |
| **Level** | Beginner–Intermediate |

---

## Objective

A browser page the client can open and talk to. WebRTC only — no telephony.

This is deliberate, not a shortcut. Per the research: a browser demo removes the SIP trunk, the DID, the carrier contract and the entire PTA question, and it is **lower latency than a phone call**, so the agent shows better than it will on day one of production. The same agent code later serves SIP with no changes.

**No build step.** Vanilla HTML plus the LiveKit JS SDK from a local vendored file. A demo that needs `npm install` before a client meeting is a demo that fails before a client meeting.

---

## Steps

### 1. Control-plane API

`src/agent/api/server.py` — FastAPI, three endpoints:

```python
@app.post("/api/token")
async def mint_token(req: TokenRequest, s: Settings = Depends(get_settings)) -> TokenResponse:
    """Short-lived LiveKit join token. The browser must never see API secrets."""
    room = f"demo-{uuid4().hex[:8]}"
    grant = VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    token = (AccessToken(s.livekit_api_key.get_secret_value(),
                         s.livekit_api_secret.get_secret_value())
             .with_identity(f"caller-{uuid4().hex[:6]}")
             .with_grants(grant)
             .with_ttl(timedelta(minutes=30)))
    return TokenResponse(token=token.to_jwt(), url=s.livekit_url, room=room)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness only — no dependency checks, so it stays fast."""
    return {"status": "ok"}


@app.get("/readyz")
async def ready(...) -> dict[str, object]:
    """Dependency checks: Postgres, Redis, and each configured provider."""
```

Split `/health` from `/readyz` deliberately. Liveness must be cheap and never fail on a downstream blip; readiness is what you check before a demo.

**Never ship LiveKit API secrets to the browser.** The token endpoint exists precisely so the secret stays server-side. A short TTL limits the damage if a token leaks from a shared screen.

### 2. Demo page

`src/agent/api/static/index.html`. Keep it genuinely simple, but include the things that make a demo *legible* to a non-technical client:

```
┌──────────────────────────────────────────────────┐
│  اردو Voice Agent — Live Demo                    │
│                                                  │
│         ┌────────────────────────┐               │
│         │      ● Connect         │               │
│         └────────────────────────┘               │
│                                                  │
│  Status:  Listening…                             │
│                                                  │
│  ┌─ Live transcript ──────────────────────────┐  │
│  │ You:   میرا order کہاں ہے                  │  │
│  │ Agent: آپ کا order کل deliver ہو جائے گا۔  │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ┌─ Latency ──────────────────────────────────┐  │
│  │ Last turn:  612 ms   ● under budget        │  │
│  │ STT 94 · LLM 287 · TTS 108 · other 123     │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  Voice:  [ Azure Uzma ▾ ]    ← live A/B switch   │
└──────────────────────────────────────────────────┘
```

Four elements, each earning its place:

- **Live transcript.** Lets the client *see* the code-switching working. Without it they only hear a voice and cannot tell what the system understood — and half your value is in the understanding.
- **Per-turn latency with a budget indicator.** Turns "it feels fast" into a number you can defend. This is the single most persuasive element on the page for a technical buyer.
- **Voice switcher.** Directly serves the A/B voice comparison the research recommends. Let the client pick their voice live; they will remember choosing it.
- **RTL rendering.** `dir="rtl"` on the Urdu text, with a Nastaliq-capable font stack. Urdu in a Latin font looks broken to a native reader and undermines the whole demo.

```css
.urdu {
  direction: rtl;
  font-family: 'Noto Nastaliq Urdu', 'Jameel Noori Nastaleeq',
               'Urdu Typesetting', serif;
  line-height: 2.1;   /* Nastaliq needs unusually generous leading */
}
```

### 3. Client-side connection

Vendor `livekit-client.umd.js` into `static/vendor/` and commit it. A CDN outage or conference wifi that blocks a CDN domain should not be able to kill your demo.

```javascript
async function connect() {
  const { token, url } = await (await fetch('/api/token', { method: 'POST' })).json();
  const room = new LivekitClient.Room({ adaptiveStream: true, dynacast: true });

  room.on(LivekitClient.RoomEvent.DataReceived, (payload) => {
    const msg = JSON.parse(new TextDecoder().decode(payload));
    if (msg.type === 'transcript') appendTranscript(msg.role, msg.text);
    if (msg.type === 'metrics')    updateLatency(msg);
  });

  await room.connect(url, token);
  await room.localParticipant.setMicrophoneEnabled(true);
}
```

The agent publishes transcripts and metrics over LiveKit's data channel, so no second websocket is needed.

### 4. Publishing transcripts from the agent

In the pipeline, after each final transcript and each spoken sentence, publish a small JSON payload on the data channel. Keep it fire-and-forget — **a failure to publish UI data must never affect the call.** Wrap it and swallow errors.

### 5. Microphone permissions and failure states

The demo fails in front of the client if you skip these:

- **HTTPS or `localhost`.** `getUserMedia` refuses to run on plain HTTP over a LAN address. If you demo from a laptop to a phone on the same network, you need a tunnel or a self-signed cert.
- **Explicit permission-denied state.** Show a clear instruction, not a dead button.
- **Visible connection state.** Connecting / connected / reconnecting / failed.
- **Echo cancellation on.** Without it, on a laptop speaker the agent hears itself and barges in on its own voice. `echoCancellation: true, noiseSuppression: true` on the audio constraints.

That last one is the classic demo-day failure: the agent interrupts itself in an endless loop and it looks catastrophically broken.

---

## Acceptance criteria

- [ ] `/api/token` returns a valid short-TTL LiveKit token; no secret reaches the browser
- [ ] `/health` responds in under 10ms without touching dependencies
- [ ] `/readyz` reports per-dependency status
- [ ] Page loads with **no build step** — open the file, it works
- [ ] LiveKit client is vendored locally, not CDN-loaded
- [ ] Connect button establishes a room and enables the microphone
- [ ] Live transcript shows both sides, Urdu rendered RTL in a Nastaliq font
- [ ] Per-turn latency displays with a pass/fail budget indicator
- [ ] Voice switcher changes TTS voice without reconnecting
- [ ] Echo cancellation enabled — agent does not interrupt itself on speaker
- [ ] Permission-denied and connection-failed states show clear messages
- [ ] Works in Chrome and Safari (including iOS Safari — different autoplay rules)
- [ ] UI-publishing failures never disrupt the call

---

## Unit tests

`tests/unit/test_token_api.py`:

| Test | Asserts |
|---|---|
| `test_token_is_valid_jwt` | Decodes with the configured secret |
| `test_token_has_room_grant` | `roomJoin` and correct room |
| `test_token_ttl_bounded` | ≤ 30 minutes |
| `test_token_response_has_no_secret` | **Security** — secret absent from body |
| `test_each_request_unique_room` | No cross-talk between demo sessions |
| `test_identity_is_unique` | Participants do not collide |

```python
def test_token_response_has_no_secret(client, settings) -> None:
    """A leaked API secret from a demo page is a real incident."""
    body = client.post("/api/token").json()
    raw = json.dumps(body)
    assert settings.livekit_api_secret.get_secret_value() not in raw
    assert "secret" not in {k.lower() for k in body}
```

`tests/unit/test_health.py`:

| Test | Asserts |
|---|---|
| `test_health_no_dependencies` | Passes with Postgres/Redis down |
| `test_readyz_reports_dependency_failure` | Returns 503 with detail |
| `test_readyz_lists_providers` | Configured providers each reported |

`tests/unit/test_ui_publishing.py`:

| Test | Asserts |
|---|---|
| `test_transcript_payload_shape` | Fields the frontend expects |
| `test_metrics_payload_shape` | Per-stage timings included |
| `test_publish_failure_does_not_raise` | **Call survives a UI failure** |

```python
async def test_publish_failure_does_not_raise() -> None:
    """UI telemetry is best-effort. It must never be able to drop a call."""
    room = FakeRoom(publish_raises=ConnectionError("data channel closed"))
    publisher = UIPublisher(room)
    await publisher.transcript("user", "سلام")     # must not raise
```

### Manual test checklist

Some of this cannot be automated and must be walked through before any client demo:

- [ ] Open in Chrome, grant mic, speak Urdu, hear a reply
- [ ] Interrupt mid-reply — agent stops promptly
- [ ] Deny mic permission — clear instruction shown
- [ ] Kill the agent worker mid-call — UI shows a failure state, not a frozen page
- [ ] Switch voice mid-conversation — next turn uses the new voice
- [ ] Test on the actual demo laptop, on the actual venue wifi, with the actual speakers

---

## Common pitfalls

- **Echo cancellation off.** Agent hears itself and barges in on its own voice — the most embarrassing possible demo failure.
- **CDN-loaded LiveKit client.** Venue wifi or a CDN blip kills the demo.
- **Plain HTTP on a LAN address.** `getUserMedia` silently refuses.
- **No RTL / no Nastaliq font.** Urdu looks broken to the audience who matter most.
- **Adding a build step.** One more thing to fail under pressure, for no demo-day benefit.
- **Letting UI publishing exceptions propagate.** A transcript-display bug should never drop a call.
- **Skipping iOS Safari.** Different autoplay and permission rules; find out before the client does.
