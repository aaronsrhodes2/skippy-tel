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

test("library: his one favorites pool, favorites and Spotify liked, minus the songs he is tired of", () => {
  const q = D.buildQueue("library", { all, favorites: ["x/f.flac", "x/old.flac"], liked: ["spotify:track:0123456789abcdefghijkl"], tired: ["x/old.flac"] }, rng(3));
  assert.deepEqual(q.map(it => it.path || it.uri).sort(), ["spotify:track:0123456789abcdefghijkl", "x/f.flac"]);
  assert.deepEqual(D.buildQueue("nonsense", { all }), []);
});

test("discovery: his unplayed songs, never a favorite, liked, played or tired one, and Spotify's new songs one in four", () => {
  const S = n => "spotify:track:" + String(n).repeat(22).slice(0, 22);
  const favs = ["a/1.mp3", "a/2.mp3"], played = ["a/3.mp3", "a/4.mp3"], tired = ["a/5.mp3"], liked = [S(1)];
  const suggested = [S(1), S(2), S(3), S(4), S(5)];
  const q = D.buildQueue("discovery", { all, favorites: favs, liked, played, tired, suggested }, rng(7));
  const keys = q.map(it => it.path || it.uri);
  assert.equal(new Set(keys).size, keys.length);
  for (const k of [...favs, ...played, ...tired, ...liked]) assert.ok(!keys.includes(k), k);
  assert.equal(keys.filter(k => k.startsWith("a/")).length, all.length - 5);                       // every other song, once
  assert.deepEqual([3, 7, 11, 15].map(i => q[i].kind), ["spotify", "spotify", "spotify", "spotify"]);
  assert.equal(q.filter(it => it.kind === "spotify").length, 4);
});

