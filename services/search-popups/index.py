"""The meaning index of the Captain's text: one SQLite file on skippy-pc (INDEX_DIR, a bind mount under SkippyTel/state), refreshed
incrementally, searched by vector and then reranked.

  files     (source, path) -> mtime, size: a file whose mtime and size are unchanged is not read again
  passages  key -> source, doc, title, at, anchor, text, vec (float16, L2-normalised)

A refresh reads only new or changed files, embeds only passages whose key it hasn't seen (a growing conversation costs its new
turns, not the whole file again) and drops the passages a changed file no longer has. A file that disappears loses its passages,
except under KEEP_GONE sources: Claude Code clears its own transcripts after a few weeks while the Skippy Memory archive keeps them
forever, and so does this index.

The vectors of every passage sit in memory as one float16 matrix (4,500 passages x 1024 = 9 MB); a search is one matrix product,
then the reranker reads the best RERANK_K and the best `k` come back with where they came from.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

import corpus

RERANK_K = 30
KEEP_GONE = {"memory"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (source TEXT, path TEXT, mtime REAL, size INTEGER, PRIMARY KEY (source, path));
CREATE TABLE IF NOT EXISTS passages (key TEXT PRIMARY KEY, source TEXT, doc TEXT, title TEXT, at TEXT, anchor TEXT, text TEXT,
                                     vec BLOB);
CREATE INDEX IF NOT EXISTS passages_doc ON passages (source, doc);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class Index:
    def __init__(self, path: Path, family: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.family = family
        self.lock = threading.Lock()
        self._mat: np.ndarray | None = None
        self._rows: list[tuple] = []
        with self._db() as db:
            db.executescript(SCHEMA)
            old = db.execute("SELECT v FROM meta WHERE k='family'").fetchone()
            if old and old[0] != family:                     # vectors from another model mean nothing to this one
                db.execute("DELETE FROM passages")
                db.execute("DELETE FROM files")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('family', ?)", (family,))

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # ── refresh ──
    def refresh(self, sources: dict[str, tuple[Path, Callable, Callable]], embed: Callable[[list[str]], np.ndarray],
                extra: Iterable[tuple[str, str, float, int, Callable[[], list[corpus.Passage]]]] = ()) -> dict:
        """sources: name -> (root, list_files(root), read(path, rel)). extra: (source, path, mtime, size, read()) for documents
        that don't live in a folder here (the bridge's archive door). `embed` is only called when something is new."""
        t0 = time.time()
        report = {"files_read": 0, "passages_new": 0, "passages_dropped": 0}
        with self.lock, self._db() as db:
            known = {(s, p): (m, z) for s, p, m, z in db.execute("SELECT source, path, mtime, size FROM files")}
            seen: set[tuple[str, str]] = set()
            todo: list[tuple[str, str, float, int, Callable]] = []
            for name, (root, list_files, read) in sources.items():
                for f in list_files(root):
                    rel = str(f.relative_to(root)).replace("\\", "/")
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    seen.add((name, rel))
                    if known.get((name, rel)) != (st.st_mtime, st.st_size):
                        todo.append((name, rel, st.st_mtime, st.st_size, lambda f=f, rel=rel, read=read: read(f, rel)))
            for name, rel, mtime, size, read in extra:
                seen.add((name, rel))
                if known.get((name, rel)) != (mtime, size):
                    todo.append((name, rel, mtime, size, read))
            new: list[tuple[str, corpus.Passage]] = []
            for name, rel, mtime, size, read in todo:
                report["files_read"] += 1
                passages = read()
                have = {k for (k,) in db.execute("SELECT key FROM passages WHERE source=? AND doc=?", (name, rel))}
                keep = {p.key for p in passages}
                gone = have - keep
                if gone:
                    db.executemany("DELETE FROM passages WHERE key=?", [(k,) for k in gone])
                    report["passages_dropped"] += len(gone)
                new += [(name, p) for p in passages if p.key not in have]   # filed under the source it was read from
                db.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)", (name, rel, mtime, size))
            for (name, rel) in set(known) - seen:            # files that are no longer there
                if name in KEEP_GONE:
                    continue
                cur = db.execute("DELETE FROM passages WHERE source=? AND doc=?", (name, rel))
                report["passages_dropped"] += cur.rowcount
                db.execute("DELETE FROM files WHERE source=? AND path=?", (name, rel))
            uniq = {p.key: (name, p) for name, p in new}
            if uniq:
                ps = list(uniq.values())
                vecs = embed([p.embed_text() for _, p in ps]).astype(np.float16)
                db.executemany("INSERT OR REPLACE INTO passages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               [(p.key, name, p.doc, p.title, p.at, p.anchor, p.text, v.tobytes()) for (name, p), v in zip(ps, vecs)])
                report["passages_new"] = len(ps)
            if todo or set(known) - seen:
                self._mat = None
        report["seconds"] = round(time.time() - t0, 2)
        return report

    def stale(self, sources: dict[str, tuple[Path, Callable, Callable]]) -> int:
        """How many files a refresh would read (no model needed to find out)."""
        with self._db() as db:
            known = {(s, p): (m, z) for s, p, m, z in db.execute("SELECT source, path, mtime, size FROM files")}
        n = 0
        for name, (root, list_files, _) in sources.items():
            for f in list_files(root):
                try:
                    st = f.stat()
                except OSError:
                    continue
                n += known.get((name, str(f.relative_to(root)).replace("\\", "/"))) != (st.st_mtime, st.st_size)
        return n

    # ── search ──
    def _matrix(self) -> tuple[np.ndarray, list[tuple]]:
        if self._mat is None:
            with self._db() as db:
                rows = db.execute("SELECT key, source, doc, title, at, anchor, text, vec FROM passages").fetchall()
            self._rows = [r[:7] for r in rows]
            self._mat = (np.stack([np.frombuffer(r[7], dtype=np.float16) for r in rows]) if rows
                         else np.zeros((0, 1), np.float16))
        return self._mat, self._rows

    def count(self) -> dict[str, int]:
        with self._db() as db:
            return dict(db.execute("SELECT source, COUNT(*) FROM passages GROUP BY source").fetchall())

    def candidates(self, qvec: np.ndarray, n: int = RERANK_K, sources: set[str] | None = None) -> list[dict]:
        mat, rows = self._matrix()
        if not rows:
            return []
        sims = mat.astype(np.float32) @ qvec.astype(np.float32)
        if sources:
            sims = np.where([r[1] in sources for r in rows], sims, -2.0)
        out, texts = [], set()
        for i in np.argsort(-sims):
            if sims[i] <= -2.0 or len(out) >= n:
                break
            r = rows[i]
            if r[6] in texts:                                # the same words saved twice (a session and its archive copy)
                continue
            texts.add(r[6])
            out.append({"key": r[0], "source": r[1], "doc": r[2], "title": r[3], "at": r[4], "anchor": r[5], "text": r[6],
                        "similarity": round(float(sims[i]), 4)})
        return out
