// The house stereo's speakers (a hexagon toggle each), the wait for one, and audiobooks in its stream: the pure half of Bilby's stereo pane
// (portal/static/bilby-player.js). portal/tests/test_player_js.py runs this under Node; the drawing itself (bilby-app.js) is seen in a browser.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../../static/bilby-player.js");

const outs = [
  { id: "enclave", name: "Enclave", on: true, open: true, floor: "upstairs" },
  { id: "hdmi", name: "Sony", on: false, open: true, floor: "downstairs" },
  { id: "pod:living", name: "Living room box", on: true, open: false, floor: "" },
];
const toggles = (list, pending, now) => C.outToggles(C.outsView({ outs: list }, pending || null, now || 0));
const byId = row => Object.fromEntries(row.filter(t => !t.gap).map(t => [t.id, t]));

// ── the row of toggles ────────────────────────────────────────────────────────────────────────────────────────────

test("one toggle per speaker: lit when its switch is on, dark when it is off", () => {
  const row = toggles(outs).filter(t => !t.gap);
  assert.deepEqual(row.map(t => [t.id, t.pressed]), [["enclave", true], ["hdmi", false], ["pod:living", true]]);
  assert.deepEqual(row.map(t => t.glyph), ["vol", "pip", "can"]);                       // a speaker, a screen, a pod
  assert.ok(row.every(t => t.tel === C.TOGGLE_TEL && t.tel === "music:speaker-toggle"));  // one telemetry name, which the nightly tidy never retires
});

test("a speaker that is on but not there stays lit and waits (its glyph breathes): the toggle has two looks and no lamp", () => {
  const t = byId(toggles(outs));
  assert.deepEqual([t["pod:living"].pressed, t["pod:living"].wait], [true, true]);
  assert.deepEqual([t.enclave.pressed, t.enclave.wait], [true, false]);                  // on and there: just lit
  assert.deepEqual([t.hdmi.pressed, t.hdmi.wait], [false, false]);                       // off (there or not): just dark
  const gone = byId(toggles([{ id: "hdmi", name: "Sony", on: false, open: false, floor: "downstairs" }]));
  assert.deepEqual([gone.hdmi.pressed, gone.hdmi.wait], [false, false]);
  assert.equal(t["pod:living"].label, "Living room box: on, but not there right now");   // said to a screen reader and on hover
  assert.equal(t.enclave.label, "Enclave (upstairs)");
  assert.equal(t.enclave.title, "Enclave (upstairs): on");
  assert.equal(t.hdmi.title, "Sony (downstairs): off");
  assert.ok(!Object.keys(t.enclave).some(k => /lamp/i.test(k)));
});

test("a tap flips the switch with the command the portal accepts, and only a well-formed id gets one", () => {
  const t = byId(toggles(outs));
  assert.deepEqual([t.enclave.tap, t.hdmi.tap, t["pod:living"].tap], ["out:enclave:off", "out:hdmi:on", "out:pod:living:off"]);
  assert.equal(C.outCommand("pod:living-room", true), "out:pod:living-room:on");
  assert.equal(C.outCommand("enclave", false), "out:enclave:off");
  for (const bad of ["", "Enclave", "a:b:c", "x".repeat(25), "pod:", ":x", "a b", "enclave\n", "../x", null, undefined, 5]) assert.equal(C.outCommand(bad, true), "", String(bad));
});

test("a floor's speakers sit together, upstairs first, with a gap between floors; the stereo's own order holds within a floor", () => {
  const mixed = [{ id: "pod:den", name: "Den", on: true, open: true, floor: "" }, { id: "hdmi", name: "Sony", on: true, open: true, floor: "downstairs" },
                 { id: "pod:bed", name: "Bedroom", on: true, open: true, floor: "upstairs" }, { id: "enclave", name: "Enclave", on: true, open: true, floor: "upstairs" },
                 { id: "pod:attic", name: "Attic", on: true, open: true, floor: "attic" }];                      // (not a floor the stereo knows: unplaced)
  assert.deepEqual(toggles(mixed).map(t => (t.gap ? "|" : t.id)), ["pod:bed", "enclave", "|", "hdmi", "|", "pod:den", "pod:attic"]);
  assert.deepEqual(toggles([outs[0]]).map(t => (t.gap ? "|" : t.id)), ["enclave"]);
  assert.deepEqual(toggles([]), []);
});

