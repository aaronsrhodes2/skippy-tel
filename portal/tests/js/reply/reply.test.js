// Reply to a part (portal/static/skippy-reply.js), shared by green and the agent. portal/tests/test_chat_reply.py runs this under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const R = require("../../../static/skippy-reply.js");

test("a quote is the selection trimmed, blank runs squeezed, and cut at MAX with an ellipsis", () => {
  assert.equal(R.clean("  the bridge is up \r\n\r\n\r\n\r\nand green  "), "the bridge is up\n\nand green");
  assert.equal(R.clean(""), "");
  const long = R.clean("x".repeat(R.MAX + 50));
  assert.equal(long.length, R.MAX);
  assert.ok(long.endsWith("…"));
});

test("the message is each quote as a markdown blockquote, then the words", () => {
  assert.equal(R.compose(["only this part"], "why?"), "> only this part\n\nwhy?");
  assert.equal(R.compose(["two\nlines", "another"], "both wrong"), "> two\n> lines\n\n> another\n\nboth wrong");
  assert.equal(R.compose(["para one\n\npara two"], ""), "> para one\n>\n> para two");    // a quote alone may go; its blank line stays inside it
  assert.equal(R.compose([], "plain"), "plain");
  assert.equal(R.compose(["  ", ""], "plain"), "plain");                                   // empty quotes are dropped
});

test("split is the reverse of compose, and only blockquotes at the very start are quotes", () => {
  for (const [qs, t] of [[["a"], "b"], [["two\nlines", "another"], "words\nover lines"], [["para one\n\npara two"], ""], [[], "plain"]]) {
    assert.deepEqual(R.split(R.compose(qs, t)), {quotes: qs, rest: t});
  }
  assert.deepEqual(R.split("hello\n> not a quote"), {quotes: [], rest: "hello\n> not a quote"});
  assert.deepEqual(R.split(">tight\n\n\n> after two blanks\n\nrest"), {quotes: ["tight", "after two blanks"], rest: "rest"});
});
