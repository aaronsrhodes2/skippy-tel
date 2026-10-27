"""search_memory: the Captain's question in, the passages of his own text that answer it out, with where each came from.

What it searches (corpus.py): the private Skippy's saved conversations, the Skippy Memory archive (read from its source transcripts,
Claude Code's ~/.claude/projects, through the archive's own parser, since Docker can't see the Drive folder; any other AI's export in the
archive comes through the bridge's /memory door) and Mentat's research log. How (index.py): bge-m3 vectors in one SQLite file on
skippy-pc, refreshed at the start of each search (only new or changed files are read, only new passages embedded), then
bge-reranker-v2-m3 reads the best 30 and the best `k` come back. The benchmark that chose those two: docs/search-speech-popups.md.

A pop-up (docs/popups.md) whose two models stay RESIDENT on skippy-pc's card (the Captain's choice, 2026-10-24: ~2.4 GB, so a search
takes seconds instead of the ~5 minutes that loading them through Docker's VM cost each time). They are loaded at start, one at a time,
each only when the card has the model plus MARGIN_GB free (a full card spills into Docker's 8 GB VM and freezes it); if Ollama is holding
the room, it is asked once to unload, as the reporter does before a picture, and reloads around them on the next chat. Until both are
in, a search loads what it needs for itself (on the card if there's room, else on the CPU) and lets go of it after. Everything stays on
skippy-pc: the index, the models, the text. The only thing that leaves is the answer to the Captain's own question (admin-only).

  GET  /health    {"ready", "loaded", "models": "resident" | why not yet, "passages": {...per source}, "device"}
  POST /run       {"args": {"query", "k"?, "sources"?, "format"?: "text" | "json"}} -> text/plain or application/json
  POST /refresh   bring the index up to date without a question (the first build after a deploy)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus  # noqa: E402
import index as index_mod  # noqa: E402

WEIGHTS = Path(os.environ.get("SEARCH_WEIGHTS", "/w"))
EMBED_DIR = WEIGHTS / "bge-m3"
RERANK_DIR = WEIGHTS / "bge-reranker-v2-m3"
INDEX_FILE = Path(os.environ.get("INDEX_DIR", "/index")) / "search.db"
DEVICE = os.environ.get("SEARCH_DEVICE", "auto")
ROOTS = {
    "agent": Path(os.environ.get("AGENT_DIR", "/src/agent")),
    "memory": Path(os.environ.get("SESSIONS_DIR", "/src/claude-projects")),
    "mentat": Path(os.environ.get("MENTAT_DIR", "/src/mentat")),
}
BRIDGE = os.environ.get("BRIDGE_URL", "").rstrip("/")            # the archive's other sources (ChatGPT, Grok exports): optional
TOKEN = os.environ.get("SKIPPY_HOME_TOKEN", "")
LABELS = {"agent": "private Skippy conversation", "memory": "Skippy Memory", "mentat": "Mentat's research log"}
EXCERPT = 900
MAX_BODY = 100_000

RESIDENT = os.environ.get("SEARCH_RESIDENT", "1") != "0"
MODEL_GB = 1.3                     # one fp16 model on the card with its working memory (the benchmark: 1.2 GB peak each)
MARGIN_GB = float(os.environ.get("VRAM_MARGIN_GB", "1.5"))       # what must stay free after a load (tts-studio keeps the same)
RETRY_S = 60
OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")

_job = threading.Lock()
_index: index_mod.Index | None = None


def _cuda() -> bool:
    try:
        import torch
        return DEVICE != "cpu" and torch.cuda.is_available()
    except ImportError:
        return False


def free_gib() -> float:
    """The card's free memory as nvidia-smi sees it (the whole card: Windows' own use, every container, Ollama). Not torch's
    mem_get_info: under WSL a fresh process was told 14.7 GB free when the card had 6.0 GB (2026-10-24), which would let a load
    overfill it. torch only when nvidia-smi isn't there."""
    import subprocess
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=10).stdout.split()
        return float(out[0]) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        import torch
        return torch.cuda.mem_get_info()[0] / 2**30


