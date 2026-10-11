/* Admin on the bridge (templates/admin.html): switches between the deck and a section's pane, nothing more. A pane is pages you flip
   (Bridge.pager); the address remembers which pane is open (#friends), and so does this tab across a form post (the page reloads
   after every save and the pane you were in opens again). No network calls here. */
(function () {
  "use strict";
  var deck = document.getElementById("adeck"), panes = {}, pagers = {}, cur = "";
  Array.prototype.forEach.call(document.querySelectorAll(".apane"), function (p) { panes[p.id] = p; });

  function remember(id) { try { if (id) sessionStorage.setItem("adm.pane", id); else sessionStorage.removeItem("adm.pane"); } catch (e) { /* private window */ } }
  function show(id, quiet) {
    if (!panes[id]) id = "";
    Object.keys(panes).forEach(function (k) { panes[k].hidden = k !== id; });
    deck.hidden = !!id;
    cur = id;
    if (id) {
      var pane = panes[id], panel = pane.querySelector(".pg");
      if (pagers[id]) pagers[id].refresh();
      else pagers[id] = Bridge.pager(panel, { pips: pane.querySelector(".pips") });
      if (!quiet) pagers[id].go(0);
    }
    remember(id);
    var target = id ? "#" + id : location.pathname + location.search;
    if (!quiet && (id ? location.hash !== target : location.hash)) history.replaceState(null, "", id ? target : location.pathname + location.search);
  }
  deck.addEventListener("click", function (e) { var b = e.target.closest("[data-pane]"); if (b) show(b.dataset.pane); });
  document.addEventListener("click", function (e) { if (e.target.closest("[data-close]")) show(""); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && cur && !e.defaultPrevented && !(e.target.matches && e.target.matches("input, textarea, select"))) { show(""); return; }
    if (!cur || (e.target.matches && e.target.matches("input, textarea, select"))) return;
    var pg = pagers[cur];
    if (e.key === "PageDown") { e.preventDefault(); pg.go(pg.page() + 1); }
    else if (e.key === "PageUp") { e.preventDefault(); pg.go(pg.page() - 1); }
  });
  addEventListener("hashchange", function () { show(location.hash.slice(1), true); });
  // a form post reloads the page: remember where the Captain was, so the pane opens again (the flash message says what happened)
  document.addEventListener("submit", function () { remember(cur); });

  var want = location.hash.slice(1), keep = "";
  try { keep = sessionStorage.getItem("adm.pane") || ""; } catch (e) { /* ignore */ }
  if (panes[want]) show(want, true);
  else if (keep && panes[keep] && document.querySelector(".flash")) show(keep, true);
  else show("", true);
})();
