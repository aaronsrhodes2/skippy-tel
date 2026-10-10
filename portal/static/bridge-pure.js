/* The bridge's pure helpers: what every page that lists things or plays things over a phone's network needs, with no browser in it
   (so portal/tests/js/bridge.test.js runs it under Node). Loaded before bridge.js on every bridge page: window.BridgePure.

     paginate / pageOf   cut a long list into screens (nothing on the bridge scrolls)
     recoverDelay / stalled   losing the signal: how long to wait before the next try, and when a stalled stream is a dead line */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.BridgePure = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /** Cut rows of the given heights into screens `avail` px tall. A row never splits (a row taller than a screen gets one to itself),
   *  and a row marked in `glue` (a heading) is never left alone at the bottom of a screen: it goes over with the row after it.
   *  Returns the index of the first row of each screen. */
  function paginate(heights, avail, glue) {
    const starts = [0];
    let y = 0;
    for (let i = 0; i < heights.length; i++) {
      const h = heights[i];
      if (y > 0 && y + h > avail) {
        let s = i;
        while (glue && s > starts[starts.length - 1] + 1 && glue[s - 1]) s--;
        starts.push(s);
        y = 0;
        for (let k = s; k < i; k++) y += heights[k];
      }
      y += h;
    }
    return starts;
  }
  /** Which screen holds row `i`, given where each screen starts. */
  function pageOf(starts, i) {
    let lo = 0, hi = starts.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= i) lo = mid; else hi = mid - 1; }
    return lo;
  }

  /** How long to wait before retry number n (0-based) after the connection drops: quick at first, then every 30 s, for as long as it takes. */
  const RECOVER_DELAYS = [2000, 4000, 8000, 15000, 30000];
  function recoverDelay(n) { return RECOVER_DELAYS[Math.min(Math.max(0, n | 0), RECOVER_DELAYS.length - 1)]; }

  /** Has playback stopped moving for long enough to call it a dead line? (It said "waiting" and no time has passed since.) */
  const STALL_MS = 8000;
  function stalled(now, lastProgressAt, waiting) { return !!waiting && lastProgressAt > 0 && now - lastProgressAt >= STALL_MS; }

  return { paginate, pageOf, RECOVER_DELAYS, recoverDelay, STALL_MS, stalled };
});
