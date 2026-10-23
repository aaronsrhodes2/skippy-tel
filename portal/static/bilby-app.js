/* Bilby's Audio Service: the browser app (portal/templates/music.html is its shell, bilby-player.js its logic, bilby-player.css its look).

   What happens here, in order of the file:
     the gate           the portal hands out a pass; the music gate on skippy-pc serves the library for it
     the library        catalog.json (cached in IndexedDB so the app opens instantly), lyrics.json, cover thumbnails
     the engine         one or two <audio> elements, preloading, errors, the browser's autoplay rule
     the queue + DJ     every edit goes through BilbyCore.Queue; Bilby DJ picks ahead of time
     the views          Home, Artists, Albums, Songs, Genres, Recently added/played, artist and album pages, search:
                        shown a screen at a time (nothing scrolls), so 21,000 songs are ~10 DOM rows
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

  // A speaker window on skippy-pc (tools/room_players.py) opens /music?room=<key>: one fixed Connect device pinned to one audio output.
  let ROOMS = {};
  try { ROOMS = JSON.parse(root.dataset.rooms || "{}") || {}; } catch (e) { /* no rooms: an ordinary player */ }
  const roomKey = (new URLSearchParams(location.search).get("room") || "").toLowerCase();
  const room = Object.prototype.hasOwnProperty.call(ROOMS, roomKey) ? Object.assign({ key: roomKey }, ROOMS[roomKey]) : null;
  let device = room ? "room-" + room.key : store.get("device", "");
  if (!/^[A-Za-z0-9_-]{8,40}$/.test(device || "")) {
    device = Array.from(crypto.getRandomValues(new Uint8Array(12)), b => b.toString(16).padStart(2, "0")).join("");
    store.set("device", device);
  }

  // ═══ icons: the portal's glyphs (templates/_glyphs.html), by the names this file uses ═════════════════════════════
  const GLYPH = { close: "x", artist: "user", album: "disc", clock: "past", plus: "new", dj: "headset", sleep: "moon" };
  const ico = n => `<svg class="g" aria-hidden="true" focusable="false"><use href="#g-${GLYPH[n] || n}"/></svg>`;
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
    tapWhy: "", playAt: 0, ourPauseAt: 0, route: { name: "home" }, search: "", nav: 0, view: null, pageMemo: new Map(), showEarlier: false, letters: false,
    sheet: "", tab: "np", loading: false, seeking: false, scrollAt: 0, geo: "",
    // the signal shield: songs held in memory ahead of time, and the way back after a dead zone (on unless switched off)
    shield: prefs.shield !== false, lastTimeAt: 0, waitingAt: 0, netBadAt: 0,
  };
  const savePrefs = () => store.set("prefs", { repeat: S.repeat, shuffle: S.shuffle, dj: S.dj, vol: S.vol, muted: S.muted, sorts: S.sorts, shield: S.shield });
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
    syncSignal();
  }
  function hideNotice(key) { const n = $("#notice"); if (!key || n.dataset.key === key) { n.hidden = true; n.dataset.key = ""; syncSignal(); } }
  /** The lamp beside the player: lit when all is well, a triangle while the line is lost (the music is waiting for it), a diamond when the library is out of reach. */
  function syncSignal() {
    const lamp = $("#signal"), n = $("#notice");
    if (!lamp) return;
    const lost = root.dataset.signal === "lost", bad = !!n && !n.hidden && n.classList.contains("bad");
    const state = lost ? "warn" : bad ? "off" : "on", label = lost ? "No signal: the music is waiting for it to come back" : bad ? "The library is out of reach" : "Connected";
    lamp.dataset.s = state; lamp.setAttribute("aria-label", label); lamp.title = label;
  }

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
          catch (e) { S.netBadAt = Date.now(); throw Object.assign(new Error("The portal isn't reachable from here."), { kind: "net" }); }
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
  const flying = () => false;
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

  // ═══ a list shown a screen at a time: Bridge.PList (static/bridge.js) ═════════════════════════════════════════════════
  const PList = Bridge.PList;

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

  // ═══ the signal shield: songs in memory ahead of time, and the way back after a dead zone ════════════════════════
  // A song held in memory plays with no network at all: the playing one (so a drop mid-song is nothing), then the next few. Fetched one
  // at a time, after the playing song has settled, and only when this browser is not asked to save data.
  const blobs = new C.BlobCache({ maxBytes: 90e6, maxItems: 5, onEvict: v => { try { URL.revokeObjectURL(v.url); } catch (e) { /* gone already */ } } });
  const fetching = new Map();                                // song index -> AbortController
  let prefetchTok = 0;
  const saveData = () => !!(navigator.connection && navigator.connection.saveData);
  async function fetchBlob(tIdx) {
    if (blobs.has(tIdx) || fetching.has(tIdx) || !gate.info || !S.lib) return;
    const t = S.lib.T[tIdx], ac = new AbortController();
    fetching.set(tIdx, ac);
    try {
      const r = await fetch(gateUrl("a", t.p), { signal: ac.signal, cache: "no-store" });
      if (!r.ok) return;
      const b = await r.blob();
      blobs.put(tIdx, b.size, { url: URL.createObjectURL(b) });
      onBlobReady(tIdx);
    } catch (e) { if (e && e.name !== "AbortError") S.netBadAt = Date.now(); /* the line dropped: the next pass tries again */ } finally { fetching.delete(tIdx); }
  }
  function upcomingTracks() { return S.q.upcoming().map(it => it.t); }
  /** Hold the playing song and the next two. Restarted whenever the queue moves; an unwanted fetch in flight is cancelled. */
  async function prefetchSoon() {
    if (!S.shield || !S.lib || !S.q.cur || saveData() || !gate.info) return;
    const order = C.prefetchOrder(S.q.cur.t, S.repeat === "one" ? [] : upcomingTracks(), 2);
    blobs.pin(order);
    for (const [k, ac] of fetching) if (!order.includes(k)) ac.abort();
    const tok = ++prefetchTok;
    for (const t of order) {
      if (tok !== prefetchTok || !S.q.cur) return;                  // the queue moved on: a newer pass owns this
      if (t === S.q.cur.t && !act().pre) continue;                  // the playing song settles first
      await fetchBlob(t);
    }
  }
  /** A song just finished downloading: if it is the one the idle element should be holding, give it to it now. */
  function onBlobReady(tIdx) {
    const other = idle();
    if (!env.singleElement && S.shield && !other.path) preloadNext();
    if (rec.on && S.q.cur && S.q.cur.t === tIdx) recoverNow();      // a song we were waiting on is here
  }

  /** `bust`: a retry after a failure asks for the file under a new URL, so a half-downloaded copy the browser cached from the failed try is not served again. */
  function load(slot, tIdx, from, bust) {
    const t = S.lib.T[tIdx];
    slot.t = tIdx; slot.path = t.p; slot.from = from || 0; slot.logged = false; slot.djDone = false; slot.retried = false; slot.pre = false; slot.everPlayed = false;
    S.ourPauseAt = Date.now();                              // replacing the source stops whatever played: that pause is ours
    const held = S.shield ? blobs.get(tIdx) : null;         // in memory: no network needed at all
    slot.el.src = held ? held.url : gateUrl("a", t.p, bust ? "?r=" + Date.now() : "");
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
    if (gate.fresh() || (S.shield && blobs.has(tIdx))) return go();
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
    if (S.shield && !saveData() && !blobs.has(t)) { if (other.path) unload(other); prefetchSoon(); return; }   // hold it in memory first: one download, not two
    load(other, t, 0);
    other.el.preload = "auto"; applyVolume();
  }

  function onEnded() {
    if (S.sleep.endOfTrack) { S.sleep.stop(); updateSleepUi(); S.want = false; onPlayState(); toast("Sleep timer: music stopped."); return; }
    const s = act();
    if (S.repeat === "one") { s.el.currentTime = 0; tryPlay(s); return; }
    advance(true);
  }

  // ── when the line dies ──
  // The place is kept (the song and the second), the wish to play stays, and a retry runs on a back-off, for as long as it takes. It
  // starts again the instant the browser says the network is back or the page comes forward. A song held in memory just keeps playing.
  const rec = { on: false, n: 0, timer: 0, t: -1, at: 0, since: 0 };
  function recoverStart(slot, why) {
    if (rec.on || !S.want || slot !== act() || !slot.path) return;
    rec.on = true; rec.n = 0; rec.t = slot.t; rec.at = slot.el.currentTime || slot.from || 0; rec.since = Date.now();
    root.dataset.signal = "lost"; syncSignal(); mediaState();
    if (S.dbg) console.debug("signal lost:", why);
    rec.timer = setTimeout(recoverTick, C.recoverDelay(0));
  }
  function recoverStop() {
    if (!rec.on) return;
    clearTimeout(rec.timer); rec.on = false; rec.n = 0;
    delete root.dataset.signal; hideNotice("gate"); syncSignal(); mediaState();
  }
  /** Try again right now (the network is back, the page is in front, a song we waited for arrived). */
  function recoverNow() { if (!rec.on) return; clearTimeout(rec.timer); recoverTick(); }
  async function recoverTick() {
    if (!rec.on) return;
    clearTimeout(rec.timer);
    const slot = act();
    if (!S.want || !S.q.cur || S.q.cur.t !== rec.t) { recoverStop(); return; }       // the person moved on, or paused: nothing to resume
    rec.timer = setTimeout(recoverTick, C.recoverDelay(++rec.n));                    // if this try hangs without an error, the next one still comes
    rec.at = slot.el.currentTime > 1 ? slot.el.currentTime : rec.at;
    if (!blobs.has(rec.t) && !gate.fresh()) { try { await gate.ensure(true); } catch (err) { return; } }      // no line yet: wait for the next try
    if (!rec.on || slot !== act()) return;
    load(slot, rec.t, rec.at, true);
    applyVolume();
    tryPlay(slot);
  }
  /** A song that was playing went quiet with the line: a stall (no error, just "waiting") counts too. */
  function stallWatch() {
    if (rec.on || !S.want) return;
    const s = act();
    if (!s.path || s.el.paused || !s.everPlayed) return;                     // a song that never started is the file's business, not the line's
    if (C.stalled(Date.now(), S.lastTimeAt, S.waitingAt > S.lastTimeAt)) recoverStart(s, "stalled");
  }
  setInterval(stallWatch, 2000);

  async function onError(slot) {
    const e = slot.el;
    if (!slot.path || !e.getAttribute("src")) return;
    if (slot !== act()) { unload(slot); return; }                     // a preload that failed is forgotten; the song loads normally when its turn comes
    if (rec.on) return;                                               // already waiting for the line: the back-off timer makes the next try
    if (S.shield && C.errorKind({ code: e.error && e.error.code, online: navigator.onLine, everPlayed: slot.everPlayed, recentNetFail: Date.now() - S.netBadAt < 90000 }) === "network") {
      recoverStart(slot, "error");                                    // the connection, not the file: keep the place and wait for it
      if (rec.on && slot.everPlayed) { rec.at = Math.max(rec.at, slot.el.currentTime || 0); }
      return;
    }
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
    e.addEventListener("playing", () => { if (!mine()) return; slot.from = 0; slot.everPlayed = true; S.lastTimeAt = Date.now(); busy(false); S.errStreak = 0; hideNotice("gate"); recoverStop(); onPlayState(); });
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
    e.addEventListener("waiting", () => { if (mine()) { S.waitingAt = Date.now(); busy(true); } });
    e.addEventListener("stalled", () => { if (mine() && slot.everPlayed) S.waitingAt = Date.now(); });
    e.addEventListener("seeked", () => { if (mine()) { syncTime(); mediaPosition(); } });
    e.addEventListener("progress", () => mine() && syncBuffered());
    e.addEventListener("ended", () => mine() && onEnded());
    e.addEventListener("error", () => onError(slot));
  }

  function onTime(slot) {
    S.lastTimeAt = Date.now();
    syncTime();
    const dur = curDur(), pos = slot.el.currentTime;
    if (!slot.logged && pos >= Math.min(8, (dur || 16) / 2)) { slot.logged = true; logPlay(slot.path); }
    if (!slot.pre && (pos >= 6 || (dur && dur - pos <= 40))) { slot.pre = true; preloadNext(); prefetchSoon(); }
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
  const els = { play: [], seek: [], cur: [], rem: [], vol: [], shuffle: [], repeat: [], dj: [], mute: [], sleep: [], devices: [], shield: [] };

  const TP = `
    <button type="button" class="key" data-act="prev" aria-label="Previous" title="Previous (Shift+Left)"><i data-i="prev"></i></button>
    <button type="button" class="key big" data-act="play" aria-label="Play" title="Play (Space)"><i data-i="play"></i></button>
    <button type="button" class="key" data-act="next" aria-label="Next" title="Next (Shift+Right)"><i data-i="next"></i></button>`;
  const SK = `
    <span class="t" data-bind="cur">0:00</span>
    <input type="range" class="rg" data-seek min="0" max="0" step="1" value="0" aria-label="Position" disabled>
    <span class="t" data-bind="rem">0:00</span>`;
  const RIGHT = `
    <div class="volbox"><button type="button" class="key" data-act="mute" aria-label="Mute" title="Mute (M)"><i data-i="vol"></i></button>
      <input type="range" class="rg vol" data-vol min="0" max="100" step="1" value="100" aria-label="Volume"></div>
    <button type="button" class="key" data-act="sleep" aria-label="Sleep timer" title="Sleep timer"><i data-i="sleep"></i><sup data-bind="sleep"></sup></button>
    <button type="button" class="key" data-act="devices" aria-label="Other devices" title="Other devices (Connect)"><i data-i="devices"></i><sup data-bind="dev"></sup></button>
    <button type="button" class="key" data-act="shield" aria-pressed="true" aria-label="Signal shield" title="Signal shield: keep the next songs in memory so the music rides through a dead zone"><i data-i="shield"></i></button>`;
  function buildSlots() {
    for (const s of $$('[data-slot="tp"]')) s.innerHTML = TP;
    for (const s of $$('[data-slot="sk"]')) s.innerHTML = SK;
    for (const s of $$('[data-slot="right"]')) s.innerHTML = RIGHT;
    fillIcons();
    els.play = $$('[data-act="play"]'); els.seek = $$("[data-seek]"); els.cur = $$('[data-bind="cur"]'); els.rem = $$('[data-bind="rem"]');
    els.vol = $$("[data-vol]"); els.shuffle = $$('[data-act="shuffle"]'); els.repeat = $$('[data-act="repeat"]');
    els.dj = $$('[data-act="dj"]'); els.mute = $$('[data-act="mute"]'); els.sleep = $$('[data-bind="sleep"]'); els.devices = $$('[data-bind="dev"]'); els.shield = $$('[data-act="shield"]');
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
    for (const b of els.shield) b.setAttribute("aria-pressed", S.shield);
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
    $("#b-sub").textContent = has ? [t.a, t.al].filter(Boolean).join(" · ") : "";
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
  function mediaState() { if (ms) { try { ms.playbackState = !S.q.cur ? "none" : (isPlaying() || (rec.on && S.want)) ? "playing" : "paused"; } catch (e) { /* ignore */ } } }
  function mediaPosition() {
    if (!ms || !ms.setPositionState) return;
    const d = curDur();
    if (!d || !Number.isFinite(d)) return;
    try { ms.setPositionState({ duration: d, position: clamp(curPos(), 0, d), playbackRate: 1 }); } catch (e) { /* out-of-range while loading */ }
  }

  // ═══ views ══════════════════════════════════════════════════════════════════════════════════════════════════════
  // Home is a deck of controls; everything else is a list shown a screen at a time (PList: swipe, PageUp/PageDown, or drag along the
  // bar under it). A letter key opens the whole alphabet when a list is sorted by name.
  const letterBubble = (show, page) => {
    const b = $("#bubble");
    if (!show || !rail.on) { b.hidden = true; return; }
    b.textContent = letterForRow(list.starts[page] || 0); b.hidden = false;
  };
  const list = new PList($("#scroll"), $("#host"), {}, { pips: $("#pips"), pgno: $("#pgno"), scrub: $("#scrub"), onscrub: letterBubble });
  const qList = new PList($("#q-scroll"), $("#q-host"), {}, { pips: $("#q-pips"), pgno: $("#q-pgno"), scrub: $("#q-scrub") });
  const VIEWS = ["home", "artists", "albums", "tracks", "genres", "added", "played", "artist", "album", "genre"];
  const LABELS = { artists: "Artists", albums: "Albums", tracks: "Songs", genres: "Genres", added: "Recently added", played: "Recently played" };
  const rail = { on: false, items: [] };
  const letterForRow = row => { let l = ""; for (const r of rail.items) { if (r.i <= row) l = r.l; else break; } return l; };

  const geometry = () => ({ rowH: innerHeight < 620 ? 50 : 56, heroH: innerHeight < 620 ? 120 : 132 });
  const counts = (box, pairs) => {
    box.replaceChildren();
    pairs.forEach(([n, g]) => { box.append(`${n.toLocaleString()}`); box.insertAdjacentHTML("beforeend", ico(g)); });
    box.querySelectorAll(".g").forEach(s => s.classList.add("mini"));
  };

  // — row makers —
  const makers = list.makers;

  makers.title = {
    make() { const e = el("div", "vrow vtitle"); e._g = el("span", "gl"); e._c = el("span", "count"); e.append(e._g, e._c); return e; },
    bind(e, r) { e._g.innerHTML = ico(r.glyph); e._c.textContent = r.count || ""; },
  };
  makers.head = {
    make() { const e = el("div", "vrow vhead"); e._g = el("span", "gl"); e._c = el("span", "count"); e.append(e._g, e._c); return e; },
    bind(e, r) { e._g.innerHTML = ico(r.glyph); e._c.textContent = r.count || ""; },
  };
  makers.msg = {
    make() { const e = el("div", "vrow vmsg"); e._g = el("span", "gl"); e._t = el("span"); e.append(e._g, e._t); return e; },
    bind(e, r) { e._g.innerHTML = ico(r.glyph || "info"); e._t.textContent = r.text; },
  };
  makers.disc = {
    make() { const e = el("div", "vrow vdisc"); e._g = el("span", "gl"); e._t = el("span"); e.append(e._g, e._t); return e; },
    bind(e, r) { e._g.innerHTML = ico("album"); e._t.textContent = String(r.n); },
  };

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
      e.append(main, more); e._r = { cv, l1, l2, more, main }; return e;
    },
    bind(e, r) {
      const a = r.ar, x = e._r;
      setArt(x.cv.img, a.artPath, 44);
      x.l1.textContent = a.name;
      counts(x.l2, [[a.albums.length, "album"], [a.tracks.length, "note"]]);
      x.more.setAttribute("aria-label", `More options for ${a.name}`);
      x.main.setAttribute("aria-label", a.name);
    },
  };
  makers.albumrow = {
    make() {
      const e = el("div", "vrow nv"); e.setAttribute("role", "listitem");
      const main = el("button", "nv-main"); main.type = "button"; main.dataset.r = "open";
      const cv = mkCover(""), txt = el("span", "txt"), l1 = el("span", "l1"), l2 = el("span", "l2");
      txt.append(l1, l2); main.append(cv.box, txt);
      const go = el("button", "ib tr-more"); go.type = "button"; go.dataset.r = "go"; go.innerHTML = ico("play");
      const more = el("button", "ib tr-more"); more.type = "button"; more.dataset.r = "more"; more.innerHTML = ico("more");
      e.append(main, go, more); e._r = { cv, l1, l2, more, go, main }; return e;
    },
    bind(e, r) {
      const a = r.al, x = e._r;
      setArt(x.cv.img, a.artPath, 44);
      x.l1.textContent = a.name;
      x.l2.textContent = r.sub === "added" ? `${a.artist} · ${C.fmtAgo(a.added, Date.now() / 1000)}` : [a.artist, a.year || ""].filter(Boolean).join(" · ");
      x.more.setAttribute("aria-label", `More options for ${a.name}`);
      x.go.setAttribute("aria-label", `Play ${a.name}`);
      x.main.setAttribute("aria-label", `${a.name} by ${a.artist}`);
    },
  };
  makers.genre = {
    make() {
      const e = el("div", "vrow nv"); e.setAttribute("role", "listitem");
      const main = el("button", "nv-main"); main.type = "button"; main.dataset.r = "open";
      const txt = el("span", "txt"), l1 = el("span", "l1"), l2 = el("span", "l2"), chev = el("span", "chev"); chev.innerHTML = ico("chev");
      txt.append(l1, l2); main.append(txt, chev);
      e.append(main); e._r = { l1, l2, main }; return e;
    },
    bind(e, r) { e._r.l1.textContent = r.g.name; counts(e._r.l2, [[r.g.tracks.length, "note"]]); e._r.main.setAttribute("aria-label", r.g.name); },
  };

  // the header of an album, an artist or a genre: what it is, its name, and the keys that act on all of it
  makers.hero = {
    make() {
      const e = el("div", "vrow hero"), cv = mkCover("");
      const txt = el("div", "txt"), kind = el("span", "kind"), h = el("h2"), who = el("span", "who"), meta = el("span", "meta"), acts = el("div", "acts");
      const mkb = (r, label, icon) => { const b = el("button", "key"); b.type = "button"; b.dataset.r = r; b.innerHTML = ico(icon); b.setAttribute("aria-label", label); b.title = label; return b; };
      acts.append(mkb("hero-play", "Play", "play"), mkb("hero-shuffle", "Shuffle", "shuffle"), mkb("hero-add", "Add to queue", "addq"));
      txt.append(kind, h, who, meta, acts); e.append(cv.box, txt);
      e._r = { cv, kind, h, who, meta };
      return e;
    },
    bind(e, r) {
      const x = e._r;
      x.cv.box.classList.toggle("round", r.kind === "artist");
      setArt(x.cv.img, r.art, 104);
      x.kind.innerHTML = ico(r.kind === "album" ? "album" : r.kind === "artist" ? "artist" : "tag");
      x.h.textContent = r.title;
      x.who.replaceChildren();
      if (r.kind === "album") { const b = el("button", "", r.sub); b.type = "button"; b.dataset.r = "hero-artist"; x.who.append(b); }
      x.meta.textContent = r.meta;
    },
  };

  // — building a view —
  const trackRows = (idx, g, o) => idx.map((t, pos) => Object.assign({ type: "track", h: g.rowH, t, list: idx, pos }, o || {}));
  const albumRows = (albums, g, sub) => albums.map(al => ({ type: "albumrow", h: g.rowH, al, sub }));
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
  function recentTracks(limit) {
    const seen = new Set(), out = [];
    for (let k = S.hist.length - 1; k >= 0 && out.length < limit; k--) {
      const i = S.lib.byPath.get(S.hist[k]); if (i !== undefined && !seen.has(i)) { seen.add(i); out.push(i); }
    }
    return out;
  }
  const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;

  /** The alphabet's jump points: the first row of each letter, from the sort key of each item. */
  function railFor(items, keyOf, rowOf) {
    const seen = new Set(), out = [];
    items.forEach((it, k) => { const l = C.letterOf(keyOf(it)); if (!seen.has(l)) { seen.add(l); out.push({ l, i: rowOf(k) }); } });
    return out;
  }

  function buildRoute(route) {
    const lib = S.lib, g = geometry(), rows = [];
    const title = (glyph, count) => rows.push({ type: "title", h: 56, glyph, count, glue: true });
    const head = (glyph, count) => rows.push({ type: "head", h: 40, glyph, count, glue: true });
    const msg = (text, glyph) => rows.push({ type: "msg", h: 96, text, glyph });
    const v = { rows, key: route.name + (route.id !== undefined ? "/" + route.id : ""), rail: null, sort: null, back: false };
    switch (route.name) {
      case "artists": {
        const mode = S.sorts.artists;
        const A = mode === "songs" ? lib.artists.slice().sort((a, b) => b.tracks.length - a.tracks.length || a.id - b.id) : lib.artists;
        title("artist", A.length.toLocaleString());
        const base = rows.length;
        A.forEach(ar => rows.push({ type: "artist", h: g.rowH + 4, ar }));
        if (mode === "name") v.rail = railFor(A, a => a.key, k => base + k);
        v.sort = { id: "artists", value: mode, options: [["name", "A to Z"], ["songs", "Most songs"]] };
        break;
      }
      case "albums": {
        const mode = S.sorts.albums, A = sortedAlbums(mode);
        title("album", A.length.toLocaleString());
        const base = rows.length;
        rows.push(...albumRows(A, g, mode === "newest" ? "added" : ""));
        if (mode === "title" || mode === "artist") v.rail = railFor(A, a => (mode === "title" ? a.key2 : C.sortKey(a.artist)), k => base + k);
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
        title("note", idx.length.toLocaleString());
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
        title("tag", lib.genres.length.toLocaleString());
        if (!lib.genres.length) msg("None of these songs has a genre tag.");
        lib.genres.forEach(gn => rows.push({ type: "genre", h: g.rowH, g: gn }));
        break;
      }
      case "added": {
        title("plus", "");
        if (!lib.hasAdded) { msg("The music gate hasn't said when songs were added yet. Once it is updated, the newest albums show up here."); break; }
        rows.push(...albumRows(sortedAlbums("newest").slice(0, 120), g, "added"));
        break;
      }
      case "played": {
        const idx = recentTracks(150);
        title("clock", idx.length ? idx.length.toLocaleString() : "");
        if (!idx.length) msg("Nothing played on this device yet. What you play shows up here (it stays in this browser).");
        rows.push(...trackRows(idx, g));
        break;
      }
      case "artist": {
        v.back = true;
        const ar = lib.artists[route.id];
        if (!ar) { msg("That artist isn't in the library any more."); break; }
        const all = ar.albums.flatMap(a => a.tracks);
        rows.push({ type: "hero", h: g.heroH, kind: "artist", title: ar.name, sub: "", meta: `${plural(ar.albums.length, "album")} · ${plural(ar.tracks.length, "song")} · ${C.fmtLong(ar.dur)}`, art: ar.artPath, list: all, obj: ar });
        head("album", ar.albums.length.toLocaleString());
        rows.push(...albumRows(ar.albums, g, ""));
        head("note", ar.tracks.length.toLocaleString());
        rows.push(...trackRows(all, g, { sub: "album" }));
        break;
      }
      case "album": {
        v.back = true;
        const al = lib.albums[route.id];
        if (!al) { msg("That album isn't in the library any more."); break; }
        rows.push({ type: "hero", h: g.heroH, kind: "album", title: al.name, sub: al.artist, meta: [al.year || "", plural(al.tracks.length, "song"), C.fmtLong(al.dur)].filter(Boolean).join(" · "), art: al.artPath, list: al.tracks, obj: al });
        let disc = 0;
        al.tracks.forEach((t, pos) => {
          const d = lib.T[t].d;
          if (al.discs > 1 && d !== disc) { disc = d; rows.push({ type: "disc", h: 34, n: d || 1, glue: true }); }
          rows.push({ type: "track", h: g.rowH, t, list: al.tracks, pos, num: true, owner: al.artist });
        });
        break;
      }
      case "genre": {
        v.back = true;
        const gn = lib.genres[route.id];
        if (!gn) { msg("That genre isn't in the library any more."); break; }
        const idx = gn.tracks.slice().sort((a, b) => C.cmp(lib.T[a].fa, lib.T[b].fa) || C.cmp(lib.T[a].fal, lib.T[b].fal) || (lib.T[a].d - lib.T[b].d) || (lib.T[a].n - lib.T[b].n));
        rows.push({ type: "hero", h: g.heroH, kind: "genre", title: gn.name, sub: "", meta: `${plural(gn.tracks.length, "song")}`, art: "", list: idx, obj: gn });
        rows.push(...trackRows(idx, g));
        break;
      }
      default:                                   // home: the deck, no rows
        v.key = "home"; v.home = true;
    }
    return v;
  }

  function buildSearch() {
    const g = geometry(), res = C.search(S.lib, S.search, 1500), rows = [];
    if (res.artists.length) { rows.push({ type: "head", h: 40, glyph: "artist", count: res.artists.length.toLocaleString(), glue: true }); res.artists.forEach(ar => rows.push({ type: "artist", h: g.rowH + 4, ar })); }
    if (res.albums.length) { rows.push({ type: "head", h: 40, glyph: "album", count: res.albums.length.toLocaleString(), glue: true }); res.albums.forEach(al => rows.push({ type: "albumrow", h: g.rowH, al })); }
    if (res.tracks.length) {
      rows.push({ type: "head", h: 40, glyph: "note", count: res.total > res.tracks.length ? `${res.tracks.length.toLocaleString()} / ${res.total.toLocaleString()}` : res.total.toLocaleString(), glue: true });
      rows.push(...trackRows(res.tracks, g));
    }
    if (!rows.length) rows.push({ type: "msg", h: 110, glyph: "search", text: `Nothing matches “${S.search}”. Try fewer words: search looks in titles, artists, albums and genres, ignoring accents and capitals.` });
    return { rows, key: "search", rail: null, sort: null, back: false };
  }

  /** Home: a control for each way into the library, the count on it. */
  function buildDeck() {
    const lib = S.lib, deck = $("#deck");
    const here = connect.others.find(d => d.state && d.state.track && d.live && d.state.playing);
    const units = [];
    if (here && !isPlaying()) units.push(["here", "devices", "Play here, where the other device left off", "", true]);
    units.push(["artists", "artist", LABELS.artists, lib.artists.length.toLocaleString()], ["albums", "album", LABELS.albums, lib.albums.length.toLocaleString()],
               ["tracks", "note", LABELS.tracks, lib.T.length.toLocaleString()], ["genres", "tag", LABELS.genres, lib.genres.length.toLocaleString()]);
    if (lib.hasAdded) units.push(["added", "plus", LABELS.added, ""]);
    units.push(["played", "clock", LABELS.played, ""], ["dj", "dj", "Bilby, play something", ""], ["shuffle-all", "shuffle", "Shuffle everything", ""]);
    const n = units.length;
    deck.style.setProperty("--rows", Math.ceil(n / 2)); deck.style.setProperty("--rows-wide", Math.ceil(n / 4)); deck.style.setProperty("--rows-side", Math.ceil(n / 5));
    deck.replaceChildren(...units.map(([id, glyph, label, count, lit], k) => {
      const u = el("div", "unit named" + (lit ? " lit" : "")); u.style.setProperty("--i", k);
      if (lit) u.dataset.s = "on";
      const hit = el("button", "hit"); hit.type = "button"; hit.dataset.u = id; hit.setAttribute("aria-label", label); hit.title = label;
      u.append(el("i", "plate"), hit);
      const claws = el("i", "claws"); claws.innerHTML = "<b></b><b></b><b></b>"; u.append(claws);
      if (lit) u.append(el("span", "lamp"));
      u.insertAdjacentHTML("beforeend", ico(glyph));
      if (count) u.append(el("span", "name", count));
      return u;
    }));
    deck.hidden = false;
  }
  $("#deck").addEventListener("click", ev => {
    const b = ev.target.closest("[data-u]");
    if (!b || !S.lib) return;
    const u = b.dataset.u;
    if (u === "dj") return djStart();
    if (u === "shuffle-all") return shuffleList(sortedTracks("title"));
    if (u === "here") { const d = connect.others.find(x => x.state && x.state.track && x.live && x.state.playing); if (d) adopt(d.state, d.id); return; }
    go(u);
  });

  function renderView(keep) {
    if (!S.lib) return;
    const prev = S.view ? S.view.key : "";
    if (prev && !keep && S.view.rows.length) S.pageMemo.set(prev, list.page);
    const v = S.search ? buildSearch() : buildRoute(S.route);
    S.view = v;
    updateTop(v);
    $("#deck").hidden = !v.home; $("#listwrap").hidden = !!v.home;
    if (v.home) { buildDeck(); rail.on = false; rail.items = []; $("#letters-key").hidden = true; return; }
    const page = keep ? list.page : (S.fresh ? 0 : (S.pageMemo.get(v.key) || 0));
    rail.items = v.rail || []; rail.on = rail.items.length > 3;
    $("#letters-key").hidden = !rail.on;
    list.setRows(v.rows, page);
    S.fresh = false;
  }
  let viewTimer = 0;
  const scheduleView = () => { clearTimeout(viewTimer); viewTimer = setTimeout(() => renderView(true), 250); };

  function updateTop(v) {
    $("#back").hidden = !v.back || !!S.search;
    const sort = $("#sort");
    if (v.sort && !S.search) {
      sort.hidden = false; sort.dataset.id = v.sort.id;
      const cur = v.sort.options.find(o => o[0] === v.sort.value);
      sort.setAttribute("aria-label", `Sort: ${cur ? cur[1] : ""}`); sort.title = `Sort: ${cur ? cur[1] : ""}`;
    } else sort.hidden = true;
  }
  function cycleSort() {
    const v = S.view;
    if (!v || !v.sort) return;
    const o = v.sort.options, at = Math.max(0, o.findIndex(x => x[0] === v.sort.value)), next = o[(at + 1) % o.length];
    S.sorts[v.sort.id] = next[0]; savePrefs();
    S.fresh = true; renderView(false);
    toast(next[1], { ms: 1400 });
  }

  // — the alphabet: every letter on one screen, the ones this list has are lit —
  function openLetters() {
    if (!rail.on) return;
    const have = new Map(rail.items.map(r => [r.l, r.i]));
    $("#letters").replaceChildren(..."#ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").map(l => {
      const b = el("button", "lang-opt", l); b.type = "button"; b.disabled = !have.has(l); b.setAttribute("aria-label", l);
      b.onclick = () => { closeLetters(); list.goRow(have.get(l)); };
      return b;
    }));
    $("#lettersheet").hidden = false; S.letters = true;
  }
  function closeLetters() { $("#lettersheet").hidden = true; S.letters = false; }

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
    const hadSearch = !!S.search;
    if (hadSearch) clearSearch(true);
    if (S.letters) closeLetters();
    if (menu) closeMenu(true);
    const inSheet = !!(history.state && history.state.sheet);
    if (S.sheet) closeSheet(true);
    if (location.hash === "#" + hash && !inSheet) { if (hadSearch) { S.fresh = true; renderView(false); } return; }
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
      if (!was && S.view && S.view.rows.length) S.pageMemo.set(S.view.key, list.page);
      S.search = v;
      $("#q-clear").hidden = !$("#q").value;
      S.fresh = !!v || !was; renderView(false);
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
  function rowAction(row, b) {
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
        if (r === "go") return playList(row.al.tracks, 0);
        if (r === "more") return openMenu(b, row.al.name, listMenu(row.al.tracks, row.al.name));
        break;
      case "genre": if (r === "open") return go(`genre/${row.g.id}`); break;
      case "hero": {
        const L = row.list;
        if (r === "hero-play") return playList(L, 0);
        if (r === "hero-shuffle") return shuffleList(L);
        if (r === "hero-add") return queueMany(L, false, row.title);
        if (r === "hero-artist") { const ar = S.lib.artistByName.get(row.obj.artist); if (ar) return go(`artist/${ar.id}`); }
        break;
      }
    }
  }
  $("#host").addEventListener("click", ev => {
    const b = ev.target.closest("button[data-r]"), rowEl = ev.target.closest(".vrow");
    if (!b || !rowEl) return;
    const row = list.rows[rowEl._i];
    // After a mouse or touch click the row lets go of the keyboard focus, so Space pauses the music instead of pressing the
    // same row again. (A click made with the keyboard keeps its focus, so keyboard users never lose their place.)
    if (ev.detail > 0 && b.dataset.r !== "more") b.blur();
    if (row) rowAction(row, b);
  });

  // ═══ menus: a panel of keys over the stage (a song's options, the sleep timer, other devices) ═══════════════════
  let menu = null;
  function closeMenu(keepFocus) {
    if (!menu) return;
    $("#menu").hidden = true;
    const a = menu.anchor; menu = null;
    if (!keepFocus && a && a.isConnected) { try { a.focus(); } catch (e) { /* gone */ } }
  }
  /** items: { icon, label (said by a screen reader and on hover), tag (a short number shown under the glyph), run } or { node }. */
  function openMenu(anchor, title, items, kind) {
    closeMenu(true);
    const m = $("#menu"), grid = el("div", "mgrid"), head = el("div", "s-head");
    m.replaceChildren();
    const h = el("h4", "grow", title || ""), x = el("button", "key"); x.type = "button"; x.dataset.act = "close-menu"; x.innerHTML = ico("down");
    x.setAttribute("aria-label", "Close"); x.title = "Close";
    head.append(h, x); m.append(head, grid);
    for (const it of items) {
      if (it.sep) continue;
      if (it.node) { m.append(it.node); continue; }
      const b = el("button", "mkey"); b.type = "button";
      b.setAttribute("role", it.checked !== undefined ? "menuitemradio" : "menuitem");
      if (it.checked !== undefined) b.setAttribute("aria-checked", it.checked);
      b.setAttribute("aria-label", it.label); b.title = it.label;
      b.insertAdjacentHTML("beforeend", ico(it.icon || "note"));
      if (it.tag) b.append(el("span", "tag", it.tag));
      b.onclick = () => { closeMenu(true); it.run(); };
      grid.append(b);
    }
    m.hidden = false;
    menu = { anchor, kind: kind || "" };
    const first = $("button.mkey", m) || $("button", m);
    if (first) first.focus();
  }
  document.addEventListener("pointerdown", e => { if (menu && !$("#menu").contains(e.target) && !(menu.anchor && menu.anchor.contains(e.target))) closeMenu(true); }, true);
  $("#menu").addEventListener("keydown", e => {
    const bs = $$("button", $("#menu")), i = bs.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); bs[(i + 1) % bs.length].focus(); }
    else if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(); }
  });
  const trackMenu = t => {
    const T = S.lib.T[t], ar = S.lib.artistByName.get(T.a), al = albumOf(T);
    const items = [
      { label: "Play next", icon: "next", run: () => queueMany([t], true, T.t) },
      { label: "Add to queue", icon: "addq", run: () => queueMany([t], false, T.t) },
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
  ].concat(extra || []);

  function sleepMenu(anchor) {
    const sl = S.sleep, now = Date.now();
    const mk = (label, kind, tag, icon) => ({ label, icon: icon || "sleep", tag, checked: sl.active && ((kind === "end" && sl.endOfTrack) || (kind !== "end" && !sl.endOfTrack && kind === sl._kind)), run: () => { sl.start(kind, Date.now()); sl._kind = kind; updateSleepUi(); startSleepTick(); toast(kind === "end" ? "Sleep timer: stops after this song" : `Sleep timer: ${kind} minutes`, { ms: 2200 }); } });
    const items = [mk("15 minutes", 15, "15"), mk("30 minutes", 30, "30"), mk("60 minutes", 60, "60"), mk("End of this song", "end", "", "pause")];
    if (sl.active) items.push({ label: `Turn off (${sl.label(now)} left)`, icon: "close", tag: sl.label(now).replace(" min", "m"), run: () => { sl.stop(); applyVolume(); updateSleepUi(); } });
    openMenu(anchor, "", items);
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
    for (const n of els.sleep) n.textContent = sl.endOfTrack ? "·" : label.replace(" min", "m");
    for (const b of $$('[data-act="sleep"]')) b.classList.toggle("on", sl.active);
    if (!sl.active) { clearInterval(sleepTimer); applyVolume(); }
  }

  // ═══ sheets: Now Playing and the Queue ══════════════════════════════════════════════════════════════════════════
  // A panel over the stage. The strip along the bottom (and its keys) stay in reach, so there is nothing to close to pause or skip.
  let sheetOpener = null;
  function setTab(which) {
    S.tab = which;
    $("#tab-np").setAttribute("aria-selected", which === "np"); $("#tab-q").setAttribute("aria-selected", which === "queue");
    $("#panel-np").hidden = which !== "np"; $("#panel-q").hidden = which !== "queue";
    if (which === "queue") renderQueue(true);
  }
  function inertBehind(on) {
    for (const s of ["#tapbar", "#notice", "#top", "#deck", "#listwrap"]) { const n = $(s); if (n) { if (on) n.setAttribute("inert", ""); else n.removeAttribute("inert"); } }
  }
  function openSheet(which, fromPop) {
    if (!S.sheet) {
      S.sheet = which; root.dataset.sheet = "open"; $("#side").hidden = false;      // visible first, so the queue list can measure itself
      sheetOpener = document.activeElement;
      if (!fromPop) history.pushState({ n: ++S.nav, sheet: which }, "", location.href);
      inertBehind(true);
    }
    if (menu) closeMenu(true);
    if (S.letters) closeLetters();
    setTab(which);
    const t = $(which === "np" ? "#tab-np" : "#tab-q"); if (t) t.focus();
  }
  function closeSheet(fromPop) {
    if (!S.sheet) return;
    S.sheet = ""; root.dataset.sheet = ""; $("#side").hidden = true; inertBehind(false);
    if (!fromPop && history.state && history.state.sheet) history.back();
    const o = sheetOpener; sheetOpener = null;
    if (o && o.isConnected) { try { o.focus(); } catch (e) { /* gone */ } }
  }

  // ═══ the queue panel ════════════════════════════════════════════════════════════════════════════════════════════
  const qm = qList.makers;
  qm.qh = {
    make() {
      const e = el("div", "vrow qh"); e._g = el("span", "gl"); e._c = el("span", "count"); e._s = el("span", "grow");
      e._b = el("button", "pill-btn"); e._b.type = "button"; e._b.dataset.r = "earlier";
      e.append(e._g, e._c, e._s, e._b); return e;
    },
    bind(e, r) {
      e._g.innerHTML = ico(r.glyph); e._c.textContent = r.count || "";
      e._b.hidden = !r.toggle; e._b.innerHTML = r.toggle ? ico(r.toggle === "hide" ? "up" : "down") : "";
      e._b.setAttribute("aria-label", r.toggle === "hide" ? "Hide the earlier songs" : "Show the earlier songs");
    },
  };
  qm.qmsg = {
    make() { const e = el("div", "vrow vmsg"); e._g = el("span", "gl"); e._t = el("span"); e.append(e._g, e._t); return e; },
    bind(e, r) { e._g.innerHTML = ico("dj"); e._t.textContent = r.text; },
  };
  qm.qr = {
    make() {
      const e = el("div", "vrow qr"); e.setAttribute("role", "listitem");
      const main = el("button", "q-main"); main.type = "button"; main.dataset.r = "jump";
      const cv = mkCover(""), txt = el("span", "txt"), l1 = el("span", "l1"), eq = el("span", "eq"), l1t = el("span"), l2 = el("span", "l2");
      eq.setAttribute("aria-hidden", "true"); eq.innerHTML = "<span></span><span></span><span></span>";
      l1.append(eq, l1t); txt.append(l1, l2); main.append(cv.box, txt);
      const mkb = (r, icon, label) => { const b = el("button", "ib q-btn"); b.type = "button"; b.dataset.r = r; b.innerHTML = ico(icon); b.setAttribute("aria-label", label); return b; };
      const up = mkb("up", "up", "Move up"), dn = mkb("down", "down", "Move down"), rm = mkb("remove", "close", "Remove from the queue");
      e.append(main, up, dn, rm);
      e._r = { main, cv, l1t, l2, up, dn, rm, eq };
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
      x.up.hidden = !canMove; x.dn.hidden = !canMove; x.rm.hidden = isCur;
      x.up.disabled = r.pos <= Q.qi + 1; x.dn.disabled = r.pos >= Q.length - 1;
      x.main.setAttribute("aria-label", isCur ? `${t.t}, playing now` : `Play ${t.t} by ${t.a}`);
    },
  };

  function queueRows() {
    const Q = S.q, rows = [], h = geometry().rowH;
    if (!Q.cur) { rows.push({ type: "qmsg", h: 120, text: "The queue is empty. Play an album or a song, or let Bilby DJ pick." }); return rows; }
    if (Q.qi > 0) {
      rows.push({ type: "qh", h: 40, glyph: "earlier", count: String(Q.qi), toggle: S.showEarlier ? "hide" : "show", glue: true });
      if (S.showEarlier) for (let p = 0; p < Q.qi; p++) rows.push({ type: "qr", h, pos: p });
    }
    rows.push({ type: "qh", h: 40, glyph: "play", glue: true });
    rows.push({ type: "qr", h, pos: Q.qi });
    const up = Q.length - Q.qi - 1;
    rows.push({ type: "qh", h: 40, glyph: "next", count: up ? up.toLocaleString() : "", glue: true });
    if (!up) rows.push({ type: "qmsg", h: 90, text: S.dj ? "Nothing queued. Bilby will pick something when this song is nearly over." : "Nothing queued after this." });
    for (let p = Q.qi + 1; p < Q.length; p++) rows.push({ type: "qr", h, pos: p });
    return rows;
  }
  function renderQueue(jump) {
    if (S.tab !== "queue") { qDirty = true; return; }       // drawn when its tab is opened
    qDirty = false;
    const Q = S.q;
    qList.setRows(queueRows(), jump ? 0 : qList.page);
    const left = Q.cur ? C.fmtLong(Q.totalSeconds(S.lib.T)) : "";
    $("#q-sum").textContent = Q.cur ? `${Q.length.toLocaleString()}${Q.length - Q.qi - 1 > 0 ? ` · ${left}` : ""}` : "";
    if (jump && Q.cur) { const idx = qList.rows.findIndex(r => r.type === "qr" && r.pos === Q.qi); if (idx >= 0) qList.goRow(idx); }
  }
  let qDirty = true;

  $("#q-host").addEventListener("click", ev => {
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
      qList.goRow(idx);
      const e = qList.el(idx), b = e && (e.querySelector(`[data-r="${r}"]:not(:disabled)`) || e.querySelector('[data-r="jump"]'));
      if (b) b.focus();
    });
  }

  // ═══ when the queue or the song changes ═════════════════════════════════════════════════════════════════════════
  const queueLeft = () => (S.q.cur ? S.q.length - S.q.qi - 1 : 0);
  function showQueueCount() { const n = queueLeft(); $("#q-cnt").textContent = n > 0 ? (n > 999 ? "999+" : String(n)) : ""; }
  function onQueueChanged() {
    S.qv++;
    renderUpNext();
    if (list) list.refresh();
    if (S.tab === "queue" && S.sheet) renderQueue(false); else qDirty = true;
    showQueueCount();
    preloadNext(); prefetchSoon(); persistSoon(); publishSoon();
  }
  /** The playing song changed but the queue's contents did not: the "now playing" marker and the count move. */
  function onQueueMoved() {
    if (S.tab === "queue" && S.sheet) renderQueue(false); else qDirty = true;
    showQueueCount();
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
                                           body: JSON.stringify({ device, name: room ? room.name : env.label, room: room ? room.key : "", state: st }) });
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
      const gap = room ? 1500 : isPlaying() ? 4500 : document.hidden ? 30000 : 9500;     // a speaker window is never looked at, and waits for commands
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
    else if (/^take:/.test(cmd)) {      // "pick up what that device is playing": how a phone sends the music to a speaker window
      const from = connect.others.find(d => d.id === cmd.slice(5));
      if (from && from.state && from.state.track) adopt(from.state, null);
    }
  }
  const liveOthers = () => connect.others.filter(d => d.live && d.state && d.state.track);
  function connectChanged() {
    const devs = liveOthers(), n = devs.length;
    for (const s of els.devices) s.textContent = n ? String(n) : "";
    const roomSig = connect.others.filter(d => d.room && d.live).map(d => d.id).join(",");
    // redraw an open Devices menu (and Home's "playing elsewhere" card) only when what they show has changed
    const sig = devs.map(d => [d.id, d.state.playing, d.state.title, d.state.armed, d.state.tap].join("|")).join(";") + "#" + roomSig;
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
  /** Send the music to the speakers on skippy-pc. The source is this device if it is playing, else the speaker that is, else
   *  this device's own place. Each target picks the source's queue up (a "take" command); the source stops unless it is a target. */
  async function playOn(targets) {
    const rooms = connect.others.filter(d => d.room && d.live);
    const playingRoom = rooms.filter(d => d.state.playing && d.state.track).sort((a, b) => a.age - b.age)[0];
    const src = isPlaying() || !playingRoom ? null : playingRoom;
    if (!src && !curTrack()) { toast("Start a song first, then choose where it plays."); return; }
    const srcId = src ? src.id : device;
    if (!src) await checkin();          // the portal needs this device's latest queue and place before the speakers ask for them
    const to = targets.filter(d => d.id !== srcId);
    for (const t of to) post(cfg.command, { device: t.id, action: "take:" + srcId }).then(r => { if (!r.ok) toast(`${t.name} isn't answering right now.`); }).catch(() => toast("Couldn't reach the portal."));
    if (!targets.some(d => d.id === srcId)) { if (src) post(cfg.command, { device: src.id, action: "pause" }).catch(() => {}); else actions.pause(); }
    [1500, 4000, 7000].forEach(ms => setTimeout(checkin, ms));
    toast("Playing on " + targets.map(d => d.name).join(" and ") + ".");
  }
  function devicesMenu(anchor) {
    const items = [], devs = liveOthers(), rooms = connect.others.filter(d => d.room && d.live);
    if (rooms.length && !room) {
      const box = el("div", "dev"), who = el("div", "who"), acts = el("div", "acts");
      who.append(el("b", "", "Play on"));
      const pick = (label, targets) => { const b = el("button", "key wide", label); b.type = "button"; b.onclick = () => { closeMenu(true); playOn(targets); }; return b; };
      for (const r of rooms) acts.append(pick(r.name, [r]));
      if (rooms.length > 1) acts.append(pick(rooms.length === 2 ? "Both" : "All", rooms));
      box.append(who, acts); items.push({ node: box });
    }
    if (!devs.length && !items.length) items.push({ node: el("div", "none", "No other device has this player open right now. Open Bilby's Audio Service on another device and it shows up here, and either one can hand over or remote-control the other.") });
    for (const d of devs) {
      const s = d.state, box = el("div", "dev"), who = el("div", "who"), what = el("div", "what");
      who.append(el("b", "", d.name));
      what.textContent = s.playing ? `${s.title || "Playing"} — ${s.artist || ""}` : `${s.title ? s.title + " (paused)" : "Paused"}`;
      const acts = el("div", "acts");
      const mk = (label, icon, run) => { const b = el("button", "key"); b.type = "button"; b.innerHTML = ico(icon); b.setAttribute("aria-label", label); b.title = label; b.onclick = () => { closeMenu(true); run(); }; return b; };
      acts.append(mk(s.playing ? "Pause" : "Play", s.playing ? "pause" : "play", () => sendRemote(d, s.playing ? "pause" : "play")), mk("Next", "next", () => sendRemote(d, "next")),
                  mk("Play here", "link", () => adopt(s, s.playing ? d.id : null)));
      box.append(who, what);
      if (s.armed === false || s.tap) box.append(el("div", "what need", "This player needs a tap first: browsers only play sound after a tap on the page. Play from here still works."));
      box.append(acts); items.push({ node: box });
    }
    openMenu(anchor, "", items, "devices");
  }

  // ═══ a speaker window: pin the audio to this room's output ═══════════════════════════════════════════════════════
  // Chrome only shows an output's name once the page has had microphone permission (tools/room_players.py grants it), then
  // `setSinkId` on both audio elements sends the sound there. A Bluetooth speaker can come and go, so look again when the
  // devices change and every 20 s while it hasn't been found.
  const sink = { id: "", label: "" };
  async function pinOutput() {
    if (!room || !navigator.mediaDevices) return;
    try {
      const named = async () => (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audiooutput" && d.label);
      let outs = await named();
      if (!outs.length) {
        try { (await navigator.mediaDevices.getUserMedia({ audio: true })).getTracks().forEach(t => t.stop()); } catch (e) { /* no microphone: names stay hidden */ }
        outs = await named();
      }
      const want = new RegExp(room.match, "i");
      const found = outs.find(d => d.deviceId !== "default" && d.deviceId !== "communications" && want.test(d.label));
      if (!found) { sink.id = ""; showNotice(`${room.name}: no audio output here matches "${room.match}" (${outs.map(d => d.label).join("; ") || "no names visible"}). Looking again.`, { kind: "warn", key: "sink" }); return; }
      if (found.deviceId !== sink.id) for (const s of slots) await s.el.setSinkId(found.deviceId);
      sink.id = found.deviceId; sink.label = found.label;
      hideNotice("sink");
    } catch (e) { sink.id = ""; showNotice(`${room.name}: couldn't pin the audio output (${(e && e.message) || e}).`, { kind: "warn", key: "sink" }); }
  }
  if (room) {
    document.title = room.name;
    pinOutput();
    setInterval(() => { if (!sink.id) pinOutput(); }, 20000);
    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) navigator.mediaDevices.addEventListener("devicechange", () => { sink.id = ""; pinOutput(); });
  }

  // ═══ keyboard, buttons ══════════════════════════════════════════════════════════════════════════════════════════
  const typing = t => t && (t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable || (t.tagName === "INPUT" && !["range", "button", "checkbox", "radio"].includes(t.type)));
  document.addEventListener("keydown", e => {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (e.key === "Escape") {
      if (S.letters) { closeLetters(); e.preventDefault(); return; }
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
      case "PageDown": case "PageUp": { e.preventDefault(); const L = S.sheet && S.tab === "queue" ? qList : list; L.go(L.page + (e.key === "PageDown" ? 1 : -1)); break; }
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
      case "home": go("home"); break;
      case "sort": cycleSort(); break;
      case "letters": openLetters(); break;
      case "close-menu": closeMenu(); break;
      case "shield":
        S.shield = !S.shield; savePrefs(); syncToggles();
        if (S.shield) prefetchSoon(); else for (const [, ac] of fetching) ac.abort();
        toast(S.shield ? "Signal shield on: the next songs are kept ready" : "Signal shield off", { ms: 1800 });
        break;
      case "back": goBack(); break;
      case "clear-search": clearSearch(); $("#q").focus(); break;
      case "clear-queue": if (S.q.cur && S.q.length - S.q.qi - 1 > 0) { const n = S.q.length - S.q.qi - 1; S.q.clearUpcoming(); onQueueChanged(); toast(`Cleared ${n.toLocaleString()} from the queue`); } break;
      case "open-artist": { const t = curTrack(), ar = t && S.lib.artistByName.get(t.a); if (ar) go(`artist/${ar.id}`); break; }
      case "open-album": { const t = curTrack(), al = t && albumOf(t); if (al) go(`album/${al.id}`); break; }
    }
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
  addEventListener("online", () => { if (!S.lib || $("#notice").dataset.key === "gate") retryGate(); recoverNow(); prefetchSoon(); });
  if (navigator.connection && navigator.connection.addEventListener) navigator.connection.addEventListener("change", () => { recoverNow(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) recoverNow(); });
  addEventListener("pageshow", () => recoverNow());
  setInterval(() => { if (S.lib && !gate.fresh() && (room || !document.hidden)) gate.ensure().catch(() => {}); }, 5 * 60 * 1000);
  if (window.ResizeObserver) {
    let rt = 0;
    new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => { if (S.lib) { list.resize(); if (S.view && S.view.home) buildDeck(); } }, 120); }).observe($("#stage"));
    new ResizeObserver(() => qList.resize()).observe($("#q-scroll"));      // the queue is measured only once its sheet is on screen
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
  showQueueCount(); syncSignal();
  loadLibrary();
  // exposed for the tests that drive the page by hand (portal/tests/dev_music.py) and for debugging in the console
  window.BilbyApp = { S, actions, gate, slots, list, qList, go, rail, toast, startTrack, tryPlay, renderView, checkin, connect, sink, room, persist, blobs, rec, recoverNow, get lib() { return S.lib; } };
})();
