/* Demo client. No build step: this file runs as-is in the browser.
 *
 * The LiveKit client is vendored under /static/vendor rather than loaded from a
 * CDN. Conference wifi blocks CDNs, and a demo that depends on one is a demo
 * that fails in the room it matters in.
 */
'use strict';

const els = {
  connect: document.getElementById('connect'),
  state: document.getElementById('state'),
  dot: document.getElementById('dot'),
  error: document.getElementById('error'),
  voice: document.getElementById('voice'),
  caller: document.getElementById('caller'),
  latency: document.getElementById('latency'),
  breakdown: document.getElementById('breakdown'),
  transcript: document.getElementById('transcript'),
  placeholder: document.getElementById('placeholder'),
  engine: document.getElementById('engine'),
};

const BUDGET_MS = 800;
let room = null;

function setState(text, kind) {
  els.state.textContent = text;
  els.dot.className = 'dot' + (kind ? ' ' + kind : '');
}

function showError(message) {
  els.error.textContent = message;
  els.error.hidden = !message;
}

function isUrdu(text) {
  return /[؀-ۿ]/.test(text);
}

function appendMessage(role, text) {
  if (els.placeholder) {
    els.placeholder.remove();
    els.placeholder = null;
  }
  const node = document.createElement('div');
  node.className = 'msg ' + (role === 'user' ? 'you' : 'agent');
  if (isUrdu(text)) node.classList.add('urdu');

  const who = document.createElement('span');
  who.className = 'who';
  who.textContent = role === 'user' ? 'You' : 'Agent';
  node.appendChild(who);
  node.appendChild(document.createTextNode(text));

  els.transcript.appendChild(node);
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function showMetrics(m) {
  const total = Math.round(m.first_audio_ms);
  els.latency.textContent = total;
  els.latency.className = 'big ' + (total <= BUDGET_MS ? 'good' : 'over');

  const stages = m.stages || {};
  els.breakdown.textContent = Object.keys(stages)
    .map((k) => k + ' ' + Math.round(stages[k]))
    .join(' · ') + (total <= BUDGET_MS ? '  — under budget' : '  — over ' + BUDGET_MS + 'ms');
}

function onData(payload) {
  let msg;
  try {
    msg = JSON.parse(new TextDecoder().decode(payload));
  } catch (e) {
    return; // never let a malformed frame break the call
  }
  if (msg.type === 'transcript' && msg.final) appendMessage(msg.role, msg.text);
  if (msg.type === 'metrics' && msg.first_audio_ms) showMetrics(msg);
  if (msg.type === 'state') setState(msg.state, 'live');
  // A call can be connected and still have nothing behind it — the socket to
  // the model failed. Silence looks identical to a working line nobody is
  // talking on, so the worker says so explicitly and it goes on screen.
  if (msg.type === 'error') {
    showError(msg.text);
    setState('no agent', 'err');
  }
}

async function connect() {
  showError('');
  els.connect.disabled = true;
  setState('connecting');

  try {
    // The caller number goes to the server, which signs it into the token
    // identity. Sending it from the page on every request instead would let
    // anyone with the console read out someone else's orders.
    const res = await fetch('/api/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ caller_phone: els.caller.value || null }),
    });
    if (!res.ok) throw new Error('token endpoint returned ' + res.status);
    const { token, url } = await res.json();

    els.caller.disabled = true;

    room = new LivekitClient.Room({ adaptiveStream: true, dynacast: true });
    room.on(LivekitClient.RoomEvent.DataReceived, onData);

    // Without this the agent speaks into the void. The track arrives either
    // way and the transcript still appears, so the failure looks like broken
    // TTS rather than a missing line of browser plumbing.
    room.on(LivekitClient.RoomEvent.TrackSubscribed, (track) => {
      if (track.kind !== 'audio') return;
      const el = track.attach();
      el.autoplay = true;
      el.setAttribute('data-agent-audio', '');
      document.body.appendChild(el);
    });

    room.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
      track.detach().forEach((el) => el.remove());
    });

    // Chrome can still refuse to play until it sees a gesture it likes. The
    // Connect click usually counts, but not always, and a silent agent with no
    // explanation is the worst version of this to debug live.
    room.on(LivekitClient.RoomEvent.AudioPlaybackStatusChanged, () => {
      if (!room.canPlaybackAudio) {
        showError('Browser blocked audio. Click anywhere to enable sound.');
        const resume = async () => {
          await room.startAudio();
          showError('');
          document.removeEventListener('click', resume);
        };
        document.addEventListener('click', resume);
      }
    });

    room.on(LivekitClient.RoomEvent.Disconnected, () => {
      document.querySelectorAll('[data-agent-audio]').forEach((el) => el.remove());
      setState('disconnected');
      els.connect.disabled = false;
      els.connect.textContent = 'Connect';
      els.connect.classList.remove('stop');
      els.caller.disabled = false;
      room = null;
    });

    await room.connect(url, token);

    // Echo cancellation is not optional. Without it the agent hears itself
    // through laptop speakers and interrupts its own sentence, which looks
    // catastrophically broken in front of a client.
    await room.localParticipant.setMicrophoneEnabled(true, {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    });

    setState('listening', 'live');
    els.connect.disabled = false;
    els.connect.textContent = 'Hang up';
    els.connect.classList.add('stop');
  } catch (err) {
    setState('failed', 'err');
    els.connect.disabled = false;
    showError(
      err && err.name === 'NotAllowedError'
        ? 'Microphone blocked. Allow it in your browser, then reload.'
        : 'Could not connect: ' + (err && err.message ? err.message : err)
    );
  }
}

async function disconnect() {
  if (room) await room.disconnect();
}

/* The voice list comes from the server, which is the only side that knows
 * which mode is running. Hard-coding all three vendors here meant the picker
 * usually pointed at a voice the running engine had never heard of, and
 * selecting it did nothing with no error anywhere. */
async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    if (!res.ok) throw new Error(res.status);
    const cfg = await res.json();

    els.voice.textContent = '';
    const group = document.createElement('optgroup');
    group.label = (cfg.voices[0] && cfg.voices[0].vendor) || 'Voices';
    cfg.voices.forEach((v) => {
      const opt = document.createElement('option');
      opt.value = v.id;
      opt.textContent = v.label;
      if (v.id === cfg.default_voice) opt.selected = true;
      group.appendChild(opt);
    });
    els.voice.appendChild(group);
    els.voice.disabled = cfg.voices.length === 0;
    if (els.engine) els.engine.textContent = cfg.engine;
  } catch (e) {
    // A missing list must not block the demo: the call still works on the
    // voice the worker started with.
    els.voice.disabled = true;
    if (els.engine) els.engine.textContent = 'the running engine';
  }
}

loadConfig();

els.connect.addEventListener('click', () => (room ? disconnect() : connect()));

els.voice.addEventListener('change', async () => {
  if (!room) return;
  // Best-effort: a voice change must never be able to drop the call.
  try {
    await room.localParticipant.publishData(
      new TextEncoder().encode(JSON.stringify({ type: 'set_voice', voice: els.voice.value })),
      { reliable: true }
    );
  } catch (e) {
    /* ignore */
  }
});
