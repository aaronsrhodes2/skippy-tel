/* MineBench (templates/minebench.html): the pager (one run a page, in the order they ran), the table's rows that jump to a run, and the
   full-screen viewer. A picture zooms with the wheel, a pinch, a double tap or the lens keys, and pans with a drag; the 3D model is the
   same element moved into the viewer (no second download), turned and zoomed by model-viewer itself, the lens keys narrowing its field
   of view; the verdict opens as text. Esc or the cross puts everything back. Nothing here talks to the network. */
(function () {
  var panel = document.getElementById("mbpg"), flow = panel.querySelector(".pg-flow");
  var pager = Bridge.pager(panel, { pips: panel.parentNode.querySelector(".pips") });

  // the page a run starts on: where its section sits in the flow, whatever page the flow is showing now
  function show(id) {
    var run = document.getElementById("run-" + id);
    if (!run || !panel.clientWidth) return;
    pager.go(Math.round((run.getBoundingClientRect().left - flow.getBoundingClientRect().left) / panel.clientWidth));
  }
  function jump(row) { show(row.dataset.run); history.replaceState(null, "", "#run-" + row.dataset.run); }
  panel.querySelectorAll(".mbruns tr[data-run]").forEach(function (row) {
    row.addEventListener("click", function () { jump(row); });
    row.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); jump(row); } });
  });
  // a link to one run (…/minebench#run-v9) opens on its page, once the pages are measured
  function fromHash() { if (location.hash.indexOf("#run-") === 0) { pager.refresh(); show(location.hash.slice(5)); } }
  window.addEventListener("load", fromHash);
  window.addEventListener("hashchange", fromHash);

  var box = document.getElementById("mbfull"), stage = box.querySelector(".stage");
  var open = null;                                              // {kind, el, home and next (where a moved model goes back), fov}
  var z = { s: 1, x: 0, y: 0, fit: 1 };

  function paint() { open.el.style.transform = "translate(" + z.x + "px," + z.y + "px) scale(" + z.s + ")"; }
  function fit() {
    var w = stage.clientWidth, h = stage.clientHeight, iw = open.el.naturalWidth || 1800, ih = open.el.naturalHeight || 1320;
    z.fit = z.s = Math.min(w / iw, h / ih);
    z.x = (w - iw * z.s) / 2; z.y = (h - ih * z.s) / 2;
    paint();
  }
  function zoomAt(factor, cx, cy) {                             // keep the point under (cx, cy) where it is
    var s = Math.max(z.fit, Math.min(z.fit * 12, z.s * factor));
    z.x = cx - (cx - z.x) * s / z.s; z.y = cy - (cy - z.y) * s / z.s; z.s = s;
    paint();
  }
  function lens(step) {                                         // the model: a narrower field of view is a closer look
    var mv = open.el, now = mv.getFieldOfView ? mv.getFieldOfView() : open.fov;
    var to = step === 0 ? open.fov : Math.max(4, Math.min(open.fov * 1.6, now * (step > 0 ? 0.75 : 1.33)));
    mv.setAttribute("field-of-view", to + "deg");
  }

  function openFull(kind, src) {
    box.classList.toggle("text-only", kind === "text");
    if (kind === "image") {
      var img = new Image();
      img.alt = src.alt; img.draggable = false;
      open = { kind: kind, el: img };
      img.onload = fit;
      img.src = src.currentSrc || src.src;
      stage.appendChild(img);
    } else if (kind === "model") {
      open = { kind: kind, el: src, home: src.parentNode, next: src.nextSibling, fov: src.getFieldOfView ? src.getFieldOfView() : 30 };
      stage.appendChild(src);
    } else {
      var t = document.createElement("div");
      t.className = "text"; t.textContent = src.textContent.trim();
      open = { kind: kind, el: t };
      stage.appendChild(t);
    }
    box.hidden = false;
    if (kind === "image" && open.el.complete && open.el.naturalWidth) fit();
    if (box.requestFullscreen && !document.fullscreenElement) box.requestFullscreen().catch(function () {});
    box.querySelector("[data-close]").focus();
  }
  function closeFull() {
    if (!open) return;
    var was = open;
    open = null;
    if (was.kind === "model") {
      was.el.removeAttribute("field-of-view");
      was.home.insertBefore(was.el, was.next);
    } else stage.removeChild(was.el);
    box.hidden = true;
    if (document.fullscreenElement === box) document.exitFullscreen().catch(function () {});
  }

  panel.addEventListener("click", function (e) {
    var key = e.target.closest("[data-full]"), text = e.target.closest("[data-full-text]");
    if (key) {
      var shot = key.closest(".shot"), mv = shot.querySelector("model-viewer");
      openFull(mv ? "model" : "image", mv || shot.querySelector("img"));
    } else if (text) openFull("text", text);
    else if (e.target.matches(".shot .views")) openFull("image", e.target);
  });
  panel.addEventListener("keydown", function (e) { if (e.key === "Enter" && e.target.matches("[data-full-text]")) openFull("text", e.target); });

  box.querySelector("[data-close]").addEventListener("click", closeFull);
  box.querySelectorAll("[data-z]").forEach(function (k) {
    k.addEventListener("click", function () {
      var step = +k.dataset.z;
      if (open.kind === "model") return lens(step);
      if (step === 0) return fit();
      zoomAt(step > 0 ? 1.5 : 1 / 1.5, stage.clientWidth / 2, stage.clientHeight / 2);
    });
  });
  // the viewer owns the keys while it is open: this listens on document, before the pager's window listener
  document.addEventListener("keydown", function (e) {
    if (!open) return;
    var step = { "+": 1, "=": 1, "-": -1, "_": -1, "0": 0 }[e.key];
    if (e.key === "Escape") { e.preventDefault(); closeFull(); }
    else if (step !== undefined && open.kind !== "text") { e.preventDefault(); box.querySelector('[data-z="' + step + '"]').click(); }
    else if (/^(Arrow|Page)/.test(e.key)) e.preventDefault();
  });
  document.addEventListener("fullscreenchange", function () { if (!document.fullscreenElement && open) closeFull(); });
  window.addEventListener("resize", function () { if (open && open.kind === "image") fit(); });

  // the picture: the wheel zooms where the pointer is, a drag pans, two fingers pinch, a double tap goes in (or back to fit)
  stage.addEventListener("wheel", function (e) {
    if (!open || open.kind !== "image") return;
    e.preventDefault();
    var r = stage.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  var pts = {}, gap = null;
  stage.addEventListener("pointerdown", function (e) {
    if (!open || open.kind !== "image") return;
    stage.setPointerCapture(e.pointerId);
    pts[e.pointerId] = { x: e.clientX, y: e.clientY }; gap = null;
  });
  stage.addEventListener("pointermove", function (e) {
    var p = pts[e.pointerId];
    if (!p) return;
    var ids = Object.keys(pts);
    if (ids.length === 1) { z.x += e.clientX - p.x; z.y += e.clientY - p.y; paint(); }
    pts[e.pointerId] = { x: e.clientX, y: e.clientY };
    if (ids.length === 2) {
      var a = pts[ids[0]], b = pts[ids[1]], d = Math.hypot(a.x - b.x, a.y - b.y), r = stage.getBoundingClientRect();
      if (gap) zoomAt(d / gap, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
      gap = d;
    }
  });
  function up(e) { delete pts[e.pointerId]; gap = null; }
  stage.addEventListener("pointerup", up);
  stage.addEventListener("pointercancel", up);
  stage.addEventListener("dblclick", function (e) {
    if (!open || open.kind !== "image") return;
    var r = stage.getBoundingClientRect();
    if (z.s > z.fit * 1.05) fit(); else zoomAt(3, e.clientX - r.left, e.clientY - r.top);
  });
})();
