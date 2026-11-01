/* The Drive page (templates/drive.html, drive.py): the book, two Bilby Shuffles and Skippy, all in this one page.
 *
 * One <audio> element plays either the book or the shuffle. Skippy's voice (static/skippy-voice.js, two modes) ducks it while he hears a turn
 * or speaks (static/skippy-duck.js) and never pauses it. "Skippy" is heard by the trained wake word in the browser (static/skippy-wake.js,
 * models from voice-gate); when that can't load, by the voice's own Whisper-based listening, the chat's Typing mode. A turn goes to the
 * house brain as if typed in the Home chat (answered silently at home, spoken here).
 */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const root = $("#drive"), au = $("#media"), D = window.DrivePure, P = window.BooksPure;
  const cfg = root.dataset;
  const S = { mode: null, q: [], qi: -1, music: null, book: null, books: null, places: null, cur: null, savedAt: 0, pauseAt: 0, lib: null, favs: null };

  const getJSON = async (url, opt) => { const r = await fetch(url, Object.assign({ credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } }, opt)); const d = await r.json(); if (!r.ok && !d.ok) throw new Error(d.why || d.error || r.status); return d; };
  const now = (glyph, text) => { $("#now-g").setAttribute("href", "#g-" + glyph); $("#now-t").textContent = text || ""; };
  const lit = (id, on) => { $(id).dataset.on = on ? "1" : "0"; };
  function paint() {
    lit("#k-book", S.mode === "book"); lit("#k-disc", S.mode === "discovery"); lit("#k-lib", S.mode === "library");
    $("#k-play-g").setAttribute("href", au.paused ? "#g-play" : "#g-pause");
    $("#k-next-g").setAttribute("href", S.mode === "book" ? "#g-fwd30" : "#g-next");
  }

  // ── ducking: this page's own player follows Skippy's voice ──
  let ours = false;                                      // the next pause is this page's own (a key), not the system's
  const duck = window.SkippyDuck ? SkippyDuck.follow({ level: f => { au.volume = Math.max(0, Math.min(1, f)); }, paused: () => au.paused, resume: () => play() }) : null;
  au.addEventListener("pause", () => { if (duck && !au.ended) duck.paused(ours); ours = false; if (S.mode === "book") saveBook(true); paint(); });
  au.addEventListener("play", paint);
  const stopHere = () => { ours = true; au.pause(); };
  async function play() { try { await au.play(); } catch (e) { now("play", "Tap play to start the sound"); } paint(); }

  // ── the book: his current one, from its last place (kept at home, the same place the pods read) ──
  const bu = p => `${S.book.url}/t/${S.book.pass}/${p}`;
  async function bookGate() { if (!S.book || S.book.exp - Date.now() / 1000 < 1800) S.book = await getJSON(cfg.bookGate); return S.book; }
  async function loadBooks() {
    await bookGate();
    if (!S.books) S.books = (await getJSON(bu("catalog.json"))).books;
    S.places = await getJSON(bu("places.json"));
    const id = S.places.current, b = S.books.find(x => x.id === id);
    $("#k-book-t").textContent = b ? b.t : "";
    return b;
  }
  async function playBook() {
    let b;
    try { b = await loadBooks(); } catch (e) { now("book", String(e.message || e)); return; }
    if (!b) { now("book", "No book started yet"); return; }
    S.mode = "book"; S.cur = b; S.savedAt = 0;
    const pos = P.resumeAt((S.places.books || {})[b.id], b.d);
    now("book", b.t + (b.a ? " · " + b.a : ""));
    ours = true; au.src = bu(`a/${b.id}`);
    au.addEventListener("loadedmetadata", () => { au.currentTime = pos; }, { once: true });
    meta(b.t, b.a, "Audiobooks");
    await play();
  }
  async function saveBook(force) {
    const b = S.cur; if (S.mode !== "book" || !b || !au.src || au.readyState < 1) return;
    if (!P.shouldSave(S.savedAt, Date.now(), force)) return;
    S.savedAt = Date.now();
    const pos = au.currentTime || 0;
    const body = { pos, chapter: P.chapterAt(b.ch, pos), speed: 1, finished: !!(au.ended && b.d && b.d - pos < P.DONE_LEFT_S) };
    try { await bookGate(); await fetch(bu(`place/${b.id}`), { method: "PUT", body: JSON.stringify(body), keepalive: true, headers: { "Content-Type": "application/json" } }); }
    catch (e) { S.savedAt = 0; }
  }

  // ── Bilby Shuffle: discovery (everything, favorites mixed in) or library (favorites) ──
  async function musicGate() { if (!S.music || S.music.exp - Date.now() / 1000 < 1800) S.music = await getJSON(cfg.musicGate); return S.music; }
  const mu = (kind, path) => `${S.music.url}/t/${S.music.pass}/${kind}/${path.split("/").map(encodeURIComponent).join("/")}`;
  function idbCatalog(put) {                              // the music app's own saved copy of the catalog (bilby-app.js), same origin
    return new Promise(res => {
      try {
        const r = indexedDB.open("bilby-music", 1);
        r.onupgradeneeded = () => r.result.createObjectStore("kv");
        r.onerror = () => res(null);
        r.onsuccess = () => {
          try {
            const tx = r.result.transaction("kv", put ? "readwrite" : "readonly"), st = tx.objectStore("kv");
            const q = put ? st.put(put, "catalog") : st.get("catalog");
            q.onsuccess = () => res(put ? true : q.result || null); q.onerror = () => res(null);
          } catch (e) { res(null); }
        };
      } catch (e) { res(null); }
    });
  }
  async function library() {
    if (S.lib) return S.lib;
    await musicGate();
    const cached = await idbCatalog();
    const r = await fetch(`${S.music.url}/t/${S.music.pass}/catalog.json`, cached && cached.etag ? { headers: { "If-None-Match": cached.etag } } : {});
    let text;
    if (r.status === 304 && cached) text = cached.text;
    else if (r.ok) { text = await r.text(); idbCatalog({ text, etag: r.headers.get("ETag") || "", at: Date.now() }); }
    else if (cached) text = cached.text;
    else throw new Error(r.status === 503 ? "The library is still being read" : "The music gate said " + r.status);
    const cat = JSON.parse(text), at = {};
    (cat.fields || []).forEach((f, i) => { at[f] = i; });
    S.lib = new Map();
    for (const row of cat.tracks || []) { const p = row[at.p]; if (typeof p === "string" && p) S.lib.set(p, { t: String(row[at.t] || p.split("/").pop()), a: String(row[at.a] || "") }); }
    return S.lib;
  }
  async function shuffle(mode) {
    let lib, favs;
    try { [lib, favs] = await Promise.all([library(), getJSON(cfg.favorites).then(d => d.items || d.favorites || [])]); }
    catch (e) { now("shuffle", String(e.message || e)); return; }
    const fav = favs.map(f => f.track).filter(t => /^spotify:/.test(t) || lib.has(t));
    S.q = D.buildQueue(mode, { all: mode === "discovery" ? Array.from(lib.keys()) : [], favorites: fav, liked: [], suggested: [] });
    S.mode = mode; S.qi = -1;
    if (!S.q.some(it => D.playable(it, false))) { now("shuffle", mode === "library" ? "No favorites yet: the star in Bilby marks one" : "The library is empty"); paint(); return; }
    await nextSong();
  }
  async function nextSong() {
    const i = D.nextPlayable(S.q, S.qi + 1, false);
    if (i < 0) { S.qi = -1; if (S.q.length) return nextSong(); return; }   // round again: a shuffle never just stops
    S.qi = i;
    const it = S.q[i], t = S.lib.get(it.path) || { t: it.path, a: "" };
    try { await musicGate(); } catch (e) { now("shuffle", String(e.message || e)); return; }
    now("shuffle", t.t + (t.a ? " · " + t.a : ""));
    ours = true; au.src = mu("a", it.path);
    meta(t.t, t.a, S.mode === "library" ? "Bilby Shuffle · library" : "Bilby Shuffle · discovery");
    await play();
  }
  au.addEventListener("ended", () => { if (S.mode === "discovery" || S.mode === "library") nextSong(); else if (S.mode === "book") saveBook(true); });
  au.addEventListener("error", () => { if (S.mode === "discovery" || S.mode === "library") setTimeout(nextSong, 800); });
  au.addEventListener("timeupdate", () => {
    $("#now-bar i").style.width = au.duration ? (100 * au.currentTime / au.duration).toFixed(2) + "%" : "0";
    if (S.mode === "book") saveBook(false);
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) saveBook(true); });
  addEventListener("pagehide", () => saveBook(true));

  function meta(title, artist, album) {
    if (!("mediaSession" in navigator)) return;
    try { navigator.mediaSession.metadata = new MediaMetadata({ title: title || "", artist: artist || "", album: album || "" }); } catch (e) { /* old browser */ }
  }
  if ("mediaSession" in navigator) {
    const set = (a, f) => { try { navigator.mediaSession.setActionHandler(a, f); } catch (e) { /* not here */ } };
    set("play", () => play()); set("pause", () => stopHere()); set("nexttrack", () => next());
  }

  // ── the keys ──
  function next() {
    if (S.mode === "book") { au.currentTime = Math.min((au.duration || 1e9) - 1, au.currentTime + 30); saveBook(true); }
    else if (S.mode) nextSong();
  }
  const key = (id, fn) => $(id + " .hit").addEventListener("click", fn);
  key("#k-book", () => { if (S.mode === "book" && !au.paused) stopHere(); else if (S.mode === "book" && au.src) play(); else playBook(); });
  key("#k-disc", () => shuffle("discovery"));
  key("#k-lib", () => shuffle("library"));
  key("#k-play", () => { if (!au.src) return playBook(); if (au.paused) play(); else stopHere(); });
  key("#k-next", next);

  // ── Skippy: the house brain, spoken here ──
  const skey = $("#k-skippy .hit"), sunit = $("#k-skippy");
  new MutationObserver(() => { if (skey.dataset.s) sunit.dataset.s = skey.dataset.s; else delete sunit.dataset.s; sunit.dataset.on = skey.getAttribute("aria-pressed") === "true" ? "1" : "0";
    const g = sunit.querySelector(".g use"); if (g) g.setAttribute("href", skey.getAttribute("aria-pressed") === "true" ? "#g-mic" : "#g-ear"); })
    .observe(skey, { attributes: true, attributeFilter: ["data-s", "aria-pressed"] });
  let earOn = false;
  const voice = SkippyVoice.attach({ gate: cfg.voiceGate, key: skey, status: $("#vstatus"), onSend: ask, wakeWord: false,
                                     onText: t => { if (t) $("#vstatus").title = t; } });
  async function ask(text) {
    try {
      const sent = await getJSON(cfg.send, { method: "POST", body: JSON.stringify({ mode: "say", text, room: "*", speak: false }), headers: { "Content-Type": "application/json", Accept: "application/json" } });
      const t0 = Date.now();
      while (Date.now() - t0 < 120000) {
        await new Promise(r => setTimeout(r, 1000));
        const ev = await getJSON(cfg.events + "?after=" + (sent.seq - 1));
        const got = D.replyAfter(ev.events, sent.seq);
        if (got) { voice.say(got.text || got.why || "Nothing came back"); return; }
        if (!ev.hub && Date.now() - t0 > 8000) { voice.say("The house isn't answering right now."); return; }
      }
      voice.say("The house took too long to answer.");
    } catch (e) { voice.say("I couldn't reach the house."); }
  }

  // The wake word in the browser: the same model the pods use, from voice-gate (the Captain's pass). Without it, the voice's own Whisper
  // listening for "Skippy" (Typing mode) takes over, so the key always works.
  let ear = null;
  async function startEar() {
    if (ear || earOn) return; earOn = true;
    try {
      const g = await getJSON(cfg.voiceGate);
      const base = `${g.url.replace(/\/$/, "")}/t/${g.pass}`;
      const info = await getJSON(base + "/wake.json");
      if (!info.ok) throw new Error("no model");
      if (!window.ort) throw new Error("onnxruntime-web didn't load");
      ort.env.wasm.wasmPaths = cfg.ort; ort.env.wasm.numThreads = 1;
      const buf = n => fetch(`${base}/wake/${n}`).then(r => { if (!r.ok) throw new Error(n); return r.arrayBuffer(); });
      const [mel, emb, word] = await Promise.all(["melspectrogram.onnx", "embedding_model.onnx", "skippy.onnx"].map(buf));
      ear = await SkippyWake.start({ ort, models: { mel, emb, word }, threshold: info.threshold, patience: info.patience, onWake: () => voice.wake("") });
      setInterval(() => ear.pause(voice.chatting()), 200);   // deaf while a conversation is on (his own voice must not wake him)
      $("#k-skippy-t").textContent = "";
    } catch (e) {
      earOn = false; ear = null;
      fallback();
    }
  }
  function fallback() { if (fallback.done) return; fallback.done = true; voice.listenForName && voice.listenForName(); }
  ["pointerdown", "keydown"].forEach(ev => document.addEventListener(ev, () => { if (ear) ear.resume(); else if (micGranted) startEar(); }, { capture: true, passive: true }));
  let micGranted = false;
  try {
    navigator.permissions.query({ name: "microphone" }).then(p => {
      const go = () => { if (p.state === "granted") { micGranted = true; startEar(); } };
      go(); p.onchange = go;
    }).catch(() => {});
  } catch (e) { /* the key's first tap asks */ }
  skey.addEventListener("click", () => { micGranted = true; setTimeout(startEar, 1500); });

  // what the book key would resume, shown under it
  loadBooks().catch(() => {});
  paint();
  window.DriveApp = { S, voice, get ear() { return ear; }, shuffle, playBook, next };
})();
