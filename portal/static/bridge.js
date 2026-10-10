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
  document.addEventListener("touchstart", function () {}, { passive: true });
  document.addEventListener("touchmove", function (e) {
    // allow a drag only inside something that is meant to scroll or be dragged (a field, a marked panel)
    var el = e.target.closest && e.target.closest("textarea, input, select, [data-pan]");
    if (!el) e.preventDefault();
  }, { passive: false });

  var Bridge = window.Bridge = window.Bridge || {};

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
    // swipe sideways to flip: a deliberate horizontal stroke, not a tap or a vertical drag
    var x0 = null, y0 = 0;
    panel.addEventListener("touchstart", function (e) { var t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; }, { passive: true });
    panel.addEventListener("touchend", function (e) {
      if (x0 === null) return;
      var t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0;
      x0 = null;
      if (Math.abs(dx) > 40 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(page + (dx < 0 ? 1 : -1));
    }, { passive: true });
    if (window.ResizeObserver) new ResizeObserver(refresh).observe(panel);
    var api = { go: go, page: function () { return page; }, pages: function () { return pages; },
                last: function () { go(pages - 1); }, refresh: refresh };
    panel._pager = api;
    refresh();
    return api;
  };
})();
