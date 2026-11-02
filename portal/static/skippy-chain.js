/* The toolchain behind each of purple's replies (the Captain, 2026-11-01: "attach that metadata to each response as a tooltip or a
 * colorful and very thin histogram that makes up the bottom border of the response").
 *
 * The bridge sends one `chain` event at the end of every run (services/claude-bridge/toolchain.py): the calls in order, each with its
 * name (n), a short summary (d: the command, the path, the URL), its kind (k), how long it took (ms) and whether it worked (ok), plus
 * the flags: what the words claimed that the calls don't show ("says pushed; no git push"), and outward calls never mentioned.
 *
 *   SkippyChain.layout(calls)          each segment's share of the strip: by duration, with a floor so a quick call still shows
 *   SkippyChain.describe(call, i)      one call as the tooltip says it: "3. Bash · 2.4 s · failed" and its summary
 *   SkippyChain.listText(chain)        every call, one per line, and the flags: the text the list key shows and copies
 *   SkippyChain.render(chain, doc)     {strip, key, list}: the hairline strip (a segment per call, a warning glyph when flagged), the
 *                                      key for the reply's menu, and the list it opens (a pre.copy, so static/copy.js gives it Copy)
 *
 * Hovering a segment, or tapping it, shows its call in a tip above the strip, with the call as a copy chip. tests/js/chain runs the
 * pure half under Node; portal/tests/test_workshop_chain_ui.py the strip in the transcript.
 */
