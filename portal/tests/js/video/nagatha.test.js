// Nagatha's Video Service: the player's pure logic (portal/static/nagatha-core.js), under Node's built-in test runner.
// portal/tests/test_video_js.py runs this when Node >= 18 is on the machine and skips cleanly when it is not. (Its own folder, so
// the music player's runner, which globs portal/tests/js/*.test.js, doesn't run it a second time.)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../../../static/nagatha-core.js");

const id = n => n.toString(16).padStart(32, "0");
const film = (n, title, extra) => Object.assign({
  id: id(n), type: "movie", title, sort: title.toLowerCase(), year: 2000 + n, runtime: 6000, overview: "", genres: ["Drama"], rating: 7.1,
  official: "PG", added: 1700000000 + n, collectionIds: [], hasPoster: true, hasBackdrop: true, resume: 0, played: false,
  play: { container: "mp4", vcodec: "h264", acodec: "aac", height: 480, direct: true, audio: [{ i: 1, lang: "eng", codec: "aac", ch: 2 }], subs: [] },
}, extra || {});
const catalog = {
  v: 1,
  items: [
    film(1, "Airplane!", { sort: "airplane!", year: 1980, genres: ["Comedy"], play: { container: "mp4", vcodec: "h264", acodec: "mp2", height: 480, direct: false,
      audio: [{ i: 1, lang: "eng", codec: "mp2", ch: 2 }, { i: 2, lang: "fra", codec: "mp2", ch: 2 }], subs: [] } }),
    film(2, "Amélie", { sort: "amelie", year: 2001, added: 1800000000, genres: ["Romance", "Comedy"] }),
    film(3, "The Matrix", { sort: "matrix", year: 1999, resume: 1200 }),
    film(4, "The Matrix Reloaded", { sort: "matrix reloaded", year: 2003, played: true, resume: 0 }),
    film(5, "Tetsuo II: Body Hammer", { sort: "tetsuo ii", year: 1992, play: { container: "mkv", vcodec: "h264", acodec: "aac", height: 720, direct: false,
      audio: [{ i: 1, lang: "jpn", codec: "aac", ch: 2 }], subs: [{ i: 2, lang: "eng", codec: "subrip", external: false, vtt: true }] } }),
    film(6, "Citizen Kane", { sort: "citizen kane", runtime: 0, added: 0, play: { container: "", vcodec: "", acodec: "", height: 0, direct: false, audio: [], subs: [] } }),
    { id: id(7), type: "series", title: "A Show", sort: "show", play: null },
    { id: "not-an-id", type: "movie", title: "Broken" },
    film(1, "Duplicate of Airplane!"),
  ],
  collections: [
    { id: id(20), title: "The Matrix Collection", sort: "matrix collection", itemIds: [id(3), id(4), id(99)], hasPoster: true, hasBackdrop: false },
    { id: "bad", title: "Bad" },
  ],
};
const lib = () => C.parseCatalog(catalog);

// ── the catalog ─────────────────────────────────────────────────────────────────────────────────────────────
test("the catalog is indexed, sorted by the gate's sort name, and rows it can't use are dropped, not fatal", () => {
  const L = lib();
  assert.equal(L.items.length, 7);                                    // the bad id and the duplicate are gone
  assert.deepEqual(L.items.map(i => i.title).slice(0, 3), ["Airplane!", "Amélie", "Citizen Kane"]);
  assert.equal(L.movies.length, 6);                                   // the series is a shelf, not a movie
  assert.equal(L.byId.get(id(7)).play, null);
  assert.equal(L.collections.length, 1);
  assert.deepEqual(L.collById.get(id(20)).itemIds, [id(3), id(4)]);   // a member the catalog doesn't have is left out
  assert.deepEqual(L.byId.get(id(3)).collectionIds, [id(20)]);        // and each member knows its collection
  assert.throws(() => C.parseCatalog({ fields: [], tracks: [] }), /not a video catalog/);   // a MUSIC catalog is not one
});

