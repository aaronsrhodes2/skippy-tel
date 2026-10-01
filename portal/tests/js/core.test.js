// Bilby's Audio Service: the player's pure logic (portal/static/bilby-player.js), run under Node's built-in test runner.
// portal/tests/test_player_js.py runs this when Node >= 18 is on the machine and skips cleanly when it is not.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const C = require("../../static/bilby-player.js");
// A shared CI machine is slower and noisier than a laptop: the budgets below are the spec's on a laptop and three times that in CI.
const SLOW = process.env.CI ? 3 : 1;

// ── a small catalog shaped like the gate's ───────────────────────────────────────────────────────────────────────
const FIELDS = ["p", "t", "a", "al", "n", "d", "g", "s", "art", "y", "m"];
const rows = [
  ["The Beatles/Help!/01 Help!.mp3", "Help!", "The Beatles", "Help!", 1, 1, "Rock", 140, 1, 1965, 1700000000],
  ["The Beatles/Help!/02 The Night Before.mp3", "The Night Before", "The Beatles", "Help!", 2, 1, "(17)Rock", 150, 1, 1965, 1700000000],
  ["The Beatles/Abbey Road/01 Come Together.mp3", "Come Together", "The Beatles", "Abbey Road", 1, 1, "Rock", 259, 1, 1969, 1700000100],
  ["The Beatles/Abbey Road/02 Something.mp3", "Something", "The Beatles", "Abbey Road", 2, 1, "Rock", 182, 0, 1969, 1700000100],
  ["Björk/Homogenic/01 Hunter.mp3", "Hunter", "Björk", "Homogenic", 1, 1, "Electronic", 255, 1, 1997, 1700001000],
  ["Sigur Rós/( )/01 Untitled.m4a", "Untitled", "Sigur Rós", "( )", 1, 1, "Post-Rock", 400, 1, 2002, 1700002000],
  ["AC-DC/Back in Black/01 Hells Bells.mp3", "Hells Bells", "AC/DC", "Back in Black", 1, 1, "Hard Rock", 312, 1, 1980, 1699000000],
  ["AC-DC/Back in Black/02 Shoot to Thrill.mp3", "Shoot to Thrill", "AC/DC", "Back in Black", 2, 1, "(79)", 317, 1, 1980, 1699000000],
  ["Loose/Single.mp3", "", "", "", 0, 0, "", 0, 0, 0, 0],
];
const catalog = { v: 1, fields: FIELDS, tracks: rows };

test("the fixture's fields are the music gate's own, in the gate's order (positional: a new field only ever goes on the end)", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "..", "services", "music-gate", "library.py"), "utf8");
  const m = /^FIELDS = \[(.*)\]/m.exec(src);
  assert.ok(m, "services/music-gate/library.py no longer has a FIELDS list the player can be checked against");
  const gate = m[1].split(",").map(s => s.trim().replace(/"/g, ""));
  assert.deepEqual(gate.slice(0, FIELDS.length), FIELDS);       // what the player knows is a prefix of what the gate sends
  assert.ok(gate.length >= FIELDS.length);
});
const lib = () => C.parseCatalog(catalog);

// ── text ─────────────────────────────────────────────────────────────────────────────────────────────────────────
test("fold ignores case and accents, including the letters that do not decompose", () => {
  assert.equal(C.fold("Sigur Rós"), "sigur ros");
  assert.equal(C.fold("BJÖRK"), "bjork");
  assert.equal(C.fold("Ærø ß Łódź"), "aero ss lodz");
  assert.equal(C.fold("it’s"), "it's");
  assert.equal(C.fold(null), "");
});

test("sort keys drop a leading article, and the rail letter is A-Z or #", () => {
  assert.equal(C.sortKey("The Beatles"), "beatles");
  assert.equal(C.sortKey("A Tribe Called Quest"), "tribe called quest");
  assert.equal(C.sortKey("The"), "the");                       // nothing left after the article: keep the word
  assert.equal(C.sortKey("(Sandy) Alex G"), "sandy) alex g");  // leading punctuation ignored
  assert.equal(C.letterOf(C.sortKey("The Beatles")), "B");
  assert.equal(C.letterOf(C.sortKey("10cc")), "#");
  assert.equal(C.letterOf(""), "#");
});

