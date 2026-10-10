// The bridge's pure helpers (portal/static/bridge-pure.js): paging a long list into screens, and the retry rules for a dead zone.
// portal/tests/test_player_js.py runs every portal/tests/js/*.test.js under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const B = require("../../static/bridge-pure.js");

test("a list is cut into screens of whole rows", () => {
  assert.deepEqual(B.paginate([], 300), [0]);
  assert.deepEqual(B.paginate([50, 50, 50], 300), [0]);
  assert.deepEqual(B.paginate([100, 100, 100, 100], 300), [0, 3]);
  assert.deepEqual(B.paginate([400, 100], 300), [0, 1]);                       // too tall for a screen: alone on one
  assert.equal(B.paginate(new Array(21000).fill(56), 560).length, 2100);
});

test("a heading is never left alone at the bottom of a screen, and a run of headings still makes progress", () => {
  assert.deepEqual(B.paginate([50, 50, 50, 40, 50, 50], 200, [false, false, false, true, false, false]), [0, 3]);
  assert.deepEqual(B.paginate([50, 50, 50, 40, 50, 50], 200), [0, 4]);
  assert.deepEqual(B.paginate([30, 30, 30], 50, [true, true, true]), [0, 1, 2]);
});

test("pageOf finds the screen a row is on", () => {
  assert.deepEqual([0, 9, 10, 24, 25, 99].map(i => B.pageOf([0, 10, 25], i)), [0, 0, 1, 1, 2, 2]);
});

test("after a drop the retries start quick and settle at every 30 seconds, for as long as it takes", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5, 500].map(B.recoverDelay), [2000, 4000, 8000, 15000, 30000, 30000, 30000]);
  assert.equal(B.recoverDelay(-1), 2000); assert.equal(B.recoverDelay(), 2000);
});

test("a stall is 'waiting' with no progress for 8 seconds, never before it started or while it is not waiting", () => {
  assert.equal(B.stalled(100000, 92000, true), true);
  assert.equal(B.stalled(100000, 92001, true), false);
  assert.equal(B.stalled(100000, 1, false), false);
  assert.equal(B.stalled(100000, 0, true), false);
});