test("search ignores case, accents and punctuation; title matches come first; every word must match", () => {
  const L = lib();
  assert.deepEqual(C.search(L, "amelie").items.map(i => i.title), ["Amélie"]);
  assert.deepEqual(C.search(L, "AIRPLANE").items.map(i => i.title), ["Airplane!"]);
  assert.deepEqual(C.search(L, "matrix").items.map(i => i.title), ["The Matrix", "The Matrix Reloaded"]);
  assert.deepEqual(C.search(L, "matrix").collections.map(c => c.title), ["The Matrix Collection"]);
  assert.deepEqual(C.search(L, "comedy").items.map(i => i.title), ["Airplane!", "Amélie"]);     // genres
  assert.deepEqual(C.search(L, "2001").items.map(i => i.title), ["Amélie"]);                    // years
  assert.deepEqual(C.search(L, "reloaded matrix").items.map(i => i.title), ["The Matrix Reloaded"]);
  assert.deepEqual(C.search(L, "tetsuo hammer").items.map(i => i.title), ["Tetsuo II: Body Hammer"]);
  assert.deepEqual(C.search(L, "tetsuo matrix").items, []);                                   // every word, not any
  assert.deepEqual(C.search(L, "  ").items, []);
  const t = C.search(L, "c");                                                                    // a title starting with it beats a genre hit
  assert.equal(t.items[0].title, "Citizen Kane");
});

test("recently added is newest first, and a film with no date is left off", () => {
  assert.deepEqual(C.recentlyAdded(lib(), 3).map(i => i.title), ["Amélie", "Tetsuo II: Body Hammer", "The Matrix Reloaded"]);
  assert.ok(!C.recentlyAdded(lib(), 50).some(i => i.title === "Citizen Kane"));
});

// ── resume ──────────────────────────────────────────────────────────────────────────────────────────────────
test("resume: the gate's point, or this browser's when it is newer than the catalog; never the first 30 s or the credits", () => {
  const L = lib(), m = L.byId.get(id(3)), at = 1_000_000;
  assert.equal(C.resumePoint(m), 1200);
  assert.equal(C.resumePoint(m, { pos: 1500.7, at: at + 1 }, at), 1500);               // saved after the catalog came: it wins
  assert.equal(C.resumePoint(m, { pos: 1500, at: at - 1 }, at), 1200);                 // older than the catalog: the gate knows better
  assert.equal(C.resumePoint(m, { pos: 20, at: at + 1 }, at), 0);                      // 20 s in: start from the top
  assert.equal(C.resumePoint(m, { pos: 5900, at: at + 1 }, at), 0);                    // 98 %: that was the credits
  assert.equal(C.resumePoint(m, { pos: 3000, at: at + 1, done: true }, at), 0);        // finished here
  assert.equal(C.resumePoint(L.byId.get(id(4))), 0);                                   // watched
  assert.equal(C.resumePoint(L.byId.get(id(6)), { pos: 500, at: 5 }), 500);            // no runtime known: no credits rule
  assert.equal(C.resumePoint(null), 0);
});

test("continue watching: part-watched films, the ones this browser touched last first", () => {
  const L = lib();
  assert.deepEqual(C.continueWatching(L, {}).map(i => i.title), ["The Matrix"]);
  const local = { [id(2)]: { pos: 600, at: 50 }, [id(3)]: { pos: 1300, at: 40 } };
  assert.deepEqual(C.continueWatching(L, local, 10).map(i => i.title), ["Amélie", "The Matrix"]);
});

test("remember keeps the newest 200 films and rounds the position", () => {
  let local = {};
  for (let n = 0; n < 205; n++) local = C.remember(local, id(n + 100), n + 0.04, n);
  assert.equal(Object.keys(local).length, 200);
  assert.ok(!(id(100) in local) && id(304) in local);
  assert.deepEqual(local[id(304)], { pos: 204, at: 204, done: false });
});

// ── which way a film is played ──────────────────────────────────────────────────────────────────────────────
test("direct when the gate says so and the first audio is wanted; HLS otherwise; nothing when the browser has no HLS", () => {
  const L = lib(), chrome = { native: false, mse: true, webkit: false }, safari = { native: true, mse: true, webkit: true };
  const matrix = L.byId.get(id(3)), airplane = L.byId.get(id(1));
  assert.deepEqual(C.chooseSource(matrix, { env: chrome }), { mode: "direct" });
  assert.deepEqual(C.chooseSource(matrix, { env: chrome, audio: 1 }), { mode: "direct" });          // the first track IS direct
  assert.deepEqual(C.chooseSource(airplane, { env: chrome }), { mode: "hls", engine: "hlsjs" });
  assert.deepEqual(C.chooseSource(airplane, { env: safari }), { mode: "hls", engine: "native" });
  const twoAudio = Object.assign({}, matrix, { play: Object.assign({}, matrix.play, { audio: [{ i: 1 }, { i: 2 }] }) });
  assert.equal(C.chooseSource(twoAudio, { env: chrome, audio: 2 }).mode, "hls");                    // a plain MP4 can't switch audio
  assert.equal(C.chooseSource(matrix, { env: chrome, forceHls: true }).mode, "hls");                // the browser refused the file
  assert.equal(C.chooseSource(airplane, { env: { native: false, mse: false } }).mode, "none");
  assert.equal(C.chooseSource(L.byId.get(id(7)), { env: chrome }).mode, "none");                    // a series has no file
});