test("time and size formatting", () => {
  assert.equal(C.fmtTime(0), "0:00");
  assert.equal(C.fmtTime(65.9), "1:05");
  assert.equal(C.fmtTime(3725), "1:02:05");
  assert.equal(C.fmtTime(NaN), "0:00");
  assert.equal(C.fmtLong(40), "1 min");
  assert.equal(C.fmtLong(42 * 60), "42 min");
  assert.equal(C.fmtLong(3 * 3600 + 12 * 60), "3 hr 12 min");
  assert.equal(C.fmtLong(2 * 3600), "2 hr");
  assert.equal(C.fmtAgo(1000, 1000 + 3600), "today");
  assert.equal(C.fmtAgo(1000, 1000 + 86400 * 1.5), "yesterday");
  assert.equal(C.fmtAgo(1000, 1000 + 86400 * 5), "5 days ago");
  assert.equal(C.fmtAgo(1000, 1000 + 86400 * 21), "3 weeks ago");
  assert.equal(C.fmtAgo(0, 1e9), "");
  assert.equal(C.fmtBytes(2048), "2 KB");
  assert.equal(C.fmtBytes(5 * 1048576), "5.0 MB");
});

test("genre tags: ID3v1 numbers become names, nothing readable is lost", () => {
  assert.equal(C.genreLabel("(17)Rock"), "Rock");
  assert.equal(C.genreLabel("(17)"), "Rock");
  assert.equal(C.genreLabel("17"), "Rock");
  assert.equal(C.genreLabel("Hard Rock"), "Hard Rock");
  assert.equal(C.genreLabel("(250)"), "");
  assert.equal(C.genreLabel(undefined), "");
});

// ── the catalog ──────────────────────────────────────────────────────────────────────────────────────────────────
test("parseCatalog groups artists, albums and genres and sorts album tracks by disc and number", () => {
  const l = lib();
  assert.equal(l.T.length, 9);
  assert.deepEqual(l.artists.map(a => a.name), ["AC/DC", "The Beatles", "Björk", "Loose artist".replace("Loose artist", "Unknown artist"), "Sigur Rós"].sort((a, b) => C.cmp(C.sortKey(a), C.sortKey(b))));
  const beatles = l.artistByName.get("The Beatles");
  assert.equal(beatles.tracks.length, 4);
  assert.deepEqual(beatles.albums.map(a => a.name), ["Help!", "Abbey Road"]);   // oldest first
  const help = l.albumByKey.get("The Beatles\u0000Help!");
  assert.deepEqual(help.tracks.map(i => l.T[i].t), ["Help!", "The Night Before"]);
  assert.equal(help.year, 1965);
  assert.equal(help.dur, 290);
  const abbey = l.albumByKey.get("The Beatles\u0000Abbey Road");
  assert.equal(abbey.artPath, "The Beatles/Abbey Road/01 Come Together.mp3");     // the first track that has art
  assert.equal(l.T[abbey.tracks[1]].ap, abbey.artPath);                            // a track without art borrows its album's
  assert.equal(l.T[8].a, "Unknown artist");
  assert.equal(l.T[8].t, "Single.mp3");                                           // no title tag: the file name
  assert.equal(l.albumByKey.get("Unknown artist\u0000").name, "Singles");
  const rock = l.genres.find(g => g.name === "Rock");
  assert.equal(rock.tracks.length, 4);                                            // "(17)Rock" folded into "Rock"
  assert.ok(l.genres.find(g => g.name === "Hard Rock").tracks.length === 2);      // "(79)" is Hard Rock in ID3v1
  assert.equal(l.hasYear, true);
  assert.equal(l.hasAdded, true);
  assert.equal(l.byPath.get("Björk/Homogenic/01 Hunter.mp3"), 4);
});

test("an album folder's trailing (year) is not part of the album's name, but two folders stay two albums", () => {
  const r = (p, al, y) => [p, "Song " + p, "The Beatles", al, 1, 1, "Rock", 100, 0, y, 1700000000];
  const l = C.parseCatalog({ v: 1, fields: FIELDS, tracks: [
    r("a/1.mp3", "Help! (1965)", 0), r("a/2.mp3", "Help! (1987)", 1987), r("a/3.mp3", "(1999)", 0), r("a/4.mp3", "Live at (Home) (2001)", 2001), r("a/5.mp3", "Abbey Road", 1969),
  ] });
  assert.deepEqual(l.albums.map(a => a.name).sort(), ["(1999)", "Abbey Road", "Help!", "Help!", "Live at (Home)"]);   // a name that is only a year is kept
  const years = Object.fromEntries(l.albums.map(a => [a.key.split("\u0000")[1], a.year]));
  assert.equal(years["Help! (1965)"], 1965);                  // the year the folder names stands in for a tag that has none
  assert.equal(years["Help! (1987)"], 1987);
  assert.equal(l.T[0].al, "Help!");                             // what the rows and Now Playing show
  assert.equal(l.T[0].alk, "Help! (1965)");                    // what groups it
  assert.equal(l.albumByKey.get("The Beatles\u0000Help! (1987)").tracks.length, 1);
  assert.equal(C.search(l, "help 1965").albums.length, 0);   // search sees the displayed name
  assert.equal(C.search(l, "help").albums.length, 2);
});

