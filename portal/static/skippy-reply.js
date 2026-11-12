/* Reply to a part (like Claude Desktop): select any words in a message, yours or Skippy's, in the public chat or the agent's
 * transcript, and a Reply key appears by the selection. Tapping it puts those words in the composer as a quote above what you type;
 * the message then goes with each quote as a markdown blockquote at its start, so every brain (Skippy Core, Claude, the agent) knows
 * which part you mean with no change on the server. Your own bubble shows the quotes as quote blocks again.
 *
 *   SkippyReply.clean(text)            the selection as a quote: trimmed, blank runs squeezed, at most MAX characters
 *   SkippyReply.compose(quotes, text)  the message: "> quote" blocks, a blank line, then the words
 *   SkippyReply.split(text)            the reverse: {quotes, rest} (only blockquotes at the very start are quotes)
 *   SkippyReply.quoteNode(text, doc)   a quote block for a bubble or the composer
 *   SkippyReply.attach({scope, bodies, onQuote})   the Reply key over `scope`, for selections inside `bodies` (a selector)
 *
 * Works with a mouse and with a long-press on iOS and Android: the key sits under the selection (the phone's own menu sits above it),
 * and it acts on pointerdown, before a tap can clear the selection. tests/js/reply runs the pure half under Node.
 */
(function (root) {
  "use strict";
  const MAX = 1200;
  function clean(text) {
    let t = String(text || "").replace(/\r\n?/g, "\n").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    if (t.length > MAX) t = t.slice(0, MAX - 1).trimEnd() + "…";
    return t;
  }
  function compose(quotes, text) {
    const qs = (quotes || []).map(clean).filter(Boolean)
      .map(q => q.split("\n").map(l => l ? "> " + l : ">").join("\n"));
    const words = String(text || "").trim();
    return qs.concat(words ? [words] : []).join("\n\n");
  }
  function split(text) {
    const lines = String(text || "").split("\n"), quotes = [];
    let cur = null, i = 0;
    for (; i < lines.length; i++) {
      const l = lines[i];
      if (/^>/.test(l)) { (cur = cur || []).push(l.replace(/^> ?/, "")); continue; }
      if (!l.trim()) { if (cur) { quotes.push(cur.join("\n").trim()); cur = null; } continue; }
      break;
    }
    if (cur) quotes.push(cur.join("\n").trim());
    return {quotes: quotes.filter(Boolean), rest: lines.slice(i).join("\n").trim()};
  }
  function quoteNode(text, doc) {
    const q = (doc || document).createElement("blockquote"); q.className = "quote"; q.textContent = text; return q;
  }

  function attach(o) {
    const doc = o.doc || document, scope = o.scope, bodies = o.bodies;
    const SVG = "http://www.w3.org/2000/svg";
    const key = doc.createElement("button");
    key.type = "button"; key.className = "key sm reply-key"; key.hidden = true; key.dataset.tel = "chat:reply";
    key.title = key.ariaLabel = "Reply to this";
    const s = doc.createElementNS(SVG, "svg"); s.setAttribute("class", "g"); s.setAttribute("aria-hidden", "true");
    const u = doc.createElementNS(SVG, "use"); u.setAttribute("href", "#g-reply"); s.appendChild(u); key.appendChild(s);
    doc.body.appendChild(key);
    let text = "", range = null, hideTimer = null, fired = 0;
    const inside = (n) => { const e = n && (n.nodeType === 1 ? n : n.parentElement); const b = e && e.closest(bodies); return b && scope.contains(b) ? b : null; };
    const coarse = () => root.matchMedia && root.matchMedia("(pointer: coarse)").matches;
    function hide() { clearTimeout(hideTimer); key.hidden = true; text = ""; range = null; }
    function place() {
      if (!range) return;
      const rects = range.getClientRects(), r = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
      if (!r || (!r.width && !r.height)) { hide(); return; }
      const box = scope.getBoundingClientRect();
      if (r.bottom < box.top || r.top > box.bottom) { key.style.visibility = "hidden"; return; }   // scrolled out of the chat
      key.style.visibility = "";
      const w = key.offsetWidth || 36, h = key.offsetHeight || 36, gap = coarse() ? 30 : 8;     // a phone's selection handle hangs below the words
      let x = Math.min(Math.max(r.right - w / 2, 8), root.innerWidth - w - 8);
      let y = r.bottom + gap;
      if (y + h > Math.min(box.bottom, root.innerHeight) - 4) y = Math.max(r.top - h - gap, 4);      // no room under it: over it
      key.style.left = Math.round(x) + "px"; key.style.top = Math.round(y) + "px";
    }
    function look() {
      const sel = root.getSelection && root.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 300); return; }
      if (!inside(sel.anchorNode) || !inside(sel.focusNode)) { hide(); return; }
      const t = clean(sel.toString());
      if (!t) { hide(); return; }
      clearTimeout(hideTimer);
      text = t; range = sel.getRangeAt(0).cloneRange();
      key.hidden = false; place();
    }
    let t0 = null;
    doc.addEventListener("selectionchange", () => { clearTimeout(t0); t0 = setTimeout(look, 120); });
    function fire() {
      if (!text || Date.now() - fired < 600) return;
      fired = Date.now();
      const q = text;
      try { root.getSelection().removeAllRanges(); } catch (e) {}
      hide();
      o.onQuote(q);
    }
    key.addEventListener("pointerdown", (e) => { e.preventDefault(); fire(); });   // before the tap clears the selection
    key.addEventListener("click", (e) => { e.preventDefault(); fire(); });          // the keyboard (and anything without pointer events)
    const follow = () => { if (!key.hidden) requestAnimationFrame(place); };
    doc.addEventListener("scroll", follow, {capture: true, passive: true});
    root.addEventListener("resize", follow);
    doc.addEventListener("keydown", (e) => { if (e.key === "Escape" && !key.hidden) hide(); });
    return {key: key, hide: hide, refresh: look};
  }

  const api = {MAX: MAX, clean: clean, compose: compose, split: split, quoteNode: quoteNode, attach: attach};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyReply = api;
})(typeof window !== "undefined" ? window : this);
