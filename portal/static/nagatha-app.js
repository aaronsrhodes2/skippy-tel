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

  // ═══ icons (inline SVG paths, so the page needs no icon font or extra request) ═══════════════════════════════════
  const PATHS = {
    play: "M8 5v14l11-7z", pause: "M6 19h4V5H6v14zm8-14v14h4V5h-4z", close: "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
    back: "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z", search: "M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
    back10: "M12 5V1L7 6l5 5V7c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6H4c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8zm-1.1 11h-.85v-3.26l-1.01.31v-.69l1.77-.63h.09V16zm4.28-1.76c0 .32-.03.6-.1.82s-.17.42-.29.57-.28.26-.45.33-.37.1-.59.1-.41-.03-.59-.1-.33-.18-.46-.33-.23-.34-.3-.57-.11-.5-.11-.82v-.74c0-.32.03-.6.1-.82s.17-.42.29-.57.28-.26.45-.33.37-.1.59-.1.41.03.59.1.33.18.46.33.23.34.3.57.11.5.11.82v.74zm-.85-.86c0-.19-.01-.35-.04-.48s-.07-.23-.12-.31-.11-.14-.19-.17-.16-.05-.25-.05-.18.02-.25.05-.14.09-.19.17-.09.18-.12.31-.04.29-.04.48v.97c0 .19.01.35.04.48s.07.24.12.32.11.14.19.17.16.05.25.05.18-.02.25-.05.14-.09.19-.17.09-.19.11-.32.04-.29.04-.48v-.97z",
    fwd10: "M18 13c0 3.31-2.69 6-6 6s-6-2.69-6-6 2.69-6 6-6v4l5-5-5-5v4c-4.42 0-8 3.58-8 8s3.58 8 8 8 8-3.58 8-8h-2zm-7.46 2.22c-.06.05-.12.09-.2.12s-.17.04-.27.04a.68.68 0 0 1-.47-.17c-.06-.05-.11-.12-.14-.2s-.05-.16-.05-.25h-.84c0 .22.04.4.13.57s.21.31.36.42.32.2.51.25.39.09.59.09c.25 0 .48-.03.68-.1s.38-.16.53-.29.26-.28.34-.47.12-.39.12-.62c0-.26-.06-.48-.2-.66s-.34-.31-.6-.39c.11-.05.22-.11.31-.18s.17-.15.23-.23.11-.18.14-.27.05-.19.05-.28c0-.22-.04-.42-.11-.59s-.18-.32-.32-.44-.31-.21-.51-.27-.42-.09-.66-.09c-.2 0-.39.03-.57.09s-.33.14-.46.25-.24.24-.32.39-.12.32-.12.5h.84c0-.07.01-.14.04-.2s.06-.11.11-.15.1-.08.16-.1.13-.04.2-.04c.17 0 .3.04.38.13s.13.21.13.37c0 .08-.01.15-.04.21s-.06.12-.11.16-.11.08-.18.1-.16.04-.25.04h-.48v.65h.48c.1 0 .19.01.27.03s.15.06.21.11.1.11.13.18.05.16.05.26c0 .18-.05.32-.16.42z",
    vol: "M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z",
    mute: "M16.5 12A4.5 4.5 0 0 0 14 7.97v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51A8.796 8.796 0 0 0 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06a8.99 8.99 0 0 0 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4 9.91 6.09 12 8.18V4z",
    cc: "M19 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 7H9.5v-.5h-2v3h2V13H11v1c0 .55-.45 1-1 1H7c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1h3c.55 0 1 .45 1 1v1zm7 0h-1.5v-.5h-2v3h2V13H18v1c0 .55-.45 1-1 1h-3c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1h3c.55 0 1 .45 1 1v1z",
    audio: "M12 3v9.28a4.39 4.39 0 0 0-1.5-.28C8.01 12 6 14.01 6 16.5S8.01 21 10.5 21c2.31 0 4.2-1.75 4.45-4H15V6h4V3h-7z",
    pip: "M19 11h-8v6h8v-6zm4 8V4.98C23 3.88 22.1 3 21 3H3c-1.1 0-2 .88-2 1.98V19c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2zm-2 .02H3V4.97h18v14.05z",
    fs: "M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z",
    fsx: "M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z",
    keys: "M20 5H4c-1.1 0-1.99.9-1.99 2L2 17c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm-9 3h2v2h-2V8zm0 3h2v2h-2v-2zM8 8h2v2H8V8zm0 3h2v2H8v-2zm-1 2H5v-2h2v2zm0-3H5V8h2v2zm9 7H8v-2h8v2zm0-4h-2v-2h2v2zm0-3h-2V8h2v2zm3 3h-2v-2h2v2zm0-3h-2V8h2v2z",
    film: "M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z",
  };
  function svg(name) {
    const ns = "http://www.w3.org/2000/svg", s = document.createElementNS(ns, "svg"), p = document.createElementNS(ns, "path");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false");
    p.setAttribute("d", PATHS[name] || ""); s.appendChild(p);
    return s;
  }
  function paintIcons(scope) { for (const i of (scope || root).querySelectorAll("i[data-i]")) { i.replaceChildren(svg(i.dataset.i)); } }
  function setIcon(btn, name) { const i = btn.querySelector("i[data-i]"); if (i && i.dataset.i !== name) { i.dataset.i = name; i.replaceChildren(svg(name)); } }
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function say(text) { const l = $("#live"); l.textContent = ""; setTimeout(() => { l.textContent = text; }, 30); }

  // ═══ what this browser remembers (a convenience: everything works without it) ════════════════════════════════════
  const store = {
    get(k, d) { try { const v = localStorage.getItem("nagatha:" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("nagatha:" + k, JSON.stringify(v)); } catch (e) { /* private window or full: fine */ } },
  };
  const S = {
    lib: null, catalogAt: 0, view: "home", collection: null, q: "", sort: store.get("sort", "title"),
    local: store.get("pos", {}), prefs: store.get("prefs", { vol: 1, muted: false, subs: "" }), detailsId: null,
    armed: new C.Armed(() => (navigator.userActivation ? navigator.userActivation.hasBeenActive : undefined)),
  };

  // ═══ notices ═════════════════════════════════════════════════════════════════════════════════════════════════════
  function showNotice(msg, opts) {
    opts = opts || {};
    const n = $("#notice");
    n.className = "notice " + (opts.kind || "");
    n.dataset.key = opts.key || "";
    const kids = [el("span", "grow", msg)];
    if (opts.retry) { const b = el("button", "pill-btn", opts.label || "Try again"); b.type = "button"; b.onclick = opts.retry; kids.push(b); }
    n.replaceChildren(...kids);
    n.hidden = false;
  }
  function hideNotice(key) { const n = $("#notice"); if (!key || n.dataset.key === key) { n.hidden = true; n.dataset.key = ""; } }

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
      root.dataset.state = "ready";
      $("#q").disabled = false;
      route();
      return;
    }
    gateProblem(null, loadCatalog);
  }
  const wait = ms => new Promise(r => setTimeout(r, ms));
  function setLoading(text) { const v = $("#view"); if (!S.lib) v.replaceChildren(el("p", "empty", text)); }

  // ═══ pictures (the gate's sizes; a burst of uncached posters can get "busy, try again", so an <img> retries) ═══════
  function posterPx() { return innerWidth >= 1600 ? 200 : innerWidth >= 700 ? 160 : 120; }
  function picture(holder, id, kind, cssPx, label) {
    const g = gate.info;
    const img = holder.querySelector("img") || holder.appendChild(el("img"));
    img.alt = label || "";
    img.loading = "lazy";
    img.decoding = "async";
    if (!g) return;
    const url = C.imgUrl(g, id, kind, C.imgWidth(cssPx, devicePixelRatio || 1));
    let tries = 0;
    img.onerror = () => { if (tries++ < 3) setTimeout(() => { img.src = url; }, 1200 * tries); else holder.classList.add("noart"); };
    img.onload = () => holder.classList.add("loaded");
    img.src = url;
  }

  // ═══ the wall ════════════════════════════════════════════════════════════════════════════════════════════════════
  function card(it) {
    const b = el("button", "card");
    b.type = "button";
    b.dataset.id = it.id;
    b.setAttribute("role", "listitem");
    b.setAttribute("aria-label", `${it.title}${it.year ? ", " + it.year : ""}`);
    const poster = el("span", "poster");
    poster.appendChild(el("span", "ph", it.title));               // the title in the frame until (or unless) the poster arrives
    if (it.hasPoster) picture(poster, it.id, "poster", posterPx(), "");
    const at = C.resumePoint(it, S.local[it.id], S.catalogAt);
    if (at && it.runtime) { const bar = el("span", "prog"); bar.style.setProperty("--p", Math.min(100, (100 * at) / it.runtime).toFixed(1) + "%"); poster.appendChild(bar); }
    if (it.played) poster.appendChild(el("span", "seen", "Watched"));
    b.append(poster, el("span", "c-title", it.title), el("span", "c-sub", String(it.year || "")));
    return b;
  }
  function collCard(c) {
    const b = el("button", "card coll");
    b.type = "button";
    b.dataset.coll = c.id;
    b.setAttribute("role", "listitem");
    b.setAttribute("aria-label", `${c.title}, ${c.itemIds.length} films`);
    const poster = el("span", "poster");
    poster.appendChild(el("span", "ph", c.title));
    if (c.hasPoster) picture(poster, c.id, "poster", posterPx(), "");
    b.append(poster, el("span", "c-title", c.title), el("span", "c-sub", `${c.itemIds.length} film${c.itemIds.length === 1 ? "" : "s"}`));
    return b;
  }
  function shelf(title, nodes, more) {
    const s = el("section", "shelf");
    const h = el("h2", "", title);
    if (more) { const a = el("a", "more", "See all"); a.href = more; h.appendChild(a); }
    const row = el("div", "row");
    row.setAttribute("role", "list");
    row.setAttribute("aria-label", title);
    row.append(...nodes);
    s.append(h, row);
    return s;
  }
  function grid(nodes, label) {
    const g = el("div", "grid");
    g.setAttribute("role", "list");
    g.setAttribute("aria-label", label);
    g.append(...nodes);
    return g;
  }
  function sorted(items) {
    const by = S.sort;
    const out = items.slice();
    if (by === "added") out.sort((a, b) => b.added - a.added || C.cmp(a.sort, b.sort));
    else if (by === "year") out.sort((a, b) => b.year - a.year || C.cmp(a.sort, b.sort));
    else if (by === "rating") out.sort((a, b) => (b.rating ?? -1) - (a.rating ?? -1) || C.cmp(a.sort, b.sort));
    return out;
  }

  function renderView() {
    const lib = S.lib;
    if (!lib) return;
    const v = $("#view");
    const nodes = [];
    $("#sort").hidden = !(S.view === "movies" || S.view === "collection") || !!S.q;
    for (const a of $("#nav").querySelectorAll("a")) a.setAttribute("aria-current", !S.q && a.dataset.view === (S.view === "collection" ? "collections" : S.view) ? "page" : "false");
    if (S.q) {
      const found = C.search(lib, S.q);
      nodes.push(el("h2", "vhead", found.items.length || found.collections.length
        ? `${found.items.length} film${found.items.length === 1 ? "" : "s"} for “${S.q}”` : `Nothing in the library matches “${S.q}”.`));
      if (found.collections.length) nodes.push(shelf("Collections", found.collections.map(collCard)));
      if (found.items.length) nodes.push(grid(found.items.map(card), "Search results"));
    } else if (S.view === "movies") {
      nodes.push(el("h2", "vhead", `Movies · ${lib.movies.length}`), grid(sorted(lib.movies).map(card), "Movies"));
    } else if (S.view === "collections") {
      nodes.push(el("h2", "vhead", `Collections · ${lib.collections.length}`), grid(lib.collections.map(collCard), "Collections"));
    } else if (S.view === "collection" && lib.collById.has(S.collection)) {
      const c = lib.collById.get(S.collection);
      const items = c.itemIds.map(id => lib.byId.get(id));
      nodes.push(el("h2", "vhead", c.title), grid(S.sort === "title" ? items : sorted(items), c.title));     // the gate lists a collection by year
      if (!items.length) nodes.push(el("p", "empty", "Jellyfin lists no films in this collection right now."));
    } else {
      const cont = C.continueWatching(lib, S.local, S.catalogAt);
      if (cont.length) nodes.push(shelf("Continue watching", cont.slice(0, 20).map(card)));
      const added = C.recentlyAdded(lib, 20);
      if (added.length) nodes.push(shelf("Recently added", added.map(card), "#movies?added"));
      if (lib.collections.length) nodes.push(shelf("Collections", lib.collections.map(collCard), "#collections"));
      nodes.push(el("h2", "vhead", `Movies · ${lib.movies.length}`), grid(lib.movies.map(card), "Movies"));
    }
    if (!lib.items.length) nodes.push(el("p", "empty", "Jellyfin's library is empty."));
    v.replaceChildren(...nodes);
  }

  // ═══ the address: #home #movies #collections #collection/<id> #film/<id> #watch/<id> (Back closes what was opened) ══
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
      if (!booted) { booted = true; renderView(); }
      return;
    }
    closeDetails(true);
    booted = true;
    if (kind === "collection" && S.lib.collById.has(id)) { S.view = "collection"; S.collection = id; }
    else if (["home", "movies", "collections"].includes(kind)) S.view = kind;
    else S.view = "home";
    if (kind === "movies" && query === "added") { S.sort = "added"; $("#sort").value = "added"; }
    renderView();
    $("#scroll").scrollTop = 0;
  }
  addEventListener("hashchange", route);
  /** Open something as a new history entry (so Back closes it). The entry is marked, so closing knows whether Back stays in
   *  the app: a film opened straight from a bookmark or a reload has no app entry behind it, and is replaced instead. */
  function go(hash) {
    if (location.hash !== "#" + hash) history.pushState({ nv: 1 }, "", "#" + hash);
    route();
  }
  function leave(fallback) {
    if (history.state && history.state.nv) history.back();
    else { history.replaceState(null, "", "#" + fallback); route(); }
  }

  // ═══ the details sheet ═══════════════════════════════════════════════════════════════════════════════════════════
  let lastFocus = null;
  function openDetails(it) {
    S.detailsId = it.id;
    lastFocus = lastFocus || document.activeElement;
    $("#d-title").textContent = it.title;
    $("#d-facts").textContent = C.factsLine(it);
    $("#d-genres").textContent = it.genres.join(" · ");
    $("#d-overview").textContent = it.overview || "No summary in Jellyfin for this one.";
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
      kids.push(actBtn(`Resume from ${C.fmtTime(at)}`, "play", true, () => startFilm(it, { start: at })));
      kids.push(actBtn("Start over", "back10", false, () => startFilm(it, { start: 0 })));
    } else kids.push(actBtn(it.played ? "Play again" : "Play", "play", true, () => startFilm(it, { start: 0 })));
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
      const c = S.lib.collById.get(id), a = el("a", "chip", `Part of ${c.title}`);
      a.href = "#collection/" + id;
      return a;
    }));
    paintIcons(sheet);
    sheet.hidden = false;
    document.body.classList.add("nv-locked");
    setTimeout(() => { const first = acts.querySelector("button") || sheet.querySelector(".d-close"); first.focus(); }, 0);
  }
  function actBtn(label, icon, primary, fn) {
    const b = el("button", "pill-btn" + (primary ? " primary" : ""));
    b.type = "button";
    const i = el("i"); i.dataset.i = icon;
    b.append(i, el("span", "", label));
    b.onclick = fn;
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
    try { g = await gate.ensure(); } catch (e) { spin(false); showMsg(e.kind === "auth" ? e.message : e.kind === "home" ? e.message : C.playProblem(0), () => open(it, opts)); return; }
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
      try { Hls = await loadHlsJs(); } catch (e) { spin(false); showMsg("The converted stream's player (hls.js) didn't load. Reload the page to try again."); return; }
      if (P.item !== it) return;
      if (!Hls.isSupported()) { spin(false); showMsg("This browser can't play the converted stream."); return; }
      // The subtitles are the gate's own WebVTT files on <track> elements, not part of the stream, so hls.js's caption and
      // subtitle handling is off: left on, it empties every text track's cues when it loads a manifest (measured on Tetsuo II:
      // the track said "loaded" with 0 cues). The <track>s are added only once the manifest is in, for the same reason.
      const hls = new Hls({ startPosition: P.start > 0 ? P.start : -1, maxBufferLength: 30, maxMaxBufferLength: 120, enableWorker: true,
                            enableWebVTT: false, enableIMSC1: false, enableCEA708Captions: false, renderTextTracksNatively: true,
                            fragLoadingMaxRetry: 4, manifestLoadingMaxRetry: 1, levelLoadingMaxRetry: 2 });
      P.hls = hls;
      hls.on(Hls.Events.ERROR, (ev, data) => onHlsError(hls, data));
      hls.once(Hls.Events.MANIFEST_PARSED, () => { if (P.hls === hls) addTracks(g, it); });
      hls.loadSource(C.hlsUrl(g, it, { audio: P.audio, start: P.start }));
      hls.attachMedia(video);
    }
    report(P.progress.start(it.id, P.start));
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
    $("#tap-label").textContent = restore && P.start ? `Tap to continue at ${C.fmtTime(P.start)}` : "Tap to start";
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
    renderView();                                                  // its "continue" bar and the shelf moved
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

  // ── errors: say what happened in a sentence; heal what can be healed (a stale pass, a file the browser refuses) ──
  async function onVideoError() {
    if (!P.item || !video.error || P.hls) return;                  // hls.js reports its own
    const it = P.item, at = video.currentTime || P.start, mode = P.mode;
    spin(false);
    let status = -1;
    if (mode === "direct") {
      try { status = (await fetch(C.directUrl(gate.info, it.id), { method: "HEAD", cache: "no-store" })).status; } catch (e) { status = 0; }
    }
    if (P.item !== it) return;
    if (status === 403 || (mode !== "direct" && P.healed < 1)) {           // the pass ran out (or the HLS stream dropped): new pass, same place
      try { await gate.ensure(true); } catch (e) { showMsg(e.message); return; }
      if (P.healed < 2) return open(it, { start: at, audio: P.audio, healed: P.healed + 1 });
    }
    if (mode === "direct" && (status === 200 || status === 206 || status === 409) && P.healed < 2) {
      say("This browser couldn't play the file as it is; converting it on skippy-pc instead.");
      return open(it, { start: at, audio: P.audio, forceHls: true, healed: P.healed + 1 });
    }
    showMsg(status > 0 ? C.playProblem(status, mode) : C.playProblem(0), () => open(it, { start: at, audio: P.audio }));
  }
  function onHlsError(hls, data) {
    if (!data || !data.fatal || hls !== P.hls) return;
    const it = P.item, at = video.currentTime || P.start;
    const code = data.response && data.response.code;
    if (code === 403 && P.healed < 2) {
      gate.ensure(true).then(() => open(it, { start: at, audio: P.audio, healed: P.healed + 1 }), e => showMsg(e.message));
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
    const kids = [el("p", "", text)];
    if (retry) { const b = el("button", "pill-btn primary", "Try again"); b.type = "button"; b.onclick = () => { hideMsg(); retry(); }; kids.push(b); }
    const c = el("button", "pill-btn", "Close"); c.type = "button"; c.onclick = () => closePlayer(false); kids.push(c);
    m.replaceChildren(...kids);
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
    paintTime();
    report(P.progress.tick(video.currentTime, !video.paused));
    if (Date.now() - P.lastLocal > 5000) { P.lastLocal = Date.now(); keepLocal(video.currentTime); }
    positionState();
  });
  video.addEventListener("play", () => { paintPlay(); idle.kick(); });
  video.addEventListener("playing", () => { spin(false); hideMsg(); hideTap(); });
  video.addEventListener("waiting", () => spin(true));
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
      case "toggle": if (!P.item) return; if (video.paused) act("play"); else video.pause(); return;
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
      case "clear-search": $("#q").value = ""; S.q = ""; $("#q-clear").hidden = true; renderView(); $("#q").focus(); return;
      case "search": closeDetails(false); $("#q").focus(); $("#q").select(); return;
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
    const c = e.target.closest(".card");
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
    const a = C.keyAction({ key: e.key, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, altKey: e.altKey, targetTag: t.tagName,
                            editable: !!t.isContentEditable, onButton: t.tagName === "BUTTON" }, !!P.item && !player.hidden);
    if (!a) { if (e.key === "Escape" && !sheet.hidden) closeDetails(false); return; }
    if (a === "close" && $("#keys").open) return;                  // the dialog closes itself
    e.preventDefault();
    act(a);
  });

  // ── search and sort ────────────────────────────────────────────────────────────────────────────────────────────
  let qTimer = 0;
  $("#q").addEventListener("input", e => {
    clearTimeout(qTimer);
    const v = e.target.value;
    $("#q-clear").hidden = !v;
    qTimer = setTimeout(() => { S.q = v.trim(); renderView(); $("#scroll").scrollTop = 0; }, 60);
  });
  $("#q").addEventListener("keydown", e => { if (e.key === "Escape" && e.target.value) { e.stopPropagation(); act("clear-search"); } });
  $("#sort").value = S.sort;
  $("#sort").addEventListener("change", e => { S.sort = e.target.value; store.set("sort", S.sort); renderView(); });

  // ═══ start ═══════════════════════════════════════════════════════════════════════════════════════════════════════
  // big touch: a coarse pointer on a wide, short screen (a car, a TV browser, a phone on its side) gets bigger targets
  const touch = matchMedia("(pointer: coarse) and (min-width: 700px)");
  const markTouch = () => root.classList.toggle("touch", touch.matches);
  markTouch();
  if (touch.addEventListener) touch.addEventListener("change", markTouch);
  if (IOS) $("#p-vol").hidden = true;                              // iOS ignores video.volume: the hardware buttons own it
  paintIcons();
  applyVolume();
  paintPlay();
  loadCatalog();
})();