(function (root) {
  "use strict";
  const KINDS = ["read", "edit", "shell", "git", "web", "deploy", "house", "other"];
  const NAMES = {read: "read / search", edit: "edit / write", shell: "shell", git: "git", web: "web", deploy: "deploy / CI / live check",
                 house: "Mentat / house", other: "other"};
  const FLOOR_MS = 400;               // a call with no time (an old run) or a quick one still gets a visible sliver
  const WARN = "⚠︎";         // the warning glyph, text presentation (no emoji)
  const KEY = "⛓︎";          // the list key: a chain

  function kindOf(c) { return KINDS.indexOf(c && c.k) >= 0 ? c.k : "other"; }
  function secs(ms) {
    if (typeof ms !== "number") return "";
    return ms < 1000 ? ms + " ms" : ms < 60000 ? (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + " s" : Math.floor(ms / 60000) + " min " + Math.round((ms % 60000) / 1000) + " s";
  }
  function layout(calls) {
    const list = Array.isArray(calls) ? calls : [];
    const total = list.reduce((s, c) => s + (typeof (c && c.ms) === "number" ? c.ms : 0), 0);
    const floor = Math.max(FLOOR_MS, total * 0.01);
    const weights = list.map(c => Math.max(floor, typeof (c && c.ms) === "number" ? c.ms : floor));
    const sum = weights.reduce((s, w) => s + w, 0) || 1;
    return list.map((c, i) => ({i: i, k: kindOf(c), bad: c && c.ok === false, share: weights[i] / sum}));
  }
  function describe(c, i) {
    const bits = [(i + 1) + ". " + String((c && c.n) || "tool")];
    if (typeof c.ms === "number") bits.push(secs(c.ms));
    bits.push(c.ok === false ? "failed" : c.ok === true ? "ok" : "no result");
    return {head: bits.join(" · "), detail: String(c.d || ""), kind: NAMES[kindOf(c)]};
  }
  function listText(chain) {
    const calls = (chain && Array.isArray(chain.calls)) ? chain.calls : [];
    const lines = calls.map((c, i) => { const d = describe(c, i); return d.head + (d.detail ? "  " + d.detail : ""); });
    if (chain && chain.more) lines.push("… and " + chain.more + " more");
    const flags = (chain && Array.isArray(chain.flags)) ? chain.flags : [];
    if (flags.length) lines.push("", ...flags.map(f => WARN + " " + f.text));
    if (!calls.length) lines.unshift("No calls: this reply is words only.");
    return lines.join("\n");
  }

  function el(doc, tag, cls, txt) { const e = doc.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }

  function render(chain, doc) {
    const calls = (chain && Array.isArray(chain.calls)) ? chain.calls : [];
    const flags = (chain && Array.isArray(chain.flags)) ? chain.flags : [];
    const strip = el(doc, "div", "chain" + (flags.length ? " flagged" : ""));
    strip.setAttribute("role", "img");
    strip.setAttribute("aria-label", calls.length + (calls.length === 1 ? " call" : " calls") + (flags.length ? ", " + flags.length + " mismatch" + (flags.length === 1 ? "" : "es") : ""));
    const tip = el(doc, "div", "chain-tip"); tip.hidden = true;
    let pinned = -1;
    function show(i) {
      tip.replaceChildren();
      if (i === "flags") {
        tip.appendChild(el(doc, "div", "th warn", WARN + " What the words claim and the calls don't show"));
        flags.forEach(f => tip.appendChild(el(doc, "div", "fl", f.text)));
      } else {
        const d = describe(calls[i], i);
        tip.appendChild(el(doc, "div", "th k-" + kindOf(calls[i]), d.head));
        tip.appendChild(el(doc, "div", "kd", d.kind));
        if (d.detail) tip.appendChild(el(doc, "code", "code", d.detail));      // a copy chip (static/copy.js)
      }
      tip.hidden = false;
    }
    function hide() { tip.hidden = true; pinned = -1; }
    layout(calls).forEach(s => {
      const seg = el(doc, "span", "seg k-" + s.k + (s.bad ? " bad" : ""));
      seg.style.flexGrow = String(Math.round(s.share * 10000) / 100);
      seg.setAttribute("data-i", String(s.i));
      seg.addEventListener("pointerenter", (e) => { if (!e || e.pointerType !== "touch") show(s.i); });
      seg.addEventListener("click", () => { if (pinned === s.i && !tip.hidden) hide(); else { pinned = s.i; show(s.i); } });   // a tap on a phone
      strip.appendChild(seg);
    });
    if (flags.length) {
      const w = el(doc, "span", "warn", WARN);
      w.setAttribute("title", flags.map(f => f.text).join("\n"));
      w.addEventListener("pointerenter", (e) => { if (!e || e.pointerType !== "touch") show("flags"); });
      w.addEventListener("click", () => { if (pinned === "flags" && !tip.hidden) hide(); else { pinned = "flags"; show("flags"); } });
      strip.appendChild(w);
    }
    // A hover tip goes when the pointer leaves the strip, unless it moved onto the tip (to copy the call); a tapped one stays until tapped again.
    let overTip = false;
    const mouse = (e) => !e || e.pointerType !== "touch";
    strip.addEventListener("pointerleave", (e) => { if (mouse(e)) setTimeout(() => { if (!overTip && pinned === -1) tip.hidden = true; }, 200); });
    tip.addEventListener("pointerenter", (e) => { if (mouse(e)) overTip = true; });
    tip.addEventListener("pointerleave", (e) => { if (mouse(e)) { overTip = false; if (pinned === -1) tip.hidden = true; } });

    const list = el(doc, "pre", "copy chain-list", listText(chain)); list.hidden = true;
    const key = el(doc, "button", "ck" + (flags.length ? " flagged" : ""), (flags.length ? WARN + " " : "") + KEY + " " + calls.length);
    key.type = "button";
    key.setAttribute("title", "The calls behind this reply");
    key.setAttribute("aria-expanded", "false");
    key.addEventListener("click", () => { list.hidden = !list.hidden; key.setAttribute("aria-expanded", String(!list.hidden)); });
    return {strip: strip, tip: tip, key: key, list: list, show: show, hide: hide};
  }

  const api = {KINDS: KINDS, NAMES: NAMES, WARN: WARN, layout: layout, describe: describe, listText: listText, render: render, secs: secs};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyChain = api;
})(typeof window !== "undefined" ? window : this);