test("a catalog from a gate that has no year or date-added still loads, and says so", () => {
  const old = { v: 1, fields: FIELDS.slice(0, 9), tracks: rows.map(r => r.slice(0, 9)) };
  const l = C.parseCatalog(old);
  assert.equal(l.hasYear, false);
  assert.equal(l.hasAdded, false);
  assert.equal(l.T[0].y, 0);
  assert.deepEqual(C.newestAlbums(l), []);
  assert.equal(C.parseCatalog({}).T.length, 0);
  assert.equal(C.parseCatalog(null).T.length, 0);
});

test("newestAlbums puts the most recently added first", () => {
  const l = lib();
  assert.deepEqual(C.newestAlbums(l).slice(0, 3).map(a => a.name), ["( )", "Homogenic", "Abbey Road"]);
});

test("lyric summaries attach by path and unknown paths are ignored", () => {
  const l = lib();
  const n = C.attachLyrics(l, { "Björk/Homogenic/01 Hunter.mp3": "A hunter of her own heart.", "nope/x.mp3": "x", "AC-DC/Back in Black/01 Hells Bells.mp3": "" });
  assert.equal(n, 1);
  assert.equal(l.T[4].ly, "A hunter of her own heart.");
  assert.equal(C.attachLyrics(l, null), 0);
});

test("sortTracks: by title (articles ignored), artist, newest, year, length", () => {
  const l = lib();
  const names = mode => C.sortTracks(l, mode).map(i => l.T[i].t);
  assert.deepEqual(names("title").slice(0, 3), ["Come Together", "Hells Bells", "Help!"]);
  assert.equal(names("artist")[0], "Hells Bells");                                  // AC/DC first
  assert.equal(C.sortTracks(l, "newest").length, 9);
  assert.equal(names("newest")[0], "Untitled");
  assert.equal(names("year")[0], "Untitled");
  assert.equal(names("length")[0], "Untitled");
  assert.equal(names("album")[0], "Hells Bells");
  assert.equal(names("nonsense")[0], "Come Together");                              // unknown mode: by title
});

// ── search ───────────────────────────────────────────────────────────────────────────────────────────────────────
test("search is accent- and case-insensitive and needs every word", () => {
  const l = lib();
  const titles = q => C.search(l, q).tracks.map(i => l.T[i].t);
  assert.deepEqual(titles("bjork"), ["Hunter"]);
  assert.deepEqual(titles("SIGUR ROS"), ["Untitled"]);
  assert.deepEqual(titles("beatles help"), ["Help!", "The Night Before"]);   // title word first, then the rest of the album
  assert.deepEqual(titles("beatles zzz"), []);
  assert.deepEqual(titles("   "), []);
  assert.equal(C.search(l, "rock").total, 7);                                  // genre counts too: Rock, Hard Rock, Post-Rock
});

test("search ranks a title that starts with the word first, and finds AC/DC however it is typed", () => {
  const l = lib();
  assert.equal(l.T[C.search(l, "some").tracks[0]].t, "Something");
  for (const q of ["acdc", "ac dc", "ac/dc", "AC-DC"]) {
    const r = C.search(l, q);
    assert.equal(r.artists.length, 1, q);
    assert.equal(r.artists[0].name, "AC/DC", q);
  }
  assert.equal(C.search(l, "back black").albums[0].name, "Back in Black");
  assert.equal(C.search(l, "homogenic").albums.length, 1);
});

test("search honours the limit but reports the true total", () => {
  const l = lib();
  const r = C.search(l, "e", 3);
  assert.equal(r.tracks.length, 3);
  assert.ok(r.total > 3);
});

