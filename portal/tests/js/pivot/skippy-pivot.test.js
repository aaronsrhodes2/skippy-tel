// The pivot phrase rule (portal/static/skippy-pivot.js, docs/pivot.md): "let's pivot" and friends ask for a pivot; "by the way", "also",
// "I noticed" only add; a pivot phrase deep in a long message is just words; "pivot table" is not a pivot.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../../../static/skippy-pivot.js");

test("the Captain's own phrases are pivots", () => {
  for (const s of ["let's pivot", "Let's pivot to the camera key", "lets pivot", "let's change the subject", "Let's change the topic.",
                   "on to a new feature", "On to the next thing", "moving on to something else", "time to switch gears", "switching gears: the status page",
                   "new topic: voice", "change of subject", "next up: the log intake", "now for something else", "we should pivot", "pivot to the portal"]) {
    assert.equal(P.detect(s), "pivot", s);
  }
});

test("by the way, also, I noticed and the like only add, even with a pivot phrase right behind", () => {
  for (const s of ["by the way, the camera key is gone", "btw the deploy is red", "Also, the Back key is odd", "also let's pivot later", "I noticed the lamp is grey",
                   "i just noticed something", "one more thing", "oh and one more thing", "while we're at it, rename it", "side note: it was slow", "quick question about the bridge",
                   "unrelated, but is the starter up?", "speaking of voices, the F5 one is good", "P.S. check the logs"]) {
    assert.equal(P.detect(s), "additive", s);
  }
});

test("ordinary messages, pivot tables and a phrase buried deep in a long message are nothing", () => {
  for (const s of ["", "   ", null, undefined, "fix the failing test", "make the pivot table sort by date", "let's pivot the table by month", "the pivot point of the lever is wrong",
                   "can you read services.py and tell me what the status row means, and then if it is stale we can think about it, and let's pivot when done"]) {
    assert.equal(P.detect(s), null, String(s));
  }
});

test("the question before a pivot appears only when stopping would lose something", () => {
  assert.equal(P.inTheMiddle(null), null);
  assert.equal(P.inTheMiddle({running: false, goal: "done", uncommitted: 0, queued: 0}), null);
  const q = P.inTheMiddle({running: true, uncommitted: 3, queued: 1, task: "the status page"});
  assert.match(q, /in the middle of something \(the status page\)/);
  assert.match(q, /a run is still going/);
  assert.match(q, /3 changes not committed yet/);
  assert.match(q, /1 message waiting/);
  assert.match(q, /Pivot now, or finish first\?/);
  assert.match(P.inTheMiddle({goal: "running"}), /a goal is still being worked on/);
  assert.match(P.inTheMiddle({uncommitted: 1}), /1 change not committed/);
});