test("the HLS engine: Safari's own where it is WebKit, hls.js wherever Media Source exists, else native or none", () => {
  assert.equal(C.hlsEngine({ native: true, webkit: true, mse: true }), "native");
  assert.equal(C.hlsEngine({ native: true, webkit: false, mse: true }), "hlsjs");     // a Chrome claiming "maybe": hls.js
  assert.equal(C.hlsEngine({ native: true, webkit: false, mse: false }), "native");
  assert.equal(C.hlsEngine({ native: false, mse: true }), "hlsjs");
  assert.equal(C.hlsEngine({}), "none");
});

test("gate URLs carry the pass in the path and only parameters the gate accepts", () => {
  const g = { ok: true, url: "https://x.ts.net:8443", pass: "1800000000.0123456789abcdef0123456789abcdef", exp: 1800000000 };
  const L = lib(), air = L.byId.get(id(1)), kane = L.byId.get(id(6));
  const t = `https://x.ts.net:8443/t/${g.pass}`;
  assert.equal(C.catalogUrl(g), `${t}/catalog.json`);
  assert.equal(C.imgUrl(g, id(1), "poster", 320), `${t}/img/${id(1)}/poster?w=320`);
  assert.equal(C.directUrl(g, id(3)), `${t}/v/${id(3)}/direct`);
  assert.equal(C.subUrl(g, id(5), 2), `${t}/sub/${id(5)}/2.vtt`);
  assert.equal(C.progressUrl(g), `${t}/progress`);
  assert.equal(C.hlsUrl(g, air, {}), `${t}/v/${id(1)}/hls/master.m3u8`);
  assert.equal(C.hlsUrl(g, air, { audio: 2, start: 1520.9 }), `${t}/v/${id(1)}/hls/master.m3u8?audio=2&start=1520`);
  assert.equal(C.hlsUrl(g, air, { audio: 9, start: 99999 }), `${t}/v/${id(1)}/hls/master.m3u8`);   // not its track, past its end: left out
  assert.equal(C.hlsUrl(g, kane, { start: 300 }), `${t}/v/${id(6)}/hls/master.m3u8`);              // runtime 0: the gate would refuse a start
});

test("picture sizes snap to the gate's and stay sharp on a dense screen", () => {
  assert.deepEqual(C.IMG_WIDTHS, [160, 320, 640, 1280]);
  assert.equal(C.imgWidth(120, 1), 160);
  assert.equal(C.imgWidth(120, 2), 320);
  assert.equal(C.imgWidth(160, 3), 640);
  assert.equal(C.imgWidth(900, 1), 1280);
  assert.equal(C.imgWidth(2000, 2), 1280);
  assert.equal(C.imgWidth(100, 0), 160);
});

test("a pass is fresh with half an hour left, and a new one is asked for half an hour before the end", () => {
  const g = { ok: true, url: "u", pass: "p", exp: 10000 };
  assert.ok(C.passFresh(g, 10000 - 1801));
  assert.ok(!C.passFresh(g, 10000 - 1800));
  assert.ok(!C.passFresh({ ok: false, exp: 99999 }, 0) && !C.passFresh(null, 0));
  assert.equal(C.refreshIn(g, 0), 8200);
  assert.equal(C.refreshIn(g, 9990), 60);                             // never a busy loop
});

