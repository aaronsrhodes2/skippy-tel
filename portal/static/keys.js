/* Keys that say what they are doing (loaded by base.html on every page; the look is at the end of bridge.css).
 *
 *  SkippyKeys.busy(el, work): a CLICKER whose job takes a moment (a shuffle loading the library, a favorite being saved) carries
 *    data-busy while `work` (a promise, or a function returning one) runs, and at least BUSY_MIN_MS so a quick one is still seen.
 *    Resolves with what the work resolved with; rejects as it did.
 *  The lamps: a unit's lamp (bridge.css, data-s) gets its meaning in the unit's tooltip, so a hover or a long press says it.
 */
(function (root) {
  "use strict";
  const BUSY_MIN_MS = 450;
  const LAMP = {                                  // docs/portal/README.md → The lamps says the same
    on: "running",
    idle: "standing by",
    boot: "starting or working",
    warn: "something is wrong",
    off: "off or out of reach",
  };

  async function busy(el, work) {
    if (!el) return typeof work === "function" ? work() : work;
    const t0 = Date.now();
    el.dataset.busy = "1";
    if (el.setAttribute) el.setAttribute("aria-busy", "true");
    try { return await (typeof work === "function" ? work() : work); }
    finally {
      const left = BUSY_MIN_MS - (Date.now() - t0);
      const done = function () { delete el.dataset.busy; if (el.removeAttribute) el.removeAttribute("aria-busy"); };
      if (left > 0) setTimeout(done, left); else done();
    }
  }

  function lampTitle(unit) {
    if (!unit || !unit.dataset) return;
    if (unit.dataset.titleBase === undefined) unit.dataset.titleBase = unit.getAttribute("title") || "";
    const base = unit.dataset.titleBase, s = unit.dataset.s;
    const said = s && LAMP[s] ? "lamp: " + LAMP[s] : "";
    const title = [base, said].filter(Boolean).join(" · ");
    if (title) unit.setAttribute("title", title); else unit.removeAttribute("title");
  }

  function watchLamps() {
    if (!root.document || !root.MutationObserver) return;
    root.document.querySelectorAll(".unit").forEach(lampTitle);
    new root.MutationObserver(function (changes) {
      for (const c of changes) {
        if (c.type === "attributes" && c.target.classList && c.target.classList.contains("unit")) lampTitle(c.target);
        else if (c.type === "childList") c.addedNodes.forEach(function (n) {
          if (n.nodeType !== 1) return;
          if (n.classList.contains("unit")) lampTitle(n);
          n.querySelectorAll && n.querySelectorAll(".unit").forEach(lampTitle);
        });
      }
    }).observe(root.document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-s"] });
  }

  root.SkippyKeys = { busy: busy, LAMP: LAMP, BUSY_MIN_MS: BUSY_MIN_MS };
  if (root.document) {
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", watchLamps);
    else watchLamps();
  }
})(typeof window !== "undefined" ? window : globalThis);
