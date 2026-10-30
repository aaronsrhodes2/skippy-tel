/* The bridge shell (templates/base.html, {% block ui %}bridge{% endblock %}): the one small script every bridge page loads.

   iOS Safari only draws :active (a control's backlight when pressed) if the page has a touch listener; this is that listener.
   It also keeps the screen from being dragged: the page is exactly the viewport, so a pull is never a scroll.

   Bridge.pager: how anything longer than a screen is shown without scrolling. The content flows into COLUMNS exactly as wide as the
   panel and as tall as it is; a page is one column; you flip with a key or swipe sideways. No scrollbar exists to hate.
       Bridge.pager(panel, {pips: railElement, onchange: function (page, pages) {}})  → {go(i), page(), pages(), last(), refresh()}
   `panel` has one child, `.pg-flow`, holding the content. Call refresh() after the content or the panel's size changes (it also
   refreshes on its own when the panel is resized). A panel marked .pg gets its pager as panel._pager.
   Nothing here talks to the network. */
(function () {
  document.addEventListener("touchmove", function (e) {
    // block horizontal drags (the page never scrolls horizontally); let vertical scroll through natively
    // iOS Safari breaks on passive:false — passive:true lets the browser handle vertical scrolls,
    // and we only preventDefault for horizontal drags where the browser would do nothing anyway.
    if (e.touches.length === 1) {
      var t = e.touches[0], dx = t.clientX - (this._x0 || t.clientX), dy = t.clientY - (this._y0 || t.clientY);
      if (!this._x0) { this._x0 = t.clientX; this._y0 = t.clientY; return; }
      if (Math.abs(dx) > 40 && Math.abs(dx) > 1.5 * Math.abs(dy)) e.preventDefault();
    }
  }, { passive: true });
  document.addEventListener("touchstart", function (e) {
    if (e.touches.length === 1) { this._x0 = e.touches[0].clientX; this._y0 = e.touches[0].clientY; }
  }, { passive: true });

  var Bridge = window.Bridge = window.Bridge || {};

  /* A list shown a screen at a time. Nothing on the bridge scrolls: a long list is cut into screens (BridgePure.paginate) and only the
     rows of the current screen exist in the page, so 21,000 songs are about ten rows of DOM. You flip with a swipe, the wheel,
     PageUp/PageDown, or by dragging along the bar under the list (the scrub bar shows where you are in the whole).
       new Bridge.PList(panel, host, makers, {pips, pgno, scrub, onscrub, onpage})
     `makers[type] = {make(), bind(el, row, i)}`; a row is {type, h (px), glue?}. Methods: setRows(rows, page), go(p), goRow(i), refresh(),
     resize(), el(i); fields: rows, starts, page, pages(). */
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  class PList {
    constructor(panel, host, makers, o) {
      this.panel = panel; this.host = host; this.makers = makers; this.o = o || {};
      this.rows = []; this.starts = [0]; this.page = 0; this.live = new Map(); this.pool = Object.create(null); this.dirty = false;
      this.thumb = this.o.pips ? this.o.pips.querySelector("i") : null;
      let x0 = null, y0 = 0, wheelAt = 0;
      panel.addEventListener("touchstart", e => { const t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; }, { passive: true });
      panel.addEventListener("touchend", e => {
        if (x0 === null) return;
        const t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0;
        x0 = null;
        if (Math.abs(dx) > 40 && Math.abs(dx) > 1.5 * Math.abs(dy)) this.go(this.page + (dx < 0 ? 1 : -1));
      }, { passive: true });
      panel.addEventListener("wheel", e => {
        const d = e.deltaY || e.deltaX;
        if (!d) return;
        e.preventDefault();
        const now = performance.now();
        if (now - wheelAt < 180) return;
        wheelAt = now; this.go(this.page + (d > 0 ? 1 : -1));
      }, { passive: false });
      if (this.o.scrub) this.wireScrub(this.o.scrub);
    }
    pages() { return this.starts.length; }
    wireScrub(box) {
      const at = e => { const r = this.o.pips.getBoundingClientRect(); return Math.floor(clamp((e.clientX - r.left) / Math.max(1, r.width), 0, 0.9999) * this.pages()); };
      let down = false;
      const show = on => { if (this.o.onscrub) this.o.onscrub(on, this.page); };
      box.addEventListener("pointerdown", e => { down = true; try { box.setPointerCapture(e.pointerId); } catch (x) { /* synthetic */ } this.go(at(e)); show(true); e.preventDefault(); });
      box.addEventListener("pointermove", e => { if (down) { this.go(at(e)); show(true); } });
      const up = () => { down = false; show(false); };
      box.addEventListener("pointerup", up); box.addEventListener("pointercancel", up);
      box.addEventListener("keydown", e => {
        const k = e.key;
        if (k === "ArrowLeft" || k === "ArrowRight" || k === "Home" || k === "End") {
          e.preventDefault(); e.stopPropagation();
          this.go(k === "Home" ? 0 : k === "End" ? this.pages() - 1 : this.page + (k === "ArrowRight" ? 1 : -1));
        }
      });
    }
    /** Cut the rows into screens for the panel's height now. False when the panel has no size yet (a sheet that is not open). */
    layout() {
      const avail = this.panel.clientHeight;
      if (avail <= 0) { this.dirty = true; return false; }
      this.dirty = false;
      this.starts = BridgePure.paginate(this.rows.map(r => r.h), avail, this.rows.map(r => !!r.glue));
      return true;
    }
    setRows(rows, page) {
      this.rows = rows;
      if (this.layout()) this.page = clamp(page | 0, 0, this.starts.length - 1);
      else { this.starts = [0]; this.page = 0; }
      this.render(0);
    }
    go(p, force) {
      p = clamp(p | 0, 0, this.starts.length - 1);
      if (p === this.page && !force) return false;
      const dir = p > this.page ? 1 : -1;
      this.page = p; this.render(dir);
      return true;
    }
    goRow(i) { this.go(BridgePure.pageOf(this.starts, i)); }
    render(dir) {
      for (const e of this.live.values()) this.release(e);
      this.live.clear();
      const a = this.starts[this.page] || 0, b = this.page + 1 < this.starts.length ? this.starts[this.page + 1] : this.rows.length;
      if (!this.dirty) {
        for (let i = a; i < b; i++) {
          const row = this.rows[i], mk = this.makers[row.type];
          const pool = this.pool[row.type] || (this.pool[row.type] = []);
          let e = pool.pop();
          if (!e) { e = mk.make(); e._type = row.type; }
          e._i = i; e.style.height = row.h + "px";
          mk.bind(e, row, i);
          this.live.set(i, e); this.host.append(e);
        }
      }
      this.host.classList.remove("flip-n", "flip-p");
      if (dir) { void this.host.offsetWidth; this.host.classList.add(dir > 0 ? "flip-n" : "flip-p"); }
      this.paint();
      if (this.o.onpage) this.o.onpage(this.page, this.pages(), dir || 0);
    }
    release(e) { e.remove(); (this.pool[e._type] || (this.pool[e._type] = [])).push(e); }
    refresh() { for (const [i, e] of this.live) this.makers[e._type].bind(e, this.rows[i], i); }
    el(i) { return this.live.get(i) || null; }
    /** The panel changed size: cut the screens again and stay on the one that holds the row that was first. */
    resize() {
      if (!this.rows.length && !this.dirty) return;
      const first = this.starts[this.page] || 0;
      if (!this.layout()) return;
      this.page = BridgePure.pageOf(this.starts, first);
      this.render(0);
    }
    paint() {
      const n = this.pages(), pips = this.o.pips;
      if (pips) {
        pips.hidden = n < 2;
        const w = pips.clientWidth, tw = Math.max(14, w / n);
        this.thumb.style.width = tw + "px";
        this.thumb.style.transform = `translateX(${n > 1 ? (w - tw) * this.page / (n - 1) : 0}px)`;
      }
      if (this.o.pgno) this.o.pgno.textContent = n > 1 ? `${(this.page + 1).toLocaleString()}/${n.toLocaleString()}` : "";
      const sc = this.o.scrub;
      if (sc) { sc.setAttribute("aria-valuemax", n); sc.setAttribute("aria-valuenow", this.page + 1); }
    }
  }
  Bridge.PList = PList;

  Bridge.pager = function (panel, opts) {
    var flow = panel.querySelector(".pg-flow"), pips = opts && opts.pips, thumb = pips && pips.querySelector("i");
    var page = 0, pages = 1, w = 0, onchange = opts && opts.onchange;
    function paintPips() {
      if (!pips) return;
      pips.hidden = pages < 2;                                     // one page: nothing to flip, nothing to show
      if (thumb) { thumb.style.width = (100 / pages) + "%"; thumb.style.transform = "translateX(" + (page * 100) + "%)"; }
    }
    function go(i) {
      page = Math.max(0, Math.min(pages - 1, i));
      flow.style.transform = "translateX(" + (-page * w) + "px)";
      paintPips();
      if (onchange) onchange(page, pages);
    }
    function refresh() {
      w = panel.clientWidth;
      var h = panel.clientHeight;
      if (!w || !h) return;                                        // hidden (a sheet that isn't open): measured when it opens
      flow.style.height = h + "px";
      flow.style.width = w + "px";
      flow.style.columnWidth = w + "px";
      pages = Math.max(1, Math.round(flow.scrollWidth / w));
      go(Math.min(page, pages - 1));
    }
    // a form field in a later page: the browser scrolls the clipped panel to show it, which would leave the pages out of step. Undo that and
    // flip to the page the field is on instead.
    panel.addEventListener("scroll", function () { if (panel.scrollLeft) panel.scrollLeft = 0; });
    flow.addEventListener("focusin", function (e) {
      if (!w) return;
      var col = Math.floor((e.target.getBoundingClientRect().left - flow.getBoundingClientRect().left + 1) / w);
      if (col >= 0 && col < pages && col !== page) go(col);
      if (panel.scrollLeft) panel.scrollLeft = 0;
    });
    // swipe sideways to flip: a deliberate horizontal stroke, not a tap or a vertical drag
    var x0 = null, y0 = 0;
    panel.addEventListener("touchstart", function (e) { var t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; }, { passive: true });
    panel.addEventListener("touchend", function (e) {
      if (x0 === null) return;
      var t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0;
      x0 = null;
      if (Math.abs(dx) > 40 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(page + (dx < 0 ? 1 : -1));
    }, { passive: true });
    // a mouse and a keyboard flip too (a desktop browser has no swipe): one wheel stroke is one page, however many events it sends,
    // and the bar under the panel is a control: click or drag along it to jump, with a ‹ › key at each end
    var wheelSum = 0, wheelQuiet = 0;
    panel.addEventListener("wheel", function (e) {
      if (pages < 2 || e.ctrlKey) return;
      var el = e.target.closest && e.target.closest("textarea, select, [data-pan]");
      if (el && el.scrollHeight > el.clientHeight) return;         // a field that scrolls by itself keeps the wheel
      e.preventDefault();
      var now = Date.now();
      if (now - wheelQuiet > 250) wheelSum = 0;                     // a pause ends a stroke
      wheelQuiet = now;
      if (wheelSum === null) return;                                // this stroke already flipped
      wheelSum += Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
      if (Math.abs(wheelSum) >= 40) { go(page + (wheelSum > 0 ? 1 : -1)); wheelSum = null; }
    }, { passive: false });
    var flip = {};
    if (pips && pips.parentNode) {
      ["prev", "next"].forEach(function (dir) {
        var k = document.createElement("button");
        k.type = "button"; k.className = "key sm pg-key pg-" + dir; k.hidden = true;
        k.setAttribute("aria-label", dir === "prev" ? "Previous page" : "Next page"); k.title = k.getAttribute("aria-label");
        k.innerHTML = '<svg class="g" aria-hidden="true"><use href="#' + (dir === "prev" ? "g-back" : "g-chev") + '"/></svg>';
        k.addEventListener("click", function () { go(page + (dir === "prev" ? -1 : 1)); });
        flip[dir] = k;
      });
      var bar = document.createElement("div");
      bar.className = "pg-bar";
      pips.parentNode.insertBefore(bar, pips);
      bar.appendChild(flip.prev); bar.appendChild(pips); bar.appendChild(flip.next);
      pips.removeAttribute("aria-hidden"); pips.setAttribute("role", "slider"); pips.setAttribute("aria-label", "Page"); pips.tabIndex = 0;
      var scrubbing = false;
      function scrubTo(x) { var r = pips.getBoundingClientRect(); if (r.width) go(Math.floor((x - r.left) / r.width * pages)); }
      pips.addEventListener("pointerdown", function (e) { scrubbing = true; pips.setPointerCapture(e.pointerId); scrubTo(e.clientX); });
      pips.addEventListener("pointermove", function (e) { if (scrubbing) scrubTo(e.clientX); });
      pips.addEventListener("pointerup", function () { scrubbing = false; });
      pips.addEventListener("pointercancel", function () { scrubbing = false; });
      pips.addEventListener("keydown", function (e) {
        var to = { Home: 0, End: pages - 1 }[e.key];                // ←/→ and PageUp/PageDown: the global handler below
        if (to !== undefined) { e.preventDefault(); go(to); }
      });
    }
    var paintBar = paintPips;
    paintPips = function () {
      paintBar();
      if (flip.prev) { flip.prev.hidden = flip.next.hidden = pages < 2; flip.prev.disabled = page === 0; flip.next.disabled = page >= pages - 1; }
      if (pips) { pips.setAttribute("aria-valuemin", "1"); pips.setAttribute("aria-valuemax", String(pages)); pips.setAttribute("aria-valuenow", String(page + 1)); }
    };
    if (window.ResizeObserver) new ResizeObserver(refresh).observe(panel);
    // a picture or a 3D model that arrives later makes the flow longer without resizing the panel: count the pages again (load doesn't
    // bubble, so listen as it comes down). Without this a page of late pictures stopped at the pages it had when it opened.
    flow.addEventListener("load", refresh, true);
    var api = { go: go, page: function () { return page; }, pages: function () { return pages; },
                last: function () { go(pages - 1); }, refresh: refresh };
    panel._pager = api;
    livePagers.push({ panel: panel, api: api });
    refresh();
    return api;
  };

  // The keyboard, for every pager on every page: PageUp/PageDown and ←/→ flip the pager you can see (the one in an open sheet or
  // dialog first), unless you are typing or something else already took the key (a page's own handler on document runs first:
  // this listens on window, so a list that flips itself with PageDown wins). Pages need no key handling of their own.
  var livePagers = [];
  function shownPager() {
    var seen = livePagers.filter(function (p) { return p.panel.isConnected && p.panel.getClientRects().length && p.api.pages() > 1; });
    var top = seen.filter(function (p) { return p.panel.closest(".sheet, [role=dialog]"); });
    return (top.length ? top[top.length - 1] : seen[0]) || null;
  }
  window.addEventListener("keydown", function (e) {
    var step = { PageDown: 1, ArrowRight: 1, PageUp: -1, ArrowLeft: -1 }[e.key];
    if (!step || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target;
    if (t && t.closest && t.closest("input, textarea, select, [contenteditable=''], [contenteditable=true], [data-keys-own]")) return;
    var p = shownPager();
    if (!p) return;
    e.preventDefault();
    p.api.go(p.api.page() + step);
  });
})();
