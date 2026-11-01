// The Drive page's pure parts: the two Bilby Shuffles (static/drive-pure.js), the duck (static/skippy-duck.js), the voice's duck state
// (static/skippy-voice.js duckState) and the in-browser wake word's pipeline and spotter (static/skippy-wake.js), under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const D = require("../../static/drive-pure.js");
const Duck = require("../../static/skippy-duck.js");
const V = require("../../static/skippy-voice.js");
const W = require("../../static/skippy-wake.js");

function rng(seed) { let s = seed; return () => { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; }; }
const all = Array.from({ length: 200 }, (_, i) => `a/${i}.mp3`);

test("discovery: every song once, his favorites about one in four, nothing twice", () => {
  const favs = ["a/1.mp3", "a/2.mp3", "x/fav1.flac", "x/fav2.flac", "spotify:track:0123456789abcdefghijkl"];
  const q = D.buildQueue("discovery", { all, favorites: favs }, rng(7));
  const keys = q.map(it => it.path || it.uri);
  assert.equal(new Set(keys).size, keys.length);
  for (const p of all) assert.ok(keys.includes(p));
  const firstFav = keys.findIndex(k => favs.includes(k));
  assert.ok(firstFav >= 0 && firstFav <= D.FAV_EVERY, "a favorite comes early");
  assert.ok(q.some(it => it.kind === "spotify"));
});

test("library: only favorites and Spotify liked songs", () => {
  const q = D.buildQueue("library", { all, favorites: ["x/f.flac"], liked: ["spotify:track:0123456789abcdefghijkl"] }, rng(3));
  assert.deepEqual(q.map(it => it.path || it.uri).sort(), ["spotify:track:0123456789abcdefghijkl", "x/f.flac"]);
  assert.deepEqual(D.buildQueue("nonsense", { all }), []);
});

test("the browser skips what it can't play itself", () => {
  const q = [{ kind: "spotify", uri: "spotify:track:x" }, { kind: "bilby", path: "a" }];
  assert.equal(D.nextPlayable(q, 0, false), 1);
  assert.equal(D.nextPlayable(q, 0, true), 0);
  assert.equal(D.nextPlayable(q, 2, false), -1);
});

test("the house brain's answer is the first typed 'said' after the turn", () => {
  const ev = [{ seq: 4, kind: "said", typed: true, text: "old" }, { seq: 5, kind: "heard", text: "hi" }, { seq: 6, kind: "said", text: "at a pod" },
              { seq: 7, kind: "said", typed: true, text: "Hello, Captain" }];
  assert.deepEqual(D.replyAfter(ev, 5), { text: "Hello, Captain" });
  assert.equal(D.replyAfter(ev.slice(0, 3), 5), null);
  assert.ok(D.replyAfter([{ seq: 9, kind: "note", text: "Forwarding is off, so this was not answered." }], 5).why);
});

test("the duck: down while Skippy talks, given back after; a system pause meanwhile is undone", () => {
  let t = 0;
  const f = Duck.createFollower({ now: () => t });
  assert.equal(f.set("listen").gain, Duck.LEVELS.listen);
  assert.equal(f.set("speak").gain, Duck.LEVELS.speak);
  f.paused(false);                                  // the car took the sound while he spoke
  const r = f.set(null);
  assert.equal(r.gain, 1); assert.equal(r.resume, true);
  f.set("speak"); f.paused(true);                   // his own pause key: stays paused
  assert.equal(f.set(null).resume, false);
  f.paused(false);                                  // a pause with no duck on is nobody's business
  f.set("listen"); assert.equal(f.set(null).resume, false);
  f.set("speak"); t += Duck.STALE_MS + 1; assert.ok(f.stale());
});

test("the voice's duck state", () => {
  assert.equal(V.duckState({ speaking: true }), "speak");
  assert.equal(V.duckState({ chat: false }), null);
  assert.equal(V.duckState({ chat: true, awaiting: true, now: 1e9 }), "listen");
  assert.equal(V.duckState({ chat: true, hearing: true, now: 1e9 }), "listen");
  assert.equal(V.duckState({ chat: true, turnAt: 1e9 - 1000, now: 1e9 }), "listen");
  assert.equal(V.duckState({ chat: true, turnAt: 1e9 - V.DUCK_HOLD_MS - 1, now: 1e9 }), null);    // nobody talking: the music comes back up
});

test("wake word: 48 kHz to 16 kHz, and the pipeline's shapes match openWakeWord's", async () => {
  const rs = new W.Resampler(48000);
  const out = [].concat(rs.push(new Float32Array(4800).fill(0.5)), rs.push(new Float32Array(4800).fill(0.5)));
  assert.equal(out.length, 3200);
  assert.ok(out.every(v => v === 16384 || v === 16383));
  const calls = [];
  const pipe = W.createPipeline(async (name, data, dims) => {
    calls.push([name, dims.join("x")]);
    if (name === "mel") return new Float32Array(8 * 32);
    if (name === "emb") return new Float32Array(96).fill(1);
    return Float32Array.of(0.9);
  });
  const scores = await pipe.feed(new Array(W.FRAME * 17).fill(0));
  assert.equal(scores.length, 2);                   // 16 embeddings before the first score
  assert.deepEqual(calls.slice(0, 2), [["mel", "1x1280"], ["emb", "1x76x32x1"]]);
  assert.ok(calls.some(c => c[0] === "mel" && c[1] === "1x1760"));
  assert.ok(calls.some(c => c[0] === "word" && c[1] === "1x16x96"));
});

test("wake word: two frames in a row at the threshold, then deaf", () => {
  const s = new W.Spotter({ threshold: 0.2, patience: 2, refractoryMs: 1500 });
  assert.equal(s.score(0.5), false); assert.equal(s.score(0.5), true);
  assert.equal(s.score(0.9), false); assert.equal(s.score(0.9), false);      // deaf
  for (let i = 0; i < 20; i++) s.score(0);
  assert.equal(s.score(0.3), false); assert.equal(s.score(0.3), true);
});

test("a Spotify row on the car is over when the album moves on, the car stops, or its length has passed", () => {
  const row = { uri: "spotify:track:a", at: 0, ms: 200000, seen: true, pausedMs: 0, from: 0 };
  assert.equal(D.carDone({ uri: "spotify:track:a", playing: true }, row, 60000), false);
  assert.equal(D.carDone({ uri: "spotify:track:b", playing: true }, row, 190000), true);          // the next album track
  assert.equal(D.carDone({ uri: "spotify:track:b" }, row, 1000), false);                            // the car still on the old song right after play
  assert.equal(D.carDone({ stopped: true, uri: "" }, row, 90000), true);
  assert.equal(D.carDone({ stopped: true, uri: "" }, Object.assign({}, row, { seen: false }), 2000), false);    // not started yet
  assert.equal(D.carDone(null, row, 200000 + D.CAR_MARGIN_MS + 1), true);                          // the car says nothing: its length
  assert.equal(D.carDone(null, Object.assign({}, row, { pausedMs: 60000 }), 200000 + D.CAR_MARGIN_MS + 1), false);   // he paused a minute
});

test("library mode plays his Spotify liked songs; discovery adds the suggestions", () => {
  const liked = ["spotify:track:" + "1".repeat(22)], suggested = ["spotify:track:" + "2".repeat(22)];
  const lib = D.buildQueue("library", { all, favorites: [], liked, suggested }, rng(1));
  assert.deepEqual(lib.map(i => i.uri), liked);
  const disc = D.buildQueue("discovery", { all, favorites: [], liked: [], suggested }, rng(1));
  assert.ok(disc.some(i => i.uri === suggested[0]));
});
