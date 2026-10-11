/* The N-body flip-book (templates/nbody.html): plates you flip through (swipe, the arrow keys, the bar under them), drawn from one data file
   (static/demos/nbody/flipbook.json, made by scripts/nbody_flipbook_build.py from a run of sigma-ground's rolling-window shootout). Every number
   on a plate is read from that file: the integrator's error against JPL's DE440, the medians and quartiles across the windows, the positions
   the run recorded. Nothing is calculated here but the chart (a log scale, a screen position). No network calls except the data file.

   A plate is {kind: "chart" | "html" | "frames", title, text, draw(ctx, w, h)} (a "frames" plate also has frame count and a frame painter). */
(function () {
  "use strict";
  var root = document.getElementById("fb");
  if (!root) return;
  var stage = document.getElementById("stage"), canvas = document.getElementById("plate"), htmlBox = document.getElementById("html");
  var frameBox = document.getElementById("frames"), frameInput = document.getElementById("frame"), frameLabel = document.getElementById("frame-t");
  var capT = document.getElementById("cap-t"), capP = document.getElementById("cap-p"), pips = document.getElementById("pips"), thumb = pips.querySelector("i");
  var pgno = document.getElementById("pgno"), scrub = document.getElementById("scrub"), notice = document.getElementById("notice"), playKey = document.getElementById("play");
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var css = function (n) { return getComputedStyle(root).getPropertyValue(n).trim() || "#fff"; };
  var el = function (t, c, x) { var n = document.createElement(t); if (c) n.className = c; if (x != null) n.textContent = x; return n; };

  var D = null, plates = [], cur = 0, timer = 0, auto = 0, frameAt = 0;
  var NAMES = { pure_newton: "Newton only", standard: "+ 1PN GR", over_physics_no_j2: "+ solar pressure", over_physics: "+ J2", over_physics_finedt: "+ J2, fine steps", jpl_de440: "DE440 stack", kepler: "Kepler fit" };
  var COLOR = { pure_newton: "--c-newton", standard: "--c-standard", over_physics_no_j2: "--c-srp", over_physics: "--c-j2", over_physics_finedt: "--c-fine", jpl_de440: "--c-jpl", kepler: "--c-kepler" };
  var sci = function (v) { return v === 0 ? "0" : v.toExponential(1).replace("e+", "e").replace("e-", "e−"); };
  var pname = function (p) { return NAMES[p] || p; };
  var median = function (a) { var s = a.slice().sort(function (x, y) { return x - y; }), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

  // ── what each predictor measured, at the last prediction time ──
  function last(c) { return c.med[c.med.length - 1]; }
  function predictors(src) { return Object.keys(COLOR).filter(function (p) { return src[p]; }); }
  function medianAcrossBodies(src, p, only) { return median((only || Object.keys(src[p])).map(function (b) { return last(src[p][b]); })); }
  function commonBodies(p) { return D.meta.bodies.filter(function (b) { return D.old[p] && D.old[p][b] && D.new[p] && D.new[p][b]; }); }
  function best() { return ["jpl_de440", "over_physics_finedt", "over_physics", "standard"].filter(function (p) { return D.new[p]; })[0]; }
  var fmtDate = function (iso) { return /^\d{4}-/.test(iso || "") ? iso.slice(0, 10) : (iso || "an earlier day"); };

  // ── drawing helpers ──
  function fit() {
    var r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1, g = canvas.getContext("2d");
    canvas.width = Math.max(1, Math.round(r.width * dpr)); canvas.height = Math.max(1, Math.round(r.height * dpr));
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, r.width, r.height);
    return { g: g, w: r.width, h: r.height };
  }
  function logRange(vals, bars) {                                           // whole powers of ten around the data; bars start a decade under the smallest
    var v = vals.filter(function (x) { return x > 0; }), lo = Math.floor(Math.log10(Math.min.apply(null, v))) - (bars ? 1 : 0), hi = Math.ceil(Math.log10(Math.max.apply(null, v)));
    if (hi <= lo) hi = lo + 1;
    return [lo, hi];
  }
  function axisLog(g, x0, x1, y0, y1, lo, hi, horizontal) {                 // power-of-ten gridlines and labels, on x (horizontal) or y
    g.font = "10px " + css("--mono"); g.lineWidth = 1;
    for (var e = lo; e <= hi; e++) {
      var t = (e - lo) / (hi - lo);
      g.strokeStyle = css("--line"); g.fillStyle = css("--muted"); g.beginPath();
      if (horizontal) { var x = x0 + t * (x1 - x0); g.moveTo(x, y0); g.lineTo(x, y1); g.stroke(); g.textAlign = "center"; g.fillText("1e" + (e < 0 ? "−" + (-e) : e), x, y1 + 12); }
      else { var y = y1 - t * (y1 - y0); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke(); g.textAlign = "right"; g.fillText("1e" + (e < 0 ? "−" + (-e) : e), x0 - 4, y + 3); }
    }
  }
  function legend(g, ps, x, y, w) {
    g.font = "11px " + css("--mono"); g.textAlign = "left"; var cx = x, cy = y;
    ps.forEach(function (p) {
      var label = pname(p), tw = g.measureText(label).width + 22;
      if (cx + tw > x + w) { cx = x; cy += 15; }
      g.fillStyle = css(COLOR[p]); g.fillRect(cx, cy - 8, 10, 10); g.fillStyle = css("--ink"); g.fillText(label, cx + 14, cy + 1); cx += tw;
    });
    return cy;
  }

  // ── the plates ──
  function build() {
    var ps = predictors(D.new), bodies = D.meta.bodies, b = best(), P = [];
    var runs = D.meta.runs || [D.meta], sg = D.meta.sigma_ground || {};
    var wins = function (p) { return (D.meta.ran_windows || {})[p] || 0; }, shared = (D.meta.shared_windows || []).join(", ");

    // 1. what was run
    P.push({ kind: "html", title: "The N-body program, run again", text: "Rebuilt from a fresh run, not from the earlier plates.", html: function () {
      var d = el("div"), p1 = el("p", null, "On " + fmtDate(D.meta.ran_at_utc) + " sigma-ground's rolling-window shootout was run again with the integrator as it is now" +
        (sg.head ? " (commit " + sg.head.slice(0, 7) + ")" : "") + ", the 1PN sign fix of 2026-10-10 included. Each run starts from JPL's DE440 positions at a yearly snapshot, integrates five years forward, and is compared with DE440 at every later yearly snapshot. Every comparison here uses the " + (D.meta.shared_windows || []).length + " start dates all the predictors were run on (" + shared + "), so none is judged on easier dates than another.");
      var t = el("table", "fbt"), h = el("tr"); ["predictor", "what it includes", "windows run"].forEach(function (x) { h.append(el("th", null, x)); }); t.append(h);
      (D.meta.predictors || []).filter(function (p) { return D.new[p.name]; }).forEach(function (p) {
        var r = el("tr"); r.append(el("td", null, p.name), el("td", null, (p.description || "").replace(/\s*--.*$/, "")), el("td", null, String(wins(p.name)))); t.append(r);
      });
      d.append(p1, t, el("p", "dim", "Positions are heliocentric, errors are the distance from DE440 in AU. Flip on: the ranking, what the fix changed, every body, and two time-lapses."));
      return d;
    } });

    // 2. the ranking
    P.push({ kind: "chart", title: "Which physics matters", text: "The median body's error after five years, per predictor. Lower is closer to DE440.", draw: function (c) {
      var rows = ps.map(function (p) { return [p, medianAcrossBodies(D.new, p)]; }).sort(function (a, z) { return a[1] - z[1]; });
      var r = logRange(rows.map(function (x) { return x[1]; }), true), g = c.g, left = 112, right = c.w - 56, top = 24, rowH = Math.min(44, (c.h - 60) / rows.length);
      axisLog(g, left, right, top - 8, top + rows.length * rowH, r[0], r[1], true);
      g.font = "12px " + css("--mono");
      rows.forEach(function (x, i) {
        var y = top + i * rowH, t = (Math.log10(x[1]) - r[0]) / (r[1] - r[0]);
        g.fillStyle = css("--ink"); g.textAlign = "right"; g.fillText(pname(x[0]), left - 8, y + rowH / 2 + 4);
        g.fillStyle = css(COLOR[x[0]]); g.fillRect(left, y + 5, Math.max(2, t * (right - left)), rowH - 10);
        g.fillStyle = css("--white"); g.textAlign = "left"; g.fillText(sci(x[1]), left + Math.max(2, t * (right - left)) + 6, y + rowH / 2 + 4);
      });
      g.fillStyle = css("--muted"); g.textAlign = "center"; g.fillText("error after 5 years (AU, log scale)", (left + right) / 2, c.h - 6);
    } });

    // 3. what the fix changed
    var both = ps.filter(function (p) { return commonBodies(p).length; });
    if (both.length) {
      var old = function (p) { return medianAcrossBodies(D.old, p, commonBodies(p)); }, now = function (p) { return medianAcrossBodies(D.new, p, commonBodies(p)); };
      var delta = both.map(function (p) { return pname(p) + " " + sci(old(p)) + " → " + sci(now(p)); });
      P.push({ kind: "chart", title: "Before and after the 1PN fix", text: "Median error after five years for the same bodies and the same start dates, the earlier run against this one: " + delta.join("; ") + ". (The earlier run is the shootout's own fixture from May, before its set of bodies grew.)", draw: function (c) {
        var vals = []; both.forEach(function (p) { vals.push(old(p), now(p)); });
        var r = logRange(vals, true), g = c.g, left = 112, right = c.w - 56, top = 34, rowH = Math.min(52, (c.h - 70) / both.length);
        axisLog(g, left, right, top - 8, top + both.length * rowH, r[0], r[1], true);
        g.font = "12px " + css("--mono");
        both.forEach(function (p, i) {
          var y = top + i * rowH, o = old(p), n = now(p), X = function (v) { return left + (Math.log10(v) - r[0]) / (r[1] - r[0]) * (right - left); };
          g.fillStyle = css("--ink"); g.textAlign = "right"; g.fillText(pname(p), left - 8, y + rowH / 2 + 4);
          g.fillStyle = css("--muted"); g.fillRect(left, y + 4, Math.max(2, X(o) - left), (rowH - 10) / 2);
          g.fillStyle = css(COLOR[p]); g.fillRect(left, y + 4 + (rowH - 10) / 2, Math.max(2, X(n) - left), (rowH - 10) / 2);
        });
        g.textAlign = "left"; g.fillStyle = css("--muted"); g.fillRect(left, 8, 10, 10); g.fillStyle = css("--ink"); g.fillText("earlier", left + 14, 17);
        g.fillStyle = css("--accent"); g.fillRect(left + 80, 8, 10, 10); g.fillStyle = css("--ink"); g.fillText("this run", left + 94, 17);
      } });
    }

    // 4. every body at once
    P.push({ kind: "chart", title: "Every body after five years", text: "One dot per predictor for each of " + bodies.length + " bodies. The moons of the giant planets are the hardest.", draw: function (c) {
      var vals = []; ps.forEach(function (p) { bodies.forEach(function (n) { if (D.new[p][n]) vals.push(last(D.new[p][n])); }); });
      var r = logRange(vals), g = c.g, left = 70, right = c.w - 12, top = 40, rowH = (c.h - top - 22) / bodies.length;
      var ly = legend(g, ps, 8, 14, c.w - 16); top = Math.max(top, ly + 14); rowH = (c.h - top - 22) / bodies.length;
      axisLog(g, left, right, top - 2, top + bodies.length * rowH, r[0], r[1], true);
      g.font = "10px " + css("--mono");
      bodies.forEach(function (n, i) {
        var y = top + i * rowH + rowH / 2;
        g.fillStyle = D.meta.parent[n] ? css("--muted") : css("--ink"); g.textAlign = "right"; g.fillText((D.meta.parent[n] ? "· " : "") + n, left - 6, y + 3);
        ps.forEach(function (p) {
          if (!D.new[p][n]) return;
          var x = left + (Math.log10(last(D.new[p][n])) - r[0]) / (r[1] - r[0]) * (right - left);
          g.fillStyle = css(COLOR[p]); g.beginPath(); g.arc(x, y, 3, 0, 6.2832); g.fill();
        });
      });
      g.fillStyle = css("--muted"); g.textAlign = "center"; g.font = "11px " + css("--mono"); g.fillText("error after 5 years (AU, log scale)", (left + right) / 2, c.h - 4);
    } });

    // 5, 6. the time-lapses
    var F = D.frames || {};
    if (F.inner) P.push(orbits(F.inner, ["Mercury", "Venus", "Earth", "Mars"], null, "The inner planets, month by month",
      "Five years of the " + pname(F.inner.predictor) + " run from the " + F.inner.window.replace("j", "J") + " snapshot, one frame a month. The rings are where DE440 puts each planet at the yearly snapshots."));
    if (F.jovian) P.push(orbits(F.jovian, ["Io", "Europa", "Ganymede", "Callisto"], "Jupiter", "Jupiter's four moons, day by day",
      "Ninety days of the " + pname(F.jovian.predictor) + " run, one frame a day, each moon's position relative to Jupiter. The integrator's own track; DE440's snapshots are too far apart to show here."));

    // 7.. one plate per body
    bodies.forEach(function (n) {
      var avail = ps.filter(function (p) { return D.new[p][n]; });
      var par = D.meta.parent[n], head = D.new[b] && D.new[b][n];
      P.push({ kind: "chart", title: n, text: (par ? "A moon of " + par + ". " : "") + "Error against DE440 over five years; the shaded band is the middle half of the windows for " + pname(b) + (head ? " (" + sci(last(head)) + " AU at five years, " + head.n[head.n.length - 1] + " windows)." : "."), draw: function (c) {
        var vals = []; avail.forEach(function (p) { var q = D.new[p][n]; q.med.forEach(function (v, i) { if (q.years[i] > 0) vals.push(v); }); });
        if (head) head.q1.forEach(function (v, i) { if (head.years[i] > 0) vals.push(v, head.q3[i]); });
        var r = logRange(vals), g = c.g, left = 40, right = c.w - 14, top = 44, bot = c.h - 24;
        var ly = legend(g, avail, 8, 14, c.w - 16); top = Math.max(top, ly + 14);
        var X = function (yr) { return left + (yr - 1) / 4 * (right - left); }, Y = function (v) { return bot - (Math.log10(v) - r[0]) / (r[1] - r[0]) * (bot - top); };
        axisLog(g, left, right, top, bot, r[0], r[1], false);
        g.fillStyle = css("--muted"); g.textAlign = "center"; g.font = "10px " + css("--mono");
        for (var yr = 1; yr <= 5; yr++) g.fillText(yr + " y", X(yr), c.h - 8);
        if (head) {
          g.fillStyle = css(COLOR[b]); g.globalAlpha = 0.18; g.beginPath(); var k = 0;
          head.years.forEach(function (yy, i) { if (yy > 0) { if (!k++) g.moveTo(X(yy), Y(head.q3[i])); else g.lineTo(X(yy), Y(head.q3[i])); } });
          for (var i2 = head.years.length - 1; i2 >= 0; i2--) if (head.years[i2] > 0) g.lineTo(X(head.years[i2]), Y(head.q1[i2]));
          g.closePath(); g.fill(); g.globalAlpha = 1;
        }
        avail.forEach(function (p) {
          var q = D.new[p][n]; g.strokeStyle = css(COLOR[p]); g.fillStyle = css(COLOR[p]); g.lineWidth = p === b ? 2.5 : 1.5; g.beginPath(); var first = true;
          q.years.forEach(function (yy, i) { if (yy > 0) { var x = X(yy), y = Y(q.med[i]); if (first) { g.moveTo(x, y); first = false; } else g.lineTo(x, y); } });
          g.stroke();
          q.years.forEach(function (yy, i) { if (yy > 0) { g.beginPath(); g.arc(X(yy), Y(q.med[i]), 2.5, 0, 6.2832); g.fill(); } });
        });
      } });
    });

    // the earlier recorded results, as they were
    P.push({ kind: "html", title: "Earlier: the 26-year Jovian run", text: "Recorded before the fix, shown as it was produced: the drift and the missing physics are in the table on purpose.", source: "src-earlier" });
    P.push({ kind: "html", title: "Earlier: the scoreboard", text: "Scoreboard after 26 years, N-body against Keplerian.", source: "src-score" });
    P.push({ kind: "html", title: "Earlier: the divergence", text: "Tap the figure to open it full size.", source: "src-fig1" });
    P.push({ kind: "html", title: "Earlier: accuracy against tolerance", text: "Tap the figure to open it full size.", source: "src-fig2" });
    P.push({ kind: "html", title: "How to read this", text: "", html: function () {
      var d = el("div");
      d.append(el("p", null, "Each plate is a view of one data file. The error of a prediction is its distance from where JPL's DE440 puts the body, taken at the yearly snapshots (J2000 to J2026), so a window can be checked at one, two, three, four and five years out."),
        el("p", null, "A window is a start date. The cheap predictors were run from sixteen start dates (J2005 to J2020); the slow, fine-step ones from " + (D.meta.shared_windows || []).length + " of them (" + shared + "). Every plate compares predictors on those shared start dates only, and a band is the middle half across them."),
        el("p", null, "Nothing here is tuned: the plates show the run as it came out, including where a body's error is large. The Kepler fit is the baseline with no interactions between bodies. The DE440 stack is the shootout's own name for the integrator with every force layer it has."),
        el("p", "dim", "Source: " + (sg.subject ? "sigma-ground " + (sg.head || "").slice(0, 7) + " (" + sg.subject + ")" : "sigma-ground") + ". The program: scripts/nbody_run.py; the data: scripts/nbody_flipbook_build.py."));
      return d;
    } });
    return P;
  }

  // ── a plate of frames: positions drawn from above, one frame per month or day ──
  function orbits(F, names, centre, title, text) {
    var pos = function (n, k) { var p = F.bodies[n][k], c = centre ? F.bodies[centre][k] : [0, 0, 0]; return [p[0] - c[0], p[1] - c[1]]; };
    var rmax = 0, count = F.jds.length;
    names.forEach(function (n) { for (var k = 0; k < count; k++) { var p = pos(n, k); rmax = Math.max(rmax, Math.hypot(p[0], p[1])); } });
    return { kind: "frames", frames: count, title: title, text: text, draw: function (c, k) {
      var g = c.g, cx = c.w / 2, cy = c.h / 2, s = (Math.min(c.w, c.h) / 2 - 22) / rmax, tail = centre ? 10 : 12;
      g.strokeStyle = css("--line"); g.lineWidth = 1; [0.25, 0.5, 0.75, 1].forEach(function (f) { g.beginPath(); g.arc(cx, cy, f * rmax * s, 0, 6.2832); g.stroke(); });
      g.fillStyle = css(centre ? "--amber" : "--amber"); g.beginPath(); g.arc(cx, cy, centre ? 7 : 6, 0, 6.2832); g.fill();
      g.font = "11px " + css("--mono"); g.textAlign = "left";
      names.forEach(function (n, i) {
        var col = css(["--c-j2", "--c-fine", "--c-standard", "--c-kepler"][i % 4]);
        g.strokeStyle = col; g.lineWidth = 1.5; g.beginPath();
        for (var j = Math.max(0, k - tail); j <= k; j++) { var q = pos(n, j); var x = cx + q[0] * s, y = cy - q[1] * s; if (j === Math.max(0, k - tail)) g.moveTo(x, y); else g.lineTo(x, y); }
        g.globalAlpha = 0.5; g.stroke(); g.globalAlpha = 1;
        var p = pos(n, k); g.fillStyle = col; g.beginPath(); g.arc(cx + p[0] * s, cy - p[1] * s, 4, 0, 6.2832); g.fill();
        g.fillStyle = css("--ink"); g.fillText(n, cx + p[0] * s + 7, cy - p[1] * s + 4);
        if (F.truth && F.truth[n]) F.truth[n].forEach(function (t) {                  // DE440's own position at the yearly snapshots
          var tx = cx + t[1] * s, ty = cy - t[2] * s, now = Math.abs(t[0] - F.jds[k]) < F.step_days / 2 + 0.5;
          g.strokeStyle = css("--white"); g.lineWidth = now ? 2 : 1; g.globalAlpha = now ? 1 : 0.35; g.beginPath(); g.arc(tx, ty, now ? 7 : 5, 0, 6.2832); g.stroke(); g.globalAlpha = 1;
        });
      });
      g.fillStyle = css("--muted"); g.textAlign = "right";
      g.fillText(F.kind === "inner" ? "month " + k + " of " + (count - 1) : "day " + k + " of " + (count - 1), c.w - 8, c.h - 8);
    } };
  }

  // ── showing a plate ──
  function setCaption(p) { capT.textContent = p.title; capP.textContent = p.text || ""; canvas.setAttribute("aria-label", p.title + ". " + (p.text || "")); }
  function paintFrame() {
    var p = plates[cur]; if (!p || p.kind !== "frames") return;
    var c = fit(); p.draw(c, frameAt); frameInput.value = frameAt; frameLabel.textContent = (frameAt + 1) + "/" + p.frames;
  }
  function stopFrames() { clearInterval(timer); timer = 0; }
  function show(i, dir) {
    cur = Math.max(0, Math.min(plates.length - 1, i));
    var p = plates[cur];
    stopFrames();
    setCaption(p);
    canvas.hidden = p.kind === "html"; htmlBox.hidden = p.kind !== "html"; frameBox.hidden = p.kind !== "frames";
    if (p.kind === "html") {
      htmlBox.replaceChildren();
      var src = p.source && document.getElementById(p.source);
      htmlBox.append(src ? src.cloneNode(true) : p.html());
      htmlBox.firstChild.hidden = false; htmlBox.firstChild.removeAttribute("id");
    } else if (p.kind === "chart") { var c = fit(); p.draw(c); }
    else {
      frameAt = 0; frameInput.max = p.frames - 1; paintFrame();
      if (!reduce) timer = setInterval(function () { frameAt = (frameAt + 1) % p.frames; paintFrame(); }, 110);
    }
    stage.classList.remove("flip-n", "flip-p");
    if (dir) { void stage.offsetWidth; stage.classList.add(dir > 0 ? "flip-n" : "flip-p"); }
    var w = pips.clientWidth, tw = Math.max(14, w / plates.length);
    thumb.style.width = tw + "px"; thumb.style.transform = "translateX(" + ((w - tw) * cur / Math.max(1, plates.length - 1)) + "px)";
    pgno.textContent = (cur + 1) + "/" + plates.length;
    scrub.setAttribute("aria-valuemax", plates.length); scrub.setAttribute("aria-valuenow", cur + 1);
    document.getElementById("prev").disabled = cur === 0; document.getElementById("next").disabled = cur === plates.length - 1;
  }
  function flip(by) { var to = cur + by; if (to >= 0 && to < plates.length) show(to, by); }
  function setAuto(on) {
    clearInterval(auto); auto = 0;
    playKey.setAttribute("aria-pressed", on ? "true" : "false");
    playKey.querySelector("use").setAttribute("href", on ? "#g-pause" : "#g-play");
    var word = on ? "Stop flipping" : "Flip through the plates";
    playKey.title = word; playKey.setAttribute("aria-label", word);
    if (on) auto = setInterval(function () { if (cur >= plates.length - 1) { setAuto(false); return; } flip(1); }, 4500);
  }

  document.getElementById("prev").addEventListener("click", function () { flip(-1); });
  document.getElementById("next").addEventListener("click", function () { flip(1); });
  playKey.addEventListener("click", function () { setAuto(!auto); });
  frameInput.addEventListener("input", function () { stopFrames(); frameAt = +frameInput.value; paintFrame(); });
  canvas.addEventListener("click", function () { if (plates[cur] && plates[cur].kind === "frames") { if (timer) stopFrames(); else timer = setInterval(function () { frameAt = (frameAt + 1) % plates[cur].frames; paintFrame(); }, 110); } });
  document.addEventListener("keydown", function (e) {
    if (e.target.matches && e.target.matches("input[type=range]")) return;
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); flip(1); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); flip(-1); }
    else if (e.key === "Home") { e.preventDefault(); show(0, -1); }
    else if (e.key === "End") { e.preventDefault(); show(plates.length - 1, 1); }
    else if (e.key === " ") { e.preventDefault(); setAuto(!auto); }
  });
  var x0 = null, y0 = 0;
  stage.addEventListener("touchstart", function (e) { var t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; }, { passive: true });
  stage.addEventListener("touchend", function (e) {
    if (x0 === null) return; var t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0; x0 = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > 1.5 * Math.abs(dy)) flip(dx < 0 ? 1 : -1);
  }, { passive: true });
  var down = false;
  function scrubTo(e) { var r = pips.getBoundingClientRect(); show(Math.floor(Math.max(0, Math.min(0.9999, (e.clientX - r.left) / r.width)) * plates.length), 0); }
  scrub.addEventListener("pointerdown", function (e) { down = true; try { scrub.setPointerCapture(e.pointerId); } catch (x) { /* synthetic */ } scrubTo(e); e.preventDefault(); });
  scrub.addEventListener("pointermove", function (e) { if (down) scrubTo(e); });
  scrub.addEventListener("pointerup", function () { down = false; }); scrub.addEventListener("pointercancel", function () { down = false; });
  if (window.ResizeObserver) new ResizeObserver(function () { if (plates[cur] && plates[cur].kind === "chart") { var c = fit(); plates[cur].draw(c); } else paintFrame(); }).observe(stage);

  fetch(root.dataset.flipbookUrl, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(function (d) {
    D = d; plates = build();
    var want = parseInt(location.hash.slice(1), 10);
    show(want > 0 && want <= plates.length ? want - 1 : 0, 0);
  }).catch(function () { notice.textContent = "The run's data didn't load. Reload the page to try again."; notice.hidden = false; });
})();