test("search over 21,000 tracks stays well under the 50 ms budget", () => {
  const big = [];
  const words = ["love", "night", "road", "fire", "river", "dream", "blue", "stone", "light", "dark", "heart", "wild", "gold", "rain"];
  let seed = 7;
  const rnd = n => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
  for (let i = 0; i < 21000; i++) {
    const a = `${words[rnd(14)]} ${words[rnd(14)]}s ${rnd(900)}`, al = `${words[rnd(14)]} ${words[rnd(14)]} ${rnd(40)}`;
    big.push([`${a}/${al}/${i}.mp3`, `${words[rnd(14)]} ${words[rnd(14)]} ${i}`, a, al, i % 12, 1, "Rock", 200, i % 3 === 0 ? 1 : 0, 1990 + rnd(30), 1600000000 + i]);
  }
  const t0 = Date.now();
  const bigLib = C.parseCatalog({ v: 1, fields: FIELDS, tracks: big });
  const parseMs = Date.now() - t0;
  assert.equal(bigLib.T.length, 21000);
  C.search(bigLib, "love");                                                    // warm
  const t1 = process.hrtime.bigint();
  const r1 = C.search(bigLib, "love night");
  const r2 = C.search(bigLib, "dark");
  const ms = Number(process.hrtime.bigint() - t1) / 1e6 / 2;
  assert.ok(r1.total > 0 && r2.total > 0);
  assert.ok(ms < 50 * SLOW, `search took ${ms.toFixed(1)} ms`);
  assert.ok(parseMs < 1500 * SLOW, `parse took ${parseMs} ms`);
  const t2 = Date.now();
  C.sortTracks(bigLib, "title");
  assert.ok(Date.now() - t2 < 600 * SLOW, "sorting 21k tracks");
});

// ── Bilby DJ ─────────────────────────────────────────────────────────────────────────────────────────────────────
// A deterministic "random": a list of values it hands out in turn.
const seq = (...vals) => { let k = 0; return () => vals[k++ % vals.length]; };

test("DJ never picks the current track; one in three picks is allowed to leave the artist, two in three are not", () => {
  const l = lib();
  const cur = l.byPath.get("The Beatles/Help!/01 Help!.mp3");
  for (let n = 0; n < 500; n++) assert.notEqual(C.djPick(l, cur, new Set()), cur);
  // roll 0.5 < 0.7: the same artist, whichever track the second draw lands on
  for (const r2 of [0, 0.3, 0.6, 0.99]) {
    const pick = C.djPick(l, cur, new Set(), seq(0.5, r2));
    assert.equal(l.T[pick].a, "The Beatles");
    assert.notEqual(pick, cur);
  }
  // roll 0.8 >= 0.7: any track, drawn by the second value (0.5 of 9 tracks = index 4: Björk)
  assert.equal(C.djPick(l, cur, new Set(), seq(0.8, 0.5)), 4);
});

test("DJ's 70/30 split really is 70/30", () => {
  const l = lib();
  const cur = l.byPath.get("The Beatles/Help!/01 Help!.mp3");
  let same = 0;
  const N = 20000;
  for (let n = 0; n < N; n++) if (l.T[C.djPick(l, cur, new Set())].a === "The Beatles") same++;
  // 70 % same artist, plus the share of the 30 % that lands there by chance (3 of the 8 other tracks)
  const expected = 0.7 + 0.3 * (3 / 8);
  assert.ok(Math.abs(same / N - expected) < 0.02, `${same / N} vs ${expected}`);
});

test("DJ skips what was played in the last 50 while something else is left, and repeats rather than stalls", () => {
  const l = lib();
  const cur = l.byPath.get("The Beatles/Help!/01 Help!.mp3");
  const night = l.byPath.get("The Beatles/Help!/02 The Night Before.mp3");
  const abbey = l.byPath.get("The Beatles/Abbey Road/01 Come Together.mp3");
  const something = l.byPath.get("The Beatles/Abbey Road/02 Something.mp3");
  // same-artist roll, two of the three other Beatles tracks recent: only one candidate is left
  for (const r2 of [0, 0.5, 0.99]) assert.equal(C.djPick(l, cur, new Set([night, abbey]), seq(0.1, r2)), something);
  // all of the artist's other tracks recent: the pool does not allow, so one of them is repeated
  const pick = C.djPick(l, cur, new Set([night, abbey, something]), seq(0.1, 0.5));
  assert.ok([night, abbey, something].includes(pick));
  // the any-track pool with all but one track recent: that one
  const all = new Set(l.T.map(t => t.i).filter(i => i !== 3 && i !== cur));
  assert.equal(C.djPick(l, cur, all, seq(0.9, 0.5)), 3);
  // everything recent: anything but the current one
  const everything = new Set(l.T.map(t => t.i));
  for (let n = 0; n < 200; n++) assert.notEqual(C.djPick(l, cur, everything, seq(0.9, Math.random())), cur);
});

