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

test("a line that is all in parentheses is an aside: shown, never spoken", () => {
  assert.equal(V.speakable("(my clone has uncommitted changes from an earlier task, so I'm not pulling)\nDone, Captain."), "Done, Captain.");
  assert.equal(V.speakable("It's live (commit 1f79b03), refresh."), "It's live (commit 1f79b03), refresh.");   // inline stays
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

// ── a loud room (the Captain, 2026-11-10: "when the car's fan is on, or music is playing, it keeps listening forever") ──
// A room is simulated as the loudness of the room alone every 60 ms (a steady fan with gusts, or music: its peaks go to 2.3 times its average) with a voice over it
// (words of 200-500 ms, gaps of 50-200 ms), seeded, so a failure is the same every run. The detector that had a cap of 0.06 and read a pause only by "every reading quiet"
// never ended 29 of 30 turns in light music, and every turn in loud music (tools in the session scratchpad: vad_sim.js).
function lcg(seed) { let s = (seed * 2654435761) >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }
function loudRoom(kind, mean, r) {
  return () => { if (kind === "fan") return mean * (0.8 + 0.4 * r()); const u = r(); return (mean / 1.2) * (0.4 + 2.4 * u * u); };
}
function voiceOver(S, r) {
  let word = true, left = 300;
  return () => { if (left <= 0) { word = !word; left = word ? 200 + r() * 300 : 50 + r() * 150; } left -= 60; return word ? S * (0.55 + 0.9 * r()) : S * 0.12 * r(); };
}
function simulate(kind, mean, S, seed, preMs, talkMs) {
  const r = lcg(seed), amb = loudRoom(kind, mean, r), say = voiceOver(S, r), vad = V.createVad(), events = [];
  let t = 0;
  for (let ms = 0; ms < preMs + talkMs + 9000; ms += 60) {
    t += 60;
    const a = amb(), v = ms >= preMs && ms < preMs + talkMs ? say() : 0;
    const e = vad.push(Math.sqrt(a * a + v * v), t); if (e) events.push([e, t]);
  }
  return events;
}
const cleanTurn = (ev, preMs, talkMs) => ev.length === 2 && ev[0][0] === "speech" && ev[0][1] >= preMs && ev[0][1] <= preMs + 900
  && ev[1][0] === "end" && ev[1][1] - (preMs + talkMs) >= 600 && ev[1][1] - (preMs + talkMs) <= 3200;

test("music playing: the voice begins once and its pause is found, turn after turn", () => {
  for (const [mean, voice] of [[0.12, 0.8], [0.2, 1.0]])
    for (let seed = 1; seed <= 30; seed++) {
      const ev = simulate("music", mean, voice, seed, 1500, 2600);
      assert.ok(cleanTurn(ev, 1500, 2600), `music ${mean} seed ${seed}: ${JSON.stringify(ev)}`);
    }
});

test("a loud steady fan (past the old cap of 0.06): the voice begins and ends", () => {
  for (const [mean, voice] of [[0.16, 0.7], [0.25, 1.0]])
    for (let seed = 1; seed <= 30; seed++) {
      const ev = simulate("fan", mean, voice, seed, 1500, 2600);
      assert.ok(cleanTurn(ev, 1500, 2600), `fan ${mean} seed ${seed}: ${JSON.stringify(ev)}`);
    }
});

test("a long sentence with pauses under a second is one utterance, in music too", () => {
  const r = lcg(7), amb = loudRoom("music", 0.1, r), say = voiceOver(0.6, r), vad = V.createVad(), ev = [];
  let t = 0;
  for (let ms = 0; ms < 1500 + 4 * 2800 + 6000; ms += 60) {                              // four stretches of 2 s of words, each followed by a 0.8 s pause
    t += 60;
    const into = ms - 1500, talking = into >= 0 && into < 4 * 2800 && into % 2800 < 2000, a = amb(), v = talking ? say() : 0;
    const e = vad.push(Math.sqrt(a * a + v * v), t); if (e) ev.push([e, t]);
  }
  assert.deepEqual(ev.map(e => e[0]), ["speech", "end"]);
});

test("a fan switched on after the room was read is a plateau, not a voice: noise after 4 s, and the next voice still counts", () => {
  for (let seed = 1; seed <= 5; seed++) {
    const r = lcg(seed), vad = V.createVad(), ev = []; let t = 0;
    for (let ms = 0; ms < 16000; ms += 60) {                                              // 0.03, then a steady 0.15 from 1.5 s, a voice from 10.5 s to 12.5 s
      t += 60;
      const lv = ms < 1500 ? 0.03 * (0.8 + 0.4 * r()) : ms < 10500 ? 0.15 * (0.9 + 0.2 * r()) : ms < 12500 ? 0.6 * (0.5 + r()) : 0.15 * (0.9 + 0.2 * r());
      const e = vad.push(lv, t); if (e) ev.push([e, t]);
    }
    assert.deepEqual(ev.map(e => e[0]), ["speech", "noise", "speech", "end"], JSON.stringify(ev));
    assert.ok(ev[1][1] - ev[0][1] >= 3500 && ev[1][1] - ev[0][1] <= 4600, `noise ${ev[1][1] - ev[0][1]} ms after it began`);
    assert.ok(ev[2][1] >= 10500 && ev[3][1] - 12500 >= 1000 && ev[3][1] - 12500 <= 2200, JSON.stringify(ev));
    assert.ok(vad.quiet > 0.1 && vad.quiet < 0.16, String(vad.quiet));                  // the room's new level
  }
});

test("a soft voice in a quiet room is still an utterance, and a quiet room is read as before", () => {
  assert.deepEqual(run(V.createVad(), [[0.004, 1000], [0.05, 1200], [0.004, 2500]]).map(e => e[0]), ["speech", "end"]);
  const v = V.createVad(); run(v, [[0.004, 2000]]);
  assert.equal(v.quiet, 0.012); assert.ok(Math.abs(v.threshold() - 0.0384) < 1e-9);       // the floor, and 3.2 times it
});

test("the room's quiet is read from the windows but the loudest, and may grow past 0.06", () => {
  const v = V.createVad(); run(v, [[0.1, 300], [0.1, 300]]);
  assert.ok(v.quiet > 0.09 && v.quiet < 0.101, String(v.quiet));
  const click = V.createVad(); run(click, [[0.004, 120], [0.004, 120], [0.4, 120], [0.004, 300]]);
  assert.ok(click.quiet < 0.1, "a click while it listens is not the room: " + click.quiet);
});