// ── progress ────────────────────────────────────────────────────────────────────────────────────────────────
test("progress: start, then at most one report per 10 s while playing, at once on a pause, and one stop", () => {
  let now = 0;
  const p = new C.Progress(() => now, 10);
  assert.equal(p.tick(5, true), null);                                // nothing before start
  assert.deepEqual(p.start(id(3), 1200), { id: id(3), state: "start", pos: 1200 });
  now = 5; assert.equal(p.tick(1205, true), null);
  now = 10; assert.deepEqual(p.tick(1210.04, true), { id: id(3), state: "progress", pos: 1210 });
  now = 15; assert.equal(p.tick(1215, true), null);
  now = 25; assert.equal(p.tick(1225, false), null);                  // paused: the pause already reported it
  assert.deepEqual(p.pause(1226), { id: id(3), state: "progress", pos: 1226 });
  assert.equal(p.pause(1226.5), null);                                // nothing moved
  assert.deepEqual(p.stop(1300, false), { id: id(3), state: "stop", pos: 1300 });
  assert.equal(p.stop(1300), null);                                   // only once
  now = 100; assert.equal(p.tick(1400, true), null);
  p.start(id(4), 0);
  assert.deepEqual(p.stop(5000, true), { id: id(4), state: "stop", pos: 5000, played: true });
  assert.deepEqual(new C.Progress(() => 0).start(id(1), -3), { id: id(1), state: "start", pos: 0 });
});

// ── keys ────────────────────────────────────────────────────────────────────────────────────────────────────
test("keys: the player's only while a film is open, never while typing, never with Ctrl/Cmd/Alt", () => {
  const k = (key, more) => Object.assign({ key }, more || {});
  assert.equal(C.keyAction(k(" "), true), "toggle");
  assert.equal(C.keyAction(k("k"), true), "toggle");
  assert.equal(C.keyAction(k(" ", { onButton: true }), true), null);        // Space on a focused button presses that button
  assert.equal(C.keyAction(k("ArrowLeft"), true), "back10");
  assert.equal(C.keyAction(k("ArrowRight", { shiftKey: true }), true), "fwd30");
  assert.equal(C.keyAction(k("f"), true), "fullscreen");
  assert.equal(C.keyAction(k("M"), true), "mute");
  assert.equal(C.keyAction(k("c"), true), "subs");
  assert.equal(C.keyAction(k("p"), true), "pip");
  assert.equal(C.keyAction(k("Escape"), true), "close");
  assert.equal(C.keyAction(k("ArrowUp"), true), "volUp");
  assert.equal(C.keyAction(k("f"), false), null);                           // no film open
  assert.equal(C.keyAction(k("/"), false), "search");
  assert.equal(C.keyAction(k("?"), false), "help");
  assert.equal(C.keyAction(k("f", { targetTag: "input" }), true), null);
  assert.equal(C.keyAction(k(" ", { editable: true }), true), null);
  assert.equal(C.keyAction(k("f", { ctrlKey: true }), true), null);
  assert.equal(C.keyAction(k("ArrowLeft", { metaKey: true }), true), null);
  assert.equal(C.keyAction(null, true), null);
});

test("a seek stays inside the film, and C turns subtitles on (the first track) and off", () => {
  assert.equal(C.seekTo(5, -10, 100), 0);
  assert.equal(C.seekTo(95, 10, 100), 99.5);
  assert.equal(C.seekTo(50, 10, NaN), 60);
  assert.equal(C.nextSub(-1, 1), 0);
  assert.equal(C.nextSub(0, 1), -1);
  assert.equal(C.nextSub(-1, 0), -1);
});

test("the autoplay state: a reload waits for a tap; a play with sound proves it; a refusal asks again", () => {
  const a = new C.Armed(() => undefined);
  assert.equal(a.armed, false);
  a.waiting(); assert.equal(a.tap, true);
  a.played(false); assert.equal(a.armed, false);                     // a muted play proves nothing about sound
  a.played(true); assert.equal(a.armed, true); assert.equal(a.tap, false);
  a.refused(); assert.equal(a.armed, false); assert.equal(a.tap, true);
  assert.equal(new C.Armed(() => true).armed, true);                  // the browser says the page was tapped
});

// ── words ───────────────────────────────────────────────────────────────────────────────────────────────────
test("times, runtimes, languages and the facts line", () => {
  assert.equal(C.fmtTime(0), "0:00");
  assert.equal(C.fmtTime(65.9), "1:05");
  assert.equal(C.fmtTime(5442), "1:30:42");
  assert.equal(C.fmtRuntime(5442), "1 h 31 min");
  assert.equal(C.fmtRuntime(3600), "1 h");
  assert.equal(C.fmtRuntime(2700), "45 min");
  assert.equal(C.fmtRuntime(0), "");
  assert.equal(C.langName("eng"), "English");
  assert.equal(C.langName("FRA"), "French");
  assert.equal(C.langName("und"), "");
  assert.equal(C.langName("xyz"), "XYZ");
  assert.deepEqual(C.audioLabels([{ i: 1, lang: "eng", codec: "aac", ch: 6 }, { i: 2, lang: "fra", codec: "aac", ch: 6 }, { i: 3, lang: "eng", codec: "aac", ch: 6 }]),
    ["English · 5.1 · AAC (1)", "French · 5.1 · AAC", "English · 5.1 · AAC (2)"]);
  assert.equal(C.subLabel({ lang: "" }), "Subtitles");
  assert.equal(C.factsLine(lib().byId.get(id(3))), "1999 · 1 h 40 min · PG · ★ 7.1");
  assert.match(C.playProblem(429, "hls"), /converting two films/);
  assert.match(C.playProblem(0), /Tailscale Funnel/);
});


