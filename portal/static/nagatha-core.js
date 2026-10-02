/* Nagatha's Video Service: the logic that needs no browser (portal/static/nagatha-app.js is the part that does).

   Small pure functions and classes, so the rules that matter (which way a film is played, where it resumes, when progress is
   reported, what a key does, how the library is searched, which picture size is asked for) run under Node
   (portal/tests/js/video/nagatha.test.js) without a page. The page loads this file first and finds it at window.NagathaCore.

   The catalog is the video gate's catalog.json (services/video-gate/README.md "Catalog item"):
     {"v":1, "items":[{id, type, title, sort, year, runtime, overview, genres, rating, official, added, collectionIds,
                       hasPoster, hasBackdrop, resume, played, play:{container, vcodec, acodec, height, direct, audio, subs}}],
      "collections":[{id, title, sort, itemIds, hasPoster, hasBackdrop}]}
   Fields only ever grow at the end, so anything missing is treated as absent, never as an error. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.NagathaCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ── text ──────────────────────────────────────────────────────────────────────────────────────────────────────
  const MARKS = /[̀-ͯ]/g;
  const SPECIAL = { "ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-" };
  const SPECIAL_RE = /[øæœßł‘’“”–—]/g;

  /** Case- and accent-insensitive: "Amélie" and "amelie" fold to the same thing. */
  function fold(s) {
    s = s == null ? "" : String(s).toLowerCase();
    if (/[^\x00-\x7f]/.test(s)) s = s.normalize("NFD").replace(MARKS, "").replace(SPECIAL_RE, c => SPECIAL[c] || c);
    return s;
  }
  /** Folded with punctuation squeezed out, so "Airplane!" / "airplane", "Mr. Smith" / "mr smith" find each other. */
  function squash(s) { return fold(s).replace(/[^\p{L}\p{N}]+/gu, " ").trim(); }

  // ── the catalog ───────────────────────────────────────────────────────────────────────────────────────────────
  const KINDS = ["movie", "series", "episode"];

  /** The gate's catalog, checked and indexed. A row the player can't use (no id, an unknown type) is dropped rather than
   *  breaking the wall; a series (a later TV library) is kept as a shelf with no `play`. */
  function parseCatalog(json) {
    if (!json || typeof json !== "object" || !Array.isArray(json.items)) throw new Error("not a video catalog");
    const items = [], byId = new Map();
    for (const raw of json.items) {
      if (!raw || typeof raw.id !== "string" || !/^[0-9a-f]{32}$/.test(raw.id) || byId.has(raw.id)) continue;
      const type = KINDS.includes(raw.type) ? raw.type : "movie";
      const it = {
        id: raw.id, type, title: String(raw.title || "Untitled"), sort: String(raw.sort || raw.title || "").toLowerCase(),
        year: raw.year | 0, runtime: Math.max(0, raw.runtime | 0), overview: String(raw.overview || ""),
        genres: Array.isArray(raw.genres) ? raw.genres.filter(g => typeof g === "string") : [],
        rating: typeof raw.rating === "number" && isFinite(raw.rating) ? raw.rating : null, official: String(raw.official || ""),
        added: raw.added | 0, collectionIds: Array.isArray(raw.collectionIds) ? raw.collectionIds.slice() : [],
        hasPoster: raw.hasPoster === true, hasBackdrop: raw.hasBackdrop === true,
        resume: Math.max(0, +raw.resume || 0), played: raw.played === true, play: normPlay(raw.play),
        seriesId: raw.seriesId || null, season: raw.season | 0, ep: raw.ep | 0,
      };
      it.key = squash(`${it.title} ${it.year || ""} ${it.genres.join(" ")}`);
      it.tkey = squash(it.title);
      items.push(it);
      byId.set(it.id, it);
    }
    items.sort((a, b) => cmp(a.sort, b.sort) || a.year - b.year);
    const collections = [], collById = new Map();
    for (const raw of Array.isArray(json.collections) ? json.collections : []) {
      if (!raw || typeof raw.id !== "string" || !/^[0-9a-f]{32}$/.test(raw.id)) continue;
      const ids = (Array.isArray(raw.itemIds) ? raw.itemIds : []).filter(id => byId.has(id));
      const c = { id: raw.id, title: String(raw.title || "Collection"), sort: String(raw.sort || raw.title || "").toLowerCase(),
                  itemIds: ids, hasPoster: raw.hasPoster === true, hasBackdrop: raw.hasBackdrop === true };
      c.key = squash(c.title);
      collections.push(c);
      collById.set(c.id, c);
    }
    collections.sort((a, b) => cmp(a.sort, b.sort));
    for (const c of collections) for (const id of c.itemIds) { const it = byId.get(id); if (!it.collectionIds.includes(c.id)) it.collectionIds.push(c.id); }
    return { items, byId, collections, collById, movies: items.filter(i => i.type === "movie") };
  }

  function normPlay(p) {
    if (!p || typeof p !== "object") return null;
    const audio = (Array.isArray(p.audio) ? p.audio : []).filter(a => a && Number.isInteger(a.i));
    const subs = (Array.isArray(p.subs) ? p.subs : []).filter(s => s && Number.isInteger(s.i));
    return { container: String(p.container || ""), vcodec: String(p.vcodec || ""), acodec: String(p.acodec || ""), height: p.height | 0,
             direct: p.direct === true, audio, subs };
  }

  function cmp(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

  /** Instant search over titles, years and genres. Every word must match somewhere; a title that starts with the query comes
   *  first, then a title with a word starting with it, then the rest (a genre or year hit), each group in the wall's order. */
  function search(lib, q, limit) {
    const want = squash(q);
    if (!want) return { items: [], collections: [] };
    const words = want.split(" ");
    const tiers = [[], [], []];
    for (const it of lib.items) {
      if (!words.every(w => it.key.includes(w))) continue;
      tiers[it.tkey.startsWith(want) ? 0 : (" " + it.tkey).includes(" " + words[0]) ? 1 : 2].push(it);
    }
    const items = tiers[0].concat(tiers[1], tiers[2]);
    const collections = lib.collections.filter(c => words.every(w => c.key.includes(w)));
    return { items: limit ? items.slice(0, limit) : items, collections };
  }

  // ── shelves ───────────────────────────────────────────────────────────────────────────────────────────────────
  /** Recently added: newest `added` first (the gate's epoch seconds), titles breaking ties so the order is stable. */
  function recentlyAdded(lib, n) {
    return lib.movies.filter(i => i.added > 0).sort((a, b) => b.added - a.added || cmp(a.sort, b.sort)).slice(0, n || 24);
  }

  /** Where this film should start: this browser's own last position when it was saved AFTER the catalog was fetched
   *  (`catalogAt`, ms: the browser knows this second, the catalog knows a few minutes ago), else Jellyfin's resume point, which
   *  already holds this browser's earlier reports and every other device's. Nothing under 30 s in (not worth resuming, and the
   *  gate stores those as 0) or in the last 5 % (that was the credits). */
  const RESUME_MIN = 30;
  function resumePoint(item, local, catalogAt) {
    if (!item) return 0;
    let pos = item.played ? 0 : item.resume;
    const newer = local && typeof local.pos === "number" && isFinite(local.pos) && !(catalogAt && local.at <= catalogAt);
    if (newer) pos = local.done ? 0 : local.pos;
    if (!(pos >= RESUME_MIN)) return 0;
    if (item.runtime > 0 && pos >= item.runtime * 0.95) return 0;
    return Math.floor(pos);
  }

  /** Continue watching: films part-way through, the ones this browser touched most recently first (its own record of when),
   *  then the rest of the gate's resume points by title. `local` is {id: {pos, at, done}}. */
  function continueWatching(lib, local, catalogAt) {
    local = local || {};
    const out = [];
    for (const it of lib.items) {
      if (!it.play || !resumePoint(it, local[it.id], catalogAt)) continue;
      out.push(it);
    }
    const at = it => (local[it.id] && local[it.id].at) || 0;
    return out.sort((a, b) => at(b) - at(a) || cmp(a.sort, b.sort));
  }

  // ── how a film is played ──────────────────────────────────────────────────────────────────────────────────────
  /** Which HLS engine this browser has. Safari (and every iOS browser, which is Safari underneath) plays HLS itself and that is
   *  the better path there (hardware decoding, AirPlay, the lock screen); everywhere else hls.js over Media Source Extensions,
   *  even where canPlayType says "maybe", because a desktop Chrome that claims native HLS has been unreliable with it. */
  function hlsEngine(env) {
    env = env || {};
    if (env.native && (env.webkit || !env.mse)) return "native";
    if (env.mse) return "hlsjs";
    return env.native ? "native" : "none";
  }

  /** Direct play (the original file, no work for skippy-pc) when the gate says every browser plays it as it is AND the viewer
   *  wants the first audio track (a browser can't switch the audio of a plain MP4); otherwise the gate's transcoded HLS. */
  function chooseSource(item, opts) {
    opts = opts || {};
    const play = item && item.play;
    if (!play) return { mode: "none", why: "This title can't be played: Jellyfin has no video file for it." };
    const firstAudio = play.audio.length ? play.audio[0].i : null;
    const wantsOther = opts.audio != null && opts.audio !== firstAudio;
    if (play.direct && !wantsOther && !opts.forceHls) return { mode: "direct" };
    const engine = hlsEngine(opts.env);
    if (engine === "none") return { mode: "none", why: "This film needs converting on skippy-pc first, and this browser can't play the converted stream." };
    return { mode: "hls", engine };
  }

  // ── gate URLs (the pass is in the path; the gate is another origin, so never a cookie) ─────────────────────────
  const IMG_WIDTHS = [160, 320, 640, 1280];
  /** The gate's nearest picture size that is still sharp for `cssPx` CSS pixels on this screen. */
  function imgWidth(cssPx, dpr) {
    const need = Math.max(1, cssPx) * Math.min(Math.max(dpr || 1, 1), 3);
    for (const w of IMG_WIDTHS) if (w >= need) return w;
    return IMG_WIDTHS[IMG_WIDTHS.length - 1];
  }
  function base(g) { return `${g.url}/t/${g.pass}`; }
  function catalogUrl(g) { return `${base(g)}/catalog.json`; }
  function imgUrl(g, id, kind, w) { return `${base(g)}/img/${id}/${kind}?w=${w}`; }
  function directUrl(g, id) { return `${base(g)}/v/${id}/direct`; }
  function subUrl(g, id, index) { return `${base(g)}/sub/${id}/${index | 0}.vtt`; }
  function progressUrl(g) { return `${base(g)}/progress`; }
  /** The transcode's master playlist. `start` only when the gate can take it (0 < start <= runtime: an unprobed film has a
   *  runtime of 0 and would be refused). The player seeks there as well; the playlist's START tag is a hint. */
  function hlsUrl(g, item, opts) {
    opts = opts || {};
    const q = [];
    if (opts.audio != null && item.play && item.play.audio.some(a => a.i === opts.audio)) q.push(`audio=${opts.audio}`);
    const start = Math.floor(opts.start || 0);
    if (start > 0 && item.runtime > 0 && start <= item.runtime) q.push(`start=${start}`);
    return `${base(g)}/v/${item.id}/hls/master.m3u8${q.length ? "?" + q.join("&") : ""}`;
  }

  /** A pass good for at least `margin` more seconds (half an hour by default): a film shouldn't start on a pass that dies mid-way. */
  function passFresh(info, nowSec, margin) {
    return !!(info && info.ok && info.pass && info.url) && info.exp - nowSec > (margin == null ? 1800 : margin);
  }
  /** When to ask the portal for a new pass: half an hour before this one runs out, but never sooner than a minute from now. */
  function refreshIn(info, nowSec) {
    if (!info || !info.exp) return 60;
    return Math.max(60, info.exp - nowSec - 1800);
  }

  // ── progress (Continue Watching in Jellyfin, and here) ────────────────────────────────────────────────────────
  /** What to tell the gate, and when: "start" when a film begins, "progress" every `every` seconds of wall time while it plays
   *  and at once on a pause or a seek, "stop" when it is closed or ends. Each returns the body to POST, or null for nothing.
   *  The gate throttles to one write per 5 s per film anyway; this keeps a playing film to one request per 10 s. */
  class Progress {
    constructor(clock, every) { this.clock = clock || (() => Date.now() / 1000); this.every = every || 10; this.id = null; this.last = 0; this.lastPos = -1; this.stopped = true; }
    _body(state, pos, extra) {
      const p = Math.max(0, Math.round((+pos || 0) * 10) / 10);
      this.last = this.clock();
      this.lastPos = p;
      return Object.assign({ id: this.id, state, pos: p }, extra || {});
    }
    start(id, pos) {
      this.id = id; this.stopped = false; this.last = this.clock(); this.lastPos = -1;
      return { id, state: "start", pos: Math.max(0, Math.round((+pos || 0) * 10) / 10) };
    }
    /** Called on every timeupdate (several times a second). */
    tick(pos, playing) {
      if (this.stopped || !this.id || !playing) return null;
      if (this.clock() - this.last < this.every) return null;
      return this._body("progress", pos);
    }
    /** A pause or a finished seek: worth saving now (the tab may be closed next), unless nothing moved. */
    pause(pos) {
      if (this.stopped || !this.id) return null;
      if (Math.abs((+pos || 0) - this.lastPos) < 1) return null;
      return this._body("progress", pos);
    }
    /** The film is closed (or the tab is going): the last word. `ended` marks it watched. */
    stop(pos, ended) {
      if (this.stopped || !this.id) return null;
      this.stopped = true;
      return this._body("stop", pos, ended ? { played: true } : null);
    }
  }

  /** This browser's own record of where each film was left: {id: {pos, at, done}}, the newest 200 kept. */
  function remember(local, id, pos, nowMs, done) {
    const out = Object.assign({}, local || {});
    out[id] = { pos: Math.max(0, Math.round((+pos || 0) * 10) / 10), at: nowMs, done: !!done };
    const ids = Object.keys(out);
    if (ids.length > 200) ids.sort((a, b) => out[a].at - out[b].at).slice(0, ids.length - 200).forEach(k => delete out[k]);
    return out;
  }

  // ── the keyboard ──────────────────────────────────────────────────────────────────────────────────────────────
  /** The action for a key press, or null. Typing in a field is never hijacked, and a key with Ctrl/Cmd/Alt belongs to the
   *  browser. `inPlayer` is true while a film is open: the player's keys work only then; "/" and "?" work everywhere. */
  function keyAction(e, inPlayer) {
    if (!e || e.ctrlKey || e.metaKey || e.altKey) return null;
    const tag = (e.targetTag || "").toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.editable) return null;
    const k = e.key;
    if (k === "/") return "search";
    if (k === "?") return "help";
    if (!inPlayer) return null;
    if (k === " " || k === "Spacebar" || k === "k" || k === "K") return e.onButton ? null : "toggle";
    if (k === "ArrowLeft") return e.shiftKey ? "back30" : "back10";
    if (k === "ArrowRight") return e.shiftKey ? "fwd30" : "fwd10";
    if (k === "ArrowUp") return "volUp";
    if (k === "ArrowDown") return "volDown";
    if (k === "f" || k === "F") return "fullscreen";
    if (k === "m" || k === "M") return "mute";
    if (k === "c" || k === "C") return "subs";
    if (k === "p" || k === "P") return "pip";
    if (k === "Home" || k === "0") return "restart";
    if (k === "Escape") return "close";
    return null;
  }

  /** The seek a key or a button asks for, kept inside the film. */
  function seekTo(t, by, duration) {
    const end = isFinite(duration) && duration > 0 ? Math.max(0, duration - 0.5) : Infinity;
    return Math.min(Math.max(0, (+t || 0) + by), end);
  }

  /** Subtitles on/off with the "c" key: off goes to the first track, on goes off. Returns the index to show or -1. */
  function nextSub(current, count) {
    if (!count) return -1;
    return current >= 0 ? -1 : 0;
  }

  // ── the browser's autoplay rule (the same as the music player's) ───────────────────────────────────────────────
  /** A browser plays sound only after a tap on THIS page load. A film started by the Play button has its tap; a film that comes
   *  back after a reload (the address remembers it) does not, so the page shows one big "Tap to continue" instead of a
   *  play() that silently fails. This only tracks what happened: armed = a play() with sound has worked since the load. */
  class Armed {
    constructor(activation) { this.activation = activation || (() => undefined); this.proven = false; this.denied = false; this.tap = false; }
    get armed() { return this.proven || (!this.denied && this.activation() === true); }
    played(audible) { if (audible === false) return; this.proven = true; this.denied = false; this.tap = false; }
    refused() { this.proven = false; this.denied = true; this.tap = true; }
    waiting() { this.tap = true; }
  }

  // ── words ─────────────────────────────────────────────────────────────────────────────────────────────────────
  function fmtTime(s) {
    s = Math.max(0, Math.floor(+s || 0));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  }
  function fmtRuntime(s) {
    const m = Math.round((+s || 0) / 60);
    if (!m) return "";
    return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60 ? (m % 60) + " min" : ""}`.trim() : `${m} min`;
  }
  const LANGS = { eng: "English", en: "English", fra: "French", fre: "French", fr: "French", spa: "Spanish", es: "Spanish", deu: "German",
                  ger: "German", de: "German", ita: "Italian", it: "Italian", jpn: "Japanese", ja: "Japanese", por: "Portuguese", pt: "Portuguese",
                  rus: "Russian", ru: "Russian", zho: "Chinese", chi: "Chinese", zh: "Chinese", kor: "Korean", ko: "Korean", nld: "Dutch",
                  dut: "Dutch", swe: "Swedish", dan: "Danish", nor: "Norwegian", fin: "Finnish", pol: "Polish", hun: "Hungarian", ces: "Czech",
                  cze: "Czech", tur: "Turkish", ara: "Arabic", heb: "Hebrew", hin: "Hindi", tha: "Thai", ell: "Greek", gre: "Greek" };
  function langName(code) {
    const c = fold(code || "").trim();
    if (!c || c === "und") return "";
    return LANGS[c] || c.toUpperCase();
  }
  const CHANNELS = { 1: "mono", 2: "stereo", 6: "5.1", 8: "7.1" };
  /** "English · 5.1 AAC", numbered when two tracks would read the same (13 Ghosts has two English tracks). */
  function audioLabels(audio) {
    const base = audio.map(a => [langName(a.lang) || "Unknown language", CHANNELS[a.ch] || (a.ch ? a.ch + " ch" : ""), (a.codec || "").toUpperCase()]
      .filter(Boolean).join(" · "));
    return base.map((l, i) => base.indexOf(l) !== base.lastIndexOf(l) ? `${l} (${base.slice(0, i + 1).filter(x => x === l).length})` : l);
  }
  function subLabel(s) { return langName(s.lang) || "Subtitles"; }

  /** One line of facts under a title: "1960 · 1 h 31 min · NR · ★ 5.8". */
  function factsLine(it) {
    return [it.year || "", fmtRuntime(it.runtime), it.official, it.rating != null ? `★ ${it.rating.toFixed(1)}` : ""].filter(Boolean).join(" · ");
  }

  /** What a failed play means, in a sentence (the gate's status, or the media element's error code). */
  function playProblem(status, mode) {
    if (status === 429) return mode === "hls" ? "skippy-pc is already converting two films. Try again when one is finished." : "Too many streams are open. Try again in a moment.";
    if (status === 409) return "This film can't be played as it is; switching to the converted stream.";
    if (status === 404) return "This film isn't in the library any more.";
    if (status === 503 || status === 502) return "Jellyfin on skippy-pc isn't answering.";
    if (status === 0) return "skippy-pc isn't reachable from here: Tailscale Funnel may be off, or the PC is asleep.";
    return "The film couldn't be played.";
  }

  return {
    fold, squash, cmp, parseCatalog, search, recentlyAdded, continueWatching, resumePoint, RESUME_MIN,
    hlsEngine, chooseSource, imgWidth, IMG_WIDTHS, catalogUrl, imgUrl, directUrl, subUrl, progressUrl, hlsUrl,
    passFresh, refreshIn, Progress, remember, keyAction, seekTo, nextSub, Armed,
    fmtTime, fmtRuntime, langName, audioLabels, subLabel, factsLine, playProblem,
  };
});
