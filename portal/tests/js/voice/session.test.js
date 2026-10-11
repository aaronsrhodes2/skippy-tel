// The hands-free voice session (portal/static/skippy-voice.js attach/handsFree) run end to end under Node with a VIRTUAL clock, a fake
// microphone whose loudness the test sets, a fake recorder and a fake gate. No real microphone or network: this proves the
// orchestration (hearing, interim words, a pause ending the utterance, the send countdown, continuing, "stop", cancelling).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");

const level = { v: 0.004 };
let stt = [], sttCalls = [], recorders = [];
// what the fake Whisper says: one text per utterance (the recorder that is asking), the whole of it once that recorder has stopped, its first words while it is still recording
let say = [], fetchLog = [], summaryMode = "ok", spoken = [];
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
globalThis.MediaRecorder = FakeRec; globalThis.AudioContext = FakeCtx; globalThis.Audio = class { pause() {} play() { return Promise.resolve(); } };
globalThis.document = { createElement: node };
globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
globalThis.speechSynthesis = { speak(u) { spoken.push(u.text); setTimeout(() => u.onend && u.onend(), 100); }, cancel() {} };
globalThis.fetch = async (url, init) => {
  const u = String(url);
  if (u.includes("/g")) return { ok: true, status: 200, json: async () => ({ ok: true, url: "https://x.ts.net:10000", pass: "1.a", exp: Date.now() / 1000 + 3600, speak: false }) };
  fetchLog.push(u);
  if (u.includes("/summary")) { if (summaryMode === "fail") return { ok: false, status: 503, json: async () => ({}) }; return { ok: true, status: 200, json: async () => ({ text: "Short spoken version." }) }; }
  if (u.includes("/stt")) { sttCalls.push(u); const idx = recorders.length - 1, full = say[idx] || "words", done = !recorders[idx] || recorders[idx].state === "inactive"; return { ok: true, status: 200, json: async () => ({ text: done ? full : full.split(" ").slice(0, Math.max(1, full.split(" ").length - 1)).join(" ") }) }; }
  return { ok: false, status: 404, json: async () => ({}) };
};
const V = require("../../../static/skippy-voice.js");

async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
async function advance(t, ms) { for (let x = 0; x < ms; x += 50) { t.mock.timers.tick(50); await flush(); } }

function setup(t, withSpeaker, keepOff) {
  t.mock.timers.enable({ apis: ["setInterval", "setTimeout", "Date"], now: 1000000 });
  level.v = 0.004; stt = []; say = []; sttCalls = []; recorders = []; gStreams.length = 0; fetchLog = []; summaryMode = "ok"; spoken = [];
  const mic = node("button"), box = node("textarea"), status = node("div"), speaker = withSpeaker ? node("button") : null, sent = [];
  box.value = "";
  const voice = V.attach({ gate: "/g", mic, box, status, speaker, onSend: (m) => sent.push(m), lang: () => "" });
  if (speaker && !keepOff) speaker._on.click();                                      // the speaker key on
  return { voice, mic, box, status, sent, speaker, label: () => (status.hidden ? null : status._built && status._built.label.textContent), lv: () => status._built && status._built.fill.style.width };
}
const speak = async (t, ms, v) => { level.v = v || 0.12; await advance(t, ms); level.v = 0.004; };

test("hearing you: the status shows a level while you speak, words appear in the box, a pause ends it, a countdown sends it", async (t) => {
  const s = setup(t);
  s.mic._on.click(); await advance(t, 600);                                         // opens the mic and learns the room
  assert.equal(s.label(), "Listening");
  assert.equal(gStreams[0].c.audio.echoCancellation, true);                          // asked for echo cancellation (music playing nearby)
  say = ["hello world"];                                                              // interims say a prefix of it, the final says all
  level.v = 0.12; await advance(t, 600);
  assert.equal(s.label(), "Hearing you"); assert.ok(parseInt(s.lv()) > 30, s.lv());  // a level bar that follows the voice
  await advance(t, 2400);                                                            // still talking: the words so far appear
  assert.equal(s.box.value, "hello");                                                // the words so far, while he is still speaking
  level.v = 0.004; await advance(t, 1900);                                           // the pause ends the utterance by itself (no mic-off)
  assert.equal(s.box.value, "hello world");
  assert.match(s.label(), /^Sending in [1-4]/);
  assert.deepEqual(s.sent, []);                                                      // not yet: a moment to read it
  await advance(t, 4300);
  assert.deepEqual(s.sent, ["hello world"]);                                         // then it goes
  assert.ok(gStreams[0].stopped, "the microphone is closed afterwards");
});

