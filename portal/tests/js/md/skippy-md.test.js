// The private Skippy's markdown (portal/static/skippy-md.js) under Node's built-in test runner; tests/test_skippy_md.py runs this.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const M = require("../../../static/skippy-md.js");

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ""; this.own = ""; this.attrs = {}; }
  appendChild(c) { this.children.push(c); return c; }
  set textContent(t) { this.own = String(t); }
  get textContent() { return this.own + this.children.map(c => c.textContent).join(""); }
}
const doc = { createElement: (t) => new El(t) };
const kinds = (text) => M.parse(text).map(b => b.t);

test("plain text is one paragraph, and blank lines split paragraphs", () => {
  assert.deepEqual(kinds("one line\nstill one"), ["p"]);
  assert.deepEqual(kinds("a\n\nb"), ["p", "p"]);
  assert.deepEqual(kinds(""), []);
  assert.deepEqual(kinds(null), []);
});

test("blocks: headings, lists, quotes and fenced code", () => {
  assert.deepEqual(kinds("# Title\n- a\n- b\n1. x\n2. y\n> said\n```py\nprint(1)\n```"), ["h", "ul", "ol", "quote", "code"]);
  const code = M.parse("```js\nlet a = 1;\n\nlet b = 2;\n```")[0];
  assert.equal(code.lang, "js"); assert.equal(code.text, "let a = 1;\n\nlet b = 2;");      // a fence keeps its blank lines
  assert.equal(M.parse("```\nnever closed")[0].text, "never closed");                      // an open fence runs to the end
  assert.equal(M.parse("- a\n- b\n- c")[0].items.length, 3);
});

test("inline: code, bold, italic, links and bare addresses", () => {
  const p = M.parse("use `docker ps` and **bold** and *it* and [docs](https://x.test/a) and https://y.test/b.")[0].inl;
  assert.deepEqual(p.filter(n => n.t !== "text").map(n => n.t), ["code", "b", "i", "a", "a"]);
  assert.equal(p.find(n => n.t === "a" && n.href === "https://y.test/b").c[0].v, "https://y.test/b");   // the full stop is the sentence's
  assert.equal(p[p.length - 1].v, ".");
});

test("a link is made only for http(s): javascript: and data: stay plain text", () => {
  for (const bad of ["[x](javascript:alert(1))", "[x](data:text/html,hi)", "[x](//evil.test)", "[x](vbscript:1)"]) {
    const html = M.render(bad, doc);
    const anchors = []; (function walk(n) { if (n.tag === "a") anchors.push(n); n.children.forEach(walk); })(html);
    assert.equal(anchors.length, 0, bad);
  }
  const ok = M.render("[x](https://ok.test/p?q=1)", doc);
  const a = ok.children[0].children[0];
  assert.equal(a.tag, "a"); assert.equal(a.href, "https://ok.test/p?q=1"); assert.equal(a.target, "_blank"); assert.match(a.rel, /noopener/);
});

test("html in a reply is only ever text", () => {
  const box = M.render("<img src=x onerror=alert(1)> and <script>bad()</script>", doc);
  assert.match(box.textContent, /<img src=x onerror=alert\(1\)>/);                         // shown, not built
  let tags = new Set(); (function walk(n) { tags.add(n.tag); n.children.forEach(walk); })(box);
  assert.ok(![...tags].some(t => ["img", "script"].includes(t)));
});

test("render builds the structure the chat styles", () => {
  const box = M.render("## Heading\n\ntext with `code`\n\n- one\n- two\n\n```\nx\n```", doc);
  assert.deepEqual(box.children.map(c => c.tag), ["h5", "p", "ul", "pre"]);
  assert.equal(box.children[2].children.length, 2);
  assert.equal(box.children[3].children[0].own, "x");
});

test("a SKIPPY_QUESTION line becomes options and leaves the words", () => {
  const q = M.extractQuestion('Which colour?\n\nSKIPPY_QUESTION: {"question":"Colour?","options":["Orange","Green"]}\n');
  assert.equal(q.text, "Which colour?");
  assert.deepEqual(q.question, { question: "Colour?", options: ["Orange", "Green"] });
  assert.equal(M.extractQuestion("`SKIPPY_QUESTION: {\"question\":\"Q?\",\"options\":[\"a\",\"b\"]}`").question.options.length, 2);   // in backticks too
  assert.deepEqual(M.extractQuestion("no question here"), { text: "no question here", question: null });
});

test("a malformed question never becomes buttons, and its line never shows", () => {
  for (const bad of ['{"question":"Q?","options":["only one"]}', '{"question":"Q?","options":["a","b","c","d","e"]}',
                     '{"question":"","options":["a","b"]}', '{"question":"Q?","options":["a",5]}', '{"question":"Q?","options":["' + "x".repeat(61) + '","b"]}', "{not json}"]) {
    const r = M.extractQuestion("words\nSKIPPY_QUESTION: " + bad);
    assert.equal(r.question, null, bad); assert.equal(r.text, "words", bad);
  }
});

test("the task list arrives as lines and becomes a checklist", () => {
  const todo = M.parseTodo("✓ read the code\n▶ write the test\n○ ship it\nnot an item\n");
  assert.deepEqual(todo.map(t => t.state), ["done", "doing", "todo"]);
  assert.equal(todo[1].text, "write the test");
  assert.deepEqual(M.parseTodo(""), []);
  assert.deepEqual(M.parseTodo(null), []);
});
