/* The Drive page's logic that needs no browser (portal/tests/js/drive.test.js runs it under Node): window.DrivePure.

     shuffle        a fair shuffle (Fisher-Yates) with the random source handed in
     buildQueue     the two Bilby Shuffles as one list of items {kind: "bilby", path} | {kind: "spotify", uri}:
                      discovery  his unplayed songs (never a favorite, liked, played or tired one), and Spotify's new songs inside his genres
                      library    his one favorites pool: favorites and Spotify liked songs, minus the ones he is tired of
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
  const SUGGEST_EVERY = 4;           // discovery: one of Spotify's new songs in about every four
  const FAV_EVERY = SUGGEST_EVERY;   // the old name (discovery no longer mixes favorites in: 2026-11-01)

  function shuffle(arr, rnd) {
    const a = arr.slice();
    rnd = rnd || Math.random;
    for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); const t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }

  const item = s => (/^spotify:/.test(s) ? { kind: "spotify", uri: s } : { kind: "bilby", path: s });
  const key = it => it.kind === "spotify" ? it.uri : it.path;

  /* The two Bilby Shuffles, the same mixes Skippy at home plays (services/satellite/mixes.py build), the Captain's words of 2026-11-01:
       library    (the house's "favorites"): his one favorites pool, his favorites and his Spotify liked songs, minus what he is tired of
       discovery  what he hasn't heard: his collection's unplayed songs (never his favorites, liked, played or tired ones), and one of
                  Spotify's new songs in about every SUGGEST_EVERY, kept inside his normal genres (the genres of his pool)
     src: {all: [library paths], favorites: [paths or URIs, newest first], liked: [URIs], suggested: [URIs], played: [paths or URIs],
           tired: [paths or URIs], genre: (path or URI) -> its genre tag, or "" when unknown (optional)} */
  function buildQueue(mode, src, rnd, max) {
    rnd = rnd || Math.random; max = max || QUEUE_MAX;
    src = src || {};
    const tired = new Set(src.tired || []);
    const favs = (src.favorites || []).filter(x => x && !tired.has(x));
    const liked = (src.liked || []).filter(x => x && !tired.has(x));
    const seen = new Set(), out = [];
    const add = it => { const k = key(it); if (!k || seen.has(k)) return false; seen.add(k); out.push(it); return true; };
    if (mode === "library") {
      for (const s of shuffle(favs.concat(liked), rnd)) { if (out.length >= max) break; add(item(s)); }
      return out;
    }
    if (mode !== "discovery") return out;
    const skip = new Set([...favs, ...liked, ...(src.played || []), ...tired]);
    const genre = typeof src.genre === "function" ? src.genre : () => "";
    const bounds = new Set(favs.concat(liked).map(genre).filter(Boolean));
    const inside = s => { const g = genre(s); return !g || !bounds.size || bounds.has(g); };
    const rest = shuffle((src.all || []).filter(s => !skip.has(s)), rnd).map(item);
    const sugg = shuffle((src.suggested || []).filter(s => !skip.has(s) && inside(s)), rnd).map(item);
    let r = 0, g = 0;
    while (out.length < max && (r < rest.length || g < sugg.length)) {
      const spotTurn = g < sugg.length && (r >= rest.length || (out.length + 1) % SUGGEST_EVERY === 0);
      if (spotTurn) add(sugg[g++]); else add(rest[r++]);
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

  return { shuffle, buildQueue, nextPlayable, playable, replyAfter, carDone, QUEUE_MAX, FAV_EVERY, SUGGEST_EVERY, CAR_MARGIN_MS };
});
