/* Bilby's Music Service: the player's logic that needs no browser (portal/static/bilby-app.js is the part that does).

   Everything here is a small pure function or class, so the rules that matter (what Bilby DJ picks next, how the queue
   moves, when the browser needs a tap before it will make sound, how a 21,000-track catalog is searched) can be run
   and tested under Node (portal/tests/js/core.test.js) without a page. The page loads this file first and finds it at
   window.BilbyCore.

   The catalog is the music gate's catalog.json: {"fields": [...], "tracks": [[...], ...]}. Fields the gate may not have
   yet (y = year, m = date added) are optional: a feature that needs one hides when it is missing. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.BilbyCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ── text: what search, sorting and the A-Z rail see ───────────────────────────────────────────────────────────
  const NON_ASCII = /[^\x00-\x7f]/;
  const MARKS = /[̀-ͯ]/g;
  const SPECIAL = { "ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "đ": "d", "ł": "l", "þ": "th", "ð": "d", "ı": "i",
                    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-" };
  const SPECIAL_RE = /[øæœßđłþðı‘’“”–—]/g;

  /** Case- and accent-insensitive form of a string: "Sigur Rós" and "sigur ros" fold to the same thing. The ASCII
   *  case (nearly the whole library) skips the Unicode work, which is what keeps indexing 21,000 tracks quick on a phone. */
  function fold(s) {
    s = s == null ? "" : String(s);
    s = s.toLowerCase();
    if (NON_ASCII.test(s)) s = s.normalize("NFD").replace(MARKS, "").replace(SPECIAL_RE, c => SPECIAL[c] || c);
    return s;
  }

  /** Folded with everything but letters and digits removed, so "AC/DC", "ac dc" and "acdc" all find each other. */
  function squash(folded) {
    return folded.replace(/[^\p{L}\p{N}]+/gu, "");
  }

  /** The key a name sorts by: folded, a leading "The "/"A "/"An " dropped (The Beatles files under B), punctuation ignored. */
  function sortKey(name) {
    let k = fold(name).trim();
    const m = /^(the|a|an)\s+(.+)$/.exec(k);
    if (m) k = m[2];
    return k.replace(/^[^\p{L}\p{N}]+/u, "") || k;
  }

  /** The A-Z rail letter for a sort key: "A".."Z", or "#" for digits and everything else. */
  function letterOf(key) {
    const c = (key || "").charAt(0).toUpperCase();
    return c >= "A" && c <= "Z" ? c : "#";
  }

  const collator = (typeof Intl !== "undefined" && Intl.Collator) ? new Intl.Collator(undefined, { numeric: true, sensitivity: "base" }) : null;
  const cmp = (a, b) => (collator ? collator.compare(a, b) : (a < b ? -1 : a > b ? 1 : 0));

  // ── time ──────────────────────────────────────────────────────────────────────────────────────────────────────
  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(Number.isFinite(sec) ? sec : 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  }

  /** "3 hr 12 min", "42 min", "1 min": how long an album or a queue runs. */
  function fmtLong(sec) {
    const mins = Math.max(1, Math.round((Number.isFinite(sec) ? sec : 0) / 60));
    const h = Math.floor(mins / 60), m = mins % 60;
    return h ? (m ? `${h} hr ${m} min` : `${h} hr`) : `${m} min`;
  }

  /** "today", "yesterday", "5 days ago", "3 weeks ago", "2 months ago", "1 year ago" for an epoch-seconds timestamp. */
  function fmtAgo(ts, now) {
    if (!ts) return "";
    const d = Math.max(0, Math.floor((now - ts) / 86400));
    if (d < 1) return "today";
    if (d < 2) return "yesterday";
    if (d < 14) return `${d} days ago`;
    if (d < 60) return `${Math.floor(d / 7)} weeks ago`;
    if (d < 700) return `${Math.floor(d / 30)} months ago`;
    const y = Math.floor(d / 365);
    return `${y} year${y > 1 ? "s" : ""} ago`;
  }

  function fmtBytes(n) {
    return n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`;
  }

  // ── genres ────────────────────────────────────────────────────────────────────────────────────────────────────
  // ID3v1 stored a genre as a number; plenty of old files still say "(17)" or "17" instead of "Rock".
  const ID3V1 = ["Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge", "Hip-Hop", "Jazz", "Metal", "New Age",
    "Oldies", "Other", "Pop", "R&B", "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska", "Death Metal", "Pranks",
    "Soundtrack", "Euro-Techno", "Ambient", "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical", "Instrumental",
    "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise", "Alternative Rock", "Bass", "Soul", "Punk", "Space", "Meditative",
    "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave", "Techno-Industrial", "Electronic", "Pop-Folk",
    "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap", "Pop/Funk", "Jungle",
    "Native American", "Cabaret", "New Wave", "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal", "Acid Punk",
    "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll", "Hard Rock"];

  /** A genre tag as a person would write it: "(17)Rock" → "Rock", "(13)" → "Pop"... a number nothing knows → "". */
  function genreLabel(raw) {
    let g = String(raw == null ? "" : raw).replace(/\u0000/g, " ").trim();
    const m = /^\((\d{1,3})\)\s*(.*)$/.exec(g);
    if (m) g = m[2] || ID3V1[+m[1]] || "";
    else if (/^\d{1,3}$/.test(g)) g = ID3V1[+g] || "";
    return g.trim();
  }

  // ── the catalog ───────────────────────────────────────────────────────────────────────────────────────────────
  /** Turn the gate's catalog into what the player browses. Tracks are objects with folded search keys; artists, albums
   *  and genres are index lists into lib.T, so a list of 21,000 songs costs 21,000 numbers, not 21,000 copies.
   *
   *  lib.hasYear / lib.hasAdded say whether the gate sent y / m at all (an older gate does not): a view that needs
   *  one is simply not offered. */
  function parseCatalog(cat) {
    const F = (cat && cat.fields) || [];
    const rows = (cat && cat.tracks) || [];
    const at = {};
    F.forEach((f, i) => { at[f] = i; });
    const T = new Array(rows.length);
    const foldMemo = new Map();                     // artists and albums repeat across hundreds of tracks: fold each once
    const foldOf = s => { let f = foldMemo.get(s); if (f === undefined) { f = fold(s); foldMemo.set(s, f); } return f; };
    const hasYear = at.y !== undefined, hasAdded = at.m !== undefined;
    let n = 0;
    for (const row of rows) {
      const p = row[at.p];
      if (typeof p !== "string" || !p) continue;
      const title = String(row[at.t] == null ? "" : row[at.t]) || p.split("/").pop();
      const artist = String(row[at.a] == null ? "" : row[at.a]) || "Unknown artist";
      const album = String(row[at.al] == null ? "" : row[at.al]);
      const genre = genreLabel(row[at.g]);
      const ft = fold(title), fa = foldOf(artist), fal = foldOf(album), fg = foldOf(genre);
      T[n] = {
        i: n, p, t: title, a: artist, al: album, g: genre,
        n: row[at.n] | 0, d: row[at.d] | 0, s: row[at.s] | 0, art: !!row[at.art],
        y: hasYear ? row[at.y] | 0 : 0, m: hasAdded ? row[at.m] | 0 : 0,
        ly: "", ft, fa, fal, fg, sq: squash(ft + fa + fal), ap: "",
      };
      n++;
    }
    T.length = n;

    const artistByName = new Map(), albumByKey = new Map(), genreByKey = new Map(), byPath = new Map();
    const artists = [], albums = [], genres = [];
    for (const t of T) {
      byPath.set(t.p, t.i);
      let ar = artistByName.get(t.a);
      if (!ar) { ar = { id: 0, name: t.a, key: sortKey(t.a), fk: t.fa, sq: squash(t.fa), tracks: [], albums: [] }; artistByName.set(t.a, ar); artists.push(ar); }
      ar.tracks.push(t.i);
      const ak = t.a + "\u0000" + t.al;
      let al = albumByKey.get(ak);
      if (!al) {
        const fk = (t.fal || "singles") + " " + t.fa;
        al = { id: 0, key: ak, artist: t.a, name: t.al || "Singles", key2: sortKey(t.al || "Singles"), fk, sq: squash(fk), tracks: [],
               year: 0, added: 0, artPath: "", dur: 0, discs: 0 };
        albumByKey.set(ak, al); albums.push(al); ar.albums.push(al);
      }
      al.tracks.push(t.i);
      al.dur += t.s;
      if (t.y && (!al.year || t.y < al.year)) al.year = t.y;
      if (t.m > al.added) al.added = t.m;
      if (t.d > al.discs) al.discs = t.d;
      if (!al.artPath && t.art) al.artPath = t.p;
      if (t.g) {
        const gk = fold(t.g);
        let g = genreByKey.get(gk);
        if (!g) { g = { id: 0, name: t.g, key: gk, tracks: [] }; genreByKey.set(gk, g); genres.push(g); }
        g.tracks.push(t.i);
      }
    }
    for (const al of albums) {
      al.tracks.sort((x, y) => (T[x].d - T[y].d) || (T[x].n - T[y].n) || cmp(T[x].t, T[y].t));
      for (const i of al.tracks) T[i].ap = al.artPath;            // every track shows its album's cover: one thumbnail per album
    }
    artists.sort((a, b) => cmp(a.key, b.key) || cmp(a.name, b.name));
    artists.forEach((a, i) => {
      a.id = i;
      a.albums.sort((x, y) => (x.year || 9999) - (y.year || 9999) || cmp(x.key2, y.key2));
      a.artPath = (a.albums.find(x => x.artPath) || {}).artPath || "";
      a.dur = a.tracks.reduce((s, i2) => s + T[i2].s, 0);
    });
    albums.sort((a, b) => cmp(a.key2, b.key2) || cmp(sortKey(a.artist), sortKey(b.artist)));
    albums.forEach((a, i) => { a.id = i; });
    genres.sort((a, b) => b.tracks.length - a.tracks.length || cmp(a.name, b.name));
    genres.forEach((g, i) => { g.id = i; });
    return { T, artists, albums, genres, byPath, artistByName, albumByKey, hasYear, hasAdded };
  }

  /** Attach the lyric summaries ({path: sentence}) to their tracks. Returns how many matched. */
  function attachLyrics(lib, by) {
    let n = 0;
    if (!by || typeof by !== "object") return 0;
    for (const p of Object.keys(by)) {
      const i = lib.byPath.get(p);
      if (i !== undefined && typeof by[p] === "string" && by[p]) { lib.T[i].ly = by[p]; n++; }
    }
    return n;
  }

  /** Every track as an index list in the order the Tracks tab offers. `mode`: title | artist | album | newest | year | length.
   *  Plain string comparison on the folded keys, not Intl.Collator: 21,000 tracks sort in ~50 ms instead of ~400. */
  function sortTracks(lib, mode) {
    const T = lib.T, n = T.length;
    const bare = f => { const m = /^(the|a|an)\s+(.+)$/.exec(f); return m ? m[2] : f; };
    const tk = new Array(n), ak = new Array(n), alk = new Array(n);
    for (let i = 0; i < n; i++) { tk[i] = bare(T[i].ft); ak[i] = bare(T[i].fa); alk[i] = bare(T[i].fal); }
    const lt = (x, y) => (x < y ? -1 : x > y ? 1 : 0);
    const title = (a, b) => lt(tk[a], tk[b]) || a - b;
    const album = (a, b) => lt(ak[a], ak[b]) || lt(alk[a], alk[b]) || (T[a].d - T[b].d) || (T[a].n - T[b].n) || lt(tk[a], tk[b]) || a - b;
    const by = {
      title,
      artist: (a, b) => lt(ak[a], ak[b]) || title(a, b),
      album,
      newest: (a, b) => (T[b].m - T[a].m) || album(a, b),
      year: (a, b) => (T[b].y - T[a].y) || album(a, b),
      length: (a, b) => (T[b].s - T[a].s) || title(a, b),
    };
    return Array.from({ length: n }, (_, i) => i).sort(by[mode] || title);
  }

  /** Albums, newest first (by the newest file in each): "Recently added" and the Home view. */
  function newestAlbums(lib) {
    return lib.albums.filter(a => a.added > 0).sort((a, b) => b.added - a.added || a.id - b.id);
  }

  // ── search ────────────────────────────────────────────────────────────────────────────────────────────────────
  const hasWordStart = (hay, tok) => hay.startsWith(tok) || hay.includes(" " + tok);

  /** Instant search over title, artist, album and genre. Every word typed must be found somewhere in a track; results
   *  rank a title that starts with the word first, then a word-start in the title or artist, then anywhere. Returns
   *  {artists, albums, tracks (indexes, best first, at most `limit`), total}. One pass over 21,000 tracks. */
  function search(lib, query, limit) {
    limit = limit || 1500;
    const toks = fold(query).split(/\s+/).filter(Boolean);
    if (!toks.length) return { artists: [], albums: [], tracks: [], total: 0 };
    const sq = toks.map(squash);
    const T = lib.T, hits = [];
    outer:
    for (let i = 0; i < T.length; i++) {
      const t = T[i];
      let score = 0;
      for (let k = 0; k < toks.length; k++) {
        const tok = toks[k];
        let s = 0;
        if (t.ft.startsWith(tok)) s = 8;
        else if (t.ft.includes(" " + tok)) s = 6;
        else if (hasWordStart(t.fa, tok)) s = 5;
        else if (t.ft.includes(tok)) s = 4;
        else if (t.fa.includes(tok)) s = 3;
        else if (t.fal.includes(tok)) s = 2;
        else if (t.fg === tok || t.fg.includes(tok)) s = 1;
        else if (sq[k] && t.sq.includes(sq[k])) s = 1;
        if (!s) continue outer;
        score += s;
      }
      hits.push(score * 1048576 + (1048575 - Math.min(i, 1048575)));   // score first, then catalog order, in one number
    }
    hits.sort((a, b) => b - a);
    const total = hits.length;
    const tracks = hits.slice(0, limit).map(h => 1048575 - (h % 1048576));
    const match = o => { for (let k = 0; k < toks.length; k++) if (!o.fk.includes(toks[k]) && !(sq[k] && o.sq.includes(sq[k]))) return false; return true; };
    const artists = lib.artists.filter(match).slice(0, 20);
    const albums = lib.albums.filter(match).slice(0, 30);
    return { artists, albums, tracks, total };
  }

  // ── Bilby DJ ──────────────────────────────────────────────────────────────────────────────────────────────────
  const DJ_SAME_ARTIST = 0.7;      // the real Bilby Music Service's rule (flask-api bilby_local.suggest_next)
  const DJ_AVOID_LAST = 50;        // and nothing heard in the last 50 tracks, when the pool allows

  /** The next track for Bilby DJ, as an index into lib.T (or -1 for an empty library).
   *
   *  The rule: never the current track. If the current artist has other tracks, 70 % of the time one of those, otherwise
   *  (30 %) any track at all. Within the pool, skip what is in `recent` (a Set of indexes: the last 50 played) whenever
   *  something else is left; if the pool is all recent, repeat rather than stall. A library of one plays that one. */
  function djPick(lib, cur, recent, rand) {
    rand = rand || Math.random;
    const T = lib.T, n = T.length;
    if (!n) return -1;
    if (n === 1) return 0;
    const ar = cur >= 0 && T[cur] ? lib.artistByName.get(T[cur].a) : null;
    const others = ar ? ar.tracks.filter(i => i !== cur) : [];
    const sameArtist = others.length > 0 && rand() < DJ_SAME_ARTIST;
    const fresh = i => i !== cur && !(recent && recent.has(i));
    if (sameArtist) {
      const pool = others.filter(fresh);
      const use = pool.length ? pool : others;
      return use[Math.floor(rand() * use.length)];
    }
    for (let tries = 0; tries < 40; tries++) {          // the library is huge and `recent` is small: a few draws almost always do
      const i = Math.floor(rand() * n);
      if (fresh(i)) return i;
    }
    const pool = [];
    for (let i = 0; i < n; i++) if (fresh(i)) pool.push(i);
    if (pool.length) return pool[Math.floor(rand() * pool.length)];
    let i = Math.floor(rand() * (n - 1));                // everything is recent: anything but the current one
    if (i >= cur && cur >= 0) i++;
    return i;
  }

  /** The index set djPick treats as "recent": the last `n` played paths, plus the current track. */
  function recentSet(lib, historyPaths, cur, n) {
    const set = new Set();
    const h = historyPaths || [];
    for (let k = Math.max(0, h.length - (n || DJ_AVOID_LAST)); k < h.length; k++) {
      const i = lib.byPath.get(h[k]);
      if (i !== undefined) set.add(i);
    }
    if (cur >= 0) set.add(cur);
    return set;
  }

  // ── the queue ─────────────────────────────────────────────────────────────────────────────────────────────────
  function shuffleInPlace(a, rand) {
    for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); const x = a[i]; a[i] = a[j]; a[j] = x; }
    return a;
  }

  /** The play queue: items {t: track index, u: unique id} and where we are (qi). Every edit lives here so the buttons,
   *  the keyboard, drag-and-drop, Media Session and a remote device's commands all go through the same rules. */
  class Queue {
    constructor(rand) {
      this.rand = rand || Math.random;
      this.items = []; this.qi = -1; this._u = 0;
      this.auto = 0;              // uid of the track Bilby DJ picked ahead of time (it stays last: later adds go before it)
      this.manualEnd = 0;         // "Play next" items already lined up end here, so a second one goes after the first
      this.orig = null;           // upcoming uids in their pre-shuffle order, so turning shuffle off can put them back
    }
    mk(t) { return { t, u: ++this._u }; }
    get length() { return this.items.length; }
    get cur() { return this.qi >= 0 ? this.items[this.qi] || null : null; }
    upcoming() { return this.items.slice(this.qi + 1); }
    /** Is the last item Bilby DJ's pick, and still to come? (Once it is the song playing, it is just a song: adds go after it.) */
    autoLast() { const n = this.items.length, l = this.items[n - 1]; return !!l && l.u === this.auto && n - 1 > this.qi; }
    isAuto(pos) { const it = this.items[pos]; return !!it && it.u === this.auto; }

    /** Replace the queue with `list` (track indexes) and stand on `start`. With shuffle, the chosen track goes first and the
     *  rest follow in a random order (the original order is remembered for turning shuffle off). */
    load(list, start, shuffle) {
      start = Math.max(0, Math.min(start | 0, list.length - 1));
      let items = list.map(t => this.mk(t));
      this.auto = 0; this.manualEnd = 0; this.orig = null;
      if (shuffle && items.length) {
        const first = items[start];
        const rest = items.slice(start + 1).concat(items.slice(0, start));
        this.orig = rest.map(x => x.u);
        items = [first].concat(shuffleInPlace(rest.slice(), this.rand));
        start = 0;
      }
      this.items = items; this.qi = items.length ? start : -1;
      return this.cur;
    }
    jump(pos) { if (pos < 0 || pos >= this.items.length) return null; this.qi = pos; return this.cur; }

    playNext(t) { return this.playNextMany([t]); }
    /** "Play next": right after the current track, and after any earlier "play next", before the rest. */
    playNextMany(list) {
      if (this.qi < 0) { this.load(list, 0, false); return 0; }
      const at = Math.max(this.qi + 1, this.manualEnd);
      this.items.splice(at, 0, ...list.map(t => this.mk(t)));
      this.manualEnd = at + list.length;
      this.orig = null;
      return at;
    }
    add(t) { return this.addMany([t]); }
    /** "Add to queue": the end, but ahead of a track Bilby DJ already picked, which stays the last. */
    addMany(list) {
      if (this.qi < 0) { this.load(list, 0, false); return 0; }
      const at = this.autoLast() ? this.items.length - 1 : this.items.length;
      this.items.splice(at, 0, ...list.map(t => this.mk(t)));
      return at;
    }
    /** Bilby DJ's pick: the last item, flagged so it can be told apart. */
    addAuto(t) {
      const it = this.mk(t);
      this.items.push(it); this.auto = it.u;
      return this.items.length - 1;
    }
    remove(pos) {
      if (pos < 0 || pos >= this.items.length || pos === this.qi) return false;
      this.items.splice(pos, 1);
      if (pos < this.qi) this.qi--;
      if (pos < this.manualEnd) this.manualEnd--;
      return true;
    }
    /** Move the upcoming item at `from` so it ends up at `to` (also upcoming: what has played, and the track playing, stay put). */
    move(from, to) {
      const n = this.items.length;
      if (from === to || from <= this.qi || to <= this.qi || from >= n || to >= n) return false;
      const curItem = this.cur;
      const [it] = this.items.splice(from, 1);
      this.items.splice(to, 0, it);
      this.qi = this.items.indexOf(curItem);
      this.manualEnd = 0; this.orig = null;
      return true;
    }
    clearUpcoming() { this.items.length = Math.max(0, this.qi + 1); this.manualEnd = 0; this.orig = null; this.auto = 0; }
    shuffleUpcoming() {
      if (this.qi < 0) return;
      const rest = this.items.slice(this.qi + 1);
      this.orig = rest.map(x => x.u);
      this.items = this.items.slice(0, this.qi + 1).concat(shuffleInPlace(rest, this.rand));
      this.manualEnd = 0;
    }
    /** Turn shuffle off: what is still to come goes back to the order it had (anything added since follows, in order). */
    unshuffle() {
      if (!this.orig) return;
      const rank = new Map(this.orig.map((u, i) => [u, i]));
      const rest = this.items.slice(this.qi + 1);
      const known = rest.filter(x => rank.has(x.u)).sort((a, b) => rank.get(a.u) - rank.get(b.u));
      const added = rest.filter(x => !rank.has(x.u));
      this.items = this.items.slice(0, this.qi + 1).concat(known, added);
      this.orig = null; this.manualEnd = 0;
    }
    /** Where "next" goes: the next position, the first one when repeating all, else -1 (the end). Does not move. */
    nextPos(repeat) {
      if (this.qi + 1 < this.items.length) return this.qi + 1;
      return repeat === "all" && this.items.length ? 0 : -1;
    }
    prevPos() { return this.qi > 0 ? this.qi - 1 : -1; }
    /** Every item's track index through fn (a new catalog re-numbers tracks); an item fn maps to -1 is dropped. */
    remap(fn) {
      const cur = this.cur;
      const kept = [];
      let qi = -1;
      for (const it of this.items) {
        const t = fn(it.t);
        if (t < 0) { if (it === cur) qi = kept.length - 1; continue; }
        it.t = t; kept.push(it);
        if (it === cur) qi = kept.length - 1;
      }
      this.items = kept; this.qi = qi;       // -1 with items left means the playing track vanished: "next" is the first one
      this.manualEnd = 0;
    }
    /** A slice around the current track, for saving and for Connect (a 21,000-song queue is not something to store). */
    window(before, after) {
      const lo = Math.max(0, this.qi - before), hi = Math.min(this.items.length, this.qi + after + 1);
      return { items: this.items.slice(lo, hi), qi: this.qi - lo };
    }
    totalSeconds(T, from) {
      let s = 0;
      for (let i = from == null ? this.qi + 1 : from; i < this.items.length; i++) s += T[this.items[i].t].s;
      return s;
    }
  }

  // ── the browser's autoplay rule ───────────────────────────────────────────────────────────────────────────────
  /** Browsers allow sound only after a tap (click, key press) on THIS page load: that permission lasts until the page
   *  reloads, so a reload (a deploy, a crash, a saved queue coming back) needs one tap again, and Safari / iOS ties it to
   *  the audio element. Some browsers also remember a per-site "allow sound" or a high engagement score; none of that is
   *  ever relied on. This object only tracks what happened:
   *    armed  a play() has worked after a tap on this page, so track changes need no further taps
   *    tap    sound was refused (or a saved queue is waiting): show "Tap to start the sound"
   *  `activation` is a function returning navigator.userActivation.hasBeenActive where the browser has that API. */
  class Armed {
    constructor(activation) { this.activation = activation || (() => undefined); this.proven = false; this.denied = false; this.tap = false; }
    /** Can the page make sound right now without a tap? Only a play() that worked proves it; where the browser says the
     *  page has been tapped (navigator.userActivation, not on WebKit) that counts too, until a refusal says otherwise. */
    get armed() { return this.proven || (!this.denied && this.activation() === true); }
    /** A play() call resolved. A muted element can always play, so that proves nothing about sound. */
    played(audible) {
      if (audible === false) return;
      this.proven = true; this.denied = false; this.tap = false;
    }
    /** play() rejected with NotAllowedError (or the browser said so some other way). */
    refused() { this.proven = false; this.denied = true; this.tap = true; }
    /** A saved queue that was playing came back after a reload: nothing can play until a tap. */
    waiting() { this.tap = true; }
    snapshot() { return { armed: this.armed, tap: this.tap }; }
  }

  // ── sleep timer ───────────────────────────────────────────────────────────────────────────────────────────────
  class SleepTimer {
    constructor() { this.until = 0; this.endOfTrack = false; }
    /** minutes (15/30/60) or "end" (stop when this track finishes). */
    start(kind, nowMs) {
      if (kind === "end") { this.endOfTrack = true; this.until = 0; }
      else { this.until = nowMs + Math.max(1, +kind || 0) * 60000; this.endOfTrack = false; }
    }
    stop() { this.until = 0; this.endOfTrack = false; }
    get active() { return this.until > 0 || this.endOfTrack; }
    due(nowMs) { return this.until > 0 && nowMs >= this.until; }
    remainingMs(nowMs) { return this.until ? Math.max(0, this.until - nowMs) : 0; }
    /** 1 normally, easing down to 0 over the last `fadeMs` so the music fades out instead of cutting off. */
    gain(nowMs, fadeMs) {
      if (!this.until) return 1;
      const left = this.until - nowMs;
      return left >= fadeMs ? 1 : Math.max(0, left / fadeMs);
    }
    label(nowMs) {
      if (this.endOfTrack) return "End of track";
      if (!this.until) return "";
      const m = Math.ceil(this.remainingMs(nowMs) / 60000);
      return `${m} min`;
    }
  }

  // ── virtual list geometry ─────────────────────────────────────────────────────────────────────────────────────
  /** Prefix sums of row heights: offsets[i] is where row i starts, offsets[n] the total height. */
  function buildOffsets(heights) {
    const off = new Float64Array(heights.length + 1);
    for (let i = 0; i < heights.length; i++) off[i + 1] = off[i] + heights[i];
    return off;
  }
  /** The row that contains y (a binary search, so 21,000 rows cost ~15 steps). */
  function rowAt(off, y) {
    let lo = 0, hi = off.length - 2;
    if (hi < 0) return 0;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (off[mid] <= y) lo = mid; else hi = mid - 1; }
    return lo;
  }
  /** [first, end) rows to have in the DOM for a scroll position, with `over` pixels of margin either side. */
  function visibleRange(off, top, height, over) {
    const n = off.length - 1;
    if (n <= 0) return [0, 0];
    const first = rowAt(off, Math.max(0, top - over));
    let end = rowAt(off, Math.max(0, top + height + over - 1e-6)) + 1;     // the bottom edge is exclusive: a row that starts there is not on screen
    if (end > n) end = n;
    return [first, end];
  }
  function chunk(list, size) {
    const out = [];
    for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
    return out;
  }

  /** The thumbnail width to ask the gate for: the smallest it makes (96/192/384/768) that covers the box at this pixel density. */
  const ART_WIDTHS = [96, 192, 384, 768];
  function artWidth(cssPx, dpr) {
    const need = Math.ceil((cssPx || 1) * (dpr || 1));
    return ART_WIDTHS.find(w => w >= need) || ART_WIDTHS[ART_WIDTHS.length - 1];
  }

  // ── where we are running ──────────────────────────────────────────────────────────────────────────────────────
  /** What the browser is, as far as playback goes. WebKit (every iPhone browser, and Safari on the Mac) lets only the
   *  audio element that a tap started keep playing on its own, so there the player re-sources ONE element; elsewhere a
   *  second hidden element preloads the next track. iOS also ignores audio.volume, so the volume slider is hidden there. */
  function detectEnv(ua, platform, touchPoints) {
    ua = ua || "";
    const ios = /iPhone|iPad|iPod/.test(ua) || (platform === "MacIntel" && (touchPoints | 0) > 1);
    const safari = /Safari\//.test(ua) && !/Chrome|Chromium|CriOS|FxiOS|Edg|OPR|Android/.test(ua);
    const name = /Tesla/.test(ua) ? "Tesla" : /iPhone/.test(ua) ? "iPhone" : (/iPad/.test(ua) || (ios && !/iPhone/.test(ua))) ? "iPad"
      : /Android/.test(ua) ? "Android" : /Macintosh/.test(ua) ? "Mac" : /Windows/.test(ua) ? "PC" : /Linux/.test(ua) ? "Linux" : "Browser";
    // Another PC or Mac with the player open is told apart by its browser ("PC · Chrome"); phones and the car are already unique
    const browser = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : /Firefox\/|FxiOS/.test(ua) ? "Firefox" : /Chrome\/|CriOS/.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "";
    const label = ["PC", "Mac", "Linux", "Browser"].includes(name) && browser ? `${name} · ${browser}` : name;
    return { ios, safari, webkit: ios || safari, singleElement: ios || safari, tesla: /Tesla/.test(ua), name, label };
  }

  /** Connect state sent to the portal: the track, where we are in it, and a window of the queue (never the whole 21k). */
  function trimPaths(items, qi, T, before, after) {
    const lo = Math.max(0, qi - before), hi = Math.min(items.length, qi + after + 1);
    return { paths: items.slice(lo, hi).map(it => T[it.t].p), idx: qi - lo };
  }

  return {
    fold, squash, sortKey, letterOf, cmp,
    fmtTime, fmtLong, fmtAgo, fmtBytes, genreLabel,
    parseCatalog, attachLyrics, sortTracks, newestAlbums, search,
    djPick, recentSet, DJ_SAME_ARTIST, DJ_AVOID_LAST,
    Queue, Armed, SleepTimer,
    buildOffsets, rowAt, visibleRange, chunk, artWidth, ART_WIDTHS,
    detectEnv, trimPaths, shuffleInPlace,
  };
});
