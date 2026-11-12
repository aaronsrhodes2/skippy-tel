/* The Audiobooks app's logic that needs no browser (so portal/tests/js/books.test.js runs it under Node): window.BooksPure.

     fmt / left           a time as 1:02:03, and how much is left as "3h 20m"
     chapterAt            which chapter a second falls in
     status               new, listening or finished, from a book and its saved place
     visible              the books to show for a search and a filter, in the order asked for: series (the default), title or author
     gridRows             the covers cut into rows of `cols`, for a list shown a screen at a time
     colsFor              how many covers fit across `width` px
     resumeAt             where Resume starts (a few seconds back, the start of a finished book)
     shouldSave           when the browser writes its place back (every so often while playing, always on a pause)
     remotePos            where a book is on a room box now, from its last check-in */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.BooksPure = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SAVE_EVERY_S = 15;
  const REWIND_S = 3;
  const DONE_LEFT_S = 20;          // within this of the end counts as finished

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

  function fmt(sec) {
    sec = Math.max(0, Math.floor(+sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
    const two = n => (n < 10 ? "0" : "") + n;
    return h ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`;
  }

  function left(sec) {
    sec = Math.max(0, Math.round((+sec || 0) / 60));
    const h = Math.floor(sec / 60), m = sec % 60;
    return h ? `${h}h ${m}m` : `${m}m`;
  }

  /** Index of the chapter holding second `pos`; chapters are [[title, startSecond], ...] in order. */
  function chapterAt(chapters, pos) {
    if (!chapters || !chapters.length) return 0;
    let lo = 0, hi = chapters.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (chapters[mid][1] <= pos) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  function finished(book, place) {
    if (place && place.finished) return true;
    if (place && place.last_played) return false;       // he started it again since OpenAudible marked it
    return !!book.oa;
  }

  function status(book, place) {
    if (finished(book, place)) return "finished";
    return place && place.pos > 1 ? "listening" : "new";
  }

  const norm = s => (s || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");

  const bare = s => norm(s).replace(/^(the|a|an)\s+/, "");
  const cmp = (x, y) => (x < y ? -1 : x > y ? 1 : 0);

  /** Books in reading order: series by name (an article does not count), each series by its number then release date, the books in no series
   *  after them by title. */
  function bySeries(a, b) {
    if (!!a.sr !== !!b.sr) return a.sr ? -1 : 1;
    if (a.sr) {
      const s = cmp(bare(a.sr), bare(b.sr));
      if (s) return s;
      const qa = a.sq == null ? Infinity : a.sq, qb = b.sq == null ? Infinity : b.sq;
      if (qa !== qb) return qa < qb ? -1 : 1;
      const d = cmp(a.rd || "", b.rd || "");
      if (d) return d;
    }
    return cmp(bare(a.t), bare(b.t));
  }
  const byAuthor = (a, b) => cmp(norm(a.a), norm(b.a)) || bySeries(a, b);
  const byTitle = (a, b) => cmp(bare(a.t), bare(b.t));
  const SORTS = ["series", "title", "author"];

  /** mode: "all" | "listening" | "new" | "finished". Search matches every word in the title, author, narrator or series. `sort`: "series"
   *  (default), "title" or "author"; the Listening filter is always most-recently-played first. */
  function visible(books, places, q, mode, sort) {
    const words = norm(q).split(/[^a-z0-9']+/).filter(Boolean);
    const out = [];
    for (const b of books) {
      const place = places[b.id];
      const st = status(b, place);
      if (mode && mode !== "all" && st !== mode) continue;
      if (words.length) {
        const hay = norm(`${b.t} ${b.a} ${b.n} ${b.sr || ""}`);
        if (!words.every(w => hay.includes(w))) continue;
      }
      out.push(b);
    }
    const order = sort === "title" ? byTitle : sort === "author" ? byAuthor : bySeries;
    out.sort((x, y) => {
      if (mode === "listening") return ((places[y.id] || {}).last_played || 0) - ((places[x.id] || {}).last_played || 0);
      return order(x, y);
    });
    return out;
  }

  function colsFor(width, min) {
    return clamp(Math.floor((width || 0) / (min || 104)), 2, 8);
  }

  function gridRows(books, cols, h) {
    const rows = [];
    for (let i = 0; i < books.length; i += cols) rows.push({ type: "shelf", h, books: books.slice(i, i + cols) });
    return rows;
  }

  function resumeAt(place, duration) {
    if (!place || place.finished || !place.pos) return 0;
    if (duration && duration - place.pos < DONE_LEFT_S) return 0;
    return Math.max(0, place.pos - REWIND_S);
  }

  /** Write the place back: always when asked (a pause, a seek, closing), else once SAVE_EVERY_S has passed since the last write. */
  function shouldSave(lastSavedAt, now, force) {
    return !!force || !lastSavedAt || now - lastSavedAt >= SAVE_EVERY_S * 1000;
  }

  /** A box said `book.pos` `age` seconds ago; while it plays, it has moved on by the speed. */
  function remotePos(book, ageS, duration) {
    if (!book) return 0;
    const p = book.pos + (book.playing ? Math.max(0, ageS) * (book.speed || 1) : 0);
    return clamp(p, 0, duration || p);
  }

  return { fmt, left, chapterAt, status, finished, visible, SORTS, bySeries, colsFor, gridRows, resumeAt, shouldSave, remotePos, SAVE_EVERY_S, DONE_LEFT_S, REWIND_S };
});
