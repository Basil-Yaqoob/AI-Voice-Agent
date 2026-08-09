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
  latency: document.getElementById('latency'),
  breakdown: document.getElementById('breakdown'),
  transcript: document.getElementById('transcript'),
  placeholder: document.getElementById('placeholder'),
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
}

async function connect() {
  showError('');
  els.connect.disabled = true;
  setState('connecting');

  try {
    const res = await fetch('/api/token', { method: 'POST' });
    if (!res.ok) throw new Error('token endpoint returned ' + res.status);
    const { token, url } = await res.json();

    room = new LivekitClient.Room({ adaptiveStream: true, dynacast: true });
    room.on(LivekitClient.RoomEvent.DataReceived, onData);
    room.on(LivekitClient.RoomEvent.Disconnected, () => {
      setState('disconnected');
      els.connect.disabled = false;
      els.connect.textContent = 'Connect';
      els.connect.classList.remove('stop');
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
