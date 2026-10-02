/* Bilby's Audio Service: the browser app (portal/templates/music.html is its shell, bilby-player.js its logic, bilby-player.css its look).

   What happens here, in order of the file:
     the gate           the portal hands out a pass; the music gate on skippy-pc serves the library for it
     the library        catalog.json (cached in IndexedDB so the app opens instantly), lyrics.json, cover thumbnails
     the engine         one or two <audio> elements, preloading, errors, the browser's autoplay rule
     the queue + DJ     every edit goes through BilbyCore.Queue; Bilby DJ picks ahead of time
     the views          Home, Artists, Albums, Songs, Genres, Recently added/played, artist and album pages, search:
                        all drawn with a virtual list, so 21,000 songs are ~25 DOM rows
     Connect            this device tells the portal what it plays; another device can take over or be a remote
   Nothing about what is played ever goes to the portal except through Connect (the Captain's own devices), and the
   telemetry beacon (base.html) only ever sees a tile tap. */
(function () {
  "use strict";
  const C = window.BilbyCore;
  const root = document.getElementById("bp");
  if (!C || !root) return;

  // ═══ small helpers ══════════════════════════════════════════════════════════════════════════════════════════════
  const $ = (sel, scope) => (scope || root).querySelector(sel);
  const $$ = (sel, scope) => Array.prototype.slice.call((scope || root).querySelectorAll(sel));
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const wait = ms => new Promise(r => setTimeout(r, ms));
  /** localStorage, never trusted: private windows and blocked storage throw, and the player must still work. */
  const store = {
    get(k, d) { try { const v = localStorage.getItem("music." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("music." + k, JSON.stringify(v)); } catch (e) { /* full or blocked: carry on without */ } },
  };
  const cfg = { gate: root.dataset.gateUrl, checkin: root.dataset.checkinUrl, command: root.dataset.commandUrl };
  const env = C.detectEnv(navigator.userAgent, navigator.platform, navigator.maxTouchPoints);
  const coarse = window.matchMedia && matchMedia("(pointer: coarse)").matches;
  root.classList.toggle("coarse", !!coarse);
  // Big targets (>= 48 px) for the car and for any finger on a screen this wide; a mouse in a short window keeps the normal layout.
  const touchMq = window.matchMedia ? matchMedia("(pointer: coarse) and (min-width: 700px)") : null;
  const syncTouch = () => root.classList.toggle("touch", env.tesla || !!(touchMq && touchMq.matches));
  syncTouch();
  if (touchMq && touchMq.addEventListener) touchMq.addEventListener("change", () => { syncTouch(); if (window.BilbyApp && BilbyApp.S.lib) BilbyApp.renderView(true); });
  const TITLE = "Bilby's Audio Service";
  const SINGLES = "Singles";

  let device = store.get("device", "");
  if (!/^[A-Za-z0-9_-]{8,40}$/.test(device || "")) {
    device = Array.from(crypto.getRandomValues(new Uint8Array(12)), b => b.toString(16).padStart(2, "0")).join("");
    store.set("device", device);
  }

  // ═══ icons (24x24 paths, drawn in currentColor) ═════════════════════════════════════════════════════════════════
  const P = {
    play: "M8 5v14l11-7z", pause: "M6 19h4V5H6v14zm8-14v14h4V5h-4z",
    prev: "M6 6h2v12H6zm3.5 6l8.5 6V6z", next: "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z",
    shuffle: "M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z",
    repeat: "M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z",
    repeat1: "M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4zm-4-2V9h-1l-2 1v1h1.5v4H13z",
    vol: "M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z",
    mute: "M16.5 12A4.5 4.5 0 0 0 14 7.97v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51A8.8 8.8 0 0 0 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06a8.99 8.99 0 0 0 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z",
    queue: "M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18c-.31-.11-.65-.18-1-.18-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3V8h3V6h-5z",
    devices: "M17 2H7c-1.1 0-2 .9-2 2v16c0 1.1.9 1.99 2 1.99L17 22c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-5 2c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2zm0 16c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z",
    sleep: "M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z",
    dj: "M12 1a9 9 0 0 0-9 9v7c0 1.66 1.34 3 3 3h3v-8H5v-2a7 7 0 0 1 14 0v2h-4v8h3c1.66 0 3-1.34 3-3v-7a9 9 0 0 0-9-9z",
    search: "M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
    close: "M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
    more: "M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z",
    note: "M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z",
    home: "M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z",
    artist: "M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z",
    album: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 14.5c-2.49 0-4.5-2.01-4.5-4.5S9.51 7.5 12 7.5s4.5 2.01 4.5 4.5-2.01 4.5-4.5 4.5zm0-5.5c-.55 0-1 .45-1 1s.45 1 1 1 1-.45 1-1-.45-1-1-1z",
    tag: "M17.63 5.84C17.27 5.33 16.67 5 16 5L5 5.01C3.9 5.01 3 5.9 3 7v10c0 1.1.9 1.99 2 1.99L16 19c.67 0 1.27-.33 1.63-.84L22 12l-4.37-6.16z",
    plus: "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
    clock: "M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z",
    back: "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z",
    down: "M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z",
    up: "M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z",
    keys: "M20 5H4c-1.1 0-1.99.9-1.99 2L2 17c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm-9 3h2v2h-2V8zm0 3h2v2h-2v-2zM8 8h2v2H8V8zm0 3h2v2H8v-2zm-1 2H5v-2h2v2zm0-3H5V8h2v2zm9 7H8v-2h8v2zm0-4h-2v-2h2v2zm0-3h-2V8h2v2zm3 3h-2v-2h2v2zm0-3h-2V8h2v2z",
    trash: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    addq: "M14 10H2v2h12v-2zm0-4H2v2h12V6zm4 8v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zM2 16h8v-2H2v2z",
    grip: "M9 5h2v2H9zm4 0h2v2h-2zM9 9h2v2H9zm4 0h2v2h-2zm-4 4h2v2H9zm4 0h2v2h-2zm-4 4h2v2H9zm4 0h2v2h-2z",
    check: "M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
    chev: "M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z",
  };
  const ico = n => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="${P[n] || ""}"/></svg>`;
  function fillIcons(scope) { for (const i of $$("[data-i]", scope)) if (!i.firstChild) i.innerHTML = ico(i.dataset.i); }
  function setIcon(b, name) { const i = b.querySelector("[data-i]"); if (i && i.dataset.i !== name) { i.dataset.i = name; i.innerHTML = ico(name); } }

  // ═══ state ══════════════════════════════════════════════════════════════════════════════════════════════════════
  const prefs = store.get("prefs", {}) || {};
  const S = {
    lib: null, q: new C.Queue(),
    repeat: ["off", "all", "one"].includes(prefs.repeat) ? prefs.repeat : "off", shuffle: !!prefs.shuffle, dj: prefs.dj !== false,
    vol: typeof prefs.vol === "number" ? clamp(prefs.vol, 0, 1) : 1, muted: !!prefs.muted, canVolume: true,
    sorts: Object.assign({ tracks: "title", albums: "title", artists: "name" }, prefs.sorts || {}),
    hist: Array.isArray(store.get("hist", [])) ? store.get("hist", []) : [],
    loadTok: 0, resumeAt: 0, want: false, qv: 1, qSent: 0, qLo: 0, errStreak: 0, lastPersist: 0, lyrics: null,
    sleep: new C.SleepTimer(),
    armed: new C.Armed(env.webkit ? null : () => !!(navigator.userActivation && navigator.userActivation.hasBeenActive)),
    tapWhy: "", playAt: 0, ourPauseAt: 0, route: { name: "home" }, search: "", nav: 0, view: null, scrollMemo: new Map(), showEarlier: false,
    sheet: "", tab: "np", loading: false, seeking: false, scrollAt: 0, geo: "",
  };
  const savePrefs = () => store.set("prefs", { repeat: S.repeat, shuffle: S.shuffle, dj: S.dj, vol: S.vol, muted: S.muted, sorts: S.sorts });
  const curTrack = () => { const c = S.q.cur; return c && S.lib ? S.lib.T[c.t] || null : null; };

  // ═══ messages: toast, live region, notice ═══════════════════════════════════════════════════════════════════════
  function toast(text, o) {
    o = o || {};
    const box = $("#toasts"), t = el("div", "toast");
    t.append(el("span", "", text));
    if (o.action) { const b = el("button", "", o.action.label); b.type = "button"; b.onclick = () => { t.remove(); o.action.run(); }; t.append(b); }
    box.append(t);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => t.remove(), o.ms || 3800);
  }
  const announce = text => { $("#live").textContent = text; };
  function showNotice(text, o) {
    o = o || {};
    const n = $("#notice");
    n.hidden = false; n.className = "notice " + (o.kind || ""); n.dataset.key = o.key || "";
    const kids = [el("span", "grow", text)];
    if (o.retry) { const b = el("button", "pill-btn", o.label || "Retry"); b.type = "button"; b.onclick = o.retry; kids.push(b); }
    if (o.progress) { const p = el("progress"); p.id = "notice-progress"; kids.push(p); }
    n.replaceChildren(...kids);
  }
  function hideNotice(key) { const n = $("#notice"); if (!key || n.dataset.key === key) { n.hidden = true; n.dataset.key = ""; } }

  // ═══ the gate: where the library is, and a pass to read it ══════════════════════════════════════════════════════
  const GATE_DOWN = "skippy-pc isn't reachable from here: Tailscale Funnel may be off, or the PC is asleep.";
  const gate = {
    info: null, inflight: null,
    fresh() { return !!this.info && this.info.exp - Date.now() / 1000 > 1800; },
    /** A pass that is good for at least half an hour more, fetched from the portal if needed (one request at a time). */
    ensure(force) {
      if (!force && this.fresh()) return Promise.resolve(this.info);
      if (!this.inflight) {
        this.inflight = (async () => {
          let r;
          try { r = await fetch(cfg.gate, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } }); }
          catch (e) { throw Object.assign(new Error("The portal isn't reachable from here."), { kind: "net" }); }
          const type = r.headers.get("content-type") || "";
          if (r.status === 401 || r.status === 403 || r.redirected || type.includes("text/html")) {
            throw Object.assign(new Error("You have been signed out of the portal. Reload the page to sign in again."), { kind: "auth" });
          }
          let g = null;
          try { g = await r.json(); } catch (e) { /* not JSON */ }
          if (!g || !g.ok) throw Object.assign(new Error((g && g.why) || "The library is out of reach."), { kind: "home" });
          const changed = !this.info || this.info.pass !== g.pass || this.info.url !== g.url;
          this.info = g;
          if (changed && S.lib) setTimeout(() => { showTrack(); list.refresh(); qList.refresh(); }, 0);   // covers can load now (the catalog may have come from the saved copy first)
          return g;
        })().finally(() => { this.inflight = null; });
      }
      return this.inflight;
    },
  };
  /** The gate's URL for a file: the pass is part of the path, never a cookie (the gate is on another origin). */
  function gateUrl(kind, path, query) {
    const g = gate.info;
    if (!g) return "";
    return `${g.url}/t/${g.pass}/${kind}${path ? "/" + path.split("/").map(encodeURIComponent).join("/") : ""}${query || ""}`;
  }
  const artUrlW = (path, w) => (path && gate.info ? gateUrl("art", path, `?w=${w}`) : "");
  const artUrl = (path, px) => artUrlW(path, C.artWidth(px, window.devicePixelRatio || 1));
  function gateProblem(err) {
    if (err && err.kind === "auth") { showNotice(err.message, { kind: "bad", key: "gate", retry: () => location.reload(), label: "Reload" }); return; }
    const msg = err && err.kind === "home" ? err.message : GATE_DOWN;
    showNotice(msg, { kind: "bad", key: "gate", retry: retryGate });
  }
  function retryGate() {
    hideNotice("gate");
    gate.info = null;
    if (!S.lib) { loadLibrary(true); return; }
    gate.ensure(true).then(() => { hideNotice("gate"); S.errStreak = 0; if (S.want) actions.resume(); }, gateProblem);
  }

  // ═══ IndexedDB: the catalog and the lyric summaries, so the next visit opens instantly ══════════════════════════
  // A saved copy is only a shortcut: whatever goes wrong with it (blocked, private window, a delete still pending in another tab,
  // a browser that never answers) the answer is "no saved copy" after a moment, never a library that waits forever.
  const idb = (() => {
    let dbp = null;
    const open = () => dbp || (dbp = new Promise(resolve => {
      const giveUp = setTimeout(() => resolve(null), 2500);
      try {
        const r = indexedDB.open("bilby-music", 1);
        r.onupgradeneeded = () => r.result.createObjectStore("kv");
        r.onsuccess = () => { clearTimeout(giveUp); resolve(r.result); };
        r.onerror = r.onblocked = () => { clearTimeout(giveUp); resolve(null); };
      } catch (e) { clearTimeout(giveUp); resolve(null); }
    }));
    const run = async (mode, fn) => {
      const db = await open();
      if (!db) return undefined;
      return new Promise(res => {
        const giveUp = setTimeout(() => res(undefined), 8000);
        const done = v => { clearTimeout(giveUp); res(v); };
        try {
          const tx = db.transaction("kv", mode);
          const req = fn(tx.objectStore("kv"));
          tx.oncomplete = () => done(req && req.result !== undefined ? req.result : undefined);
          tx.onerror = tx.onabort = () => done(undefined);
        } catch (e) { done(undefined); }
      });
    };
    return { get: k => run("readonly", s => s.get(k)), put: (k, v) => run("readwrite", s => s.put(v, k)) };
  })();

  // ═══ the library ════════════════════════════════════════════════════════════════════════════════════════════════
  async function readWithProgress(r) {
    if (!r.body || !r.body.getReader) return r.text();
    const reader = r.body.getReader(), dec = new TextDecoder();
    let got = 0, out = "", last = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      got += value.length; out += dec.decode(value, { stream: true });
      const now = performance.now();
      if (now - last > 120) { last = now; const n = $("#notice .grow"); if (n && $("#notice").dataset.key === "load") n.textContent = `Loading the library… ${C.fmtBytes(got)}`; }
    }
    return out + dec.decode();
  }

  /** The catalog: with the saved copy's ETag when this gate allows a page to ask (a 304 costs nothing), plain otherwise. */
  async function fetchCatalog(cached) {
    const url = gateUrl("catalog.json");
    let r = null, usedCond = false;
    if (cached && cached.etag && cached.cond !== false) {
      try { r = await fetch(url, { headers: { "If-None-Match": cached.etag } }); usedCond = true; }
      catch (e) { r = null; }               // a gate that does not answer the browser's preflight: fall back to a plain GET
    }
    if (!r) {
      r = await fetch(url);
      if (cached && cached.etag && cached.cond !== false && r.ok) { cached.cond = false; idb.put("catalog", cached); }
    }
    if (r.status === 304 && usedCond) return { status: 304 };
    if (!r.ok) return { status: r.status };
    return { status: 200, etag: r.headers.get("ETag") || "", text: await readWithProgress(r) };
  }

  let retryTimer = 0;
  async function loadLibrary(manual) {
    if (S.loading) return;
    S.loading = true;
    clearTimeout(retryTimer);
    try {
      let cached = await idb.get("catalog");
      if (cached && cached.text && !S.lib) {
        try { adoptCatalog(cached.text); }                  // instantly, from this device; the gate is asked about changes below
        catch (e) { cached = null; showNotice("Opening the library…", { key: "load", progress: true }); }
      } else if (!S.lib) {
        showNotice("Opening the library…", { key: "load", progress: true });
      }
      for (let attempt = 0; attempt < 8; attempt++) {
        try {
          await gate.ensure(manual || attempt > 0);
          const res = await fetchCatalog(cached);
          if (res.status === 503) {
            showNotice("skippy-pc is still reading the library's tags; trying again shortly…", { key: "load", progress: true });
            await wait(6000); continue;
          }
          if (res.status === 403 && attempt < 2) { gate.info = null; continue; }     // a pass the gate no longer takes: ask for a new one
          if (res.status === 304) { hideNotice("load"); hideNotice("gate"); return; }
          if (res.status !== 200) throw Object.assign(new Error(`The music gate said ${res.status}.`), { kind: "gate" });
          if (!cached || cached.text !== res.text) {
            adoptCatalog(res.text);
            idb.put("catalog", { text: res.text, etag: res.etag, at: Date.now(), cond: cached && cached.cond });
          } else if (res.etag && res.etag !== cached.etag) idb.put("catalog", Object.assign(cached, { etag: res.etag }));
          hideNotice("load"); hideNotice("gate");
          return;
        } catch (e) {
          if (e && e.kind === "auth") { gateProblem(e); return; }
          if (S.lib) { showNotice((e && e.kind === "home" ? e.message : GATE_DOWN) + " Showing the copy saved on this device.", { kind: "warn", key: "gate", retry: () => loadLibrary(true) }); return; }
          gateProblem(e);
          retryTimer = setTimeout(() => { if (!S.lib && !document.hidden) loadLibrary(); }, 30000);
          return;
        }
      }
      showNotice("The library is still being read. Try again in a minute.", { kind: "warn", key: "gate", retry: () => loadLibrary(true) });
    } finally { S.loading = false; }
  }

  function adoptCatalog(text) {
    const t0 = performance.now();
    const lib = C.parseCatalog(JSON.parse(text));
    if (!lib.T.length) throw new Error("empty catalog");
    const old = S.lib;
    S.lib = lib;
    if (S.lyrics) C.attachLyrics(lib, S.lyrics);
    if (old) {                                  // a newer catalog numbers the tracks differently: keep the queue by path
      S.q.remap(t => { const i = lib.byPath.get(old.T[t].p); return i === undefined ? -1 : i; });
      for (const s of slots) s.t = s.path ? (lib.byPath.has(s.path) ? lib.byPath.get(s.path) : -1) : -1;
      S.sortCache = null;
    }
    $("#q").disabled = false;
    $("#nav-added").hidden = !lib.hasAdded;
    if (!old) { restoreState(); onRoute(false); startConnect(); loadLyrics(); }
    else { renderView(true); showTrack(); onQueueChanged(); }
    if (window.console && console.debug) console.debug(`bilby: ${lib.T.length} tracks indexed in ${Math.round(performance.now() - t0)} ms`);
  }

  async function loadLyrics() {
    const cached = await idb.get("lyrics");
    if (cached && cached.by) { S.lyrics = cached.by; C.attachLyrics(S.lib, S.lyrics); showLyric(); }
    if (cached && cached.at && Date.now() - cached.at < 6 * 3600e3) return;
    try {
      await gate.ensure();
      const r = await fetch(gateUrl("lyrics.json"));
      if (!r.ok) return;                         // an older gate has none: the line stays hidden
      const data = await r.json();
      if (data && data.by && typeof data.by === "object") {
        S.lyrics = data.by; C.attachLyrics(S.lib, S.lyrics); showLyric();
        idb.put("lyrics", { by: data.by, at: Date.now() });
      }
    } catch (e) { /* no summaries today: not an error */ }
  }

  // ═══ cover art: thumbnails, never loaded for a row that is only flying past ═════════════════════════════════════
  const artWait = new Set();
  let artTimer = 0;
  const flying = () => performance.now() - S.scrollAt < 140;
  function setArt(img, path, px) {
    const url = path ? artUrl(path, px) : "";
    if (img._u === url) return;
    img._u = url;
    img.classList.remove("ok");
    if (!url) { img.removeAttribute("src"); return; }
    if (flying()) { artWait.add(img); clearTimeout(artTimer); artTimer = setTimeout(flushArt, 160); }
    else img.src = url;
  }
  function flushArt() { for (const img of artWait) if (img._u && img.getAttribute("src") !== img._u) img.src = img._u; artWait.clear(); }
  function mkCover(cls) {
    const box = el("span", "cover " + (cls || "")), img = el("img");
    img.alt = ""; img.decoding = "async";
    img.onload = () => img.classList.add("ok");
    img.onerror = () => { img.classList.remove("ok"); };
    box.innerHTML = ico("note"); box.append(img);
    return { box, img };
  }

  // ═══ the virtual list: only the rows on screen exist ════════════════════════════════════════════════════════════
  class VList {
    constructor(sc, host, makers, onScroll) {
      this.sc = sc; this.host = host; this.makers = makers; this.rows = []; this.off = C.buildOffsets([]);
      this.live = new Map(); this.pool = Object.create(null); this.raf = 0; this.over = 360;
      sc.addEventListener("scroll", () => { S.scrollAt = performance.now(); if (onScroll) onScroll(); this.schedule(); }, { passive: true });
    }
    schedule() { if (!this.raf) this.raf = requestAnimationFrame(() => { this.raf = 0; this.update(); }); }
    setRows(rows) {
      for (const e of this.live.values()) this.release(e);
      this.live.clear();
      this.rows = rows; this.off = C.buildOffsets(rows.map(r => r.h));
      this.host.style.height = this.off[rows.length] + "px";
      this.update();
    }
    update() {
      const [a, b] = C.visibleRange(this.off, this.sc.scrollTop, this.sc.clientHeight, this.over);
      for (const [i, e] of this.live) if (i < a || i >= b) { this.live.delete(i); this.release(e); }
      let added = false;
      for (let i = a; i < b; i++) {
        if (this.live.has(i)) continue;
        const row = this.rows[i], mk = this.makers[row.type];
        const pool = this.pool[row.type] || (this.pool[row.type] = []);
        let e = pool.pop();
        if (!e) { e = mk.make(); e._type = row.type; }
        e._i = i; e.style.transform = `translateY(${this.off[i]}px)`; e.style.height = row.h + "px";
        mk.bind(e, row, i);
        this.live.set(i, e); added = true;
      }
      if (added) {                              // keep the DOM in row order, so Tab walks the list top to bottom
        let ref = this.host.firstChild;
        for (let i = a; i < b; i++) { const e = this.live.get(i); if (e === ref) ref = ref.nextSibling; else this.host.insertBefore(e, ref); }
      }
    }
    release(e) { e.remove(); (this.pool[e._type] || (this.pool[e._type] = [])).push(e); }
    refresh() { for (const [i, e] of this.live) this.makers[e._type].bind(e, this.rows[i], i); }
    scrollTo(i, center) {
      if (i < 0 || i >= this.rows.length) return;
      const h = this.sc.clientHeight;
      this.sc.scrollTop = Math.max(0, this.off[i] - (center ? (h - this.rows[i].h) / 2 : 0));
    }
    el(i) { return this.live.get(i) || null; }
  }

  // ═══ the audio engine ═══════════════════════════════════════════════════════════════════════════════════════════
  // Two hidden <audio> elements: one plays, the other already has the next song loaded, so ending is a swap and not a
  // download. WebKit (every iPhone browser, Safari on the Mac) ties "may play without a tap" to the one element a tap
  // started, so there ONE element is re-sourced instead (a gap of a moment between songs, but it never goes silent).
  const slots = [$("#au0"), $("#au1")].map((e, n) => ({ el: e, n, t: -1, path: "", from: 0, logged: false, djDone: false, retried: false }));
  let ai = 0;
  const act = () => slots[ai], idle = () => slots[1 - ai];
  const isPlaying = () => { const s = act(); return !!s.path && !s.el.paused && !s.el.ended; };
  const curPos = () => { const s = act(); return s.path ? (s.el.currentTime || s.from || 0) : S.resumeAt; };
  function curDur() {
    const d = act().el.duration, t = curTrack();
    return Number.isFinite(d) && d > 0 ? d : (t ? t.s : 0);
  }
  function probeVolume() {
    try { const e = slots[0].el; e.volume = 0.5; S.canVolume = Math.abs(e.volume - 0.5) < 0.01; e.volume = 1; } catch (x) { S.canVolume = false; }
    if (env.ios) S.canVolume = false;
    root.dataset.novol = S.canVolume ? "0" : "1";
  }
  function applyVolume() {
    const g = S.sleep.active ? S.sleep.gain(Date.now(), 8000) : 1;
    for (const s of slots) {
      if (S.canVolume) s.el.volume = clamp(S.vol * S.vol * g, 0, 1);
      s.el.muted = S.muted;
    }
  }

  /** `bust`: a retry after a failure asks for the file under a new URL, so a half-downloaded copy the browser cached from the failed try is not served again. */
  function load(slot, tIdx, from, bust) {
    const t = S.lib.T[tIdx];
    slot.t = tIdx; slot.path = t.p; slot.from = from || 0; slot.logged = false; slot.djDone = false; slot.retried = false; slot.pre = false;
    S.ourPauseAt = Date.now();                              // replacing the source stops whatever played: that pause is ours
    slot.el.src = gateUrl("a", t.p, bust ? "?r=" + Date.now() : "");
  }
  function unload(slot) {
    slot.t = -1; slot.path = ""; slot.from = 0; S.ourPauseAt = Date.now();
    try { slot.el.removeAttribute("src"); slot.el.load(); } catch (e) { /* nothing was loaded */ }
  }

  /** Ask the browser to play. A refusal (NotAllowedError) is the autoplay rule, not a failure: show the one-tap bar. */
  async function tryPlay(slot) {
    slot = slot || act();
    S.want = true; S.playAt = Date.now();
    try {
      await slot.el.play();
      S.armed.played(!slot.el.muted);
      hideTap();
      return true;
    } catch (e) {
      if (e && e.name === "NotAllowedError") { S.armed.refused(); showTap(); }
      // AbortError: a newer load or a pause cut this play() short, which has its own follow-up; anything else surfaces as the element's error event
      return false;
    } finally { onPlayState(); }
  }

  /** Make queue position `pos` the song that plays. Everything before play() is synchronous when the pass is fresh, because
   *  Safari only counts a play() that is still inside the tap that asked for it. */
  function startTrack(pos, o) {
    o = o || {};
    const c = S.q.jump(pos);
    if (!c) return Promise.resolve(false);
    const tok = ++S.loadTok, tIdx = c.t;
    S.resumeAt = 0;
    showTrack(); onQueueMoved();
    const go = () => {
      if (o.play === false) { const s = act(); if (s.t !== tIdx) { load(s, tIdx, o.from); } return Promise.resolve(true); }
      const other = idle();
      if (!env.singleElement && !o.from && other.t === tIdx && other.path && other.el.getAttribute("src")) {
        const old = act(); S.ourPauseAt = Date.now(); old.el.pause(); ai = 1 - ai; unload(old);       // the song was preloaded: swap, no download
      } else load(act(), tIdx, o.from);
      applyVolume();
      const p = tryPlay(act());
      preloadNext();
      return p;
    };
    if (gate.fresh()) return go();
    return gate.ensure().then(() => (tok === S.loadTok ? go() : false), err => { gateProblem(err); return false; });
  }

  /** Where "next" goes: the queue, else a pick by Bilby DJ, else the end. */
  function advance(auto) {
    const q = S.q;
    let np = q.nextPos(S.repeat === "one" ? "off" : S.repeat);
    if (np < 0) { djPickNow(); np = q.nextPos("off"); }
    if (np < 0) { endOfQueue(auto); return Promise.resolve(false); }
    if (auto && !env.singleElement) {           // the preloaded swap, in the same breath as the old song ending
      const t = q.items[np].t, other = idle();
      if (other.t === t && other.path && other.el.getAttribute("src") && other.el.readyState >= 2 && !other.el.error) {
        q.jump(np);
        const old = act(); ai = 1 - ai; unload(old);
        S.resumeAt = 0; showTrack(); onQueueMoved(); applyVolume();
        const p = tryPlay(act());
        preloadNext();
        return p;
      }
    }
    return startTrack(np, { play: true });
  }
  /** Pause on purpose (so the pause handler can tell it from one the system did). */
  function pauseNow() { S.want = false; S.ourPauseAt = Date.now(); if (isPlaying()) act().el.pause(); }
  function endOfQueue(auto) {
    S.want = false;
    const s = act();
    if (!auto && s.path) { S.ourPauseAt = Date.now(); s.el.pause(); seekTo(0); toast("That was the end of the queue."); }
    onPlayState();
  }

  /** Have the next song loading on the idle element, so ending is a swap. Held back until the playing song has run a few
   *  seconds, so the two downloads never fight over the line while the first song is still starting. */
  function preloadNext() {
    if (env.singleElement || !S.lib) return;
    const q = S.q, other = idle();
    const np = S.repeat === "one" ? -1 : q.nextPos(S.repeat);
    if (np < 0 || np === q.qi || !gate.info) { if (other.path) unload(other); return; }
    const t = q.items[np].t;
    if (other.t === t && other.path) return;
    if (!act().pre) { if (other.path) unload(other); return; }
    load(other, t, 0);
    other.el.preload = "auto"; applyVolume();
  }

  function onEnded() {
    if (S.sleep.endOfTrack) { S.sleep.stop(); updateSleepUi(); S.want = false; onPlayState(); toast("Sleep timer: music stopped."); return; }
    const s = act();
    if (S.repeat === "one") { s.el.currentTime = 0; tryPlay(s); return; }
    advance(true);
  }

  async function onError(slot) {
    const e = slot.el;
    if (!slot.path || !e.getAttribute("src")) return;
    if (slot !== act()) { unload(slot); return; }                     // a preload that failed is forgotten; the song loads normally when its turn comes
    if (!slot.retried) {                                              // most often a pass the gate no longer takes: ask for a new one, carry on from here
      slot.retried = true;
      const at = e.currentTime || slot.from || 0, t = slot.t;
      try { await gate.ensure(true); } catch (err) { gateProblem(err); return; }
      if (slot !== act() || slot.t !== t) return;
      load(slot, t, at, true); slot.retried = true;
      if (S.want) tryPlay(slot);
      return;
    }
    S.errStreak++;
    const t = curTrack();
    if (S.errStreak >= 3) {                                           // three in a row is the connection, not the files
      S.want = false; onPlayState(); gateProblem(null); return;
    }
    toast(`Couldn't play “${t ? t.t : "that song"}”; skipping it.`);
    await wait(700);
    if (slot === act()) advance(true);
  }

  function busy(on) { for (const b of els.play) { if (on) b.dataset.busy = "1"; else delete b.dataset.busy; } }

  function wireSlot(slot) {
    const e = slot.el, mine = () => slot === act();
    e.addEventListener("loadedmetadata", () => {
      if (!mine()) return;
      if (slot.from > 0) { try { e.currentTime = slot.from; } catch (x) { /* some browsers want canplay first */ } }
      syncTime(); mediaPosition();
    });
    e.addEventListener("canplay", () => {
      if (!mine()) return;
      busy(false);
      if (slot.from > 0 && Math.abs(e.currentTime - slot.from) > 2) { try { e.currentTime = slot.from; } catch (x) { /* ignore */ } }
    });
    e.addEventListener("durationchange", () => mine() && syncTime());
    e.addEventListener("timeupdate", () => mine() && onTime(slot));
    e.addEventListener("playing", () => { if (!mine()) return; slot.from = 0; busy(false); S.errStreak = 0; hideNotice("gate"); onPlayState(); });
    e.addEventListener("play", () => mine() && onPlayState());
    e.addEventListener("pause", () => {
      if (!mine()) return;
      if (S.want && !e.ended && !e.error && Date.now() - S.ourPauseAt > 400) {
        // Nobody here asked for that pause (and the file did not just fail: the browser pauses an element whose load errored, and
        // that is the gate's trouble, not the autoplay rule's). Right after our play() it is the browser taking the sound back (some
        // resolve play() and pause it at once when the page was never tapped): ask for a tap, unless the browser itself says the page
        // HAS been tapped (navigator.userActivation: then it is not that rule). Later, it is the system (a call, headphones, another
        // app): stay paused and do not fight it.
        const tapped = !!(navigator.userActivation && navigator.userActivation.hasBeenActive);
        if (Date.now() - S.playAt < 1500 && !tapped) { S.armed.refused(); showTap(); } else S.want = false;
      }
      onPlayState();
    });
    e.addEventListener("waiting", () => mine() && busy(true));
    e.addEventListener("seeked", () => { if (mine()) { syncTime(); mediaPosition(); } });
    e.addEventListener("progress", () => mine() && syncBuffered());
    e.addEventListener("ended", () => mine() && onEnded());
    e.addEventListener("error", () => onError(slot));
  }

  function onTime(slot) {
    syncTime();
    const dur = curDur(), pos = slot.el.currentTime;
    if (!slot.logged && pos >= Math.min(8, (dur || 16) / 2)) { slot.logged = true; logPlay(slot.path); }
    if (!slot.pre && (pos >= 6 || (dur && dur - pos <= 40))) { slot.pre = true; preloadNext(); }
    if (!slot.djDone && dur && dur - pos <= 30) { slot.djDone = true; if (djNeeded()) djPickNow(); }
    if (Date.now() - S.lastPersist > 10000) persist();
  }

  // ═══ Bilby DJ ═══════════════════════════════════════════════════════════════════════════════════════════════════
  const djNeeded = () => S.dj && S.repeat === "off" && !!S.q.cur && S.q.nextPos("off") < 0;
  /** The pick is made when the song is ~30 s from its end, so it is already in the queue (and preloaded) when it is needed. */
  function djPickNow() {
    if (!S.lib || !djNeeded()) return;
    const cur = S.q.cur.t;
    const pick = C.djPick(S.lib, cur, C.recentSet(S.lib, S.hist, cur, C.DJ_AVOID_LAST));
    if (pick < 0) return;
    S.q.addAuto(pick);
    onQueueChanged();
  }
  function djStart() {
    if (!S.lib) return Promise.resolve(false);
    S.dj = true; savePrefs(); syncToggles();
    const pick = C.djPick(S.lib, -1, C.recentSet(S.lib, S.hist, -1, C.DJ_AVOID_LAST));
    if (pick < 0) return Promise.resolve(false);
    S.q.load([pick], 0, false);
    onQueueChanged();
    return startTrack(0, { play: true });
  }

  // ═══ history (what was played: this browser only) ═══════════════════════════════════════════════════════════════
  function logPlay(path) {
    if (!path) return;
    S.hist.push(path);
    if (S.hist.length > 300) S.hist = S.hist.slice(-200);
    store.set("hist", S.hist);
    if (S.route.name === "played" || S.route.name === "home") scheduleView();
  }

  // ═══ actions: the one set of verbs the buttons, keys, lock screen and a remote device all use ═══════════════════
  function seekTo(sec) {
    const s = act();
    sec = Math.max(0, sec || 0);
    if (!s.path) { S.resumeAt = sec; syncTime(); return; }
    const d = curDur();
    if (s.el.readyState === 0) s.from = sec;
    try { s.el.currentTime = d ? Math.min(sec, d) : sec; } catch (e) { s.from = sec; }
    syncTime();
  }
  function playList(list, pos) {
    S.q.load(list, pos, S.shuffle); onQueueChanged();
    return startTrack(S.q.qi, { play: true });
  }
  function shuffleList(list) {
    if (!list.length) return;
    S.shuffle = true; savePrefs(); syncToggles();
    S.q.load(list, Math.floor(Math.random() * list.length), true); onQueueChanged();
    return startTrack(0, { play: true });
  }
  function queueMany(list, next, what) {
    if (!list.length) return;
    const was = !S.q.cur;
    if (next) S.q.playNextMany(list); else S.q.addMany(list);
    onQueueChanged();
    if (was) { showTrack(); onQueueMoved(); }
    toast(`${next ? "Playing next" : "Added to the queue"}: ${what || (list.length === 1 ? S.lib.T[list[0]].t : list.length + " songs")}`);
  }
  const actions = {
    toggle() {
      if (!S.q.cur) return djStart();
      const s = act();
      if (!s.path || s.t !== S.q.cur.t) return startTrack(S.q.qi, { from: S.resumeAt, play: true });
      if (s.el.paused || s.el.ended) { if (s.el.ended) s.el.currentTime = 0; return tryPlay(s); }
      pauseNow();
    },
    resume() { if (!isPlaying()) return actions.toggle(); },
    pause() { pauseNow(); onPlayState(); },
    next() { if (!S.q.cur) return djStart(); return advance(false); },
    prev() {
      if (!S.q.cur) return;
      if (curPos() > 3 || S.q.qi <= 0) { seekTo(0); return; }
      return startTrack(S.q.qi - 1, { play: true });
    },
    seekBy(d) { seekTo(clamp(curPos() + d, 0, curDur() || 1e9)); },
    seekTo,
    shuffle() {
      S.shuffle = !S.shuffle;
      if (S.shuffle) S.q.shuffleUpcoming(); else S.q.unshuffle();
      savePrefs(); syncToggles(); onQueueChanged();
      toast(S.shuffle ? "Shuffle on" : "Shuffle off", { ms: 1500 });
    },
    repeat() {
      S.repeat = S.repeat === "off" ? "all" : S.repeat === "all" ? "one" : "off";
      savePrefs(); syncToggles(); preloadNext();
      toast(`Repeat: ${S.repeat}`, { ms: 1500 });
    },
    dj() {
      S.dj = !S.dj; savePrefs(); syncToggles();
      if (S.dj) { act().djDone = false; if (djNeeded() && curDur() - curPos() <= 30) djPickNow(); }
      else if (S.q.autoLast()) { S.q.remove(S.q.length - 1); onQueueChanged(); }          // a pick nobody wanted: withdraw it
      toast(S.dj ? "Bilby DJ on: Bilby keeps the music going" : "Bilby DJ off", { ms: 1800 });
    },
    mute() { S.muted = !S.muted; applyVolume(); syncToggles(); savePrefs(); },
    volume(v) { S.vol = clamp(v, 0, 1); if (S.vol > 0 && S.muted) S.muted = false; applyVolume(); syncToggles(); savePrefs(); },
    volBy(d) { actions.volume(Math.round((S.vol + d) * 100) / 100); },
    djstart: djStart,
  };

  // ═══ the autoplay bar ═══════════════════════════════════════════════════════════════════════════════════════════
  function showTap(why) {
    if (why) S.tapWhy = why;
    const t = curTrack(), bar = $("#tapbar"), label = $("#tap-label"), text = $("#tap-why");
    if (S.tapWhy === "restore" && t) {
      label.textContent = "Tap to continue";
      text.replaceChildren("Bilby will carry on with ", el("b", "", t.t), ` at ${C.fmtTime(curPos())}. Browsers only let a page make sound after you tap it, and a reload or an update starts that over, so one tap is expected.`);
    } else if (S.tapWhy === "remote") {
      label.textContent = "Tap to start the sound";
      text.textContent = "Another device asked this one to play, but browsers only let a page make sound after you tap it. Tap once and it starts, and from then on it takes your remote's commands without more taps.";
    } else {
      label.textContent = "Tap to start the sound";
      text.textContent = "Your browser won't play sound until you tap the page. Tap once and the music carries on from where it was; after a reload you'll need one tap again.";
    }
    bar.hidden = false;
    publishSoon();
  }
  function hideTap() { $("#tapbar").hidden = true; S.tapWhy = ""; }

  // ═══ persistence: the queue survives a reload (a window around where we are, never all 21,000) ══════════════════
  function persist() {
    S.lastPersist = Date.now();
    if (!S.lib || !S.q.cur) return;
    const w = S.q.window(50, 300);
    store.set("state", { v: 1, paths: w.items.map(it => S.lib.T[it.t].p), qi: w.qi, pos: Math.round(curPos() * 10) / 10,
                         repeat: S.repeat, shuffle: S.shuffle, dj: S.dj, playing: isPlaying() || S.want || S.armed.tap, at: Date.now() });
  }
  let persistTimer = 0;
  const persistSoon = () => { clearTimeout(persistTimer); persistTimer = setTimeout(persist, 800); };

  /** After a reload: put the queue back, paused. Nothing plays without a tap, so if it WAS playing the one-tap bar says so. */
  function restoreState() {
    const st = store.get("state", null);
    if (!st || !Array.isArray(st.paths) || !st.paths.length) return;
    const list = [];
    let at = -1;
    st.paths.forEach((p, k) => { const i = S.lib.byPath.get(p); if (i !== undefined) { if (k === st.qi) at = list.length; list.push(i); } });
    if (!list.length) return;
    if (at < 0) at = Math.min(st.qi | 0, list.length - 1);
    S.q.load(list, at, false);
    S.resumeAt = +st.pos || 0;
    S.dj = st.dj !== false; S.shuffle = !!st.shuffle; if (["off", "all", "one"].includes(st.repeat)) S.repeat = st.repeat;
    syncToggles(); onQueueChanged(); showTrack(); syncTime();
    if (st.playing && Date.now() - (st.at || 0) < 12 * 3600e3) { S.armed.waiting(); showTap("restore"); }
  }

  // ═══ what the player shows ══════════════════════════════════════════════════════════════════════════════════════
  const els = { play: [], seek: [], cur: [], rem: [], vol: [], shuffle: [], repeat: [], dj: [], mute: [], sleep: [], devices: [] };

  const TP = `
    <button type="button" class="ib" data-act="shuffle" aria-pressed="false" aria-label="Shuffle" title="Shuffle (S)"><i data-i="shuffle"></i></button>
    <button type="button" class="ib" data-act="prev" aria-label="Previous" title="Previous (Shift+Left)"><i data-i="prev"></i></button>
    <button type="button" class="ib big" data-act="play" aria-label="Play" title="Play (Space)"><i data-i="play"></i></button>
    <button type="button" class="ib" data-act="next" aria-label="Next" title="Next (Shift+Right)"><i data-i="next"></i></button>
    <button type="button" class="ib" data-act="repeat" aria-pressed="false" aria-label="Repeat: off" title="Repeat (R)"><i data-i="repeat"></i></button>`;
  const SK = `
    <span class="t" data-bind="cur">0:00</span>
    <input type="range" class="rg" data-seek min="0" max="0" step="1" value="0" aria-label="Position" disabled>
    <span class="t" data-bind="rem">0:00</span>`;
  const RIGHT = `
    <div class="volbox"><button type="button" class="ib" data-act="mute" aria-label="Mute" title="Mute (M)"><i data-i="vol"></i></button>
      <input type="range" class="rg vol" data-vol min="0" max="100" step="1" value="100" aria-label="Volume"></div>
    <button type="button" class="ib" data-act="sleep" aria-label="Sleep timer" title="Sleep timer"><i data-i="sleep"></i><sup data-bind="sleep"></sup></button>
    <button type="button" class="ib" data-act="devices" aria-label="Other devices" title="Other devices (Connect)"><i data-i="devices"></i><sup data-bind="dev"></sup></button>
    <button type="button" class="ib" data-act="queue" aria-label="Queue" title="Queue"><i data-i="queue"></i></button>
    <button type="button" class="ib" data-act="dj" aria-pressed="true" aria-label="Bilby DJ" title="Bilby DJ keeps the music going when the queue runs out"><i data-i="dj"></i></button>`;
  function buildSlots() {
    for (const s of $$('[data-slot="tp"]')) s.innerHTML = TP;
    for (const s of $$('[data-slot="sk"]')) s.innerHTML = SK;
    for (const s of $$('[data-slot="right"], [data-slot="sub"]')) s.innerHTML = RIGHT;
    fillIcons();
    els.play = $$('[data-act="play"]'); els.seek = $$("[data-seek]"); els.cur = $$('[data-bind="cur"]'); els.rem = $$('[data-bind="rem"]');
    els.vol = $$("[data-vol]"); els.shuffle = $$('[data-act="shuffle"]'); els.repeat = $$('[data-act="repeat"]');
    els.dj = $$('[data-act="dj"]'); els.mute = $$('[data-act="mute"]'); els.sleep = $$('[data-bind="sleep"]'); els.devices = $$('[data-bind="dev"]');
  }

  function syncPlay() {
    const playing = isPlaying();
    root.dataset.playing = playing ? "1" : "0";
    for (const b of els.play) {
      setIcon(b, playing ? "pause" : "play");
      b.setAttribute("aria-label", playing ? "Pause" : "Play");
      b.title = (playing ? "Pause" : "Play") + " (Space)";
      b.disabled = false;
    }
  }
  function syncToggles() {
    for (const b of els.shuffle) b.setAttribute("aria-pressed", S.shuffle);
    for (const b of els.repeat) {
      b.setAttribute("aria-pressed", S.repeat !== "off");
      b.setAttribute("aria-label", `Repeat: ${S.repeat}`);
      setIcon(b, S.repeat === "one" ? "repeat1" : "repeat");
    }
    for (const b of els.dj) b.setAttribute("aria-pressed", S.dj);
    for (const b of els.mute) { const off = S.muted || S.vol === 0; setIcon(b, off ? "mute" : "vol"); b.setAttribute("aria-label", off ? "Unmute" : "Mute"); }
    for (const r of els.vol) { r.value = Math.round((S.muted ? 0 : S.vol) * 100); r.style.setProperty("--p", r.value + "%"); r.style.setProperty("--b", r.value + "%"); }
  }
  function onPlayState() {
    syncPlay(); mediaState(); persistSoon(); publishSoon();
  }

  /** Elapsed and remaining, the position sliders and the thin line on the mini player. */
  function syncTime() {
    const pos = curPos(), dur = curDur(), has = !!S.q.cur;
    const pct = dur ? clamp(pos / dur, 0, 1) * 100 : 0;
    $("#b-prog").style.width = pct + "%";
    if (S.seeking) return;
    for (const r of els.seek) {
      r.disabled = !has; r.max = Math.max(1, Math.floor(dur)); r.value = Math.floor(pos);
      r.style.setProperty("--p", pct + "%");
      r.setAttribute("aria-valuetext", `${C.fmtTime(pos)} of ${C.fmtTime(dur)}`);
    }
    for (const n of els.cur) n.textContent = C.fmtTime(pos);
    for (const n of els.rem) n.textContent = dur ? "-" + C.fmtTime(Math.max(0, dur - pos)) : "0:00";
    syncBuffered();
  }
  function syncBuffered() {
    const e = act().el, dur = curDur();
    if (!dur || !e.buffered || !e.buffered.length) return;
    let end = 0;
    for (let i = 0; i < e.buffered.length; i++) if (e.buffered.start(i) <= e.currentTime + 0.5) end = Math.max(end, e.buffered.end(i));
    const pct = clamp(end / dur, 0, 1) * 100;
    for (const r of els.seek) r.style.setProperty("--b", pct + "%");
  }

  function setCover(box, path, px) {
    const img = box.querySelector("img");
    if (img) setArt(img, path, px);
  }

  /** The song that is now current: the bar, Now Playing, the lock screen, the tab title and the screen reader. */
  function showTrack() {
    const t = curTrack(), has = !!t;
    root.dataset.has = has ? "1" : "0";
    $("#b-title").textContent = has ? t.t : "Nothing playing";
    $("#b-sub").textContent = has ? [t.a, t.al].filter(Boolean).join(" · ") : "Pick something, or let Bilby DJ";
    setCover($("#b-cover"), has ? t.ap : "", 48);
    const npc = $("#np-cover");
    setCover(npc, has ? t.ap : "", Math.max(npc.clientWidth || 0, 300));
    $("#np-title").textContent = has ? t.t : "Nothing playing";
    const by = $("#np-by");
    by.replaceChildren();
    if (has) {
      const a = el("button", "", t.a); a.type = "button"; a.dataset.act = "open-artist"; by.append(a);
      if (t.al) { const b = el("button", "", t.al); b.type = "button"; b.dataset.act = "open-album"; by.append(" · ", b); }
    }
    $("#np-tags").textContent = has ? [t.y ? String(t.y) : "", t.g].filter(Boolean).join(" · ") : "";
    $("#np-empty").hidden = has;
    showLyric();
    renderUpNext();
    document.title = has ? `${t.t} · ${t.a} — ${TITLE}` : `${TITLE} — Maker Portal`;
    if (has) announce(`${t.t}, by ${t.a}`);
    mediaMeta();
    if (list) list.refresh();
    if (qList) qList.refresh();
    syncTime(); syncPlay();
  }
  /** What the song is about, one sentence, from the Mac's lyric index. Hidden when there is none. */
  function showLyric() {
    const t = curTrack(), p = $("#np-lyric");
    if (t && t.ly) { p.replaceChildren(el("span", "sr", "About this song: "), t.ly); p.hidden = false; } else p.hidden = true;
  }
  function renderUpNext() {
    const ol = $("#np-up"), up = S.q.upcoming().slice(0, 5);
    ol.replaceChildren(...up.map((it, k) => {
      const t = S.lib.T[it.t], li = el("li");
      li.append(el("span", "l1", t.t), el("span", "l2", t.a));
      if (S.q.isAuto(S.q.qi + 1 + k)) li.append(el("span", "auto", "Bilby's pick"));
      return li;
    }));
    $("#np-up").parentElement.hidden = !up.length;
  }

  // ═══ the lock screen, the car, media keys ═══════════════════════════════════════════════════════════════════════
  const ms = "mediaSession" in navigator ? navigator.mediaSession : null;
  function initMediaSession() {
    if (!ms) return;
    const on = (a, f) => { try { ms.setActionHandler(a, f); } catch (e) { /* this browser lacks that action */ } };
    on("play", () => actions.resume()); on("pause", () => actions.pause());
    on("previoustrack", () => actions.prev()); on("nexttrack", () => actions.next());
    on("seekto", d => actions.seekTo(d.seekTime));
    on("seekbackward", d => actions.seekBy(-(d.seekOffset || 10))); on("seekforward", d => actions.seekBy(d.seekOffset || 10));
    on("stop", () => { actions.pause(); actions.seekTo(0); });
  }
  function mediaMeta() {
    if (!ms) return;
    const t = curTrack();
    if (!t) { ms.metadata = null; return; }
    try {
      ms.metadata = new MediaMetadata({
        title: t.t, artist: t.a, album: t.al || "",
        artwork: t.ap && gate.info ? C.ART_WIDTHS.map(w => ({ src: artUrlW(t.ap, w), sizes: `${w}x${w}`, type: "image/jpeg" })) : [],
      });
    } catch (e) { /* an old MediaMetadata: skip the lock-screen text */ }
  }
  function mediaState() { if (ms) { try { ms.playbackState = !S.q.cur ? "none" : isPlaying() ? "playing" : "paused"; } catch (e) { /* ignore */ } } }
  function mediaPosition() {
    if (!ms || !ms.setPositionState) return;
    const d = curDur();
    if (!d || !Number.isFinite(d)) return;
    try { ms.setPositionState({ duration: d, position: clamp(curPos(), 0, d), playbackRate: 1 }); } catch (e) { /* out-of-range while loading */ }
  }

  // ═══ views ══════════════════════════════════════════════════════════════════════════════════════════════════════
  const list = new VList($("#scroll"), $("#host"), {}, () => { if (rail.on) rail.hideBubble(); });
  const qList = new VList($("#q-scroll"), $("#q-host"), {});
  const VIEWS = ["home", "artists", "albums", "tracks", "genres", "added", "played", "artist", "album", "genre"];
  const TITLES = { home: "Home", artists: "Artists", albums: "Albums", tracks: "Songs", genres: "Genres", added: "Recently added", played: "Recently played" };

  function geometry() {
    const cs = getComputedStyle(root), sc = $("#scroll");
    const num = (n, d) => parseFloat(cs.getPropertyValue(n)) || d;
    const pad = num("--pad", 12), gap = num("--gap", 12), min = num("--card-min", 140), rowH = num("--row-h", 56);
    const W = sc.clientWidth || 360, rail = railOn ? 22 : 0;
    const inner = Math.max(200, W - pad * 2 - rail);
    const cols = clamp(Math.floor((inner + gap) / (min + gap)), 2, 8);
    const cw = (inner - gap * (cols - 1)) / cols;
    return { W, pad, gap, rowH, cols, cw, cardH: Math.round(cw + 62), heroH: W < 560 ? 200 : 168 };
  }
  let railOn = false;

  // — row makers —
  const makers = list.makers;
  const nameOf = al => al.name;
  const trackSub = (t, o) => o.album ? [t.a, t.al].filter(Boolean).join(" · ") : t.a;

  makers.title = {
    make() { const e = el("div", "vrow vtitle"); e._h = el("h2"); e._c = el("span", "count"); e.append(e._h, e._c); return e; },
    bind(e, r) { e._h.textContent = r.text; e._c.textContent = r.count || ""; },
  };
  makers.head = {
    make() { const e = el("div", "vrow vhead"); e._h = el("h3"); e._b = el("button", "link"); e._b.type = "button"; e._b.dataset.r = "link"; e.append(e._h, e._b); return e; },
    bind(e, r) { e._h.textContent = r.text; e._b.hidden = !r.link; e._b.textContent = r.link ? r.link.label : ""; },
  };
  makers.msg = { make() { return el("div", "vrow vmsg"); }, bind(e, r) { e.textContent = r.text; } };
  makers.disc = { make() { return el("div", "vrow vdisc"); }, bind(e, r) { e.textContent = r.text; } };
  makers.stats = { make() { return el("div", "vrow vstats"); }, bind(e, r) { e.textContent = r.text; } };

  makers.track = {
    make() {
      const e = el("div", "vrow tr"); e.setAttribute("role", "listitem");
      const main = el("button", "tr-main"); main.type = "button"; main.dataset.r = "play";
      const num = el("span", "num"), cv = mkCover("");
      const txt = el("span", "txt"), l1 = el("span", "l1"), eq = el("span", "eq"), l1t = el("span"), l2 = el("span", "l2"), dur = el("span", "dur");
      eq.setAttribute("aria-hidden", "true"); eq.innerHTML = "<span></span><span></span><span></span>";
      l1.append(eq, l1t); txt.append(l1, l2);
      main.append(num, cv.box, txt, dur);
      const more = el("button", "ib tr-more"); more.type = "button"; more.dataset.r = "more"; more.innerHTML = ico("more");
      e.append(main, more);
      e._r = { main, num, cv, l1t, l2, dur, more, eq };
      return e;
    },
    bind(e, r) {
      const t = S.lib.T[r.t], x = e._r, cur = !!S.q.cur && S.q.cur.t === r.t;
      e.classList.toggle("cur", cur);
      x.num.hidden = !r.num; x.cv.box.hidden = !!r.num;
      if (r.num) x.num.textContent = t.n ? String(t.n) : "";
      else setArt(x.cv.img, t.ap, 40);
      x.l1t.textContent = t.t;
      // the second line: artist · album by default; just the album on an artist's own page; just the artist (when it is not the
      // album's own artist) on an album page
      x.l2.textContent = r.sub === "album" ? (t.al || SINGLES) : r.num ? (t.a === r.owner ? "" : t.a) : [t.a, t.al].filter(Boolean).join(" · ");
      x.dur.textContent = t.s ? C.fmtTime(t.s) : "";
      x.more.setAttribute("aria-label", `More options for ${t.t}`);
      x.main.setAttribute("aria-label", `Play ${t.t} by ${t.a}`);
    },
  };

  makers.artist = {
    make() {
      const e = el("div", "vrow nv"); e.setAttribute("role", "listitem");
      const main = el("button", "nv-main"); main.type = "button"; main.dataset.r = "open";
      const cv = mkCover("round"), txt = el("span", "txt"), l1 = el("span", "l1"), l2 = el("span", "l2");
      txt.append(l1, l2); main.append(cv.box, txt);
      const more = el("button", "ib tr-more"); more.type = "button"; more.dataset.r = "more"; more.innerHTML = ico("more");
      e.append(main, more); e._r = { cv, l1, l2, more }; return e;
    },
    bind(e, r) {
      const a = r.ar, x = e._r;
      setArt(x.cv.img, a.artPath, 44);
      x.l1.textContent = a.name;
      x.l2.textContent = `${a.albums.length} album${a.albums.length === 1 ? "" : "s"} · ${a.tracks.length.toLocaleString()} song${a.tracks.length === 1 ? "" : "s"}`;
      x.more.setAttribute("aria-label", `More options for ${a.name}`);
    },
  };
  makers.albumrow = {
    make() {
      const e = el("div", "vrow nv"); e.setAttribute("role", "listitem");
      const main = el("button", "nv-main"); main.type = "button"; main.dataset.r = "open";
      const cv = mkCover(""), txt = el("span", "txt"), l1 = el("span", "l1"), l2 = el("span", "l2");
      txt.append(l1, l2); main.append(cv.box, txt);
      const more = el("button", "ib tr-more"); more.type = "button"; more.dataset.r = "more"; more.innerHTML = ico("more");
      e.append(main, more); e._r = { cv, l1, l2, more }; return e;
    },
    bind(e, r) {
      const a = r.al, x = e._r;
      setArt(x.cv.img, a.artPath, 44);
      x.l1.textContent = a.name;
      x.l2.textContent = [a.artist, a.year || ""].filter(Boolean).join(" · ");
      x.more.setAttribute("aria-label", `More options for ${a.name}`);
    },
  };
  makers.genre = {
    make() {
      const e = el("div", "vrow nv"); e.setAttribute("role", "listitem");
      const main = el("button", "nv-main"); main.type = "button"; main.dataset.r = "open";
      const txt = el("span", "txt"), l1 = el("span", "l1"), l2 = el("span", "l2"), chev = el("span", "chev"); chev.innerHTML = ico("chev");
      txt.append(l1, l2); main.append(txt, chev);
      e.append(main); e._r = { l1, l2 }; return e;
    },
    bind(e, r) { e._r.l1.textContent = r.g.name; e._r.l2.textContent = `${r.g.tracks.length.toLocaleString()} songs`; },
  };

  // a grid row of album cards
  makers.cards = {
    make() {
      const e = el("div", "vrow cards"); e._cards = [];
      for (let k = 0; k < 8; k++) {
        const c = el("div", "card-a"), open = el("button", "open"); open.type = "button"; open.dataset.r = "open"; open.dataset.k = k;
        const cv = mkCover(""), t1 = el("span", "t1"), t2 = el("span", "t2");
        open.append(cv.box, t1, t2);
        const go = el("button", "go"); go.type = "button"; go.dataset.r = "go"; go.dataset.k = k; go.innerHTML = ico("play");
        c.append(open, go); e.append(c); e._cards.push({ c, cv, t1, t2, go, open });
      }
      return e;
    },
    bind(e, r) {
      e.style.setProperty("--cols", r.cols); e.style.setProperty("--cw", r.cw + "px");
      e._cards.forEach((x, k) => {
        const a = r.albums[k];
        x.c.hidden = !a;
        if (!a) return;
        setArt(x.cv.img, a.artPath, r.cw);
        x.t1.textContent = a.name;
        x.t2.textContent = r.sub === "added" ? `${a.artist} · ${C.fmtAgo(a.added, Date.now() / 1000)}` : [a.artist, a.year || ""].filter(Boolean).join(" · ");
        x.go.setAttribute("aria-label", `Play ${a.name}`); x.open.setAttribute("aria-label", `${a.name} by ${a.artist}`);
      });
    },
  };

  // the header of an album, artist or genre page
  makers.hero = {
    make() {
      const e = el("div", "vrow hero"), cv = mkCover("");
      const txt = el("div", "txt"), kind = el("span", "kind"), h = el("h2"), who = el("span", "who"), meta = el("span", "meta"), acts = el("div", "acts");
      const mkb = (r, label, icon, cls) => { const b = el("button", "pill-btn " + (cls || "")); b.type = "button"; b.dataset.r = r; b.innerHTML = ico(icon); b.append(label); return b; };
      const play = mkb("hero-play", "Play", "play", "primary"), shuf = mkb("hero-shuffle", "Shuffle", "shuffle"), add = mkb("hero-add", "Add to queue", "addq");
      acts.append(play, shuf, add);
      txt.append(kind, h, who, meta, acts); e.append(cv.box, txt);
      e._r = { cv, kind, h, who, meta };
      return e;
    },
    bind(e, r) {
      const x = e._r;
      x.cv.box.classList.toggle("round", r.kind === "artist");
      setArt(x.cv.img, r.art, 124);
      x.kind.textContent = r.kind === "album" ? "Album" : r.kind === "artist" ? "Artist" : "Genre";
      x.h.textContent = r.title;
      x.who.replaceChildren();
      if (r.kind === "album") { const b = el("button", "", r.sub); b.type = "button"; b.dataset.r = "hero-artist"; x.who.append(b); }
      x.meta.textContent = r.meta;
    },
  };

  makers.homeacts = {
    make() {
      const e = el("div", "vrow homeacts");
      const a = el("button", "pill-btn primary"); a.type = "button"; a.dataset.r = "dj"; a.innerHTML = ico("dj"); a.append("Bilby, play something");
      const b = el("button", "pill-btn"); b.type = "button"; b.dataset.r = "shuffle-all"; b.innerHTML = ico("shuffle"); b.append("Shuffle everything");
      e.append(a, b); return e;
    },
    bind() { /* static */ },
  };
  makers.here = {
    make() {
      const e = el("div", "vrow here"), box = el("div", "box"), cv = mkCover(""), txt = el("div", "txt"), b = el("button", "pill-btn primary");
      b.type = "button"; b.dataset.r = "take"; b.innerHTML = ico("play"); b.append("Play here");
      box.append(cv.box, txt, b); e.append(box); e._r = { cv, txt }; return e;
    },
    bind(e, r) {
      const d = r.dev, s = d.state, x = e._r, i = S.lib.byPath.get(s.track);
      setArt(x.cv.img, i !== undefined ? S.lib.T[i].ap : "", 48);
      x.txt.replaceChildren(el("b", "", `${s.title || "Something"} — ${s.artist || ""}`), `${d.live && s.playing ? "Playing now on" : "Where you left off on"} ${d.name}, at ${C.fmtTime(s.pos)}`);
    },
  };

  // — building a view —
  const trackRows = (idx, g, o) => idx.map((t, pos) => Object.assign({ type: "track", h: g.rowH, t, list: idx, pos }, o || {}));
  const cardRows = (albums, g, sub) => C.chunk(albums, g.cols).map(a => ({ type: "cards", h: g.cardH + g.gap, albums: a, cols: g.cols, cw: g.cw, sub }));
  const albumOf = t => S.lib.albumByKey.get(t.a + "\u0000" + t.alk);
  const sortedAlbums = mode => {
    const key = "albums:" + mode;
    S.sortCache = S.sortCache || {};
    if (S.sortCache[key]) return S.sortCache[key];
    const A = S.lib.albums.slice(), ak = a => C.sortKey(a.artist);
    const by = { title: (a, b) => C.cmp(a.key2, b.key2) || C.cmp(ak(a), ak(b)), artist: (a, b) => C.cmp(ak(a), ak(b)) || (a.year || 9999) - (b.year || 9999) || C.cmp(a.key2, b.key2),
                 year: (a, b) => (b.year || 0) - (a.year || 0) || C.cmp(a.key2, b.key2), newest: (a, b) => b.added - a.added || a.id - b.id };
    return (S.sortCache[key] = A.sort(by[mode] || by.title));
  };
  const sortedTracks = mode => {
    const key = "tracks:" + mode;
    S.sortCache = S.sortCache || {};
    return S.sortCache[key] || (S.sortCache[key] = C.sortTracks(S.lib, mode));
  };
  function recentAlbums(limit) {
    const seen = new Set(), out = [];
    for (let k = S.hist.length - 1; k >= 0 && out.length < limit; k--) {
      const i = S.lib.byPath.get(S.hist[k]); if (i === undefined) continue;
      const a = albumOf(S.lib.T[i]); if (a && !seen.has(a.id)) { seen.add(a.id); out.push(a); }
    }
    return out;
  }
  function recentTracks(limit) {
    const seen = new Set(), out = [];
    for (let k = S.hist.length - 1; k >= 0 && out.length < limit; k--) {
      const i = S.lib.byPath.get(S.hist[k]); if (i !== undefined && !seen.has(i)) { seen.add(i); out.push(i); }
    }
    return out;
  }
  const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;

  /** The A-Z rail: the first row of each letter, from the sort key of each item. */
  function railFor(items, keyOf, rowOf) {
    const seen = new Set(), out = [];
    items.forEach((it, k) => { const l = C.letterOf(keyOf(it)); if (!seen.has(l)) { seen.add(l); out.push({ l, i: rowOf(k) }); } });
    return out;
  }

  function buildRoute(route) {
    const lib = S.lib, g = geometry(), rows = [];
    const title = (text, count) => rows.push({ type: "title", h: 60, text, count });
    const head = (text, link) => rows.push({ type: "head", h: 42, text, link });
    const v = { rows, key: route.name + (route.id !== undefined ? "/" + route.id : ""), rail: null, sort: null, back: false };
    const heroH = g.heroH;
    switch (route.name) {
      case "artists": {
        const mode = S.sorts.artists;
        const A = mode === "songs" ? lib.artists.slice().sort((a, b) => b.tracks.length - a.tracks.length || a.id - b.id) : lib.artists;
        title("Artists", plural(A.length, "artist"));
        const base = rows.length;
        A.forEach(ar => rows.push({ type: "artist", h: g.rowH + 4, ar }));
        if (mode === "name") v.rail = railFor(A, a => a.key, k => base + k);
        v.sort = { id: "artists", value: mode, options: [["name", "A to Z"], ["songs", "Most songs"]] };
        break;
      }
      case "albums": {
        const mode = S.sorts.albums, A = sortedAlbums(mode);
        title("Albums", plural(A.length, "album"));
        const base = rows.length;
        rows.push(...cardRows(A, g, mode === "newest" ? "added" : ""));
        if (mode === "title" || mode === "artist") v.rail = railFor(A, a => (mode === "title" ? a.key2 : C.sortKey(a.artist)), k => base + Math.floor(k / g.cols));
        const opts = [["title", "Title"], ["artist", "Artist"]];
        if (lib.hasYear) opts.push(["year", "Newest release"]);
        if (lib.hasAdded) opts.push(["newest", "Recently added"]);
        v.sort = { id: "albums", value: mode, options: opts };
        break;
      }
      case "tracks": {
        let mode = S.sorts.tracks;
        if ((mode === "newest" && !lib.hasAdded) || (mode === "year" && !lib.hasYear)) mode = "title";
        const idx = sortedTracks(mode);
        title("Songs", plural(idx.length, "song"));
        const base = rows.length, rh = g.rowH;
        for (let k = 0; k < idx.length; k++) rows.push({ type: "track", h: rh, t: idx[k], list: idx, pos: k });
        if (mode === "title" || mode === "artist" || mode === "album") {
          v.rail = railFor(idx, t => C.sortKey(mode === "title" ? lib.T[t].t : lib.T[t].a), k => base + k);
        }
        const opts = [["title", "Title"], ["artist", "Artist"], ["album", "Album"], ["length", "Longest"]];
        if (lib.hasYear) opts.push(["year", "Newest release"]);
        if (lib.hasAdded) opts.push(["newest", "Recently added"]);
        v.sort = { id: "tracks", value: mode, options: opts };
        break;
      }
      case "genres": {
        title("Genres", plural(lib.genres.length, "genre"));
        if (!lib.genres.length) rows.push({ type: "msg", h: 90, text: "None of these songs has a genre tag." });
        lib.genres.forEach(gn => rows.push({ type: "genre", h: g.rowH, g: gn }));
        break;
      }
      case "added": {
        title("Recently added");
        if (!lib.hasAdded) { rows.push({ type: "msg", h: 110, text: "The music gate hasn't said when songs were added yet. Once it is updated, the newest albums show up here." }); break; }
        const A = sortedAlbums("newest").slice(0, 120);
        rows.push(...cardRows(A, g, "added"));
        break;
      }
      case "played": {
        const idx = recentTracks(150);
        title("Recently played", idx.length ? plural(idx.length, "song") : "");
        if (!idx.length) rows.push({ type: "msg", h: 110, text: "Nothing played on this device yet. What you play shows up here (it stays in this browser)." });
        rows.push(...trackRows(idx, g));
        break;
      }
      case "artist": {
        const ar = lib.artists[route.id];
        if (!ar) { rows.push({ type: "msg", h: 110, text: "That artist isn't in the library any more." }); v.back = true; break; }
        v.back = true;
        const all = ar.albums.flatMap(a => a.tracks);
        rows.push({ type: "hero", h: heroH, kind: "artist", title: ar.name, sub: "", meta: `${plural(ar.albums.length, "album")} · ${plural(ar.tracks.length, "song")} · ${C.fmtLong(ar.dur)}`, art: ar.artPath, list: all, obj: ar });
        head("Albums");
        rows.push(...cardRows(ar.albums, g, ""));
        head(`All songs · ${ar.tracks.length.toLocaleString()}`);
        rows.push(...trackRows(all, g, { sub: "album" }));
        break;
      }
      case "album": {
        const al = lib.albums[route.id];
        if (!al) { rows.push({ type: "msg", h: 110, text: "That album isn't in the library any more." }); v.back = true; break; }
        v.back = true;
        rows.push({ type: "hero", h: heroH, kind: "album", title: al.name, sub: al.artist, meta: [al.year || "", plural(al.tracks.length, "song"), C.fmtLong(al.dur)].filter(Boolean).join(" · "), art: al.artPath, list: al.tracks, obj: al });
        let disc = 0;
        al.tracks.forEach((t, pos) => {
          const d = lib.T[t].d;
          if (al.discs > 1 && d !== disc) { disc = d; rows.push({ type: "disc", h: 34, text: `Disc ${d || 1}` }); }
          rows.push({ type: "track", h: g.rowH, t, list: al.tracks, pos, num: true, owner: al.artist });
        });
        break;
      }
      case "genre": {
        const gn = lib.genres[route.id];
        if (!gn) { rows.push({ type: "msg", h: 110, text: "That genre isn't in the library any more." }); v.back = true; break; }
        v.back = true;
        const idx = gn.tracks.slice().sort((a, b) => C.cmp(lib.T[a].fa, lib.T[b].fa) || C.cmp(lib.T[a].fal, lib.T[b].fal) || (lib.T[a].d - lib.T[b].d) || (lib.T[a].n - lib.T[b].n));
        rows.push({ type: "hero", h: heroH, kind: "genre", title: gn.name, sub: "", meta: `${plural(gn.tracks.length, "song")}`, art: "", list: idx, obj: gn });
        rows.push(...trackRows(idx, g));
        break;
      }
      default: {                                    // home
        v.key = "home";
        rows.push({ type: "homeacts", h: g.W < 560 ? 136 : 84 });
        const here = connect.others.find(d => d.state && d.state.track && d.live && d.state.playing);
        if (here && !isPlaying()) rows.push({ type: "here", h: 84, dev: here });
        const ra = recentAlbums(g.cols * 1);
        if (ra.length) { head("Recently played", { label: "See all", hash: "played" }); rows.push(...cardRows(ra, g, "")); }
        if (lib.hasAdded) { head("Recently added", { label: "See all", hash: "added" }); rows.push(...cardRows(sortedAlbums("newest").slice(0, g.cols * 2), g, "added")); }
        head("Your library");
        const secs = lib.T.reduce((s, t) => s + t.s, 0);
        rows.push({ type: "stats", h: 56, text: `${plural(lib.T.length, "song")} · ${plural(lib.artists.length, "artist")} · ${plural(lib.albums.length, "album")} · ${C.fmtLong(secs)} of music, streamed from skippy-pc.` });
      }
    }
    return v;
  }

  function buildSearch() {
    const g = geometry(), res = C.search(S.lib, S.search, 1500), rows = [];
    if (res.artists.length) { rows.push({ type: "head", h: 42, text: "Artists" }); res.artists.forEach(ar => rows.push({ type: "artist", h: g.rowH + 4, ar })); }
    if (res.albums.length) { rows.push({ type: "head", h: 42, text: "Albums" }); res.albums.forEach(al => rows.push({ type: "albumrow", h: g.rowH + 4, al })); }
    if (res.tracks.length) {
      rows.push({ type: "head", h: 42, text: res.total > res.tracks.length ? `Songs · top ${res.tracks.length.toLocaleString()} of ${res.total.toLocaleString()}` : `Songs · ${res.total.toLocaleString()}` });
      rows.push(...trackRows(res.tracks, g));
    }
    if (!rows.length) rows.push({ type: "msg", h: 120, text: `Nothing matches “${S.search}”. Try fewer words: search looks in titles, artists, albums and genres, ignoring accents and capitals.` });
    return { rows, key: "search", rail: null, sort: null, back: false };
  }

  function renderView(keep) {
    if (!S.lib) return;
    const sc = $("#scroll"), prev = S.view ? S.view.key : "";
    if (prev && !keep) S.scrollMemo.set(prev, sc.scrollTop);
    const top = sc.scrollTop;
    const t0 = performance.now();
    const v = S.search ? buildSearch() : buildRoute(S.route);
    S.view = v;
    S.geo = geoKey();
    setNavCurrent(); updateTop(v);
    railOn = !!(v.rail && v.rail.length > 3 && v.rows.length > 40);
    sc.classList.toggle("railed", railOn);
    list.setRows(v.rows);
    buildRail(v);
    if (keep) sc.scrollTop = top;
    else sc.scrollTop = S.fresh ? 0 : (S.scrollMemo.get(v.key) || 0);
    S.fresh = false;
    list.update();
    if (window.console && console.debug && v.rows.length > 1000) console.debug(`bilby: view ${v.key} built in ${Math.round(performance.now() - t0)} ms (${v.rows.length} rows)`);
  }
  let viewTimer = 0;
  const scheduleView = () => { clearTimeout(viewTimer); viewTimer = setTimeout(() => renderView(true), 250); };
  const geoKey = () => { const g = geometry(); return `${g.cols}/${g.rowH}/${g.heroH}/${Math.round(g.cw)}`; };

  function setNavCurrent() {
    const name = S.search ? "" : (["artist"].includes(S.route.name) ? "artists" : ["album"].includes(S.route.name) ? "albums" : S.route.name === "genre" ? "genres" : S.route.name);
    for (const a of $$("#nav a")) { if (a.dataset.view === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); }
  }
  function updateTop(v) {
    $("#back").hidden = !v.back || !!S.search;
    const sel = $("#sort");
    if (v.sort && !S.search) {
      sel.hidden = false; sel.dataset.id = v.sort.id;
      sel.replaceChildren(...v.sort.options.map(([val, label]) => { const o = el("option", "", label); o.value = val; o.selected = val === v.sort.value; return o; }));
    } else sel.hidden = true;
  }

  // — the A-Z rail —
  const rail = {
    box: $("#rail"), on: false, items: [],
    hideBubble() { const b = $(".rail-bubble"); if (b) b.remove(); this.box.classList.remove("on"); },
  };
  function buildRail(v) {
    const box = rail.box;
    rail.on = railOn; rail.items = v.rail || [];
    box.hidden = !railOn;
    if (!railOn) return;
    const have = new Map(rail.items.map(r => [r.l, r.i]));
    const letters = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
    box.replaceChildren(...letters.map(l => { const s = el("span", "", l); if (!have.has(l)) s.style.opacity = ".3"; return s; }));
  }
  function railJump(clientY, show) {
    const r = rail.box.getBoundingClientRect(), letters = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    let k = clamp(Math.floor((clientY - r.top) / r.height * letters.length), 0, letters.length - 1);
    const have = new Map(rail.items.map(x => [x.l, x.i]));
    let l = letters[k];
    for (let d = 0; d < letters.length && !have.has(l); d++) { l = letters[Math.min(letters.length - 1, k + d)]; }
    if (!have.has(l)) for (let d = 0; d < letters.length && !have.has(l); d++) { l = letters[Math.max(0, k - d)]; }
    if (!have.has(l)) return;
    list.scrollTo(have.get(l));
    if (show) {
      let b = $(".rail-bubble");
      if (!b) { b = el("div", "rail-bubble"); $("#listwrap").append(b); }
      b.textContent = l;
    }
  }
  rail.box.addEventListener("pointerdown", e => { rail.box.setPointerCapture(e.pointerId); rail.box.classList.add("on"); railJump(e.clientY, true); e.preventDefault(); });
  rail.box.addEventListener("pointermove", e => { if (rail.box.hasPointerCapture(e.pointerId)) railJump(e.clientY, true); });
  const railEnd = () => rail.hideBubble();
  rail.box.addEventListener("pointerup", railEnd); rail.box.addEventListener("pointercancel", railEnd);

  // — routing: views are #hash routes; sheets are history entries, so the back button closes them —
  function parseHash() {
    const [name, id] = location.hash.replace(/^#\/?/, "").split("/");
    const n = VIEWS.includes(name) ? name : "home";
    const num = id === undefined || id === "" ? undefined : parseInt(id, 10);
    return { name: n, id: Number.isFinite(num) ? num : undefined };
  }
  /** Open a view. A sheet (Now Playing, the Queue) that is open closes into it: its history entry becomes the view's, so Back
   *  goes to where the person was before the sheet, not back into it. */
  function go(hash) {
    if (S.search) clearSearch(true);
    const inSheet = !!(history.state && history.state.sheet);
    if (S.sheet) closeSheet(true);
    if (location.hash === "#" + hash && !inSheet) return;
    if (inSheet) history.replaceState({ n: ++S.nav }, "", "#" + hash);
    else history.pushState({ n: ++S.nav }, "", "#" + hash);
    onRoute(false);
  }
  function onRoute(back) {
    S.route = parseHash();
    S.hashShown = location.hash;
    S.fresh = !back;
    if (S.search) clearSearch(true);
    renderView(false);
    if (!back) $("#scroll").scrollTop = 0;
  }
  const parentHash = () => ({ artist: "artists", album: "albums", genre: "genres" })[S.route.name] || "home";
  function goBack() {
    if (history.state && history.state.n > 0) history.back(); else go(parentHash());
  }
  window.addEventListener("popstate", e => {
    const st = e.state || {};
    if (S.sheet && !st.sheet) closeSheet(true);
    else if (!S.sheet && st.sheet) openSheet(st.sheet, true);
    if (S.lib && location.hash !== S.hashShown) onRoute(true);
  });
  history.replaceState({ n: 0 }, "", location.href);

  // — search —
  let searchTimer = 0;
  function onSearchInput() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const v = $("#q").value.trim();
      if (v === S.search) return;
      const was = S.search;
      if (!was && S.view) S.scrollMemo.set(S.view.key, $("#scroll").scrollTop);
      S.search = v;
      $("#q-clear").hidden = !$("#q").value;
      S.fresh = !!v || !was; renderView(false);
      if (v) $("#scroll").scrollTop = 0;
    }, 90);
  }
  function clearSearch(silent) {
    $("#q").value = ""; $("#q-clear").hidden = true;
    clearTimeout(searchTimer);
    if (!S.search) return;
    S.search = "";
    if (!silent) renderView(false);
  }

  // — clicks on rows (one delegated listener for the whole list) —
  function rowAction(row, b, rowEl, ev) {
    const r = b.dataset.r, T = S.lib.T;
    switch (row.type) {
      case "track":
        if (r === "play") return playList(row.list, row.pos);
        if (r === "more") return openMenu(b, T[row.t].t, trackMenu(row.t));
        break;
      case "artist":
        if (r === "open") return go(`artist/${row.ar.id}`);
        if (r === "more") return openMenu(b, row.ar.name, listMenu(row.ar.albums.flatMap(a => a.tracks), row.ar.name, [{ label: "Go to artist", icon: "artist", run: () => go(`artist/${row.ar.id}`) }]));
        break;
      case "albumrow":
        if (r === "open") return go(`album/${row.al.id}`);
        if (r === "more") return openMenu(b, row.al.name, listMenu(row.al.tracks, row.al.name));
        break;
      case "genre": if (r === "open") return go(`genre/${row.g.id}`); break;
      case "cards": {
        const a = row.albums[+b.dataset.k];
        if (!a) return;
        if (r === "open") return go(`album/${a.id}`);
        if (r === "go") return playList(a.tracks, 0);
        break;
      }
      case "head": if (r === "link" && row.link) return go(row.link.hash); break;
      case "hero": {
        const L = row.list;
        if (r === "hero-play") return playList(L, 0);
        if (r === "hero-shuffle") return shuffleList(L);
        if (r === "hero-add") return queueMany(L, false, row.title);
        if (r === "hero-artist") { const ar = S.lib.artistByName.get(row.obj.artist); if (ar) return go(`artist/${ar.id}`); }
        break;
      }
      case "homeacts":
        if (r === "dj") return djStart();
        if (r === "shuffle-all") return shuffleList(sortedTracks("title"));
        break;
      case "here": if (r === "take") return adopt(row.dev.state, row.dev.live && row.dev.state.playing ? row.dev.id : null); break;
    }
  }
  $("#host").addEventListener("click", ev => {
    const b = ev.target.closest("button[data-r]"), rowEl = ev.target.closest(".vrow");
    if (!b || !rowEl) return;
    const row = list.rows[rowEl._i];
    // After a mouse or touch click the row lets go of the keyboard focus, so Space pauses the music instead of pressing the
    // same row again. (A click made with the keyboard keeps its focus, so keyboard users never lose their place.)
    if (ev.detail > 0 && b.dataset.r !== "more") b.blur();
    if (row) rowAction(row, b, rowEl, ev);
  });

  // ═══ menus ══════════════════════════════════════════════════════════════════════════════════════════════════════
  let menu = null;
  function closeMenu(keepFocus) {
    if (!menu) return;
    $("#menu").hidden = true; $("#scrim").hidden = true;
    const a = menu.anchor; menu = null;
    if (!keepFocus && a && a.isConnected) { try { a.focus(); } catch (e) { /* gone */ } }
  }
  function openMenu(anchor, title, items, kind) {
    closeMenu(true);
    const m = $("#menu");
    m.replaceChildren();
    if (title) m.append(el("h4", "", title));
    for (const it of items) {
      if (it.sep) { m.append(el("hr")); continue; }
      if (it.node) { m.append(it.node); continue; }
      const b = el("button"); b.type = "button";
      b.setAttribute("role", it.checked !== undefined ? "menuitemradio" : "menuitem");
      if (it.checked !== undefined) b.setAttribute("aria-checked", it.checked);
      if (it.icon) b.insertAdjacentHTML("beforeend", ico(it.icon));
      b.append(it.label);
      b.onclick = () => { closeMenu(true); it.run(); };
      m.append(b);
    }
    m.hidden = false;
    const narrow = innerWidth < 700;
    $("#scrim").hidden = !narrow;
    m.style.cssText = "";
    if (narrow) { m.style.left = "0"; m.style.right = "0"; m.style.bottom = "0"; m.style.maxWidth = "none"; m.style.borderRadius = "16px 16px 0 0"; m.style.maxHeight = "70vh"; m.style.overflowY = "auto"; }
    else {
      const r = anchor.getBoundingClientRect(), mw = m.offsetWidth, mh = m.offsetHeight;
      let x = r.right - mw, y = r.bottom + 4;
      if (y + mh > innerHeight - 8) y = Math.max(8, r.top - mh - 4);
      m.style.left = clamp(x, 8, innerWidth - mw - 8) + "px"; m.style.top = y + "px";
    }
    menu = { anchor, kind: kind || "" };
    const first = $("button", m);
    if (first) first.focus();
  }
  $("#scrim").addEventListener("click", () => closeMenu());
  document.addEventListener("pointerdown", e => { if (menu && !$("#menu").contains(e.target) && !(menu.anchor && menu.anchor.contains(e.target))) closeMenu(true); }, true);
  $("#menu").addEventListener("keydown", e => {
    const bs = $$("button", $("#menu")), i = bs.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); bs[(i + 1) % bs.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(); }
    else if (e.key === "Tab") closeMenu(true);
  });
  const trackMenu = t => {
    const T = S.lib.T[t], ar = S.lib.artistByName.get(T.a), al = albumOf(T);
    const items = [
      { label: "Play next", icon: "next", run: () => queueMany([t], true, T.t) },
      { label: "Add to queue", icon: "addq", run: () => queueMany([t], false, T.t) },
      { sep: true },
    ];
    if (ar) items.push({ label: "Go to artist", icon: "artist", run: () => go(`artist/${ar.id}`) });
    if (al) items.push({ label: "Go to album", icon: "album", run: () => go(`album/${al.id}`) });
    return items;
  };
  const listMenu = (tracks, name, extra) => [
    { label: "Play", icon: "play", run: () => playList(tracks, 0) },
    { label: "Shuffle", icon: "shuffle", run: () => shuffleList(tracks) },
    { label: "Play next", icon: "next", run: () => queueMany(tracks, true, name) },
    { label: "Add to queue", icon: "addq", run: () => queueMany(tracks, false, name) },
  ].concat(extra && extra.length ? [{ sep: true }].concat(extra) : []);

  function sleepMenu(anchor) {
    const sl = S.sleep, now = Date.now();
    const mk = (label, kind) => ({ label, checked: sl.active && ((kind === "end" && sl.endOfTrack) || (kind !== "end" && !sl.endOfTrack && kind === sl._kind)), run: () => { sl.start(kind, Date.now()); sl._kind = kind; updateSleepUi(); startSleepTick(); toast(kind === "end" ? "Sleep timer: stops after this song" : `Sleep timer: ${kind} minutes`, { ms: 2200 }); } });
    const items = [mk("15 minutes", 15), mk("30 minutes", 30), mk("60 minutes", 60), mk("End of this song", "end")];
    if (sl.active) items.push({ sep: true }, { label: `Turn off (${sl.label(now)} left)`, run: () => { sl.stop(); applyVolume(); updateSleepUi(); } });
    openMenu(anchor, "Sleep timer", items);
  }
  let sleepTimer = 0;
  function startSleepTick() { clearInterval(sleepTimer); sleepTimer = setInterval(sleepTick, 1000); }
  function sleepTick() {
    const sl = S.sleep;
    if (!sl.active) { clearInterval(sleepTimer); return; }
    if (sl.due(Date.now())) { sl.stop(); actions.pause(); applyVolume(); updateSleepUi(); toast("Sleep timer: music stopped."); return; }
    applyVolume(); updateSleepUi();
  }
  function updateSleepUi() {
    const sl = S.sleep, label = sl.active ? sl.label(Date.now()) : "";
    for (const n of els.sleep) n.textContent = sl.endOfTrack ? "end" : label.replace(" min", "m");
    for (const b of $$('[data-act="sleep"]')) b.classList.toggle("on", sl.active);
    if (!sl.active) { clearInterval(sleepTimer); applyVolume(); }
  }

  // ═══ sheets: Now Playing and the Queue ══════════════════════════════════════════════════════════════════════════
  const wide = () => matchMedia("(min-width: 1000px)").matches;
  let sheetOpener = null;
  function setTab(which) {
    S.tab = which;
    $("#tab-np").setAttribute("aria-selected", which === "np"); $("#tab-q").setAttribute("aria-selected", which === "queue");
    $("#panel-np").hidden = which !== "np"; $("#panel-q").hidden = which !== "queue";
    if (which === "queue") renderQueue(true);
  }
  function inertBehind(on) {
    for (const s of ["#tapbar", "#notice", ".bp-body", "#bar"]) { const n = $(s); if (n) { if (on) n.setAttribute("inert", ""); else n.removeAttribute("inert"); } }
  }
  function openSheet(which, fromPop) {
    if (wide()) { setTab(which); return; }                 // docked: the tab is all there is to switch
    if (!S.sheet) {
      S.sheet = which; root.dataset.sheet = "open";         // visible first, so the queue list can measure itself
      sheetOpener = document.activeElement;
      if (!fromPop) history.pushState({ n: ++S.nav, sheet: which }, "", location.href);
      if (innerWidth < 700) inertBehind(true);
    }
    setTab(which);
    const t = $(which === "np" ? "#tab-np" : "#tab-q"); if (t) t.focus();
  }
  function closeSheet(fromPop) {
    if (!S.sheet) return;
    S.sheet = ""; root.dataset.sheet = ""; inertBehind(false);
    if (!fromPop && history.state && history.state.sheet) history.back();
    const o = sheetOpener; sheetOpener = null;
    if (o && o.isConnected) { try { o.focus(); } catch (e) { /* gone */ } }
  }
  matchMedia("(min-width: 1000px)").addEventListener("change", () => { if (S.sheet) closeSheet(); renderView(true); });

  // ═══ the queue panel ════════════════════════════════════════════════════════════════════════════════════════════
  const qm = qList.makers;
  qm.qh = {
    make() { const e = el("div", "vrow qh"); e._h = el("h3"); e._b = el("button", "pill-btn"); e._b.type = "button"; e._b.dataset.r = "earlier"; e.append(e._h, e._b); return e; },
    bind(e, r) { e._h.textContent = r.text; e._b.hidden = !r.link; e._b.textContent = r.link || ""; },
  };
  qm.qmsg = { make() { return el("div", "vrow vmsg"); }, bind(e, r) { e.textContent = r.text; } };
  qm.qr = {
    make() {
      const e = el("div", "vrow qr"); e.setAttribute("role", "listitem");
      const grip = el("button", "q-grip"); grip.type = "button"; grip.dataset.r = "grip"; grip.innerHTML = ico("grip"); grip.setAttribute("aria-label", "Drag to reorder");
      const main = el("button", "q-main"); main.type = "button"; main.dataset.r = "jump";
      const cv = mkCover(""), txt = el("span", "txt"), l1 = el("span", "l1"), eq = el("span", "eq"), l1t = el("span"), l2 = el("span", "l2");
      eq.setAttribute("aria-hidden", "true"); eq.innerHTML = "<span></span><span></span><span></span>";
      l1.append(eq, l1t); txt.append(l1, l2); main.append(cv.box, txt);
      const mkb = (r, icon, label) => { const b = el("button", "ib q-btn"); b.type = "button"; b.dataset.r = r; b.innerHTML = ico(icon); b.setAttribute("aria-label", label); return b; };
      const up = mkb("up", "up", "Move up"), dn = mkb("down", "down", "Move down"), rm = mkb("remove", "close", "Remove from the queue");
      e.append(grip, main, up, dn, rm);
      e._r = { grip, main, cv, l1t, l2, up, dn, rm, eq };
      return e;
    },
    bind(e, r) {
      const Q = S.q, it = Q.items[r.pos], x = e._r;
      if (!it) return;
      const t = S.lib.T[it.t], isCur = r.pos === Q.qi, auto = Q.isAuto(r.pos);
      e.classList.toggle("cur", isCur); e.classList.toggle("played", r.pos < Q.qi); e.dataset.pos = r.pos;
      setArt(x.cv.img, t.ap, 40);
      x.l1t.textContent = t.t;
      x.l2.replaceChildren(auto ? el("span", "auto", "Bilby's pick · ") : "", t.a);
      x.eq.style.display = isCur ? "inline-flex" : "none";
      const canMove = r.pos > Q.qi;
      x.grip.hidden = !canMove; x.up.hidden = !canMove; x.dn.hidden = !canMove; x.rm.hidden = isCur;
      x.up.disabled = r.pos <= Q.qi + 1; x.dn.disabled = r.pos >= Q.length - 1;
      x.main.setAttribute("aria-label", isCur ? `${t.t}, playing now` : `Play ${t.t} by ${t.a}`);
    },
  };

  function queueRows() {
    const Q = S.q, rows = [], h = geometry().rowH;
    if (!Q.cur) { rows.push({ type: "qmsg", h: 120, text: "The queue is empty. Play an album or a song, or let Bilby DJ pick." }); return rows; }
    if (Q.qi > 0) {
      rows.push({ type: "qh", h: 40, text: `Earlier · ${Q.qi}`, link: S.showEarlier ? "Hide" : "Show" });
      if (S.showEarlier) for (let p = 0; p < Q.qi; p++) rows.push({ type: "qr", h, pos: p });
    }
    rows.push({ type: "qh", h: 40, text: "Now playing" });
    rows.push({ type: "qr", h, pos: Q.qi });
    const up = Q.length - Q.qi - 1;
    rows.push({ type: "qh", h: 40, text: up ? `Next up · ${up.toLocaleString()}` : "Next up" });
    if (!up) rows.push({ type: "qmsg", h: 90, text: S.dj ? "Nothing queued. Bilby will pick something when this song is nearly over." : "Nothing queued after this." });
    for (let p = Q.qi + 1; p < Q.length; p++) rows.push({ type: "qr", h, pos: p });
    return rows;
  }
  function renderQueue(scroll) {
    if (S.tab !== "queue") { qDirty = true; return; }       // drawn when its tab is opened
    qDirty = false;
    const Q = S.q;
    qList.setRows(queueRows());
    const left = Q.cur ? C.fmtLong(Q.totalSeconds(S.lib.T)) : "";
    $("#q-sum").textContent = Q.cur ? `${Q.length.toLocaleString()} in the queue${Q.length - Q.qi - 1 > 0 ? ` · ${left} to go` : ""}` : "The queue is empty.";
    if (scroll && Q.cur) { const idx = qList.rows.findIndex(r => r.type === "qr" && r.pos === Q.qi); if (idx >= 0) qList.scrollTo(Math.max(0, idx - 1)); }
  }
  let qDirty = true;

  const qHost = $("#q-host");
  qHost.addEventListener("click", ev => {
    const b = ev.target.closest("button[data-r]"), rowEl = ev.target.closest(".vrow");
    if (!b || !rowEl) return;
    const row = qList.rows[rowEl._i];
    if (!row) return;
    const r = b.dataset.r;
    if (row.type === "qh" && r === "earlier") { S.showEarlier = !S.showEarlier; renderQueue(false); return; }
    if (row.type !== "qr") return;
    const pos = row.pos;
    if (r === "jump") { if (pos === S.q.qi) return actions.toggle(); return startTrack(pos, { play: true }); }
    if (r === "remove") { if (S.q.remove(pos)) { onQueueChanged(); } return; }
    if (r === "up" || r === "down") {
      const to = r === "up" ? pos - 1 : pos + 1;
      if (S.q.move(pos, to)) { onQueueChanged(); refocusQueue(to, r); }
    }
  });
  function refocusQueue(pos, r) {
    requestAnimationFrame(() => {
      const idx = qList.rows.findIndex(x => x.type === "qr" && x.pos === pos);
      if (idx < 0) return;
      if (!qList.el(idx)) qList.scrollTo(idx, true);
      qList.update();
      const e = qList.el(idx), b = e && (e.querySelector(`[data-r="${r}"]:not(:disabled)`) || e.querySelector('[data-r="jump"]'));
      if (b) b.focus();
    });
  }

  // drag to reorder (pointer events: mouse and touch alike); the up and down buttons do the same for keyboards
  let drag = null;
  qHost.addEventListener("pointerdown", e => {
    const grip = e.target.closest(".q-grip");
    if (!grip) return;
    const rowEl = grip.closest(".vrow"), row = qList.rows[rowEl._i];
    if (!row || row.type !== "qr" || row.pos <= S.q.qi) return;
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    drag = { from: row.pos, to: row.pos, rowEl, pid: e.pointerId, y: e.clientY, raf: 0, drop: el("div", "q-drop") };
    rowEl.classList.add("dragging"); qHost.append(drag.drop);
    dragMove(e.clientY);
  });
  function dragTarget(clientY) {
    const sc = $("#q-scroll"), r = sc.getBoundingClientRect();
    const y = clientY - r.top + sc.scrollTop;
    let i = C.rowAt(qList.off, y);
    const rows = qList.rows;
    // only upcoming rows are drop targets
    const first = rows.findIndex(x => x.type === "qr" && x.pos === S.q.qi + 1), last = rows.length - 1;
    if (first < 0) return null;
    i = clamp(i, first, last);
    while (i > first && rows[i].type !== "qr") i--;
    return rows[i].type === "qr" ? { i, pos: rows[i].pos } : null;
  }
  function dragMove(clientY) {
    if (!drag) return;
    drag.y = clientY;
    const t = dragTarget(clientY);
    if (!t) return;
    drag.to = t.pos;
    const top = drag.to > drag.from ? qList.off[t.i] + qList.rows[t.i].h : qList.off[t.i];
    drag.drop.style.top = (top - 1) + "px";
  }
  function dragLoop() {
    if (!drag) return;
    const sc = $("#q-scroll"), r = sc.getBoundingClientRect();
    if (drag.y < r.top + 44) sc.scrollTop -= 12; else if (drag.y > r.bottom - 44) sc.scrollTop += 12;
    dragMove(drag.y);
    drag.raf = requestAnimationFrame(dragLoop);
  }
  qHost.addEventListener("pointermove", e => { if (drag && e.pointerId === drag.pid) { dragMove(e.clientY); if (!drag.raf) drag.raf = requestAnimationFrame(dragLoop); } });
  function dragEnd(commit) {
    if (!drag) return;
    cancelAnimationFrame(drag.raf);
    drag.drop.remove(); drag.rowEl.classList.remove("dragging");
    const d = drag; drag = null;
    if (commit && d.to !== d.from && S.q.move(d.from, d.to)) { onQueueChanged(); toast("Moved", { ms: 1200 }); }
  }
  qHost.addEventListener("pointerup", e => { if (drag && e.pointerId === drag.pid) dragEnd(true); });
  qHost.addEventListener("pointercancel", () => dragEnd(false));
  document.addEventListener("keydown", e => { if (drag && e.key === "Escape") { e.stopPropagation(); dragEnd(false); } }, true);

  // ═══ when the queue or the song changes ═════════════════════════════════════════════════════════════════════════
  function onQueueChanged() {
    S.qv++;
    renderUpNext();
    if (list) list.refresh();
    if (S.tab === "queue" && (S.sheet || wide())) renderQueue(false); else qDirty = true;
    const n = S.q.cur ? S.q.length - S.q.qi - 1 : 0;
    $("#nav-queue").lastChild.textContent = n > 0 ? `Queue · ${n > 999 ? "999+" : n}` : "Queue";
    preloadNext(); persistSoon(); publishSoon();
  }
  /** The playing song changed but the queue's contents did not: the "now playing" marker and the count move. */
  function onQueueMoved() {
    if (S.tab === "queue" && (S.sheet || wide())) renderQueue(false); else qDirty = true;
    const n = S.q.cur ? S.q.length - S.q.qi - 1 : 0;
    $("#nav-queue").lastChild.textContent = n > 0 ? `Queue · ${n > 999 ? "999+" : n}` : "Queue";
  }

  // ═══ Connect: this device tells the portal what it plays; another can take over or be its remote ═══════════════
  const connect = { others: [], busy: false, timer: 0, last: 0 };
  function connectState() {
    const t = curTrack();
    if (!t) return null;
    const a = S.armed.snapshot();
    const st = { track: t.p, title: t.t, artist: t.a, pos: Math.round(curPos() * 10) / 10, playing: isPlaying(), idx: 0, dj: S.dj, shuffle: S.shuffle, repeat: S.repeat, armed: a.armed, tap: a.tap };
    const idx = S.q.qi - S.qLo;
    if (S.qSent !== S.qv || idx < 0 || idx >= 600) {
      const w = S.q.window(20, 300);
      st.queue = w.items.map(it => S.lib.T[it.t].p); st.idx = w.qi; st._lo = S.q.qi - w.qi;
    } else st.idx = idx;
    return st;
  }
  async function checkin() {
    if (connect.busy || !S.lib) return;
    connect.busy = true; connect.last = Date.now();
    const st = connectState(), qv = S.qv, lo = st && st._lo;
    if (st) delete st._lo;
    try {
      const r = await fetch(cfg.checkin, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" },
                                           body: JSON.stringify({ device, name: env.label, state: st }) });
      if (!r.ok) return;
      const reply = await r.json();
      if (st && st.queue) { S.qSent = qv; S.qLo = lo; }
      connect.others = Array.isArray(reply.devices) ? reply.devices : [];
      for (const c of reply.commands || []) remote(c);
      connectChanged();
    } catch (e) { /* offline: the next check-in catches up */ } finally { connect.busy = false; }
  }
  let publishTimer = 0;
  function publishSoon() { clearTimeout(publishTimer); publishTimer = setTimeout(checkin, 400); }
  function startConnect() {
    clearInterval(connect.timer);
    connect.timer = setInterval(() => {
      const gap = isPlaying() ? 4500 : document.hidden ? 30000 : 9500;
      if (Date.now() - connect.last >= gap) checkin();
    }, 1500);
    checkin();
  }
  /** A command from another device runs through exactly the code the buttons use. If this page has never been tapped the
   *  browser refuses the sound, and the one-tap bar explains it. */
  function remote(cmd) {
    if (cmd === "play") { if (!S.armed.armed) S.tapWhy = "remote"; actions.resume(); }
    else if (cmd === "pause") actions.pause();
    else if (cmd === "next") actions.next();
    else if (cmd === "prev") actions.prev();
  }
  const liveOthers = () => connect.others.filter(d => d.live && d.state && d.state.track);
  function connectChanged() {
    const devs = liveOthers(), n = devs.length;
    for (const s of els.devices) s.textContent = n ? String(n) : "";
    // redraw an open Devices menu (and Home's "playing elsewhere" card) only when what they show has changed
    const sig = devs.map(d => [d.id, d.state.playing, d.state.title, d.state.armed, d.state.tap].join("|")).join(";");
    if (sig === connect.sig) return;
    connect.sig = sig;
    if (menu && menu.kind === "devices") devicesMenu(menu.anchor);
    if (S.route.name === "home" && !S.search && S.view && S.view.key === "home") scheduleView();
  }
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
  }
  /** Pick up another device's queue and place (and ask it to pause, if it is playing). */
  function adopt(s, takeOver) {
    if (!S.lib || !s) return;
    const list = (s.queue || []).map(p => S.lib.byPath.get(p)).filter(i => i !== undefined);
    const at = list.indexOf(S.lib.byPath.get(s.track));
    if (at < 0) { toast("That song isn't in the library any more."); return; }
    S.dj = s.dj !== false; S.shuffle = !!s.shuffle; if (["off", "all", "one"].includes(s.repeat)) S.repeat = s.repeat;
    S.q.load(list, at, false); syncToggles(); onQueueChanged();
    startTrack(at, { from: s.pos || 0, play: true });
    if (takeOver) post(cfg.command, { device: takeOver, action: "pause" }).then(() => setTimeout(checkin, 1200)).catch(() => {});
  }
  function sendRemote(d, action) {
    post(cfg.command, { device: d.id, action }).then(r => r.json().then(j => {
      // needs_tap is the portal's own reading of that device's latest check-in: its page has not made a sound since it loaded
      if (j && j.queued && j.needs_tap && action !== "pause") toast(`${d.name} needs a tap before it can make sound: ${action === "play" ? "it will start" : "it will carry on"} as soon as someone taps it.`, { ms: 6000 });
      if (j && j.queued === false) toast(`${d.name} isn't answering right now.`);
    })).catch(() => toast("Couldn't reach the portal.")).finally(() => [1500, 4000, 7000, 10000].forEach(ms => setTimeout(checkin, ms)));   // the other player picks the command up at its next check-in, up to ~5 s on: look until the answer shows
  }
  function devicesMenu(anchor) {
    const items = [], devs = liveOthers();
    if (!devs.length) items.push({ node: el("div", "none", "No other device has this player open right now. Open Bilby's Audio Service on another device and it shows up here, and either one can hand over or remote-control the other.") });
    for (const d of devs) {
      const s = d.state, box = el("div", "dev"), who = el("div", "who"), what = el("div", "what");
      who.append(el("b", "", d.name));
      what.textContent = s.playing ? `${s.title || "Playing"} — ${s.artist || ""}` : `${s.title ? s.title + " (paused)" : "Paused"}`;
      const acts = el("div", "acts");
      const mk = (label, run) => { const b = el("button", "", label); b.type = "button"; b.onclick = () => { closeMenu(true); run(); }; return b; };
      acts.append(mk(s.playing ? "Pause" : "Play", () => sendRemote(d, s.playing ? "pause" : "play")), mk("Next", () => sendRemote(d, "next")), mk("Play here", () => adopt(s, s.playing ? d.id : null)));
      box.append(who, what);
      if (s.armed === false || s.tap) box.append(el("div", "what need", "This player needs a tap first: browsers only play sound after a tap on the page. Play from here still works."));
      box.append(acts); items.push({ node: box });
    }
    openMenu(anchor, "Other devices", items, "devices");
  }

  // ═══ keyboard, buttons ══════════════════════════════════════════════════════════════════════════════════════════
  const typing = t => t && (t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable || (t.tagName === "INPUT" && !["range", "button", "checkbox", "radio"].includes(t.type)));
  document.addEventListener("keydown", e => {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (e.key === "Escape") {
      if (menu) { closeMenu(); e.preventDefault(); return; }
      if (S.sheet) { closeSheet(); e.preventDefault(); return; }
      if ((t === $("#q") || !typing(t)) && ($("#q").value || S.search)) { clearSearch(); e.preventDefault(); }
      return;
    }
    if (typing(t) || $("#keys").open) return;
    const onRange = t.tagName === "INPUT" && t.type === "range", onButton = t.tagName === "BUTTON" || t.tagName === "A" || t.tagName === "SUMMARY";
    switch (e.key) {
      case " ": if (onButton) return; e.preventDefault(); actions.toggle(); break;
      case "ArrowLeft": if (onRange) return; e.preventDefault(); if (e.shiftKey) actions.prev(); else actions.seekBy(-5); break;
      case "ArrowRight": if (onRange) return; e.preventDefault(); if (e.shiftKey) actions.next(); else actions.seekBy(5); break;
      case "ArrowUp": if (onRange) return; e.preventDefault(); actions.volBy(0.05); break;
      case "ArrowDown": if (onRange) return; e.preventDefault(); actions.volBy(-0.05); break;
      case "m": case "M": e.preventDefault(); actions.mute(); break;
      case "s": case "S": e.preventDefault(); actions.shuffle(); break;
      case "r": case "R": e.preventDefault(); actions.repeat(); break;
      case "/": e.preventDefault(); if (e.shiftKey) openKeys(); else { $("#q").focus(); $("#q").select(); } break;     // Shift+/ is "?" on a US keyboard
      case "?": e.preventDefault(); openKeys(); break;
    }
  });
  function openKeys() { const d = $("#keys"); if (d.showModal) { if (!d.open) d.showModal(); } else d.setAttribute("open", ""); }

  root.addEventListener("click", ev => {
    const b = ev.target.closest("[data-act]");
    if (!b || b.closest("#host")) return;
    const a = b.dataset.act;
    switch (a) {
      case "play": actions.toggle(); break;
      case "next": actions.next(); break;
      case "prev": actions.prev(); break;
      case "shuffle": actions.shuffle(); break;
      case "repeat": actions.repeat(); break;
      case "dj": actions.dj(); break;
      case "mute": actions.mute(); break;
      case "djstart": djStart(); break;
      case "tap": actions.resume(); break;
      case "np": openSheet("np"); break;
      case "queue": if (S.sheet && S.tab === "queue") closeSheet(); else openSheet("queue"); break;
      case "tab-np": setTab("np"); break;
      case "tab-q": setTab("queue"); break;
      case "close-sheet": closeSheet(); break;
      case "sleep": sleepMenu(b); break;
      case "devices": devicesMenu(b); break;
      case "help": openKeys(); break;
      case "back": goBack(); break;
      case "clear-search": clearSearch(); $("#q").focus(); break;
      case "clear-queue": if (S.q.cur && S.q.length - S.q.qi - 1 > 0) { const n = S.q.length - S.q.qi - 1; S.q.clearUpcoming(); onQueueChanged(); toast(`Cleared ${n.toLocaleString()} from the queue`); } break;
      case "open-artist": { const t = curTrack(), ar = t && S.lib.artistByName.get(t.a); if (ar) go(`artist/${ar.id}`); break; }
      case "open-album": { const t = curTrack(), al = t && albumOf(t); if (al) go(`album/${al.id}`); break; }
    }
  });
  // a link in the nav (#artists, #albums...) closes a sheet, and a view change is a real history entry
  $("#nav").addEventListener("click", e => {
    const a = e.target.closest("a[data-view]");
    if (!a) return;
    e.preventDefault();
    go(a.dataset.view);
  });

  // the position and volume sliders
  function wireRanges() {
    for (const r of els.seek) {
      r.addEventListener("input", () => {
        S.seeking = true; clearTimeout(r._t); r._t = setTimeout(() => { S.seeking = false; }, 700);
        const v = +r.value, d = curDur();
        r.style.setProperty("--p", (d ? v / d * 100 : 0) + "%");
        for (const n of els.cur) n.textContent = C.fmtTime(v);
        for (const n of els.rem) n.textContent = d ? "-" + C.fmtTime(Math.max(0, d - v)) : "0:00";
      });
      r.addEventListener("change", () => { S.seeking = false; seekTo(+r.value); });
      r.addEventListener("pointerdown", () => { S.seeking = true; });
      r.addEventListener("pointerup", () => { setTimeout(() => { S.seeking = false; }, 50); });
    }
    for (const r of els.vol) r.addEventListener("input", () => actions.volume(+r.value / 100));
  }

  $("#q").addEventListener("input", onSearchInput);
  $("#q").addEventListener("search", onSearchInput);
  $("#sort").addEventListener("change", e => {
    const id = e.target.dataset.id;
    S.sorts[id] = e.target.value; savePrefs();
    S.fresh = true; renderView(false);
  });

  // the tab or window being closed, hidden or brought back
  addEventListener("pagehide", () => persist());
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { persist(); return; }
    if (!S.lib) return;
    checkin();
    if (!gate.fresh()) gate.ensure().catch(() => {});
    // Someone asked this page to play while it was in the background and the browser would not (no tap yet): say so now
    if (S.want && S.q.cur && !isPlaying() && !act().el.ended && !S.armed.armed) showTap();
  });
  addEventListener("online", () => { if (!S.lib || $("#notice").dataset.key === "gate") retryGate(); });
  setInterval(() => { if (S.lib && !gate.fresh() && !document.hidden) gate.ensure().catch(() => {}); }, 5 * 60 * 1000);
  if (window.ResizeObserver) {
    let rt = 0;
    new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => { if (S.lib && geoKey() !== S.geo) renderView(true); else list.update(); }, 120); }).observe($("#scroll"));
    new ResizeObserver(() => qList.update()).observe($("#q-scroll"));      // the queue is measured only once its sheet is on screen
  }

  // ═══ start ══════════════════════════════════════════════════════════════════════════════════════════════════════
  buildSlots();
  fillIcons();
  for (const img of $$(".cover img")) { img.onload = () => img.classList.add("ok"); img.onerror = () => img.classList.remove("ok"); }   // the covers the page itself holds
  wireRanges();
  for (const s of slots) wireSlot(s);
  probeVolume(); applyVolume(); syncToggles(); syncPlay(); updateSleepUi();
  initMediaSession();
  $("#tab-np").setAttribute("aria-selected", "true");
  showTrack();
  setTab("np");
  loadLibrary();
  // exposed for the tests that drive the page by hand (portal/tests/dev_music.py) and for debugging in the console
  window.BilbyApp = { S, actions, gate, slots, list, qList, toast, startTrack, tryPlay, renderView, checkin, connect, persist, get lib() { return S.lib; } };
})();
