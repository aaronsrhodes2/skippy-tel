// The keys' shared helpers (portal/static/keys.js): a clicker shows it is busy while its job runs, long enough to be seen.
// portal/tests/test_player_js.py runs every portal/tests/js/*.test.js under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
require("../../static/keys.js");
const K = globalThis.SkippyKeys;

function el() { const a = {}; return { dataset: {}, setAttribute: (k, v) => { a[k] = v; }, removeAttribute: (k) => { delete a[k]; }, attrs: a }; }

test("a clicker is busy while its job runs, and at least BUSY_MIN_MS", async () => {
  const k = el();
  let finish; const job = new Promise(r => { finish = r; });
  const run = K.busy(k, () => job);
  assert.equal(k.dataset.busy, "1"); assert.equal(k.attrs["aria-busy"], "true");
  finish(42);
  assert.equal(await run, 42);
  assert.equal(k.dataset.busy, "1");                                    // a quick job still shows for a moment
  await new Promise(r => setTimeout(r, K.BUSY_MIN_MS + 30));
  assert.equal(k.dataset.busy, undefined); assert.equal(k.attrs["aria-busy"], undefined);
});

test("a failed job clears the busy look and still throws", async () => {
  const k = el();
  await assert.rejects(K.busy(k, async () => { throw new Error("no"); }), /no/);
  await new Promise(r => setTimeout(r, K.BUSY_MIN_MS + 30));
  assert.equal(k.dataset.busy, undefined);
});

test("every lamp state has words", () => {
  for (const s of ["on", "idle", "boot", "warn", "off"]) assert.ok(K.LAMP[s]);
});
