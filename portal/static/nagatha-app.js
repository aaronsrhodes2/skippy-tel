/* Nagatha's Video Service: the page (portal/templates/video.html). The rules live in nagatha-core.js (window.NagathaCore);
   this file is the part that needs a browser: the poster wall, the details sheet, the <video> and its controls.

   Where things come from: the portal gives a pass (GET data-gate-url → {ok, url, pass, exp}); everything else is read straight
   from skippy-pc's video gate with that pass in the path (catalog, posters, the film, subtitles, progress). The portal never
   sees the film or what is being watched. */
(function () {
  "use strict";
  const C = window.NagathaCore;
  const root = document.getElementById("nv");
  if (!root || !C) return;
  const cfg = { gate: root.dataset.gateUrl, hls: root.dataset.hlsSrc, progress: root.dataset.progress !== "0" };
  const $ = sel => root.querySelector(sel);
  const video = $("#video"), player = $("#player"), sheet = $("#sheet");
  const IOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const WEBKIT = IOS || (/Safari\//.test(navigator.userAgent) && !/Chrome|Chromium|CriOS|FxiOS|Edg|OPR|Android/.test(navigator.userAgent));

  // ═══ glyphs: the portal's set (templates/_glyphs.html), so the page needs no icon font or extra request ═══════════════
  const GLYPH = { close: "x", film: "lens" };
  const ico = n => `<svg class="g" aria-hidden="true" focusable="false"><use href="#g-${GLYPH[n] || n}"/></svg>`;
  function paintIcons(scope) { for (const i of (scope || root).querySelectorAll("i[data-i]")) if (!i.firstChild) i.innerHTML = ico(i.dataset.i); }
  function setIcon(btn, name) { const i = btn.querySelector("i[data-i]"); if (i && i.dataset.i !== name) { i.dataset.i = name; i.innerHTML = ico(name); } }
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function say(text) { const l = $("#live"); l.textContent = ""; setTimeout(() => { l.textContent = text; }, 30); }
  function key(icon, label, cls, fn) {
    const b = el("button", "key" + (cls ? " " + cls : ""));
    b.type = "button"; b.innerHTML = ico(icon); b.setAttribute("aria-label", label); b.title = label;
    if (fn) b.onclick = fn;
    return b;
  }
  function toast(text, ms) {
    const box = $("#toasts"), t = el("div", "toast", text);
    box.append(t);
    while (box.children.length > 2) box.firstChild.remove();
    setTimeout(() => t.remove(), ms || 1800);
  }

  // ═══ what this browser remembers (a convenience: everything works without it) ════════════════════════════════════
  const store = {
    get(k, d) { try { const v = localStorage.getItem("nagatha:" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("nagatha:" + k, JSON.stringify(v)); } catch (e) { /* private window or full: fine */ } },
  };
  const S = {
    lib: null, catalogAt: 0, view: "home", collection: null, q: "", sort: C.SORTS.includes(store.get("sort", "title")) ? store.get("sort", "title") : "title",
    local: store.get("pos", {}), prefs: store.get("prefs", { vol: 1, muted: false, subs: "" }), detailsId: null,
    pageMemo: new Map(), letters: false, fresh: false, viewKey: "", viewRows: 0, geo: "", cur: null, waitingAt: 0, lastTimeAt: 0,
    armed: new C.Armed(() => (navigator.userActivation ? navigator.userActivation.hasBeenActive : undefined)),
  };

  // ═══ notices, and the signal lamp ═══════════════════════════════════════════════════════════════════════════════════
  function showNotice(msg, opts) {
    opts = opts || {};
    const n = $("#notice");
    n.className = "notice " + (opts.kind || "");
    n.dataset.key = opts.key || "";
    const kids = [el("span", "grow", msg)];
    if (opts.retry) kids.push(key("retry", opts.label || "Try again", "sm", opts.retry));
    n.replaceChildren(...kids);
    n.hidden = false;
    syncSignal();
  }
  function hideNotice(k) { const n = $("#notice"); if (!k || n.dataset.key === k) { n.hidden = true; n.dataset.key = ""; syncSignal(); } }
  /** The lamp: lit when all is well, a triangle while the line is lost (the film is waiting for it), a diamond when the library is out of reach. */
  function syncSignal() {
    const n = $("#notice"), bad = !n.hidden && n.classList.contains("bad");
    const state = rec.on ? "warn" : bad ? "off" : "on";
    const label = rec.on ? "No signal: the film is waiting for it to come back" : bad ? "The library is out of reach" : "Connected";
    for (const lamp of [$("#signal"), $("#p-signal")]) { lamp.dataset.s = state; lamp.setAttribute("aria-label", label); lamp.title = label; }
    $("#p-signal").hidden = state === "on";
  }

  // ═══ the pass ════════════════════════════════════════════════════════════════════════════════════════════════════
  const gate = {
    info: null, inflight: null, timer: 0,
    /** A pass good for at least half an hour more, from the portal if needed (one request at a time). */
    ensure(force) {
      if (!force && C.passFresh(this.info, Date.now() / 1000)) return Promise.resolve(this.info);
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
          if (!g || !g.ok) throw Object.assign(new Error((g && g.why) || "The film library is out of reach."), { kind: "home" });
          this.info = g;
          this.schedule();
          return g;
        })().finally(() => { this.inflight = null; });
      }
      return this.inflight;
    },
    /** Ask again half an hour before this pass runs out, so nothing new ever starts on a dying pass. */
    schedule() {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.ensure(true).catch(() => {}), C.refreshIn(this.info, Date.now() / 1000) * 1000);
    },
  };
  function gateProblem(err, retry) {
    if (err && err.kind === "auth") { showNotice(err.message, { kind: "bad", key: "gate", retry: () => location.reload(), label: "Reload" }); return; }
    const msg = err && err.kind === "home" ? err.message : C.playProblem(0);
    showNotice(msg, { kind: "bad", key: "gate", retry });
  }

  // ═══ the catalog ═════════════════════════════════════════════════════════════════════════════════════════════════
  async function loadCatalog() {
    hideNotice("gate");
    root.dataset.state = "loading";
    setLoading("Opening the library…");
    let g;
    try { g = await gate.ensure(); } catch (e) { root.dataset.state = "error"; gateProblem(e, loadCatalog); setLoading("The library is out of reach."); return; }
    for (let attempt = 0; attempt < 4; attempt++) {
      let r;
      try { r = await fetch(C.catalogUrl(g), { cache: "no-cache" }); }      // the gate's ETag makes an unchanged catalog a 304
      catch (e) { root.dataset.state = "error"; gateProblem(null, loadCatalog); setLoading("The library is out of reach."); return; }
      if (r.status === 403 && attempt < 1) { try { g = await gate.ensure(true); } catch (e) { gateProblem(e, loadCatalog); return; } continue; }
      if (r.status === 503 && attempt < 3) { setLoading("skippy-pc is still reading the library…"); await wait(5000); continue; }
      if (!r.ok) { root.dataset.state = "error"; gateProblem({ kind: "home", message: C.playProblem(r.status) }, loadCatalog); return; }
      try { S.lib = C.parseCatalog(await r.json()); } catch (e) { gateProblem({ kind: "home", message: "The library came back in a shape this page doesn't know." }, loadCatalog); return; }
      S.catalogAt = Date.now();
      hideNotice("load");
      root.dataset.state = "ready";
      $("#q").disabled = false;
      route();
      return;
    }
    gateProblem(null, loadCatalog);
  }
  const wait = ms => new Promise(r => setTimeout(r, ms));
  function setLoading(text) { if (!S.lib) showNotice(text, { key: "load" }); }

  // ═══ pictures (the gate's sizes; a burst of uncached posters can get "busy, try again", so an <img> retries) ═══════
  function picture(holder, id, kind, cssPx, label) {
    const g = gate.info;
    const img = holder.querySelector("img") || holder.appendChild(el("img"));
    img.alt = label || "";
    img.decoding = "async";
    holder.classList.remove("loaded", "noart");
    if (!g) return;
    const url = C.imgUrl(g, id, kind, C.imgWidth(cssPx, devicePixelRatio || 1));
    let tries = 0;
    img._u = url;
    img.onerror = () => { if (img._u !== url) return; if (tries++ < 3) setTimeout(() => { if (img._u === url) img.src = url; }, 1200 * tries); else holder.classList.add("noart"); };
    img.onload = () => { if (img._u === url) holder.classList.add("loaded"); };
    img.src = url;
  }

  // ═══ the wall: Home is a deck of controls; everything else is posters a screen at a time ═══════════════════════════
  // Nothing scrolls. A list is cut into screens of whole rows of posters (Bridge.PList, BridgePure.paginate); you flip with a
  // swipe, PageUp/PageDown, or by dragging along the bar under it. The alphabet key opens every letter on one screen.
  const LABELS = { continue: "Continue watching", added: "Recently added", movies: "Movies", collections: "Collections" };
  const VIEWS = ["home", "movies", "collections", "continue", "added"];
  const rail = { on: false, items: [] };
  const letterForRow = row => { let l = ""; for (const r of rail.items) { if (r.i <= row) l = r.l; else break; } return l; };
  const list = new Bridge.PList($("#scroll"), $("#host"), {}, { pips: $("#pips"), pgno: $("#pgno"), scrub: $("#scrub"), onscrub: (on, page) => {
    const b = $("#bubble");
    if (!on || !rail.on) { b.hidden = true; return; }
    b.textContent = letterForRow(list.starts[page] || 0); b.hidden = false;
  } });
  const shape = () => C.gridShape($("#scroll").clientWidth || innerWidth);

  // — row makers —
  const makers = list.makers;
  makers.title = {
    make() { const e = el("div", "vrow vtitle"); e._g = el("span", "gl"); e._t = el("span", "name"); e._c = el("span", "count"); e.append(e._g, e._t, e._c); return e; },
    bind(e, r) { e._g.innerHTML = ico(r.glyph); e._t.textContent = r.text || ""; e._c.textContent = r.count || ""; },
  };
  makers.head = {
    make() { const e = el("div", "vrow vhead"); e._g = el("span", "gl"); e._c = el("span", "count"); e.append(e._g, e._c); return e; },
    bind(e, r) { e._g.innerHTML = ico(r.glyph); e._c.textContent = r.count || ""; },
  };
  makers.msg = {
    make() { const e = el("div", "vrow vmsg"); e._g = el("span", "gl"); e._t = el("span"); e.append(e._g, e._t); return e; },
    bind(e, r) { e._g.innerHTML = ico(r.glyph || "info"); e._t.textContent = r.text; },
  };
  /** A row of posters. A film or a collection: a collection has `itemIds`. */
  makers.cards = {
    make() {
      const e = el("div", "vrow cards"); e._cards = [];
      for (let k = 0; k < 8; k++) {
        const b = el("button", "film"), poster = el("span", "poster"), ph = el("span", "ph"), title = el("span", "c-title"), sub = el("span", "c-sub");
        b.type = "button"; b.setAttribute("role", "listitem");
        poster.append(ph); b.append(poster, title, sub); e.append(b);
        e._cards.push({ b, poster, ph, title, sub });
      }
      return e;
    },
    bind(e, r) {
      e.style.setProperty("--cw", r.cw + "px");
      e._cards.forEach((x, k) => {
        const it = r.items[k];
        x.b.hidden = !it;
        if (!it) return;
        const coll = !!it.itemIds;
        x.b.classList.toggle("coll", coll);
        delete x.b.dataset.id; delete x.b.dataset.coll;
        x.b.dataset[coll ? "coll" : "id"] = it.id;
        x.poster.className = "poster";
        for (const n of x.poster.querySelectorAll(".prog, .seen")) n.remove();
        x.ph.textContent = it.title;
        x.title.textContent = it.title;
        if (coll) {
          x.sub.textContent = `${it.itemIds.length}`;
          x.b.setAttribute("aria-label", `${it.title}, ${it.itemIds.length} films`);
        } else {
          x.sub.textContent = String(it.year || "");
          x.b.setAttribute("aria-label", `${it.title}${it.year ? ", " + it.year : ""}${it.played ? ", watched" : ""}`);
          const at = C.resumePoint(it, S.local[it.id], S.catalogAt);
          if (at && it.runtime) { const bar = el("span", "prog"); bar.style.setProperty("--p", Math.min(100, (100 * at) / it.runtime).toFixed(1) + "%"); x.poster.append(bar); }
          if (it.played) x.poster.append(el("span", "seen"));
        }
        if (it.hasPoster) picture(x.poster, it.id, "poster", r.cw, ""); else { const img = x.poster.querySelector("img"); if (img) img.removeAttribute("src"); }
      });
    },
  };

  const posterRows = entries => { const g = shape(); return C.chunk(entries, g.cols).map(items => ({ type: "cards", h: g.rowH, items, cw: g.cw })); };

  function buildView() {
    const lib = S.lib, rows = [], g = shape();
    const v = { rows, key: S.q ? "search" : S.view === "collection" ? "collection/" + S.collection : S.view, rail: null, sort: false, back: false, home: false, geo: `${g.cols}/${Math.round(g.cw)}` };
    const title = (glyph, count, text) => rows.push({ type: "title", h: 56, glyph, count, text, glue: true });
    const head = (glyph, count) => rows.push({ type: "head", h: 40, glyph, count, glue: true });
    const msg = (text, glyph) => rows.push({ type: "msg", h: 96, text, glyph });
    const n = a => a.length.toLocaleString();
    if (S.q) {
      const found = C.search(lib, S.q);
      if (!found.items.length && !found.collections.length) msg(`Nothing in the library matches “${S.q}”.`, "search");
      if (found.collections.length) { head("stack", n(found.collections)); rows.push(...posterRows(found.collections)); }
      if (found.items.length) { head("lens", n(found.items)); rows.push(...posterRows(found.items)); }
      return v;
    }
    switch (S.view) {
      case "movies": {
        const items = C.sortFilms(lib.movies, S.sort);
        title("lens", n(items));
        const base = rows.length;
        rows.push(...posterRows(items));
        if (S.sort === "title") v.rail = C.letterRows(items, g.cols, base);
        v.sort = true;
        break;
      }
      case "collections": {
        title("stack", n(lib.collections));
        const base = rows.length;
        rows.push(...posterRows(lib.collections));
        v.rail = C.letterRows(lib.collections, g.cols, base);
        break;
      }
      case "collection": {
        v.back = true;
        const c = lib.collById.get(S.collection);
        if (!c) { msg("That collection isn't in the library any more.", "stack"); break; }
        const items = c.itemIds.map(id => lib.byId.get(id)).filter(Boolean);
        title("stack", n(items), c.title);
        if (!items.length) msg("Jellyfin lists no films in this collection right now.", "stack");
        rows.push(...posterRows(S.sort === "title" ? items : C.sortFilms(items, S.sort)));
        v.sort = true;
        break;
      }
      case "continue": {
        const items = C.continueWatching(lib, S.local, S.catalogAt);
        title("play", n(items));
        if (!items.length) msg("Nothing started yet. A film you stop part-way waits here.", "play");
        rows.push(...posterRows(items));
        break;
      }
      case "added": {
        const items = C.recentlyAdded(lib, 60);
        title("new", "");
        if (!items.length) msg("The library hasn't said when films were added.", "new");
        rows.push(...posterRows(items));
        break;
      }
      default:
        v.key = "home"; v.home = true;
    }
    if (!lib.items.length && !v.home) msg("Jellyfin's library is empty.", "lens");
    return v;
  }

  /** Home: a control for each way into the library, the count on it. */
  function buildDeck() {
    const lib = S.lib, deck = $("#deck"), cont = C.continueWatching(lib, S.local, S.catalogAt);
    const units = [];
    if (cont.length) units.push(["continue", "play", LABELS.continue, String(cont.length), true]);
    if (lib.movies.some(i => i.added > 0)) units.push(["added", "new", LABELS.added, ""]);
    units.push(["movies", "lens", LABELS.movies, lib.movies.length.toLocaleString()]);
    if (lib.collections.length) units.push(["collections", "stack", LABELS.collections, lib.collections.length.toLocaleString()]);
    const count = units.length;
    deck.style.setProperty("--rows", Math.ceil(count / 2)); deck.style.setProperty("--rows-wide", Math.ceil(count / 4)); deck.style.setProperty("--rows-side", Math.ceil(count / 5));
    deck.replaceChildren(...units.map(([id, glyph, label, num, lit], k) => {
      const u = el("div", "unit named"); u.style.setProperty("--i", k);
      if (lit) u.dataset.s = "on";
      const hit = el("button", "hit"); hit.type = "button"; hit.dataset.u = id; hit.setAttribute("aria-label", label); hit.title = label;
      u.append(el("i", "plate"), hit);
      const claws = el("i", "claws"); claws.innerHTML = "<b></b><b></b><b></b>"; u.append(claws);
      if (lit) u.append(el("span", "lamp"));
      u.insertAdjacentHTML("beforeend", ico(glyph));
      if (num) u.append(el("span", "name", num));
      return u;
    }));
    deck.hidden = false;
  }
  $("#deck").addEventListener("click", ev => { const b = ev.target.closest("[data-u]"); if (b && S.lib) go(b.dataset.u); });

  function renderView(keep) {
    if (!S.lib) return;
    if (S.viewKey && !keep && S.viewRows) S.pageMemo.set(S.viewKey, list.page);
    const v = buildView();
    S.viewKey = v.key; S.viewRows = v.rows.length; S.geo = v.geo; S.cur = v;
    $("#back").hidden = v.home || !!S.q;
    const sort = $("#sort");
    sort.hidden = !(v.sort && !S.q);
    if (!sort.hidden) { sort.setAttribute("aria-label", `Sort: ${SORT_LABELS[S.sort]}`); sort.title = `Sort: ${SORT_LABELS[S.sort]}`; }
    $("#deck").hidden = !v.home; $("#listwrap").hidden = v.home;
    if (v.home) { buildDeck(); rail.on = false; rail.items = []; $("#letters-key").hidden = true; return; }
    const page = keep ? list.page : (S.fresh ? 0 : (S.pageMemo.get(v.key) || 0));
    rail.items = v.rail || []; rail.on = rail.items.length > 3;
    $("#letters-key").hidden = !rail.on;
    list.setRows(v.rows, page);
    S.fresh = false;
  }
  const SORT_LABELS = { title: "A to Z", added: "Recently added", year: "Newest first", rating: "Best rated" };
  function cycleSort() {
    const at = C.SORTS.indexOf(S.sort);
    S.sort = C.SORTS[(at + 1) % C.SORTS.length];
    store.set("sort", S.sort);
    S.fresh = true; renderView(false);
    toast(SORT_LABELS[S.sort]);
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

  // ═══ the address: #home #movies #collections #continue #added #collection/<id> #film/<id> #watch/<id> (Back closes what was opened) ══
  let booted = false;
  function route() {
    if (!S.lib) return;
    const raw = location.hash.slice(1) || "home";
    const [path, query] = raw.split("?");
    const [kind, id] = path.split("/");
    if (kind === "watch" && S.lib.byId.has(id)) {
      closeDetails(true);
      if (!P.item || P.item.id !== id) restoreFilm(S.lib.byId.get(id));
      booted = true;
      return;
    }
    if (P.item) closePlayer(true);
    if (kind === "film" && S.lib.byId.has(id)) {
      openDetails(S.lib.byId.get(id));
      if (!booted) { booted = true; renderView(false); }
      return;
    }
    closeDetails(true);
    if (S.letters) closeLetters();
    booted = true;
    if (S.q) clearSearch(true);
    if (kind === "collection" && S.lib.collById.has(id)) { S.view = "collection"; S.collection = id; }
    else if (VIEWS.includes(kind)) S.view = kind;
    else S.view = "home";
    if (kind === "movies" && query === "added") { S.sort = "added"; store.set("sort", S.sort); S.fresh = true; }
    renderView(false);
  }
  addEventListener("hashchange", route);
  /** Open something as a new history entry (so Back closes it). The entry is marked, so closing knows whether Back stays in
   *  the app: a film opened straight from a bookmark or a reload has no app entry behind it, and is replaced instead. */
  function go(hash) {
    if (S.q) clearSearch(true);
    if (location.hash !== "#" + hash) history.pushState({ nv: 1 }, "", "#" + hash);
    else if (S.cur && S.cur.home === false) { S.fresh = true; }
    route();
  }
  function leave(fallback) {
    if (history.state && history.state.nv) history.back();
    else { history.replaceState(null, "", "#" + fallback); route(); }
  }
  const parentHash = () => (S.view === "collection" ? "collections" : "home");
  function goBack() { if (history.state && history.state.nv) history.back(); else { history.replaceState(null, "", "#" + parentHash()); route(); } }

  // ═══ the details sheet ═══════════════════════════════════════════════════════════════════════════════════════════
  let lastFocus = null;
  function openDetails(it) {
    S.detailsId = it.id;
    lastFocus = lastFocus || document.activeElement;
    $("#d-title").textContent = it.title;
    $("#d-facts").textContent = C.factsLine(it);
    $("#d-genres").textContent = it.genres.join(" · ");
    $("#d-overview").textContent = it.overview || "";
    const bd = $("#d-backdrop"), ps = $("#d-poster");
    bd.className = "d-backdrop"; ps.className = "d-poster";
    bd.querySelector("img").removeAttribute("src"); ps.querySelector("img").removeAttribute("src");
    bd.hidden = !it.hasBackdrop;
    if (it.hasBackdrop) picture(bd, it.id, "backdrop", Math.min(innerWidth, 900), "");
    ps.hidden = !it.hasPoster;
    if (it.hasPoster) picture(ps, it.id, "poster", 200, `Poster: ${it.title}`);
    const acts = $("#d-actions");
    const at = C.resumePoint(it, S.local[it.id], S.catalogAt);
    const kids = [];
    if (!it.play) kids.push(el("p", "warn", "Jellyfin has no video file for this one."));
    else if (at) {
      kids.push(actKey(`Resume from ${C.fmtTime(at)}`, "play", true, () => startFilm(it, { start: at }), C.fmtTime(at)));
      kids.push(actKey("Start over", "retry", false, () => startFilm(it, { start: 0 })));
    } else kids.push(actKey(it.played ? "Play again" : "Play", "play", true, () => startFilm(it, { start: 0 })));
    acts.replaceChildren(...kids);
    const tech = [];
    if (it.play) {
      tech.push(it.play.direct ? "Plays as it is" : "Converted on skippy-pc as it plays");
      if (it.play.height) tech.push(`${it.play.height}p`);
      if (it.play.audio.length > 1) tech.push(`${it.play.audio.length} audio tracks`);
      const subs = it.play.subs.filter(s => s.vtt);
      if (subs.length) tech.push(`Subtitles: ${subs.map(C.subLabel).join(", ")}`);
    }
    $("#d-tech").textContent = tech.join(" · ");
    const coll = $("#d-coll");
    coll.replaceChildren(...it.collectionIds.filter(id => S.lib.collById.has(id)).map(id => {
      const c = S.lib.collById.get(id), a = el("a", "chip");
      a.innerHTML = ico("stack"); a.append(el("span", "", c.title));
      a.href = "#collection/" + id; a.setAttribute("aria-label", `Part of ${c.title}`);
      return a;
    }));
    sheet.hidden = false;
    document.body.classList.add("nv-locked");
    setTimeout(() => { const first = acts.querySelector("button") || sheet.querySelector(".key"); first.focus(); }, 0);
  }
  /** Play, Resume, Start over: a glyph key (the time on it when it resumes). */
  function actKey(label, icon, primary, fn, time) {
    const b = key(icon, label, "act" + (primary ? " on" : ""), fn);
    if (time) b.append(el("span", "t", time));
    return b;
  }
  function closeDetails(fromRoute) {
    if (sheet.hidden) return;
    sheet.hidden = true;
    S.detailsId = null;
    document.body.classList.remove("nv-locked");
    if (!fromRoute && location.hash.startsWith("#film/")) leave(S.view === "collection" ? "collection/" + S.collection : S.view);
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
    lastFocus = null;
  }

  // ═══ the player ══════════════════════════════════════════════════════════════════════════════════════════════════
  const P = { item: null, mode: "", engine: "", hls: null, audio: null, start: 0, progress: new C.Progress(), healed: 0, loadingHls: null, lastLocal: 0 };

  function hlsEnv() {
    return { native: !!video.canPlayType("application/vnd.apple.mpegurl"), webkit: WEBKIT,
             mse: !!(window.MediaSource || window.ManagedMediaSource) && typeof (window.MediaSource || window.ManagedMediaSource).isTypeSupported === "function" };
  }
  function loadHlsJs() {
    if (window.Hls) return Promise.resolve(window.Hls);
    if (!P.loadingHls) {
      P.loadingHls = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = cfg.hls;
        s.onload = () => (window.Hls ? resolve(window.Hls) : reject(new Error("hls.js didn't load")));
        s.onerror = () => { P.loadingHls = null; reject(new Error("hls.js didn't load")); };
        document.head.appendChild(s);
      });
    }
    return P.loadingHls;
  }

  /** The Play / Resume / Start over buttons: a tap, so the browser will let the film make sound. */
  function startFilm(it, opts) {
    closeDetails(true);
    open(it, Object.assign({ tap: false }, opts));
    if (location.hash !== "#watch/" + it.id) history.pushState({ nv: 1 }, "", "#watch/" + it.id);     // Back (or Esc) closes the film
  }
  /** A reload with #watch/<id> in the address: the film comes back where it was, waiting for the one tap a reload needs. */
  function restoreFilm(it) {
    if (!it.play) { go("film/" + it.id); return; }
    open(it, { start: C.resumePoint(it, S.local[it.id], S.catalogAt), tap: true });
  }

  async function open(it, opts) {
    if (P.item && P.item.id !== it.id) finish(false);
    teardown();
    P.item = it; P.audio = opts.audio == null ? null : opts.audio; P.start = opts.start || 0; P.healed = opts.healed || 0;
    $("#p-title").textContent = it.title;
    player.hidden = false;
    document.body.classList.add("nv-locked", "nv-playing");
    hideMsg();
    spin(true);
    paintMenus();
    setSession(it);
    applyVolume();
    idle.kick();
    let g;
    try { g = await gate.ensure(); } catch (e) {
      if (opts.recovering && e.kind === "net") return;                    // still no line: the next try is already on its way
      if (e.kind === "net") { recoverStart("net"); return; }
      spin(false); showMsg(e.kind === "auth" ? e.message : e.kind === "home" ? e.message : C.playProblem(0), () => open(it, opts)); return;
    }
    if (P.item !== it) return;
    const pick = C.chooseSource(it, { audio: P.audio, env: hlsEnv(), forceHls: opts.forceHls });
    if (pick.mode === "none") { spin(false); showMsg(pick.why); return; }
    P.mode = pick.mode; P.engine = pick.engine || "";
    if (pick.mode === "direct") {
      addTracks(g, it);
      video.src = C.directUrl(g, it.id) + (P.start > 0 ? `#t=${Math.floor(P.start)}` : "");
    } else if (pick.engine === "native") {
      addTracks(g, it);
      video.src = C.hlsUrl(g, it, { audio: P.audio, start: P.start });
    } else {
      let Hls;
      try { Hls = await loadHlsJs(); } catch (e) { if (opts.recovering) return; spin(false); showMsg("The converted stream's player (hls.js) didn't load. Reload the page to try again."); return; }
      if (P.item !== it) return;
      if (!Hls.isSupported()) { spin(false); showMsg("This browser can't play the converted stream."); return; }
      // The subtitles are the gate's own WebVTT files on <track> elements, not part of the stream, so hls.js's caption and
      // subtitle handling is off: left on, it empties every text track's cues when it loads a manifest (measured on Tetsuo II:
      // the track said "loaded" with 0 cues). The <track>s are added only once the manifest is in, for the same reason.
      const hls = new Hls({ startPosition: P.start > 0 ? P.start : -1, maxBufferLength: 90, maxMaxBufferLength: 240, enableWorker: true,
                            enableWebVTT: false, enableIMSC1: false, enableCEA708Captions: false, renderTextTracksNatively: true,
                            fragLoadingMaxRetry: 4, manifestLoadingMaxRetry: 1, levelLoadingMaxRetry: 2 });
      P.hls = hls;
      hls.on(Hls.Events.ERROR, (ev, data) => onHlsError(hls, data));
      hls.once(Hls.Events.MANIFEST_PARSED, () => { if (P.hls === hls) addTracks(g, it); });
      hls.loadSource(C.hlsUrl(g, it, { audio: P.audio, start: P.start }));
      hls.attachMedia(video);
    }
    if (!opts.recovering) report(P.progress.start(it.id, P.start));
    if (opts.tap && !S.armed.armed) { S.armed.waiting(); showTap(true); spin(false); return; }
    tryPlay();
  }

  function addTracks(g, it) {
    for (const t of video.querySelectorAll("track")) t.remove();
    const subs = it.play ? it.play.subs.filter(s => s.vtt) : [];
    for (const s of subs) {
      const t = document.createElement("track");
      t.kind = "subtitles"; t.label = C.subLabel(s); t.dataset.index = s.i;
      const lang = (s.lang || "").slice(0, 3);
      if (/^[a-z]{2,3}$/.test(lang)) t.srclang = lang;
      t.src = C.subUrl(g, it.id, s.i);
      video.appendChild(t);
    }
    // a subtitle choice carries over from one film to the next when the next has the same language
    setTimeout(() => { const want = S.prefs.subs; const i = want ? subTracks().findIndex(t => t.label === want) : -1; showSub(i, true); }, 0);
  }
  function subTracks() { return Array.from(video.textTracks || []).filter(t => t.kind === "subtitles" && Array.from(video.querySelectorAll("track")).some(n => n.track === t)); }
  function showSub(index, quiet) {
    const tracks = subTracks();
    tracks.forEach((t, i) => { t.mode = i === index ? "showing" : "disabled"; });
    const on = index >= 0 && tracks[index];
    const b = $("#p-subs");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.classList.toggle("on", !!on);
    if (!quiet) { S.prefs.subs = on ? tracks[index].label : ""; store.set("prefs", S.prefs); say(on ? `Subtitles: ${tracks[index].label}` : "Subtitles off"); }
  }
  function currentSub() { return subTracks().findIndex(t => t.mode === "showing"); }

  function teardown() {
    if (P.hls) { try { P.hls.destroy(); } catch (e) { /* already gone */ } P.hls = null; }
    video.removeAttribute("src");
    for (const t of video.querySelectorAll("track")) t.remove();
    try { video.load(); } catch (e) { /* nothing loaded */ }
    P.mode = ""; P.engine = "";
  }

  function tryPlay() {
    const p = video.play();
    if (!p || !p.then) return;
    p.then(() => { S.armed.played(!video.muted); hideTap(); }, e => {
      if (e && e.name === "NotAllowedError") { S.armed.refused(); showTap(false); spin(false); }
      // AbortError: a new source replaced this one (a seek, a switch); the next play() is already on its way
    });
  }
  function showTap(restore) {
    const t = $("#tapbar");
    $("#tap-label").textContent = restore && P.start ? C.fmtTime(P.start) : "";
    t.hidden = false;
    setTimeout(() => t.querySelector("button").focus(), 0);
  }
  function hideTap() { $("#tapbar").hidden = true; }

  /** Close the film: the last progress report, the transcode freed (the gate stops it on "stop"), back to where it was opened. */
  function finish(ended) {
    if (!P.item) return;
    const pos = ended ? P.item.runtime || video.currentTime : video.currentTime;
    keepLocal(pos, ended);
    report(P.progress.stop(pos, ended));
  }
  function closePlayer(fromRoute) {
    if (!P.item) return;
    const it = P.item;
    finish(false);
    teardown();
    P.item = null;
    player.hidden = true;
    hideTap(); hideMsg(); closeMenu();
    document.body.classList.remove("nv-playing");
    if (sheet.hidden) document.body.classList.remove("nv-locked");
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    if (navigator.mediaSession) navigator.mediaSession.metadata = null;
    if (!fromRoute && location.hash.startsWith("#watch/")) leave("film/" + it.id);
    recoverStop();
    renderView(true);                                              // its "continue" bar and the shelf moved
    if (fromRoute && location.hash === "#film/" + it.id) return;
  }

  // ── progress: to the gate (Jellyfin's Continue Watching) and to this browser ──────────────────────────────────
  function keepLocal(pos, done) {
    if (!P.item || !(pos >= 0)) return;
    S.local = C.remember(S.local, P.item.id, pos, Date.now(), done || (P.item.runtime && pos >= P.item.runtime * 0.95));
    store.set("pos", S.local);
  }
  function report(body, retried) {
    const g = gate.info;
    if (!body || !g || !cfg.progress) return;      // a friend watching: kept in their own browser only (keepLocal)
    fetch(C.progressUrl(g), { method: "POST", body: JSON.stringify(body), keepalive: true,
                                headers: { "Content-Type": "text/plain" } })     // a "simple" request: no CORS preflight per report
      .then(r => { if (r.status === 403 && !retried) gate.ensure(true).then(() => report(body, true), () => {}); }, () => {});
  }
  addEventListener("pagehide", () => {
    if (!P.item || !gate.info) return;
    keepLocal(video.currentTime);
    const body = P.progress.stop(video.currentTime, false);
    if (body && navigator.sendBeacon) navigator.sendBeacon(C.progressUrl(gate.info), JSON.stringify(body));   // text/plain: the gate takes it
  });

  // ── when the line dies ──
  // A phone in the mountains drops off the network for minutes at a time. The film keeps its place and its wish to play, and a retry runs on
  // a back-off (2, 4, 8, 15, then every 30 s) for as long as it takes; it starts again at once when the browser says the network is back or
  // the page comes forward. What is already buffered keeps playing in the meantime (hls.js holds about 90 s ahead). Nothing is reported to
  // the gate while it waits. Closing the film, or pausing it, ends the wait.
  const rec = { on: false, n: 0, timer: 0, at: 0, id: "" };
  function recoverStart(why) {
    if (rec.on || !P.item) return;
    rec.on = true; rec.n = 0; rec.id = P.item.id;
    rec.at = video.currentTime > 1 ? video.currentTime : P.start || 0;
    spin(true); hideMsg(); syncSignal();
    if (navigator.mediaSession) { try { navigator.mediaSession.playbackState = "playing"; } catch (e) { /* ignore */ } }
    rec.timer = setTimeout(recoverTick, C.recoverDelay(0));
    if (why) say("The signal is lost; the film will carry on when it is back.");
  }
  function recoverStop() {
    if (!rec.on) return;
    clearTimeout(rec.timer); rec.on = false; rec.n = 0;
    syncSignal();
  }
  /** Try again right now (the network is back, the page is in front). */
  function recoverNow() { if (rec.on) { clearTimeout(rec.timer); recoverTick(); } }
  async function recoverTick() {
    if (!rec.on) return;
    clearTimeout(rec.timer);
    if (!P.item || P.item.id !== rec.id) { recoverStop(); return; }
    rec.timer = setTimeout(recoverTick, C.recoverDelay(++rec.n));      // if this try hangs without an error, the next one still comes
    if (video.readyState > 0 && !video.error && video.currentTime > 1) rec.at = video.currentTime;
    try { await gate.ensure(true); } catch (e) { return; }              // no line yet: wait for the next try
    if (!rec.on || !P.item || P.item.id !== rec.id) return;
    open(P.item, { start: rec.at, audio: P.audio, recovering: true, healed: 0 });
  }
  /** A film that was playing went quiet with the line: "waiting" and no progress for long enough. */
  setInterval(() => {
    if (!P.item || rec.on || video.paused || video.ended) return;
    if (C.stalled(Date.now(), S.lastTimeAt, S.waitingAt > S.lastTimeAt)) recoverStart("stalled");
  }, 2000);
  addEventListener("online", recoverNow);
  if (navigator.connection && navigator.connection.addEventListener) navigator.connection.addEventListener("change", recoverNow);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) recoverNow(); });
  addEventListener("pageshow", recoverNow);

  // ── errors: say what happened in a sentence; heal what can be healed (a stale pass, a file the browser refuses) ──
  async function onVideoError() {
    if (!P.item || !video.error || P.hls || rec.on) return;        // hls.js reports its own; a wait for the line is already running
    const it = P.item, at = video.currentTime || P.start, mode = P.mode;
    spin(false);
    let status = -1;
    if (mode === "direct") {
      try { status = (await fetch(C.directUrl(gate.info, it.id), { method: "HEAD", cache: "no-store" })).status; } catch (e) { status = 0; }
    }
    if (P.item !== it) return;
    if (status === 403 || (mode !== "direct" && P.healed < 1)) {           // the pass ran out (or the HLS stream dropped): new pass, same place
      try { await gate.ensure(true); } catch (e) { if (e.kind === "net") { recoverStart("net"); return; } showMsg(e.message); return; }
      if (P.healed < 2) return open(it, { start: at, audio: P.audio, healed: P.healed + 1 });
    }
    if (mode === "direct" && (status === 200 || status === 206 || status === 409) && P.healed < 2) {
      say("This browser couldn't play the file as it is; converting it on skippy-pc instead.");
      return open(it, { start: at, audio: P.audio, forceHls: true, healed: P.healed + 1 });
    }
    if (status === 0) { recoverStart("net"); return; }                       // the line, not the film: wait for it
    showMsg(status > 0 ? C.playProblem(status, mode) : C.playProblem(0), () => open(it, { start: at, audio: P.audio }));
  }
  function onHlsError(hls, data) {
    if (!data || !data.fatal || hls !== P.hls || rec.on) return;
    const it = P.item, at = video.currentTime || P.start;
    const code = data.response && data.response.code;
    if (data.type === "networkError" && !code) { recoverStart("hls"); return; }      // no HTTP answer at all: the line
    if (code === 403 && P.healed < 2) {
      gate.ensure(true).then(() => open(it, { start: at, audio: P.audio, healed: P.healed + 1 }), e => { if (e.kind === "net") recoverStart("net"); else showMsg(e.message); });
      return;
    }
    if (code === 429) { spin(false); showMsg(C.playProblem(429, "hls"), () => open(it, { start: at, audio: P.audio })); return; }
    if (data.type === "mediaError" && P.healed < 2) { P.healed++; hls.recoverMediaError(); return; }
    if (data.type === "networkError" && P.healed < 2 && code !== 404) { P.healed++; hls.startLoad(at); return; }
    spin(false);
    showMsg(code ? C.playProblem(code, "hls") : C.playProblem(0), () => open(it, { start: at, audio: P.audio }));
  }
  video.addEventListener("error", onVideoError);

  function showMsg(text, retry) {
    const m = $("#p-msg");
    const acts = el("div", "acts");
    if (retry) acts.append(key("retry", "Try again", "on", () => { hideMsg(); retry(); }));
    acts.append(key("close", "Close the film", "", () => closePlayer(false)));
    m.replaceChildren(el("p", "", text), acts);
    m.hidden = false;
  }
  function hideMsg() { $("#p-msg").hidden = true; }
  function spin(on) { $("#p-spin").hidden = !on; }

  // ── the controls ───────────────────────────────────────────────────────────────────────────────────────────────
  const seek = $("#p-seek");
  let dragging = false;
  function duration() { const d = video.duration; return isFinite(d) && d > 0 ? d : (P.item && P.item.runtime) || 0; }
  function paintTime() {
    const d = duration(), t = dragging ? +seek.value : video.currentTime;
    if (!dragging) { seek.max = String(Math.floor(d)); seek.value = String(Math.floor(t)); }
    seek.style.setProperty("--p", d ? ((100 * t) / d).toFixed(2) + "%" : "0%");
    seek.setAttribute("aria-valuetext", `${C.fmtTime(t)} of ${C.fmtTime(d)}`);
    $("#p-time").textContent = `${C.fmtTime(t)} / ${C.fmtTime(d)}`;
  }
  function paintPlay() {
    const b = $("#p-play"), paused = video.paused;
    setIcon(b, paused ? "play" : "pause");
    b.setAttribute("aria-label", paused ? "Play" : "Pause");
    player.dataset.paused = paused ? "1" : "0";
  }
  function applyVolume() {
    video.volume = Math.min(1, Math.max(0, S.prefs.vol));
    video.muted = !!S.prefs.muted;
    $("#p-vol").value = String(S.prefs.vol);
    const m = $("#p-mute");
    setIcon(m, S.prefs.muted || S.prefs.vol === 0 ? "mute" : "vol");
    m.setAttribute("aria-pressed", S.prefs.muted ? "true" : "false");
    m.setAttribute("aria-label", S.prefs.muted ? "Unmute" : "Mute");
  }
  function savePrefs() { store.set("prefs", S.prefs); }
  seek.addEventListener("input", () => { dragging = true; paintTime(); idle.kick(); });
  seek.addEventListener("change", () => { dragging = false; video.currentTime = +seek.value; paintTime(); });
  $("#p-vol").addEventListener("input", e => { S.prefs.vol = +e.target.value; if (S.prefs.vol > 0) S.prefs.muted = false; applyVolume(); savePrefs(); });

  video.addEventListener("timeupdate", () => {
    S.lastTimeAt = Date.now();
    paintTime();
    report(P.progress.tick(video.currentTime, !video.paused));
    if (Date.now() - P.lastLocal > 5000) { P.lastLocal = Date.now(); keepLocal(video.currentTime); }
    positionState();
  });
  video.addEventListener("play", () => { paintPlay(); idle.kick(); });
  video.addEventListener("playing", () => { S.lastTimeAt = Date.now(); spin(false); hideMsg(); hideTap(); recoverStop(); });
  video.addEventListener("waiting", () => { S.waitingAt = Date.now(); spin(true); });
  video.addEventListener("canplay", () => spin(false));
  video.addEventListener("pause", () => { paintPlay(); idle.kick(); if (P.item && !video.ended) { keepLocal(video.currentTime); report(P.progress.pause(video.currentTime)); } });
  video.addEventListener("seeked", () => { if (P.item && video.paused) report(P.progress.pause(video.currentTime)); });
  video.addEventListener("loadedmetadata", () => {
    paintTime();
    // direct play takes its start from the #t= fragment and hls.js from startPosition; native HLS (Safari) needs telling
    if (P.start > 0 && Math.abs(video.currentTime - P.start) > 3) { try { video.currentTime = P.start; } catch (e) { /* not seekable yet */ } }
  });
  video.addEventListener("ended", () => { finish(true); paintPlay(); say("The end."); });
  video.addEventListener("volumechange", () => { if (video.muted !== !!S.prefs.muted) { S.prefs.muted = video.muted; applyVolume(); savePrefs(); } });
  // On a touch screen the first tap on a film whose controls have faded only brings them back (it must not also pause it).
  let wakeTap = false;
  player.addEventListener("pointerdown", e => { wakeTap = player.dataset.idle === "1" && e.pointerType !== "mouse"; }, { capture: true });
  video.addEventListener("click", () => { if (wakeTap) { wakeTap = false; return; } act("toggle"); });
  video.addEventListener("dblclick", () => act("fullscreen"));

  /** Controls fade after three seconds of nothing while the film plays; any touch, mouse move or key brings them back. */
  const idle = {
    t: 0,
    kick() {
      player.dataset.idle = "0";
      clearTimeout(this.t);
      this.t = setTimeout(() => {
        const f = document.activeElement;
        const typing = f && f.closest && f.closest(".p-ctl") && f.matches(":focus-visible");    // a keyboard user is in the controls
        if (!video.paused && $("#p-menu").hidden && !typing) player.dataset.idle = "1";
      }, 3000);
    },
  };
  for (const ev of ["pointermove", "pointerdown", "keydown", "focusin"]) player.addEventListener(ev, () => idle.kick(), { passive: true });

  // ── menus: subtitles and audio track ───────────────────────────────────────────────────────────────────────────
  function paintMenus() {
    const it = P.item;
    const subs = it && it.play ? it.play.subs.filter(s => s.vtt) : [];
    $("#p-subs").hidden = !subs.length;
    $("#p-audio").hidden = !(it && it.play && it.play.audio.length > 1);
    $("#p-pip").hidden = !(document.pictureInPictureEnabled || video.webkitSupportsPresentationMode);
  }
  function openMenu(kind, anchor) {
    const m = $("#p-menu"), it = P.item;
    if (!it) return;
    const items = [];
    if (kind === "subs") {
      const cur = currentSub();
      items.push(["Off", cur < 0, () => showSub(-1)]);
      subTracks().forEach((t, i) => items.push([t.label, cur === i, () => showSub(i)]));
    } else {
      const labels = C.audioLabels(it.play.audio);
      const cur = P.audio == null ? it.play.audio[0].i : P.audio;
      it.play.audio.forEach((a, i) => items.push([labels[i], a.i === cur, () => {
        if (a.i === cur) return;
        // a browser can't switch a plain MP4's audio: another track means the converted stream, from where we are
        say(`Switching to ${labels[i]}`);
        open(it, { start: video.currentTime, audio: a.i });
      }]));
    }
    m.replaceChildren(el("p", "m-head", kind === "subs" ? "Subtitles" : "Audio"), ...items.map(([label, on, fn]) => {
      const b = el("button", "m-item", label);
      b.type = "button";
      b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.onclick = () => { closeMenu(); fn(); };
      return b;
    }));
    m.dataset.for = anchor.id;
    m.hidden = false;
    anchor.setAttribute("aria-expanded", "true");
    (m.querySelector('[aria-checked="true"]') || m.querySelector("button")).focus();
  }
  function closeMenu() {
    const m = $("#p-menu");
    if (m.hidden) return;
    m.hidden = true;
    const a = document.getElementById(m.dataset.for || "");
    if (a) { a.setAttribute("aria-expanded", "false"); a.focus(); }
  }
  $("#p-menu").addEventListener("keydown", e => {
    const btns = Array.from($("#p-menu").querySelectorAll("button")), i = btns.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); e.stopPropagation(); btns[(i + (e.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length].focus(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(); }
  });
  document.addEventListener("pointerdown", e => { if (!$("#p-menu").hidden && !e.target.closest("#p-menu, #p-subs, #p-audio")) closeMenu(); });

  // ── full screen, picture in picture, the lock screen ───────────────────────────────────────────────────────────
  function fullscreen() {
    const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fsEl) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
    const req = player.requestFullscreen || player.webkitRequestFullscreen;
    if (req) { Promise.resolve(req.call(player)).catch(() => {}); return; }
    if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();      // iPhone: only the video itself can go full screen
  }
  function paintFs() {
    const on = !!(document.fullscreenElement || document.webkitFullscreenElement);
    const b = $("#p-fs");
    setIcon(b, on ? "fsx" : "fs");
    b.setAttribute("aria-label", on ? "Leave full screen" : "Full screen");
    if (on && screen.orientation && screen.orientation.lock) screen.orientation.lock("landscape").catch(() => {});
  }
  document.addEventListener("fullscreenchange", paintFs);
  document.addEventListener("webkitfullscreenchange", paintFs);
  function pip() {
    if (document.pictureInPictureElement) { document.exitPictureInPicture().catch(() => {}); return; }
    if (document.pictureInPictureEnabled && !video.disablePictureInPicture) { video.requestPictureInPicture().catch(() => {}); return; }
    if (video.webkitSupportsPresentationMode && video.webkitSupportsPresentationMode("picture-in-picture")) {
      video.webkitSetPresentationMode(video.webkitPresentationMode === "picture-in-picture" ? "inline" : "picture-in-picture");
    }
  }
  function setSession(it) {
    if (!("mediaSession" in navigator)) return;
    const g = gate.info, art = [];
    if (g && it.hasPoster) for (const w of [320, 640]) art.push({ src: C.imgUrl(g, it.id, "poster", w), sizes: `${w}x${Math.round(w * 1.5)}`, type: "image/jpeg" });
    try {
      navigator.mediaSession.metadata = new MediaMetadata({ title: it.title, artist: [it.year || "", "Nagatha's Video Service"].filter(Boolean).join(" · "), artwork: art });
    } catch (e) { /* an older browser */ }
    const h = {
      play: () => act("play"), pause: () => act("pause"), stop: () => closePlayer(false),
      seekbackward: d => seekBy(-((d && d.seekOffset) || 10)), seekforward: d => seekBy((d && d.seekOffset) || 10),
      seekto: d => { if (d && isFinite(d.seekTime)) video.currentTime = d.seekTime; },
    };
    for (const [k, fn] of Object.entries(h)) { try { navigator.mediaSession.setActionHandler(k, fn); } catch (e) { /* not supported here */ } }
  }
  let lastPos = 0;
  function positionState() {
    if (!("mediaSession" in navigator) || !navigator.mediaSession.setPositionState || Date.now() - lastPos < 2000) return;
    lastPos = Date.now();
    const d = duration();
    if (d > 0) { try { navigator.mediaSession.setPositionState({ duration: d, position: Math.min(video.currentTime, d), playbackRate: video.playbackRate || 1 }); } catch (e) { /* ignore */ } }
  }
  function seekBy(by) { video.currentTime = C.seekTo(video.currentTime, by, duration()); paintTime(); idle.kick(); }

  // ═══ actions: buttons, keys and the lock screen all end up here ═══════════════════════════════════════════════════
  function act(name, src) {
    switch (name) {
      case "toggle": if (!P.item) return; if (rec.on) { recoverStop(); spin(false); return; } if (video.paused) act("play"); else video.pause(); return;
      case "play": hideTap(); tryPlay(); return;
      case "pause": video.pause(); return;
      case "tap": hideTap(); S.armed.played(true); tryPlay(); return;
      case "back10": return seekBy(-10);
      case "fwd10": return seekBy(10);
      case "back30": return seekBy(-30);
      case "fwd30": return seekBy(30);
      case "restart": video.currentTime = 0; return;
      case "volUp": case "volDown":
        S.prefs.vol = Math.round(Math.min(1, Math.max(0, S.prefs.vol + (name === "volUp" ? 0.1 : -0.1))) * 100) / 100;
        if (S.prefs.vol > 0) S.prefs.muted = false;
        applyVolume(); savePrefs(); say(`Volume ${Math.round(S.prefs.vol * 100)}%`); return;
      case "mute": S.prefs.muted = !S.prefs.muted; applyVolume(); savePrefs(); return;
      case "subs": { const tracks = subTracks(); const n = C.nextSub(currentSub(), tracks.length); if (tracks.length) showSub(n); return; }
      case "subs-menu": return $("#p-menu").hidden ? openMenu("subs", src || $("#p-subs")) : closeMenu();
      case "audio-menu": return $("#p-menu").hidden ? openMenu("audio", src || $("#p-audio")) : closeMenu();
      case "fullscreen": return fullscreen();
      case "pip": return pip();
      case "close": if (!$("#p-menu").hidden) return closeMenu(); return closePlayer(false);
      case "close-player": return closePlayer(false);
      case "close-details": return closeDetails(false);
      case "clear-search": clearSearch(); $("#q").focus(); return;
      case "search": closeDetails(false); $("#q").focus(); $("#q").select(); return;
      case "back": return goBack();
      case "home": return go("home");
      case "sort": return cycleSort();
      case "letters": return openLetters();
      case "help": { const k = $("#keys"); if (k.open) k.close(); else if (k.showModal) k.showModal(); return; }
      default:
    }
  }
  root.addEventListener("click", e => {
    const b = e.target.closest("[data-act]");
    if (b && root.contains(b)) {
      // a mouse click leaves no focus on a player button, so Space afterwards pauses the film instead of pressing that button again
      if (e.detail > 0 && b.closest("#player") && !b.closest("#p-menu, #tapbar")) b.blur();
      act(b.dataset.act, b);
      return;
    }
    const c = e.target.closest(".film");
    if (!c) return;
    if (c.dataset.coll) go("collection/" + c.dataset.coll);
    else if (c.dataset.id) { lastFocus = c; go("film/" + c.dataset.id); }
  });
  sheet.addEventListener("click", e => { if (e.target === sheet) closeDetails(false); });     // a tap outside the card
  sheet.addEventListener("keydown", e => {
    if (e.key === "Escape") { e.preventDefault(); closeDetails(false); return; }
    if (e.key !== "Tab") return;                                                           // keep Tab inside the open sheet
    const f = Array.from(sheet.querySelectorAll("button, a[href]")).filter(n => !n.closest("[hidden]"));
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  });
  document.addEventListener("keydown", e => {
    const t = e.target || {};
    if (e.key === "Escape" && S.letters) { closeLetters(); e.preventDefault(); return; }
    if ((e.key === "PageDown" || e.key === "PageUp") && !P.item && sheet.hidden && !$("#keys").open && !(t.tagName === "INPUT" || t.tagName === "TEXTAREA")) {
      e.preventDefault(); list.go(list.page + (e.key === "PageDown" ? 1 : -1)); return;
    }
    const a = C.keyAction({ key: e.key, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, altKey: e.altKey, targetTag: t.tagName,
                            editable: !!t.isContentEditable, onButton: t.tagName === "BUTTON" }, !!P.item && !player.hidden);
    if (!a) { if (e.key === "Escape" && !sheet.hidden) closeDetails(false); return; }
    if (a === "close" && $("#keys").open) return;                  // the dialog closes itself
    e.preventDefault();
    act(a);
  });

  // ── search ─────────────────────────────────────────────────────────────────────────────────────────────────────
  let qTimer = 0;
  function clearSearch(silent) {
    clearTimeout(qTimer);
    $("#q").value = ""; $("#q-clear").hidden = true;
    if (!S.q) return;
    S.q = "";
    if (!silent) { S.fresh = false; renderView(false); }
  }
  $("#q").addEventListener("input", e => {
    clearTimeout(qTimer);
    const v = e.target.value;
    $("#q-clear").hidden = !v;
    qTimer = setTimeout(() => {
      const was = S.q;
      if (!was && S.viewRows) S.pageMemo.set(S.viewKey, list.page);
      S.q = v.trim();
      S.fresh = !!S.q; renderView(false);
    }, 60);
  });
  $("#q").addEventListener("keydown", e => { if (e.key === "Escape" && e.target.value) { e.stopPropagation(); act("clear-search"); } });

  // ═══ start ═══════════════════════════════════════════════════════════════════════════════════════════════════════
  if (IOS) $("#p-vol").hidden = true;                              // iOS ignores video.volume: the hardware buttons own it
  paintIcons();
  applyVolume();
  paintPlay();
  syncSignal();
  if (window.ResizeObserver) {
    let rt = 0;
    new ResizeObserver(() => {
      clearTimeout(rt);
      rt = setTimeout(() => {
        if (!S.lib) return;
        const g = shape();
        if (S.cur && !S.cur.home && S.geo !== `${g.cols}/${Math.round(g.cw)}`) renderView(true); else list.resize();
      }, 120);
    }).observe($("#stage"));
  }
  loadCatalog();
})();
