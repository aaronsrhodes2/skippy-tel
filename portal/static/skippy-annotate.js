/* Mark up a picture and send it back as a change request (the Captain, 2026-10-25): tap a screenshot in Skippy's chat (the running
 * game, the portal), draw arrows and boxes in colour, and the marked-up copy goes into the message box with a legend that says, in
 * words, what is where: the colour, the shape and its exact place in the ORIGINAL picture's pixels. The words matter: Skippy Core's
 * coder is text-only and sees pictures through the vision model's description, so the legend carries the marks exactly.
 *
 *   SkippyAnnotate.open({src, name, onSend(dataUrl, legend, fileName)})   full screen over the page; Esc or ✕ closes it
 *
 * Colours are the house palette (CLAUDE.md, the colour doctrine): red, amber, green, cyan. The Captain says what each one asks for in
 * his own words; the legend only says where they are. Nothing is stored on the device: the picture is drawn here and sent.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyAnnotate = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const COLORS = [{name: "red", hex: "#FF4444"}, {name: "amber", hex: "#FFAA00"}, {name: "green", hex: "#00FF88"}, {name: "cyan", hex: "#00CCFF"}];
  const NUMS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳";
  const MAX_EDGE = 1600, BUDGET = 1100000;      // the copy that is sent: the chat's per-picture budget (portal/tests/test_limit_chains.py)

  // The words: one line per mark, positions in the original picture's pixels (what the agent's own screenshot file has).
  function legend(marks, name, w, h) {
    if (!marks.length) return "";
    const lines = marks.map((m, i) => {
      const n = NUMS[i] || "(" + (i + 1) + ")", p = (x, y) => "(" + Math.round(x) + "," + Math.round(y) + ")";
      return m.tool === "arrow" ? `${n} ${m.color} arrow from ${p(m.x1, m.y1)} to ${p(m.x2, m.y2)}`
                                : `${n} ${m.color} box from ${p(Math.min(m.x1, m.x2), Math.min(m.y1, m.y2))} to ${p(Math.max(m.x1, m.x2), Math.max(m.y1, m.y2))}`;
    });
    return `[Annotated copy of ${name ? "your screenshot " + name : "the picture"} (${w}x${h}; positions in its pixels):\n` + lines.join("\n") + "]\n";
  }

  function drawMark(ctx, m, i, scale) {
    const c = (COLORS.find(k => k.name === m.color) || COLORS[0]).hex, lw = Math.max(3, 4 * scale);
    ctx.save(); ctx.strokeStyle = c; ctx.fillStyle = c; ctx.lineWidth = lw; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.shadowColor = "rgba(0,0,0,.85)"; ctx.shadowBlur = 3 * scale;
    let bx, by;
    if (m.tool === "arrow") {
      ctx.beginPath(); ctx.moveTo(m.x1, m.y1); ctx.lineTo(m.x2, m.y2); ctx.stroke();
      const a = Math.atan2(m.y2 - m.y1, m.x2 - m.x1), hl = 14 * scale + lw * 2;
      ctx.beginPath(); ctx.moveTo(m.x2, m.y2);
      ctx.lineTo(m.x2 - hl * Math.cos(a - 0.45), m.y2 - hl * Math.sin(a - 0.45));
      ctx.lineTo(m.x2 - hl * Math.cos(a + 0.45), m.y2 - hl * Math.sin(a + 0.45)); ctx.closePath(); ctx.fill();
      bx = m.x1; by = m.y1;
    } else {
      ctx.strokeRect(Math.min(m.x1, m.x2), Math.min(m.y1, m.y2), Math.abs(m.x2 - m.x1), Math.abs(m.y2 - m.y1));
      bx = Math.min(m.x1, m.x2); by = Math.min(m.y1, m.y2);
    }
    if (i != null) {                              // its number, where the mark starts
      const r = 11 * scale; ctx.shadowBlur = 0;
      ctx.beginPath(); ctx.arc(bx, by, r, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#000"; ctx.font = `700 ${Math.round(13 * scale)}px system-ui, sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(i + 1), bx, by + 0.5);
    }
    ctx.restore();
  }

  function exportCopy(img, marks) {                // the marked-up copy as a JPEG data URL inside the chat's budget
    const w = img.naturalWidth, h = img.naturalHeight;
    for (const [edge, q] of [[MAX_EDGE, 0.9], [MAX_EDGE, 0.8], [1300, 0.78], [1000, 0.72], [800, 0.68]]) {
      const k = Math.min(1, edge / Math.max(w, h)), c = document.createElement("canvas");
      c.width = Math.round(w * k); c.height = Math.round(h * k);
      const ctx = c.getContext("2d"); ctx.drawImage(img, 0, 0, c.width, c.height); ctx.scale(k, k);
      const s = Math.max(w, h) / 900;             // marks stay readable at any picture size
      marks.forEach((m, i) => drawMark(ctx, m, i, s));
      const url = c.toDataURL("image/jpeg", q);
      if (url.length <= BUDGET) return url;
    }
    throw new Error("too big");
  }

  const CSS = `
.sa-wrap{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.94);display:flex;flex-direction:column;touch-action:none;color:#e6edf3;font:14px system-ui,sans-serif}
.sa-bar{display:flex;gap:6px;align-items:center;padding:8px 12px;flex-wrap:wrap}
.sa-bar button{min-width:40px;height:40px;border:1px solid #2a3a35;background:#05090b;color:#e6edf3;font:600 18px system-ui;cursor:pointer;border-radius:0}
.sa-bar button[aria-pressed=true]{outline:2px solid #00FF88;outline-offset:-2px}
.sa-bar .sw{width:40px;padding:0}.sa-bar .sw i{display:block;width:22px;height:22px;margin:auto;border-radius:50%}
.sa-bar .sp{flex:1}.sa-bar .go{background:#00FF88;color:#000;padding:0 14px}
.sa-stage{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:0 12px 12px}
.sa-box{position:relative;max-width:100%;max-height:100%}.sa-box img{display:block;max-width:100%;max-height:calc(100vh - 80px);user-select:none;-webkit-user-drag:none}
.sa-box canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
.sa-hint{padding:0 12px 6px;color:#8b949e;font-size:12px}`;

  function open(opts) {
    if (!document.getElementById("sa-css")) { const s = document.createElement("style"); s.id = "sa-css"; s.textContent = CSS; document.head.appendChild(s); }
    const wrap = document.createElement("div"); wrap.className = "sa-wrap"; wrap.setAttribute("role", "dialog"); wrap.setAttribute("aria-label", "Mark up this picture");
    const bar = document.createElement("div"); bar.className = "sa-bar";
    const stage = document.createElement("div"); stage.className = "sa-stage";
    const box = document.createElement("div"); box.className = "sa-box";
    const img = new Image(); img.alt = opts.name || "picture"; img.crossOrigin = "anonymous"; img.src = opts.src;
    const cv = document.createElement("canvas"); box.append(img, cv); stage.append(box);
    const hint = document.createElement("div"); hint.className = "sa-hint";
    hint.textContent = "Drag to draw. Each mark is numbered; say what each colour asks for in your message.";
    wrap.append(bar, hint, stage); document.body.appendChild(wrap);
    const marks = []; let tool = "arrow", color = "red", draft = null;
    const key = (label, title, on) => { const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.title = b.ariaLabel = title; b.addEventListener("click", on); return b; };
    const tools = [key("↗", "Arrow", () => pick("arrow")), key("▢", "Box", () => pick("box"))];
    const swatches = COLORS.map(c => { const b = key("", "Colour: " + c.name, () => paint(c.name)); b.className = "sw"; const i = document.createElement("i"); i.style.background = c.hex; b.append(i); b.dataset.c = c.name; return b; });
    const sp = document.createElement("span"); sp.className = "sp";
    const undo = key("↶", "Undo the last mark", () => { marks.pop(); redraw(); });
    const close = key("✕", "Close without sending", done);
    const go = key("✓ Add to message", "Add the marked-up picture to your message", send); go.className = "go";
    bar.append(...tools, ...swatches, sp, undo, close, go);
    function pick(t) { tool = t; tools.forEach(b => b.setAttribute("aria-pressed", String(b.title.toLowerCase() === t))); }
    function paint(c) { color = c; swatches.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.c === c))); }
    pick("arrow"); paint("red");
    const scale = () => img.naturalWidth / (img.clientWidth || 1);
    function redraw() {
      const k = img.clientWidth / (img.naturalWidth || 1);
      cv.width = img.clientWidth * (window.devicePixelRatio || 1); cv.height = img.clientHeight * (window.devicePixelRatio || 1);
      const ctx = cv.getContext("2d"); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height);
      ctx.scale((window.devicePixelRatio || 1) * k, (window.devicePixelRatio || 1) * k);
      const s = 1 / k;                            // marks drawn at screen size, stored in picture pixels
      marks.forEach((m, i) => drawMark(ctx, m, i, s));
      if (draft) drawMark(ctx, draft, null, s);
    }
    img.addEventListener("load", redraw); window.addEventListener("resize", redraw);
    const at = (e) => { const r = cv.getBoundingClientRect(), k = scale(); return [(e.clientX - r.left) * k, (e.clientY - r.top) * k]; };
    cv.addEventListener("pointerdown", e => { cv.setPointerCapture(e.pointerId); const [x, y] = at(e); draft = {tool, color, x1: x, y1: y, x2: x, y2: y}; });
    cv.addEventListener("pointermove", e => { if (!draft) return; [draft.x2, draft.y2] = at(e); redraw(); });
    cv.addEventListener("pointerup", () => {
      if (draft && Math.hypot(draft.x2 - draft.x1, draft.y2 - draft.y1) > 6 * scale()) marks.push(draft);   // a tap is not a mark
      draft = null; redraw();
    });
    function onKey(e) { if (e.key === "Escape") done(); if ((e.ctrlKey || e.metaKey) && e.key === "z") { marks.pop(); redraw(); } }
    document.addEventListener("keydown", onKey);
    function done() { document.removeEventListener("keydown", onKey); window.removeEventListener("resize", redraw); wrap.remove(); }
    function send() {
      if (!img.complete || !img.naturalWidth) return;
      let url;
      try { url = exportCopy(img, marks); } catch (e) { hint.textContent = "That picture is too big to send."; return; }
      const base = (opts.name || "picture").replace(/\.\w+$/, "");
      opts.onSend(url, legend(marks, opts.name, img.naturalWidth, img.naturalHeight), base + "-marked.jpg");
      done();
    }
    return {close: done};
  }

  return {open, legend, COLORS};
});