test("a speaker list the stereo garbled is cleaned, not trusted: bad ids, repeats and more than twelve are dropped", () => {
  const messy = [{ id: "Bad Id" }, { id: "a:b:c" }, { id: 5 }, null, "x", { id: "pod:" }, { name: "no id" }, { id: "enclave", name: "", on: "yes", open: 1, floor: "attic" },
                 { id: "enclave", name: "again", on: true }];
  assert.deepEqual(C.outsView({ outs: messy }, null, 0), [{ id: "enclave", name: "enclave", floor: "", on: false, open: false, away: false, asking: false }]);
  const many = Array.from({ length: 30 }, (_, i) => ({ id: `pod:r${i}`, name: `R${i}`, on: true, open: true }));
  assert.equal(C.outsView({ outs: many }, null, 0).length, 12);
  for (const none of [null, {}, { outs: "enclave" }, { outs: { id: "enclave" } }, { outs: [] }]) assert.deepEqual(C.outsView(none, null, 0), []);       // an older radio: no row at all
});

test("a tap shows at once and is held until the stereo's own check-in agrees, or the wait runs out", () => {
  const state = { outs };
  const pend = { hdmi: { on: true, until: 10000 } };
  const hdmi = (st, p, now) => C.outsView(st, p, now).find(o => o.id === "hdmi");
  assert.deepEqual([hdmi(state, pend, 5000).on, hdmi(state, pend, 5000).asking], [true, true]);               // flipped on the screen before the radio has acted
  assert.deepEqual([hdmi(state, pend, 10000).on, hdmi(state, pend, 10000).asking], [false, false]);           // it never agreed: the stereo's own word again
  assert.deepEqual(Object.keys(C.settleOuts(pend, state, 5000)), ["hdmi"]);                                    // the stereo still says off: keep waiting
  const agreed = { outs: outs.map(o => (o.id === "hdmi" ? { ...o, on: true } : o)) };
  assert.deepEqual(Object.keys(C.settleOuts(pend, agreed, 5000)), []);                                         // it agrees: no longer a guess
  assert.deepEqual(Object.keys(C.settleOuts(pend, state, 10001)), []);                                         // ran out
  assert.deepEqual(Object.keys(C.settleOuts(pend, null, 5000)), []);                                           // the stereo went away
  assert.deepEqual([hdmi(agreed, pend, 5000).on, hdmi(agreed, pend, 5000).asking], [true, false]);
  assert.equal(C.OUT_HOLD_MS, 12000);                                                                            // two slow check-ins of the hub (4 s each, when it is not playing) and some
});

