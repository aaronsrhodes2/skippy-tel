/* The Drive page's logic that needs no browser (portal/tests/js/drive.test.js runs it under Node): window.DrivePure.

     shuffle        a fair shuffle (Fisher-Yates) with the random source handed in
     buildQueue     the two Bilby Shuffles as one list of items {kind: "bilby", path} | {kind: "spotify", uri}:
                      discovery  every song of Bilby's, his favorites mixed in about one in four, and Spotify's suggestions (from his liked songs)
                      library    his favorites and his Spotify liked songs, nothing else
     playable       what this browser can play itself (Bilby's files; a Spotify item needs a Spotify player)
     carDone        whether a Spotify row playing on the car's own Spotify is over
     replyAfter     the house brain's answer to the turn sent at `seq`, from the Home chat's events (portal/housechat.py) */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.DrivePure = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const QUEUE_MAX = 400;
  const FAV_EVERY = 4;               // discovery: one favorite in about every four songs

  function shuffle(arr, rnd) {
    const a = arr.slice();
    rnd = rnd || Math.random;
    for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); const t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }

  const item = s => (/^spotify:/.test(s) ? { kind: "spotify", uri: s } : { kind: "bilby", path: s });
  const key = it => it.kind === "spotify" ? it.uri : it.path;

  /* src: {all: [library paths], favorites: [paths or spotify URIs, newest first], liked: [spotify URIs], suggested: [spotify URIs]} */
  function buildQueue(mode, src, rnd, max) {
    rnd = rnd || Math.random; max = max || QUEUE_MAX;
    src = src || {};
    const favs = shuffle((src.favorites || []).filter(Boolean), rnd).map(item);
    const seen = new Set(), out = [];
    const add = it => { const k = key(it); if (!k || seen.has(k)) return false; seen.add(k); out.push(it); return true; };
    if (mode === "library") {
      const pool = shuffle(favs.concat((src.liked || []).map(item)), rnd);
      for (const it of pool) { if (out.length >= max) break; add(it); }
      return out;
    }
    if (mode !== "discovery") return out;
    const rest = shuffle((src.all || []).map(item).concat((src.suggested || []).map(item)), rnd);
    let f = 0, r = 0;
    while (out.length < max && (f < favs.length || r < rest.length)) {
      const favTurn = f < favs.length && (r >= rest.length || (out.length + 1) % FAV_EVERY === 0);
      if (favTurn) add(favs[f++]); else add(rest[r++]);
    }
    return out;
  }

  /* The next item from `at` this browser can play, or -1. */
  function nextPlayable(queue, at, canSpotify) {
    for (let i = Math.max(0, at); i < queue.length; i++) if (playable(queue[i], canSpotify)) return i;
    return -1;
  }
  function playable(it, canSpotify) { return !!it && (it.kind === "bilby" || (it.kind === "spotify" && !!canSpotify)); }

  /* events: the Home chat's events since before the turn; seq: the send's own seq (the "heard" line). The first "said" after it, typed. */
  function replyAfter(events, seq) {
    for (const e of events || []) {
      if (!e || e.seq <= seq) continue;
      if (e.kind === "said" && e.typed) return { text: e.text || "" };
      if (e.kind === "note" && /not answered/i.test(e.text || "")) return { text: "", why: e.text };
    }
    return null;
  }

  /* A Spotify row playing on the car's own Spotify: is it over? The car plays the track as its album from that track, so the next album track
     starting (another uri) is the end; so is the car's Spotify stopping on its own after it had started, and, when the car says nothing,
     the track's length plus a margin. st: services/spotify's /car answer, or null. */
  const CAR_MARGIN_MS = 8000;
  function carDone(st, row, now) {
    if (st && st.uri && st.uri !== row.uri && now - row.at > 3000) return true;
    if (st && st.stopped && row.seen) return true;
    return !!row.ms && now - row.at - (row.pausedMs || 0) > row.ms - (row.from || 0) + CAR_MARGIN_MS;
  }

  return { shuffle, buildQueue, nextPlayable, playable, replyAfter, carDone, QUEUE_MAX, FAV_EVERY, CAR_MARGIN_MS };
});
