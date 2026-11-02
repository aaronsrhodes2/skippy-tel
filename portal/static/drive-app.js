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
    const row = S.sp && S.sp.row;
    $("#k-play-g").setAttribute("href", (row ? row.paused : au.paused) ? "#g-play" : "#g-pause");
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
  // His Spotify (liked songs, and suggestions from his liked artists) comes from home; a Spotify row plays on the CAR's own Spotify (drive.py).
  // Without it (home silent, the car's Spotify not open) the shuffle goes on with Bilby's rows alone.
  S.sp = { can: false, info: new Map(), row: null, timer: 0 };
  const postJSON = (url, body) => getJSON(url, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json", Accept: "application/json" } });
  async function spotifyMix() {
    try {
      const m = await getJSON(cfg.spMix);
      for (const t of (m.liked || []).concat(m.suggested || [])) S.sp.info.set(t.uri, { t: t.title, a: t.artist, ms: t.ms || 0 });
      S.sp.can = true;
      return { liked: (m.liked || []).map(t => t.uri), suggested: (m.suggested || []).map(t => t.uri) };
    } catch (e) { S.sp.can = false; return { liked: [], suggested: [] }; }
  }
  async function shuffle(mode) {
    let lib, favs, sp;
    stopCar();
    try { [lib, favs, sp] = await Promise.all([library(), getJSON(cfg.favorites).then(d => d.items || d.favorites || []), spotifyMix()]); }
    catch (e) { now("shuffle", String(e.message || e)); return; }
    for (const f of favs) if (/^spotify:/.test(f.track) && !S.sp.info.has(f.track)) S.sp.info.set(f.track, { t: f.title, a: f.artist, ms: 0 });
    const fav = favs.map(f => f.track).filter(t => /^spotify:/.test(t) || lib.has(t));
    S.q = D.buildQueue(mode, { all: mode === "discovery" ? Array.from(lib.keys()) : [], favorites: fav, liked: sp.liked, suggested: mode === "discovery" ? sp.suggested : [] });
    S.mode = mode; S.qi = -1;
    if (!S.q.some(it => D.playable(it, S.sp.can))) { now("shuffle", mode === "library" ? "No favorites yet: the star in Bilby marks one" : "The library is empty"); paint(); return; }
    await nextSong();
  }
  function stopCar(keep) {                               // the car's Spotify stops when the page moves on to anything else (keep: a Spotify row follows)
    clearInterval(S.sp.timer);
    if (S.sp.row && !keep) postJSON(cfg.spPause, {}).catch(() => {});
    S.sp.row = null;
  }
  async function playOnCar(it) {
    const info = S.sp.info.get(it.uri) || { t: "Spotify", a: "", ms: 0 };
    if (!au.paused) stopHere();
    now("shuffle", info.t + (info.a ? " · " + info.a : "") + " · Spotify in the car");
    try { await postJSON(cfg.spPlay, { uri: it.uri }); }
    catch (e) { S.sp.can = false; S.sp.row = null; now("shuffle", String(e.message || e)); return nextSong(); }   // no car Spotify: Bilby's rows only from here
    S.sp.row = { uri: it.uri, at: Date.now(), ms: info.ms, seen: false, pausedMs: 0, from: 0, paused: false };
    meta(info.t, info.a, "Spotify in the car");
    clearInterval(S.sp.timer);
    S.sp.timer = setInterval(watchCar, 4000);
    paint();
  }
  async function watchCar() {
    const row = S.sp.row; if (!row || row.paused) return;
    let st = null;
    try { st = await getJSON(cfg.spCar); } catch (e) { st = null; }
    if (row !== S.sp.row) return;
    if (st && st.uri === row.uri) { row.seen = true; row.pos = st.pos_ms || 0; if (st.ms) row.ms = st.ms; }
    if (D.carDone(st, row, Date.now())) {
      const n = D.nextPlayable(S.q, S.qi + 1, S.sp.can);
      stopCar(n >= 0 && S.q[n].kind === "spotify");
      nextSong();
    }
  }
  // Skippy spoke over a Spotify row: if the car paused its Spotify for the browser's sound, it plays on when he is done (the page's own player
  // gets the same from skippy-duck.js).
  addEventListener("skippy-duck", e => {
    const row = S.sp.row;
    if (!row || row.paused || (e.detail && e.detail.state)) return;
    setTimeout(async () => {
      if (row !== S.sp.row || row.paused) return;
      try { const st = await getJSON(cfg.spCar); if (st.paused && st.uri === row.uri) postJSON(cfg.spPlay, { uri: row.uri, pos_ms: st.pos_ms || 0 }).catch(() => {}); } catch (x) { /* the car said nothing */ }
    }, 1500);
  });
  async function nextSong() {
    const i = D.nextPlayable(S.q, S.qi + 1, S.sp.can);
    if (i < 0) { S.qi = -1; if (S.q.some(it => D.playable(it, S.sp.can))) return nextSong(); return; }   // round again: a shuffle never just stops
    S.qi = i;
    const it = S.q[i];
    if (it.kind === "spotify") return playOnCar(it);
    stopCar();
    const t = S.lib.get(it.path) || { t: it.path, a: "" };
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
  key("#k-book", () => { stopCar(); if (S.mode === "book" && !au.paused) stopHere(); else if (S.mode === "book" && au.src) play(); else playBook(); });
  key("#k-disc", () => shuffle("discovery"));
  key("#k-lib", () => shuffle("library"));
  $("#k-play .hit.next").addEventListener("click", next);
  key("#k-play", () => {
    const row = S.sp.row;
    if (row) {                                           // a Spotify row: the car's own player pauses, and plays on from where it was
      if (row.paused) { row.pausedMs += Date.now() - row.pausedAt; row.paused = false; postJSON(cfg.spPlay, { uri: row.uri, pos_ms: Math.round(row.pos || 0) }).catch(() => {}); }
      else { row.paused = true; row.pausedAt = Date.now(); postJSON(cfg.spPause, {}).catch(() => {}); }
      paint();
      return;
    }
    if (!au.src) return playBook();
    if (au.paused) play(); else stopHere();
  });

  // ── Skippy: the house brain, spoken here ──
  const skey = $("#k-skippy .hit"), sunit = $("#k-skippy");
  new MutationObserver(() => { if (skey.dataset.s) sunit.dataset.s = skey.dataset.s; else delete sunit.dataset.s; sunit.dataset.on = skey.getAttribute("aria-pressed") === "true" ? "1" : "0"; })
    .observe(skey, { attributes: true, attributeFilter: ["data-s", "aria-pressed"] });
  let earOn = false;
  const voice = SkippyVoice.attach({ gate: cfg.voiceGate, key: skey, status: $("#vstatus"), onSend: ask, wakeWord: false,
                                     onText: t => { if (t) $("#vstatus").title = t; } });
  let asking = false;                                    // a turn is with the house brain: his face thinks
  async function ask(text) {
    asking = true;
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
    finally { asking = false; }
  }

  // ── his face: the pods' own (static/skippy-face.js), in his key, doing what a pod's does ──
  // speaking while his voice plays, thinking while the house brain works, listening in a conversation, offline when skippy-pc is silent.
  let speaking = false, houseDown = false;
  addEventListener("skippy-speaking", e => { speaking = !!e.detail; });
  (function face() {
    const canvas = $("#k-skippy canvas");
    if (!window.SkippyFace || !canvas || !canvas.getContext || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const f = new SkippyFace();
    Promise.all([0, 1, 2, 3, 4].map(i => new Promise((ok, fail) => { const im = new Image(); im.onload = () => ok(im); im.onerror = fail; im.src = cfg.frames + "0" + i + ".png"; })))
      .then(balls => {
        f.balls = balls;
        const ctx = canvas.getContext("2d");
        setInterval(() => {                               // the pod's own 50 ms tick
          if (document.hidden) return;
          const now = performance.now(), chat = voice.chatting(), was = f.talking;
          f.talking = speaking;
          if (f.talking && !was) f.talkT0 = now;
          f.showMic = chat && !speaking && !asking;
          f.setSession(chat);
          f.setState(speaking ? "speaking" : asking ? "thinking" : chat ? "listening" : houseDown ? "offline" : "idle");
          f.tick(now); f.render(ctx);
          sunit.classList.add("live");
        }, 50);
      }, () => { /* the still head stays */ });
  })();

  // ── the house: one lamp per thing he cares about (drive.py house_lamps), every 20 s ──
  const hunit = $("#k-house");
  let house = null;
  async function pollHouse() {
    if (document.hidden) return;
    try { house = await getJSON(cfg.house); } catch (e) { house = { verdict: "down", down: ["the portal"], lamps: [] }; }
    for (const l of house.lamps || []) { const i = hunit.querySelector(`[data-lamp="${l.id}"]`); if (i) { if (l.ok == null) delete i.dataset.ok; else i.dataset.ok = l.ok ? "1" : "0"; } }
    hunit.dataset.s = house.verdict === "ok" ? "on" : house.verdict === "warn" ? "warn" : "off";
    houseDown = house.verdict === "down";
  }
  pollHouse(); setInterval(pollHouse, 20000);
  document.addEventListener("visibilitychange", pollHouse);
  key("#k-house", () => { pollHouse(); });                // lights and symbols only (the Captain): a tap just looks again

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
  window.DriveApp = { S, voice, get ear() { return ear; }, shuffle, playBook, next, pollHouse };
})();
