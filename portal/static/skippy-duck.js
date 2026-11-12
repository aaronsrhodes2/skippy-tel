/* Ducking: Skippy's voice turns the music DOWN while he listens or speaks, never off (the Captain, 2026-10-31: in the car he must hear
 * music and talk to Skippy at once, the way a pod ducks the stereo).
 *
 *   voice side (skippy-voice.js):  SkippyDuck.announce("speak" | "listen" | null)
 *   media side (Bilby, Audiobooks, the Drive page):
 *       SkippyDuck.follow({ level(f) { el.volume = base * f }, paused() { return el.paused }, resume() { el.play() } })
 *
 * The state goes to this page (a window event) and to every other tab of the portal on this device (a BroadcastChannel), because the chat
 * and the music are often two tabs. A media player that is PAUSED by the system while a duck is on (a browser or a car that hands the sound
 * to whichever page spoke last) was not paused by its listener: when the duck ends it plays again by itself. The pure part (LEVELS, gain,
 * createFollower) is exported for portal/tests/js/duck.test.js.
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkippyDuck = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";
  const LEVELS = { speak: 0.18, listen: 0.4 };          // a fraction of the player's own volume (an element's volume is linear: 0.18 is about -15 dB)
  const CHANNEL = "skippy-duck", EVENT = "skippy-duck";
  const STALE_MS = 90000;                                // a tab that ducked and then died never leaves the music down for good
  const RAMP_MS = 300;

  function gain(state) { return Object.prototype.hasOwnProperty.call(LEVELS, state) ? LEVELS[state] : 1; }

  /* The media side's state machine, with no browser in it: feed it duck states and pause events, it says what to do. */
  function createFollower(o) {
    const now = o.now || function () { return Date.now(); };
    let state = null, since = 0, systemPaused = false;
    return {
      get state() { return state; },
      set: function (s) {                                // a duck state arrived: returns the gain to apply and whether to resume
        const was = state;
        state = Object.prototype.hasOwnProperty.call(LEVELS, s) ? s : null;
        since = now();
        let resume = false;
        if (!state && was && systemPaused) { resume = true; systemPaused = false; }
        if (!was && state) systemPaused = false;
        return { gain: gain(state), resume: resume };
      },
      paused: function (ours) {                          // the element paused: `ours` = the player asked for it
        if (ours || !state) { systemPaused = false; return; }
        systemPaused = true;                             // taken by the system while Skippy had the floor: given back when he is done
      },
      stale: function () { return !!state && now() - since > STALE_MS; },
    };
  }

  let chan = null;
  function channel() {
    if (chan || !root.BroadcastChannel) return chan;
    try { chan = new BroadcastChannel(CHANNEL); } catch (e) { chan = null; }
    return chan;
  }
  let last = null;
  function announce(state) {
    state = Object.prototype.hasOwnProperty.call(LEVELS, state) ? state : null;
    if (state === last) return;
    last = state;
    const msg = { state: state, at: Date.now() };
    try { root.dispatchEvent(new CustomEvent(EVENT, { detail: msg })); } catch (e) { /* no DOM events */ }
    const c = channel(); if (c) { try { c.postMessage(msg); } catch (e) { /* closed */ } }
  }

  /* p: { level(f), paused(), resume(), ours?() } — ours() says the last pause was the player's own (its pause key). */
  function follow(p) {
    const f = createFollower({});
    let cur = 1, target = 1, timer = 0;
    function ramp() {                                    // a short fade, so the duck is not a click
      clearInterval(timer);
      const from = cur, t0 = Date.now();
      timer = setInterval(function () {
        const k = Math.min(1, (Date.now() - t0) / RAMP_MS);
        cur = from + (target - from) * k;
        try { p.level(cur); } catch (e) { /* the player is gone */ }
        if (k >= 1) clearInterval(timer);
      }, 30);
    }
    function on(msg) {
      const r = f.set(msg && msg.state);
      target = r.gain; ramp();
      if (r.resume && p.paused() && p.resume) { try { p.resume(); } catch (e) { /* the browser refused: the player's own tap bar says so */ } }
    }
    root.addEventListener && root.addEventListener(EVENT, function (e) { on(e.detail); });
    const c = channel(); if (c) c.addEventListener("message", function (e) { on(e.data); });
    setInterval(function () { if (f.stale()) on({ state: null }); }, 15000);
    return {
      gain: function () { return cur; },
      paused: function (ours) { f.paused(ours); },
    };
  }

  return { LEVELS: LEVELS, gain: gain, createFollower: createFollower, announce: announce, follow: follow, STALE_MS: STALE_MS };
});
