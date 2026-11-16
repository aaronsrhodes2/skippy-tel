/* Audio session coordination: all browser pages share one session on flask-api.
   When a new source starts, the old one is stopped. Each page reports its state
   and polls for takeover.

   Usage (any page):
     AudioSession.init({ source: "music" | "audiobook" | "drive", ... })
       → starts reporting and polling
     AudioSession.reportPlay(state)
       → POSTs current playback state to flask-api
     AudioSession.reportStop()
       → tells flask-api this page stopped

   The music page hooks this into its Connect system (bilby-app.js).
   The audiobooks and drive pages call it directly. */
window.AudioSession = (function () {
  "use strict";
  var S = { source: null, active: false, timer: 0, lastPoll: 0, lastState: null, onTakeover: null };
  var API = "/api";  // goes through the portal's proxy

  /** Start the session coordinator. Call once per page load. */
  function init(opts) {
    S.source = opts.source;
    S.onTakeover = opts.onTakeover || function () { stopPlayback(); };
    S.active = true;
    S.lastPoll = Date.now();
    clearInterval(S.timer);
    S.timer = setInterval(pollSession, 8000);  // poll every 8s
  }

  /** Report current playback state. Called when playback starts or changes. */
  function reportPlay(state) {
    if (!S.active || !S.source) return;
    state = state || {};
    state.source = S.source;
    state.action = "start";
    S.lastState = state;
    fetch(API + "/audio/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state)
    }).catch(function () { /* offline: next poll catches up */ });
  }

  /** Report that this page stopped playback. */
  function reportStop() {
    if (!S.active) return;
    S.lastState = null;
    fetch(API + "/audio/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: S.source, action: "stop" })
    }).catch(function () { /* ignore */ });
  }

  /** Report pause (session stays active, just paused). */
  function reportPause() {
    if (!S.active) return;
    fetch(API + "/audio/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: S.source, action: "pause" })
    }).catch(function () { /* ignore */ });
  }

  /** Report resume (session was paused, now playing again). */
  function reportResume() {
    if (!S.active) return;
    fetch(API + "/audio/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: S.source, action: "resume" })
    }).catch(function () { /* ignore */ });
  }

  /** Poll flask-api for session changes. If another source took over, call onTakeover. */
  function pollSession() {
    if (!S.active) return;
    fetch(API + "/audio/session", {
      method: "GET",
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    }).then(function (r) { return r.json(); }).then(function (data) {
      var sess = data.envelope ? data.envelope : data;
      if (!sess || !sess.source) return;
      // Another source took over?
      if (sess.source !== S.source && sess.status !== "stopped") {
        S.lastPoll = Date.now();
        S.onTakeover(sess.source);
        return;
      }
      // Same source, but status changed?
      if (S.lastState && sess.status === "stopped" && sess.source === S.source) {
        S.lastPoll = Date.now();
        S.onTakeover("stopped");
      }
      S.lastPoll = Date.now();
    }).catch(function () { /* offline: ignore */ });
  }

  /** Stop polling (called on page unload or when source changes). */
  function destroy() {
    S.active = false;
    clearInterval(S.timer);
    reportStop();
  }

  return { init: init, reportPlay: reportPlay, reportStop: reportStop,
           reportPause: reportPause, reportResume: reportResume, destroy: destroy };
})();
