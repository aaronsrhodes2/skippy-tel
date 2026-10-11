// Skippy's voice client: the pure helpers (portal/static/skippy-voice.js), under Node's built-in test runner.
// portal/tests/test_voice.py runs this when Node >= 18 is on the machine. (Its own folder, like the video player's.)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const V = require("../../../static/skippy-voice.js");

test("pickMime takes the first recording type the browser can make, or none", () => {
  assert.equal(V.pickMime(m => m === "audio/webm"), "audio/webm");
  assert.equal(V.pickMime(m => m.startsWith("audio/mp4")), "audio/mp4");        // Safari
  assert.equal(V.pickMime(() => true), "audio/webm;codecs=opus");               // Chrome and Firefox: opus first
  assert.equal(V.pickMime(() => false), "");                                    // let the browser choose
  assert.equal(V.pickMime(() => { throw new Error("no"); }), "");
});

test("speakable drops code chips, links and markdown marks", () => {
  assert.equal(V.speakable("Use `docker ps` and see https://x.test/a now"), "Use docker ps and see now");
  assert.equal(V.speakable("**Bold** _this_ # head"), "Bold this head");
  assert.equal(V.speakable(""), "");
  assert.equal(V.speakable(null), "");
});

test("speakable never exceeds the gate's 500 characters and cuts at a sentence", () => {
  const long = ("This is a sentence that goes on. ").repeat(40);
  const out = V.speakable(long);
  assert.ok(out.length <= V.MAX_SPEECH);
  assert.ok(out.endsWith("."));
  assert.ok(V.speakable("word ".repeat(300)).length <= V.MAX_SPEECH);
});