// ── the wall is posters a screen at a time ───────────────────────────────────────────────────────────────────────────
test("posters fit the width: at least two across, never more than eight, and the row is as tall as its posters", () => {
  const phone = C.gridShape(375), wide = C.gridShape(1600), tiny = C.gridShape(200), tv = C.gridShape(3000);
  assert.equal(phone.cols, 3); assert.ok(phone.cw >= 100 && phone.cw <= 125);
  assert.equal(tiny.cols, 2); assert.equal(tv.cols, 8); assert.ok(wide.cols > phone.cols);
  assert.equal(phone.rowH, Math.round(phone.cw * 1.5) + C.TEXT_H + 10);
  assert.ok(phone.cols * phone.cw + (phone.cols - 1) * 10 <= 375 - 24 + 0.001);              // inside the gutters
  assert.equal(C.gridShape(undefined).cols, 2);
});

test("chunk cuts a list into rows", () => {
  assert.deepEqual(C.chunk([1, 2, 3, 4, 5], 3), [[1, 2, 3], [4, 5]]);
  assert.deepEqual(C.chunk([], 3), []);
  assert.deepEqual(C.chunk([1, 2], 0), [[1], [2]]);                                           // never an endless loop
});

test("a title files under its first letter; a digit or a symbol under #", () => {
  assert.deepEqual(["Airplane!", "amélie", "(500) Days", "2001", "Ørnen", "…And Justice"].map(C.letterOf), ["A", "A", "#", "#", "O", "A"]);
  assert.equal(C.letterOf(""), "#"); assert.equal(C.letterOf(undefined), "#");
});

test("the alphabet's jump points are the first row of each letter, whatever the number across", () => {
  const items = ["alien", "amelie", "bambi", "blade", "casino", "7th heaven"].map((t, n) => film(n + 1, t));
  assert.deepEqual(C.letterRows(items.slice(0, 5), 2, 1), [{ l: "A", i: 1 }, { l: "B", i: 2 }, { l: "C", i: 3 }]);
  assert.deepEqual(C.letterRows(items.slice(0, 5), 3, 0), [{ l: "A", i: 0 }, { l: "B", i: 0 }, { l: "C", i: 1 }]);
});

test("films sort by added, year or rating with the title breaking ties, and title keeps the catalog's order", () => {
  const a = film(1, "Alpha", { added: 5, year: 1999, rating: 7 }), b = film(2, "Beta", { added: 9, year: 1999, rating: null }), c = film(3, "Gamma", { added: 9, year: 2010, rating: 9 });
  assert.deepEqual(C.sortFilms([a, b, c], "added").map(f => f.title), ["Beta", "Gamma", "Alpha"]);
  assert.deepEqual(C.sortFilms([a, b, c], "year").map(f => f.title), ["Gamma", "Alpha", "Beta"]);
  assert.deepEqual(C.sortFilms([a, b, c], "rating").map(f => f.title), ["Gamma", "Alpha", "Beta"]);     // no rating last
  assert.deepEqual(C.sortFilms([c, a, b], "title").map(f => f.title), ["Gamma", "Alpha", "Beta"]);
  assert.deepEqual(C.SORTS, ["title", "added", "year", "rating"]);
});

test("the shared bridge pieces are what the player uses to cut a wall into screens and to wait out a dead zone", () => {
  assert.deepEqual(C.paginate([100, 100, 100, 100], 300), [0, 3]);
  assert.equal(C.pageOf([0, 3, 9], 4), 1);
  assert.deepEqual([0, 1, 4, 9].map(C.recoverDelay), [2000, 4000, 30000, 30000]);
  assert.equal(C.stalled(20000, 11000, true), true); assert.equal(C.stalled(20000, 15000, true), false);
});
