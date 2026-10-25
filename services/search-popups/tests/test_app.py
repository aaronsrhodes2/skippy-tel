"""search_memory's door: arguments are checked, a search refreshes then embeds then reranks (models faked here: the real ones are
measured in docs/search-speech-popups.md), the models are always unloaded, and the HTTP contract is the pop-up one."""
import json
import sys
import threading
import types
import urllib.error
import urllib.request

import numpy as np
import pytest

import app
import index as index_mod
from test_index import fake_vec, turns_file


class FakeModels(types.ModuleType):
    def __init__(self):
        super().__init__("models")
        self.closed = []
        fake = self

        class Embedder:
            def __init__(self, family, device="auto", path=None):
                self.name = "embed"

            def encode(self, texts, query=False):
                return np.stack([fake_vec(t) for t in texts])

            def close(self):
                fake.closed.append(self.name)

        class Reranker:
            def __init__(self, family, device="auto", path=None):
                self.name = "rerank"

            def score(self, query, docs):                         # the reranker prefers the passage mentioning "rogue"
                return [0.9 if "rogue" in d else 0.1 for d in docs]

            def close(self):
                fake.closed.append(self.name)
        self.Embedder, self.Reranker = Embedder, Reranker


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeModels()
    monkeypatch.setitem(sys.modules, "models", fake)
    for name in ("agent", "memory", "mentat"):
        (tmp_path / name).mkdir()
    turns_file(tmp_path / "agent" / "2026-10-01_a.turns.json",
               "the lighthouse door had a wire trap on it", "the rogue cut the wire and waved the others forward")
    monkeypatch.setattr(app, "ROOTS", {n: tmp_path / n for n in ("agent", "memory", "mentat")})
    monkeypatch.setattr(app, "BRIDGE", "")
    monkeypatch.setattr(app, "_index", index_mod.Index(tmp_path / "s.db", "bge"))
    return fake


def test_check_args():
    assert app.check_args({"query": "  what about the lighthouse  "}) == ("what about the lighthouse", 6, None)
    assert app.check_args({"query": "x" * 10, "k": 3, "sources": ["mentat"]}) == ("x" * 10, 3, {"mentat"})
    for bad in ({}, {"query": "x"}, {"query": "ok?", "k": 0}, {"query": "ok?", "k": True}, {"query": "ok?", "sources": ["web"]},
                {"query": "ok?", "sources": []}):
        with pytest.raises(ValueError):
            app.check_args(bad)


def test_search_refreshes_reranks_and_unloads(svc):
    found = app.search("the lighthouse door trap", 2)
    assert found["timing"]["new_passages"] == 2
    assert "rogue" in found["results"][0]["text"]                 # the reranker's order, not the vectors'
    assert svc.closed == ["embed", "rerank"]                      # nothing stays on the card between searches
    text = app.as_text(found)
    assert text.startswith('The best 2 passages for "the lighthouse door trap"')
    assert "private Skippy conversation" in text and "source: 2026-10-01_a.turns.json" in text


def test_nothing_found_says_so(svc):
    assert "Nothing in the Captain's" in app.as_text(app.search("anything", 3, {"mentat"}))


@pytest.fixture
def server(svc, monkeypatch):
    weights = app.ROOTS["agent"].parent / "w"
    for d in ("bge-m3", "bge-reranker-v2-m3"):
        (weights / d).mkdir(parents=True)
        (weights / d / "model.safetensors").write_bytes(b"x")
    monkeypatch.setattr(app, "EMBED_DIR", weights / "bge-m3")
    monkeypatch.setattr(app, "RERANK_DIR", weights / "bge-reranker-v2-m3")
    srv = app.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get_content_type(), e.read()


def test_http_contract(server):
    with urllib.request.urlopen(server + "/health") as r:
        h = json.loads(r.read())
    assert h["ready"] is True and h["loaded"] is False and set(h["sources"]) == {"agent", "memory", "mentat"}
    status, ctype, body = post(server + "/run", {"args": {"query": "lighthouse trap"}})
    assert status == 200 and ctype == "text/plain" and b"rogue" in body
    status, ctype, body = post(server + "/run", {"args": {"query": "lighthouse trap", "format": "json"}})
    assert status == 200 and ctype == "application/json" and json.loads(body)["results"]
    status, _, body = post(server + "/run", {"args": {}})
    assert status == 400 and "query" in json.loads(body)["error"]
    status, ctype, body = post(server + "/refresh", {})
    assert status == 200 and json.loads(body)["passages"] == {"agent": 2}


def test_not_ready_without_weights(svc, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "EMBED_DIR", tmp_path / "nope")
    assert app.health()["ready"] is False


def test_the_question_is_never_logged(svc, capsys):
    app.search("my secret question about the lighthouse", 1)
    assert "secret" not in capsys.readouterr().out