test("DJ on a library of one plays that one, and of none says so", () => {
  const one = C.parseCatalog({ v: 1, fields: FIELDS, tracks: [rows[0]] });
  assert.equal(C.djPick(one, 0, new Set([0])), 0);
  assert.equal(C.djPick(one, -1, new Set()), 0);
  assert.equal(C.djPick(C.parseCatalog({ fields: FIELDS, tracks: [] }), -1, new Set()), -1);
});

test("DJ with nothing playing yet just picks something", () => {
  const l = lib();
  const p = C.djPick(l, -1, new Set(), seq(0.5));
  assert.ok(p >= 0 && p < l.T.length);
});

test("recentSet is the last 50 played paths plus the current track", () => {
  const l = lib();
  const hist = [];
  for (let i = 0; i < 120; i++) hist.push(l.T[i % 4].p);
  hist.push("gone/from/library.mp3");
  const s = C.recentSet(l, hist, 7, 50);
  assert.deepEqual([...s].sort((a, b) => a - b), [0, 1, 2, 3, 7]);
  assert.deepEqual([...C.recentSet(l, ["Björk/Homogenic/01 Hunter.mp3"], -1)], [4]);
  assert.equal(C.DJ_AVOID_LAST, 50);
  assert.equal(C.DJ_SAME_ARTIST, 0.7);
});

// ── the queue ────────────────────────────────────────────────────────────────────────────────────────────────────
const ids = q => q.items.map(x => x.t);
const mkq = (list, start = 0, shuffle = false, rand) => { const q = new C.Queue(rand); q.load(list, start, shuffle); return q; };

test("load stands on the chosen track; shuffle keeps it first and the rest are all still there", () => {
  const q = mkq([10, 11, 12, 13, 14], 2);
  assert.deepEqual(ids(q), [10, 11, 12, 13, 14]);
  assert.equal(q.cur.t, 12);
  const s = mkq([10, 11, 12, 13, 14], 2, true);
  assert.equal(s.qi, 0);
  assert.equal(s.cur.t, 12);
  assert.deepEqual(ids(s).sort(), [10, 11, 12, 13, 14]);
  assert.equal(mkq([], 0).cur, null);
  assert.equal(mkq([5], 9).cur.t, 5);                                // a start past the end is clamped
});

test("next goes forward, wraps only when repeating all, ends otherwise", () => {
  const q = mkq([1, 2, 3], 1);
  assert.equal(q.nextPos("off"), 2);
  q.jump(2);
  assert.equal(q.nextPos("off"), -1);
  assert.equal(q.nextPos("all"), 0);
  assert.equal(q.prevPos(), 1);
  q.jump(0);
  assert.equal(q.prevPos(), -1);
  assert.equal(new C.Queue().nextPos("all"), -1);                    // nothing to repeat
});

test("play next lines items up in the order they were added; add goes to the end", () => {
  const q = mkq([1, 2, 3], 0);
  q.playNext(90); q.playNext(91);
  assert.deepEqual(ids(q), [1, 90, 91, 2, 3]);
  q.add(99);
  assert.deepEqual(ids(q), [1, 90, 91, 2, 3, 99]);
  q.jump(1);                                                          // 90 starts playing: a new "play next" goes right after it, before 91
  q.playNext(92);
  assert.deepEqual(ids(q), [1, 90, 91, 92, 2, 3, 99].filter(x => x !== 92).slice(0, 3).concat([92], [2, 3, 99]));
});

test("play next onto an empty queue starts one", () => {
  const q = new C.Queue();
  q.playNext(5);
  assert.deepEqual(ids(q), [5]);
  assert.equal(q.cur.t, 5);
});

test("a track Bilby DJ picked ahead stays last: adds go before it, and it is flagged", () => {
  const q = mkq([1, 2], 1);
  const at = q.addAuto(77);
  assert.equal(at, 2);
  assert.ok(q.autoLast() && q.isAuto(2) && !q.isAuto(1));
  q.add(50);
  assert.deepEqual(ids(q), [1, 2, 50, 77]);
  assert.ok(q.autoLast());
  q.remove(3);
  assert.ok(!q.autoLast());
  q.add(51);
  assert.deepEqual(ids(q), [1, 2, 50, 51]);
});

