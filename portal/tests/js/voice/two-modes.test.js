// The chat's TWO voice modes (portal/static/skippy-voice.js, opts.key; the Captain's design 2026-10-23), under Node with a VIRTUAL
// clock, a fake microphone, recorder and gate (the same harness as session.test.js). Typing: nothing spoken, and once the browser
// allows the mic it listens only for "Skippy". Hands-off: listens and speaks round after round until a sleep word ("thanks") or a tap.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");

const level = { v: 0.004 };
let stt = [], sttCalls = [], recorders = [];
// what the fake Whisper says: one text per utterance (the recorder that is asking), the whole of it once that recorder has stopped, its first words while it is still recording
let say = [], langs = [], fetchLog = [], summaryMode = "ok", spoken = [];      // langs[i]: [lang, sure] Whisper reports for utterance i
class FakeAnalyser { constructor() { this.fftSize = 1024; } getFloatTimeDomainData(buf) { buf.fill(level.v); } connect() {} }
class FakeCtx { createMediaStreamSource() { return { connect() {} }; } createAnalyser() { return new FakeAnalyser(); } close() { return Promise.resolve(); } }
class FakeRec {
  constructor(stream, opts) { this.mimeType = (opts && opts.mimeType) || ""; this.state = "inactive"; recorders.push(this); }
  start() { this.state = "recording"; this._i = setInterval(() => this.ondataavailable && this.ondataavailable({ data: new Blob(["xxxxxxxx"]) }), 250); }
  stop() { if (this.state === "inactive") return; this.state = "inactive"; clearInterval(this._i); if (this.ondataavailable) this.ondataavailable({ data: new Blob(["x"]) }); if (this.onstop) this.onstop(); }
  static isTypeSupported() { return true; }
}
function node(tag) {
  return { tag, children: [], style: {}, dataset: {}, className: "", hidden: false, _on: {}, _text: "",
    classList: { toggle() {}, add() {}, remove() {} },
    set textContent(t) { this._text = String(t); }, get textContent() { return this._text; },
    appendChild(c) { this.children.push(c); return c; }, append(...cs) { cs.forEach(c => this.children.push(c)); },
    replaceChildren(...cs) { this.children = cs; }, setAttribute() {}, addEventListener(ev, fn) { this._on[ev] = fn; } };
}
const gStreams = [];
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: { getUserMedia: async (c) => { const s = { c, getTracks: () => [{ stop() { s.stopped = true; } }] }; gStreams.push(s); return s; } } } });
globalThis.MediaRecorder = FakeRec; globalThis.AudioContext = FakeCtx; globalThis.Audio = class { addEventListener() {} pause() {} play() { return Promise.resolve(); } };
globalThis.document = { createElement: node };
globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
globalThis.speechSynthesis = { speak(u) { spoken.push(u.text); setTimeout(() => u.onend && u.onend(), 100); }, cancel() {} };
globalThis.fetch = async (url, init) => {
  const u = String(url);
  if (u.includes("/g")) return { ok: true, status: 200, json: async () => ({ ok: true, url: "https://x.ts.net:10000", pass: "1.a", exp: Date.now() / 1000 + 3600, speak: false }) };
  fetchLog.push(u);
  if (u.includes("/summary")) { if (summaryMode === "fail") return { ok: false, status: 503, json: async () => ({}) }; return { ok: true, status: 200, json: async () => ({ text: "Short spoken version." }) }; }
  if (u.includes("/stt")) { sttCalls.push(u); const idx = recorders.length - 1, full = say[idx] || "words", done = !recorders[idx] || recorders[idx].state === "inactive"; return { ok: true, status: 200, json: async () => ({ text: done ? full : full.split(" ").slice(0, Math.max(1, full.split(" ").length - 1)).join(" "), lang: (langs[idx] || ["en"])[0], sure: (langs[idx] || ["en", 0.99])[1] }) }; }
  return { ok: false, status: 404, json: async () => ({}) };
};
const V = require("../../../static/skippy-voice.js");

async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
async function advance(t, ms) { for (let x = 0; x < ms; x += 50) { t.mock.timers.tick(50); await flush(); } }

function setup(t, granted, more) {
  t.mock.timers.enable({ apis: ["setInterval", "setTimeout", "Date"], now: 1000000 });
  level.v = 0.004; stt = []; say = []; langs = []; sttCalls = []; recorders = []; gStreams.length = 0; fetchLog = []; summaryMode = "ok"; spoken = [];
  navigator.permissions = { query: async () => ({ state: granted ? "granted" : "prompt" }) };
  const key = node("button"), box = node("textarea"), status = node("div"), sent = [];
  box.value = "";
  const voice = V.attach(Object.assign({ gate: "/g", key, box, status, onSend: (m) => sent.push(m), lang: () => "" }, more || {}));
  return { voice, key, box, status, sent, label: () => (status.hidden ? null : status._built && status._built.label.textContent) };
}
const listening = () => recorders.length > 0 && recorders[recorders.length - 1].state === "recording";
const speak = async (t, ms, v) => { level.v = v || 0.12; await advance(t, ms); level.v = 0.004; };

test("Typing: with the mic already allowed it listens at once, but only for the word, and never speaks", async (t) => {
  const s = setup(t, true);
  await advance(t, 700);
  assert.ok(listening()); assert.equal(s.voice.chatting(), false); assert.ok(!s.label());           // no status strip in Typing
  say = []; say[recorders.length - 1] = "what are we having for dinner";
  await speak(t, 1500); await advance(t, 2200);
  assert.deepEqual(s.sent, []); assert.equal(s.box.value, "");
  s.voice.say("A typed reply."); await advance(t, 500);
  assert.deepEqual(spoken, []);                                                                        // Typing is silent
});

