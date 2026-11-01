"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../../static/audiobooks-pure.js");

const B = (id, t, a, extra) => Object.assign({ id, t, a, n: "", d: 3600, ch: [["One", 0], ["Two", 1000], ["Three", 2500]], oa: false }, extra);
const books = [B("hob", "The Hobbit", "Tolkien"), B("dune", "Dune", "Herbert", { n: "Scott Brick", oa: true }), B("ant", "An Ant", "Bug"), B("zed", "Zed", "Zee")];

test("times", () => {
  assert.equal(P.fmt(0), "0:00");
  assert.equal(P.fmt(65.9), "1:05");
  assert.equal(P.fmt(3723), "1:02:03");
  assert.equal(P.fmt(-4), "0:00");
  assert.equal(P.left(12000), "3h 20m");
  assert.equal(P.left(90), "2m");
});

test("which chapter a second is in", () => {
  const ch = books[0].ch;
  assert.equal(P.chapterAt(ch, 0), 0);
  assert.equal(P.chapterAt(ch, 999.9), 0);
  assert.equal(P.chapterAt(ch, 1000), 1);
  assert.equal(P.chapterAt(ch, 99999), 2);
  assert.equal(P.chapterAt([], 5), 0);
});

test("status: OpenAudible's finished flag gives way to a fresh listen", () => {
  assert.equal(P.status(books[0], undefined), "new");
  assert.equal(P.status(books[0], { pos: 30, last_played: 5 }), "listening");
  assert.equal(P.status(books[1], undefined), "finished");
  assert.equal(P.status(books[1], { pos: 10, last_played: 9 }), "listening");
  assert.equal(P.status(books[1], { pos: 10, last_played: 9, finished: true }), "finished");
});

test("search and filters", () => {
  const places = { hob: { pos: 100, last_played: 50 }, ant: { pos: 5, last_played: 90 } };
  assert.deepEqual(P.visible(books, places, "", "all").map(b => b.id), ["ant", "dune", "hob", "zed"]);      // by title, no article
  assert.deepEqual(P.visible(books, places, "", "listening").map(b => b.id), ["ant", "hob"]);               // most recent first
  assert.deepEqual(P.visible(books, places, "", "new").map(b => b.id), ["zed"]);
  assert.deepEqual(P.visible(books, places, "", "finished").map(b => b.id), ["dune"]);
  assert.deepEqual(P.visible(books, places, "scott brick", "all").map(b => b.id), ["dune"]);                // the narrator
  assert.deepEqual(P.visible(books, places, "tolkien hobbit", "all").map(b => b.id), ["hob"]);
  assert.deepEqual(P.visible(books, places, "nothing here", "all"), []);
});

test("series order: each series in reading order, then the books with no series by title", () => {
  const S = (id, t, sr, sq, rd, a) => ({ id, t, a: a || "Z", n: "", sr, sq, rd, d: 1, ch: [], oa: false });
  const lib = [S("w", "Zebra", "", null), S("b2", "Beta Two", "The Beta Saga", 2, "2005-01-01"), S("a1", "Alpha", "Alpha Cycle", 1, "2001-01-01"),
               S("b1", "Beta One", "The Beta Saga", 1, "2004-01-01"), S("a0", "Alpha Prequel", "Alpha Cycle", null, "1999-01-01"),
               S("x", "Aardvark", "", null), S("a1b", "Alpha Again", "Alpha Cycle", 1, "2002-06-01", "Y")];
  const ids = sort => P.visible(lib, {}, "", "all", sort).map(b => b.id);
  assert.deepEqual(ids(), ["a1", "a1b", "a0", "b1", "b2", "x", "w"]);                  // Alpha Cycle (1 by date, then the unnumbered), The Beta Saga (filed under B), then the rest
  assert.deepEqual(ids("series"), ids());
  assert.deepEqual(ids("title"), ["x", "a1", "a1b", "a0", "b1", "b2", "w"]);                  // Aardvark, Alpha, Alpha Again, Alpha Prequel, Beta One, Beta Two, Zebra
  assert.deepEqual(ids("author"), ["a1b", "a1", "a0", "b1", "b2", "x", "w"]);                // by author, a series still in order within one author
  assert.equal(P.visible(lib, {}, "beta saga", "all").length, 2);                      // the series name is searchable
  assert.deepEqual(P.SORTS, ["series", "title", "author"]);
});

test("the grid is cut into rows", () => {
  assert.equal(P.colsFor(375), 3);
  assert.equal(P.colsFor(50), 2);
  assert.equal(P.colsFor(5000), 8);
  const rows = P.gridRows(books, 3, 150);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map(r => r.books.length), [3, 1]);
  assert.ok(rows.every(r => r.type === "shelf" && r.h === 150));
});

test("resume", () => {
  assert.equal(P.resumeAt(undefined, 3600), 0);
  assert.equal(P.resumeAt({ pos: 100 }, 3600), 97);
  assert.equal(P.resumeAt({ pos: 1 }, 3600), 0);
  assert.equal(P.resumeAt({ pos: 3590 }, 3600), 0);              // the last seconds: start over
  assert.equal(P.resumeAt({ pos: 100, finished: true }, 3600), 0);
});

test("saving the place", () => {
  assert.ok(P.shouldSave(0, 1000, false));
  assert.ok(!P.shouldSave(1000, 5000, false));
  assert.ok(P.shouldSave(1000, 16000, false));
  assert.ok(P.shouldSave(1000, 1001, true));
});

test("where a book is on a box now", () => {
  assert.equal(P.remotePos({ pos: 100, playing: true, speed: 1.5 }, 10, 3600), 115);
  assert.equal(P.remotePos({ pos: 100, playing: false, speed: 1 }, 10, 3600), 100);
  assert.equal(P.remotePos({ pos: 3599, playing: true, speed: 1 }, 50, 3600), 3600);
  assert.equal(P.remotePos(null, 1, 1), 0);
});
