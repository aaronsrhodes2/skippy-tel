// The Tabletop (templates/tabletop.html, portal/tabletop.py): the adventure's shared boards.
// The board is a fixed surface (board units) drawn inside a view you drag to pan and pinch or wheel to zoom. Cards are
// pictures and notes; yours (or every card, for the GM) can be dragged, edited and taken down. Other people's changes
// arrive by a poll every few seconds that costs one small answer while nothing has moved.
(() => {
  "use strict";
  const root = document.getElementById("tt");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const BOARDS_URL = root.dataset.boardsUrl;
  const boardUrl = (id) => root.dataset.boardUrl.replace("BID", encodeURIComponent(id));
  const GM = root.dataset.gm === "1";
  const ME = root.dataset.me;
  const INKS = ["green", "amber", "cyan", "violet", "white", "red"];
  const POLL_MS = 4000;
  const MAX_SIDE = 2000;                     // a picture's longest side once shrunk on this device, before it is sent

  const view = $("tt-view"), boardEl = $("tt-board"), notice = $("tt-notice");
  const keys = { note: $("tt-note-key"), pic: $("tt-pic-key"), fit: $("tt-fit-key") };
  let boards = [], board = null, cam = { x: 0, y: 0, s: 0.2 }, busy = 0, pollTimer = null, listPage = 0;

  // ── talking to the portal ──
  async function call(method, url, body) {
    const opt = { method, headers: { "X-Tabletop": "1" }, credentials: "same-origin" };
    if (body instanceof FormData) opt.body = body;
    else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    const resp = await fetch(url, opt);
    let data = {};
    try { data = await resp.json(); } catch (e) { /* not JSON: a sign-in bounce or a proxy page */ }
    if (!resp.ok) throw new Error(data.error || (resp.status === 403 ? "You're not on this table's list." : `Something went wrong (${resp.status}).`));
    return data;
  }
  let noticeTimer = null;
  function say(text, ms = 4000) {
    notice.textContent = text; notice.hidden = false;
    clearTimeout(noticeTimer);
    if (ms) noticeTimer = setTimeout(() => { notice.hidden = true; }, ms);
  }
  const remember = (k, v) => { try { localStorage.setItem("tt-" + k, v); } catch (e) { /* private window */ } };
  const recall = (k) => { try { return localStorage.getItem("tt-" + k); } catch (e) { return null; } };

  // ── the camera: board units → screen ──
  function applyCam() { boardEl.style.transform = `translate(${cam.x}px, ${cam.y}px) scale(${cam.s})`; }
  function clampScale(s) { return Math.min(3, Math.max(0.05, s)); }
  function zoomAt(px, py, factor) {
    const s = clampScale(cam.s * factor), k = s / cam.s;
    cam.x = px - (px - cam.x) * k; cam.y = py - (py - cam.y) * k; cam.s = s; applyCam();
  }
  function fit(all) {
    if (!board) return;
    const r = view.getBoundingClientRect();
    let box = { x0: 0, y0: 0, x1: board.w, y1: board.h };
    const cards = board.cards;
    if (!all && cards.length) {
      box = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
      for (const c of cards) {
        const el = boardEl.querySelector(`[data-id="${c.id}"]`);
        const h = el ? el.offsetHeight : c.w * (c.ratio || 0.6);
        box.x0 = Math.min(box.x0, c.x); box.y0 = Math.min(box.y0, c.y);
        box.x1 = Math.max(box.x1, c.x + c.w); box.y1 = Math.max(box.y1, c.y + h);
      }
      const pad = 80; box.x0 -= pad; box.y0 -= pad; box.x1 += pad; box.y1 += pad;
    }
    const s = clampScale(Math.min(r.width / (box.x1 - box.x0), r.height / (box.y1 - box.y0)));
    cam = { s, x: (r.width - (box.x1 - box.x0) * s) / 2 - box.x0 * s, y: (r.height - (box.y1 - box.y0) * s) / 2 - box.y0 * s };
    applyCam();
  }
  function centre() {          // near the middle of the screen: where a new card goes, scattered a little so cards don't stack exactly
    const r = view.getBoundingClientRect(), jitter = () => (Math.random() - 0.5) * Math.min(r.width, r.height) * 0.4 / cam.s;
    return { x: (r.width / 2 - cam.x) / cam.s + jitter(), y: (r.height / 2 - cam.y) / cam.s + jitter() };
  }

  // ── drawing ──
  const canChange = (c) => GM || c.by === ME;
  function cardEl(c) {
    const el = document.createElement("div");
    el.className = "tt-card" + (canChange(c) ? " mine" : "");
    el.dataset.id = c.id;
    el.setAttribute("role", "button");
    el.setAttribute("aria-label", (c.kind === "image" ? "Picture" : "Note") + " from " + (c.name || "someone"));
    if (c.kind === "image") {
      const img = document.createElement("img");
      img.alt = c.text || "";
      img.loading = "lazy";
      img.style.aspectRatio = `1 / ${c.ratio || 0.75}`;
      img.src = `${boardUrl(board.id)}/img/${c.img}`;
      el.appendChild(img);
      if (c.text) { const cap = document.createElement("div"); cap.className = "cap"; cap.textContent = c.text; el.appendChild(cap); }
    } else {
      const note = document.createElement("div");
      note.className = "note ink-" + (c.ink || "green");
      note.textContent = c.text;
      el.appendChild(note);
    }
    const by = document.createElement("div"); by.className = "by"; by.textContent = c.name || "";
    el.appendChild(by);
    place(el, c);
    return el;
  }
  function place(el, c) {
    el.style.left = c.x + "px"; el.style.top = c.y + "px"; el.style.width = c.w + "px";
    el.style.transform = `rotate(${c.rot || 0}deg)`; el.style.zIndex = c.z || 0;
  }
  function draw() {
    boardEl.style.width = board.w + "px"; boardEl.style.height = board.h + "px";
    const have = new Map([...boardEl.children].map((el) => [el.dataset.id, el]));
    for (const c of board.cards) {
      const old = have.get(c.id);
      const sig = JSON.stringify([c.text, c.ink, c.img]);
      if (old && old.dataset.sig === sig) { if (!old.classList.contains("dragging")) place(old, c); have.delete(c.id); continue; }
      const el = cardEl(c); el.dataset.sig = sig;
      old ? old.replaceWith(el) : boardEl.appendChild(el);
      have.delete(c.id);
    }
    for (const el of have.values()) el.remove();
    $("tt-empty").hidden = board.cards.length > 0;
    $("tt-empty-text").textContent = "Nothing pinned yet. Add a note or a picture from the keys above.";
  }
  function showNoBoard(text) {
    board = null; boardEl.replaceChildren();
    $("tt-title").textContent = "Tabletop";
    Object.values(keys).forEach((k) => { k.disabled = true; });
    $("tt-empty").hidden = false; $("tt-empty-text").textContent = text;
  }

  // ── loading and the poll ──
  async function loadBoards() {
    boards = (await call("GET", BOARDS_URL)).boards;
    return boards;
  }
  async function open(id, refit = true) {
    const data = await call("GET", boardUrl(id));
    board = data;
    $("tt-title").textContent = board.title;
    Object.values(keys).forEach((k) => { k.disabled = false; });
    remember("board", id);
    if (location.hash !== "#" + id) history.replaceState(null, "", "#" + id);
    draw();
    if (refit) requestAnimationFrame(() => fit(false));
    schedulePoll();
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, POLL_MS);
  }
  async function poll() {
    if (!board || document.hidden || busy) return schedulePoll();
    try {
      const data = await call("GET", `${boardUrl(board.id)}?since=${board.rev}`);
      if (data.cards && board && data.id === board.id) { board = data; $("tt-title").textContent = board.title; draw(); }
    } catch (e) {
      if (/gone/.test(e.message)) { say(e.message); await start(); return; }
    }
    schedulePoll();
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && board) poll(); });

  function merge(card) {           // a card the portal just confirmed, folded into the board without waiting for the poll
    const i = board.cards.findIndex((c) => c.id === card.id);
    if (i >= 0) board.cards[i] = card; else board.cards.push(card);
    board.rev = -1;                // our own copy is now ahead of the rev we know: the next poll fetches the real one
    draw();
  }

  // ── panning, zooming, dragging a card ──
  const pointers = new Map();
  let gesture = null;             // {kind: "pan"|"pinch"|"card", ...}
  view.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    view.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const cardNode = e.target.closest(".tt-card");
    if (pointers.size === 2) {
      if (gesture && gesture.kind === "card") endCardDrag(false);
      const [a, b] = [...pointers.values()];
      gesture = { kind: "pinch", d: Math.hypot(a.x - b.x, a.y - b.y), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
    } else if (cardNode && board) {
      const c = board.cards.find((k) => k.id === cardNode.dataset.id);
      gesture = { kind: "card", el: cardNode, card: c, sx: e.clientX, sy: e.clientY, ox: c.x, oy: c.y, moved: false, mine: canChange(c) };
    } else {
      gesture = { kind: "pan", sx: e.clientX, sy: e.clientY, ox: cam.x, oy: cam.y };
      view.classList.add("panning");
    }
  });
  view.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (!gesture) return;
    if (gesture.kind === "pinch" && pointers.size >= 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y), r = view.getBoundingClientRect();
      const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      cam.x += cx - gesture.cx; cam.y += cy - gesture.cy;
      zoomAt(cx - r.left, cy - r.top, d / gesture.d);
      gesture.d = d; gesture.cx = cx; gesture.cy = cy;
    } else if (gesture.kind === "pan") {
      cam.x = gesture.ox + e.clientX - gesture.sx; cam.y = gesture.oy + e.clientY - gesture.sy; applyCam();
    } else if (gesture.kind === "card") {
      const dx = e.clientX - gesture.sx, dy = e.clientY - gesture.sy;
      if (!gesture.moved && Math.hypot(dx, dy) < 6) return;
      if (!gesture.mine) {                     // someone else's card: dragging it pans the board instead
        gesture = { kind: "pan", sx: gesture.sx, sy: gesture.sy, ox: cam.x, oy: cam.y };
        view.classList.add("panning");
        return;
      }
      gesture.moved = true;
      gesture.el.classList.add("dragging");
      gesture.card.x = Math.max(0, Math.min(board.w - 40, gesture.ox + dx / cam.s));
      gesture.card.y = Math.max(0, Math.min(board.h - 40, gesture.oy + dy / cam.s));
      gesture.el.style.zIndex = 100000;
      place(gesture.el, { ...gesture.card, z: 100000 });
    }
  });
  function endCardDrag(commit) {
    const g = gesture; gesture = null;
    g.el.classList.remove("dragging");
    if (!commit) return;
    if (!g.moved) { openCard(g.card); return; }
    busy++;
    call("POST", `${boardUrl(board.id)}/c/${g.card.id}/move`, { x: g.card.x, y: g.card.y, w: g.card.w, rot: g.card.rot })
      .then((d) => merge(d.card))
      .catch((err) => { say(err.message); g.card.x = g.ox; g.card.y = g.oy; draw(); })
      .finally(() => { busy--; });
  }
  function pointerEnd(e) {
    if (!pointers.has(e.pointerId)) return;
    pointers.delete(e.pointerId);
    if (gesture && gesture.kind === "card") endCardDrag(e.type === "pointerup");
    else if (gesture && gesture.kind === "pinch" && pointers.size === 1) {
      const [p] = [...pointers.values()];
      gesture = { kind: "pan", sx: p.x, sy: p.y, ox: cam.x, oy: cam.y };
    } else if (pointers.size === 0) { gesture = null; view.classList.remove("panning"); }
  }
  view.addEventListener("pointerup", pointerEnd);
  view.addEventListener("pointercancel", pointerEnd);
  view.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = view.getBoundingClientRect();
    if (e.ctrlKey || e.deltaMode || Math.abs(e.deltaY) > Math.abs(e.deltaX)) zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)));
    else { cam.x -= e.deltaX; cam.y -= e.deltaY; applyCam(); }
  }, { passive: false });
  view.addEventListener("keydown", (e) => {
    const r = view.getBoundingClientRect(), step = 60;
    const moves = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
    if (moves[e.key]) { cam.x += moves[e.key][0]; cam.y += moves[e.key][1]; applyCam(); e.preventDefault(); }
    else if (e.key === "+" || e.key === "=") zoomAt(r.width / 2, r.height / 2, 1.25);
    else if (e.key === "-") zoomAt(r.width / 2, r.height / 2, 0.8);
    else if (e.key === "0") fit(false);
  });
  window.addEventListener("resize", () => { if (board) fit(false); });

  // ── sheets ──
  function openSheet(id) { $(id).hidden = false; }
  function closeSheets() { document.querySelectorAll("#tt .sheet").forEach((s) => { s.hidden = true; }); }
  root.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeSheets(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheets(); });

  // The boards sheet: a screen of boards at a time, never a scroll.
  function drawList() {
    const list = $("tt-list"), per = Math.max(3, Math.floor((list.clientHeight || 300) / 50) - 1);
    const pages = Math.max(1, Math.ceil(boards.length / per));
    listPage = Math.min(listPage, pages - 1);
    list.replaceChildren();
    if (!boards.length) {
      const p = document.createElement("p"); p.className = "tt-by";
      p.textContent = GM ? "No boards yet. Name the first one below." : "The GM hasn't set out a board yet.";
      list.appendChild(p);
    }
    for (const b of boards.slice(listPage * per, listPage * per + per)) {
      const row = document.createElement("div"); row.className = "row"; row.setAttribute("role", "listitem");
      const go = document.createElement("button"); go.type = "button";
      go.setAttribute("aria-current", board && board.id === b.id ? "true" : "false");
      go.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-d20"/></svg>';
      const name = document.createElement("span"); name.textContent = b.title; go.appendChild(name);
      go.addEventListener("click", () => { closeSheets(); open(b.id).catch((err) => say(err.message)); });
      row.appendChild(go);
      if (GM) {
        row.appendChild(keyBtn("g-tag", "Rename", async () => {
          const title = prompt("Board name", b.title);
          if (!title) return;
          try { await call("POST", `${boardUrl(b.id)}/rename`, { title }); await loadBoards(); drawList(); if (board && board.id === b.id) open(b.id, false); }
          catch (err) { say(err.message); }
        }));
        row.appendChild(keyBtn("g-trash", "Remove board", async () => {
          if (!confirm(`Remove "${b.title}" and everything pinned on it? This can't be undone.`)) return;
          try { await call("POST", `${boardUrl(b.id)}/delete`); await loadBoards(); drawList(); if (board && board.id === b.id) start(); }
          catch (err) { say(err.message); }
        }, true));
      }
      list.appendChild(row);
    }
    if (pages > 1) {
      const pager = document.createElement("div"); pager.className = "tt-pager";
      pager.appendChild(keyBtn("g-up", "Earlier boards", () => { listPage = Math.max(0, listPage - 1); drawList(); }));
      pager.appendChild(keyBtn("g-down", "Later boards", () => { listPage = Math.min(pages - 1, listPage + 1); drawList(); }));
      list.appendChild(pager);
    }
  }
  function keyBtn(glyph, label, fn, danger) {
    const b = document.createElement("button"); b.type = "button"; b.className = "key sm" + (danger ? " danger" : "");
    b.setAttribute("aria-label", label); b.title = label;
    b.innerHTML = `<svg class="g" aria-hidden="true"><use href="#${glyph}"/></svg>`;
    b.addEventListener("click", fn);
    return b;
  }
  $("tt-boards-key").addEventListener("click", async () => {
    openSheet("tt-boards");
    try { await loadBoards(); } catch (err) { say(err.message); }
    drawList();
  });
  const newForm = $("tt-new");
  if (newForm) newForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("tt-new-title");
    try {
      const { board: b } = await call("POST", BOARDS_URL, { title: input.value });
      input.value = ""; await loadBoards(); closeSheets(); await open(b.id);
    } catch (err) { say(err.message); }
  });

  // The card sheet: a new note, or any card up close.
  let editing = null;            // {card} or {kind: "text"} for a new note
  function drawInks(selected, enabled) {
    const box = $("tt-inks"); box.replaceChildren(); box.hidden = !enabled;
    for (const ink of INKS) {
      const b = document.createElement("button"); b.type = "button"; b.className = "ink-" + ink;
      b.setAttribute("role", "radio"); b.setAttribute("aria-checked", ink === selected ? "true" : "false"); b.setAttribute("aria-label", ink);
      b.addEventListener("click", () => { editing.ink = ink; drawInks(ink, true); });
      box.appendChild(b);
    }
  }
  function openCard(card) {
    const sheet = $("tt-card"), mine = card ? canChange(card) : true, isNote = !card || card.kind === "text";
    editing = card ? { card, ink: card.ink || "green" } : { ink: recall("ink") || "green" };
    sheet.classList.toggle("note", isNote);
    $("tt-card-by").textContent = card ? (card.name || "") : "";
    const pic = $("tt-card-pic"); pic.replaceChildren();
    if (card && card.kind === "image") {
      const img = document.createElement("img"); img.src = `${boardUrl(board.id)}/img/${card.img}`; img.alt = card.text || "";
      pic.appendChild(img);
    }
    const text = $("tt-card-text");
    text.value = card ? (card.text || "") : "";
    text.readOnly = !mine;
    text.placeholder = isNote ? "A clue, a quote, a name to remember…" : (mine ? "A caption (optional)" : "");
    text.hidden = !mine && !text.value;
    drawInks(editing.ink, mine && isNote);
    $("tt-card-del").hidden = !card || !mine;
    $("tt-card-save").hidden = !mine;
    openSheet("tt-card");
    if (mine && isNote) setTimeout(() => text.focus(), 50);
  }
  $("tt-card-save").addEventListener("click", async () => {
    const text = $("tt-card-text").value;
    try {
      if (editing.card) {
        const d = await call("POST", `${boardUrl(board.id)}/c/${editing.card.id}/edit`, { text, ink: editing.ink });
        merge(d.card);
      } else {
        const at = centre();
        remember("ink", editing.ink);
        const d = await call("POST", `${boardUrl(board.id)}/text`, { text, ink: editing.ink, x: at.x - 180, y: at.y - 80 });
        merge(d.card);
      }
      closeSheets();
    } catch (err) { say(err.message); }
  });
  $("tt-card-del").addEventListener("click", async () => {
    if (!editing.card || !confirm("Take this card off the board?")) return;
    try {
      await call("POST", `${boardUrl(board.id)}/c/${editing.card.id}/delete`);
      board.cards = board.cards.filter((c) => c.id !== editing.card.id); board.rev = -1; draw(); closeSheets();
    } catch (err) { say(err.message); }
  });
  keys.note.addEventListener("click", () => openCard(null));
  keys.fit.addEventListener("click", () => fit(false));

  // ── pictures: from the picker, a drop, or a paste ──
  async function shrink(file) {
    // GIFs (they may move) and small PNGs (they may be see-through) go as they are; everything else is drawn at most
    // MAX_SIDE on its longest side and sent as a JPEG, so a 12-megapixel phone photo is a few hundred KB.
    const bitmap = await createImageBitmap(file).catch(() => null);
    if (!bitmap) throw new Error(`${file.name || "That file"} isn't a picture this browser can read.`);
    const ratio = bitmap.height / bitmap.width;
    const keep = file.type === "image/gif" || (file.type === "image/png" && file.size < 4e6 && Math.max(bitmap.width, bitmap.height) <= MAX_SIDE);
    if (keep) { bitmap.close && bitmap.close(); return { blob: file, ratio }; }
    const k = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * k); canvas.height = Math.round(bitmap.height * k);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close && bitmap.close();
    const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.86));
    return { blob, ratio };
  }
  async function upload(files) {
    if (!board) return say("Pick a board first.");
    const list = [...files].filter((f) => f.type.startsWith("image/"));
    if (!list.length) return;
    busy++;
    try {
      let n = 0;
      for (const file of list) {
        say(list.length > 1 ? `Pinning ${++n} of ${list.length}…` : "Pinning…", 0);
        const { blob, ratio } = await shrink(file);
        const at = centre(), form = new FormData();
        form.append("img", blob, "card");
        form.append("ratio", String(ratio));
        form.append("x", String(at.x - 260 + (n - 1) * 40)); form.append("y", String(at.y - 260 * ratio + (n - 1) * 40));
        merge((await call("POST", `${boardUrl(board.id)}/image`, form)).card);
      }
      notice.hidden = true;
    } catch (err) { say(err.message); } finally { busy--; }
  }
  keys.pic.addEventListener("click", () => $("tt-file").click());
  $("tt-file").addEventListener("change", (e) => { upload(e.target.files); e.target.value = ""; });
  view.addEventListener("dragover", (e) => { if (board && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); $("tt-drop").hidden = false; } });
  view.addEventListener("dragleave", () => { $("tt-drop").hidden = true; });
  view.addEventListener("drop", (e) => { e.preventDefault(); $("tt-drop").hidden = true; upload(e.dataTransfer.files); });
  document.addEventListener("paste", (e) => {
    if (e.target.closest && e.target.closest("textarea, input")) return;
    const files = [...(e.clipboardData && e.clipboardData.files) || []];
    if (files.length) upload(files);
  });

  // ── start: the board in the address (#id), else the last one you looked at, else the newest ──
  async function start() {
    if (root.dataset.ready !== "1") return showNoBoard("The Tabletop isn't set up yet: the GM needs to give it somewhere to keep pictures.");
    try { await loadBoards(); } catch (err) { return showNoBoard(err.message); }
    if (!boards.length) return showNoBoard(GM ? "No boards yet. Open the boards key and name the first one." : "The GM hasn't set out a board yet.");
    const want = location.hash.slice(1) || recall("board");
    const pick = boards.find((b) => b.id === want) || boards[boards.length - 1];
    try { await open(pick.id); } catch (err) { showNoBoard(err.message); }
  }
  window.addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id && (!board || board.id !== id)) open(id).catch((err) => say(err.message)); });
  applyCam();
  start();
})();