def ollama_unload() -> None:
    """Ask Ollama to let go of its models (reporter.free_gpu does the same before a picture); it reloads on its next chat."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=5) as r:
            loaded = json.loads(r.read()).get("models") or []
        for m in loaded:
            body = json.dumps({"model": m.get("name"), "keep_alive": 0}).encode()
            urllib.request.urlopen(urllib.request.Request(f"{OLLAMA}/api/generate", data=body, method="POST",
                                                          headers={"Content-Type": "application/json"}), timeout=60).read()
        print(json.dumps({"event": "asked_ollama_to_unload", "models": len(loaded)}), flush=True)
    except (OSError, ValueError):
        pass


class Models:
    """The embedder and the reranker: resident once loaded, else borrowed for one search."""

    def __init__(self):
        self.emb = None
        self.rk = None
        self.state = "not loaded yet"

    def loaded(self) -> bool:
        return self.emb is not None and self.rk is not None

    @staticmethod
    def _room() -> bool:
        return _cuda() and free_gib() >= MODEL_GB + MARGIN_GB

    def load_resident(self, sleep=time.sleep, tries: int = 0) -> None:
        """Load both onto the card and keep them; wait (asking Ollama once) while there's no room. `tries` > 0 gives up after that
        many waits (the tests); 0 waits as long as it takes."""
        import models
        asked, waits = False, 0
        while not self.loaded():
            if not _cuda():
                self.state = "no card here: each search loads the models on the CPU"
                return
            if self._room():
                if self.emb is None:
                    self.emb = models.Embedder("bge", "cuda", path=EMBED_DIR)
                else:
                    self.rk = models.Reranker("bge", "cuda", path=RERANK_DIR)
                continue
            if not asked and waits >= 1:
                ollama_unload()
                asked = True
            self.state = f"waiting for room on the card ({free_gib():.1f} GB free, {MODEL_GB + MARGIN_GB:.1f} needed)"
            waits += 1
            if tries and waits >= tries:
                return
            sleep(RETRY_S)
        self.state = "resident"
        print(json.dumps({"event": "models_resident", "free_gib_after": round(free_gib(), 1)}), flush=True)

    def _borrow_device(self) -> str:
        return "cuda" if self._room() else "cpu"

    def embedder(self):
        """(the embedder, whether it must be closed after this search)."""
        if self.emb is not None:
            return self.emb, False
        import models
        return models.Embedder("bge", self._borrow_device(), path=EMBED_DIR), True

    def reranker(self):
        if self.rk is not None:
            return self.rk, False
        import models
        return models.Reranker("bge", self._borrow_device(), path=RERANK_DIR), True


MODELS = Models()


def the_index() -> index_mod.Index:
    global _index
    if _index is None:
        _index = index_mod.Index(INDEX_FILE, "bge")
    return _index


def sources() -> dict:
    return {
        "agent": (ROOTS["agent"], corpus.agent_files, corpus.read_agent),
        "memory": (ROOTS["memory"], corpus.session_files, corpus.read_session),
        "mentat": (ROOTS["mentat"], corpus.mentat_files, corpus.read_mentat),
    }


def _bridge(path: str):
    req = urllib.request.Request(BRIDGE + path, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def archive_extras() -> list:
    """The archive's conversations that don't come from Claude Code (an export the Captain dropped in its inbox), through the bridge's
    /memory door. That door cuts a long conversation to its last 60,000 characters (memory/sync.py READ_MAX_CHARS)."""
    if not (BRIDGE and TOKEN):
        return []
    try:
        rows = _bridge("/memory/list").get("conversations") or []
    except (OSError, ValueError):
        return []
    out = []
    for row in rows:
        key = str(row.get("key") or "")
        if not key or row.get("source") == "claude-code":
            continue

        def read(key=key, title=str(row.get("title") or "")):
            try:
                found = _bridge("/memory/conversation?key=" + urllib.parse.quote(key))
            except (OSError, ValueError):
                return []
            return corpus.memory_text(str(found.get("text") or ""), "archive/" + key, title)
        stamp = sum(ord(c) * (i + 1) for i, c in enumerate(str(row.get("updated") or "")))   # changes when the conversation does
        out.append(("memory", "archive/" + key, float(stamp), int(row.get("messages") or 0), read))
    return out


def refresh(box: dict) -> dict:
    """Bring the index up to date; the embedder is only fetched when something is new. `box` keeps it for the search that follows."""
    def embed(texts):
        if "e" not in box:
            box["e"] = MODELS.embedder()
        return box["e"][0].encode(texts)
    return the_index().refresh(sources(), embed, archive_extras())


def _release(pair) -> None:
    model, borrowed = pair
    if borrowed:
        model.close()


def search(query: str, k: int = 6, only: set[str] | None = None) -> dict:
    t0 = time.time()
    box: dict = {}
    try:
        report = refresh(box)
        if "e" not in box:
            box["e"] = MODELS.embedder()
        cands = the_index().candidates(box["e"][0].encode([query], query=True)[0], index_mod.RERANK_K, only)
    finally:
        if "e" in box:
            _release(box["e"])
    t1 = time.time()
    if cands:
        pair = MODELS.reranker()
        try:
            scores = pair[0].score(query, [f"{c['title']}\n{c['text']}" for c in cands])
        finally:
            _release(pair)
        for c, sc in zip(cands, scores):
            c["score"] = round(float(sc), 4)
        cands.sort(key=lambda c: -c["score"])
    t2 = time.time()
    timing = {"refresh_s": report["seconds"], "new_passages": report["passages_new"], "search_s": round(t1 - t0, 2),
              "rerank_s": round(t2 - t1, 2), "resident": MODELS.loaded()}
    print(json.dumps({"event": "search", **timing, "candidates": len(cands)}), flush=True)   # never the question: it's his
    return {"query": query, "results": cands[:k], "timing": timing}


def as_text(found: dict) -> str:
    if not found["results"]:
        return f"Nothing in the Captain's conversations, the Skippy Memory archive or Mentat's log matches \"{found['query']}\".\n"
    out = [f"The best {len(found['results'])} passages for \"{found['query']}\":"]
    for n, r in enumerate(found["results"], 1):
        where = ", ".join(x for x in (LABELS.get(r["source"], r["source"]), r["at"].replace("T", " "), r["anchor"]) if x)
        text = r["text"] if len(r["text"]) <= EXCERPT else r["text"][:EXCERPT].rsplit(" ", 1)[0] + " ..."
        out.append(f"{n}. {r['title'] or r['doc']}  ({where}; relevance {r.get('score', 0):.2f})\n   source: {r['doc']}\n\n{text}")
    return "\n\n".join(out) + "\n"


def check_args(args: dict) -> tuple[str, int, set[str] | None]:
    query = args.get("query")
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 500:
        raise ValueError("query: the question, 2 to 500 characters")
    k = args.get("k", 6)
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 20:
        raise ValueError("k: how many passages, 1 to 20")
    only = args.get("sources")
    if only is not None:
        if not isinstance(only, list) or not only or not set(only) <= set(ROOTS):
            raise ValueError("sources: a list of agent, memory, mentat (or left out for all three)")
        only = set(only)
    return query.strip(), k, only


def health() -> dict:
    weights = (EMBED_DIR / "model.safetensors").is_file() and (RERANK_DIR / "model.safetensors").is_file()
    try:
        passages = the_index().count()
    except Exception:  # noqa: BLE001 - an unreadable index is "not ready", not a crash
        passages = None
    present = [n for n, r in ROOTS.items() if r.is_dir()]
    return {"ready": bool(weights and passages is not None and present), "loaded": MODELS.loaded(), "models": MODELS.state,
            "weights": weights, "passages": passages, "sources": present, "device": DEVICE}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype == "text/plain" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, status: int, why: str) -> None:
        self._send(status, json.dumps({"error": why}).encode())

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, json.dumps(health()).encode())
        self._err(404, "no such page")

    def do_POST(self):  # noqa: N802
        if self.path not in ("/run", "/refresh"):
            return self._err(404, "no such page")
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            return self._err(413, "too big")
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
            args = body.get("args") if isinstance(body.get("args"), dict) else {}
            if self.path == "/run":
                query, k, only = check_args(args)
        except ValueError as exc:
            return self._err(400, str(exc) or "that wasn't a pop-up request")
        if not _job.acquire(timeout=600):
            return self._err(503, "another search is still running")
        try:
            if self.path == "/refresh":
                box: dict = {}
                try:
                    report = refresh(box)
                finally:
                    if "e" in box:
                        _release(box["e"])
                return self._send(200, json.dumps({**report, "passages": the_index().count()}).encode())
            found = search(query, k, only)
        except Exception as exc:  # noqa: BLE001 - one bad search must not take the service down
            return self._err(500, f"{type(exc).__name__}: {exc}"[:300])
        finally:
            _job.release()
        if args.get("format") == "json":
            return self._send(200, json.dumps(found).encode())
        self._send(200, as_text(found).encode(), "text/plain")

    def log_message(self, *a):
        pass


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


if __name__ == "__main__":
    if RESIDENT:
        threading.Thread(target=MODELS.load_resident, name="resident-models", daemon=True).start()
    serve(int(os.environ.get("PORT", "47430"))).serve_forever()
