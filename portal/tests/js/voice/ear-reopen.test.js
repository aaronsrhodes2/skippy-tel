// The ear survives closing and reopening the page (portal/static/skippy-voice.js: EAR_KEY, wake(), ifRemembered()), under Node with a
// VIRTUAL clock, a fake microphone, recorder, gate, localStorage and an AudioContext that can start asleep the way iOS starts it.
// After a reopen: allowed mic -> it listens at once and, if the phone keeps sound asleep, a "tap anywhere" strip wakes it with no question;
// not allowed (Safari's "Ask") -> the strip's tap asks once and the iPad hint names the setting that sticks.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");

const level = { v: 0.004 };
let recorders = [], say = [], ctxs = [], ctxStart = "running", blocked = false, store = {}, docOn = [], gesture = false;
class FakeAnalyser { constructor() { this.fftSize = 1024; } getFloatTimeDomainData(buf) { buf.fill(level.v); } connect() {} }
class FakeCtx {
  constructor() { this.state = ctxStart; ctxs.push(this); }
  createMediaStreamSource() { return { connect() {} }; } createAnalyser() { return new FakeAnalyser(); } close() { return Promise.resolve(); }
  resume() {                                                   // like iOS: sound starts only inside a tap
    if (this.state !== "running" && gesture) { this.state = "running"; if (this.onstatechange) this.onstatechange(); }
    return Promise.resolve();
  }
}
class FakeRec {
  constructor(stream, opts) { this.mimeType = (opts && opts.mimeType) || ""; this.state = "inactive"; recorders.push(this); }
  start() { this.state = "recording"; this._i = setInterval(() => this.ondataavailable && this.ondataavailable({ data: new Blob(["xxxxxxxx"]) }), 250); }
  stop() { if (this.state === "inactive") return; this.state = "inactive"; clearInterval(this._i); if (this.ondataavailable) this.ondataavailable({ data: new Blob(["x"]) }); if (this.onstop) this.onstop(); }
  static isTypeSupported() { return true; }
}
function node(tag) {
  return { tag, children: [], style: {}, dataset: {}, className: "", hidden: false, _on: {}, _text: "", attrs: {},
    classList: { toggle() {}, add() {}, remove() {} },
    set textContent(t) { this._text = String(t); }, get textContent() { return this._text; },
    appendChild(c) { this.children.push(c); return c; }, append(...cs) { cs.forEach(c => this.children.push(c)); },
    replaceChildren(...cs) { this.children = cs; }, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(ev, fn) { this._on[ev] = fn; },
    closest() { return this.tag === "button" ? this : null; } };
}
const gStreams = [];
const nav = { userAgent: "Node", platform: "Linux", maxTouchPoints: 0,
  mediaDevices: { getUserMedia: async (c) => { if (blocked) throw new Error("NotAllowedError"); const s = { c, getTracks: () => [{ stop() { s.stopped = true; } }] }; gStreams.push(s); return s; } } };
Object.defineProperty(globalThis, "navigator", { configurable: true, value: nav });
globalThis.MediaRecorder = FakeRec; globalThis.AudioContext = FakeCtx; globalThis.Audio = class { addEventListener() {} pause() {} play() { return Promise.resolve(); } };
globalThis.document = { createElement: node, addEventListener(ev, fn) { docOn.push({ ev, fn }); } };
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
globalThis.speechSynthesis = { speak(u) { setTimeout(() => u.onend && u.onend(), 100); }, cancel() {} };
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.includes("/g")) return { ok: true, status: 200, json: async () => ({ ok: true, url: "https://x.ts.net:10000", pass: "1.a", exp: Date.now() / 1000 + 3600, speak: false }) };
  if (u.includes("/stt")) { const idx = recorders.length - 1; return { ok: true, status: 200, json: async () => ({ text: say[idx] || "" }) }; }
  return { ok: false, status: 404, json: async () => ({}) };
};
const V = require("../../../static/skippy-voice.js");

async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
async function advance(t, ms) { for (let x = 0; x < ms; x += 50) { t.mock.timers.tick(50); await flush(); } }
function tap(target) { gesture = true; for (const ev of ["pointerdown", "touchend", "click"]) docOn.filter(h => h.ev === ev).forEach(h => h.fn({ target: target || node("div") })); gesture = false; }

// perm: "granted" | "prompt" | "denied" | "none" (no Permissions API); ear: true remembers the ear was on; ipad: an iPad's Safari
function setup(t, o) {
  t.mock.timers.reset();
  t.mock.timers.enable({ apis: ["setInterval", "setTimeout", "Date"], now: 1000000 });
  level.v = 0.004; recorders = []; say = []; ctxs = []; gStreams.length = 0; docOn = []; blocked = !!o.blocked;
  ctxStart = o.asleep ? "suspended" : "running";
  store = o.ear ? { [V.EAR_KEY]: "yes" } : {};
  nav.userAgent = o.ipad ? "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15" : "Node";
  nav.platform = o.ipad ? "MacIntel" : "Linux"; nav.maxTouchPoints = o.ipad ? 5 : 0;
  if (o.perm === "none") delete nav.permissions;
  else nav.permissions = { query: async () => ({ state: o.perm }) };
  const key = node("button"), ear = node("button"), box = node("textarea"), status = node("div"), sent = [];
  box.value = "";
  const voice = o.workshop ? V.attach({ gate: "/g", mic: key, ear, box, status, onSend: (m) => sent.push(m), lang: () => "" })
    : V.attach({ gate: "/g", key, box, status, onSend: (m) => sent.push(m), lang: () => "" });
  return { voice, key, ear, status, sent, label: () => (status.hidden || !status._built ? null : status._built.label.textContent) };
}
const listening = () => recorders.length > 0 && recorders[recorders.length - 1].state === "recording";