test("Typing: without permission nothing listens until the key is tapped, and the tap starts Hands-off", async (t) => {
  const s = setup(t, false);
  await advance(t, 700);
  assert.equal(recorders.length, 0); assert.equal(gStreams.length, 0);                                 // never a prompt on load
  s.key._on.click(); await advance(t, 600);
  assert.equal(s.voice.chatting(), true); assert.ok(listening()); assert.equal(s.label(), "Listening");
});

test("'Skippy, ...' switches to Hands-off: the reply is spoken, silence doesn't end it, 'thanks' goes back to Typing", async (t) => {
  const s = setup(t, true);
  await advance(t, 700);
  say = []; say[recorders.length - 1] = "Skippy, hello";
  await speak(t, 1500); await advance(t, 2200); await advance(t, 4300);
  assert.deepEqual(s.sent, ["hello"]); assert.equal(s.voice.chatting(), true);
  s.voice.say("Hi there."); await advance(t, 1200);
  assert.deepEqual(spoken, ["Hi there."]); assert.ok(listening());                                     // spoken, then listening again
  await advance(t, 45000);
  assert.equal(s.voice.chatting(), true); assert.ok(listening());                                      // a long silence: still Hands-off
  say = []; say[recorders.length - 1] = "Thanks, Skippy.";
  await speak(t, 1500); await advance(t, 2200); await advance(t, 1500);
  assert.equal(s.voice.chatting(), false); assert.equal(s.voice.standby(), true); assert.deepEqual(s.sent, ["hello"]);
  assert.equal(gStreams.length, 1); assert.equal(gStreams[0].stopped, undefined);                      // the one microphone, still open for the word
  s.voice.say("Not spoken."); await advance(t, 500);
  assert.deepEqual(spoken, ["Hi there."]);
});

test("a tap in Hands-off goes back to Typing; typing a turn keeps Hands-off and the reply is still spoken", async (t) => {
  const s = setup(t, true);
  await advance(t, 700);
  s.key._on.click(); await advance(t, 600);
  assert.equal(s.voice.chatting(), true);
  s.box.value = "typed instead"; s.box._on.input(); await advance(t, 100);
  assert.equal(s.voice.chatting(), true);
  s.voice.say("Answer."); await advance(t, 1200);
  assert.deepEqual(spoken, ["Answer."]); assert.ok(listening());
  s.key._on.click(); await advance(t, 800);
  assert.equal(s.voice.chatting(), false); assert.equal(s.voice.standby(), true);
});

test("the sleep words, and only said alone", () => {
  for (const w of ["thanks", "Thank you.", "thanks Skippy", "thank you very much!", "goodbye", "go to sleep", "that's all", "good night"])
    assert.ok(V.END_RE.test(w), w);
  for (const w of ["thanks for the code, now run the tests", "thank you for asking about the weather"])
    assert.ok(!V.END_RE.test(w), w);
});

test("Hands-off: someone speaking another language is written down as heard, not sent; your English still goes to Skippy", async (t) => {
  const heardLines = [];
  const s = setup(t, false, { onHeard: (h) => heardLines.push(h) });
  await advance(t, 700);
  s.key._on.click(); await advance(t, 600);                                                           // Hands-off
  assert.equal(s.voice.chatting(), true);
  say[recorders.length - 1] = "Where is the train station?"; langs[recorders.length - 1] = ["es", 0.94];
  await speak(t, 1500); await advance(t, 2200);
  assert.deepEqual(heardLines, [{ text: "Where is the train station?", lang: "es", sure: 0.94 }]);
  assert.equal(s.box.value, ""); assert.deepEqual(s.sent, []);                                         // nothing in the box, nothing sent
  assert.ok(listening()); assert.equal(s.voice.chatting(), true);                                     // and it keeps listening
  say[recorders.length - 1] = "Goodbye"; langs[recorders.length - 1] = ["fr", 0.9];                  // a stranger's "au revoir" is not your sleep word
  await speak(t, 1500); await advance(t, 2200);
  assert.equal(s.voice.chatting(), true); assert.equal(heardLines.length, 2);
  say[recorders.length - 1] = "what did she say"; langs[recorders.length - 1] = ["en", 0.99];
  await speak(t, 1500); await advance(t, 2200); await advance(t, 4300);
  assert.deepEqual(s.sent, ["what did she say"]); assert.equal(heardLines.length, 2);
});

test("Hands-off: a language Whisper is unsure of stays your message, and without onHeard nothing changes", async (t) => {
  const heardLines = [];
  const s = setup(t, false, { onHeard: (h) => heardLines.push(h) });
  await advance(t, 700);
  s.key._on.click(); await advance(t, 600);
  say[recorders.length - 1] = "turn on the lights"; langs[recorders.length - 1] = ["nl", 0.41];      // a short English line misheard as Dutch
  await speak(t, 1500); await advance(t, 2200); await advance(t, 4300);
  assert.deepEqual(heardLines, []); assert.deepEqual(s.sent, ["turn on the lights"]);
});

test("heard(): another language, sure enough", () => {
  assert.ok(V.heard("es", 0.9)); assert.ok(V.heard("ja", V.HEARD_SURE));
  for (const [l, p] of [["en", 0.99], ["", 0.99], ["es", 0.5], ["es", undefined], [undefined, 1]]) assert.ok(!V.heard(l, p), l + " " + p);
});