test("the toggles are cut into lines that fit the screen, so a short screen breaks between lines and hides no speaker", () => {
  const row = list => C.outToggles(C.outsView({ outs: list }, null, 0));
  const ids = line => line.map(x => (x.gap ? "|" : x.id));
  assert.deepEqual([C.SO_W, C.GAP_W], [76, 17]);                                                                 // (what bilby-player.css draws; test_player.py holds the two together)
  assert.deepEqual(C.outsLines(row(outs), 375).map(ids), [["enclave", "|", "hdmi", "|", "pod:living"]]);          // a phone holds the three, floors apart, on one line
  assert.deepEqual(C.outsLines(row(outs), 220).map(ids), [["enclave", "|", "hdmi"], ["pod:living"]]);            // narrower: a gap that would end a line is dropped
  assert.deepEqual(C.outsLines(row(outs), 10).map(ids), [["enclave"], ["hdmi"], ["pod:living"]]);                // never narrower than one toggle
  const twelve = Array.from({ length: 12 }, (_, i) => ({ id: `pod:r${i}`, name: `R${i}`, on: true, open: true, floor: "upstairs" }));
  assert.deepEqual(C.outsLines(row(twelve), 375).map(l => l.length), [4, 4, 4]);
  assert.deepEqual(C.outsLines(row(twelve), 1200).map(l => l.length), [12]);
  assert.deepEqual(C.outsLines(row(twelve), NaN).map(l => l.length), [4, 4, 4]);                                  // an unmeasured width is a small phone (320)
  assert.deepEqual(C.outsLines([], 375), []);
  assert.deepEqual(C.outsLines(null, 375), []);
  const mixed = [{ id: "a", on: true, open: true, floor: "upstairs" }, { id: "b", on: true, open: true, floor: "upstairs" }, { id: "c", on: true, open: true, floor: "downstairs" },
                 { id: "d", on: true, open: true, floor: "downstairs" }, { id: "e", on: true, open: true, floor: "" }];
  for (let width = 50; width <= 1300; width += 25) {                                                              // at every width: each speaker once, in order, no line starts or ends on a gap
    const lines = C.outsLines(row(mixed), width), flat = lines.flat();
    assert.deepEqual(flat.filter(x => !x.gap).map(x => x.id), ["a", "b", "c", "d", "e"], String(width));
    assert.ok(lines.every(l => l.length && !l[0].gap && !l[l.length - 1].gap), String(width));
    assert.ok(lines.every(l => l.reduce((n, x) => n + (x.gap ? C.GAP_W : C.SO_W), 0) <= Math.max(C.SO_W, width - 12)), String(width));      // and each line fits
  }
});

// ── the stream waits for a speaker ────────────────────────────────────────────────────────────────────────────────

test("when the stream is held for want of a speaker, the page says so and what to do", () => {
  for (const none of [{}, { held: null }, { held: "yes" }, { held: 5 }, null]) assert.equal(C.heldView(none, 1000), null);
  const h = C.heldView({ held: { why: "  no speaker is on ", since: 1000 } }, 1090);
  assert.deepEqual(h, { why: "no speaker is on", since: 1000, secs: 90 });
  assert.deepEqual(C.heldText(h, 1090, "speaker"), { line: "Waiting for a speaker. Switch one on.", detail: `no speaker is on · ${C.fmtLong(90)}` });
  assert.deepEqual(C.heldText(h, 1090), C.heldText(h, 1090, "speaker"));                                         // (the speaker's wait is the default)
  assert.equal(C.heldText(h, 1030, "speaker").detail, "no speaker is on");                                       // not yet a minute: no duration
  assert.equal(C.heldText({ why: "", since: 1000 }, 1030, "speaker").detail, "");
  assert.equal(C.heldText({ why: "x", since: 5000 }, 1000, "speaker").detail, "x");                              // the radio's clock ahead of ours: no negative wait
  assert.equal(C.heldView({ held: { why: "w".repeat(300), since: "later" } }, 1000).why.length, 80);
  assert.equal(C.heldView({ held: {} }, 1000).since, 0);                                                         // a held stream with no details is still held
});

test("a hold for want of a speaker is told from the car's hold, which no switch brings back: the radio's own words, no instruction", () => {
  const held = { why: "no speaker is on", since: 1000 };
  const quiet = { held, outs: [{ id: "enclave", on: false, open: true }, { id: "hdmi", on: true, open: false }, { id: "pod:living", on: false, open: false }] };
  assert.equal(C.holdKind(quiet), "speaker");                                                                    // nothing is both on and there
  assert.equal(C.holdKind({ held, outs: quiet.outs.map(o => ({ ...o, on: false })) }), "speaker");               // every switch off
  assert.equal(C.holdKind({ held, outs }), "other");                                                             // the Enclave is on and there: someone hears it
  assert.equal(C.holdKind({ held }), "other");                                                                   // no speakers listed: no switch to point at
  assert.equal(C.holdKind({ outs }), "");                                                                        // not held
  assert.equal(C.holdKind(null), "");
  const car = { held: { why: "he is listening in the car", since: 1000 }, outs };
  assert.equal(C.holdKind(car), "other");
  assert.deepEqual(C.heldText(C.heldView(car, 1090), 1090, "other"), { line: "He is listening in the car", detail: C.fmtLong(90) });
  assert.deepEqual(C.heldText({ why: "", since: 1000 }, 1010, "other"), { line: "Paused for now.", detail: "" });
  assert.equal(C.heldNote(car), "He is listening in the car");                                                   // what the Devices menu says
  assert.equal(C.heldNote(quiet), "Waiting for a speaker. Switch one on in the House stereo.");
  assert.equal(C.heldNote({ outs }), "");
});

