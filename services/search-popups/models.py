"""The two models a search uses, each loaded only while it is needed: an embedder (meaning -> a vector) and a reranker
(question + passage -> how well the passage answers it).

Two families, both open, both read from the models archive (D:/models/huggingface, offline):
  * Qwen3-Embedding-0.6B + Qwen3-Reranker-0.6B (Apache 2.0): last-token pooling with an instruction on the query side;
    the reranker is a small causal LM asked "yes" or "no" and scored by P(yes).
  * BAAI bge-m3 + bge-reranker-v2-m3 (MIT): CLS pooling; the reranker is a cross-encoder with one logit.

Plain transformers + torch (no sentence-transformers), so the same code runs on the CPU or the GPU. Vectors come back
L2-normalised float32, so a dot product is the cosine.
"""
from __future__ import annotations

import gc
import os
from pathlib import Path

import numpy as np

MODELS_ROOT = Path(os.environ.get("MODELS_ROOT", "D:/models")) / "huggingface"
QUERY_TASK = "Given a question the Captain asks about his own past conversations, notes and research, retrieve the passages that answer it"

FAMILIES = {
    "qwen3": {"embed": "Qwen__Qwen3-Embedding-0.6B", "rerank": "Qwen__Qwen3-Reranker-0.6B"},
    "bge": {"embed": "BAAI__bge-m3", "rerank": "BAAI__bge-reranker-v2-m3"},
}


def warm(path: Path) -> None:
    """Read the weights file once, in order, before loading it. Docker's disk image sits on skippy-pc's D: hard drive, and transformers
    loads safetensors through mmap from several threads at once: cold, those scattered reads took over 4 minutes for a 1.1 GB file that
    reads straight through in 29 s (and in 0.5 s once it is in the page cache)."""
    for f in sorted(Path(path).glob("*.safetensors")):
        with open(f, "rb", buffering=0) as fh:
            while fh.read(16 << 20):
                pass


def _torch():
    import torch
    return torch


def pick_device(want: str = "auto") -> str:
    torch = _torch()
    if want == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return want


def _dtype(device: str):
    torch = _torch()
    return torch.float16 if device == "cuda" else torch.float32


class Embedder:
    def __init__(self, family: str, device: str = "auto", path: Path | None = None, max_len: int = 512):
        from transformers import AutoModel, AutoTokenizer
        self.family, self.device, self.max_len = family, pick_device(device), max_len
        path = path or MODELS_ROOT / FAMILIES[family]["embed"]
        warm(path)
        side = "left" if family == "qwen3" else "right"
        self.tok = AutoTokenizer.from_pretrained(str(path), padding_side=side)
        self.model = AutoModel.from_pretrained(str(path), dtype=_dtype(self.device), device_map=self.device).eval()

    def _pool(self, out, mask):
        torch = _torch()
        h = out.last_hidden_state
        if self.family == "qwen3":                     # left padding: the last position is every row's last real token
            v = h[:, -1]
        else:                                          # bge-m3: the CLS token
            v = h[:, 0]
        return torch.nn.functional.normalize(v.float(), dim=-1)

    def encode(self, texts: list[str], query: bool = False, batch: int = 16) -> np.ndarray:
        torch = _torch()
        if query and self.family == "qwen3":
            texts = [f"Instruct: {QUERY_TASK}\nQuery:{t}" for t in texts]
        out = []
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))   # similar lengths together: less padding
        with torch.inference_mode():
            for i in range(0, len(order), batch):
                idx = order[i:i + batch]
                enc = self.tok([texts[j] for j in idx], padding=True, truncation=True, max_length=self.max_len,
                               return_tensors="pt").to(self.device)
                vec = self._pool(self.model(**enc), enc["attention_mask"]).cpu().numpy()
                out.extend(zip(idx, vec))
        out.sort(key=lambda p: p[0])
        return np.stack([v for _, v in out]).astype(np.float32) if out else np.zeros((0, 1024), np.float32)

    def close(self):
        _free(self)


class Reranker:
    def __init__(self, family: str, device: str = "auto", path: Path | None = None, max_len: int = 1024):
        from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer
        self.family, self.device, self.max_len = family, pick_device(device), max_len
        path = path or MODELS_ROOT / FAMILIES[family]["rerank"]
        warm(path)
        if family == "qwen3":
            self.tok = AutoTokenizer.from_pretrained(str(path), padding_side="left")
            self.model = AutoModelForCausalLM.from_pretrained(str(path), dtype=_dtype(self.device), device_map=self.device).eval()
            self.yes = self.tok.convert_tokens_to_ids("yes")
            self.no = self.tok.convert_tokens_to_ids("no")
            self.prefix = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the "
                           "Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n")
            self.suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        else:
            self.tok = AutoTokenizer.from_pretrained(str(path))
            self.model = AutoModelForSequenceClassification.from_pretrained(
                str(path), dtype=_dtype(self.device), device_map=self.device).eval()

    def score(self, query: str, docs: list[str], batch: int = 8) -> list[float]:
        torch = _torch()
        scores: list[float] = []
        with torch.inference_mode():
            for i in range(0, len(docs), batch):
                part = docs[i:i + batch]
                if self.family == "qwen3":
                    pairs = [f"{self.prefix}<Instruct>: {QUERY_TASK}\n<Query>: {query}\n<Document>: {d}{self.suffix}"
                             for d in part]
                    enc = self.tok(pairs, padding=True, truncation=True, max_length=self.max_len,
                                   return_tensors="pt").to(self.device)
                    logits = self.model(**enc).logits[:, -1, :]
                    two = torch.stack([logits[:, self.no], logits[:, self.yes]], dim=1).float()
                    scores += torch.softmax(two, dim=1)[:, 1].cpu().tolist()
                else:
                    enc = self.tok([query] * len(part), part, padding=True, truncation=True, max_length=self.max_len,
                                   return_tensors="pt").to(self.device)
                    scores += torch.sigmoid(self.model(**enc).logits.view(-1).float()).cpu().tolist()
        return scores

    def close(self):
        _free(self)


def _free(obj) -> None:
    obj.model = None
    obj.tok = None
    gc.collect()
    torch = _torch()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
