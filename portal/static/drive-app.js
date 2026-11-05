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
  // skippy-pc's own front door (static/direct.js): a gate pass names it as `direct`, and this page goes through it when it answers /health and through the Funnel when it does not.
  // A call that got no answer from the door is made once more through the Funnel (the door then rests five minutes); an HTTP error from the gate itself is not the door's fault.
  const door = (g, fresh) => (window.SkippyDirect ? SkippyDirect.pick(g, fresh ? { fresh: true } : undefined) : g);
  const noAnswer = e => e instanceof TypeError || e instanceof SyntaxError;
  const leaveDoor = g => !!(window.SkippyDirect && g && SkippyDirect.demote(g));
  async function onceMore(g, call) { try { return await call(); } catch (e) { if (!noAnswer(e) || !leaveDoor(g)) throw e; return call(); } }
  const now = (glyph, text) => { $("#now-g").setAttribute("href", "#g-" + glyph); $("#now-t").textContent = S.nowText = text || ""; };
  const lit = (id, on) => { $(id).dataset.on = on ? "1" : "0"; };
  function paint() {
    lit("#k-book", S.mode === "book"); lit("#k-disc", S.mode === "discovery"); lit("#k-lib", S.mode === "library");
    const row = S.sp && S.sp.row;
    $("#k-play-g").setAttribute("href", (row ? row.paused : au.paused) ? "#g-play" : "#g-pause");
    $("#k-next-g").setAttribute("href", S.mode === "book" ? "#g-fwd30" : "#g-next");
  }

  // ── ducking: this page's own player follows Skippy's voice ──
  let ours = false;                                      // the next pause is this page's own (a key), not the system's
  let warmed = "", warmCtl = null;                        // the song fetched ahead (see warmNext) and the fetch that is still running
  function warmStop() { if (warmCtl) { warmCtl.abort(); warmCtl = null; } }   // an unfinished whole-file download of an address makes the audio element's own request for it wait (Chromium's cache lock, up to 20 s of silence)
  const duck = window.SkippyDuck ? SkippyDuck.follow({ level: f => { au.volume = Math.max(0, Math.min(1, f)); }, paused: () => au.paused, resume: () => play() }) : null;
  au.addEventListener("pause", () => { if (!ours && !au.ended) heard("system-pause"); if (duck && !au.ended) duck.paused(ours); ours = false; if (S.mode === "book") saveBook(true); paint(); });
  au.addEventListener("play", paint);
  const stopHere = () => { ours = true; au.pause(); };
  async function play() {
    try { await au.play(); if (S.tapWaits) $("#now-t").textContent = S.nowText || ""; S.tapWaits = false; }
    catch (e) { S.tapWaits = true; $("#now-t").textContent = (S.label || "") + (S.label ? " · " : "") + "tap anywhere"; }
    paint();
  }

  // ── the book: his current one, from its last place (kept at home, the same place the pods read) ──
  const bu = p => `${S.book.url}/t/${S.book.pass}/${p}`;
  async function bookGate() { if (!S.book || S.book.exp - Date.now() / 1000 < 1800) S.book = await door(await getJSON(cfg.bookGate)); return S.book; }
  async function loadBooks() {
    await bookGate();
    await onceMore(S.book, async () => {
      if (!S.books) S.books = (await getJSON(bu("catalog.json"))).books;
      S.places = await getJSON(bu("places.json"));
    });
    const id = S.places.current, b = S.books.find(x => x.id === id);
    $("#k-book-t").textContent = b ? b.t : "";
    return b;
  }
  async function playBook() {
    let b;
    try { b = await loadBooks(); } catch (e) { now("book", String(e.message || e)); return; }
    if (!b) { now("book", "No book started yet"); return; }
    S.mode = "book"; S.cur = b; S.savedAt = 0; S.savedPos = null;
    const pl = (S.places.books || {})[b.id];             // the ONE current book, at the exact second it was left (the Captain: no rewind)
    const pos = pl && !pl.finished && P.resumeAt(pl, b.d) > 0 ? +pl.pos || 0 : 0;
    S.label = "Resume: " + b.t;
    now("book", b.t + (b.a ? " · " + b.a : ""));
    pauseHouseBook();
    warmStop();
    ours = true; au.src = bu(`a/${b.id}`);
    au.addEventListener("loadedmetadata", () => { au.currentTime = pos; }, { once: true });
    meta(b.t, b.a, "Audiobooks");
    await play();
  }
  // The car's place goes home every few seconds, marked as playing in the car, so the house resumes where the car stopped (and pauses its copy).
  const SAVE_MS = 5000;
  async function saveBook(force) {
    const b = S.cur; if (S.mode !== "book" || !b || !au.src || au.readyState < 1) return;
    if (!force && Date.now() - S.savedAt < SAVE_MS) return;
    const pos = au.currentTime || 0, playing = !au.paused && !au.ended;
    // a save that hasn't moved and isn't a change of playing (a page closed or hidden while paused) would only stamp the book "last played now"
    // and win the next hand-off over Bilby heard since: nothing is sent
    if (S.savedPos != null && Math.abs(pos - S.savedPos) < 0.5 && playing === S.savedPlaying && !au.ended) return;
    S.savedAt = Date.now(); S.savedPos = pos; S.savedPlaying = playing;
    const body = { pos, chapter: P.chapterAt(b.ch, pos), speed: 1, finished: !!(au.ended && b.d && b.d - pos < P.DONE_LEFT_S), src: "car", playing };
    try { await bookGate(); await fetch(bu(`place/${b.id}`), { method: "PUT", body: JSON.stringify(body), keepalive: true, headers: { "Content-Type": "application/json" } }); }
    catch (e) { S.savedAt = 0; if (!document.hidden && noAnswer(e)) leaveDoor(S.book); }      // (a page going away fails its last save for its own reasons: that is not the door's)
  }

  async function pauseHouseBook() {                       // a pod reading this book aloud at home stops: the book is in the car now
    try {
      const r = await getJSON(cfg.rooms);
      for (const d of r.rooms || []) if (d.live && d.book && d.book.playing) postJSON(cfg.roomCmd, { device: d.id, action: "book:pause" }).catch(() => {});
    } catch (e) { /* home silent: the "playing in the car" mark on the place is the house's other cue */ }
  }

  // ── Bilby Shuffle: discovery (what he has not heard) or library (his one favorites pool) ──
  async function musicGate() { if (!S.music || S.music.exp - Date.now() / 1000 < 1800) S.music = await door(await getJSON(cfg.musicGate)); return S.music; }
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
    const r = await onceMore(S.music, () => fetch(`${S.music.url}/t/${S.music.pass}/catalog.json`, cached && cached.etag ? { headers: { "If-None-Match": cached.etag } } : {}));
    let text;
    if (r.status === 304 && cached) text = cached.text;
    else if (r.ok) { text = await r.text(); idbCatalog({ text, etag: r.headers.get("ETag") || "", at: Date.now() }); }
    else if (cached) text = cached.text;
    else throw new Error(r.status === 503 ? "The library is still being read" : "The music gate said " + r.status);
    const cat = JSON.parse(text), at = {};
    (cat.fields || []).forEach((f, i) => { at[f] = i; });
    S.lib = new Map();
    for (const row of cat.tracks || []) { const p = row[at.p]; if (typeof p === "string" && p) S.lib.set(p, { t: String(row[at.t] || p.split("/").pop()), a: String(row[at.a] || ""), g: String(at.g >= 0 ? row[at.g] || "" : "") }); }
    return S.lib;
  }
  // His Spotify (liked songs, and suggestions from his liked artists) comes from home; a Spotify row plays on the CAR's own Spotify (drive.py).
  // Without it (home silent, the car's Spotify not open) the shuffle goes on with Bilby's rows alone.
  S.sp = { can: false, info: new Map(), row: null, timer: 0 };
  const postJSON = (url, body) => getJSON(url, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json", Accept: "application/json" } });
  // The mix takes home ~20 s to build the first time (then it keeps it 30 min): it is asked for as the page opens, kept here 25 min, and a
  // shuffle never waits for it: Bilby's songs start at once and the Spotify rows join the queue when the mix arrives (SP_WAIT_MS).
  const SP_WAIT_MS = 2500, SP_KEEP_MS = 25 * 60 * 1000, CAR_RETRY_MS = 10 * 60 * 1000;
  // Off (drive.py SPOTIFY_IN_CAR, the Captain, 2026-11-05): the car plays Bilby's library alone. A Spotify row switches the Tesla's sound between the browser and its own
  // Spotify app through the portal and skippy-pc; the mix is not even asked for, and S.sp.can stays false, so no Spotify row is ever playable here.
  const SP_ON = cfg.spOn === "1";
  const NO_SP = { liked: [], suggested: [] };
  function spotifyMix() {
    if (!SP_ON) return Promise.resolve(NO_SP);
    if (S.sp.mix && Date.now() - S.sp.mixAt < SP_KEEP_MS) return S.sp.mix;
    S.sp.mixAt = Date.now();
    S.sp.mix = getJSON(cfg.spMix).then(m => {
      for (const t of (m.liked || []).concat(m.suggested || [])) S.sp.info.set(t.uri, { t: t.title, a: t.artist, ms: t.ms || 0 });
      S.sp.can = true;
      return { liked: (m.liked || []).map(t => t.uri), suggested: (m.suggested || []).map(t => t.uri) };
    }, () => { S.sp.can = false; S.sp.mix = null; return { liked: [], suggested: [] }; });
    return S.sp.mix;
  }
  async function shuffle(mode, first) {
    let lib, pool;
    stopCar();
    const mixing = spotifyMix();
    try { [lib, pool] = await Promise.all([library(), getJSON(cfg.favorites)]); }
    catch (e) { now("shuffle", String(e.message || e)); return; }
    let sp = await Promise.race([mixing, new Promise(r => setTimeout(() => r(null), SP_WAIT_MS))]);
    const tok = S.shuffleTok = (S.shuffleTok || 0) + 1;
    build(mode, lib, pool, sp || NO_SP);
    if (!sp && !S.q.some(it => D.playable(it, true))) {   // nothing of Bilby's to start with (no favorites yet): this queue is all Spotify, wait for it
      now("shuffle", "");
      sp = await mixing;
      if (tok !== S.shuffleTok) return;
      build(mode, lib, pool, sp);
    }
    if (!sp) mixing.then(late => {                        // the Spotify rows arrive while Bilby already plays: the rest of the queue is rebuilt with them
      if (tok !== S.shuffleTok || S.mode !== mode || !late.liked.length && !late.suggested.length) return;
      const done = S.q.slice(0, S.qi + 1), had = new Set(done.map(it => it.path || it.uri));
      const rest = build(mode, lib, pool, late, true).filter(it => !had.has(it.path || it.uri));
      S.q = done.concat(rest);
    });
    if (first && first.track && (/^spotify:/.test(first.track) || lib.has(first.track))) {   // the hand-off: that song first, where it was
      const k = first.track;
      S.q = [/^spotify:/.test(k) ? { kind: "spotify", uri: k } : { kind: "bilby", path: k }].concat(S.q.filter(it => (it.path || it.uri) !== k));
      S.startAt = /^spotify:/.test(k) ? 0 : +first.pos || 0;
    }
    if (!S.q.some(it => D.playable(it, S.sp.can))) {      // checked BEFORE the mode changes (the review, 2026-11-05): what was playing goes on under its own mode, and the words say why
      now("shuffle", S.q.length ? "Those are Spotify songs, and Spotify is off in the car" : mode === "library" ? "No favorites yet: the star in Bilby marks one" : "The library is empty");
      paint(); return;
    }
    S.label = "Bilby: " + mode;
    S.mode = mode; S.qi = -1;
    await nextSong();
  }
  function build(mode, lib, pool, sp, only) {          // only: return the queue without making it the current one
    const favs = pool.items || pool.favorites || [];
    for (const f of favs) if (/^spotify:/.test(f.track) && !S.sp.info.has(f.track)) S.sp.info.set(f.track, { t: f.title, a: f.artist, ms: 0 });
    const fav = favs.map(f => f.track).filter(t => /^spotify:/.test(t) || lib.has(t));
    // a song's genre: its own tag, else its artist's in Bilby (a Spotify row has only its artist): his genre boundaries for discovery
    const byArtist = new Map();
    for (const t of lib.values()) if (t.g && !byArtist.has(t.a.toLowerCase())) byArtist.set(t.a.toLowerCase(), t.g);
    const genre = k => { const t = lib.get(k); if (t) return t.g || byArtist.get(t.a.toLowerCase()) || "";
                         const i = S.sp.info.get(k); return i ? byArtist.get(String(i.a || "").split(", ")[0].toLowerCase()) || "" : ""; };
    const mine = items => D.localFirst(items, u => S.sp.info.get(u), lib);   // a Spotify song he owns plays from Bilby: no Spotify, no rate limit
    const q = D.buildQueue(mode, { all: mode === "discovery" ? Array.from(lib.keys()) : [], favorites: mine(fav), liked: mine(sp.liked),
                                   suggested: mode === "discovery" ? mine(sp.suggested) : [], played: pool.played || [], tired: pool.tired || [], genre });
    if (!only) S.q = q;
    return q;
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
    catch (e) { S.sp.can = false; S.sp.offAt = Date.now(); S.sp.row = null; now("shuffle", String(e.message || e)); return nextSong(); }   // no car Spotify: Bilby's rows for a while
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
    if (!S.sp.can && S.sp.offAt && Date.now() - S.sp.offAt > CAR_RETRY_MS) { S.sp.can = true; S.sp.offAt = 0; }   // Spotify opened in the car since: try it again
    const i = D.nextPlayable(S.q, S.qi + 1, S.sp.can);
    if (i < 0) { S.qi = -1; if (S.q.some(it => D.playable(it, S.sp.can))) return nextSong(); return; }   // round again: a shuffle never just stops
    S.qi = i;
    const it = S.q[i];
    if (it.kind === "spotify") return playOnCar(it);
    stopCar();
    const t = S.lib.get(it.path) || { t: it.path, a: "" };
    try { await musicGate(); } catch (e) { now("shuffle", String(e.message || e)); return; }
    now("shuffle", t.t + (t.a ? " · " + t.a : ""));
    warmStop();
    ours = true; au.src = mu("a", it.path);
    if (S.startAt > 0) { const at = S.startAt; au.addEventListener("loadedmetadata", () => { au.currentTime = at; }, { once: true }); }
    S.startAt = 0;
    meta(t.t, t.a, S.mode === "library" ? "Bilby Shuffle · library" : "Bilby Shuffle · discovery");
    await play();
  }
  au.addEventListener("ended", () => { if (S.mode === "discovery" || S.mode === "library") nextSong(); else if (S.mode === "book") saveBook(true); });
  // The door gave out under a song or a book: the same one goes on from the Funnel, from where it stopped (static/direct.js). False when it was not on the door.
  function resumeOnFunnel() {
    const music = S.mode === "discovery" || S.mode === "library";
    if (!music && S.mode !== "book") return false;
    const it = S.q[S.qi];
    if (music ? !(it && it.path) : !S.cur) return false;
    if (!leaveDoor(music ? S.music : S.book)) return false;
    const at = au.currentTime || 0;
    warmStop(); ours = true; au.src = music ? mu("a", it.path) : bu(`a/${S.cur.id}`);
    if (at > 0) au.addEventListener("loadedmetadata", () => { au.currentTime = at; }, { once: true });
    play();
    return true;
  }
  au.addEventListener("error", () => { if (resumeOnFunnel()) return; if (S.mode === "discovery" || S.mode === "library") setTimeout(nextSong, 800); });
  au.addEventListener("timeupdate", () => {
    $("#now-bar i").style.width = au.duration ? (100 * au.currentTime / au.duration).toFixed(2) + "%" : "0";
    if (S.mode === "book") saveBook(false);
    const it = S.q[S.qi];                                // heard: a Bilby song past 30 s goes on the played list, so discovery moves on from it
    if ((S.mode === "discovery" || S.mode === "library") && it && it.kind === "bilby" && au.currentTime > 30 && S.heard !== it.path) {
      S.heard = it.path;
      postJSON(cfg.played, { track: it.path }).catch(() => {});
    }
    if ((S.mode === "discovery" || S.mode === "library") && au.currentTime > 8) warmNext();
  });
  // The next song is fetched whole while this one plays, at low priority (the Captain, 2026-11-05: "throughput problems to the car"): on a poor link a new song costs a new
  // connection (seconds of handshake through a slow Funnel ingress) and a cold start; fetched ahead it is in the browser's cache, under the same address, when `ended` sets it
  // as the source. Nothing about playing changes: a miss is just today's cold start. Once per song; a failure is not retried (the link is poor, the current song comes first).
  // Two rules from the review (2026-11-05, both reproduced in Chrome 156): it starts only once THIS song is wholly downloaded (at 0.5 Mbit/s two downloads left both under the 40 KB/s a
  // 320 kbps song needs: 7 stalls in 100 s), and it is aborted before the audio element is given a new source (otherwise the element's Range request for the same address waited for it).
  function warmNext() {
    const path = D.warmTarget(S.q, S.qi, S.sp.can);
    if (!path || path === warmed || !S.music) return;
    const b = au.buffered;
    if (!D.songFullyBuffered(au.duration, b && b.length ? b.end(b.length - 1) : 0)) return;
    warmed = path;
    const ctl = warmCtl = new AbortController();
    fetch(mu("a", path), { priority: "low", signal: ctl.signal }).then(r => (r.ok ? r.arrayBuffer() : null)).catch(() => {}).finally(() => { if (warmCtl === ctl) warmCtl = null; });
  }
  document.addEventListener("visibilitychange", () => { if (document.hidden) saveBook(true); });
  addEventListener("pagehide", () => saveBook(true));

  function meta(title, artist, album) {
    if (!("mediaSession" in navigator)) return;
    try { navigator.mediaSession.metadata = new MediaMetadata({ title: title || "", artist: artist || "", album: album || "" }); } catch (e) { /* old browser */ }
  }
  // The steering wheel's left knob (the Captain, 2026-11-02): a press pauses and plays, but a click forward didn't skip. Which signal
  // the Tesla's browser sends for it isn't documented, so every likely one skips (the media session's next track and seek forward,
  // the media keys as keypresses) and back goes to the song's start (30 s back in a book). Each arrival is counted as drive:in-<what>
  // (telemetry, no song names), so one drive tells which signal the knob really sends.
  const heard = what => {
    try { navigator.sendBeacon(cfg.tap, new Blob([JSON.stringify({ e: "drive:in-" + what, p: location.pathname })], { type: "application/json" })); }
    catch (e) { /* no beacon: nothing counted */ }
  };
  function back() {
    if (S.mode === "book") { au.currentTime = Math.max(0, au.currentTime - 30); saveBook(true); }
    else if (S.mode && au.src) au.currentTime = 0;
  }
  if ("mediaSession" in navigator) {
    const set = (a, f) => { try { navigator.mediaSession.setActionHandler(a, () => { heard(a); f(); }); } catch (e) { /* not here */ } };
    set("play", () => play()); set("pause", () => stopHere());
    set("nexttrack", () => next()); set("seekforward", () => next());
    set("previoustrack", () => back()); set("seekbackward", () => back());
  }
  const KNOB = { MediaTrackNext: next, MediaFastForward: next, MediaTrackPrevious: back, MediaRewind: back };
  document.addEventListener("keydown", e => {
    if (!/^(Media|Audio|Arrow)/.test(e.key)) return;
    heard("key-" + e.key.toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 30));
    if (KNOB[e.key]) { e.preventDefault(); KNOB[e.key](); }
  });

  // ── the keys ──
  function next() {
    if (S.mode === "book") { au.currentTime = Math.min((au.duration || 1e9) - 1, au.currentTime + 30); saveBook(true); }
    else if (S.mode) nextSong();
  }
  const key = (id, fn) => $(id + " .hit").addEventListener("click", fn);
  key("#k-book", () => { stopCar(); if (S.mode === "book" && !au.paused) stopHere(); else if (S.mode === "book" && au.src) play(); else playBook(); });
  // a clicker that takes a moment says so (keys.js): the shuffles wait on the library and Spotify's mix before the first song
  const busy = (id, work) => window.SkippyKeys ? SkippyKeys.busy($(id), work) : work();
  key("#k-disc", () => busy("#k-disc", () => shuffle("discovery")));
  key("#k-lib", () => busy("#k-lib", () => shuffle("library")));
  // the star: this song into his favorites (the pool "Shuffle library" plays), Bilby's or a Spotify row
  $("#k-play .hit.fav").addEventListener("click", () => busy("#k-play", async () => {
    const it = (S.mode === "discovery" || S.mode === "library") ? S.q[S.qi] : null;
    if (!it) { now("star", "Nothing playing to add: favorites are songs, not the book"); return; }
    const track = it.path || it.uri;
    const info = it.kind === "spotify" ? (S.sp.info.get(it.uri) || {}) : (S.lib.get(it.path) || {});
    try {
      await postJSON(cfg.favAdd, { track, title: info.t || "", artist: info.a || "", name: "the car" });
      $("#k-play").dataset.s = "on"; setTimeout(() => { delete $("#k-play").dataset.s; }, 2500);
      now("star", "In your favorites: " + (info.t || track) + (info.a ? " · " + info.a : ""));
    } catch (e) { $("#k-play").dataset.s = "warn"; setTimeout(() => { delete $("#k-play").dataset.s; }, 4000); now("star", "Not saved: " + (e.message || e)); }
  }));
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

  // ── captions, on /hud: other people's speech in another language (heard) and what the glasses see (seen) ──
  // The original words first, then the English taking their place word by word (D.captionFrames), so there is a moment to read them
  // (the Captain, 2026-11-02). At most three rows; a finished row fades after D.CAPTION_FADE_MS. `heardLog` is the speech purple is told
  // about with the next message (D.heardNote): he is the one who answers, and he should know someone was talking near the Captain.
  const heardBox = $("#heard"), heardLog = [];
  function addCaption(c) {
    if (!heardBox || !String(c.en || "").trim()) return null;
    const row = document.createElement("div"); row.className = "turn heard";
    const g = document.createElementNS("http://www.w3.org/2000/svg", "svg"); g.setAttribute("class", "g"); g.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", c.kind === "seen" ? "#g-eye" : "#g-globe"); g.appendChild(use);
    const tx = document.createElement("div"); tx.className = "tx";
    const who = document.createElement("div"); who.className = "who"; who.textContent = c.kind + " · " + D.langName(c.lang || "") + " → English";
    const line = document.createElement("div"); line.className = "line";
    const enEl = document.createElement("span"), origEl = document.createElement("span"); enEl.className = "en"; origEl.className = "orig";
    line.append(enEl, document.createTextNode(" "), origEl); tx.append(who, line); row.append(g, tx);
    heardBox.prepend(row); pin(row);
    while (heardBox.children.length > 3) heardBox.lastChild.remove();
    const frames = D.captionFrames(c.orig, c.en);
    let i = 0;
    const play = () => {
      if (!row.isConnected) return;
      enEl.textContent = frames[i].en; origEl.textContent = frames[i].orig;
      if (i + 1 < frames.length) { const wait = i === 0 ? D.captionHoldMs(c.orig) : D.CAPTION_STEP_MS; i++; setTimeout(play, wait); }
      else setTimeout(() => { row.classList.add("gone"); setTimeout(() => row.remove(), 700); }, D.CAPTION_FADE_MS);
    };
    play();
    return row;
  }
  function onHeard(h) {
    heardLog.push({ text: h.text, lang: h.lang }); if (heardLog.length > 10) heardLog.shift();
    addCaption({ orig: h.raw, en: h.text, lang: h.lang, kind: "heard" });
  }

  const voice = SkippyVoice.attach(Object.assign({ gate: cfg.voiceGate, key: skey, status: $("#vstatus"), onSend: ask, wakeWord: false,
                                     onText: t => { if (t) $("#vstatus").title = t; } },
                                     heardBox ? { onHeard, wantOriginal: true } : {}));        // /hud only: in the car, foreign speech stays a message to Skippy
  let asking = false;                                    // a turn is with the house brain: his face thinks
  // /hud (hud.py): purple Skippy, the agent on skippy-pc with the full power, is the brain behind the voice. One conversation, kept
  // in this browser and started afresh after HUD_CONV_IDLE_MS quiet, so he can follow up; the reply's last text is what is spoken
  // (as the agent's chat does). Its brain is the agent's own default (Private: Skippy Core).
  const HUD_CONV_IDLE_MS = 2 * 3600 * 1000;
  function hudConv() {
    let c = null; try { c = JSON.parse(localStorage.getItem("hud-conv") || "null"); } catch (e) { /* none */ }
    if (!c || !c.id || Date.now() - c.at > HUD_CONV_IDLE_MS) c = { id: (crypto.randomUUID ? crypto.randomUUID() : "h" + Date.now().toString(16) + Math.random().toString(16).slice(2, 10)).replace(/[^A-Za-z0-9-]/g, "") };
    c.at = Date.now(); try { localStorage.setItem("hud-conv", JSON.stringify(c)); } catch (e) { /* this load only */ }
    return c.id;
  }
  function said(text) { const s = $("#said"); if (!s) return; s.textContent = text || ""; s.hidden = !text; }
  async function askPurple(text) {
    asking = true; said("");
    const H = { "Content-Type": "application/json", Accept: "application/json", "X-Workshop": "1" };
    const note = D.heardNote(heardLog); heardLog.length = 0;           // what was said near him since his last message rides ahead of it
    try {
      const r = await fetch(cfg.wsSend, { method: "POST", credentials: "same-origin", headers: H,
                                          body: JSON.stringify({ message: note + text, conv: hudConv(), project: "", client_id: hudConv() + "-" + Date.now() }) });
      const d = await r.json();
      if (!r.ok) { voice.say(d.error || "Purple Skippy isn't answering."); return; }
      let after = 0, last = "";
      const t0 = Date.now();
      while (Date.now() - t0 < 15 * 60 * 1000) {
        await new Promise(res => setTimeout(res, 1200));
        const ev = await (await fetch(cfg.wsEvents + "?task=" + encodeURIComponent(d.task) + "&after=" + after, { credentials: "same-origin", headers: H })).json();
        for (const e of ev.events || []) if (e && e.t === "text" && String(e.text || "").trim()) last = e.text;
        after = ev.next || after;
        if (last) said(last);
        if (!/^(queued|running|waiting)$/.test(ev.state || "")) break;
      }
      voice.say(last || "Done, with nothing to say.");
    } catch (e) { voice.say("I couldn't reach purple Skippy."); }
    finally { asking = false; }
  }

  async function ask(text) {
    const fw = vision && V.flashWord(text);                // "snap" / "snap it big time" are the camera key's words: never sent to Skippy
    if (fw) { snap(fw === "hold", "word"); return; }
    const hw = V && V.hostWord(text);                      // "record a walk" / "stop recording": the glasses' recorder, never sent to Skippy
    if (hw) { hostRecord(hw === "record-on"); return; }
    const rw = vision && V.readWord(text);                 // "read that", "what am I looking at", "steady on": about what he sees, never sent to Skippy
    if (rw) { viewWord(rw); return; }
    if (cfg.wsSend) return askPurple(text);
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
    let g = null;
    try {
      g = await door(await getJSON(cfg.voiceGate));
      const base = `${g.url.replace(/\/$/, "")}/t/${g.pass}`;
      const info = await getJSON(base + "/wake.json");
      if (!info.ok) throw new Error("no model");
      if (!window.ort) throw new Error("onnxruntime-web didn't load");
      ort.env.wasm.wasmPaths = cfg.ort; ort.env.wasm.numThreads = 1;
      const buf = n => fetch(`${base}/wake/${n}`).then(r => { if (!r.ok) throw new Error(n); return r.arrayBuffer(); });
      const [mel, emb, word] = await Promise.all(["melspectrogram.onnx", "embedding_model.onnx", "skippy.onnx"].map(buf));
      ear = await SkippyWake.start({ ort, models: { mel, emb, word }, threshold: info.threshold, patience: info.patience, onWake: () => voice.wake("") });
      const mine = ear;
      setInterval(() => mine.pause(voice.chatting()), 200);  // deaf while a conversation is on (his own voice must not wake him)
    } catch (e) {
      earOn = false; ear = null;
      if (noAnswer(e) && leaveDoor(g)) { startEar(); return; }      // the door gave no answer: once more through the Funnel before the voice's own listening takes over
      fallback();
    }
  }
  function fallback() { if (fallback.done) return; fallback.done = true; voice.listenForName && voice.listenForName(); }
  ["pointerdown", "keydown"].forEach(ev => document.addEventListener(ev, () => { if (ear) ear.resume(); else if (micGranted) startEar(); }, { capture: true, passive: true }));
  let micGranted = false;
  // hub mode (/hud): every key goes, his face fills the screen, and the wake word listens with no touch, like a pod
  const hub = $("#k-hub");
  if (hub) {
    const setHub = on => {
      root.classList.toggle("hub", on); hub.setAttribute("aria-pressed", on ? "true" : "false");
      try { localStorage.setItem("hud-hub", on ? "1" : "0"); } catch (e) { /* this load only */ }
      if (on && !ear) { micGranted = true; startEar(); }
    };
    hub.addEventListener("click", () => setHub(!root.classList.contains("hub")));
    let was = false; try { was = localStorage.getItem("hud-hub") === "1"; } catch (e) { /* off */ }
    if (was) setHub(true);
  }
  try {
    navigator.permissions.query({ name: "microphone" }).then(p => {
      const go = () => { if (p.state === "granted") { micGranted = true; startEar(); } };
      go(); p.onchange = go;
    }).catch(() => {});
  } catch (e) { /* the key's first tap asks */ }
  skey.addEventListener("click", () => { micGranted = true; setTimeout(startEar, 1500); });

  // what the book key would resume, shown under it; and the Spotify mix, so the first shuffle has it
  spotifyMix();
  // The hand-off (the Captain): getting into the car continues what he heard last, the current book or Bilby, with no choosing. A key pressed
  // before it is decided wins; a browser that wants a tap first plays it on the first tap anywhere outside a key, and says what it will be.
  let handTok = 0;
  ["pointerdown", "keydown"].forEach(ev => document.addEventListener(ev, e => {
    if (e.target && e.target.closest && e.target.closest(".hit")) { handTok++; S.tapWaits = false; return; }   // a key: its own action, no hand-off
    if (S.tapWaits && au.src && au.paused) { S.tapWaits = false; play(); }
  }, { capture: true }));
  (async function handOff() {
    const mine = ++handTok;
    let places = null, last = null, pool = null;
    try { await loadBooks(); places = S.places; } catch (e) { places = null; }
    try { last = (await getJSON(cfg.last)).bilby; } catch (e) { last = null; }
    try { pool = await getJSON(cfg.favorites); } catch (e) { pool = null; }
    if (mine !== handTok) return;
    const favs = new Set(((pool && (pool.items || pool.favorites)) || []).map(f => f.track));
    const pick = D.handOff(places, last, t => favs.has(t));
    S.label = pick.kind === "book" ? "Resume: " + ((S.books || []).find(b => b.id === pick.id) || { t: "" }).t : "Bilby: " + pick.mode;
    now(pick.kind === "book" ? "book" : "shuffle", S.label);
    if (pick.kind === "book") await playBook();
    else await shuffle(pick.mode, pick.track ? { track: pick.track, pos: pick.pos } : null);
  })();
  // ── Skippy-vision (static/vision.js, portal/vision.py): one flash of every screen and camera (the Captain, 2026-11-02) ──
  // /hud has the camera key: a tap saves a moment, a press of V.HOLD_MS saves it with the glasses' 7 s before and 3 s after; the words
  // "snap" and "snap it big time" do the same. It says "Saved." (aloud in hub mode) and offers the trash key for 10 s: Forget.
  // The Tesla's Drive page (/drive) has no key: it joins a moment made in the last minute by adding its own picture.
  const V = window.SkippyVision, flashKey = $("#k-flash"), forgetKey = $("#k-forget");
  let lastMoment = null, forgetTimer = 0, sayTimer = 0;
  function note(text, ms) {                              // a line under the keys that fades; in hub mode there is no screen to read, so it is spoken
    said(text); clearTimeout(sayTimer);
    if (text) sayTimer = setTimeout(() => { const s = $("#said"); if (s && s.textContent === text) said(""); }, ms || 4000);
    if (text && root.classList.contains("hub")) voice.say(text);
  }
  const vision = V && cfg.vision ? V.attach({
    flash: cfg.vision, read: cfg.visionRead, look: cfg.visionLook, momentAdd: cfg.visionAdd, momentDelete: cfg.visionDelete, momentKeep: cfg.visionKeep,
    html2canvas: cfg.snapshotLib, pageName: "hud-page.jpg",
    say: text => note(text),
    onSaved: (moment, picture) => {
      lastMoment = moment; note("Saved.");
      if (forgetKey) { forgetKey.hidden = false; clearTimeout(forgetTimer); forgetTimer = setTimeout(() => { forgetKey.hidden = true; }, 10000); }
      describeMoment(moment.id, picture);
    },
  }) : null;

  // ── reading what he sees (portal/vision.py /vision/read and /vision/look, Skippy Core's vision slot) ──
  // After every flash the moment is read for writing in another language (a caption for each, and kept in the moment) and described in one
  // sentence (kept too). "Read that" does the first now, "what am I looking at" the second, aloud in hub mode; "steady on" reads by itself
  // every five seconds while the glasses are worn in hub mode (voice only, off until asked, and eased off by the heat: see the host's thermal).
  const seenRecent = new Map();                          // a sign in view is not read out again every few seconds
  async function readView(picture, momentId, aloud) {
    if (!vision || !picture) return [];
    const got = await vision.readSigns(picture, momentId);
    if (got === null) { if (aloud) note("I couldn't read that."); return []; }
    const fresh = got.filter(s => { const t = seenRecent.get(s.en); if (t && Date.now() - t < 60000) return false; seenRecent.set(s.en, Date.now()); return true; });
    fresh.forEach(s => addCaption({ orig: s.text, en: s.en, lang: s.lang, kind: "seen" }));
    if (aloud) note(fresh.length ? fresh.map(s => s.en).join(". ") : got.length ? "Still the same signs." : "Nothing in another language.", 8000);
    return fresh;
  }
  async function describeMoment(id, picture) {
    if (!picture) return;
    await readView(picture, id, false);
    await vision.lookAt(picture, id);                    // (kept in the moment: Your chats > Moments shows it)
  }
  let steadyTimer = 0, steadyLast = "";
  function setSteady(on, say) {
    clearInterval(steadyTimer); steadyTimer = 0; steadyLast = "";
    try { localStorage.setItem("hud-steady", on ? "1" : "0"); } catch (e) { /* this load only */ }
    try { if (window.OpenViture && OpenViture.steady) OpenViture.steady(on); } catch (e) { /* the host keeps it across a reload; no host, no matter */ }
    if (on) steadyTimer = setInterval(async () => {
      if (document.hidden || !root.classList.contains("hub") || !vision || !vision.hosted()) return;      // only while worn, in hub mode
      const picture = await vision.frame();
      if (!picture || picture === steadyLast) return;
      steadyLast = picture;
      readView(picture, null, false);
    }, 5000);
    if (say) note(on ? "Steady reading on, every five seconds." : "Steady reading off.");
  }
  async function viewWord(word) {                        // a spoken word about what he sees: never sent to Skippy
    if (word === "steady-on" || word === "steady-off") { setSteady(word === "steady-on", true); return; }
    const picture = await vision.frame();
    if (!picture) { note("I have no camera to look through."); return; }
    if (word === "look") note((await vision.lookAt(picture)) || "I couldn't tell.", 12000);
    else await readView(picture, null, true);
  }
  // ── captions pinned in the world (glasses only): a row stays where it was made while the head turns and is hidden beyond about half the view
  // (D.pinOffset). Without the host or a pose lock a row simply stays on the screen ──
  const pinned = new Set();
  const yawNow = () => { try { return D.yawDeg(JSON.parse(OpenViture.pose())); } catch (e) { return null; } };
  function pin(row) {
    if (!(window.OpenViture && OpenViture.pose)) return;
    const yaw = yawNow();
    if (yaw !== null) { row._yaw = yaw; pinned.add(row); }
  }
  if (window.OpenViture && OpenViture.pose) setInterval(() => {
    if (!pinned.size || document.hidden) return;
    const yaw = yawNow();
    if (yaw === null) return;
    for (const row of pinned) {
      if (!row.isConnected) { pinned.delete(row); continue; }
      const p = D.pinOffset(yaw, row._yaw, innerWidth);
      row.style.transform = p.show ? "translateX(" + Math.round(p.dx) + "px)" : "none"; row.style.visibility = p.show ? "visible" : "hidden";
    }
  }, 50);

  // ── the walk recorder (the Captain: the backyard, later): the host writes the cameras' frames and the head's pose to the phone while it records ──
  function hostRecord(on) {
    if (!(window.OpenViture && OpenViture.record)) { note("Recording a walk needs the glasses."); return; }
    let ok = false;
    try { ok = JSON.parse(OpenViture.record(on)) === true; } catch (e) { /* none */ }
    note(ok ? (on ? "Recording the walk." : "Walk saved on the phone.") : (on ? "I couldn't start recording." : "I wasn't recording."));
  }

  try {
    const hostSteady = window.OpenViture && OpenViture.status ? JSON.parse(OpenViture.status()).steady === true : false;      // the host remembers it across a reload
    if (hostSteady || localStorage.getItem("hud-steady") === "1") setSteady(true, false);
  } catch (e) { /* off */ }

  // ── keep the screen on while /hud is in front: a phone dims and locks mid-sentence otherwise ──
  if (cfg.wsSend && navigator.wakeLock) {
    let lock = null;
    const hold = async () => {
      if (document.hidden || lock) return;
      try { lock = await navigator.wakeLock.request("screen"); lock.addEventListener("release", () => { lock = null; }); } catch (e) { lock = null; }
    };
    hold(); document.addEventListener("visibilitychange", hold);
  }

  // ── the lamp in the strip: the phone's charge and, on the glasses' host, how hot it is (the Captain: watch heat and battery) ──
  // At a SEVERE thermal status or worse the camera eases off to 10 fps, steady reading stops and he is told once in ten minutes; it goes back
  // to full when the phone is cool again. Without the host (the phone's own browser) the lamp is the charge alone.
  const batt = $("#k-batt"), lamp = { pct: null, charging: false, temp: null, status: 0 };
  if (batt) {
    const hosted = !!(window.OpenViture && OpenViture.thermal);
    let easedByHeat = false, heatTold = 0;
    const paintLamp = () => {
      batt.dataset.lvl = D.battLevel(lamp.pct, lamp.charging); batt.dataset.heat = D.heatLevel(lamp.status, lamp.temp);
      batt.querySelector("b").textContent = D.lampText(lamp.pct, lamp.charging, lamp.temp, hosted);
      batt.title = "Battery" + (lamp.pct == null ? "" : " " + Math.round(lamp.pct) + "%") + (lamp.charging ? ", charging" : "") + (batt.dataset.heat === "hot" ? ", hot" : batt.dataset.heat === "warm" ? ", warm" : "");
    };
    if (navigator.getBattery) navigator.getBattery().then(b => {
      const upd = () => { lamp.pct = b.level * 100; lamp.charging = !!b.charging; paintLamp(); };
      upd(); b.addEventListener("levelchange", upd); b.addEventListener("chargingchange", upd);
    }).catch(() => {});
    if (hosted) {
      const relieve = () => {
        if (lamp.status >= D.HOT_STATUS) {
          if (!easedByHeat) { easedByHeat = true; try { OpenViture.easeOff(true); } catch (e) { /* no host */ } setSteady(false, false); }
          if (Date.now() - heatTold > 10 * 60 * 1000) { heatTold = Date.now(); note("The phone is hot, so I've eased off the camera.", 6000); }
        } else if (lamp.status <= 1 && easedByHeat) { easedByHeat = false; try { OpenViture.easeOff(false); } catch (e) { /* no host */ } }
      };
      const look = () => {
        try { const t = JSON.parse(OpenViture.thermal()); lamp.temp = typeof t.batteryC === "number" ? t.batteryC : null; lamp.status = t.thermal | 0; } catch (e) { return; }
        paintLamp(); relieve();
      };
      look(); setInterval(look, 30000);
    }
    paintLamp();
  }

  // ── "Sign the glasses in": the glasses' browser (OpenViture's WebView) has no cookies, so a normal browser (the phone's) mints it a one-time link
  // (portal/authlink.py) and hands it to OpenViture: on Android Chrome an intent:// address opens OpenViture's door at once; otherwise the link is
  // copied (or shared) for him to open there within five minutes ──
  const linkKey = $("#k-link");
  if (linkKey && cfg.linkNew && !window.OpenViture) {
    linkKey.hidden = false;
    linkKey.addEventListener("click", async () => {
      linkKey.classList.add("busy");
      try {
        const d = await getJSON(cfg.linkNew, { method: "POST", body: "{}", headers: { "Content-Type": "application/json", "X-Link": "1", Accept: "application/json" } });
        const u = new URL(d.url);
        let left = false; document.addEventListener("visibilitychange", () => { left = true; }, { once: true });
        location.href = "intent://" + u.host + u.pathname + u.search + "#Intent;scheme=https;package=dev.openviture.test;end";
        setTimeout(async () => {
          if (left || document.hidden) return;                                   // OpenViture took it
          try { await navigator.clipboard.writeText(d.url); note("Link copied. Open it in OpenViture within 5 minutes.", 9000); }
          catch (e) { if (navigator.share) navigator.share({ text: d.url }).catch(() => {}); else note(d.url, 15000); }
        }, 1500);
      } catch (e) { note("Couldn't make a link: " + (e.message || e), 6000); }
      finally { linkKey.classList.remove("busy"); }
    });
  }
  async function snap(hold, by) {
    if (!vision) return null;
    if (flashKey) flashKey.classList.add("busy");
    try { return await vision.flash(hold, by); } finally { if (flashKey) flashKey.classList.remove("busy"); }
  }
  if (vision && flashKey) {
    let pressTimer = 0, held = false;
    flashKey.addEventListener("pointerdown", () => { held = false; clearTimeout(pressTimer); pressTimer = setTimeout(() => { held = true; snap(true, "key"); }, V.HOLD_MS); });
    ["pointerup", "pointerleave", "pointercancel"].forEach(ev => flashKey.addEventListener(ev, () => clearTimeout(pressTimer)));
    flashKey.addEventListener("click", () => { if (held) { held = false; return; } snap(false, "key"); });       // (a hold already flashed: its click is not a tap)
  }
  if (vision && forgetKey) forgetKey.addEventListener("click", async () => {
    const m = lastMoment; forgetKey.hidden = true; clearTimeout(forgetTimer); lastMoment = null;
    if (m) note(await vision.forget(m.id) ? "Forgotten." : "Couldn't forget it.");
  });
  if (V && cfg.visionMoments && !cfg.vision) {
    const joiner = V.attach({ momentAdd: cfg.visionAdd, html2canvas: cfg.snapshotLib, pageName: "drive-page.jpg" }), joined = new Set();
    const look = async () => {
      if (document.hidden) return;
      try {
        const r = await getJSON(cfg.visionMoments + "?limit=3&since=" + (Date.now() / 1000 - 60));
        for (const m of r.moments || []) {
          if (joined.has(m.id) || (m.files || []).some(f => f.name === "drive-page.jpg")) continue;
          joined.add(m.id); joiner.addPage(m.id, "drive-page.jpg");
        }
      } catch (e) { /* not the Captain's, or home is silent: no moment to join */ }
    };
    setInterval(look, 15000);
  }

  paint();
  window.DriveApp = { S, voice, get ear() { return ear; }, get vision() { return vision; }, shuffle, playBook, next, pollHouse, saveBook };
})();