test("speaking again during the count continues the message and restarts the count", async (t) => {
  const s = setup(t);
  say = ["one two", "three"];
  s.mic._on.click(); await advance(t, 600); await speak(t, 1500); await advance(t, 1800);
  assert.equal(s.box.value, "one two");
  await advance(t, 1500);                                                            // 1.5 s into the count: he speaks again
  await speak(t, 1000); await advance(t, 1900);
  assert.equal(s.box.value, "one two three");
  assert.deepEqual(s.sent, []);                                                      // the count restarted
  await advance(t, 4300);
  assert.deepEqual(s.sent, ["one two three"]);
});

test("saying stop during the count throws the message away and keeps listening", async (t) => {
  const s = setup(t);
  say = ["send the thing", "stop"];
  s.mic._on.click(); await advance(t, 600); await speak(t, 1500); await advance(t, 1800);
  await advance(t, 1200); await speak(t, 900); await advance(t, 1900);
  assert.equal(s.label(), "Cancelled: go ahead"); assert.equal(s.box.value, "");        // not "send the thing stop": it is gone
  await advance(t, 1500);
  assert.equal(s.label(), "Listening");                                                  // and he can simply say it again
  assert.deepEqual(s.sent, []);
});

test("a tap on the mic key during the count cancels it", async (t) => {
  const s = setup(t);
  say = ["hello"];
  s.mic._on.click(); await advance(t, 600); await speak(t, 1500); await advance(t, 1800);
  assert.match(s.label(), /^Sending in/);
  s.mic._on.click(); await advance(t, 6000);
  assert.deepEqual(s.sent, []); assert.equal(s.box.value, "hello"); assert.ok(gStreams[0].stopped);
});

test("typing in the box takes over: the session is cancelled", async (t) => {
  const s = setup(t);
  say = ["hello"];
  s.mic._on.click(); await advance(t, 600); await speak(t, 1500); await advance(t, 1800);
  s.box.value = "hello there"; s.box._on.input(); await advance(t, 6000);
  assert.deepEqual(s.sent, []); assert.equal(s.box.value, "hello there");
});

test("silence for ten seconds says so and sends nothing", async (t) => {
  const s = setup(t);
  s.mic._on.click(); await advance(t, 11000);
  assert.deepEqual(s.sent, []); assert.ok(gStreams[0].stopped);
});

test("what he had already typed stays in front of what he says", async (t) => {
  const s = setup(t);
  s.box.value = "please";
  say = ["fix the pill"];
  s.mic._on.click(); await advance(t, 600); await speak(t, 1500); await advance(t, 1800);
  assert.equal(s.box.value, "please fix the pill");
});

// ── the no-touch conversation ──
async function talk(t, s, words, say_) { say = []; say[recorders.length - 1] = say_; await speak(t, words || 1500); await advance(t, 1800); await advance(t, 4300); }   // one turn: speak, pause, countdown, sent

test("a conversation: after the reply is spoken the mic opens by itself for the next turn, round after round", async (t) => {
  const s = setup(t);
  s.mic._on.click(); await advance(t, 600);
  await talk(t, s, 1500, "first question");
  assert.deepEqual(s.sent, ["first question"]);
  assert.equal(s.label(), "Thinking…"); assert.equal(s.mic.dataset.s, "boot");   // the reply is on its way; the mic is closed
  assert.ok(gStreams[0].stopped);
  await advance(t, 3000);
  assert.equal(gStreams.length, 1);                                                      // nothing reopens until he has answered
  s.voice.say("The answer, spoken.");                                                    // the reply lands (no speaker here: it ends at once)
  await advance(t, 1200);
  assert.equal(gStreams.length, 2); assert.equal(s.label(), "Listening");                // the mic is open again with no touch
  s.box.value = "";
  await talk(t, s, 1500, "second question");
  assert.deepEqual(s.sent, ["first question", "second question"]);
  s.voice.say("Another answer.");
  await advance(t, 1200);
  assert.equal(gStreams.length, 3);                                                      // round after round
});

