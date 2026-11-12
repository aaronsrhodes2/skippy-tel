// The toolchain strip (portal/static/skippy-chain.js) under each of purple's replies. portal/tests/test_workshop_chain_ui.py runs this under Node.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../../../static/skippy-chain.js");

// A small DOM: enough for render() (elements, classes, attributes, listeners, hidden, style).
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ""; this.textContent = ""; this.hidden = false; this.style = {}; this.on = {}; this.attrs = {}; }
  appendChild(c) { this.children.push(c); return c; }
  replaceChildren(...cs) { this.children = cs; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(ev, fn) { (this.on[ev] = this.on[ev] || []).push(fn); }
  fire(ev, e) { (this.on[ev] || []).forEach(fn => fn(e || {})); }
}
const doc = {createElement: (t) => new El(t)};
const text = (n) => [n.textContent, ...n.children.map(text)].join(" ");

const chain = {t: "chain", calls: [
  {n: "Read", d: "portal/app.py", k: "read", ms: 40, ok: true},
  {n: "Bash", d: "python -m pytest -q", k: "shell", ms: 30000, ok: true},
  {n: "Bash", d: "git push origin main", k: "git", ms: 2000, ok: false},
  {n: "Edit", d: "a.py", k: "nonsense"},
], flags: [{rule: "deploy", text: "says deployed or live; no deploy call and no look at the live service"}]};

test("each call gets a share of the strip by its time, with a floor, in order, and an unknown kind is other", () => {
  const segs = C.layout(chain.calls);
  assert.deepEqual(segs.map(s => s.k), ["read", "shell", "git", "other"]);
  assert.ok(Math.abs(segs.reduce((s, x) => s + x.share, 0) - 1) < 1e-9);
  assert.ok(segs[1].share > segs[2].share && segs[2].share > segs[0].share);
  assert.equal(segs[0].share, segs[3].share);                     // the quick call and the one with no time both get the floor
  assert.ok(segs[0].share > 0.005);
  assert.deepEqual(segs.map(s => s.bad), [false, false, true, false]);
  assert.deepEqual(C.layout(undefined), []);
});

test("a call reads as its number, name, time and outcome, then its summary", () => {
  assert.deepEqual(C.describe(chain.calls[2], 2), {head: "3. Bash · 2.0 s · failed", detail: "git push origin main", kind: "git"});
  assert.equal(C.describe(chain.calls[3], 3).head, "4. Edit · no result");
  assert.equal(C.secs(30000), "30 s");
  assert.equal(C.secs(125000), "2 min 5 s");
});

test("the list is every call, one per line, then the flags; a reply with no calls says so", () => {
  const lines = C.listText(chain).split("\n");
  assert.equal(lines[0], "1. Read · 40 ms · ok  portal/app.py");
  assert.equal(lines.length, 4 + 2);
  assert.ok(lines[5].startsWith(C.WARN + " says deployed"));
  assert.match(C.listText({calls: [], flags: []}), /^No calls/);
  assert.match(C.listText({calls: chain.calls, flags: [], more: 7}), /and 7 more$/);
});

test("render: a segment per call, red for a failed one, a warning glyph when flagged, a key and a copyable list", () => {
  const r = C.render(chain, doc);
  const segs = r.strip.children.filter(c => c.className.startsWith("seg"));
  assert.deepEqual(segs.map(s => s.className), ["seg k-read", "seg k-shell", "seg k-git bad", "seg k-other"]);
  assert.ok(r.strip.className.includes("flagged"));
  assert.ok(r.strip.children.some(c => c.className === "warn" && c.textContent === C.WARN));
  assert.equal(r.list.className, "copy chain-list");                   // static/copy.js gives a pre.copy its Copy button
  assert.ok(r.list.hidden && r.tip.hidden);
  assert.ok(r.key.textContent.includes("4"));
  r.key.fire("click");
  assert.ok(!r.list.hidden && r.key.attrs["aria-expanded"] === "true");
  r.key.fire("click");
  assert.ok(r.list.hidden);
});

test("hovering a segment shows its call with a copy chip; a tap pins it and a second tap closes it; the glyph shows the flags", () => {
  const r = C.render(chain, doc);
  const [, shell] = r.strip.children;
  shell.fire("pointerenter", {pointerType: "mouse"});
  assert.ok(!r.tip.hidden);
  assert.match(text(r.tip), /2\. Bash .* 30 s .* ok/);
  assert.ok(r.tip.children.some(c => c.tag === "code" && c.className === "code" && c.textContent === "python -m pytest -q"));
  const fresh = C.render(chain, doc);
  const git = fresh.strip.children[2];
  git.fire("pointerenter", {pointerType: "touch"});                     // a finger's enter is not a hover
  assert.ok(fresh.tip.hidden);
  git.fire("click"); assert.ok(!fresh.tip.hidden); assert.match(text(fresh.tip), /failed/);
  git.fire("click"); assert.ok(fresh.tip.hidden);
  fresh.strip.children.find(c => c.className === "warn").fire("click");
  assert.match(text(fresh.tip), /no deploy call/);
});

test("a reply with no flags has no glyph", () => {
  const r = C.render({calls: [chain.calls[0]], flags: []}, doc);
  assert.ok(!r.strip.children.some(c => c.className === "warn") && !r.strip.className.includes("flagged"));
});
