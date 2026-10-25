"""Benchmark the two model families on the Captain's real corpus: speed, memory and whether the right conversation comes back.

    python services/search-popups/bench.py --device cpu --threads 8 [--families qwen3,bge] [--limit N] [--out FILE.json]

QUERIES are paraphrased on purpose (few words shared with the passage that answers them): a keyword search would miss most,
which is what a meaning search is for. A query hits when a returned passage's document (its file name or title) matches
one of its gold strings. Reports hit@1, hit@5 and MRR@10 for the embedder alone and after reranking its top RERANK_K.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus  # noqa: E402
import models  # noqa: E402

RERANK_K = 30

QUERIES = [
    ("the mod for Baldur's Gate that lets me have a bigger group of companions", ["pixelbites"]),
    ("stop the game streaming box asking me for a code every time it connects", ["steam-link"]),
    ("what did another AI think of my personality", ["grok-thinks-of-me"]),
    ("how the phone and glasses demo went on my trip outdoors", ["camping"]),
    ("copying the files off a USB stick", ["thumb-drive", "easy-task"]),
    ("a decentralised alliance of open AI models", ["mycelium-distributed"]),
    ("an extension broke the desktop app and now it won't quit", ["windows-mcp"]),
    ("fixing the names of my film files", ["movies-go-through"]),
    ("large file storage for the game's repository", ["gitlfs"]),
    ("porting my space game so it plays in a web browser", ["how-difficult-would-it-be-to-conver"]),
    ("prep for the tabletop roleplaying campaign I'm running", ["daggerhear", "heroes-of-chance", "heroes of chance"]),
    ("change the colour of the administration tile and ship it", ["admin-card-red"]),
    ("the sandbox game server is missing Half-Life content", ["cloud-server-content", "cloud server content"]),
    ("talking about getting power by merging atoms", ["nuclear-fusion", "nuclear fusion", "discussing-nuc"]),
    ("the conveyor-belt factory game project with an AI", ["ai-factorio"]),
    ("writing a book that teaches physics from the ground up", ["strange-project"]),
    ("gathering all the story background from the old Remnant experiment", ["extract-all-the-lore"]),
    ("play my songs on the car, the laptop and the phone", ["iphone-macbook-pc-tesla", "iphone, macbook"]),
    ("how big is a black hole's horizon for a given mass", ["schwarzschild"]),
    ("how far a thrown ball travels", ["baseball", "projectile"]),
    ("designing a game about self-replicating space probes", ["von-neum"]),
    ("turning a session recording into a list of rules to remember", ["trantscript"]),
]

ROOTS = {
    "agent": Path(os.environ.get("AGENT_DIR", "D:/Aaron/conversations/private-skippy")),
    "memory": Path(os.environ.get("MEMORY_DIR", "G:/My Drive/Skippy Memory/conversations")),
    "mentat": Path(os.environ.get("MENTAT_DIR", "D:/Aaron/development/SkippyTel/state/mentat-research")),
}


def load_corpus(limit: int = 0) -> list[corpus.Passage]:
    out = []
    for name, (files, read) in corpus.SOURCES.items():
        root = ROOTS[name]
        for f in files(root):
            out += read(f, str(f.relative_to(root)).replace("\\", "/"))
    return out[:limit] if limit else out


class Peak:
    """Peak resident memory of this process (sampled) and, on CUDA, peak allocated VRAM."""
    def __init__(self):
        import psutil
        self.proc, self.rss, self.stop = psutil.Process(), 0, False

    def __enter__(self):
        self.rss = self.proc.memory_info().rss
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:  # noqa: BLE001
            pass
        return self

    def _run(self):
        while not self.stop:
            self.rss = max(self.rss, self.proc.memory_info().rss)
            time.sleep(0.05)

    def __exit__(self, *a):
        self.stop = True
        self.t.join()

    def vram_gib(self) -> float:
        import torch
        return torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0


def hit_rank(passages, ranked, gold) -> int:
    for r, i in enumerate(ranked[:10], 1):
        p = passages[i]
        hay = (p.doc + " " + p.title).lower()
        if any(g in hay for g in gold):
            return r
    return 0


def metrics(ranks):
    n = len(ranks)
    return {"hit@1": sum(1 for r in ranks if r == 1) / n, "hit@5": sum(1 for r in ranks if 0 < r <= 5) / n,
            "mrr@10": sum(1 / r for r in ranks if r) / n}


def run(family: str, device: str, passages, rerankers: list[str]) -> dict:
    res = {"family": family, "device": device, "passages": len(passages)}
    texts = [p.embed_text() for p in passages]
    with Peak() as pk:
        t = time.time()
        emb = models.Embedder(family, device)
        res["embed_load_s"] = round(time.time() - t, 1)
        t = time.time()
        vecs = emb.encode(texts)
        res["index_s"] = round(time.time() - t, 1)
        res["passages_per_s"] = round(len(texts) / max(res["index_s"], 1e-6), 1)
        t = time.time()
        qv = emb.encode([q for q, _ in QUERIES], query=True)
        res["query_embed_ms"] = round((time.time() - t) / len(QUERIES) * 1000)
    res["embed_peak_rss_gib"] = round(pk.rss / 2**30, 2)
    res["embed_peak_vram_gib"] = round(pk.vram_gib(), 2)
    emb.close()
    sims = qv @ vecs.T
    tops = [list(np.argsort(-s)[:RERANK_K]) for s in sims]
    res["embed_only"] = metrics([hit_rank(passages, top, g) for top, (_, g) in zip(tops, QUERIES)])
    res["misses"] = [q for top, (q, g) in zip(tops, QUERIES) if not hit_rank(passages, top, g)]
    for rr in rerankers:
        with Peak() as pk:
            t = time.time()
            rk = models.Reranker(rr, device)
            load = time.time() - t
            t = time.time()
            ranks = []
            for top, (q, g) in zip(tops, QUERIES):
                sc = rk.score(q, [texts[i] for i in top])
                order = [top[i] for i in np.argsort(-np.array(sc))]
                ranks.append(hit_rank(passages, order, g))
            per_q = (time.time() - t) / len(QUERIES)
        rk.close()
        res[f"rerank_{rr}"] = {"load_s": round(load, 1), "per_query_s": round(per_q, 2),
                               "peak_rss_gib": round(pk.rss / 2**30, 2), "peak_vram_gib": round(pk.vram_gib(), 2),
                               **metrics(ranks)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--families", default="qwen3,bge")
    ap.add_argument("--rerankers", default="qwen3,bge")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.threads:
        import torch
        torch.set_num_threads(a.threads)
    passages = load_corpus(a.limit)
    print(f"{len(passages)} passages", flush=True)
    results = []
    for fam in a.families.split(","):
        r = run(fam, a.device, passages, a.rerankers.split(","))
        print(json.dumps(r, indent=1), flush=True)
        results.append(r)
    if a.out:
        Path(a.out).write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
