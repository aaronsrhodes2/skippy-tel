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
  function closeAll() { root.querySelectorAll(".sheet").forEach((s) => { s.hidden = true; }); stopHeroPoll(); }
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
  fearKey.addEventListener("click", () => { closeAll(); $("tt-fearsheet").hidden = false; showFear(fear || 0); });
  $("tt-fear-up").addEventListener("click", () => setFear({ delta: 1 }));
  $("tt-fear-down").addEventListener("click", () => setFear({ delta: -1 }));
  $("tt-fear-up").hidden = $("tt-fear-down").hidden = !GM;
  call("GET", FEAR_URL).then((d) => showFear(d.fear)).catch(() => {});

  // ── the party ──
  async function openParty() {
    closeAll();
    $("tt-party").hidden = false;
    const list = $("tt-party-list");
    list.replaceChildren(el("p", "tt-by", "…"));
    try {
      const { heroes, fear: f } = await call("GET", HEROES_URL);
      showFear(f);
      list.replaceChildren();
      if (!heroes.length) list.appendChild(el("p", "tt-by", GM ? "No heroes yet. Add the first below." : "The GM hasn't added the heroes yet."));
      for (const h of heroes) {
        const b = el("button", "tt-herotile"); b.type = "button"; b.setAttribute("role", "listitem");
        b.appendChild(el("span", "nm", h.name || "Unnamed"));
        b.appendChild(el("span", "cl", [h.class, h.subclass].filter(Boolean).join(" · ") + (h.level ? ` · L${h.level}` : "")));
        b.appendChild(el("span", "pl", h.player_name || h.player || "no player yet"));
        if (h.project) {
          const p = el("span", "pj"); p.appendChild(el("span", null, h.project));
          const clock = el("span", "clk");
          for (let i = 0; i < h.clock_max; i++) clock.appendChild(el("i", i < h.clock ? "on" : ""));
          p.appendChild(clock); b.appendChild(p);
        }
        if (h.gm_ref && h.gm_ref.length) {          // the GM sees each hero's quick reference right on the tile
          const ul = el("ul", "qr");
          h.gm_ref.forEach((d) => ul.appendChild(el("li", null, d)));
          b.appendChild(ul);
        }
        b.addEventListener("click", () => openHero(h.id));
        list.appendChild(b);
      }
      // …and alongside them, everything else of the campaign's: the boards (collages of pictures, cast and lore cards, art)
      const { boards } = await call("GET", root.dataset.boardsUrl);
      if (boards.length) {
        list.appendChild(el("h3", "tt-hub-h", "Boards"));
        for (const b of boards) {
          const t = el("button", "tt-herotile board"); t.type = "button"; t.setAttribute("role", "listitem");
          t.innerHTML = glyph("g-image"); t.appendChild(el("span", "nm", b.title));
          t.addEventListener("click", () => { closeAll(); location.hash = b.id; });
          list.appendChild(t);
        }
      }
    } catch (err) { list.replaceChildren(el("p", "tt-by", err.message)); }
  }
  $("tt-heroes-key").addEventListener("click", openParty);
  $("tt-hero-back").addEventListener("click", openParty);
  const heroNew = $("tt-hero-new");
  if (heroNew) heroNew.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const { id } = await call("POST", HEROES_URL, { name: $("tt-hero-new-name").value }); $("tt-hero-new-name").value = ""; openHero(id); }
    catch (err) { say(err.message); }
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
      try { await call("POST", `${heroUrl(hero.id)}/delete`); openParty(); } catch (err) { say(err.message); }
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
  root.addEventListener("tt-closed", () => { flush(); stopHeroPoll(); });
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
  const heroSheet = $("tt-hero");
  heroSheet.addEventListener("dragover", (e) => { if (tab === "folder" && canEdit && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
  heroSheet.addEventListener("drop", (e) => { if (tab === "folder" && canEdit) { e.preventDefault(); upload(e.dataTransfer.files); } });

  // A link straight to a hero: /tabletop#hero=<id>
  const want = /^#hero=([0-9a-z]+)$/.exec(location.hash);
  if (want) openHero(want[1]);
})();
