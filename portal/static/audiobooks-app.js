/* Audiobooks on the bridge (portal/templates/audiobooks.html, the Captain only). The page is a shell: it asks the portal for a pass
   (/audiobooks/gate) and then talks straight to skippy-pc's audiobook gate through Tailscale Funnel with it: the catalog, the covers,
   the audio, and the places (read, and written back so "Skippy, read my book" continues where this page stopped). Nothing but the pass, the room
   list and "Play on" commands goes through the portal. The logic that needs no browser is static/audiobooks-pure.js.

   Two ways a book plays: in this browser (an <audio> element on the gate's file), or on a room box (Play on): the portal queues a `book:` command,
   the hub's BookPlayer plays it from the same place, and this page becomes its remote (it asks /audiobooks/rooms every few seconds). */
(function () {
  "use strict";
  const P = window.BooksPure, Bridge = window.Bridge;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const root = $("#ab");
  const au = $("#au");
  const SPEEDS = [0.8, 1, 1.15, 1.25, 1.5, 1.75, 2];
  const SLEEPS = [0, 15, 30, 60, "chapter"];
  const GAP = 8, PAD = 4;

  const S = {
    url: "", pass: "", exp: 0, books: [], byId: {}, places: {}, mode: "all", sort: "series", q: "", shown: [],
    cur: null,                    // the book the page is showing as current (here, or on a box)
    loaded: "",                   // the id of the book in the <audio> element, once it has been told to load
    remote: null,                 // {device, name, book, at} while a room box is playing
    speed: 1, sleep: 0, sleepAt: 0, sleepChapter: 0, savedAt: 0, rooms: [], sheet: "", tab: "about", cols: 3, lastSaveKey: "",
  };

  const icon = name => `<svg class="g" aria-hidden="true"><use href="#g-${name}"/></svg>`;
  const hydrate = r => $$("i[data-i]", r).forEach(i => { i.outerHTML = icon(i.dataset.i); });
  hydrate(root);

  function toast(text) {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = text;
    $("#toasts").append(t);
    setTimeout(() => t.remove(), 3500);
  }
  function notice(text) { const n = $("#notice"); n.textContent = text || ""; n.hidden = !text; }
  const say = text => { $("#live").textContent = text; };

  // ── the gate ────────────────────────────────────────────────────────────────────
  const gu = path => `${S.url}/t/${S.pass}/${path}`;

  async function getGate() {
    const r = await fetch(root.dataset.gateUrl, { headers: { Accept: "application/json" } });
    const g = await r.json();
    if (!g.ok) throw new Error(g.why || "The audiobook gate is out of reach.");
    S.url = g.url; S.pass = g.pass; S.exp = g.exp;
    return g;
  }

  async function boot() {
    try {
      await getGate();
      const [cat, pl] = await Promise.all([fetch(gu("catalog.json")).then(r => { if (!r.ok) throw new Error("The library isn't ready yet."); return r.json(); }),
                                           fetch(gu("places.json"), { cache: "no-store" }).then(r => r.json())]);
      S.books = cat.books; S.byId = Object.fromEntries(cat.books.map(b => [b.id, b]));
      S.places = pl.books || {};
      root.dataset.state = "ready"; notice("");
      $("#q").disabled = false;
      render(0);
      const last = pl.current && S.byId[pl.current];
      if (last && !S.cur) setCurrent(last, false);
      await pollRooms();
      setInterval(refreshPlaces, 20000);
      setInterval(pollRooms, 3000);
      setInterval(renewPass, 60000);
    } catch (e) {
      root.dataset.state = "down"; notice(String(e.message || e));
      setTimeout(boot, 10000);
    }
  }

  async function renewPass() {
    if (S.exp - Date.now() / 1000 < 1800) { try { await getGate(); } catch (e) { /* keeps the old one until it fails for real */ } }
  }

  async function refreshPlaces() {
    if (document.hidden) return;
    try {
      const pl = await fetch(gu("places.json"), { cache: "no-store" }).then(r => r.json());
      for (const [id, p] of Object.entries(pl.books || {})) {
        const mine = S.places[id];
        if (!mine || (p.last_played || 0) > (mine.last_played || 0)) S.places[id] = p;
      }
      if (!playingHere() && !S.remote) { list.refresh(); paintSheet(); paintBar(); }
    } catch (e) { /* a dead zone: next time */ }
  }

  async function savePlace(force) {
    const b = S.cur; if (!b || S.remote || S.loaded !== b.id) return;      // only a book this page has actually loaded: a not-yet-played one must not save its 0:00
    const now = Date.now();
    if (!P.shouldSave(S.savedAt, now, force)) return;
    const pos = au.currentTime || 0;
    const p = S.places[b.id] || {};
    const body = { pos, chapter: P.chapterAt(b.ch, pos), speed: S.speed, finished: p.finished === true || (b.d && b.d - pos < P.DONE_LEFT_S && au.ended) };
    S.savedAt = now;
    S.places[b.id] = Object.assign({}, p, body, { last_played: Math.floor(now / 1000) });
    try { await fetch(gu(`place/${b.id}`), { method: "PUT", body: JSON.stringify(body), keepalive: true, headers: { "Content-Type": "application/json" } }); }
    catch (e) { S.savedAt = 0; }                       // not saved: try again at the next beat
  }

  // ── the list: covers, a screen at a time ──────────────────────────────────────────
  const list = new Bridge.PList($("#scroll"), $("#host"), {}, { pips: $("#pips"), pgno: $("#pgno"), scrub: $("#scrub") });
  const cellW = () => Math.floor(($("#scroll").clientWidth - PAD * 2 - GAP * (S.cols - 1)) / S.cols);

  function ensureShelf(cols) {
    const type = "shelf" + cols;
    if (list.makers[type]) return type;
    list.makers[type] = {
      make() {
        const e = document.createElement("div"); e.className = "vrow shelf"; e.setAttribute("role", "list");
        for (let i = 0; i < cols; i++) {
          const c = document.createElement("button"); c.type = "button"; c.className = "cell"; c.dataset.act = "open";
          c.innerHTML = `<span class="cover">${icon("book")}<img alt=""><b class="sq"></b></span><i class="bar"><b></b></i><span class="t"></span>`;
          const img = $("img", c); img.addEventListener("load", () => img.classList.add("ok"));
          e.append(c);
        }
        return e;
      },
      bind(e, row) {
        const w = cellW();
        $$(".cell", e).forEach((c, i) => {
          const b = row.books[i];
          c.hidden = !b; c.style.width = w + "px";
          if (!b) return;
          c.dataset.id = b.id; c.title = b.t + (b.a ? " — " + b.a : "");
          $(".cover", c).style.height = w + "px";
          const img = $("img", c);
          if (img.dataset.id !== b.id) { img.classList.remove("ok"); img.dataset.id = b.id; img.src = b.c ? gu(`art/${b.id}?w=240`) : ""; }
          const st = P.status(b, S.places[b.id]);
          c.dataset.st = st;
          const pos = (S.places[b.id] || {}).pos || 0;
          $(".bar b", c).style.width = st === "listening" && b.d ? Math.min(100, 100 * pos / b.d) + "%" : (st === "finished" ? "100%" : "0%");
          $(".t", c).textContent = b.t;
          $(".sq", c).textContent = S.sort === "series" && b.sr && b.sq != null ? "#" + b.sq : "";
          c.title = b.t + (b.a ? " — " + b.a : "") + (b.sr ? " (" + b.sr + (b.sq != null ? " #" + b.sq : "") + ")" : "");
          c.classList.toggle("cur", !!S.cur && S.cur.id === b.id);
        });
      },
    };
    return type;
  }

  function render(page) {
    S.cols = P.colsFor($("#scroll").clientWidth, 104);
    S.shown = P.visible(S.books, S.places, S.q, S.mode, S.sort);
    const h = cellW() + 52;
    const rows = P.gridRows(S.shown, S.cols, h).map(r => Object.assign(r, { type: ensureShelf(S.cols) }));
    list.setRows(rows, page || 0);
    counts();
    $("#host").dataset.empty = S.shown.length ? "0" : "1";
  }

  const SORT_NAMES = { series: "Sorted by series", title: "Sorted by title", author: "Sorted by author" };
  function paintSort() {
    const k = $("#sort");
    k.setAttribute("aria-label", SORT_NAMES[S.sort]); k.title = SORT_NAMES[S.sort];
    $(".cnt", k).textContent = S.sort === "series" ? "S" : S.sort === "title" ? "T" : "A";
  }

  function counts() {
    const n = { all: S.books.length, listening: 0, new: 0, finished: 0 };
    for (const b of S.books) n[P.status(b, S.places[b.id])]++;
    $$("#filters .key").forEach(k => { $(".cnt", k).textContent = n[k.dataset.mode] || ""; });
  }

  new ResizeObserver(() => { if (S.books.length) { const first = list.starts[list.page] || 0; render(0); list.goRow(first); } }).observe($("#scroll"));

  // ── one book ───────────────────────────────────────────────────────────────────────
  const cList = new Bridge.PList($("#c-scroll"), $("#c-host"), {}, { pips: $("#c-pips"), pgno: $("#c-pgno"), scrub: $("#c-scrub") });
  cList.makers.ch = {
    make() {
      const e = document.createElement("div"); e.className = "vrow tr"; e.setAttribute("role", "listitem");
      e.innerHTML = `<button type="button" class="tr-main" data-act="chapter"><span class="num"></span><span class="txt"><span class="l1"><span></span></span></span><span class="dur"></span></button>`;
      return e;
    },
    bind(e, row, i) {
      const b = S.cur && S.cur.id === S.sheet ? S.cur : S.byId[S.sheet];
      e.dataset.i = i;
      $(".num", e).textContent = i + 1;
      $(".l1 span", e).textContent = row.title;
      $(".dur", e).textContent = P.fmt(row.start);
      e.classList.toggle("cur", !!b && P.chapterAt(b.ch, curPos(b)) === i);
    },
  };

  function openBook(id) {
    const b = S.byId[id]; if (!b) return;
    S.sheet = id; S.tab = "about";
    $("#side").hidden = false; $("#np").hidden = true; $("#menu").hidden = true;
    setCover($("#s-cover"), b, 500);
    $("#s-title").textContent = b.t; $("#s-by").textContent = b.a;
    $("#s-nar").innerHTML = b.n ? icon("mic") : "";
    if (b.n) $("#s-nar").append(" " + b.n);
    $("#s-nar").hidden = !b.n;
    $("#s-meta").textContent = [b.sr ? b.sr + (b.sq != null ? " #" + b.sq : "") : "", P.left(b.d), b.g, b.y].filter(Boolean).join(" · ");
    $("#s-summary").textContent = b.s || "";
    showTab("about");
    cList.setRows(b.ch.map((c, i) => ({ type: "ch", h: 46, title: c[0], start: c[1] })), 0);
    paintSheet();
  }

  function showTab(t) {
    S.tab = t;
    $("#panel-about").hidden = t !== "about"; $("#panel-chapters").hidden = t !== "chapters";
    $("#tab-about").setAttribute("aria-selected", t === "about"); $("#tab-chapters").setAttribute("aria-selected", t === "chapters");
    if (t === "chapters") {
      const b = S.byId[S.sheet];
      cList.resize();
      cList.goRow(P.chapterAt(b.ch, curPos(b)));
    }
  }

  function closeSheet() { S.sheet = ""; $("#side").hidden = true; }

  function curPos(b) {
    if (S.remote && S.remote.book && S.remote.book.id === b.id) return P.remotePos(S.remote.book, (Date.now() - S.remote.at) / 1000, b.d);
    if (S.cur && S.cur.id === b.id && au.src && !S.remote) return au.currentTime || 0;
    return (S.places[b.id] || {}).pos || 0;
  }

  function playingHere() { return !!S.cur && !S.remote && !au.paused && !au.ended; }
  function playingNow() { return S.remote ? !!(S.remote.book && S.remote.book.playing) : playingHere(); }

  function paintSheet() {
    if (!S.sheet) return;
    const b = S.byId[S.sheet];
    const place = S.places[b.id], pos = curPos(b);
    const st = P.status(b, place);
    $("#s-prog").style.width = (b.d ? Math.min(100, 100 * pos / b.d) : 0) + "%";
    const here = !!S.cur && S.cur.id === b.id && playingNow();
    $("#s-play").innerHTML = icon(here ? "pause" : "play") + `<span class="cnt" id="s-play-t">${st === "listening" ? P.left(b.d - pos) : ""}</span>`;
    $("#s-play").setAttribute("aria-label", here ? "Pause" : st === "listening" ? "Resume" : "Play");
    $("#s-done").setAttribute("aria-pressed", st === "finished");
    $$("#c-host .vrow").forEach(e => { e.classList.toggle("cur", P.chapterAt(b.ch, pos) === +e.dataset.i); });
  }

  function setCover(coverEl, b, w) {
    const img = $("img", coverEl);
    img.classList.remove("ok");
    img.onload = () => img.classList.add("ok");
    img.src = b && b.c ? gu(`art/${b.id}?w=${w}`) : "";
  }

  // ── playing here ───────────────────────────────────────────────────────────────────
  function setCurrent(b, load) {
    const same = S.cur && S.cur.id === b.id;
    S.cur = b;
    const place = S.places[b.id];
    S.speed = (place && place.speed) || 1;
    if (!same) {
      setCover($("#b-cover"), b, 120); setCover($("#np-cover"), b, 500);
    }
    if (load && !S.remote) {
      if (!same || !au.src) { au.src = gu(`a/${b.id}`); }
    }
    $("#bar").hidden = false;
    paintBar();
  }

  function loadHere(b, pos) {
    const same = S.cur && S.cur.id === b.id && au.src && au.src.includes(`/a/${b.id}`);
    if (!same) S.loaded = "";                            // nothing may be saved for the book until it is in place at `pos` (a pause while the source changes reads 0:00)
    setCurrent(b, true);
    const go = () => { au.currentTime = pos; au.playbackRate = S.speed; au.preservesPitch = true; S.loaded = b.id; };
    if (same && au.readyState >= 1) go();
    else { au.addEventListener("loadedmetadata", go, { once: true }); au.load(); }
  }

  async function playHere(b, pos) {
    if (S.remote) await stopRemote();
    loadHere(b, pos);
    try { await au.play(); root.dataset.playing = "1"; $("#tapbar").hidden = true; }
    catch (e) { $("#tapbar").hidden = false; }          // the browser wants a tap first
    mediaSession();
    paintAll();
  }

  function startBook(b, restart) {
    const place = S.places[b.id];
    const pos = restart ? 0 : P.resumeAt(place, b.d);
    if (S.remote) return remoteCmd(`book:play:${b.id}` + (pos ? `:${pos.toFixed(1)}` : ""), b);
    return playHere(b, pos);
  }

  function toggle() {
    if (!S.cur) return;
    if (S.remote) {
      if (!S.remote.book) return remoteCmd(`book:play:${S.cur.id}` + resumeSuffix(S.cur));       // the box has told us nothing yet: start it from the place
      return remoteCmd(playingNow() ? "book:pause" : "book:resume");
    }
    if (au.paused) { au.play().then(() => { root.dataset.playing = "1"; $("#tapbar").hidden = true; paintAll(); }).catch(() => { $("#tapbar").hidden = false; }); }
    else { S.pauseAt = Date.now(); au.pause(); }
  }

  const resumeSuffix = b => { const r = P.resumeAt(S.places[b.id], b.d); return r ? `:${r.toFixed(1)}` : ""; };

  function seekTo(sec) {
    const b = S.cur; if (!b) return;
    sec = Math.min(Math.max(0, sec), Math.max(0, b.d - 1));
    if (S.remote) return remoteCmd(`book:play:${b.id}:${sec.toFixed(1)}`);
    au.currentTime = sec; savePlace(true); paintAll();
  }

  function skip(d) {
    const b = S.cur; if (!b) return;
    if (S.remote) return remoteCmd(`book:skip:${d}`);
    seekTo(au.currentTime + d);
  }

  function chapter(dir) {
    const b = S.cur; if (!b) return;
    if (S.remote) return remoteCmd(`book:chapter:${dir}`);
    const pos = au.currentTime, i = P.chapterAt(b.ch, pos);
    const target = dir > 0 ? (b.ch[i + 1] ? b.ch[i + 1][1] : null) : (pos - b.ch[i][1] > 5 || i === 0 ? b.ch[i][1] : b.ch[i - 1][1]);
    if (target !== null) seekTo(target);
  }

  function setSpeed(x) {
    S.speed = x;
    if (S.remote) remoteCmd(`book:speed:${x}`); else { au.playbackRate = x; savePlace(true); }
    paintAll();
  }

  function cycleSleep() {
    if (S.remote) { toast("The pod can't be told to sleep from here yet"); return; }
    const i = (SLEEPS.indexOf(S.sleep) + 1) % SLEEPS.length;
    S.sleep = SLEEPS[i];
    S.sleepAt = typeof S.sleep === "number" && S.sleep ? Date.now() + S.sleep * 60000 : 0;
    S.sleepChapter = S.sleep === "chapter" && S.cur ? (S.cur.ch[P.chapterAt(S.cur.ch, au.currentTime) + 1] || [0, S.cur.d])[1] : 0;
    paintAll();
  }

  function sleepCheck() {
    if (S.remote || au.paused) return;
    if ((S.sleepAt && Date.now() >= S.sleepAt) || (S.sleepChapter && au.currentTime >= S.sleepChapter - 0.4)) {
      S.pauseAt = Date.now(); au.pause(); S.sleep = 0; S.sleepAt = 0; S.sleepChapter = 0; say("Sleep timer: stopped");
    }
  }

  au.addEventListener("timeupdate", () => { savePlace(false); sleepCheck(); paintProgress(); });
  au.addEventListener("pause", () => { if (duck && !au.ended) duck.paused(Date.now() - S.pauseAt < 400); root.dataset.playing = "0"; if (!au.ended) savePlace(true); paintAll(); });
  // Skippy's voice ducks the book instead of stopping it, and a pause the system made meanwhile is undone when he is done (static/skippy-duck.js).
  const duck = window.SkippyDuck ? SkippyDuck.follow({ level: f => { au.volume = Math.max(0, Math.min(1, f)); }, paused: () => au.paused, resume: () => au.play().catch(() => {}) }) : null;
  au.addEventListener("play", () => { root.dataset.playing = "1"; paintAll(); });
  au.addEventListener("ended", () => { const b = S.cur; if (!b) return; S.places[b.id] = Object.assign({}, S.places[b.id], { finished: true }); savePlace(true); paintAll(); });
  au.addEventListener("error", () => { if (au.src) { notice("This book won't play right now."); setTimeout(() => notice(""), 4000); } });
  document.addEventListener("visibilitychange", () => { if (document.hidden) savePlace(true); else { refreshPlaces(); pollRooms(); } });
  window.addEventListener("pagehide", () => savePlace(true));

  function mediaSession() {
    if (!("mediaSession" in navigator) || !S.cur) return;
    const b = S.cur;
    navigator.mediaSession.metadata = new MediaMetadata({ title: b.t, artist: b.a, album: "Audiobooks", artwork: b.c ? [{ src: gu(`art/${b.id}?w=500`), sizes: "500x500", type: "image/jpeg" }] : [] });
    const set = (a, f) => { try { navigator.mediaSession.setActionHandler(a, f); } catch (e) { /* not supported */ } };
    set("play", toggle); set("pause", toggle);
    set("seekbackward", () => skip(-30)); set("seekforward", () => skip(30));
    set("previoustrack", () => chapter(-1)); set("nexttrack", () => chapter(1));
    set("seekto", d => seekTo(d.seekTime));
  }

  // ── the bar and the player panel ─────────────────────────────────────────────────
  function paintProgress() {
    const b = S.cur; if (!b) return;
    const pos = curPos(b), i = P.chapterAt(b.ch, pos);
    $("#b-prog").style.width = (b.d ? Math.min(100, 100 * pos / b.d) : 0) + "%";
    $("#b-sub").textContent = `${b.ch.length > 1 ? b.ch[i][0] + " · " : ""}${P.left(b.d - pos)}`;
    if (!$("#np").hidden) {
      const seek = $("#np-seek");
      if (document.activeElement !== seek) { seek.max = Math.floor(b.d); seek.value = Math.floor(pos); }
      $("#np-t0").textContent = P.fmt(pos); $("#np-t1").textContent = "-" + P.fmt(b.d - pos);
      $("#np-ch").textContent = b.ch.length > 1 ? b.ch[i][0] : "";
      $("#np-where").textContent = S.remote ? S.remote.name : "";
    }
    if (S.sleepAt) $("#np-sleep-t").textContent = Math.max(1, Math.ceil((S.sleepAt - Date.now()) / 60000));
    else $("#np-sleep-t").textContent = S.sleepChapter ? "¶" : "";
    $("#s-prog").style.width = S.sheet === b.id ? (b.d ? Math.min(100, 100 * pos / b.d) : 0) + "%" : $("#s-prog").style.width;
  }

  function paintBar() {
    const b = S.cur; if (!b) return;
    $("#b-title").textContent = b.t;
    $("#np-title").textContent = b.t;
    const on = playingNow();
    for (const id of ["#b-toggle", "#np-toggle"]) { $(id).innerHTML = icon(on ? "pause" : "play"); $(id).setAttribute("aria-label", on ? "Pause" : "Play"); }
    $("#np-speed .tx").textContent = S.speed + "×";
    $("#np-sleep").setAttribute("aria-pressed", !!S.sleep);
    $("#signal").dataset.s = S.remote ? "idle" : "on";
    $("#signal").title = S.remote ? S.remote.name : "This browser";
    $("#np-on").setAttribute("aria-pressed", !!S.remote);
    paintProgress();
  }

  function paintAll() { paintBar(); paintSheet(); list.refresh(); }

  setInterval(() => { if (S.cur && (playingNow() || S.remote)) { paintProgress(); if (S.sheet) paintSheet(); } }, 1000);

  // ── Play on: a pod ────────────────────────────────────────────────────────────────
  async function pollRooms(force) {
    if ((document.hidden && force !== true) || root.dataset.state !== "ready") return;
    try {
      const r = await fetch(root.dataset.roomsUrl, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!r.ok) return;
      S.rooms = (await r.json()).rooms || [];
    } catch (e) { return; }
    if (S.remote) {
      const dev = S.rooms.find(d => d.id === S.remote.device);
      if (!dev || !dev.live) { S.remote.lost = (S.remote.lost || 0) + 1; if (S.remote.lost > 3) { toast(`${S.remote.name} went away`); S.remote = null; paintAll(); } }
      else if (dev.book && S.remote.holdUntil && Date.now() < S.remote.holdUntil && dev.book.playing !== S.remote.expectPlaying) {
        S.remote.lost = 0;                                  // an older state than our command: keep what we showed
      } else if (dev.book) {
        S.remote.lost = 0; S.remote.holdUntil = 0; S.remote.book = Object.assign({}, dev.book); S.remote.at = Date.now() - dev.age * 1000;
        S.remote.book.pos = dev.book.pos;
        const b = S.byId[dev.book.id];
        if (b && (!S.cur || S.cur.id !== b.id)) setCurrent(b, false);
        S.speed = dev.book.speed || 1;
        if (dev.book.pos > 1) S.places[dev.book.id] = Object.assign({}, S.places[dev.book.id], { pos: dev.book.pos, speed: dev.book.speed, last_played: Math.floor(Date.now() / 1000), finished: false });
        paintAll();
      }
    } else if (!playingHere()) {
      // a box that is reading a book on its own (by voice): show it, so this page can be its remote
      const busy = S.rooms.find(d => d.live && d.book && d.book.playing && S.byId[d.book.id]);
      if (busy) {
        S.remote = { device: busy.id, name: busy.name, book: Object.assign({}, busy.book), at: Date.now() - busy.age * 1000 };
        setCurrent(S.byId[busy.book.id], false); S.speed = busy.book.speed || 1; paintAll();
      }
    }
  }

  async function remoteCmd(action, book) {
    if (!S.remote) return false;
    try {
      const r = await fetch(root.dataset.commandUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device: S.remote.device, action }) });
      if (!r.ok) { toast(`${S.remote.name} isn't answering`); return false; }
    } catch (e) { toast("No signal"); return false; }
    if (book) { setCurrent(book, false); }
    expect(action, book || S.cur);
    setTimeout(pollRooms, 1500); setTimeout(pollRooms, 4000);        // the hub takes its next check-in to act
    return true;
  }

  /** What the box will say once it has done `action`, shown at once and held for HOLD_MS: the check-in that carried the command still reports the old
   *  state, and believing it would flip the keys back (and the next press would then send the opposite command). */
  const HOLD_MS = 6000;
  function expect(action, b) {
    const r = S.remote; if (!r || !b) return;
    const cur = r.book && r.book.id === b.id ? r.book : { id: b.id, title: b.t, pos: curPos(b), chapter: 0, playing: false, speed: S.speed };
    const now = Date.now(), pos = P.remotePos(cur, (now - r.at) / 1000, b.d);
    let m;
    const next = Object.assign({}, cur, { pos });
    if ((m = /^book:play:[a-z0-9-]+(?::([\d.]+))?$/.exec(action))) { next.playing = true; if (m[1]) next.pos = +m[1]; }
    else if (action === "book:resume") next.playing = true;
    else if (action === "book:pause") next.playing = false;
    else if ((m = /^book:skip:(-?\d+)$/.exec(action))) next.pos = Math.max(0, pos + +m[1]);
    else if ((m = /^book:speed:([\d.]+)$/.exec(action))) next.speed = +m[1];
    else if (action.startsWith("book:chapter:")) { /* the box says where it lands */ }
    r.book = next; r.at = now; r.holdUntil = now + HOLD_MS; r.expectPlaying = next.playing;
    paintAll();
  }

  async function stopRemote() {
    if (!S.remote) return;
    const dev = S.remote; S.remote = null;
    try { await fetch(root.dataset.commandUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device: dev.device, action: "book:pause" }) }); } catch (e) { /* it stays on */ }
  }

  async function sendTo(device) {
    const b = S.cur || S.byId[S.sheet]; if (!b) return;
    const pos = Math.round(curPos(b) * 10) / 10;
    const resume = P.resumeAt({ pos, finished: (S.places[b.id] || {}).finished }, b.d);
    if (S.remote && S.remote.device === device.id) { closeMenu(); return; }  // already there
    if (playingHere()) { S.pauseAt = Date.now(); au.pause(); }                                           // one place at a time: this browser stops, the box takes over
    await savePlace(true);
    if (S.remote) await stopRemote();                                        // from one box to another: the first one stops
    S.remote = { device: device.id, name: device.name, book: null, at: Date.now() };
    const ok = await remoteCmd(`book:play:${b.id}` + (resume ? `:${resume.toFixed(1)}` : ""), b);
    if (!ok) S.remote = null;
    closeMenu(); paintAll();
  }

  function openMenu() {
    const menu = $("#menu");
    menu.innerHTML = `<div class="s-head"><h4>${icon("devices")}</h4><span class="grow"></span><button type="button" class="key" data-act="close-menu" aria-label="Close" title="Close">${icon("x")}</button></div><div class="mgrid" id="m-grid"></div>`;
    const grid = $("#m-grid", menu);
    const here = document.createElement("button");
    here.type = "button"; here.className = "mkey"; here.dataset.act = "play-here"; here.setAttribute("aria-checked", !S.remote);
    here.innerHTML = `${icon("headset")}<span class="tag">${S.remote ? "" : "●"}</span>`; here.title = "This browser";
    grid.append(here);
    for (const d of S.rooms) {
      const k = document.createElement("button");
      k.type = "button"; k.className = "mkey"; k.dataset.act = "play-room"; k.dataset.id = d.id; k.title = d.name;
      k.setAttribute("aria-checked", !!S.remote && S.remote.device === d.id);
      k.innerHTML = `<span class="lamp" data-s="${d.live ? "on" : "off"}"></span>${icon("ear")}<span class="tag">${d.name.replace(/ \(Skippy\)$/, "")}</span>`;
      k.disabled = !d.live;
      grid.append(k);
    }
    menu.hidden = false;
  }
  function closeMenu() { $("#menu").hidden = true; }

  // ── the keys ──────────────────────────────────────────────────────────────────────────
  root.addEventListener("click", ev => {
    const el = ev.target.closest("[data-act]"); if (!el) return;
    const act = el.dataset.act;
    const cell = el.closest(".cell");
    switch (act) {
      case "open": if (cell && cell.dataset.id) openBook(cell.dataset.id); break;
      case "close-sheet": closeSheet(); break;
      case "tab-about": showTab("about"); break;
      case "tab-chapters": showTab("chapters"); break;
      case "sort": S.sort = P.SORTS[(P.SORTS.indexOf(S.sort) + 1) % P.SORTS.length]; paintSort(); render(0); break;
      case "mode": S.mode = el.dataset.mode; $$("#filters .key").forEach(k => k.setAttribute("aria-pressed", k === el)); render(0); break;
      case "clear-search": $("#q").value = ""; S.q = ""; $("#q-clear").hidden = true; render(0); break;
      case "tap": au.play().then(() => { $("#tapbar").hidden = true; paintAll(); }).catch(() => {}); break;
      case "play-book": {
        const b = S.byId[S.sheet];
        if (S.cur && S.cur.id === b.id && (S.remote ? S.remote.book : au.src)) toggle(); else startBook(b, false);
        break;
      }
      case "restart-book": startBook(S.byId[S.sheet], true); break;
      case "finish-book": {
        const b = S.byId[S.sheet], p = S.places[b.id] || {};
        const done = P.status(b, p) !== "finished";
        S.places[b.id] = Object.assign({}, p, { finished: done, last_played: Math.floor(Date.now() / 1000) });
        fetch(gu(`place/${b.id}`), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pos: p.pos || 0, chapter: p.chapter || 0, speed: p.speed || 1, finished: done }) }).catch(() => {});
        counts(); paintAll(); break;
      }
      case "chapter": {
        const b = S.byId[S.sheet], i = +el.closest(".vrow").dataset.i;
        const start = b.ch[i][1];
        if (S.cur && S.cur.id === b.id && !S.remote && au.src) { seekTo(start); if (au.paused) toggle(); }
        else if (S.remote) remoteCmd(`book:play:${b.id}:${start.toFixed(1)}`, b);
        else playHere(b, start);
        break;
      }
      case "np": $("#np").hidden = false; $("#side").hidden = true; paintBar(); { const b = S.cur; if (b) { const seek = $("#np-seek"); seek.max = Math.floor(b.d); seek.value = Math.floor(curPos(b)); } } break;
      case "close-np": $("#np").hidden = true; break;
      case "np-book": if (S.cur) { $("#np").hidden = true; openBook(S.cur.id); } break;
      case "toggle": toggle(); break;
      case "back30": skip(-30); break;
      case "fwd30": skip(30); break;
      case "chap-prev": chapter(-1); break;
      case "chap-next": chapter(1); break;
      case "speed": setSpeed(SPEEDS[(SPEEDS.indexOf(S.speed) + 1) % SPEEDS.length] || 1); break;
      case "sleep": cycleSleep(); break;
      case "play-on": pollRooms(true).then(openMenu); break;
      case "close-menu": closeMenu(); break;
      case "play-here": { const was = S.remote; closeMenu(); if (was) { const b = S.cur, pos = curPos(b); stopRemote().then(() => playHere(b, Math.max(0, pos - 2))); } break; }
      case "play-room": { const d = S.rooms.find(x => x.id === el.dataset.id); if (d) sendTo(d); break; }
      default: break;
    }
  });

  $("#np-seek").addEventListener("change", e => seekTo(+e.target.value));
  $("#np-seek").addEventListener("input", e => { $("#np-t0").textContent = P.fmt(+e.target.value); });

  let searchTimer = 0;
  $("#q").addEventListener("input", e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { S.q = e.target.value; $("#q-clear").hidden = !S.q; render(0); }, 120);
  });

  document.addEventListener("keydown", e => {
    if (e.target.matches("input, textarea, select")) { if (e.key === "Escape") e.target.blur(); return; }
    if (e.key === " ") { e.preventDefault(); toggle(); }
    else if (e.key === "ArrowLeft") skip(-30);
    else if (e.key === "ArrowRight") skip(30);
    else if (e.key === "PageDown") list.go(list.page + 1);
    else if (e.key === "PageUp") list.go(list.page - 1);
    else if (e.key === "/") { e.preventDefault(); $("#q").focus(); }
    else if (e.key === "Escape") { if (!$("#menu").hidden) closeMenu(); else if (!$("#np").hidden) $("#np").hidden = true; else if (S.sheet) closeSheet(); }
  });

  window.__books = S;                 // for the dev harness and the browser tests
  boot();
})();
