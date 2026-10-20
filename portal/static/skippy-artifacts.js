/* Artifacts in Skippy's chat: what a reply PRODUCES (a plan, a document, code, a playable page, a picture, a song, a video, a
 * 3D model) shown as a collapsible card in the conversation instead of a wall of text.
 *
 *   SkippyArtifacts.fromBlock(b)       a SkippyMd fenced block → a spec, or null (short code stays inline)
 *   SkippyArtifacts.fromMedia(m)       a reply's media item ({kind, url, title, seconds}) → a spec
 *   SkippyArtifacts.card(spec, opts)   → <details class="artifact">: one line collapsed (glyph · title · size), the viewer opened
 *
 * Collapsed it is one line. Opened: on a wide screen the viewer unfolds in place; on a phone it fills the screen as a sheet with a
 * close key (the bridge's one-screen rule: a long document or code is paged, never scrolled).
 *
 * Safe by construction: text goes in with textContent; an HTML artifact runs in an iframe sandboxed WITHOUT allow-same-origin
 * (its scripts run, but it can't see the portal, its cookies or its storage); an SVG is shown as an <img> (no scripts run in an
 * image); media and models come only from the URLs the portal itself handed back. No network calls of its own, except loading
 * the self-hosted <model-viewer> module the first time a 3D model is opened.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyArtifacts = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const CODE_LINES = 15;                         // shorter code stays inline in the reply
  const KINDS = {                                // kind → glyph (templates/_glyphs.html)
    doc: "note", code: "console", html: "play", svg: "image", image: "image", audio: "audio", video: "pip", model: "cube",
  };
  let config = { modelViewer: "", glyph: null, md: null, pager: null };

  function fromBlock(b) {
    if (!b || b.t !== "code") return null;
    const lang = (b.lang || "").toLowerCase(), text = b.text || "";
    if (lang === "html" || lang === "htm") {
      const t = /<title[^>]*>([^<]{1,80})<\/title>/i.exec(text);
      return { kind: "html", title: t ? t[1].trim() : "page", content: text, lang: "html", name: slug(t ? t[1] : "page") + ".html" };
    }
    if (lang === "svg") return { kind: "svg", title: "drawing", content: text, lang: "svg", name: "drawing.svg" };
    if (lang === "markdown" || lang === "md" || lang === "plan") {
      const h = /^\s*#{1,6}\s+(.{1,80})$/m.exec(text);
      return { kind: "doc", title: h ? h[1].trim() : (lang === "plan" ? "plan" : "document"), content: text, lang: "md",
               name: slug(h ? h[1] : lang) + ".md" };
    }
    if (text.split("\n").length >= CODE_LINES) {
      return { kind: "code", title: lang || "code", content: text, lang: lang, name: "snippet." + (EXT[lang] || lang || "txt") };
    }
    return null;
  }
  const EXT = { python: "py", py: "py", javascript: "js", js: "js", typescript: "ts", ts: "ts", bash: "sh", sh: "sh", json: "json",
                css: "css", kotlin: "kt", rust: "rs", go: "go", sql: "sql", yaml: "yml", yml: "yml", java: "java", c: "c", cpp: "cpp" };
  function slug(s) { return String(s || "file").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "file"; }

  function fromMedia(m) {
    const kind = { audio: "audio", video: "video", image: "image", model: "model" }[m && m.kind] || "audio";
    return { kind, title: (m && m.title) || kind, url: m && m.url, seconds: m && m.seconds, open: true };
  }

  function size(spec) {
    if (spec.content != null) {
      const n = new Blob([spec.content]).size;
      return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
    }
    return spec.seconds ? spec.seconds + " s" : "";
  }

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function needModelViewer() {
    if (!config.modelViewer || customElements.get("model-viewer") || document.querySelector("script[data-model-viewer]")) return;
    const s = el("script"); s.type = "module"; s.src = config.modelViewer; s.dataset.modelViewer = "1"; document.head.appendChild(s);
  }

  // The viewer itself (built only when opened, so a closed card costs nothing: no iframe, no media loaded).
  function viewer(spec) {
    const k = spec.kind;
    if (k === "html") {
      const f = el("iframe", "art-frame");
      f.setAttribute("sandbox", "allow-scripts allow-pointer-lock");         // NOT allow-same-origin: it can't reach the portal
      f.setAttribute("referrerpolicy", "no-referrer"); f.title = spec.title; f.srcdoc = spec.content;
      return f;
    }
    if (k === "svg") { const i = el("img", "art-img"); i.alt = spec.title; i.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(spec.content); return i; }
    if (k === "image") { const i = el("img", "art-img"); i.alt = spec.title; i.src = spec.url; return i; }
    if (k === "audio") { const a = el("audio", "art-media"); a.controls = true; a.preload = "metadata"; a.src = spec.url; return a; }
    if (k === "video") { const v = el("video", "art-media"); v.controls = true; v.playsInline = true; v.preload = "metadata"; v.src = spec.url; return v; }
    if (k === "model") {
      needModelViewer();
      const m = document.createElement("model-viewer");
      m.className = "art-model"; m.setAttribute("src", spec.url); m.setAttribute("camera-controls", ""); m.setAttribute("auto-rotate", "");
      m.setAttribute("touch-action", "pan-y"); m.setAttribute("alt", spec.title);
      return m;
    }
    if (k === "doc") return config.md ? config.md(spec.content) : el("pre", "art-code", spec.content);
    const pre = el("pre", "art-code"); pre.appendChild(el("code", null, spec.content)); return pre;   // code
  }

  function actions(spec) {
    const bar = el("div", "art-keys");
    if (spec.content != null) {
      const copy = el("button", "key sm"); copy.type = "button"; copy.dataset.copy = spec.content; copy.title = copy.ariaLabel = "Copy";
      if (config.glyph) copy.appendChild(config.glyph("tag"));
      const dl = el("a", "key sm"); dl.title = dl.ariaLabel = "Download " + spec.name; dl.download = spec.name;
      dl.href = URL.createObjectURL(new Blob([spec.content], { type: spec.kind === "html" ? "text/html" : spec.kind === "svg" ? "image/svg+xml" : "text/plain" }));
      if (config.glyph) dl.appendChild(config.glyph("down"));
      bar.append(copy, dl);
    } else if (spec.url) {
      const open = el("a", "key sm"); open.href = spec.url; open.target = "_blank"; open.rel = "noopener"; open.title = open.ariaLabel = "Open on its own";
      if (config.glyph) open.appendChild(config.glyph("out"));
      bar.append(open);
    }
    return bar;
  }

  const narrow = () => typeof matchMedia === "function" && matchMedia("(max-width: 700px)").matches;

  // A phone: the opened artifact fills the screen; long text is paged with the bridge pager, never scrolled.
  function sheet(spec) {
    const s = el("div", "art-sheet"); s.setAttribute("role", "dialog"); s.setAttribute("aria-label", spec.title);
    const head = el("div", "art-head");
    if (config.glyph) head.appendChild(config.glyph(KINDS[spec.kind]));
    head.appendChild(el("span", "art-title", spec.title));
    const close = el("button", "key sm art-close"); close.type = "button"; close.title = close.ariaLabel = "Close";
    if (config.glyph) close.appendChild(config.glyph("x"));
    head.append(actions(spec), close);
    const body = el("div", "art-body");
    const v = viewer(spec);
    if ((spec.kind === "doc" || spec.kind === "code") && config.pager) {
      const pg = el("div", "pg"), flow = el("div", "pg-flow"), pips = el("div", "pips"); pips.appendChild(el("i"));
      flow.appendChild(v); pg.appendChild(flow); body.append(pg, pips);
      s.append(head, body); document.body.appendChild(s);
      config.pager(pg, pips);
    } else {
      body.appendChild(v); s.append(head, body); document.body.appendChild(s);
    }
    const shut = () => { s.remove(); document.removeEventListener("keydown", esc); };
    const esc = e => { if (e.key === "Escape") shut(); };
    close.addEventListener("click", shut); document.addEventListener("keydown", esc);
    return s;
  }

  function card(spec, opts) {
    opts = opts || {};
    const d = el("details", "artifact art-" + spec.kind); d.dataset.kind = spec.kind;
    const sum = el("summary");
    if (config.glyph) sum.appendChild(config.glyph(KINDS[spec.kind] || "note"));
    sum.appendChild(el("span", "art-title", spec.title));
    const sz = size(spec); if (sz) sum.appendChild(el("span", "art-size", sz));
    d.appendChild(sum);
    let built = false;
    function build() { if (built) return; built = true; const b = el("div", "art-body"); b.append(actions(spec), viewer(spec)); d.appendChild(b); }
    sum.addEventListener("click", e => {
      if (narrow() && !(spec.kind === "audio")) { e.preventDefault(); sheet(spec); }     // a phone: full screen (a song plays in place)
    });
    d.addEventListener("toggle", () => { if (d.open) build(); if (opts.onToggle) opts.onToggle(d.open); });
    if (spec.open) { d.open = true; build(); }
    return d;
  }

  return { fromBlock, fromMedia, card, configure(c) { config = Object.assign(config, c || {}); }, CODE_LINES, KINDS };
});
