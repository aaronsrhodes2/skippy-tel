// The Tabletop's heroes (templates/tabletop.html, portal/tabletop.py → Heroes): the party, each hero's sheet with every field of the
// official Daggerheart sheet and its character guide page, the hero's current project, their folder, and the GM's Fear.
// A hero's player (and the GM) can change the sheet; every field saves itself a moment after you stop typing, and the open sheet
// takes in other people's changes every few seconds without touching the field you are in. Pages are flipped with the bar under the
// sheet, the wheel or the arrow keys (Bridge.pager).
(() => {
  "use strict";
  const root = document.getElementById("tt");
  if (!root || root.dataset.ready !== "1") return;
  const $ = (id) => document.getElementById(id);
  const GM = root.dataset.gm === "1";
  const HEROES_URL = root.dataset.heroesUrl, FEAR_URL = root.dataset.fearUrl;
  const heroUrl = (id) => root.dataset.heroUrl.replace("HID", encodeURIComponent(id));
  const POLL_MS = 4000, SAVE_MS = 700, FEAR_MAX = 12;
  const notice = $("tt-notice");

  async function call(method, url, body) {
    const opt = { method, headers: { "X-Tabletop": "1" }, credentials: "same-origin" };
    if (body instanceof FormData) opt.body = body;
    else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    const resp = await fetch(url, opt);
    let data = {};
    try { data = await resp.json(); } catch (e) { /* not JSON */ }
    if (!resp.ok) throw new Error(data.error || `Something went wrong (${resp.status}).`);
    return data;
  }
  let noticeTimer = null;
  function say(text, ms = 4000) {
    notice.textContent = text; notice.hidden = false;
    clearTimeout(noticeTimer);
    if (ms) noticeTimer = setTimeout(() => { notice.hidden = true; }, ms);
  }
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const glyph = (id) => `<svg class="g" aria-hidden="true"><use href="#${id}"/></svg>`;
  function closeAll() { root.querySelectorAll(".sheet").forEach((s) => { s.hidden = true; }); stopHeroPoll(); stopFearFx(); }
  const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
  function put(obj, path, value) {
    const keys = path.split("."), last = keys.pop();
    keys.reduce((o, k) => o[k], obj)[last] = value;
  }

  // ── the Fear ──
  let fear = null;
  const fearN = $("tt-fear-n"), fearKey = $("tt-fear");
  function showFear(n) {
    if (typeof n !== "number") return;
    fear = n;
    fearN.textContent = String(n);
    fearKey.classList.toggle("lit", n > 0);
    $("tt-fear-big").textContent = String(n);
    const pips = $("tt-fear-pips"); pips.replaceChildren();
    for (let i = 0; i < FEAR_MAX; i++) {
      const b = el("button", "pip" + (i < n ? " on" : "")); b.type = "button";
      b.setAttribute("aria-label", `Fear ${i + 1}`); b.disabled = !GM;
      b.addEventListener("click", () => setFear({ value: i + 1 === n ? i : i + 1 }));
      pips.appendChild(b);
    }
  }
  async function setFear(body) {
    try { showFear((await call("POST", FEAR_URL, body)).fear); } catch (err) { say(err.message); }
  }
  root.addEventListener("tt-fear", (e) => showFear(e.detail));
  fearKey.addEventListener("click", () => { closeAll(); $("tt-fearsheet").hidden = false; showFear(fear || 0); startFearFx(); });
  $("tt-fear-up").addEventListener("click", () => setFear({ delta: 1 }));
  $("tt-fear-down").addEventListener("click", () => setFear({ delta: -1 }));
  $("tt-fear-up").hidden = $("tt-fear-down").hidden = !GM;
  call("GET", FEAR_URL).then((d) => showFear(d.fear)).catch(() => {});

  // ── the Fear's backdrop: "Fear..." over a void of stars, giant ghostly wireframe skulls turning slowly, wireframe tentacles
  // lashing and mingling. Lines only (no fills): black stays see-through on the glasses. It runs only while the sheet is open, and
  // draws a single still frame for anyone who asked their device for less motion. ──
  const fxCanvas = $("tt-fear-fx");
  const STILL = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const RED = "255,68,68", VIOLET = "129,140,248", CYAN = "0,204,255", WHITE = "255,255,255";
  // a skull in unit space (about 0.8 wide, 1 high, centred): the outline, the eye sockets, the nose, the teeth
  const SKULL = (() => {
    const outline = [];
    for (let i = 0; i <= 10; i++) { const a = Math.PI * (1.08 - 1.16 * (i / 10)); outline.push([0.4 * Math.cos(a), -0.1 - 0.4 * Math.sin(a)]); }
    outline.push([0.34, 0.12], [0.27, 0.2], [0.22, 0.27], [0.2, 0.42], [0.1, 0.5], [-0.1, 0.5], [-0.2, 0.42], [-0.22, 0.27], [-0.27, 0.2], [-0.34, 0.12]);
    const eye = (cx) => Array.from({ length: 8 }, (_, i) => [cx + 0.1 * Math.cos(i * Math.PI / 4 + 0.4), 0.0 + 0.11 * Math.sin(i * Math.PI / 4 + 0.4)]);
    const teeth = [-0.14, -0.07, 0, 0.07, 0.14].map((x) => [[x, 0.37], [x, 0.47]]);
    return { outline, eyes: [eye(-0.16), eye(0.16)], nose: [[0, 0.12], [-0.045, 0.25], [0.045, 0.25]], teeth };
  })();
  let fx = null;
  function stopFearFx() {
    if (!fx) return;
    cancelAnimationFrame(fx.raf);
    if (fx.watch) fx.watch.disconnect();
    fx = null;
  }
  function startFearFx() {
    if (!fxCanvas || fx) return;
    const ctx = fxCanvas.getContext("2d");
    const st = fx = { raf: 0, t: 0, last: 0, w: 0, h: 0, stars: [], skulls: [], tents: [], watch: null };
    const rnd = (a, b) => a + Math.random() * (b - a);
    function seed() {
      const { w, h } = st;
      st.stars = Array.from({ length: Math.round(w * h / 7000) }, () => ({ x: rnd(0, w), y: rnd(0, h), r: rnd(.4, 1.6), ph: rnd(0, 6.3), sp: rnd(.4, 1.8) }));
      st.skulls = [{ x: .2, y: .55, k: .85, ph: 0, sp: .21, c: RED, a: .24 }, { x: .8, y: .4, k: .7, ph: 2, sp: .17, c: VIOLET, a: .22 },
                   { x: .52, y: .85, k: .6, ph: 4, sp: .13, c: RED, a: .16 }];
      const side = (n, x, y, ang, len, c) => Array.from({ length: n }, (_, i) => ({ x: x(i), y: y(i), ang, len, c, ph: rnd(0, 6.3), sp: rnd(.5, 1.1), amp: rnd(.5, .9), wid: rnd(10, 22) }));
      st.tents = [...side(3, (i) => w * (.15 + .35 * i), () => h + 10, -Math.PI / 2, h * 1.05, VIOLET),
                  ...side(2, () => -10, (i) => h * (.35 + .3 * i), -.25, w * .75, CYAN),
                  ...side(2, () => w + 10, (i) => h * (.3 + .35 * i), Math.PI + .25, w * .75, VIOLET)];
    }
    function size() {
      const r = fxCanvas.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      if (!r.width || !r.height) return;
      fxCanvas.width = Math.round(r.width * dpr); fxCanvas.height = Math.round(r.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      st.w = r.width; st.h = r.height; seed();
      if (STILL) draw();
    }
    function line(pts, close) {
      ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      if (close) ctx.closePath();
      ctx.stroke();
    }
    function skull(s, t) {
      const H = Math.min(st.w, st.h) * 1.05 * s.k, turn = 0.3 + 0.7 * Math.abs(Math.cos(t * s.sp + s.ph));      // turning about its own axis
      const cx = st.w * s.x + Math.sin(t * s.sp * 1.7 + s.ph) * st.w * .03, cy = st.h * s.y + Math.cos(t * s.sp * 1.3 + s.ph) * st.h * .03;
      const P = ([x, y]) => [cx + x * turn * H, cy + y * H];
      ctx.strokeStyle = `rgba(${s.c},${s.a * (0.75 + 0.25 * Math.sin(t * .9 + s.ph))})`; ctx.lineWidth = 1.4;
      const out = SKULL.outline.map(P);
      line(out, true);
      for (let i = 0; i < out.length; i += 2) line([out[i], P([0, 0.06])]);                 // facets: the wireframe's struts
      SKULL.eyes.forEach((e) => { const pts = e.map(P); line(pts, true); pts.forEach((p, i) => { if (i % 2 === 0) line([p, P([e[0][0] * 0 + (e === SKULL.eyes[0] ? -.16 : .16), 0])]); }); });
      line(SKULL.nose.map(P), true);
      SKULL.teeth.forEach((tp) => line(tp.map(P)));
      line([P([-.14, .42]), P([.14, .42])]);
    }
    function tentacle(n, t) {
      const N = 26, seg = n.len / N, pts = [];
      let x = n.x, y = n.y, a = n.ang;
      const lash = Math.pow(Math.max(0, Math.sin(t * n.sp * .6 + n.ph)), 3);                // now and then, a whip
      for (let i = 0; i <= N; i++) {
        const s = i / N;
        a = n.ang + n.amp * Math.sin(t * n.sp * 1.4 - s * 5 + n.ph) * (.25 + s) + lash * 1.1 * Math.sin(t * 5 - s * 7 + n.ph) * s;
        pts.push([x, y, a, n.wid * Math.pow(1 - s, .85) + 1.5]);
        x += Math.cos(a) * seg * (1 - .5 * s); y += Math.sin(a) * seg * (1 - .5 * s);
      }
      const left = pts.map(([px, py, pa, w]) => [px + Math.cos(pa + Math.PI / 2) * w, py + Math.sin(pa + Math.PI / 2) * w]);
      const right = pts.map(([px, py, pa, w]) => [px + Math.cos(pa - Math.PI / 2) * w, py + Math.sin(pa - Math.PI / 2) * w]);
      ctx.strokeStyle = `rgba(${n.c},.3)`; ctx.lineWidth = 1.3;
      line(left); line(right);
      ctx.strokeStyle = `rgba(${n.c},.16)`;
      for (let i = 0; i <= N; i += 2) line([left[i], right[i]]);                            // the ribs: it reads as a tube, not a line
      line(pts.map((p) => [p[0], p[1]]));
      ctx.strokeStyle = `rgba(${n.c},.22)`;
      for (let i = 3; i < N - 2; i += 3) { ctx.beginPath(); ctx.arc(left[i][0], left[i][1], Math.max(1.5, pts[i][3] * .22), 0, 6.3); ctx.stroke(); }   // suckers
    }
    function draw() {
      const { w, h, t } = st;
      ctx.clearRect(0, 0, w, h);
      st.stars.forEach((s) => {
        ctx.strokeStyle = `rgba(${WHITE},${.25 + .45 * Math.abs(Math.sin(t * s.sp + s.ph))})`; ctx.lineWidth = s.r;
        ctx.beginPath(); ctx.moveTo(s.x - s.r, s.y); ctx.lineTo(s.x + s.r, s.y); ctx.moveTo(s.x, s.y - s.r); ctx.lineTo(s.x, s.y + s.r); ctx.stroke();
      });
      st.skulls.forEach((s) => skull(s, t));
      const face = 'Consolas, "Courier New", monospace';                                      // "Fear...": outlined, never filled, as big as fits
      ctx.font = `700 100px ${face}`;
      const fs = Math.min(h * .5, 100 * (w * .92) / Math.max(1, ctx.measureText("Fear...").width));
      ctx.font = `700 ${fs}px ${face}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.lineWidth = 2; ctx.strokeStyle = `rgba(${RED},${.34 + .1 * Math.sin(t * 2.3)})`;
      ctx.strokeText("Fear...", w / 2 + Math.sin(t * 7) * .8, h * .5);
      st.tents.forEach((n) => tentacle(n, t));
    }
    function frame(now) {
      if (!fx || fx !== st) return;
      const dt = Math.min(.05, (now - (st.last || now)) / 1000); st.last = now;
      if (!document.hidden) { st.t += dt; draw(); }
      st.raf = requestAnimationFrame(frame);
    }
    if (window.ResizeObserver) { st.watch = new ResizeObserver(size); st.watch.observe(fxCanvas); }
    size();
    if (STILL) draw(); else st.raf = requestAnimationFrame(frame);
  }
  root.addEventListener("tt-closed", stopFearFx);

  // an avatar cropped to its face: the picture scaled up around the focus point (x, y in percent, zoom 1 to 4) inside a clipping box
  function cropTo(img, f) {
    const o = f || { x: 50, y: 30, zoom: 1 };
    img.style.transformOrigin = `${o.x}% ${o.y}%`; img.style.transform = `scale(${o.zoom})`;
  }
  function avatarBox(h, src, cls) {
    const wrap = el("span", "avwrap" + (cls ? " " + cls : "")), img = el("img");
    img.src = src; img.alt = ""; img.loading = "lazy"; cropTo(img, h.avatar_focus);
    wrap.appendChild(img);
    return wrap;
  }

  // ── the heroes: a card each along the top of the tabletop, like dossiers at the table's edge ──
  const strip = $("tt-strip");
  let stripSig = "";
  async function drawStrip() {
    try {
      const { heroes, fear: f } = await call("GET", HEROES_URL);
      showFear(f);
      const sig = JSON.stringify(heroes);
      if (sig === stripSig) return;
      stripSig = sig;
      strip.replaceChildren();
      for (const h of heroes) {
        const b = el("button", "tt-herocard"); b.type = "button"; b.setAttribute("role", "listitem");
        if (h.avatar) b.appendChild(avatarBox(h, `${heroUrl(h.id)}/files/${h.avatar}`, ""));   // their face; the folder glyph until one is chosen
        else b.innerHTML = glyph("g-folder");
        b.appendChild(el("span", "nm", h.name || "Unnamed"));
        b.appendChild(el("span", "cl", [h.class, h.level ? `L${h.level}` : ""].filter(Boolean).join(" · ")));
        b.appendChild(el("span", "pl", h.player_name || (h.player ? h.player.split("@")[0] : "")));
        if (h.project) {
          const clock = el("span", "clk"); clock.title = h.project;
          for (let i = 0; i < h.clock_max; i++) clock.appendChild(el("i", i < h.clock ? "on" : ""));
          b.appendChild(clock);
        }
        if (h.gm_ref && h.gm_ref.length) b.title = h.gm_ref.join("\n");   // the GM's quick reference, on hover
        b.addEventListener("click", () => openHero(h.id));
        strip.appendChild(b);
      }
      if (GM) {
        const add = el("button", "tt-herocard add"); add.type = "button"; add.innerHTML = glyph("g-new");
        add.setAttribute("aria-label", "Add a hero"); add.title = "Add a hero";
        add.addEventListener("click", () => { closeAll(); $("tt-newhero").hidden = false; $("tt-hero-new-name").focus(); });
        strip.appendChild(add);
      } else if (!heroes.length) strip.appendChild(el("span", "tt-by", "The GM hasn't added the heroes yet."));
    } catch (err) { say(err.message); }
  }
  drawStrip();
  setInterval(() => { if (!document.hidden) drawStrip(); }, 15000);
  const heroNew = $("tt-hero-new");
  if (heroNew) heroNew.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const { id } = await call("POST", HEROES_URL, { name: $("tt-hero-new-name").value });
      $("tt-hero-new-name").value = ""; await drawStrip(); openHero(id);
    } catch (err) { say(err.message); }
  });

  // ── one hero ──
  // The sheet's layout: sections in the official sheet's order. Each field names the path it edits (portal/tabletop.py SPEC).
  const T = (path, label, o = {}) => ({ k: "text", path, label, ...o });
  const A = (path, label, o = {}) => ({ k: "area", path, label, ...o });
  const N = (path, label, lo, hi, o = {}) => ({ k: "num", path, label, lo, hi, ...o });
  const C = (path, label) => ({ k: "check", path, label });
  const K = (path, label, max, o = {}) => ({ k: "track", path, label, max, ...o });   // boxes you tap; max may be a path
  const TRAITS = [["agility", "Sprint · Leap · Maneuver"], ["strength", "Lift · Smash · Grapple"], ["finesse", "Control · Hide · Tinker"],
                  ["instinct", "Perceive · Sense · Navigate"], ["presence", "Charm · Perform · Deceive"], ["knowledge", "Recall · Analyze · Comprehend"]];
  const weapon = (p) => [T(`${p}.name`, "Name"), T(`${p}.trait_range`, "Trait & range"), T(`${p}.damage`, "Damage dice & type"), A(`${p}.feature`, "Feature", { rows: 2 })];
  const LEVEL = [["traits", 3, "+1 to two unmarked traits, and mark them"], ["hp", 2, "+1 Hit Point slot"], ["stress", 2, "+1 Stress slot"],
                 ["experiences", 1, "+1 to two Experiences"], ["domain_card", 1, "An extra domain card of your level or lower"],
                 ["evasion", 1, "+1 Evasion"], ["subclass", 1, "Upgraded subclass card (cross out multiclass)"],
                 ["proficiency", 2, "+1 Proficiency"], ["multiclass", 2, "Multiclass: an additional class"]];
  const tier = (t, label, keys) => ({ title: label, fields: LEVEL.filter(([k]) => keys.includes(k)).map(([k, n, text]) => K(`levelups.${t}.${k}`, text, n)) });
  const ALL = LEVEL.map(([k]) => k), T2 = ["traits", "hp", "stress", "experiences", "domain_card", "evasion"];
  const SHEET = [
    { title: "Hero", fields: [T("name", "Name"), T("pronouns", "Pronouns"), T("heritage", "Heritage"), T("class", "Class"), T("domains", "Domains"),
                               T("subclass", "Subclass"), N("level", "Level", 1, 10)] },
    { title: "Evasion & armor", fields: [N("evasion", "Evasion", 0, 40), N("armor_score", "Armor", 0, 12), K("armor_slots", "Armor slots", 12)] },
    { title: "Traits", traits: true },
    { title: "Damage & health", fields: [N("thresholds.major", "Major threshold (minor below)", 0, 99), N("thresholds.severe", "Severe threshold", 0, 99),
                                         K("hp.marked", "HP", "hp.max", { maxPath: "hp.max" }), K("stress.marked", "Stress", "stress.max", { maxPath: "stress.max" })] },
    { title: "Hope", fields: [K("hope", "Hope", 6, { shape: "dia" }), A("hope_feature", "Hope feature", { rows: 3 })] },
    { title: "Experience", fields: [0, 1, 2, 3, 4].map((i) => ({ k: "exp", i })) },
    { title: "Gold", fields: [K("gold.handfuls", "Handfuls", 10, { shape: "round" }), K("gold.bags", "Bags", 10), C("gold.chest", "Chest")] },
    { title: "Class feature", fields: [A("class_feature", "Class feature", { rows: 6 })] },
    { title: "Active weapons", fields: [K("proficiency", "Proficiency", 6, { shape: "round" }), { k: "label", text: "Primary" }, ...weapon("weapons.primary"),
                                        { k: "label", text: "Secondary" }, ...weapon("weapons.secondary")] },
    { title: "Active armor", fields: [T("armor.name", "Name"), T("armor.thresholds", "Base thresholds"), T("armor.score", "Base score"), A("armor.feature", "Feature", { rows: 2 })] },
    { title: "Inventory", fields: Array.from({ length: 12 }, (_, i) => T(`inventory.${i}`, "", { bare: true })) },
    ...[0, 1].map((i) => ({ title: "Inventory weapon", fields: [{ k: "slot", path: `inv_weapons.${i}.slot` }, ...weapon(`inv_weapons.${i}`)] })),
    { title: "Character description", fields: [T("description.clothes", "Clothes that are"), T("description.eyes", "Eyes like"), T("description.body", "Body that's"),
                                               T("description.skin", "Skin the color of"), T("description.attitude", "Attitude like"),
                                               A("starting_choices", "Starting inventory choices", { rows: 2 })] },
    { title: "Background questions", fields: [0, 1, 2].flatMap((i) => [A(`background.${i}.q`, "Question", { rows: 2 }), A(`background.${i}.a`, "Answer", { rows: 3 })]) },
    { title: "Connections", fields: [0, 1, 2].flatMap((i) => [A(`connections.${i}.q`, "Question", { rows: 2 }), A(`connections.${i}.a`, "Answer", { rows: 3 })]) },
    tier("tier2", "Tier 2 · levels 2–4", T2), tier("tier3", "Tier 3 · levels 5–7", ALL), tier("tier4", "Tier 4 · levels 8–10", ALL),
    { title: "Domain cards", fields: Array.from({ length: 10 }, (_, i) => T(`domain_cards.${i}`, "", { bare: true })) },
    { title: "Notes", fields: [A("notes", "Notes", { rows: 8 })] },
  ];
  const PROJECT = [
    { title: "Current project", wide: true, fields: [T("project.name", "Project"), A("project.goal", "What it's for", { rows: 3 }),
      K("project.clock", "Progress", "project.clock_max", { maxPath: "project.clock_max" }), A("project.notes", "Notes", { rows: 8 })] },
  ];

  let hero = null, tab = "sheet", canEdit = false, pager = null, pollTimer = null, pending = new Map(), saveTimer = null, saving = false;
  const flow = $("tt-hero-flow"), saveLamp = $("tt-save");

  function field(f) {
    const box = el("label", "hf hf-" + f.k);
    if (f.label && !f.bare) box.appendChild(el("span", "lb", f.label));
    let input;
    if (f.k === "text" || f.k === "num") {
      input = el("input"); input.type = f.k === "num" ? "number" : "text";
      if (f.k === "num") { input.min = f.lo; input.max = f.hi; input.inputMode = "numeric"; }
      input.autocomplete = "off";
    } else if (f.k === "area") {
      input = el("textarea"); input.rows = f.rows || 3;
    } else if (f.k === "check") {
      input = el("input"); input.type = "checkbox";
      box.classList.add("inline");
    }
    if (input) {
      input.dataset.path = f.path; input.disabled = !canEdit;
      input.addEventListener(f.k === "check" ? "change" : "input", () => queue(f.path, f.k === "check" ? input.checked : input.value));
      box.appendChild(input);
    }
    return box;
  }
  function track(f) {
    const box = el("div", "hf hf-track");
    box.appendChild(el("span", "lb", f.label));
    const row = el("span", "boxes " + (f.shape || "")); row.dataset.track = f.path;
    if (f.maxPath) row.dataset.max = f.maxPath; else row.dataset.fixed = f.max;
    box.appendChild(row);
    if (f.maxPath) {
      const mx = el("span", "mx");
      mx.appendChild(el("span", "lb", "of"));
      const input = el("input"); input.type = "number"; input.min = 1; input.max = 12; input.inputMode = "numeric";
      input.dataset.path = f.maxPath; input.disabled = !canEdit; input.setAttribute("aria-label", f.label + " slots");
      input.addEventListener("input", () => queue(f.maxPath, input.value));
      mx.appendChild(input); box.appendChild(mx);
    }
    return box;
  }
  function drawTrack(row) {
    const s = hero.sheet, n = Number(get(s, row.dataset.track)) || 0;
    const max = row.dataset.max ? Number(get(s, row.dataset.max)) || 0 : Number(row.dataset.fixed);
    row.replaceChildren();
    for (let i = 0; i < max; i++) {
      const b = el("button", i < n ? "on" : ""); b.type = "button"; b.disabled = !canEdit;
      b.setAttribute("aria-label", `${i + 1}`); b.setAttribute("aria-pressed", i < n ? "true" : "false");
      b.addEventListener("click", () => { const v = i + 1 === n ? i : i + 1; queue(row.dataset.track, v); put(hero.sheet, row.dataset.track, v); drawTrack(row); });
      row.appendChild(b);
    }
  }
  function section(sec) {
    const box = el("section", "hs" + (sec.wide ? " wide" : ""));
    box.appendChild(el("h3", null, sec.title));
    if (sec.traits) {
      const grid = el("div", "traits");
      for (const [t, verbs] of TRAITS) {
        const cell = el("div", "trait");
        cell.appendChild(el("span", "tn", t));
        const v = el("input"); v.type = "number"; v.min = -5; v.max = 10; v.inputMode = "numeric"; v.dataset.path = `traits.${t}.value`;
        v.disabled = !canEdit; v.setAttribute("aria-label", t); v.addEventListener("input", () => queue(v.dataset.path, v.value));
        const m = el("input"); m.type = "checkbox"; m.dataset.path = `traits.${t}.marked`; m.disabled = !canEdit;
        m.setAttribute("aria-label", t + " marked"); m.addEventListener("change", () => queue(m.dataset.path, m.checked));
        cell.appendChild(v); cell.appendChild(m); cell.appendChild(el("span", "tv", verbs));
        grid.appendChild(cell);
      }
      box.appendChild(grid);
      return box;
    }
    const grid = el("div", "fields"); box.appendChild(grid);
    for (const f of sec.fields) {
      const put = (node) => grid.appendChild(node);
      if (f.k === "track") put(track(f));
      else if (f.k === "label") put(el("span", "sub", f.text));
      else if (f.k === "exp") {
        const row = el("div", "hf exp");
        row.appendChild(field(T(`experiences.${f.i}.name`, "", { bare: true })));
        row.appendChild(field(N(`experiences.${f.i}.bonus`, "", -5, 10, { bare: true })));
        put(row);
      } else if (f.k === "slot") {
        const row = el("div", "hf slot");
        for (const v of ["primary", "secondary"]) {
          const lab = el("label", "inline"); const r = el("input"); r.type = "checkbox"; r.dataset.path = f.path; r.dataset.slot = v;
          r.disabled = !canEdit;
          r.addEventListener("change", () => { queue(f.path, r.checked ? v : ""); row.querySelectorAll("input").forEach((o) => { if (o !== r) o.checked = false; }); });
          lab.appendChild(r); lab.appendChild(el("span", null, v === "primary" ? "Primary" : "Secondary")); row.appendChild(lab);
        }
        put(row);
      } else put(field(f));
    }
    return box;
  }
  function quickRef() {               // the GM's storytelling crib for this hero: one detail per line, never sent to players
    const box = el("section", "hs gm qref");
    box.appendChild(el("h3", null, "GM quick reference"));
    const area = el("textarea"); area.rows = Math.max(4, (hero.gm_ref || []).length + 1);
    area.value = (hero.gm_ref || []).join("\n");
    area.setAttribute("aria-label", "GM quick reference: one detail per line");
    area.placeholder = "One detail per line: names, special items, abilities, background hooks…";
    let t = null;
    area.addEventListener("input", () => {
      lamp("…"); clearTimeout(t);
      t = setTimeout(async () => {
        try {
          const lines = area.value.split("\n");
          await call("POST", `${heroUrl(hero.id)}/gm`, { gm_ref: lines });
          hero.gm_ref = lines.filter((x) => x.trim()); lamp("saved");
        }
        catch (err) { lamp("not saved"); say(err.message); }
      }, SAVE_MS);
    });
    box.appendChild(area);
    return box;
  }
  function gmPanel() {                // the GM's own controls: whose hero this is, and taking a hero out
    const box = el("section", "hs gm");
    box.appendChild(el("h3", null, "GM"));
    const email = field(T("_player", "Player's Google email")), name = field(T("_player_name", "Player's name"));
    const ei = email.querySelector("input"), ni = name.querySelector("input");
    ei.removeAttribute("data-path"); ni.removeAttribute("data-path");
    ei.value = hero.player || ""; ni.value = hero.player_name || "";
    ei.disabled = ni.disabled = false;
    ei.addEventListener("input", (e) => e.stopImmediatePropagation(), true);
    ni.addEventListener("input", (e) => e.stopImmediatePropagation(), true);
    const savePlayer = async () => {
      try { await call("POST", `${heroUrl(hero.id)}/player`, { player: ei.value, player_name: ni.value }); say("Player saved."); }
      catch (err) { say(err.message); }
    };
    ei.addEventListener("change", savePlayer); ni.addEventListener("change", savePlayer);
    box.appendChild(email); box.appendChild(name);
    const del = el("button", "key sm danger"); del.type = "button"; del.innerHTML = glyph("g-trash");
    del.setAttribute("aria-label", "Remove this hero"); del.title = "Remove this hero";
    del.addEventListener("click", async () => {
      if (!confirm(`Remove ${hero.sheet.name || "this hero"}, their sheet and their folder? This can't be undone.`)) return;
      try { await call("POST", `${heroUrl(hero.id)}/delete`); closeAll(); stripSig = ""; drawStrip(); } catch (err) { say(err.message); }
    });
    box.appendChild(del);
    if (hero.history && hero.history.length) {
      box.appendChild(el("span", "sub", "Latest changes"));
      const log = el("ol", "hist");
      for (const c of hero.history.slice(-8).reverse()) {
        const when = new Date(c.at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
        log.appendChild(el("li", null, `${when} · ${c.by.split("@")[0]} · ${c.path}: ${c.old ?? "–"} → ${c.new ?? "–"}`));
      }
      box.appendChild(log);
    }
    return box;
  }
  function folder() {
    const box = el("section", "hs wide folder");
    box.appendChild(el("h3", null, "Folder"));
    const bar = el("div", "fbar");
    if (canEdit) {
      const add = el("button", "key"); add.type = "button"; add.innerHTML = glyph("g-new");
      add.setAttribute("aria-label", "Add documents or pictures"); add.title = "Add documents or pictures (PDF, JPEG, PNG, GIF, WebP)";
      add.addEventListener("click", () => $("tt-hero-file").click());
      bar.appendChild(add);
      bar.appendChild(el("span", "tt-by", "Add PDFs or pictures: the key, or drop them here."));
    }
    box.appendChild(bar);
    const grid = el("div", "files");
    if (!hero.files.length) grid.appendChild(el("p", "tt-by", "Nothing in the folder yet."));
    for (const f of hero.files) {
      const url = `${heroUrl(hero.id)}/files/${f.id}`;
      const tile = el("div", "file");
      const a = el("a", "open"); a.href = url; a.target = "_blank"; a.rel = "noopener";
      if (f.ext === "pdf") a.innerHTML = glyph("g-sheet") + '<span class="pdf">PDF</span>';
      else { const img = el("img"); img.src = url; img.alt = f.name; img.loading = "lazy"; a.appendChild(img); }
      tile.appendChild(a);
      tile.appendChild(el("span", "fn", f.name));
      tile.appendChild(el("span", "fm", `${(f.size / 1048576).toFixed(f.size > 1048576 ? 1 : 2)} MB · ${f.by.split("@")[0]}`));
      const keys = el("span", "fk");
      const dl = el("a", "key sm"); dl.href = url + "?dl=1"; dl.innerHTML = glyph("g-down"); dl.setAttribute("aria-label", "Download"); dl.title = "Download";
      keys.appendChild(dl);
      if (canEdit && f.ext !== "pdf") {                // a picture can be the hero's face on their card
        const face = el("button", "key sm" + (hero.avatar === f.id ? " on" : "")); face.type = "button"; face.innerHTML = glyph("g-user");
        face.setAttribute("aria-pressed", hero.avatar === f.id ? "true" : "false");
        face.setAttribute("aria-label", "Use as the avatar"); face.title = hero.avatar === f.id ? "This is the avatar (tap to clear it)" : "Use as the avatar";
        face.addEventListener("click", async () => {
          try { await call("POST", `${heroUrl(hero.id)}/avatar`, { file: hero.avatar === f.id ? "" : f.id }); stripSig = ""; await load(hero.id); drawStrip(); }
          catch (err) { say(err.message); }
        });
        keys.appendChild(face);
      }
      if (GM || f.by === root.dataset.me) {
        const del = el("button", "key sm danger"); del.type = "button"; del.innerHTML = glyph("g-trash");
        del.setAttribute("aria-label", "Take it out of the folder"); del.title = "Take it out of the folder";
        del.addEventListener("click", async () => {
          if (!confirm(`Take "${f.name}" out of the folder?`)) return;
          try { await call("POST", `${url}/delete`); await load(hero.id); } catch (err) { say(err.message); }
        });
        keys.appendChild(del);
      }
      tile.appendChild(keys);
      grid.appendChild(tile);
    }
    box.appendChild(grid);
    return box;
  }
  function render() {
    flow.replaceChildren();
    if (tab === "sheet") { if (GM) flow.appendChild(quickRef()); SHEET.forEach((s) => flow.appendChild(section(s))); if (GM) flow.appendChild(gmPanel()); }
    else if (tab === "project") PROJECT.forEach((s) => flow.appendChild(section(s)));
    else flow.appendChild(folder());
    fill();
    root.querySelectorAll("#tt-hero [data-tab]").forEach((k) => k.setAttribute("aria-selected", k.dataset.tab === tab ? "true" : "false"));
    requestAnimationFrame(() => { if (!pager) pager = Bridge.pager($("tt-hero-pg"), { pips: $("tt-hero").querySelector(".pips") }); else pager.refresh(); pager.go(0); });
  }
  function fill() {                    // put the sheet's values in the fields (never the one you are typing in)
    const s = hero.sheet, focused = document.activeElement;
    flow.querySelectorAll("[data-path]").forEach((input) => {
      if (input === focused || pending.has(input.dataset.path)) return;
      const v = get(s, input.dataset.path);
      if (input.dataset.slot) input.checked = v === input.dataset.slot;
      else if (input.type === "checkbox") input.checked = !!v;
      else if (String(input.value) !== String(v ?? "")) input.value = v ?? "";
    });
    flow.querySelectorAll("[data-track]").forEach(drawTrack);
    $("tt-hero-name").textContent = s.name || "Unnamed hero";
    const box = $("tt-hero-avw"), face = $("tt-hero-av");   // the avatar beside the name
    if (hero.avatar) { face.src = `${heroUrl(hero.id)}/files/${hero.avatar}`; cropTo(face, hero.avatar_focus); box.hidden = false; }
    else { face.removeAttribute("src"); box.hidden = true; }
    $("tt-hero-av-key").hidden = !canEdit;             // choosing the avatar is the player's (and the GM's)
  }

  // ── saving: changes wait a moment, then go as one batch ──
  function queue(path, value) {
    pending.set(path, value);
    if (hero) put(hero.sheet, path, value);
    if (path.endsWith(".max") || path.endsWith("clock_max")) flow.querySelectorAll("[data-track]").forEach(drawTrack);
    if (path === "name") $("tt-hero-name").textContent = value || "Unnamed hero";
    lamp("…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, SAVE_MS);
  }
  async function flush() {
    if (!pending.size || !hero) return;
    if (saving) { saveTimer = setTimeout(flush, SAVE_MS); return; }
    const ops = [...pending].map(([path, value]) => ({ path, value }));
    pending = new Map(); saving = true;
    try {
      const { rev } = await call("POST", `${heroUrl(hero.id)}/set`, { ops });
      hero.rev = rev; lamp("saved");
    } catch (err) { lamp("not saved"); say(err.message, 6000); }
    finally { saving = false; }
  }
  function lamp(text) { saveLamp.textContent = text; saveLamp.dataset.s = text; }

  async function load(id) {
    hero = await call("GET", heroUrl(id));
    canEdit = !!hero.can_edit;
    $("tt-hero-print").href = `${heroUrl(id)}/print`;
    render();
  }
  async function openHero(id) {
    closeAll();
    $("tt-hero").hidden = false;
    lamp(""); tab = "sheet";
    try { await load(id); } catch (err) { say(err.message); return; }
    heroPoll();
  }
  function stopHeroPoll() { clearTimeout(pollTimer); pollTimer = null; }
  function heroPoll() {
    stopHeroPoll();
    pollTimer = setTimeout(async () => {
      if (!hero || $("tt-hero").hidden) return;
      if (!document.hidden && !pending.size && !saving) {
        try {
          const d = await call("GET", `${heroUrl(hero.id)}?since=${hero.rev}`);
          if (d.sheet) {
            const filesChanged = JSON.stringify(d.files) !== JSON.stringify(hero.files);
            hero = d; canEdit = !!d.can_edit;
            if (tab === "folder" && filesChanged) render(); else fill();
          }
        } catch (e) { /* a blip: the next poll catches up */ }
      }
      heroPoll();
    }, POLL_MS);
  }
  root.querySelectorAll("#tt-hero [data-tab]").forEach((k) => k.addEventListener("click", () => { flush(); tab = k.dataset.tab; render(); }));
  root.addEventListener("tt-closed", () => { flush(); stopHeroPoll(); setTimeout(drawStrip, 800); });
  window.addEventListener("pagehide", flush);

  // ── the folder: the add key, or a drop on the open folder ──
  async function upload(files) {
    if (!hero || !canEdit) return;
    const list = [...files];
    let n = 0;
    try {
      for (const file of list) {
        say(list.length > 1 ? `Adding ${++n} of ${list.length}…` : "Adding…", 0);
        const form = new FormData(); form.append("file", file, file.name); form.append("name", file.name);
        await call("POST", `${heroUrl(hero.id)}/files`, form);
      }
      notice.hidden = true;
    } catch (err) { say(err.message, 6000); }
    tab = "folder"; await load(hero.id);
  }
  $("tt-hero-file").addEventListener("change", (e) => { upload(e.target.files); e.target.value = ""; });
  // The avatar from a picture on this device: it goes into the folder like any file, and becomes the face on the hero's card.
  $("tt-hero-av-key").addEventListener("click", () => $("tt-avatar-file").click());
  $("tt-avatar-file").addEventListener("change", async (e) => {
    const file = e.target.files[0]; e.target.value = "";
    if (!file || !hero || !canEdit) return;
    try {
      say("Adding the avatar…", 0);
      const form = new FormData(); form.append("file", file, file.name); form.append("name", file.name);
      const { file: added } = await call("POST", `${heroUrl(hero.id)}/files`, form);
      await call("POST", `${heroUrl(hero.id)}/avatar`, { file: added.id });
      notice.hidden = true; stripSig = ""; await load(hero.id); drawStrip();
    } catch (err) { say(err.message, 6000); }
  });
  const heroSheet = $("tt-hero");
  heroSheet.addEventListener("dragover", (e) => { if (tab === "folder" && canEdit && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
  heroSheet.addEventListener("drop", (e) => { if (tab === "folder" && canEdit) { e.preventDefault(); upload(e.dataTransfer.files); } });

  // A link straight to a hero: /tabletop#hero=<id>
  const want = /^#hero=([0-9a-z]+)$/.exec(location.hash);
  if (want) openHero(want[1]);
})();
