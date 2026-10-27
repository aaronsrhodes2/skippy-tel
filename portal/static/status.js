/* The Status page: machines as nodes, connections as lines; a server opens into containers, models and MCPs (docs/status-design.md).
   Nothing is fetched while the page is hidden. The map pans (drag) and zooms (wheel, two fingers); the sheet pages, it never scrolls. */
(function () {
  "use strict";
  const root = document.getElementById("stat");
  if (!root) return;
  const NS = "http://www.w3.org/2000/svg";
  const map = document.getElementById("s-map");
  const sheet = document.getElementById("s-sheet");
  const body = document.getElementById("s-body");
  const title = document.getElementById("s-title");
  const note = document.getElementById("s-note");
  const HEAL = new Set(JSON.parse(root.dataset.heal || "[]"));
  const POS = { "skippy-portal": [500, 90], "skippy-pc": [170, 330], "skippy-core": [830, 330], "games-vm": [500, 550] };
  const W = 190, H = 84, CUT = 14;

  let data = null, open = null, openKid = null, page = 0, view = { x: 0, y: 0, w: 1000, h: 640 };
  const el = (name, attrs, parent) => {
    const e = document.createElementNS(NS, name);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  const html = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const say = (text) => { note.textContent = text; note.hidden = !text; if (text) setTimeout(() => { note.hidden = true; }, 6000); };
  const worst = (lamps) => ["down", "degraded", "stale", "off", "ok"].find((l) => lamps.includes(l)) || "ok";

  function applyView() { map.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`); }
  function fit() { view = { x: 0, y: 0, w: 1000, h: 640 }; applyView(); }

  function cutBox(x, y, w, h, c) {
    return `${x - w / 2 + c},${y - h / 2} ${x + w / 2},${y - h / 2} ${x + w / 2},${y + h / 2 - c} ${x + w / 2 - c},${y + h / 2} ${x - w / 2},${y + h / 2} ${x - w / 2},${y - h / 2 + c}`;
  }

  function draw() {
    map.replaceChildren();
    if (!data) return;
    const by = Object.fromEntries(data.machines.map((m) => [m.id, m]));
    const edges = el("g", {}, map);
    for (const e of data.edges) {
      const a = POS[e.from], b = POS[e.to];
      if (!a || !b) continue;
      el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: `edge ${e.lamp} ${e.kind === "trunk" ? "trunk" : ""}` }, edges);
      const t = el("text", { x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2 - 8, "text-anchor": "middle", class: "sub" }, edges);
      t.textContent = e.label;
    }
    for (const m of data.machines) {
      const [x, y] = POS[m.id] || [500, 320];
      const g = el("g", { class: "node" + (open === m.id ? " on" : ""), tabindex: 0, role: "button", "aria-label": `${m.label}: ${m.state}` }, map);
      el("polygon", { points: cutBox(x, y, W, H, CUT) }, g);
      el("circle", { cx: x - W / 2 + 20, cy: y - H / 2 + 22, r: 7, class: `lamp ${m.state}` }, g);
      const label = el("text", { x: x - W / 2 + 36, y: y - H / 2 + 28 }, g); label.textContent = m.label;
      const sub = el("text", { x, y: y + 14, "text-anchor": "middle", class: "sub" }, g);
      const kids = m.containers.length + m.serves.length;
      sub.textContent = `${m.containers.length} containers · ${m.serves.length} services` + (m.age != null && m.age > 0 ? ` · ${m.age}s` : "");
      if (m.busy) el("polygon", { points: cutBox(x, y, W + 10, H + 10, CUT), class: "busy" }, g);
      // expanded: the node's parts as a ring of lamps around it
      if (open === m.id) {
        const parts = [...m.containers.map((c) => ({ lamp: c.lamp, name: c.name, kind: "c", ref: c })), ...m.serves.map((s) => ({ lamp: s.lamp, name: s.name, kind: "s", ref: s }))];
        const shown = parts.slice(0, 18);
        shown.forEach((p, i) => {
          const ang = (i / shown.length) * Math.PI * 2 - Math.PI / 2, rad = 120;
          const px = x + Math.cos(ang) * rad * 1.35, py = y + Math.sin(ang) * rad * 0.9;
          const k = el("g", { class: "kid" }, map);
          el("line", { x1: x, y1: y, x2: px, y2: py, class: `edge ${p.lamp}`, style: "stroke-width:1;animation:none;stroke-dasharray:none;opacity:.5" }, k);
          el("circle", { cx: px, cy: py, r: 9, class: `lamp ${p.lamp}` }, k);
          const t = el("text", { x: px, y: py + 24, "text-anchor": "middle", class: "sub" }, k); t.textContent = p.name.slice(0, 18);
          k.addEventListener("click", (ev) => { ev.stopPropagation(); openKid = p; show(m); });
        });
      }
      g.addEventListener("click", () => { open = open === m.id ? null : m.id; openKid = null; page = 0; draw(); if (open) show(m); else hide(); });
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); g.dispatchEvent(new Event("click")); } });
    }
    const count = data.problems.length;
    const chip = document.getElementById("s-problems");
    document.getElementById("s-count").textContent = count;
    chip.classList.toggle("warn", count > 0 && count < 3);
    chip.classList.toggle("bad", count >= 3);
  }

  function row(lamp, name, detail, key) {
    const r = html("div", "row");
    r.append(html("span", `dot ${lamp}`), html("span", "nm", name), html("span", "dt", detail || ""));
    if (key) r.append(key);
    return r;
  }
  function healKey(name) {
    const b = html("button", "key"); b.type = "button";
    b.setAttribute("aria-label", `Heal ${name}`); b.title = `Heal ${name}`;
    b.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-retry"/></svg>';
    b.addEventListener("click", () => post(root.dataset.healUrl, { name }).then((r) => say(r.why)));
    return b;
  }
  function pageRows(rows, perPage) {
    const rowsBox = html("div", "rows");
    const pages = Math.max(1, Math.ceil(rows.length / perPage));
    page = Math.min(page, pages - 1);
    rows.slice(page * perPage, page * perPage + perPage).forEach((r) => rowsBox.append(r));
    const pager = html("div", "pager");
    const prev = html("button", "key"), next = html("button", "key");
    prev.type = next.type = "button";
    prev.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-up"/></svg>'; prev.setAttribute("aria-label", "Previous page");
    next.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-down"/></svg>'; next.setAttribute("aria-label", "Next page");
    prev.disabled = page === 0; next.disabled = page >= pages - 1;
    prev.onclick = () => { page--; render(); }; next.onclick = () => { page++; render(); };
    pager.append(prev, html("span", "", `${page + 1} / ${pages}`), next);
    body.replaceChildren(rowsBox, pager);
  }

  let current = null;
  function show(m) { current = m; sheet.hidden = false; render(); }
  function hide() { sheet.hidden = true; current = null; }
  function render() {
    if (!current) return;
    const m = data.machines.find((x) => x.id === current.id) || current;
    const perPage = Math.max(4, Math.floor((sheet.clientHeight - 150) / 46));
    const rows = [];
    if (openKid) {
      title.textContent = `${m.label} · ${openKid.name}`;
      const r = openKid.ref;
      rows.push(row(openKid.lamp, openKid.name, r.state || r.detail || ""));
      if (openKid.kind === "c") {
        [["status", r.status], ["image", r.image], ["restarts", r.restarts], ["cpu %", r.cpu], ["memory MB", r.mem_mb]].forEach(([k, v]) => { if (v !== null && v !== undefined && v !== "") rows.push(row("off", k, String(v))); });
        if (m.id === "skippy-pc" && HEAL.has(r.name.replace(/^skippytel-/, ""))) rows.push(row("off", "heal this one", "", healKey(r.name.replace(/^skippytel-/, ""))));
      }
      pageRows(rows, perPage);
      return;
    }
    title.textContent = `${m.label} · ${m.state}`;
    if (m.deployer && m.deployer.state) rows.push(row(m.deployer.state === "ok" ? "ok" : "degraded", "deploys", `${m.deployer.state} ${m.deployer.commit || ""}`));
    for (const [k, v] of Object.entries(m.vitals || {})) rows.push(row("off", k.replace(/_/g, " "), String(v)));
    for (const c of m.containers) rows.push(row(c.lamp, c.name, c.status || c.state));
    for (const s of m.serves) rows.push(row(s.lamp, s.name, s.detail || s.kind));
    if (!rows.length) rows.push(row(m.state, "nothing reported yet", ""));
    pageRows(rows, perPage);
  }
  function problems() {
    current = { id: "problems", label: "Problems" };
    openKid = null; open = null; draw(); sheet.hidden = false;
    title.textContent = `Problems · ${data.problems.length}`;
    const rows = data.problems.map((p) => row("degraded", p.what, p.machine));
    if (!rows.length) rows.push(row("ok", "everything is ok", ""));
    pageRows(rows, Math.max(4, Math.floor((sheet.clientHeight - 150) / 46)));
  }

  // Back steps up one level (a part → its machine → the map); Close leaves the sheet. Side by side, and not alike.
  document.getElementById("s-back").addEventListener("click", () => { if (openKid) { openKid = null; page = 0; render(); } else { open = null; hide(); draw(); } });
  document.getElementById("s-close").addEventListener("click", () => { open = null; openKid = null; hide(); draw(); });
  document.getElementById("s-problems").addEventListener("click", problems);
  document.getElementById("s-fit").addEventListener("click", fit);
  document.getElementById("s-repair").addEventListener("click", () => post(root.dataset.repairUrl, {}).then((r) => say(r.why)));

  function post(url, payload) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), credentials: "same-origin" })
      .then((r) => r.json().catch(() => ({ why: `error ${r.status}` })))
      .catch(() => ({ why: "couldn't reach the portal" }));
  }

  // pan and zoom
  let pts = new Map(), last = null, pinch = 0;
  const toSvg = (cx, cy) => { const r = map.getBoundingClientRect(); return [view.x + ((cx - r.left) / r.width) * view.w, view.y + ((cy - r.top) / r.height) * view.h]; };
  function zoom(f, cx, cy) {
    const [sx, sy] = toSvg(cx, cy), w = Math.min(2000, Math.max(250, view.w * f)), k = w / view.w;
    view = { x: sx - (sx - view.x) * k, y: sy - (sy - view.y) * k, w, h: view.h * k };
    applyView();
  }
  map.addEventListener("wheel", (ev) => { ev.preventDefault(); zoom(ev.deltaY > 0 ? 1.12 : 0.89, ev.clientX, ev.clientY); }, { passive: false });
  map.addEventListener("pointerdown", (ev) => { pts.set(ev.pointerId, [ev.clientX, ev.clientY]); last = [ev.clientX, ev.clientY]; map.classList.add("drag"); });
  map.addEventListener("pointermove", (ev) => {
    if (!pts.has(ev.pointerId)) return;
    pts.set(ev.pointerId, [ev.clientX, ev.clientY]);
    if (pts.size === 2) {
      const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoom(pinch / d, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      pinch = d; return;
    }
    const r = map.getBoundingClientRect();
    view.x -= ((ev.clientX - last[0]) / r.width) * view.w; view.y -= ((ev.clientY - last[1]) / r.height) * view.h;
    last = [ev.clientX, ev.clientY]; applyView();
  });
  const up = (ev) => { pts.delete(ev.pointerId); pinch = 0; last = pts.size ? [...pts.values()][0] : null; if (!pts.size) map.classList.remove("drag"); };
  map.addEventListener("pointerup", up); map.addEventListener("pointercancel", up);

  function load() {
    if (document.hidden) return;
    fetch(root.dataset.stateUrl, { credentials: "same-origin", cache: "no-store" }).then((r) => r.json()).then((d) => {
      data = d; draw(); if (current && current.id !== "problems") render();
      document.getElementById("s-age").textContent = new Date(d.at * 1000).toLocaleTimeString();
    }).catch(() => { document.getElementById("s-age").textContent = "offline"; });
  }
  document.addEventListener("visibilitychange", load);
  load();
  setInterval(load, 10000);
})();
