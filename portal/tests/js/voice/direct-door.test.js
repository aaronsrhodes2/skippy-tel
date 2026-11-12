// The voice client (static/skippy-voice.js) and the direct front door (static/direct.js) together, under Node with a fake gate and a fake
// edge: with the door answering, the voice goes straight to skippy-pc; when the door drops while the pass is held (a pass lasts hours),
// the call is made again through the Funnel and the pass stays there; and a page without direct.js behaves exactly as it always did.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");

const FUNNEL = "https://skippy-pc.tail1234.ts.net:10000";
const DOOR = "https://home.makerportal.live/voice";
let calls = [], edgeUp = true, summaryDown = false;

function node(tag) {
  return { tag, children: [], style: {}, dataset: {}, className: "", hidden: false, _on: {}, _text: "",
    classList: { toggle() {}, add() {}, remove() {} },
    set textContent(t) { this._text = String(t); }, get textContent() { return this._text; },
    appendChild(c) { this.children.push(c); return c; }, append(...cs) { cs.forEach(c => this.children.push(c)); },
    replaceChildren(...cs) { this.children = cs; }, setAttribute() {}, addEventListener(ev, fn) { this._on[ev] = fn; } };
}
const audios = [];
globalThis.Audio = class { constructor() { audios.push(this); this.plays = 0; } addEventListener() {} pause() {} play() { this.plays++; return Promise.resolve(); } };
globalThis.document = { createElement: node };
globalThis.fetch = async (url, init) => {
  const u = String(url);
  calls.push(u);
  if (u === "/g") {
    return { ok: true, status: 200, json: async () => ({ ok: true, url: FUNNEL, direct: DOOR, pass: "1.a", exp: Date.now() / 1000 + 3600, speak: true }) };
  }
  if (u.startsWith(DOOR)) {
    if (!edgeUp) throw new TypeError("Failed to fetch");                                               // the edge is not answering at all
    if (u === DOOR + "/health") return { ok: true, status: 200, json: async () => ({ ok: true, ready: true }) };
  }
  if (u.endsWith("/summary")) {
    if (summaryDown) return { ok: false, status: 503, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => ({ text: "A short version." }) };
  }
  if (u.endsWith("/tts")) return { ok: true, status: 200, blob: async () => new Blob(["sound"]) };
  return { ok: false, status: 404, json: async () => ({}) };
};
const D = require("../../../static/direct.js");
const V = require("../../../static/skippy-voice.js");

function setup(withDoor, now) {
  calls = []; edgeUp = true; summaryDown = false;
  if (withDoor) globalThis.SkippyDirect = D.create({ storage: null, now }); else delete globalThis.SkippyDirect;     // (a new instance: it remembers nothing from the last test)
  const speaker = node("button"), mic = node("button");
  const voice = V.attach({ gate: "/g", mic, speaker, box: node("textarea"), status: node("div"), onSend() {}, lang: () => "" });
  speaker._on.click();                                                                                  // the speaker key on: replies are spoken (the tap also plays the silent unlock clip once)
  const mine = audios[audios.length - 1], unlock = mine.plays;
  return { voice, heard: () => mine.plays - unlock };                                                   // replies the audio element has been told to play
}
const LONG = "A long reply about the whole day, the weather, the trains and what the cat did next to the window. ".repeat(4);   // over the summary limit
const tails = list => list.map(u => u.replace(FUNNEL, "FUNNEL").replace(DOOR, "DOOR"));

test("with the door answering, the voice goes straight to skippy-pc: the probe once, then the call on the direct address", async () => {
  const { voice, heard } = setup(true);
  await voice.say("Hello there, Captain.");
  assert.deepEqual(tails(calls), ["/g", "DOOR/health", "DOOR/t/1.a/tts"]);
  assert.equal(heard(), 1);
  await voice.say("And again.");                                                                        // the same pass, the door believed: no second probe
  assert.deepEqual(tails(calls).slice(3), ["DOOR/t/1.a/tts"]);
});

test("a long reply is summarized and spoken through the door too", async () => {
  const { voice } = setup(true);
  await voice.say(LONG);
  assert.deepEqual(tails(calls), ["/g", "DOOR/health", "DOOR/t/1.a/summary", "DOOR/t/1.a/tts"]);
});

test("the door drops while the pass is held: the call is made again through the Funnel, and the pass stays there", async () => {
  const { voice, heard } = setup(true);
  await voice.say("First.");
  assert.deepEqual(tails(calls), ["/g", "DOOR/health", "DOOR/t/1.a/tts"]);
  edgeUp = false;
  calls = [];
  await voice.say("Second.");                                                                           // no answer from the door: once more, on the Funnel
  assert.deepEqual(tails(calls), ["DOOR/t/1.a/tts", "FUNNEL/t/1.a/tts"]);
  assert.equal(heard(), 2);                                                                             // and it was heard
  calls = [];
  await voice.say("Third.");                                                                            // the door is remembered as down: straight to the Funnel
  assert.deepEqual(tails(calls), ["FUNNEL/t/1.a/tts"]);
});

test("the same goes for the summary of a long reply", async () => {
  const { voice } = setup(true);
  await voice.say("First.");
  edgeUp = false;
  calls = [];
  await voice.say(LONG);
  assert.deepEqual(tails(calls), ["DOOR/t/1.a/summary", "FUNNEL/t/1.a/summary", "FUNNEL/t/1.a/tts"]);
});

test("an answer from the door that is an error is the gate's answer, not a dropped door: no second call, no switch", async () => {
  const { voice } = setup(true);
  summaryDown = true;
  await voice.say(LONG);                                                                                // the summary is refused with a 503: the opening is spoken instead
  assert.deepEqual(tails(calls), ["/g", "DOOR/health", "DOOR/t/1.a/summary", "DOOR/t/1.a/tts"]);
});

test("a page without direct.js goes to the Funnel exactly as it always did, whatever the pass says", async () => {
  const { voice } = setup(false);
  await voice.say("Hello there, Captain.");
  assert.deepEqual(tails(calls), ["/g", "FUNNEL/t/1.a/tts"]);
  await voice.say(LONG);
  assert.deepEqual(tails(calls).slice(2), ["FUNNEL/t/1.a/summary", "FUNNEL/t/1.a/tts"]);
});

test("a door that was never reachable leaves the pass on the Funnel from the first call", async () => {
  const { voice } = setup(true);
  edgeUp = false;
  await voice.say("Hello there, Captain.");
  assert.deepEqual(tails(calls), ["/g", "DOOR/health", "FUNNEL/t/1.a/tts"]);                          // one probe, no call wasted on the door
  await voice.say("Again.");
  assert.deepEqual(tails(calls).slice(3), ["FUNNEL/t/1.a/tts"]);
});

test("a pass held for hours looks at its door again, in the background, once the last answer is old: back on the direct door when it is up again", async () => {
  const clock = { t: Date.now() };
  const { voice } = setup(true, () => clock.t);
  edgeUp = false;
  await voice.say("The door is down.");
  assert.deepEqual(tails(calls), ["/g", "DOOR/health", "FUNNEL/t/1.a/tts"]);
  edgeUp = true;
  clock.t += 301_000;                                                                                     // the five minutes a silence is believed are over
  calls = [];
  await voice.say("The door may be back.");                                                               // (this one may still go by the Funnel: the look is not waited for)
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(calls.includes(DOOR + "/health"), calls.join(" "));
  calls = [];
  await voice.say("Back on the direct door.");
  assert.deepEqual(tails(calls), ["DOOR/t/1.a/tts"]);
});
