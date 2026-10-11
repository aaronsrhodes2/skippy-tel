/* Markdown for the private Skippy's replies: headings, lists, quotes, fenced code, inline code, bold, italic, links.
 *
 *   SkippyMd.parse(text)          → blocks (pure: tested under Node in tests/js/md/skippy-md.test.js)
 *   SkippyMd.render(text, doc)    → a <div class="md"> built from DOM nodes
 *
 * Safe by construction: the text is only ever put in with textContent, never innerHTML, so nothing in a reply can become markup;
 * a link is made only for http(s) addresses and always opens in a new tab with rel="noopener". It makes no network calls.
 * Inline `code` becomes <code class="code">, which static/copy.js turns into a one-tap copy chip like everywhere else on the portal.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyMd = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))|(https?:\/\/[^\s<>`]+)/g;

  function inline(s) {
    const out = [], re = new RegExp(INLINE.source, "g");        // its own regex: inline() calls itself, and lastIndex is per regex
    let last = 0, m;
    while ((m = re.exec(s))) {
      if (m.index > last) out.push({ t: "text", v: s.slice(last, m.index) });
      const tok = m[0];
      if (m[1]) out.push({ t: "code", v: tok.slice(1, -1) });
      else if (m[2]) out.push({ t: "b", c: inline(tok.slice(2, -2)) });
      else if (m[3]) out.push({ t: "i", c: inline(tok.slice(1, -1)) });
      else if (m[4]) { const k = tok.indexOf("]("); out.push({ t: "a", href: tok.slice(k + 2, -1), c: inline(tok.slice(1, k)) }); }
      else {                                                    // a bare address: its trailing punctuation is the sentence's
        const url = tok.replace(/[.,;:!?)\]]+$/, "");
        out.push({ t: "a", href: url, c: [{ t: "text", v: url }] });
        if (url.length < tok.length) out.push({ t: "text", v: tok.slice(url.length) });
      }
      last = m.index + tok.length;
    }
    if (last < s.length) out.push({ t: "text", v: s.slice(last) });
    return out;
  }

  function parse(text) {
    const lines = String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n"), blocks = [];
    let i = 0, para = [];
    const flush = () => { if (para.length) { blocks.push({ t: "p", inl: inline(para.join("\n")) }); para = []; } };
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if ((m = /^\s*```\s*([\w+-]*)\s*$/.exec(line))) {                 // fenced code, to the closing fence (or the end)
        flush();
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++;
        blocks.push({ t: "code", lang: m[1] || "", text: body.join("\n") });
      } else if ((m = /^(#{1,3})\s+(.*\S)\s*$/.exec(line))) {
        flush(); blocks.push({ t: "h", n: m[1].length, inl: inline(m[2]) }); i++;
      } else if (/^\s*[-*]\s+\S/.test(line) || /^\s*\d+[.)]\s+\S/.test(line)) {
        flush();
        const ordered = /^\s*\d+[.)]/.test(line), items = [];
        while (i < lines.length && (ordered ? /^\s*\d+[.)]\s+\S/ : /^\s*[-*]\s+\S/).test(lines[i])) {
          items.push(inline(lines[i].replace(ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*]\s+/, ""))); i++;
        }
        blocks.push({ t: ordered ? "ol" : "ul", items: items });
      } else if (/^>\s?/.test(line)) {
        flush();
        const q = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
        blocks.push({ t: "quote", inl: inline(q.join("\n")) });
      } else if (/^\s*$/.test(line)) { flush(); i++; }
      else { para.push(line); i++; }
    }
    flush();
    return blocks;
  }

  function render(text, doc) {
    doc = doc || document;
    const el = (tag, cls, txt) => { const e = doc.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
    const fill = (parent, nodes) => nodes.forEach(n => {
      if (n.t === "text") parent.appendChild(el("span", null, n.v));
      else if (n.t === "code") parent.appendChild(el("code", "code", n.v));
      else if (n.t === "b") { const b = el("strong"); fill(b, n.c); parent.appendChild(b); }
      else if (n.t === "i") { const b = el("em"); fill(b, n.c); parent.appendChild(b); }
      else if (n.t === "a") { const a = el("a"); a.href = n.href; a.target = "_blank"; a.rel = "noopener noreferrer"; fill(a, n.c); parent.appendChild(a); }
    });
    const box = el("div", "md");
    parse(text).forEach(b => {
      if (b.t === "p") { const p = el("p"); fill(p, b.inl); box.appendChild(p); }
      else if (b.t === "h") { const h = el("h" + (b.n + 3), "mdh"); fill(h, b.inl); box.appendChild(h); }
      else if (b.t === "code") { const pre = el("pre", "codeblock"); pre.appendChild(el("code", null, b.text)); if (b.lang) pre.title = b.lang; box.appendChild(pre); }
      else if (b.t === "quote") { const q = el("blockquote"); fill(q, b.inl); box.appendChild(q); }
      else { const list = el(b.t); b.items.forEach(it => { const li = el("li"); fill(li, it); list.appendChild(li); }); box.appendChild(list); }
    });
    return box;
  }

  return { parse: parse, render: render };
});