test("once Bilby DJ's pick is the song playing, adds go after it, not before", () => {
  const q = mkq([1, 2], 1);
  q.addAuto(77);
  q.jump(2);                                                          // the pick starts playing
  assert.ok(!q.autoLast());
  q.add(50);
  assert.deepEqual(ids(q), [1, 2, 77, 50]);
  assert.equal(q.cur.t, 77);
});

test("remove keeps the current track current, and the current track cannot be removed", () => {
  const q = mkq([1, 2, 3, 4], 2);
  assert.equal(q.remove(2), false);
  assert.equal(q.remove(0), true);
  assert.equal(q.qi, 1);
  assert.equal(q.cur.t, 3);
  assert.equal(q.remove(2), true);
  assert.deepEqual(ids(q), [2, 3]);
  assert.equal(q.remove(9), false);
});

test("move reorders upcoming items only", () => {
  const q = mkq([1, 2, 3, 4, 5], 1);
  assert.equal(q.move(4, 2), true);
  assert.deepEqual(ids(q), [1, 2, 5, 3, 4]);
  assert.equal(q.cur.t, 2);
  assert.equal(q.move(1, 3), false);                                  // the playing track does not move
  assert.equal(q.move(3, 1), false);                                  // and nothing moves above it
  assert.equal(q.move(0, 3), false);
  assert.equal(q.move(2, 2), false);
  assert.equal(q.move(2, 9), false);
  assert.equal(q.move(2, 4), true);
  assert.deepEqual(ids(q), [1, 2, 3, 4, 5]);
});

test("clearUpcoming keeps what is playing and what has played", () => {
  const q = mkq([1, 2, 3, 4], 1);
  q.addAuto(9);
  q.clearUpcoming();
  assert.deepEqual(ids(q), [1, 2]);
  assert.equal(q.cur.t, 2);
  assert.ok(!q.autoLast());
});

