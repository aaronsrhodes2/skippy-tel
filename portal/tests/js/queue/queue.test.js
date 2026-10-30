// The chat queue (portal/static/skippy-queue.js), shared by green and the agent. portal/tests/test_chat_queue.py runs this under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Q = require("../../../static/skippy-queue.js");

test("a repeat is the same words, whatever the case, the spacing or the punctuation at the ends", () => {
  assert.ok(Q.same("Is the bridge up?", "is the  bridge up"));
  assert.ok(Q.same("check the voice.", "Check the voice!"));
  assert.ok(!Q.same("is the bridge up", "is the bridge down"));
  assert.ok(!Q.same("", "  "));                                   // nothing is never a repeat of nothing
});

test("add keeps the first and refuses a repeat anywhere in the line, not only the last one", () => {
  const q = [];
  assert.ok(Q.add(q, "A thing"));
  assert.ok(Q.add(q, "B thing"));
  assert.ok(!Q.add(q, "a thing?"));                              // A, B, A: the second A is the same message
  assert.ok(!Q.add(q, "   "));
  assert.deepEqual(q, ["A thing", "B thing"]);
});

test("the line goes as one message, numbered in order, with repeats taken out", () => {
  assert.equal(Q.gulp(["only this"]), "only this");
  assert.equal(Q.gulp(["one", "One.", "two"]), "Several things queued while you worked, in order:\n1. one\n2. two");
  assert.equal(Q.gulp(["same", "SAME"]), "same");                 // two copies of one thing are just that thing
});