test("discovery keeps Spotify's suggestions inside his normal genres", () => {
  const goth = "spotify:track:" + "g".repeat(22), polka = "spotify:track:" + "p".repeat(22), unknown = "spotify:track:" + "u".repeat(22);
  const genre = k => ({ "a/1.mp3": "Gothic Rock", [goth]: "Gothic Rock", [polka]: "Polka" })[k] || "";
  const q = D.buildQueue("discovery", { all, favorites: ["a/1.mp3"], suggested: [goth, polka, unknown], genre }, rng(2));
  const uris = q.filter(it => it.kind === "spotify").map(it => it.uri).sort();
  assert.deepEqual(uris, [goth, unknown].sort());                                                  // an artist Bilby doesn't know: his liked artists' own
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

test("local first: a Spotify song he owns plays from Bilby; the rest stay Spotify", () => {
  const lib = new Map([["Bauhaus/In the Flat Field/Dark Entries.mp3", { t: "Dark Entries", a: "Bauhaus" }],
                       ["Massive Attack/Mezzanine/Teardrop.mp3", { t: "Teardrop", a: "Massive Attack" }]]);
  const info = new Map([["spotify:track:1", { t: "Dark Entries - 2009 Remaster", a: "Bauhaus" }],
                        ["spotify:track:2", { t: "Teardrop (feat. Elizabeth Fraser)", a: "Massive Attack, Elizabeth Fraser" }],
                        ["spotify:track:3", { t: "Never Owned", a: "Somebody" }]]);
  assert.deepEqual(D.localFirst(["spotify:track:1", "spotify:track:2", "spotify:track:3", "x/y.mp3"], u => info.get(u), lib),
                   ["Bauhaus/In the Flat Field/Dark Entries.mp3", "Massive Attack/Mezzanine/Teardrop.mp3", "spotify:track:3", "x/y.mp3"]);
});

test("the hand-off: the newer of the current book and Bilby's last play, with no choosing", () => {
  const places = { current: "aces-1", books: { "aces-1": { pos: 2126.3, last_played: 2000, finished: false }, "old": { pos: 5, last_played: 1000 } } };
  const pool = new Set(["Fav/Song.mp3"]), inPool = t => pool.has(t);
  // the book is newer: that book, at its exact place
  assert.deepEqual(D.handOff(places, { at: 1500, track: "Fav/Song.mp3", pos: 61 }, inPool), { kind: "book", id: "aces-1", at: 2000, pos: 2126.3 });
  // Bilby is newer: that song where it was, in the shuffle it belongs to
  const b = D.handOff(places, { at: 2500, track: "Fav/Song.mp3", pos: 61, title: "Song" }, inPool);
  assert.equal(b.kind, "bilby"); assert.equal(b.mode, "library"); assert.equal(b.track, "Fav/Song.mp3"); assert.equal(b.pos, 61);
  assert.equal(D.handOff(places, { at: 2500, track: "Other/New.mp3", pos: 3 }, inPool).mode, "discovery");
  // a finished book never comes back; nothing known at all: his usual shuffle
  assert.equal(D.handOff({ current: "aces-1", books: { "aces-1": { pos: 9, last_played: 3000, finished: true } } }, null, inPool).kind, "bilby");
  assert.deepEqual(D.handOff(null, null, inPool), { kind: "bilby", mode: "library", track: "", pos: 0, at: 0, title: "", artist: "" });
  assert.equal(D.handOff(places, null, inPool).kind, "book");                 // no Bilby record: the book
  assert.equal(D.handOff(places, { at: 2000, track: "x" }, inPool).kind, "book");   // a tie goes to the book
});

// ── the glasses' captions (heard speech, seen signs): the original, then the English replacing it word by word ──
test("a caption shows the original, then each English word takes the place of the original ones, left to right", () => {
  const f = D.captionFrames("donde esta la estacion de tren", "where is the train station");
  assert.deepEqual(f[0], { en: "", orig: "donde esta la estacion de tren" });                 // first: only the original, to be read
  assert.deepEqual(f[f.length - 1], { en: "where is the train station", orig: "" });          // last: only the English
  assert.equal(f.length, 6);                                                                  // the original, then one frame per English word
  for (let i = 1; i < f.length; i++) {                                                        // monotonic: English only grows, the original only shrinks
    assert.ok(f[i].en.startsWith(f[i - 1].en)); assert.ok(f[i - 1].orig.endsWith(f[i].orig));
  }
  assert.equal(f[1].en, "where");
});

test("a caption with no original shows the English at once; words of different counts still end on all English", () => {
  assert.deepEqual(D.captionFrames("", "hello there"), [{ en: "hello there", orig: "" }]);
  assert.deepEqual(D.captionFrames("   ", "hello"), [{ en: "hello", orig: "" }]);
  assert.deepEqual(D.captionFrames("hola", "where is the train station").pop(), { en: "where is the train station", orig: "" });
  assert.deepEqual(D.captionFrames("a b c d e f g h", "yes").pop(), { en: "yes", orig: "" });
  assert.deepEqual(D.captionFrames("hola", ""), [{ en: "", orig: "hola" }]);                   // nothing to translate into: leave the original
});

test("the original is held long enough to read, but never longer than four seconds", () => {
  assert.equal(D.captionHoldMs("hola"), 900 + 55 * 4);
  assert.equal(D.captionHoldMs("x".repeat(500)), 4000);
  assert.equal(D.captionHoldMs(""), 900);
});

test("what was heard rides ahead of his next message to purple, as the chat's note does", () => {
  assert.equal(D.heardNote([]), ""); assert.equal(D.heardNote(null), ""); assert.equal(D.heardNote([{ text: "  ", lang: "es" }]), "");
  const note = D.heardNote([{ text: " where is the station ", lang: "es" }, { text: "hello", lang: "<script>" }]);
  assert.equal(note, "[Heard near me while Hands-off listened, translated into English by Whisper (other people talking, not me):\n"
    + "- (es) where is the station\n- (another language) hello]\n\n");                          // a bad language code never reaches the message
  const many = Array.from({ length: 14 }, (_, i) => ({ text: "line " + i, lang: "fr" }));
  assert.equal(D.heardNote(many).split("\n").filter((l) => l.startsWith("- (")).length, 10);   // the last ten
  assert.ok(D.heardNote([{ text: "x".repeat(900), lang: "de" }]).includes("x".repeat(400)) && !D.heardNote([{ text: "x".repeat(900), lang: "de" }]).includes("x".repeat(401)));
  assert.equal(D.langName("es"), "Spanish");
});

test("the strip's lamp: green from 40%, amber to 15%, red below; charging is always green; unknown is no colour", () => {
  assert.equal(D.battLevel(82, false), "ok");
  assert.equal(D.battLevel(40, false), "ok");
  assert.equal(D.battLevel(39, false), "low");
  assert.equal(D.battLevel(15, false), "low");
  assert.equal(D.battLevel(14, false), "crit");
  assert.equal(D.battLevel(5, true), "ok");
  for (const bad of [null, undefined, NaN, "80"]) assert.equal(D.battLevel(bad, false), "", String(bad));
});

test("heat: SEVERE and worse is hot, moderate or a battery at 42 degrees is warm, anything cooler is nothing", () => {
  assert.equal(D.HOT_STATUS, 3);
  for (const s of [3, 4, 5, 6]) assert.equal(D.heatLevel(s, 30), "hot", String(s));
  assert.equal(D.heatLevel(2, 30), "warm");
  assert.equal(D.heatLevel(0, 42), "warm");
  assert.equal(D.heatLevel(1, 41.9), "");
  assert.equal(D.heatLevel(undefined, null), "");
  assert.equal(D.heatLevel(0, 55), "warm");
});

test("the lamp's words: the charge, a plus while charging, and the glasses' heat only when the host is there", () => {
  assert.equal(D.lampText(82.4, false, 38.2, true), "82 · 38°");
  assert.equal(D.lampText(82.4, true, 43.6, true), "82+ · 44°");
  assert.equal(D.lampText(82.4, false, 38.2, false), "82");
  assert.equal(D.lampText(null, false, null, true), "");
  assert.equal(D.lampText(null, false, 41, true), " · 41°");
});

test("pinned captions: the head's turn from the pose, the short way round, hidden beyond half the view", () => {
  const rot = deg => ({ qw: Math.cos(deg * Math.PI / 360), qx: 0, qy: 0, qz: Math.sin(deg * Math.PI / 360) });         // about the vertical (Z up)
  assert.equal(D.yawDeg({ qw: 1, qx: 0, qy: 0, qz: 0 }), 0);
  for (const deg of [-120, -90, -30, 10, 90, 135]) assert.ok(Math.abs(D.yawDeg(rot(deg)) - deg) < 1e-9, String(deg));
  assert.ok(Math.abs(D.yawDeg({ qw: Math.cos(Math.PI / 12), qx: 0, qy: Math.sin(Math.PI / 12), qz: 0 })) < 1e-9);       // nodding 30 degrees is not turning
  for (const bad of [null, undefined, {}, { qw: 1 }, { qw: "1", qx: 0, qy: 0, qz: 0 }]) assert.equal(D.yawDeg(bad), null);
  assert.deepEqual(D.pinOffset(5, 5, 1920), { dx: 0, show: true });
  const left = D.pinOffset(10, 0, 1920);
  assert.ok(left.show && Math.abs(left.dx - 10 * 1920 / D.FOV_DEG) < 1e-9);                                          // turned left: the room slides right
  assert.ok(D.pinOffset(-10, 0, 1920).dx < 0);
  assert.equal(D.pinOffset(23, 0, 1920).show, true);
  assert.equal(D.pinOffset(23.5, 0, 1920).show, false);
  assert.equal(D.pinOffset(-30, 0, 1920).show, false);
  const wrap = D.pinOffset(-179, 179, 1000);                                                                         // across the +-180 seam: two degrees, not 358
  assert.ok(wrap.show && Math.abs(wrap.dx - 2 * 1000 / D.FOV_DEG) < 1e-9);
  assert.deepEqual(D.pinOffset(null, 5, 1920), { dx: 0, show: true });                                              // no pose: the row stays where it is
});

test("the song to fetch ahead is the next playable Bilby row: never a Spotify row, never nothing", () => {
  const q = [{ kind: "bilby", path: "a" }, { kind: "spotify", uri: "spotify:track:x" }, { kind: "bilby", path: "c" }];
  assert.equal(D.warmTarget(q, 0, false), "c");        // Spotify rows are off in the car: the next song is c
  assert.equal(D.warmTarget(q, 0, true), "");          // a Spotify row is next: it plays on the car's Spotify, there is nothing to fetch
  assert.equal(D.warmTarget(q, 2, false), "");         // the end of the queue
  assert.equal(D.warmTarget([], -1, false), "");
});

test("the next song is fetched ahead only once the playing one is wholly downloaded, so the two never share a slow link", () => {
  assert.equal(D.songFullyBuffered(180, 0), false);          // nothing buffered yet
  assert.equal(D.songFullyBuffered(180, 90), false);         // half of it
  assert.equal(D.songFullyBuffered(180, 177.9), false);      // still two seconds short
  assert.equal(D.songFullyBuffered(180, 178), true);         // the end, within the browser's rounding
  assert.equal(D.songFullyBuffered(180, 180), true);
  assert.equal(D.songFullyBuffered(0, 0), false);            // no duration known (a stream, or metadata not loaded): not yet
  assert.equal(D.songFullyBuffered(NaN, 10), false);
});
