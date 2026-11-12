// The house stereo as one source (portal/static/bilby-player.js stereoView). portal/tests/test_player_js.py runs this under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../../static/bilby-player.js");

const lib = { "a/One.flac": { t: "One", a: "Artist A" }, "b/Two.flac": { t: "Two", a: "Artist B" } };
const lookup = p => lib[p] || null;

test("a mixed queue reads as one list: library rows from the catalog, Spotify rows from the titles the stereo sends", () => {
  const v = C.stereoView({ queue: ["a/One.flac", "spotify:track:x", "b/Two.flac"], idx: 1, track: "spotify:track:x", playing: true,
    titles: { "spotify:track:x": ["Around the World", "Daft Punk"] }, mix: { on: true, xfade: 6 } }, lookup);
  assert.deepEqual(v.items.map(i => [i.src, i.title, i.artist, i.cur]),
    [["library", "One", "Artist A", false], ["spotify", "Around the World", "Daft Punk", true], ["library", "Two", "Artist B", false]]);
  assert.ok(v.spotify && v.playing);
  assert.equal(v.now.title, "Around the World");
});

test("how each item is entered: Spotify into Bilby fades, Bilby into Bilby mixes in DJ mode, everything else is a cut", () => {
  const q = ["a/One.flac", "b/Two.flac", "spotify:track:x", "a/One.flac", "spotify:track:y", "spotify:track:z"];
  const on = C.stereoView({ queue: q, mix: { on: true, xfade: 6 } }, lookup).items.map(i => i.trans);
  assert.deepEqual(on, ["", "mix", "cut", "fade", "cut", "cut"]);
  const off = C.stereoView({ queue: q, mix: { on: false, xfade: 0 } }, lookup).items.map(i => i.trans);
  assert.deepEqual(off, ["", "cut", "cut", "cut", "cut", "cut"]);
});

test("a stereo that sends no queue, no titles and no mix is still one readable track", () => {
  const v = C.stereoView({ track: "spotify:track:q", title: "Song", artist: "Me", playing: false }, lookup);
  assert.equal(v.items.length, 1);
  assert.deepEqual([v.items[0].title, v.items[0].artist, v.mix.on, v.mix.xfade], ["Song", "Me", false, 0]);
  assert.equal(C.stereoView(null, lookup).items.length, 0);
  assert.equal(C.stereoView({ queue: ["x/y/Unknown Song.mp3"] }, null).items[0].title, "Unknown Song");
});
