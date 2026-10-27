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
  // Two layouts: a wide window spreads the machines out; a phone held upright stacks them in a tall box so the words stay readable.
  const LAYOUTS = {
    wide: {box: [1000, 640], pos: {"skippy-portal": [500, 90], "skippy-pc": [170, 330], "skippy-core": [830, 330], "games-vm": [500, 550]}},
    tall: {box: [560, 900], pos: {"skippy-portal": [280, 110], "skippy-pc": [150, 420], "skippy-core": [410, 420], "games-vm": [280, 760]}},
  };
  let layout = LAYOUTS.wide, POS = layout.pos;
  function pickLayout() {
    const next = map.clientWidth && map.clientHeight && map.clientWidth < map.clientHeight ? LAYOUTS.tall : LAYOUTS.wide;
    if (next !== layout) { layout = next; POS = layout.pos; fit(); draw(); }
  }
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
  function fit() { view = { x: 0, y: 0, w: layout.box[0], h: layout.box[1] }; applyView(); }

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
          const pr = p.ref || {};
          const st = pr.reach || pr.ready ? light(pr.reach, pr.ready) : p.lamp;      // the stoplight, as in the rows
          el("circle", { cx: px, cy: py, r: 9, class: `lamp ${st}` }, k);
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

  // One stoplight dot per service, model or container, made from two facts (PLATINUM section 24): does it answer at all (reachable), and does it say
  // it can take work (ready). GREEN = reachable and ready. YELLOW = it answers but says it is not ready (loading, waiting on something). RED = it does
  // not answer, or it is hung (a job running and going nowhere). BLUE = ON CALL: a tool or model that loads when asked and is not warm right now (the
  // quality picture model, the 3D modeler). GREY = it cannot say (a plain 200 proves nothing), or it is stopped.
  function light(reach, ready) {
    if (reach === "down" || ready === "hung") return "down";
    if (reach === "ok" && ready === "call") return "call";                       // BLUE: on call, loads when asked
    if (reach === "ok" && ready === "ok") return "ok";
    if (reach === "ok" && ready === "down") return "warn";
    return "off";
  }
  const SAYS = {ok: "yes", call: "on call: it loads when asked", down: "no", hung: "hung: a job is running and going nowhere", off: "can't tell"};
  function dots(reach, ready, lamp) {
    const box = html("span", "dots");
    const state = reach || ready ? light(reach, ready) : lamp;
    const d = html("span", `dot ${state}`);
    if (reach || ready) { d.title = `reachable: ${SAYS[reach || "off"]}; ready: ${SAYS[ready || "off"]}`; d.setAttribute("aria-label", d.title); }
    box.append(d);
    return box;
  }
  function row(lamp, name, detail, key, pair) {
    const r = html("div", "hrow");
    r.append(pair ? dots(pair.reach, pair.ready, lamp) : html("span", `dot ${lamp}`), html("span", "nm", name), html("span", "dt", detail || ""));
    if (lamp === "none") r.classList.add("plain");
    if ((detail || "").length > 26) r.classList.add("log");       // a long explanation goes under the name, never in place of it
    if (key) r.append(key);
    return r;
  }
  function key(glyph, label, onClick) {
    const b = html("button", "key"); b.type = "button"; b.setAttribute("aria-label", label); b.title = label;
    b.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-' + glyph + '"/></svg>';
    b.addEventListener("click", (ev) => onClick(ev));
    return b;
  }
  // The log lines: a screen at a time, newest first, filtered by machine, by errors only, or by one source (a container's own).
  let logFilter = {};
  function showLogs(filter) {
    logFilter = Object.assign({}, filter || {});
    current = {id: "logs", label: "Logs"}; openKid = null; open = null; page = 0; draw(); sheet.hidden = false;
    renderLogs();
  }
  function renderLogs() {
    if (!current || current.id !== "logs") return;
    title.textContent = "Logs" + (logFilter.source ? " \u00b7 " + logFilter.source.replace(/^docker\//, "") : "");
    const qs = new URLSearchParams();
    ["host", "source", "level"].forEach((k) => { if (logFilter[k]) qs.set(k, logFilter[k]); });
    qs.set("limit", "300");
    fetch(root.dataset.logsUrl + "?" + qs, {credentials: "same-origin", cache: "no-store"}).then((r) => r.json()).then((d) => {
      if (!current || current.id !== "logs") return;
      const bar = html("div", "hrow");
      ["", "skippy-pc", "skippy-core", "games-vm", "portal"].forEach((h) => {
        const b = html("button", "chip" + ((logFilter.host || "") === h ? " on" : ""), h ? h.replace("skippy-", "") : "all"); b.type = "button";
        b.addEventListener("click", () => { logFilter.host = h; page = 0; renderLogs(); }); bar.append(b);
      });
      const err = html("button", "chip" + (logFilter.level === "error" ? " on" : ""), "errors"); err.type = "button";
      err.addEventListener("click", () => { logFilter.level = logFilter.level === "error" ? "" : "error"; page = 0; renderLogs(); }); bar.append(err);
      const rows = d.records.map((r) => {
        const one = row(r.level === "error" ? "down" : r.level === "warn" ? "degraded" : "ok", r.msg.split("\n")[0].slice(0, 140),
                        r.ts.slice(11, 19) + " " + r.host.replace("skippy-", "") + " " + r.source.replace(/^docker\//, ""));
        one.classList.add("log");
        return one;
      });
      if (!rows.length) rows.push(row("ok", "no lines match", ""));
      pageRows(rows, Math.max(3, Math.floor((sheet.clientHeight - 200) / 66)));
      body.prepend(bar);
    }).catch(() => say("Couldn't read the logs."));
  }
  function probe(names) {
    post(root.dataset.probeUrl, {names}).then((r) => { say(r.why); setTimeout(load, 1500); });
  }
  function healKey(name) {
    const b = html("button", "key"); b.type = "button";
    b.setAttribute("aria-label", `Heal ${name}`); b.title = `Heal ${name}`;
    b.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-retry"/></svg>';
    b.addEventListener("click", () => post(root.dataset.healUrl, { name }).then((r) => say(r.why)));
    return b;
  }
  function pageRows(rows, perPage) {
    const rowsBox = html("div", "hrows");
    const pages = Math.max(1, Math.ceil(rows.length / perPage));
    page = Math.min(page, pages - 1);
    rows.slice(page * perPage, page * perPage + perPage).forEach((r) => rowsBox.append(r));
    const pager = html("div", "hpager");
    const prev = html("button", "key"), next = html("button", "key");
    prev.type = next.type = "button";
    prev.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-up"/></svg>'; prev.setAttribute("aria-label", "Previous page");
    next.innerHTML = '<svg class="g" aria-hidden="true"><use href="#g-down"/></svg>'; next.setAttribute("aria-label", "Next page");
    prev.disabled = page === 0; next.disabled = page >= pages - 1;
    const again = () => (current && current.id === "logs" ? renderLogs() : render());
    prev.onclick = () => { page--; again(); }; next.onclick = () => { page++; again(); };
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
      rows.push(row(openKid.lamp, openKid.name, r.state || r.detail || "", null, r.reach || r.ready ? {reach: r.reach, ready: r.ready} : null));
      if (r.reach || r.ready) {
        rows.push(row(r.reach === "ok" ? "ok" : r.reach === "off" ? "off" : "down", "reachable", SAYS[r.reach || "off"]));
        rows.push(row(r.ready === "ok" ? "ok" : r.ready === "off" ? "off" : r.ready === "hung" ? "hung" : r.ready === "call" ? "call" : "warn", "ready", SAYS[r.ready || "off"] + (r.detail && r.ready !== "call" ? " \u00b7 " + r.detail : "")));
      }
      if (openKid.kind === "c") {
        [["status", r.status], ["image", r.image], ["restarts", r.restarts], ["cpu %", r.cpu], ["memory MB", r.mem_mb]].forEach(([k, v]) => { if (v !== null && v !== undefined && v !== "") rows.push(row("off", k, String(v))); });
        const lg = r.logs;
        rows.push(row(lg && lg.errors ? "degraded" : lg ? "ok" : "off", "its log lines (15 min)", lg ? lg.n + " lines, " + lg.errors + " errors" : "none",
                      key("sheet", "Show its log lines", () => showLogs({host: m.id, source: "docker/" + r.name}))));
        if (m.id === "skippy-pc" && HEAL.has(r.name.replace(/^skippytel-/, ""))) rows.push(row("off", "heal this one", "", healKey(r.name.replace(/^skippytel-/, ""))));
      }
      pageRows(rows, perPage);
      return;
    }
    title.textContent = `${m.label} · ${m.state}`;
    if (m.deployer && m.deployer.state) rows.push(row(m.deployer.state === "ok" ? "ok" : "degraded", "deploys", `${m.deployer.state} ${m.deployer.commit || ""}`));
    for (const [k, v] of Object.entries(m.vitals || {})) rows.push(row("none", k.replace(/_/g, " "), String(v)));
    const opens = (r, part) => { r.classList.add("tap"); r.addEventListener("click", () => { openKid = part; page = 0; render(); }); return r; };   // a row opens its own card
    if (m.containers.length || m.serves.length) {          // what the colours mean, once, at the top
      const lg = html("div", "hrow legend");
      [["ok", "ready"], ["call", "on call"], ["warn", "not ready"], ["down", "down / hung"], ["off", "can't tell"]].forEach(([c, w]) => { lg.append(html("span", `dot ${c}`), html("span", "dt", w)); });
      rows.unshift(lg);
    }
    // "Up 16 minutes" says it is running, not that it is ready: a container with no readiness check shows a hollow ring and says so in words
    const unsure = (c) => light(c.reach, c.ready) === "off" && /^up\b/i.test(c.status || "");
    for (const c of m.containers) rows.push(opens(row(c.lamp, c.name, unsure(c) ? (c.status || "") + " \u00b7 no readiness check" : (c.status || c.state), null, {reach: c.reach, ready: c.ready}), {lamp: c.lamp, name: c.name, kind: "c", ref: c}));
    for (const s of m.serves) {
      const pr = s.probe ? (s.probe.ok ? "answered in " + (s.probe.ms / 1000).toFixed(1) + " s" : "no answer: " + s.probe.why) : "";
      const detail = s.asking ? "asking\u2026" : (pr ? pr : (s.detail || s.kind));
      const r = opens(row(s.probe && !s.probe.ok ? "degraded" : s.lamp, s.name, detail, null, s.reach || s.ready ? {reach: s.reach, ready: s.ready} : null), {lamp: s.lamp, name: s.name, kind: "s", ref: s});
      if (s.kind === "model" && m.id === "skippy-core") r.append(key("play", "Ask " + s.name + " a test question", (ev) => { ev.stopPropagation(); probe([s.name]); }));
      rows.push(r);
    }
    if (m.id === "skippy-core") rows.push(row("off", "ask every model a test question", "", key("play", "Probe every model", () => probe("all"))));
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
  document.getElementById("s-logs").addEventListener("click", () => showLogs({}));
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
  window.addEventListener("resize", pickLayout);
  pickLayout();
  load();
  setInterval(load, 10000);
})();