test("reopened with the mic allowed and sound asleep (iOS): it listens at once, and one tap anywhere wakes it with no question", async (t) => {
  const s = setup(t, { perm: "granted", ear: true, asleep: true, ipad: true });
  await advance(t, 800);
  assert.ok(listening()); assert.equal(gStreams.length, 1);
  assert.equal(s.label(), V.WAKE_TEXT);                                               // the strip says what it needs
  tap(); await advance(t, 100);
  assert.equal(ctxs[0].state, "running"); assert.equal(s.label(), null);              // woken, strip gone
  assert.equal(gStreams.length, 1);                                                   // the same microphone: no second ask
  say[recorders.length - 1] = "Skippy, hello";
  level.v = 0.12; await advance(t, 1500); level.v = 0.004; await advance(t, 2200);
  assert.equal(s.voice.chatting(), true);                                             // the word works after that one tap
});

test("reopened with the mic allowed and sound already running: no strip at all", async (t) => {
  const s = setup(t, { perm: "granted", ear: true });
  await advance(t, 800);
  assert.ok(listening()); assert.equal(s.label(), null);
});

test("reopened on an iPad where Safari still asks: nothing on load, the first tap asks once, then the hint names the setting", async (t) => {
  const s = setup(t, { perm: "prompt", ear: true, ipad: true });
  await advance(t, 800);
  assert.equal(gStreams.length, 0); assert.equal(recorders.length, 0);                // never a question on load
  assert.equal(s.label(), V.WAKE_TEXT);
  tap(); await advance(t, 600);
  assert.equal(gStreams.length, 1); assert.ok(listening()); assert.equal(s.voice.chatting(), false);    // back to listening for the word
  assert.equal(s.label(), V.SAFARI_HINT);
  tap(); await advance(t, 600);
  assert.equal(gStreams.length, 1);                                                   // only the first tap asks
  await advance(t, 9500);
  assert.equal(s.label(), null);
});

test("no Permissions API: the remembered ear comes back after the first tap", async (t) => {
  const s = setup(t, { perm: "none", ear: true });
  await advance(t, 500);
  assert.equal(gStreams.length, 0); assert.equal(s.label(), V.WAKE_TEXT);
  tap(); await advance(t, 600);
  assert.ok(listening()); assert.notEqual(s.label(), V.SAFARI_HINT);                  // the hint is for iPhone and iPad only
});

test("tapping the voice key itself while the strip waits does what the key does, opening the microphone once", async (t) => {
  const s = setup(t, { perm: "prompt", ear: true });
  await advance(t, 500);
  tap(s.key); s.key._on.click(); await advance(t, 600);
  assert.equal(gStreams.length, 1); assert.equal(s.voice.chatting(), true);
});

test("never on: nothing is remembered, no strip, no listening", async (t) => {
  const s = setup(t, { perm: "prompt", ear: false });
  await advance(t, 800);
  assert.equal(s.label(), null); assert.equal(gStreams.length, 0);
  tap(); await advance(t, 400);
  assert.equal(gStreams.length, 0);
});

test("the ear is remembered once the mic answers, and forgotten when it is blocked", async (t) => {
  let s = setup(t, { perm: "granted", ear: false });
  await advance(t, 800);
  assert.equal(store[V.EAR_KEY], "yes");
  s = setup(t, { perm: "prompt", ear: true, blocked: true, ipad: true });
  await advance(t, 300);
  tap(); await advance(t, 300);
  assert.equal(store[V.EAR_KEY], "no");
  assert.ok(s.label().includes("Website Settings"));
});

test("the agent's ear key (standby): remembered on, re-armed on reopen; turned off, forgotten", async (t) => {
  const s = setup(t, { perm: "granted", ear: true, workshop: true });
  await advance(t, 800);
  assert.equal(s.voice.standby(), true); assert.ok(listening()); assert.equal(s.ear.attrs["aria-pressed"], "true");
  s.ear._on.click(); await advance(t, 200);
  assert.equal(s.voice.standby(), false); assert.equal(store[V.EAR_KEY], "no");
});

test("isIOS: iPhone and iPad (which says it is a Mac with a touch screen), never a real Mac", () => {
  assert.ok(V.isIOS({ userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)" }));
  assert.ok(V.isIOS({ userAgent: "Mozilla/5.0 (Macintosh)", platform: "MacIntel", maxTouchPoints: 5 }));
  assert.ok(!V.isIOS({ userAgent: "Mozilla/5.0 (Macintosh)", platform: "MacIntel", maxTouchPoints: 0 }));
  assert.ok(!V.isIOS({ userAgent: "Mozilla/5.0 (Linux; Android 14)" }));
});
