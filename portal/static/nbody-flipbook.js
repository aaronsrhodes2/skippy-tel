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

  var D = null, B = null, plates = [], cur = 0, timer = 0, auto = 0, frameAt = 0;
  // plain words for the October run's predictor labels (the data file keeps the program's own names)
  var NAMES = { pure_newton: "Newton only", standard: "+ relativity", over_physics_no_j2: "+ sunlight pressure", over_physics: "+ planets' flattening", over_physics_finedt: "+ flattening, finer steps", jpl_de440: "every force layer", kepler: "ellipses only" };
  var COLOR = { pure_newton: "--c-newton", standard: "--c-standard", over_physics_no_j2: "--c-srp", over_physics: "--c-j2", over_physics_finedt: "--c-fine", jpl_de440: "--c-jpl", kepler: "--c-kepler" };
  var sci = function (v) { return v === 0 ? "0" : v.toExponential(1).replace("e+", "e").replace("e-", "e−"); };
  var tenTo = function (e) { return +("1e" + e); };                        // a decade label back to a number (axis bookkeeping, not physics)
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
    var every = horizontal ? Math.max(1, Math.ceil(34 / ((x1 - x0) / Math.max(1, hi - lo)))) : 1;   // label every n-th decade when they would touch
    for (var e = lo; e <= hi; e++) {
      var t = (e - lo) / (hi - lo);
      g.strokeStyle = css("--line"); g.fillStyle = css("--muted"); g.beginPath();
      if (horizontal) { var x = x0 + t * (x1 - x0); g.moveTo(x, y0); g.lineTo(x, y1); g.stroke(); if ((e - lo) % every === 0) { g.textAlign = "center"; g.fillText("1e" + (e < 0 ? "−" + (-e) : e), x, y1 + 12); } }
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

  // ── the three blankets: our prediction, JPL's prediction at the time, reality as the edge ──
  // B (static/demos/nbody/blankets.json): rows = JPL ephemerides by the year they were issued, cols = target years; a cell is the median error against reality (km)
  // of a run started from that ephemeris's own state. Reality has no "issued" axis: it is the diagonal the two blankets hang from.
  function ramp(t) {                                                       // 0..1 -> the five ramp tokens, piecewise
    var k = Math.max(0, Math.min(0.9999, t)) * 4, i = Math.floor(k);
    return mix(css("--ramp-" + i), css("--ramp-" + (i + 1)), k - i);
  }
  function mix(a, b, t) {                                                  // two CSS colours (hex or rgb()) blended; falls back to a when it cannot parse
    var pa = parse(a), pb = parse(b); if (!pa || !pb) return a;
    return "rgb(" + Math.round(pa[0] + (pb[0] - pa[0]) * t) + "," + Math.round(pa[1] + (pb[1] - pa[1]) * t) + "," + Math.round(pa[2] + (pb[2] - pa[2]) * t) + ")";
  }
  function parse(c) {
    var m = /^#([0-9a-f]{6})$/i.exec(c); if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)];
    m = /rgba?\(([^)]+)\)/.exec(c); if (m) { var p = m[1].split(",").map(parseFloat); return [p[0], p[1], p[2]]; }
    return null;
  }
  function blanketRange(sets) {                                            // shared log range over every finite cell of the given grids
    var v = []; sets.forEach(function (g) { g.forEach(function (row) { row.forEach(function (x) { if (x != null && x > 0) v.push(x); }); }); });
    return v.length ? logRange(v) : [-2, 3];
  }
  function heatPanel(g, grid, x0, y0, w, h, r, rows, cols, issue, withEdge) {
    var cw = w / cols.length, rh = h / rows.length;
    if (!grid.some(function (row) { return row.some(function (x) { return x != null; }); })) { pending(g, x0, y0, w, h); return; }   // nothing measured yet: say so, not an empty grid
    for (var i = 0; i < rows.length; i++) for (var j = 0; j < cols.length; j++) {
      var v = grid[i][j], y = y0 + h - (i + 1) * rh;                     // oldest issue at the bottom: rows cool as they rise
      if (v == null) continue;
      g.fillStyle = v > 0 ? ramp((Math.log10(v) - r[0]) / (r[1] - r[0])) : css("--ramp-0");
      g.fillRect(x0 + j * cw, y, Math.ceil(cw), Math.ceil(rh));
    }
    if (withEdge) {                                                        // reality: the edge where the target is the issue date
      g.strokeStyle = css("--c-truth"); g.lineWidth = 2; g.beginPath();
      for (var k = 0; k < rows.length; k++) {
        var xe = x0 + Math.max(0, Math.min(cols.length, issue[k] - cols[0] + 0.5)) * cw, yt = y0 + h - (k + 1) * rh;
        if (!k) g.moveTo(xe, y0 + h); g.lineTo(xe, yt + rh); g.lineTo(xe, yt);
      }
      g.stroke();
    }
    g.strokeStyle = css("--line"); g.lineWidth = 1; g.strokeRect(x0, y0, w, h);
  }
  function pending(g, x0, y0, w, h) {
    g.fillStyle = css("--muted"); g.font = "10px " + css("--mono"); g.textAlign = "center";
    g.fillText("pending the", x0 + w / 2, y0 + h / 2 - 6); g.fillText("DE442 rerun", x0 + w / 2, y0 + h / 2 + 8);
    g.strokeStyle = css("--line"); g.lineWidth = 1; g.strokeRect(x0, y0, w, h);
  }
  function winsPanel(g, ours, jpl, band, x0, y0, w, h, rows, cols) {      // who is closer to reality; grey where both sit inside the reality band
    var cw = w / cols.length, rh = h / rows.length;
    if (!ours.some(function (row, i) { return row.some(function (x, j) { return x != null && jpl[i][j] != null; }); })) { pending(g, x0, y0, w, h); return; }
    for (var i = 0; i < rows.length; i++) for (var j = 0; j < cols.length; j++) {
      var a = ours[i][j], b = jpl[i][j]; if (a == null || b == null) continue;
      var y = y0 + h - (i + 1) * rh, floor = band && band[j] != null ? band[j] : 0;
      g.fillStyle = (a <= floor && b <= floor) ? css("--muted") : a < b ? css("--c-ours") : css("--c-theirs");
      g.globalAlpha = (a <= floor && b <= floor) ? 0.5 : Math.min(1, 0.35 + Math.abs(Math.log10(Math.max(a, 1e-9) / Math.max(b, 1e-9)))); g.fillRect(x0 + j * cw, y, Math.ceil(cw), Math.ceil(rh)); g.globalAlpha = 1;
    }
    g.strokeStyle = css("--line"); g.lineWidth = 1; g.strokeRect(x0, y0, w, h);
  }
  function blanketPlate(name, bl, text) {
    var rows = B.meta.vintages, cols = B.meta.targets, issue = B.meta.issue_years;
    return { kind: "chart", title: name, text: text, draw: function (c) {
      var g = c.g, r = blanketRange([bl.ours, bl.jpl]), gap = 14, left = 46, top = 30, narrow = (c.w - left - 10 - 2 * gap) / 3 < 150, bot = c.h - (narrow ? 62 : 46);
      var pw = (c.w - left - 10 - 2 * gap) / 3, ph = bot - top;
      g.font = "11px " + css("--mono"); g.textAlign = "left"; g.fillStyle = css("--c-ours"); g.fillText(narrow ? "ours" : B.meta.words.ours, left, 16);
      g.fillStyle = css("--c-theirs"); g.fillText(narrow ? "JPL at the time" : B.meta.words.jpl, left + pw + gap, 16);
      g.fillStyle = css("--ink"); g.fillText(narrow ? "closer" : "who is closer", left + 2 * (pw + gap), 16);
      heatPanel(g, bl.ours, left, top, pw, ph, r, rows, cols, issue, true);
      heatPanel(g, bl.jpl, left + pw + gap, top, pw, ph, r, rows, cols, issue, true);
      winsPanel(g, bl.ours, bl.jpl, bl.band, left + 2 * (pw + gap), top, pw, ph, rows, cols);
      g.fillStyle = css("--muted"); g.font = "9px " + css("--mono"); g.textAlign = "right";
      var lastLabel = null;                                                 // one label per distinct issue year, and only when the rows are tall enough to read
      rows.forEach(function (v, i) { var yr = String(Math.round(issue[i])); if (yr !== lastLabel && ph / rows.length >= 10) { g.fillText(yr, left - 4, bot - (i + 0.5) * (ph / rows.length) + 3); lastLabel = yr; } });
      g.textAlign = "center"; [0, Math.floor(cols.length / 2), cols.length - 1].forEach(function (j) { g.fillText(String(cols[j]), left + (j + 0.5) * (pw / cols.length), bot + 11); });
      g.fillText("target year →", left + pw / 2, bot + 24); g.save(); g.translate(10, (top + bot) / 2); g.rotate(-Math.PI / 2); g.fillText("issued ↑", 0, 0); g.restore();
      var lx = left + 2 * (pw + gap), lw = pw, ly = bot + 8;                // the ramp legend under the third panel, clear of the data
      for (var s = 0; s < lw; s++) { g.fillStyle = ramp(s / lw); g.fillRect(lx + s, ly, 1, 6); }
      g.fillStyle = css("--muted"); g.textAlign = "left"; g.fillText(sci(tenTo(r[0])) + " km", lx, ly + 17); g.textAlign = "right"; g.fillText(sci(tenTo(r[1])) + " km", lx + lw, ly + 17);
      var keys = [["— reality (the edge)", "--c-truth"], ["■ ours closer", "--c-ours"], ["■ JPL closer", "--c-theirs"], ["■ both inside reality's spread", "--muted"]];
      g.textAlign = "left"; var kx = left, ky = bot + 36;                   // measured, wrapping: the words never overlap, whatever the width
      keys.forEach(function (it) { var tw = g.measureText(it[0]).width + 16; if (kx + tw > c.w - 10 && kx > left) { kx = left; ky += 13; } g.fillStyle = css(it[1]); g.fillText(it[0], kx, ky); kx += tw; });
    } };
  }
  function leadPlate(name, L) {                                            // one lead: error against the target year, with reality's spread as the floor
    var byL = L[String(1)] ? L : null;
    return { kind: "frames", frames: 5, still: true, title: name + ": the same lead, year after year", text: "Pick a lead with the slider (1 to 5 years ahead). Each point is the median over every JPL ephemeris whose prediction, started that many years before, lands on that target year. The grey floor is how far JPL's previous reality (DE440) sits from today's (DE442): below it, nothing can be told apart from the truth.", draw: function (c, k) {
      var g = c.g, lead = String(k + 1), q = L[lead]; if (!q) { g.fillStyle = css("--muted"); g.fillText("no runs at this lead", 20, 30); return; }
      var bl = B.blankets[name] || B.composite, band = bl.band || [], vals = q.ours.concat(q.jpl).concat(band.filter(function (x) { return x != null && x > 0; }));
      var r = logRange(vals), left = 44, right = c.w - 14, top = 30, bot = c.h - 26, t0 = q.targets[0], t1 = q.targets[q.targets.length - 1];
      var X = function (t) { return left + (t - t0) / Math.max(1, t1 - t0) * (right - left); }, Y;
      // the legend first, laid out by measured width so the words never pile up; the chart starts under it
      var items = [["● " + B.meta.words.ours, "--c-ours"], ["- - " + B.meta.words.jpl, "--c-theirs"], ["■ reality's own spread", "--muted"], [lead + " year" + (lead === "1" ? "" : "s") + " ahead, km (log)", "--ink"]];
      g.font = "11px " + css("--mono"); g.textAlign = "left"; var lx = left, ly = 14;
      items.forEach(function (it) { var tw = g.measureText(it[0]).width + 18; if (lx + tw > right && lx > left) { lx = left; ly += 15; } g.fillStyle = css(it[1]); g.fillText(it[0], lx, ly); lx += tw; });
      top = ly + 12; Y = function (v) { return bot - (Math.log10(Math.max(v, tenTo(r[0]))) - r[0]) / (r[1] - r[0]) * (bot - top); };
      axisLog(g, left, right, top, bot, r[0], r[1], false);
      g.fillStyle = css("--muted"); g.globalAlpha = 0.35; g.beginPath(); g.moveTo(left, bot);
      B.meta.targets.forEach(function (t, j) { if (band[j] != null && band[j] > 0) g.lineTo(X(t), Y(band[j])); });
      g.lineTo(right, bot); g.closePath(); g.fill(); g.globalAlpha = 1;
      // ours as a solid line, JPL's dashed ON TOP: where the two coincide (the composite, the giants) the dashes show through instead of vanishing
      var line = function (vals) { var pen = false; g.beginPath(); q.targets.forEach(function (t, i) { var v = vals[i]; if (v == null || !(v > 0)) { pen = false; return; } var x = X(t), y = Y(v); if (!pen) { g.moveTo(x, y); pen = true; } else g.lineTo(x, y); }); g.stroke(); };
      g.strokeStyle = css("--c-ours"); g.fillStyle = css("--c-ours"); g.lineWidth = 2.5; line(q.ours);     // a missing cell breaks the line; it is never drawn at the floor
      q.targets.forEach(function (t, i) { if (q.ours[i] != null && q.ours[i] > 0) { g.beginPath(); g.arc(X(t), Y(q.ours[i]), 2.5, 0, 6.2832); g.fill(); } });
      g.strokeStyle = css("--c-theirs"); g.lineWidth = 1.5; g.setLineDash([5, 4]); line(q.jpl); g.setLineDash([]);
      var pairs = q.targets.filter(function (t, i) { return q.ours[i] != null && q.jpl[i] != null; });
      var same = pairs.length && q.targets.every(function (t, i) { return q.ours[i] == null || q.jpl[i] == null || Math.abs(Math.log10(q.ours[i] / q.jpl[i])) < 0.01; });
      g.fillStyle = css("--muted"); g.textAlign = "right";
      if (!pairs.length) g.fillText("ours: pending the DE442 rerun", right - 4, top + 12); else if (same) g.fillText("the two curves coincide to 1 %", right - 4, top + 12);
      g.font = "10px " + css("--mono"); g.fillStyle = css("--muted"); g.textAlign = "center";
      q.targets.forEach(function (t, i) { if (i % Math.ceil(q.targets.length / Math.max(3, Math.floor((right - left) / 60))) === 0) g.fillText(String(t), X(t), c.h - 8); });
    } };
  }
  // ── the telescope arena: the daily MPC scoreboard (sigma-ground C27) and our own Eros orbit solution (C28); arcseconds are the scoreboard's, nothing is computed here ──
  function skyLegend(g, items, left, right) {                              // measured, wrapping legend; returns the y where the chart may start
    g.font = "11px " + css("--mono"); g.textAlign = "left"; var lx = left, ly = 14;
    items.forEach(function (it) { var tw = g.measureText(it[0]).width + 18; if (lx + tw > right && lx > left) { lx = left; ly += 15; } g.fillStyle = css(it[1]); g.fillText(it[0], lx, ly); lx += tw; });
    return ly + 12;
  }
  function skyBodyPlate(b) {                                                // one tracked body: ours and JPL against the telescopes, window by window, as the prediction ages
    var W = b.windows, vals = [];
    W.forEach(function (s) { [s.ours, s.jpl, s.sigma].forEach(function (v) { if (v != null && v > 0) vals.push(v); }); });
    return { kind: "chart", title: b.name + " against the telescopes", text: "Our prediction, started once from " + b.start_date + " and never re-fitted, scored against each new month of Minor Planet Center observations (dots: our median miss on the sky; dashes: JPL's own prediction for the same instants). The grey floor is the telescopes' own scatter: below it no prediction can be told from the truth. " + (W.length ? "Latest: " + W[W.length - 1].n + " observations to " + W[W.length - 1].to + ", " + W[W.length - 1].lead_years.toFixed(1) + " years after the start." : ""), draw: function (c) {
      var g = c.g, left = 48, right = c.w - 14, bot = c.h - 26, r = logRange(vals);
      var top = skyLegend(g, [["● " + B.meta.words.ours, "--c-ours"], ["- - " + B.meta.words.jpl, "--c-theirs"], ["■ the telescopes' scatter", "--muted"], ["arcsec on the sky (log) vs years after the start", "--ink"]], left, right);
      var L0 = Math.floor(W[0].lead_years), L1 = Math.max(L0 + 1, Math.ceil(W[W.length - 1].lead_years));   // the axis spans the scored leads (the scoreboard began at 6 yr for Eros)
      var X = function (L) { return left + (L - L0) / (L1 - L0) * (right - left); }, Y = function (v) { return bot - (Math.log10(Math.max(v, tenTo(r[0]))) - r[0]) / (r[1] - r[0]) * (bot - top); };
      axisLog(g, left, right, top, bot, r[0], r[1], false);
      g.fillStyle = css("--muted"); g.globalAlpha = 0.35; g.beginPath(); g.moveTo(X(W[0].lead_years), bot);
      W.forEach(function (s) { if (s.sigma > 0) g.lineTo(X(s.lead_years), Y(s.sigma)); }); g.lineTo(X(W[W.length - 1].lead_years), bot); g.closePath(); g.fill(); g.globalAlpha = 1;
      var line = function (key) { var pen = false; g.beginPath(); W.forEach(function (s) { var v = s[key]; if (!(v > 0)) { pen = false; return; } var x = X(s.lead_years), y = Y(v); if (!pen) { g.moveTo(x, y); pen = true; } else g.lineTo(x, y); }); g.stroke(); };
      g.strokeStyle = css("--c-ours"); g.fillStyle = css("--c-ours"); g.lineWidth = 2.5; line("ours");
      W.forEach(function (s) { if (s.ours > 0) { g.beginPath(); g.arc(X(s.lead_years), Y(s.ours), 2.5, 0, 6.2832); g.fill(); } });
      g.strokeStyle = css("--c-theirs"); g.lineWidth = 1.5; g.setLineDash([5, 4]); line("jpl"); g.setLineDash([]);
      g.font = "10px " + css("--mono"); g.fillStyle = css("--muted"); g.textAlign = "center";
      for (var L = L0; L <= L1; L++) g.fillText(L + " yr", X(L), c.h - 8);
    } };
  }
  function erosFitPlate(E) {                                                // our own orbit solution for Eros against the observations it never saw, beside JPL's
    var rows = E.per_year, vals = [];
    rows.forEach(function (p) { [p.own_fit_median_arcsec, p.from_jpl_state_median_arcsec, p.jpl_median_arcsec].forEach(function (v) { if (v > 0) vals.push(v); }); });
    return { kind: "chart", title: "Eros from our own orbit solution", text: "No borrowed start: Eros's orbit was fitted from Minor Planet Center observations of " + E.fit_window[0].slice(0, 4) + " to " + E.fit_window[1].slice(0, 4) + " alone (" + E.observations_used + " of " + E.observations_total + " kept, rms " + E.fit_rms_arcsec.toFixed(2) + " arcsec) with our engine as the model, then propagated into the " + E.n_predicted_observations + " observations that followed. The solid line is that solution; the dotted line is our engine started from JPL's state instead (dynamics alone); the dashes are JPL. Median miss on the sky per year.", draw: function (c) {
      var g = c.g, left = 48, right = c.w - 14, bot = c.h - 26, r = logRange(vals);
      var top = skyLegend(g, [["● our fit, our engine", "--c-ours"], ["· · our engine from JPL's state", "--c-ours"], ["- - " + B.meta.words.jpl, "--c-theirs"], ["arcsec (log) vs years after the fit's end", "--ink"]], left, right);
      var L1 = Math.ceil(rows[rows.length - 1].lead_years_median);
      var X = function (L) { return left + L / L1 * (right - left); }, Y = function (v) { return bot - (Math.log10(Math.max(v, tenTo(r[0]))) - r[0]) / (r[1] - r[0]) * (bot - top); };
      axisLog(g, left, right, top, bot, r[0], r[1], false);
      var line = function (key) { g.beginPath(); rows.forEach(function (p, i) { var x = X(p.lead_years_median), y = Y(p[key]); if (!i) g.moveTo(x, y); else g.lineTo(x, y); }); g.stroke(); };
      g.strokeStyle = css("--c-ours"); g.fillStyle = css("--c-ours"); g.lineWidth = 2.5; line("own_fit_median_arcsec");
      rows.forEach(function (p) { g.beginPath(); g.arc(X(p.lead_years_median), Y(p.own_fit_median_arcsec), 2.5, 0, 6.2832); g.fill(); });
      g.lineWidth = 1.5; g.setLineDash([1.5, 3]); line("from_jpl_state_median_arcsec");
      g.strokeStyle = css("--c-theirs"); g.setLineDash([5, 4]); line("jpl_median_arcsec"); g.setLineDash([]);
      g.font = "10px " + css("--mono"); g.fillStyle = css("--muted"); g.textAlign = "center";
      for (var L = 0; L <= L1; L++) g.fillText(L + " yr", X(L), c.h - 8);
    } };
  }
  function skySetPlate(S) {                                                 // every tracked body at its latest window: ours and JPL, one row each
    var rows = S.bodies.map(function (b) { var s = b.windows[b.windows.length - 1]; return { name: b.name, lead: s.lead_years, ours: s.ours, jpl: s.jpl, sigma: s.sigma, n: s.n }; }).sort(function (a, b) { return b.lead - a.lead; });
    var vals = []; rows.forEach(function (q) { [q.ours, q.jpl, q.sigma].forEach(function (v) { if (v > 0) vals.push(v); }); });
    return { kind: "chart", title: "The tracked set against the telescopes", text: rows.length + " bodies the scoreboard follows every day (Eros and Apophis, then the heaviest asteroids), each at its latest month of observations: our median miss on the sky and JPL's for the same instants, with the telescopes' scatter as the grey bar. Longest-running prediction at the top.", draw: function (c) {
      var g = c.g, left = 112, right = c.w - 14, bot = c.h - 24, r = logRange(vals);
      var top = skyLegend(g, [["● " + B.meta.words.ours, "--c-ours"], ["◇ " + B.meta.words.jpl, "--c-theirs"], ["▬ the telescopes' scatter", "--muted"], ["arcsec on the sky (log)", "--ink"]], left, right);
      var rh = (bot - top) / rows.length, X = function (v) { return left + (Math.log10(Math.max(v, tenTo(r[0]))) - r[0]) / (r[1] - r[0]) * (right - left); };
      axisLog(g, left, right, top, bot, r[0], r[1], true);
      var same = rows.every(function (q) { return !(q.ours > 0 && q.jpl > 0) || Math.abs(Math.log10(q.ours / q.jpl)) < 0.01; });
      if (same) { g.font = "10px " + css("--mono"); g.fillStyle = css("--muted"); g.textAlign = "right"; g.fillText("our dot sits inside JPL's diamond on every row: the two coincide to 1 %", right - 4, top + 10); }
      g.font = (rh >= 11 ? 10 : 8) + "px " + css("--mono");
      rows.forEach(function (q, i) {
        var y = top + (i + 0.5) * rh;
        if (q.sigma > 0) { g.fillStyle = css("--muted"); g.globalAlpha = 0.35; g.fillRect(left, y - Math.max(1, rh * 0.3), X(q.sigma) - left, Math.max(2, rh * 0.6)); g.globalAlpha = 1; }
        if (q.jpl > 0) { g.strokeStyle = css("--c-theirs"); g.lineWidth = 1.5; g.beginPath(); g.moveTo(X(q.jpl), y - 3.5); g.lineTo(X(q.jpl) + 3.5, y); g.lineTo(X(q.jpl), y + 3.5); g.lineTo(X(q.jpl) - 3.5, y); g.closePath(); g.stroke(); }
        if (q.ours > 0) { g.fillStyle = css("--c-ours"); g.beginPath(); g.arc(X(q.ours), y, 2.5, 0, 6.2832); g.fill(); }
        if (rh >= 8) { g.fillStyle = css("--ink"); g.textAlign = "right"; g.fillText(q.name.slice(0, 11) + " " + q.lead.toFixed(1) + "y", left - 6, y + 3); }
      });
    } };
  }
  function skyWidePlate(W) {                                                // the whole sky: every public observation of the nightly-scored bodies against our frozen predictions, by class and by lead
    var classes = Object.keys(W.by_class), leads = Object.keys(W.by_lead);
    return { kind: "html", title: "The whole sky", text: W.n_bodies.toLocaleString() + " bodies, " + W.n_obs.toLocaleString() + " Minor Planet Center observations scored against our own prediction from the MPC orbit of the day each body was first seen, never refreshed; " + W.n_forward.toLocaleString() + " of them were observed after that day, so they are forecasts in the strict sense.", html: function () {
      var d = el("div");
      d.append(el("p", null, "The daily scoreboard above follows a few dozen bodies with JPL beside them. This table is the rest of the sky: every night the pipeline reads the Minor Planet Center's orbit update, freezes a start for any body it has not seen, fetches the observations of the near-Earth bodies touched that day and a rotating sample of the others, and scores them all with the same engine in one pass. A miss at lead zero or less is the orbit fit's own residual; a miss at positive lead is ours ahead of the telescopes."));
      var t = el("table", "skywide"), h = el("tr"); ["class", "bodies", "observations", "median (all)", "median (forward)"].forEach(function (x) { h.append(el("th", null, x)); }); t.append(h);
      classes.forEach(function (k) { var v = W.by_class[k], r = el("tr"); [k, v.bodies.toLocaleString(), v.n_obs.toLocaleString(), v.median_arcsec.toFixed(3) + "″", v.median_forward_arcsec == null ? "—" : v.median_forward_arcsec.toFixed(3) + "″"].forEach(function (x) { r.append(el("td", null, x)); }); t.append(r); });
      d.append(t);
      var t2 = el("table", "skywide"), h2 = el("tr"); ["lead", "observations", "median", "90th percentile"].forEach(function (x) { h2.append(el("th", null, x)); }); t2.append(h2);
      leads.forEach(function (k) { var v = W.by_lead[k], r = el("tr"); [k, v.n.toLocaleString(), v.median_arcsec.toFixed(3) + "″", v.p90_arcsec.toFixed(3) + "″"].forEach(function (x) { r.append(el("td", null, x)); }); t2.append(r); });
      d.append(t2);
      d.append(el("p", "dim", "As of " + fmtDate(W.generated_utc) + ", " + W.nights.length + " night" + (W.nights.length === 1 ? "" : "s") + ". Each night's forecast for the next seven midnights is sealed and hashed before the observations exist (sigma-ground fixtures/mpc_live/sky_seal_log.jsonl)."));
      return d;
    } };
  }
  function skyPlates() {
    var S = B && B.sky; if (!S || !S.bodies || !S.bodies.length) return [];
    var P = [], eros = S.bodies.filter(function (b) { return b.number === 433; })[0];
    P.push({ kind: "html", title: "Against the telescopes", text: "The one arena where reality is a measurement, not an ephemeris.", html: function () {
      var d = el("div");
      d.append(el("p", null, "For the planets, reality is JPL's latest fit. For asteroids it is the sky itself: the Minor Planet Center publishes every night's observations, so a prediction can be scored against what telescopes saw, and so can JPL's. Every day the scoreboard fetches the newest positions of " + S.bodies.length + " bodies, propagates OUR prediction from a start that is never moved, and scores both predictions on the sky (" + S.ledger + ")."),
        el("p", null, "Eros is the proof piece: its orbit was also fitted by us from observations alone, so for one body the prediction owes nothing to JPL, start included."),
        el("p", "dim", "Scoreboard as of " + fmtDate(S.generated_utc) + "; units: " + S.units + "."));
      return d;
    } });
    if (eros) P.push(skyBodyPlate(eros));
    if (S.eros_fit) P.push(erosFitPlate(S.eros_fit));
    P.push(skySetPlate(S));
    if (B.sky_wide && B.sky_wide.by_class && Object.keys(B.sky_wide.by_class).length) P.push(skyWidePlate(B.sky_wide));
    return P;
  }
  function blanketPlates() {
    if (!B) return [];
    var P = [], w = B.meta.words;
    P.push({ kind: "html", title: "Three words", text: "How to read the blankets that follow.", html: function () {
      var d = el("div");
      d.append(el("p", null, w.ours + " is this program's integrator. " + w.jpl + " is JPL's own ephemeris as it stood when it was issued, run forward from the same starting state. " + w.truth + " is JPL's latest fit, " + B.meta.truth + ", which has every observation to date in it."),
        el("p", null, "Each blanket is a grid: up the side, the year an ephemeris was issued (what was known when); along the bottom, the year being predicted. A cell is the median distance from reality, in kilometres, over every run started from that ephemeris. Reality has no 'issued' axis, so it is the bright edge both blankets hang from; above the edge is the future as seen from each issue date."),
        el("p", null, "Rows should cool as they rise: a later ephemeris knows more. The third panel says who was closer in each cell, and goes grey where both sit inside reality's own spread, the distance between JPL's previous reality (DE440) and today's."),
        el("p", "dim", "This build: reality = " + B.meta.truth + "; JPL's prediction at the time from " + B.meta.vintages.length + " ephemerides (" + B.meta.vintages[0] + " to " + B.meta.vintages[B.meta.vintages.length - 1] + "), " +
          B.meta.runs + " integrated runs; " + (B.meta.ours_note || "") + ". Built " + fmtDate(B.meta.built_utc) + "."));
      return d;
    } });
    skyPlates().forEach(function (p) { P.push(p); });                      // the telescope arena first: Eros, then the tracked set
    P.push(blanketPlate("Everything at once, by mass", B.composite, "Every body folded into one number: the mass-weighted error (DE440 masses; Jupiter and Saturn carry 92 % of it, so this blanket is mostly the giants, whose limit is the starting state, not the physics). Oldest issue at the bottom, reality along the bright edge."));
    P.push(leadPlate("Everything at once, by mass", B.by_lead[B.meta.bodies[0]] ? composeLead("mass") : {}));
    if (B.composite_body) {
      P.push(blanketPlate("Everything at once, by body", B.composite_body, "The same grid with each body counted once: the median over the ten bodies in each cell, so the Moon's fraction of a kilometre weighs as much as Uranus's hundreds. This is the typical body; the mass blanket is the centre of mass."));
      P.push(leadPlate("Everything at once, by body", B.by_lead[B.meta.bodies[0]] ? composeLead("body") : {}));
    }
    B.meta.bodies.forEach(function (n) {
      P.push(blanketPlate(n, B.blankets[n], (n === "Moon" ? "The Moon, geocentric error folded into heliocentric. " : "") + "Issued year up the side, target year along the bottom, kilometres from reality by colour; the bright edge is reality."));
      P.push(leadPlate(n, B.by_lead[n] || {}));
    });
    return P;
  }
  function composeLead(how) {                                              // the composite by-lead curves from the per-body ones: "mass" = weighted mean, "body" = median; a cell needs every body
    var out = {}, wts = B.composite.weights, N = B.meta.bodies.length;
    for (var L = 1; L <= 5; L++) {
      var acc = {}; B.meta.bodies.forEach(function (n) {
        var q = B.by_lead[n] && B.by_lead[n][String(L)]; if (!q) return;
        q.targets.forEach(function (t, i) { var a = acc[t] = acc[t] || { o: [], j: [], wo: [], wj: [] }; if (q.ours[i] != null) { a.o.push(q.ours[i]); a.wo.push(wts[n]); } if (q.jpl[i] != null) { a.j.push(q.jpl[i]); a.wj.push(wts[n]); } });
      });
      var fold = function (v, w) { if (v.length < N) return null; if (how === "body") return median(v); var s = 0, sw = 0; v.forEach(function (x, k) { s += w[k] * x; sw += w[k]; }); return s / sw; };
      var ts = Object.keys(acc).map(Number).sort(function (a, b) { return a - b; }).filter(function (t) { return acc[t].j.length === N; });
      if (ts.length) out[String(L)] = { targets: ts, ours: ts.map(function (t) { return fold(acc[t].o, acc[t].wo); }), jpl: ts.map(function (t) { return fold(acc[t].j, acc[t].wj); }) };
    }
    return out;
  }

  // ── the plates ──
  // The deck shown by default is the comparison: the three words, everything at once, then each body, then one plate on which physics matters and the two time-lapses.
  // ?all appends the October run's full set (its predictor table, the 1PN before/after, every body's five-year curve, the earlier recorded results).
  var FULL = /[?&]all\b/.test(location.search);
  function build() {
    var ps = predictors(D.new), bodies = D.meta.bodies, b = best(), P = blanketPlates();
    P.push({ kind: "chart", title: "Which physics matters", text: "The same integrator with more of the real forces switched on, from a Newton-only baseline to every layer: the median body's error after five years, against DE440. Lower is closer.", draw: function (c) {
      var rows = ps.map(function (p) { return [p, medianAcrossBodies(D.new, p)]; }).sort(function (a, z) { return a[1] - z[1]; });
      var g = c.g; g.font = "12px " + css("--mono");
      var labelW = Math.max.apply(null, rows.map(function (x) { return g.measureText(pname(x[0])).width; }));
      var r = logRange(rows.map(function (x) { return x[1]; }), true), left = Math.min(labelW + 14, c.w * 0.5), right = c.w - 56, top = 24, rowH = Math.min(44, (c.h - 60) / rows.length);
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
    var F0 = D.frames || {};
    if (F0.inner) P.push(orbits(F0.inner, ["Mercury", "Venus", "Earth", "Mars"], null, "The inner planets, month by month",
      "Five years of the integrator's own track from the " + F0.inner.window.replace("j", "J") + " start, one frame a month. The rings are where JPL's DE440 puts each planet at the yearly snapshots."));
    if (F0.jovian) P.push(orbits(F0.jovian, ["Io", "Europa", "Ganymede", "Callisto"], "Jupiter", "Jupiter's four moons, day by day",
      "Ninety days of the same run, one frame a day, each moon's position relative to Jupiter."));
    if (!FULL) return P;
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
      frameAt = p.still ? Math.min(p.frames - 1, 2) : 0; frameInput.max = p.frames - 1; paintFrame();
      if (!reduce && !p.still) timer = setInterval(function () { frameAt = (frameAt + 1) % p.frames; paintFrame(); }, 110);   // a "still" plate is a slider, not a time-lapse
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

  var getJson = function (u) { return fetch(u, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); };
  Promise.all([getJson(root.dataset.flipbookUrl), root.dataset.blanketsUrl ? getJson(root.dataset.blanketsUrl).catch(function () { return null; }) : null]).then(function (ds) {
    D = ds[0]; B = ds[1]; plates = build();
    var want = parseInt(location.hash.slice(1), 10);
    show(want > 0 && want <= plates.length ? want - 1 : 0, 0);
  }).catch(function () { notice.textContent = "The run's data didn't load. Reload the page to try again."; notice.hidden = false; });
})();