test("shuffling the rest and turning it off restores the order, with additions at the end", () => {
  const q = mkq([1, 2, 3, 4, 5, 6, 7, 8], 0, false, seq(0.9, 0.1, 0.5, 0.3, 0.7, 0.2, 0.8));
  q.shuffleUpcoming();
  assert.equal(q.cur.t, 1);
  assert.deepEqual(ids(q).slice().sort(), [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.notDeepEqual(ids(q), [1, 2, 3, 4, 5, 6, 7, 8]);
  q.add(100);
  q.remove(3);
  const left = new Set(ids(q).slice(1));
  q.unshuffle();
  const rest = ids(q).slice(1);
  assert.deepEqual(new Set(rest), left);
  assert.deepEqual(rest.filter(x => x !== 100), rest.filter(x => x !== 100).slice().sort((a, b) => a - b));
  assert.equal(rest[rest.length - 1], 100);
});

test("loading with shuffle on still lets shuffle-off restore the list order", () => {
  const q = mkq([10, 11, 12, 13, 14, 15], 2, true, seq(0.1, 0.9, 0.4, 0.7));
  assert.equal(q.cur.t, 12);
  q.unshuffle();
  assert.deepEqual(ids(q), [12, 13, 14, 15, 10, 11]);                 // the tracks after the one picked, then round to the start
});

test("remap re-numbers tracks for a new catalog and drops what is gone", () => {
  const q = mkq([1, 2, 3, 4], 2);                                     // playing track 3
  q.remap(t => (t === 2 ? -1 : t + 10));
  assert.deepEqual(ids(q), [11, 13, 14]);
  assert.equal(q.cur.t, 13);                                          // still the same song, one place earlier
  const gone = mkq([1, 2, 3, 4], 2);
  gone.remap(t => (t === 3 ? -1 : t + 10));
  assert.deepEqual(ids(gone), [11, 12, 14]);
  assert.equal(gone.qi, 1);                                           // the playing song vanished: the one before stands in, next is 14
  assert.equal(gone.nextPos("off"), 2);
  const q2 = mkq([1, 2, 3], 0);
  q2.remap(t => (t === 1 ? -1 : t));
  assert.equal(q2.qi, -1);                                            // the playing track is gone and nothing is before it: next is the first
  assert.equal(q2.nextPos("off"), 0);
  const q3 = mkq([1], 0);
  q3.remap(() => -1);
  assert.equal(q3.length, 0);
  assert.equal(q3.qi, -1);
});

test("a window around the current track is what gets saved, never all 21,000", () => {
  const q = mkq(Array.from({ length: 21000 }, (_, i) => i), 10000);
  const w = q.window(50, 300);
  assert.equal(w.items.length, 351);
  assert.equal(w.items[w.qi].t, 10000);
  const near = mkq([1, 2, 3], 0).window(50, 300);
  assert.equal(near.items.length, 3);
  assert.equal(near.qi, 0);
  const T = lib().T;
  const tp = C.trimPaths(mkq([0, 1, 2, 3, 4], 2).items, 2, T, 1, 1);
  assert.deepEqual(tp.paths, [T[1].p, T[2].p, T[3].p]);
  assert.equal(tp.idx, 1);
});

test("total time left in the queue", () => {
  const T = lib().T;
  const q = mkq([0, 1, 2], 0);
  assert.equal(q.totalSeconds(T), T[1].s + T[2].s);
  assert.equal(q.totalSeconds(T, 0), T[0].s + T[1].s + T[2].s);
});

// ── the browser's autoplay rule ──────────────────────────────────────────────────────────────────────────────────
test("a fresh page is not armed; a play() that worked arms it; a refusal asks for a tap and disarms it", () => {
  const a = new C.Armed();
  assert.deepEqual(a.snapshot(), { armed: false, tap: false });
  a.refused();
  assert.deepEqual(a.snapshot(), { armed: false, tap: true });          // "Tap to start the sound"
  a.played(true);
  assert.deepEqual(a.snapshot(), { armed: true, tap: false });          // tapped: the bar goes, track changes need no more taps
  a.refused();                                                          // e.g. iOS took the audio session back
  assert.deepEqual(a.snapshot(), { armed: false, tap: true });
});

test("a muted play() proves nothing about sound", () => {
  const a = new C.Armed();
  a.refused();
  a.played(false);
  assert.deepEqual(a.snapshot(), { armed: false, tap: true });
});

test("a saved queue that was playing waits for a tap without having been refused", () => {
  const a = new C.Armed();
  a.waiting();
  assert.deepEqual(a.snapshot(), { armed: false, tap: true });
  a.played(true);
  assert.equal(a.snapshot().tap, false);
});

test("where the browser reports that the page was tapped, that counts as armed until a refusal says otherwise", () => {
  let active = false;
  const a = new C.Armed(() => active);
  assert.equal(a.armed, false);
  active = true;
  assert.equal(a.armed, true);
  a.refused();
  assert.equal(a.armed, false);                                          // the browser's flag does not outvote a refusal
  a.played(true);
  assert.equal(a.armed, true);
});

// ── sleep timer ──────────────────────────────────────────────────────────────────────────────────────────────────
test("sleep timer: minutes, end of track, fade-out and labels", () => {
  const s = new C.SleepTimer();
  assert.equal(s.active, false);
  assert.equal(s.label(0), "");
  s.start(30, 1000);
  assert.ok(s.active);
  assert.equal(s.due(1000 + 29 * 60000), false);
  assert.equal(s.due(1000 + 30 * 60000), true);
  assert.equal(s.label(1000), "30 min");
  assert.equal(s.label(1000 + 29 * 60000 + 1), "1 min");
  assert.equal(s.gain(1000, 8000), 1);
  assert.equal(s.gain(1000 + 30 * 60000 - 4000, 8000), 0.5);
  assert.equal(s.gain(1000 + 30 * 60000 + 5, 8000), 0);
  s.start("end", 0);
  assert.ok(s.active && s.endOfTrack);
  assert.equal(s.due(1e12), false);                                      // end-of-track fires on `ended`, not on the clock
  assert.equal(s.label(0), "End of track");
  s.stop();
  assert.equal(s.active, false);
});

// ── virtual list geometry ────────────────────────────────────────────────────────────────────────────────────────
test("row offsets and the visible window", () => {
  const heights = [100, 20, 20, 20, 60, 20];
  const off = C.buildOffsets(heights);
  assert.deepEqual([...off], [0, 100, 120, 140, 160, 220, 240]);
  assert.equal(C.rowAt(off, 0), 0);
  assert.equal(C.rowAt(off, 99), 0);
  assert.equal(C.rowAt(off, 100), 1);
  assert.equal(C.rowAt(off, 159), 3);
  assert.equal(C.rowAt(off, 9999), 5);
  assert.equal(C.rowAt(C.buildOffsets([]), 5), 0);
  assert.deepEqual(C.visibleRange(off, 110, 30, 0), [1, 3]);
  assert.deepEqual(C.visibleRange(off, 0, 1000, 50), [0, 6]);
  assert.deepEqual(C.visibleRange(C.buildOffsets([]), 0, 100, 10), [0, 0]);
});

test("a 21,000-row list needs only a handful of rows in the DOM at once", () => {
  const n = 21000, off = C.buildOffsets(new Array(n).fill(56));
  const [a, b] = C.visibleRange(off, 56 * 10000 + 13, 700, 280);
  assert.ok(b - a <= 25, `${b - a} rows`);
  assert.ok(a <= 10000 && b > 10000);
  const t0 = process.hrtime.bigint();
  for (let k = 0; k < 1000; k++) C.visibleRange(off, k * 977 % (56 * n), 700, 280);
  assert.ok(Number(process.hrtime.bigint() - t0) / 1e6 < 50);
});

test("chunk splits rows of album cards", () => {
  assert.deepEqual(C.chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assert.deepEqual(C.chunk([], 3), []);
});

test("thumbnail widths: the smallest the gate makes that covers the box at this pixel density", () => {
  assert.equal(C.artWidth(44, 2), 96);
  assert.equal(C.artWidth(48, 2), 96);
  assert.equal(C.artWidth(49, 2), 192);
  assert.equal(C.artWidth(160, 1), 192);
  assert.equal(C.artWidth(160, 3), 768 >= 480 ? 768 : 384);
  assert.equal(C.artWidth(340, 2), 768);
  assert.equal(C.artWidth(900, 3), 768);                                 // never asks for more than the gate makes
  assert.equal(C.artWidth(undefined, undefined), 96);
});

// ── where we are running ─────────────────────────────────────────────────────────────────────────────────────────
test("WebKit browsers get one audio element; Chrome and Firefox get two", () => {
  const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1";
  const iphoneChrome = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0 Mobile/15E148 Safari/604.1";
  const pwa = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148";
  const ipadAsMac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15";
  const macSafari = ipadAsMac;
  const macChrome = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
  const winEdge = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0";
  const tesla = "Mozilla/5.0 (X11; GNU/Linux) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 Tesla/2024.8";
  const android = "Mozilla/5.0 (Linux; Android 14; SM-S911U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36";
  const firefox = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0";
  const env = (ua, plat = "", touch = 0) => C.detectEnv(ua, plat, touch);
  assert.deepEqual([env(iphone).singleElement, env(iphone).name], [true, "iPhone"]);
  assert.equal(env(iphoneChrome).singleElement, true);                   // every iPhone browser is WebKit
  assert.equal(env(pwa).singleElement, true);                            // the home-screen app has no "Safari" in its UA
  assert.deepEqual([env(ipadAsMac, "MacIntel", 5).ios, env(ipadAsMac, "MacIntel", 5).name], [true, "iPad"]);   // iPadOS says Macintosh
  assert.deepEqual([env(macSafari, "MacIntel", 0).singleElement, env(macSafari, "MacIntel", 0).name], [true, "Mac"]);
  assert.equal(env(macChrome, "MacIntel", 0).singleElement, false);
  assert.equal(env(winEdge).singleElement, false);
  assert.equal(env(winEdge).name, "PC");
  assert.deepEqual([env(tesla).tesla, env(tesla).name, env(tesla).singleElement], [true, "Tesla", false]);
  assert.deepEqual([env(android).name, env(android).singleElement], ["Android", false]);
  assert.equal(env(firefox).singleElement, false);
  assert.equal(env("").name, "Browser");
});

test("a device's label tells two PCs apart by browser and leaves phones and the car alone", () => {
  const winEdge = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0";
  const winChrome = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
  const macSafari = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15";
  const firefox = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0";
  const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1";
  const tesla = "Mozilla/5.0 (X11; GNU/Linux) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 Tesla/2024.8";
  const label = (ua, plat = "", touch = 0) => C.detectEnv(ua, plat, touch).label;
  assert.equal(label(winEdge), "PC · Edge");
  assert.equal(label(winChrome), "PC · Chrome");
  assert.equal(label(firefox), "PC · Firefox");
  assert.equal(label(macSafari, "MacIntel", 0), "Mac · Safari");
  assert.equal(label(iphone), "iPhone");
  assert.equal(label(tesla), "Tesla");
  assert.equal(label(""), "Browser");
});