test("the stereo's pane is drawn again when its speakers, its wait or its chapter change, and not otherwise", () => {
  const base = { playing: true, kind: "song", outs };
  const sig = C.stereoSig(base);
  assert.equal(C.stereoSig({ ...base, outs: outs.map(o => ({ ...o })) }), sig);
  assert.notEqual(C.stereoSig({ ...base, outs: outs.map(o => (o.id === "hdmi" ? { ...o, on: true } : o)) }), sig);   // a switch
  assert.notEqual(C.stereoSig({ ...base, outs: outs.map(o => (o.id === "pod:living" ? { ...o, open: true } : o)) }), sig);   // a speaker that came back
  assert.notEqual(C.stereoSig({ ...base, held: { why: "no speaker is on", since: 5 } }), sig);
  assert.notEqual(C.stereoSig({ ...base, playing: false }), sig);
  const ch = n => C.stereoSig({ ...base, book: { id: "dune-m4b", chapter: n, playing: true } });
  assert.notEqual(ch(3), ch(4));
  assert.equal(C.stereoSig(null), C.stereoSig({}));
});

// ── audiobooks in the stereo's stream ─────────────────────────────────────────────────────────────────────────────

const lib = { "a/One.flac": { t: "One", a: "Artist A" }, "b/Two.flac": { t: "Two", a: "Artist B" } };
const lookup = p => lib[p] || null;
const book = { id: "dune-m4b", title: "Dune", pos: 100, chapter: 2, playing: true, speed: 1 };
const withBooks = {
  kind: "book", track: "", title: "Dune", artist: "Frank Herbert", playing: true, idx: 1, book, mix: { on: true, xfade: 6 },
  queue: ["a/One.flac", "book:dune-m4b", "b/Two.flac", "book:emma", "spotify:track:x"],
  titles: { "book:dune-m4b": ["Dune", "Frank Herbert"], "book:emma": ["Emma", "Jane Austen"] },
};

test("a book in the queue is a row with its title and author from `titles`, never playable in the browser, never mixed or faded", () => {
  const v = C.stereoView(withBooks, lookup);
  assert.deepEqual(v.items.map(i => [i.src, i.title, i.artist, i.playable]),
    [["library", "One", "Artist A", true], ["book", "Dune", "Frank Herbert", false], ["library", "Two", "Artist B", true], ["book", "Emma", "Jane Austen", false], ["spotify", "Spotify", "", false]]);
  assert.deepEqual(v.items.map(i => i.trans), ["", "cut", "cut", "cut", "cut"]);                  // DJ mode and the crossfade are for songs: a book is a cut either side
  assert.ok(v.hasBook && v.spotify);
  assert.equal(C.stereoView({ queue: ["book:nobody"] }, lookup).items[0].title, "Audiobook");   // a book the stereo sent no title for
  assert.ok(C.isBookItem("book:x") && !C.isBookItem("a/book:x") && !C.isBookItem("spotify:track:x") && !C.isBookItem(null));
});

