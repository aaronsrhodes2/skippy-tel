// Skippy's voice client: the pure helpers (portal/static/skippy-voice.js), under Node's built-in test runner.
// portal/tests/test_voice.py runs this when Node >= 18 is on the machine. (Its own folder, like the video player's.)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const V = require("../../../static/skippy-voice.js");

test("pickMime takes the first recording type the browser can make, or none", () => {
  assert.equal(V.pickMime(m => m === "audio/webm"), "audio/webm");
  assert.equal(V.pickMime(m => m.startsWith("audio/mp4")), "audio/mp4");        // Safari
  assert.equal(V.pickMime(() => true), "audio/webm;codecs=opus");               // Chrome and Firefox: opus first
  assert.equal(V.pickMime(() => false), "");                                    // let the browser choose
  assert.equal(V.pickMime(() => { throw new Error("no"); }), "");
});

test("speakable drops code chips, links and markdown marks", () => {
  assert.equal(V.speakable("Use `docker ps` and see https://x.test/a now"), "Use docker ps and see now");
  assert.equal(V.speakable("**Bold** _this_ # head"), "Bold this head");
  assert.equal(V.speakable(""), "");
  assert.equal(V.speakable(null), "");
});

test("speakable never exceeds the gate's 500 characters and cuts at a sentence", () => {
  const long = ("This is a sentence that goes on. ").repeat(40);
  const out = V.speakable(long);
  assert.ok(out.length <= V.MAX_SPEECH);
  assert.ok(out.endsWith("."));
  assert.ok(V.speakable("word ".repeat(300)).length <= V.MAX_SPEECH);
});

// A microphone, simulated: levels every 60 ms.
function run(vad, levels) {
  const events = []; let t = 0;
  for (const [lv, ms] of levels) for (let i = 0; i < ms; i += 60) { t += 60; const e = vad.push(lv, t); if (e) events.push([e, t]); }
  return events;
}

test("the silence detector hears speech begin once, and the end only after a pause", () => {
  const ev = run(V.createVad(), [[0.004, 1000], [0.12, 1200], [0.004, 1000], [0.004, 1000]]);
  assert.deepEqual(ev.map(e => e[0]), ["speech", "end"]);
  assert.ok(ev[0][1] >= 1000 && ev[0][1] <= 1400);                                    // began when the voice did
  assert.ok(ev[1][1] - 2200 >= 1300 && ev[1][1] - 2200 <= 1700, String(ev[1][1]));    // ended about 1.4 s into the pause
});

test("a short pause inside a sentence does not end it", () => {
  const ev = run(V.createVad(), [[0.004, 800], [0.12, 1000], [0.004, 700], [0.12, 1000], [0.004, 1800]]);
  assert.deepEqual(ev.map(e => e[0]), ["speech", "end"]);                               // one utterance, not two
});

test("nothing is heard in a quiet room, and a steady noisy room is not speech", () => {
  assert.deepEqual(run(V.createVad(), [[0.004, 3000]]), []);
  const noisy = run(V.createVad(), [[0.05, 2500], [0.35, 800], [0.05, 2000]]);          // fan noise, then a voice well above it
  assert.deepEqual(noisy.map(e => e[0]), ["speech", "end"]);
  assert.ok(noisy[0][1] >= 2500);                                                       // the fan alone never counted
});

test("a click is not speech", () => {
  assert.deepEqual(run(V.createVad(), [[0.004, 600], [0.2, 60], [0.004, 3000]]), []);
});

test("saying stop alone cancels the send; stop inside a sentence does not", () => {
  for (const yes of ["stop", "Stop.", "cancel", "Wait!", "hold on", "don't send", "Don't send it.", "never mind", "no", "okay stop", "abort"]) assert.ok(V.STOP_RE.test(yes), yes);
  for (const no of ["stop the server and redeploy", "no I want it orange", "please wait for the deploy", "cancel the subscription", "go", "stopwatch"]) assert.ok(!V.STOP_RE.test(no), no);
  assert.equal(V.SEND_DELAY_MS, 4000);
});

test("the wake word: 'Skippy' anywhere near the start wakes it, and what follows is the message", () => {
  const cases = [["Hey Skippy, turn that down", "turn that down"], ["Skippy.", ""], ["OK Skippy", ""], ["um skippy what time is it", "what time is it"],
                 ["Hi Skippie! play some music", "play some music"], ["skippy,   lights off", "lights off"]];
  for (const [said, rest] of cases) assert.deepEqual(V.wakeSplit(said), { woke: true, rest }, said);
});

test("room talk without the word never wakes it", () => {
  for (const said of ["the skipping rope", "what are we having for dinner", "skip this one", "", null, "my friend Skip came by", "x ".repeat(40) + "skippy"])
    assert.equal(V.wakeSplit(said).woke, false, String(said));                                    // (a word after the first 60 characters is not an address)
});
