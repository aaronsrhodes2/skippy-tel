// static/skippy-artifacts.js: which fenced blocks become artifacts, and what a reply's media becomes. Run: node --test tests/js/artifacts
const test = require("node:test");
const assert = require("node:assert");
const A = require("../../../static/skippy-artifacts.js");

test("an html fence is a playable page named by its <title>", () => {
  const s = A.fromBlock({ t: "code", lang: "html", text: "<!DOCTYPE html><title>ASCII Arkanoid</title><p>hi" });
  assert.strictEqual(s.kind, "html"); assert.strictEqual(s.title, "ASCII Arkanoid"); assert.strictEqual(s.name, "ascii-arkanoid.html");
});

test("svg, markdown and plan fences are artifacts; a plan is titled by its first heading", () => {
  assert.strictEqual(A.fromBlock({ t: "code", lang: "svg", text: "<svg/>" }).kind, "svg");
  const p = A.fromBlock({ t: "code", lang: "plan", text: "intro\n## Rebuild the bridge\n- one" });
  assert.strictEqual(p.kind, "doc"); assert.strictEqual(p.title, "Rebuild the bridge");
  assert.strictEqual(A.fromBlock({ t: "code", lang: "markdown", text: "no heading" }).title, "document");
});

test("long code is an artifact, short code stays inline, and other blocks are left alone", () => {
  const long = Array.from({ length: A.CODE_LINES }, (_, i) => "x = " + i).join("\n");
  const s = A.fromBlock({ t: "code", lang: "python", text: long });
  assert.strictEqual(s.kind, "code"); assert.strictEqual(s.name, "snippet.py");
  assert.strictEqual(A.fromBlock({ t: "code", lang: "python", text: "x = 1" }), null);
  assert.strictEqual(A.fromBlock({ t: "p", inl: [] }), null);
  assert.strictEqual(A.fromBlock(null), null);
});

test("a reply's media is an opened artifact of its own kind", () => {
  const s = A.fromMedia({ kind: "audio", url: "/chat/media/abc", title: "Calm song", seconds: 92 });
  assert.deepStrictEqual([s.kind, s.title, s.url, s.open], ["audio", "Calm song", "/chat/media/abc", true]);
  assert.strictEqual(A.fromMedia({ kind: "model", url: "/x.glb" }).kind, "model");
  assert.strictEqual(A.fromMedia({ kind: "weird", url: "/x" }).kind, "audio");
});