test("saying goodbye ends the conversation and sends nothing", async (t) => {
  const s = setup(t);
  s.mic._on.click(); await advance(t, 600);
  await talk(t, s, 1500, "hello");
  s.voice.say("Hi."); await advance(t, 1200);
  say = []; say[recorders.length - 1] = "goodbye"; s.box.value = "";
  await speak(t, 1500); await advance(t, 2000);
  assert.deepEqual(s.sent, ["hello"]); assert.equal(s.label(), "Conversation ended");
  await advance(t, 5000);
  assert.equal(gStreams.length, 2); assert.equal(s.voice.chatting(), false); assert.equal(s.label(), null);   // and it stays closed
});

test("thirty seconds of quiet after a reply ends the conversation politely", async (t) => {
  const s = setup(t);
  s.mic._on.click(); await advance(t, 600);
  await talk(t, s, 1500, "hello");
  s.voice.say("Hi."); await advance(t, 1200);
  await advance(t, 31000);
  assert.match(s.label() || "", /not hearing you/); assert.equal(s.voice.chatting(), false); assert.ok(gStreams[1].stopped);
});

test("a tap while Skippy is thinking ends the conversation; no reply reopens anything afterwards", async (t) => {
  const s = setup(t);
  s.mic._on.click(); await advance(t, 600);
  await talk(t, s, 1500, "hello");
  assert.equal(s.voice.chatting(), true);
  s.mic._on.click(); await advance(t, 500);
  assert.equal(s.voice.chatting(), false);
  s.voice.say("late reply"); await advance(t, 3000);
  assert.equal(gStreams.length, 1);                                                      // the late reply does not reopen the mic
});

test("an error reply still gives him the turn back", async (t) => {
  const s = setup(t);
  s.mic._on.click(); await advance(t, 600);
  await talk(t, s, 1500, "hello");
  s.voice.resume(); await advance(t, 1200);                                              // the page calls resume() when no reply will come
  assert.equal(gStreams.length, 2);
});

const LONG = "I changed the portal's app file, ran the whole suite and deployed it; here is the long version with `code` and a path portal/app.py. ".repeat(6);

test("a short reply is spoken as it is, a long one as the gate's summary, with nothing read out that cannot be said", async (t) => {
  const s = setup(t, true);
  await s.voice.say("All done, Captain.");
  assert.deepEqual(spoken, ["All done, Captain."]); assert.ok(!fetchLog.some(u => u.includes("/summary")));
  await s.voice.say(LONG); await advance(t, 300);
  assert.deepEqual(spoken, ["All done, Captain.", "Short spoken version."]);                   // not the 700 characters of it
  assert.equal(fetchLog.filter(u => u.includes("/summary")).length, 1);
});

test("if the summary cannot be had, the opening is spoken, cut at a sentence, and the conversation goes on", async (t) => {
  const s = setup(t, true);
  summaryMode = "fail";
  s.mic._on.click(); await advance(t, 600);
  await talk(t, s, 1500, "tell me everything");
  s.voice.say(LONG); await advance(t, 1500);
  assert.equal(spoken.length, 1); assert.ok(spoken[0].length <= 500 && spoken[0].endsWith("."), spoken[0]);
  assert.equal(gStreams.length, 2);                                                            // and the mic reopened for his turn
});

test("starting a conversation turns his voice on by itself, and the reply is spoken before the mic reopens", async (t) => {
  const s = setup(t, true, true);                                                              // the speaker key is OFF
  assert.equal(s.voice.enabled(), false);
  s.mic._on.click(); await advance(t, 600);
  assert.equal(s.voice.enabled(), true);                                                       // a conversation needs his voice
  await talk(t, s, 1500, "hello");
  const before = gStreams.length;
  s.voice.say("A reply."); await advance(t, 100);
  assert.equal(gStreams.length, before);                                                       // still speaking: the mic stays shut
  await advance(t, 900);
  assert.equal(gStreams.length, before + 1); assert.equal(s.label(), "Listening");
});