test("while the stream is on a book the now-playing area shows its title and the chapter from state.book", () => {
  const v = C.stereoView(withBooks, lookup);
  assert.equal(v.kind, "book");
  assert.equal(v.now.title, "Dune");
  assert.equal(v.now.artist, "Frank Herbert");
  assert.equal(v.now.src, "book");
  assert.equal(v.chapter, 2);
  assert.equal(C.stereoView({ ...withBooks, book: { ...book, chapter: 0 } }, lookup).chapter, 0);              // no chapter said: none shown
  assert.equal(C.stereoView({ ...withBooks, kind: "song", track: "a/One.flac", idx: 0, title: "One" }, lookup).chapter, 0);   // a song is on: the book object is not what plays
  // the book alone, with no queue sent
  const lone = C.stereoView({ kind: "book", track: "", title: "Dune", artist: "Frank Herbert", playing: true, book }, null);
  assert.deepEqual(lone.items.map(i => [i.src, i.title, i.cur]), [["book", "Dune", true]]);
  assert.equal(lone.now.title, "Dune");
  // an older radio that sends no `kind` still reads as a book: the queue item the stream is at says so
  const old = C.stereoView({ track: "", playing: true, idx: 0, queue: ["book:dune-m4b"], titles: withBooks.titles }, lookup);
  assert.deepEqual([old.kind, old.now.title, old.now.src], ["book", "Dune", "book"]);
});

test("what the current item is: the stereo's word for it, else read from the track or the queue", () => {
  assert.equal(C.kindOf({ kind: "spotify" }), "spotify");
  assert.equal(C.kindOf({ kind: "book", track: "" }), "book");
  assert.equal(C.kindOf({ track: "spotify:track:x" }), "spotify");
  assert.equal(C.kindOf({ track: "a/One.flac" }), "song");
  assert.equal(C.kindOf({ track: "", queue: ["book:x"], idx: 0 }), "book");
  assert.equal(C.kindOf({ track: "", queue: [] }), "");
  assert.equal(C.kindOf({ kind: "podcast", track: "a/One.flac" }), "song");                     // a word it does not know is not believed
  assert.equal(C.kindOf(null), "");
});

test("\"Play here\" refuses Spotify and audiobooks with a toast, and a queue's book and Spotify items are never taken up", () => {
  assert.equal(C.hereRefusal({ track: "spotify:track:x" }), "That's Spotify: it plays on the house stereo, not in the browser.");   // (the words it always had)
  assert.equal(C.hereRefusal({ kind: "spotify", track: "" }), "That's Spotify: it plays on the house stereo, not in the browser.");
  assert.equal(C.hereRefusal(withBooks), "That's an audiobook: it plays on the house stereo, not in the browser.");
  assert.equal(C.hereRefusal({ track: "", queue: ["book:x"], idx: 0 }), "That's an audiobook: it plays on the house stereo, not in the browser.");
  assert.equal(C.hereRefusal({ kind: "song", track: "a/One.flac", queue: ["a/One.flac"] }), "");
  assert.equal(C.hereRefusal(null), "");
  const at = new Map([["a/One.flac", 0], ["b/Two.flac", 1]]), index = p => at.get(p);
  assert.deepEqual(C.pickUp({ track: "b/Two.flac", idx: 3, queue: ["a/One.flac", "book:emma", "spotify:track:x", "b/Two.flac"] }, index), { items: [0, 1], at: 1 });
  assert.deepEqual(C.pickUp(withBooks, index), { no: C.hereRefusal(withBooks) });
  assert.deepEqual(C.pickUp({ track: "z/Gone.flac", queue: ["z/Gone.flac"] }, index), { no: "That song isn't in the library any more." });
  assert.deepEqual(C.pickUp({ track: "a/One.flac", queue: ["book:emma"] }, index), { no: "That song isn't in the library any more." });
});

test("a stereo on a book is a device to be a remote for; a pod reading a book on its own still is not (the Audiobooks app lists those)", () => {
  assert.equal(C.offers({ track: "a/One.flac" }), true);
  assert.equal(C.offers(withBooks), true);
  assert.equal(C.offers({ track: "", queue: ["book:x"], idx: 0 }), true);
  assert.equal(C.offers({ track: "", queue: [], book }), false);
  assert.equal(C.offers({}), false);
  assert.equal(C.offers(null), false);
});
