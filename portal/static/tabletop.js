// The Tabletop's cork board (templates/tabletop.html, portal/tabletop.py): where everything about the game that isn't one hero's is
// added and shown. The board is a fixed surface (board units) drawn inside a view you drag to pan and pinch or wheel to zoom.
// Cards are pictures (polaroids) and notes (cue cards); yours (or every card, for the GM) can be dragged, edited and taken down.
// Other people's changes arrive by a poll every few seconds that costs one small answer while nothing has moved.
(() => {
  "use strict";
  const root = document.getElementById("tt");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const BOARD = root.dataset.boardUrl;                       // …/board: the cards hang off it (…/board/text, …/board/c/<id>/move)
  const GM = root.dataset.gm === "1";
  const ME = root.dataset.me;
  const INKS = ["green", "amber", "cyan", "violet", "white", "red"];
  const POLL_MS = 4000;
  const MAX_SIDE = 2000;                     // a picture's longest side once shrunk on this device, before it is sent

  const view = $("tt-view"), boardEl = $("tt-board"), notice = $("tt-notice");
  const keys = { note: $("tt-note-key"), pic: $("tt-pic-key"), fit: $("tt-fit-key") };
  let board = null, cam = { x: 0, y: 0, s: 0.2 }, busy = 0, pollTimer = null, drawn = false;

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

  // Who sees a card (portal/tabletop.py enforces it; this only draws it): shown, fogged (a fog patch where it is, nothing of what it is)
  // or hidden (a GM-only note). The GM chooses; the players' copy of the board never contains what they may not see.
  const VIS = ["shown", "fog", "hidden"];
  const vis = (c) => c.vis || "shown";
  const FOG_RATIO = 0.7;                                      // a fog patch's height / width: the same for every card, so it gives nothing away
  const VIS_GLYPH = { shown: "g-eye", fog: "g-fog", hidden: "g-hide" };
  const VIS_WORDS = { shown: "shown", fog: "fogged", hidden: "hidden" };
  let visMode = GM && VIS.includes(recall("vis")) ? recall("vis") : "hidden";   // what the GM's new cards start as
  const svg = (id) => `<svg class="g" aria-hidden="true"><use href="#${id}"/></svg>`;

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
        const h = el ? el.offsetHeight : c.w * (c.ratio || FOG_RATIO);
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
  const fogVeil = (extra) => { const v = document.createElement("span"); v.className = "veil" + (extra ? " " + extra : ""); v.setAttribute("aria-hidden", "true"); return v; };
  function cardEl(c) {
    const el = document.createElement("div");
    el.dataset.id = c.id; el.dataset.vis = vis(c);
    if (vis(c) === "fog" && !GM) {                     // a player's view of a fogged card: a fog patch, and nothing of what it is
      el.className = "tt-card fogged";
      el.setAttribute("aria-label", "Not revealed yet");
      el.innerHTML = '<span class="q" aria-hidden="true">?</span>';
      place(el, c);
      el.style.height = Math.round(c.w * FOG_RATIO) + "px";
      return el;
    }
    el.className = "tt-card " + (c.kind === "image" ? "polaroid" : "cue") + (canChange(c) ? " mine" : "") + (vis(c) !== "shown" ? " gmonly vis-" + vis(c) : "");
    const pin = document.createElement("i");           // the pin holding it to the cork: a colour of its own, picked from the card's id
    pin.className = "pin pin-" + (parseInt(c.id.slice(-2), 16) % 5);
    el.appendChild(pin);
    el.setAttribute("role", "button");
    el.setAttribute("aria-label", (c.kind === "image" ? "Picture" : "Note") + " from " + (c.name || "someone") + (vis(c) !== "shown" ? ", " + VIS_WORDS[vis(c)] : ""));
    if (c.kind === "image") {
      const img = document.createElement("img");
      img.alt = c.text || "";
      img.loading = "lazy";
      img.style.aspectRatio = `1 / ${c.ratio || 0.75}`;
      img.src = `${BOARD}/img/${c.img}`;
      el.appendChild(img);
      if (c.text) { const cap = document.createElement("div"); cap.className = "cap"; cap.textContent = c.text; el.appendChild(cap); }
    } else {
      const note = document.createElement("div");
      note.className = "note ink-" + (c.ink || "green");
      if (c.title) { const ttl = document.createElement("span"); ttl.className = "ttl"; ttl.textContent = c.title; note.appendChild(ttl); }
      note.appendChild(document.createTextNode(c.text));
      el.appendChild(note);
    }
    const by = document.createElement("div"); by.className = "by"; by.textContent = c.name || "";
    el.appendChild(by);
    if (GM) {                                          // the GM sees what the players can't, marked, and can change it with one tap
      if (vis(c) === "fog") el.appendChild(fogVeil());
      const tag = document.createElement("span"); tag.className = "vtag"; tag.innerHTML = svg(VIS_GLYPH[vis(c)]);
      tag.title = VIS_WORDS[vis(c)]; el.appendChild(tag);
      const key = document.createElement("button"); key.type = "button"; key.className = "vkey";
      const next = vis(c) === "shown" ? "fog" : "shown";
      key.innerHTML = svg(VIS_GLYPH[next]);
      key.setAttribute("aria-label", next === "shown" ? "Reveal it to the players" : "Fog it");
      key.title = next === "shown" ? "Reveal it to the players" : "Fog it";
      key.addEventListener("click", (ev) => { ev.stopPropagation(); setVis(c.id, next); });
      el.appendChild(key);
    }
    place(el, c);
    return el;
  }
  async function setVis(id, v) {
    try { merge((await call("POST", `${BOARD}/c/${id}/vis`, { vis: v })).card); } catch (err) { say(err.message); }
  }
  function place(el, c) {
    el.style.left = c.x + "px"; el.style.top = c.y + "px"; el.style.width = c.w + "px";
    el.style.transform = `rotate(${c.rot || 0}deg)`; el.style.zIndex = c.z || 0;
  }
  function draw() {
    boardEl.style.width = board.w + "px"; boardEl.style.height = board.h + "px";
    const have = new Map([...boardEl.children].map((el) => [el.dataset.id, el]));
    const firstDraw = !drawn; drawn = true;
    for (const c of board.cards) {
      const old = have.get(c.id);
      const sig = JSON.stringify([c.text, c.title, c.ink, c.img, c.vis, c.kind]);
      if (old && old.dataset.sig === sig) { if (!old.classList.contains("dragging")) place(old, c); have.delete(c.id); continue; }
      const el = cardEl(c); el.dataset.sig = sig;
      if (old && old.dataset.vis === "fog" && vis(c) === "shown") {           // revealed: the fog lifts off it
        const lift = fogVeil("lifting"); el.appendChild(lift);
        lift.addEventListener("animationend", () => lift.remove());
      } else if (!old && !firstDraw) el.classList.add("appear");               // pinned up since you last looked
      old ? old.replaceWith(el) : boardEl.appendChild(el);
      have.delete(c.id);
    }
    for (const el of have.values()) el.remove();
    $("tt-empty").hidden = board.cards.length > 0;
    $("tt-empty-text").textContent = "Nothing pinned yet. Add a note or a picture from the keys above, or drop a picture here.";
  }
  function showNoBoard(text) {
    board = null; boardEl.replaceChildren();
    Object.values(keys).forEach((k) => { k.disabled = true; });
    $("tt-empty").hidden = false; $("tt-empty-text").textContent = text;
  }

  // ── loading and the poll ──
  async function load(refit) {
    board = await call("GET", BOARD);
    if (board.fear !== undefined) root.dispatchEvent(new CustomEvent("tt-fear", { detail: board.fear }));
    Object.values(keys).forEach((k) => { k.disabled = false; });
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
      const data = await call("GET", `${BOARD}?since=${board.rev}`);
      if (data.fear !== undefined) root.dispatchEvent(new CustomEvent("tt-fear", { detail: data.fear }));
      if (data.cards && board) { board = data; draw(); }
    } catch (e) { /* a blip: the next poll catches up */ }
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
    if (e.target.closest(".vkey")) return;                  // the GM's reveal key is a button, not a handle
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
    if (!g.moved) { if (g.card.vis !== "fog" || GM) openCard(g.card); return; }
    busy++;
    call("POST", `${BOARD}/c/${g.card.id}/move`, { x: g.card.x, y: g.card.y, w: g.card.w, rot: g.card.rot })
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
  function closeSheets() { document.querySelectorAll("#tt .sheet").forEach((s) => { s.hidden = true; }); root.dispatchEvent(new CustomEvent("tt-closed")); }
  root.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeSheets(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheets(); });

  // The card sheet: a new note, or any card up close.
  let editing = null;            // {card} or {ink} for a new note
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
    editing = card ? { card, ink: card.ink || "green", vis: vis(card) } : { ink: recall("ink") || "green", vis: visMode };
    sheet.classList.toggle("note", isNote);
    $("tt-card-by").textContent = card ? (card.name || "") : "";
    const pic = $("tt-card-pic"); pic.replaceChildren();
    if (card && card.kind === "image") {
      const img = document.createElement("img"); img.src = `${BOARD}/img/${card.img}`; img.alt = card.text || "";
      pic.appendChild(img);
    }
    const text = $("tt-card-text");
    text.value = card ? (card.text || "") : "";
    text.readOnly = !mine;
    text.placeholder = isNote ? "A clue, a quote, a name to remember…" : (mine ? "A caption (optional)" : "");
    text.hidden = !mine && !text.value;
    drawInks(editing.ink, mine && isNote);
    const title = $("tt-card-title");
    title.value = card ? (card.title || "") : ""; title.readOnly = !mine; title.hidden = !isNote || (!mine && !title.value);
    drawVisChoice();
    $("tt-card-del").hidden = !card || !mine;
    $("tt-card-save").hidden = !mine;
    openSheet("tt-card");
    if (mine && isNote) setTimeout(() => text.focus(), 50);
  }
  function drawVisChoice() {                           // the GM's: who sees this card (an existing card changes at once; a new one starts so)
    const box = $("tt-card-vis");
    if (!box) return;
    box.querySelectorAll("[data-vis]").forEach((b) => b.setAttribute("aria-checked", b.dataset.vis === editing.vis ? "true" : "false"));
  }
  if ($("tt-card-vis")) $("tt-card-vis").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-vis]");
    if (!b) return;
    editing.vis = b.dataset.vis;
    if (editing.card) await setVis(editing.card.id, editing.vis);
    drawVisChoice();
  });
  $("tt-card-save").addEventListener("click", async () => {
    const text = $("tt-card-text").value;
    try {
      if (editing.card) {
        const d = await call("POST", `${BOARD}/c/${editing.card.id}/edit`, { text, ink: editing.ink, title: $("tt-card-title").value });
        merge(d.card);
      } else {
        const at = centre();
        remember("ink", editing.ink);
        const d = await call("POST", `${BOARD}/text`, { text, title: $("tt-card-title").value, ink: editing.ink, vis: editing.vis, x: at.x - 180, y: at.y - 80 });
        merge(d.card);
      }
      closeSheets();
    } catch (err) { say(err.message); }
  });
  $("tt-card-del").addEventListener("click", async () => {
    if (!editing.card || !confirm("Take this card off the board?")) return;
    try {
      await call("POST", `${BOARD}/c/${editing.card.id}/delete`);
      board.cards = board.cards.filter((c) => c.id !== editing.card.id); board.rev = -1; draw(); closeSheets();
    } catch (err) { say(err.message); }
  });
  const visKey = $("tt-vis-key");                          // the GM's: whether the cards they add next start shown, fogged or hidden
  function drawVisKey() {
    if (!visKey) return;
    visKey.innerHTML = svg(VIS_GLYPH[visMode]);
    const words = `New cards start ${VIS_WORDS[visMode]}`;
    visKey.setAttribute("aria-label", words); visKey.title = words;
  }
  if (visKey) visKey.addEventListener("click", () => { visMode = VIS[(VIS.indexOf(visMode) + 1) % VIS.length]; remember("vis", visMode); drawVisKey(); say(`New cards start ${VIS_WORDS[visMode]}.`, 1800); });
  drawVisKey();
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
    if (!board) return say("The board isn't ready yet.");
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
        form.append("ratio", String(ratio)); form.append("vis", visMode);
        form.append("x", String(at.x - 260 + (n - 1) * 40)); form.append("y", String(at.y - 260 * ratio + (n - 1) * 40));
        merge((await call("POST", `${BOARD}/image`, form)).card);
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

  // ── start ──
  async function start() {
    if (root.dataset.ready !== "1") return showNoBoard("The Tabletop isn't set up yet: the GM needs to give it somewhere to keep pictures.");
    try { await load(true); } catch (err) { showNoBoard(err.message); }
  }
  applyCam();
  start();
})();
