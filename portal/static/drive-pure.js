/* The Drive page's logic that needs no browser (portal/tests/js/drive.test.js runs it under Node): window.DrivePure.

     shuffle        a fair shuffle (Fisher-Yates) with the random source handed in
     buildQueue     the two Bilby Shuffles as one list of items {kind: "bilby", path} | {kind: "spotify", uri}:
                      discovery  his unplayed songs (never a favorite, liked, played or tired one), and Spotify's new songs inside his genres
                      library    his one favorites pool: favorites and Spotify liked songs, minus the ones he is tired of
     playable       what this browser can play itself (Bilby's files; a Spotify item needs a Spotify player)
     carDone        whether a Spotify row playing on the car's own Spotify is over
     handOff        what the car continues as it opens: the current book or Bilby, whichever he heard last
     replyAfter     the house brain's answer to the turn sent at `seq`, from the Home chat's events (portal/housechat.py)
     captionFrames  a caption's words as frames: the original, then each English word taking the place of the original ones, left to right
     captionHoldMs  how long the original is left to be read before the English starts replacing it
     heardNote      the context line that rides ahead of his next message to purple (the same text portal/chat.py heard_note makes)
     langName       a language's English name, from its code */
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

  /* Local first (the Captain, 2026-11-01: "prefer bilby use local library versions to spotify versions of songs"): each Spotify URI that is a
     song in his library (same first artist and title, folded like services/satellite/mixes.py _n and feat_off) becomes Bilby's path.
     nameOf(uri) -> {t, a} (its title and artists, from the mix), lib: Map path -> {t, a}. No Spotify call. */
  const fold = s => String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/\s*[([][^)\]]*(?:remaster|version|edit|mix|mono|stereo|live|deluxe)[^)\]]*[)\]]/g, "")
    .replace(/\s+-\s+.*(?:remaster|version|edit|live).*$/, "").replace(/[^a-z0-9]+/g, " ").trim().replace(/^the /, "");
  const featOff = t => String(t || "").replace(/\s*[([](?:feat\.?|ft\.?|featuring|with) [^)\]]*[)\]]/gi, "").replace(/\s+(?:feat\.?|ft\.?|featuring) .*$/i, "");
  const songKey = (title, artist) => fold(String(artist || "").split(", ")[0]) + "|" + fold(featOff(title));
  function localFirst(items, nameOf, lib) {
    const have = new Map();
    for (const [path, t] of lib || []) { const k = songKey(t.t, t.a); if (!have.has(k)) have.set(k, path); }
    return (items || []).map(s => {
      if (!/^spotify:/.test(s)) return s;
      const n = nameOf(s);
      return (n && have.get(songKey(n.t, n.a))) || s;
    });
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

  /* What the car plays the moment it opens (the Captain, 2026-11-01: "music should be bilby, or if I was listening to an audio book more
     recently, that is what it will play in the car"): the newer of the one current book (places.current, its last_played) and Bilby's last
     play (drive.py bilby_last). Bilby continues that song where it was, in the shuffle it belongs to (in his favorites pool: library, else
     discovery); with no record of either, his usual shuffle (library). Times are epoch seconds. */
  function handOff(places, bilby, inPool) {
    const id = places && places.current, p = id && (places.books || {})[id];
    const book = p && !p.finished ? { kind: "book", id, at: +p.last_played || 0, pos: +p.pos || 0 } : null;
    const b = bilby && bilby.track ? bilby : null;
    if (book && (!b || book.at >= (+b.at || 0))) return book;
    if (b) return { kind: "bilby", mode: inPool && inPool(b.track) ? "library" : "discovery", track: b.track, pos: +b.pos || 0, at: +b.at || 0,
                    title: b.title || "", artist: b.artist || "" };
    return { kind: "bilby", mode: "library", track: "", pos: 0, at: 0, title: "", artist: "" };
  }

  // ── captions: other people's speech (heard) and signs (seen) on the glasses (the Captain, 2026-11-02): "show the original, and then
  // show it being replaced with English token by token, as an animation so I have a moment to read the words before they become English" ──
  const CAPTION_STEP_MS = 110, CAPTION_FADE_MS = 20000;
  const words = s => String(s || "").split(/\s+/).filter(Boolean);
  function captionFrames(orig, en) {
    const o = words(orig), e = words(en);
    if (!e.length) return [{ en: "", orig: o.join(" ") }];
    if (!o.length) return [{ en: e.join(" "), orig: "" }];                    // no original to show: the English at once
    const frames = [{ en: "", orig: o.join(" ") }];
    for (let k = 1; k <= e.length; k++) {
      const gone = Math.round(k * o.length / e.length);                        // the original words the English has caught up with
      frames.push({ en: e.slice(0, k).join(" "), orig: o.slice(gone).join(" ") });
    }
    return frames;                                                           // the last frame is all English
  }
  const captionHoldMs = orig => Math.min(4000, 900 + 55 * String(orig || "").length);
  const HEARD_LANG = /^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$/;
  function heardNote(lines) {
    const last = (lines || []).filter(l => l && String(l.text || "").trim()).slice(-10);      // the last ten, each cut short
    if (!last.length) return "";
    return "[Heard near me while Hands-off listened, translated into English by Whisper (other people talking, not me):\n"
      + last.map(l => "- (" + (HEARD_LANG.test(String(l.lang || "")) ? l.lang : "another language") + ") " + String(l.text).trim().slice(0, 400)).join("\n")
      + "]\n\n";
  }
  // ── the strip's lamp: the phone's charge, and on the glasses' host how hot it is (the Captain: "watch heat and battery") ──
  // The host's thermal() answers {batteryC, thermal} with Android's PowerManager status: 0 none, 1 light, 2 moderate, 3 severe and up.
  const HOT_STATUS = 3, WARM_C = 42;
  function battLevel(pct, charging) {                                          // "ok" (green), "low" (amber), "crit" (red), or "" when unknown
    if (typeof pct !== "number" || !isFinite(pct)) return "";
    return charging || pct >= 40 ? "ok" : pct >= 15 ? "low" : "crit";
  }
  function heatLevel(status, tempC) {                                          // "hot" at SEVERE or worse, "warm" at moderate or a warm battery, else ""
    if ((+status || 0) >= HOT_STATUS) return "hot";
    return (+status || 0) === 2 || (typeof tempC === "number" && tempC >= WARM_C) ? "warm" : "";
  }
  function lampText(pct, charging, tempC, hosted) {
    return (typeof pct === "number" ? Math.round(pct) + (charging ? "+" : "") : "") + (hosted && typeof tempC === "number" ? " · " + Math.round(tempC) + "°" : "");
  }
  // ── captions pinned in the world (glasses only): a row stays where it was made while the head turns ──
  const FOV_DEG = 46.1, PIN_HIDE_DEG = 23;                                       // the glasses' view: 46.1 degrees across; beyond half of it a row is out of sight
  function yawDeg(pose) {                                                       // the head's turn about the vertical in degrees (Z up, as OpenViture's own quaternion-to-Euler); null without a pose
    if (!pose || ["qw", "qx", "qy", "qz"].some(k => typeof pose[k] !== "number")) return null;
    return Math.atan2(2 * (pose.qw * pose.qz + pose.qx * pose.qy), 1 - 2 * (pose.qy * pose.qy + pose.qz * pose.qz)) * 180 / Math.PI;
  }
  function pinOffset(yawNow, yawThen, viewportPx) {                              // {dx, show}: how far to shift a row made at yawThen so it stays put in the room
    if (typeof yawNow !== "number" || typeof yawThen !== "number") return { dx: 0, show: true };
    const d = ((yawNow - yawThen + 540) % 360 + 360) % 360 - 180;                // the short way round, -180..180 (positive: the head turned left)
    return { dx: d * viewportPx / FOV_DEG, show: Math.abs(d) <= PIN_HIDE_DEG };    // a left turn moves a fixed thing to the right of the view
  }
  function langName(code) { try { return new Intl.DisplayNames(["en"], { type: "language" }).of(code) || code; } catch (e) { return code; } }

  return { captionFrames, captionHoldMs, heardNote, langName, yawDeg, pinOffset, FOV_DEG, battLevel, heatLevel, lampText, HOT_STATUS, CAPTION_STEP_MS, CAPTION_FADE_MS, shuffle, buildQueue, localFirst, handOff, nextPlayable, playable, replyAfter, carDone, QUEUE_MAX, FAV_EVERY, SUGGEST_EVERY, CAR_MARGIN_MS };
});
