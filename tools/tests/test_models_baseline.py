"""The baseline downloader's promises: core before extended, a finished entry is never fetched again, the run stops
before the disk reserve, one model's failure never stops the others, and a gated repo is reported apart from a failure."""
import json

import pytest

import models_baseline as mb


def _data(*entries, reserve=200):
    return {"reserve_free_gb": reserve, "models": list(entries)}


def _e(repo, group="core", gb=1, **kw):
    return {"group": group, "kind": "llm", "repo": repo, "approx_gb": gb, **kw}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """fetch_repo writes a file instead of calling huggingface_hub; free space is whatever the test says."""
    calls, state = [], {"free": 10_000.0, "errors": {}}

    def fetch_repo(e, root, token):
        calls.append(e["repo"])
        if e["repo"] in state["errors"]:
            raise state["errors"][e["repo"]]
        d = mb.dest_of(e, root)
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.gguf").write_bytes(b"w")

    monkeypatch.setattr(mb, "fetch_repo", fetch_repo)
    monkeypatch.setattr(mb, "free_gb", lambda root: state["free"])
    monkeypatch.setattr(mb, "hf_token", lambda: None)
    return calls, state, tmp_path / "models"


def test_the_real_list_loads():
    data = mb.load_list()
    assert data["models"] and {e["group"] for e in data["models"]} == {"core", "extended"}
    assert len({mb.entry_key(e) for e in data["models"]}) == len(data["models"]), "two entries share a key"


@pytest.mark.parametrize("entry, message", [
    ({"group": "core", "repo": "a/b", "url": "http://x"}, "exactly one"),
    ({"group": "core", "url": "http://x"}, "needs a dest"),
    ({"group": "later", "repo": "a/b"}, "core or extended"),
])
def test_a_bad_list_is_refused(tmp_path, entry, message):
    p = tmp_path / "list.json"
    p.write_text(json.dumps({"models": [entry]}))
    with pytest.raises(ValueError, match=message):
        mb.load_list(p)


def test_core_first_then_extended_in_list_order():
    data = _data(_e("x/ext1", "extended"), _e("x/core1"), _e("x/ext2", "extended"), _e("x/core2"))
    assert [e["repo"] for e in mb.ordered(data, None)] == ["x/core1", "x/core2", "x/ext1", "x/ext2"]
    assert [e["repo"] for e in mb.ordered(data, "extended")] == ["x/ext1", "x/ext2"]


def test_a_finished_entry_is_never_fetched_again(fake):
    calls, _, root = fake
    data = _data(_e("x/a"), _e("x/b"))
    first = mb.run(data, root, None, log=lambda m: None)
    assert first["done"] == ["x/a", "x/b"] and calls == ["x/a", "x/b"]
    second = mb.run(data, root, None, log=lambda m: None)
    assert second["skipped"] == ["x/a", "x/b"] and second["done"] == [] and calls == ["x/a", "x/b"]


def test_done_is_per_entry_not_per_repo(fake):
    """The same repo twice (two quants): finishing one doesn't mark the other."""
    calls, _, root = fake
    a, b = _e("x/r", include=["q4.gguf"]), _e("x/r", "extended", include=["q8.gguf"])
    mb.run(_data(a), root, None, log=lambda m: None)
    assert mb.is_done(a, root) and not mb.is_done(b, root)
    mb.run(_data(a, b), root, None, log=lambda m: None)
    assert calls == ["x/r", "x/r"] and mb.is_done(b, root)


def test_it_stops_before_the_reserve(fake):
    calls, state, root = fake
    state["free"] = 210.0
    summary = mb.run(_data(_e("x/small", gb=5), _e("x/big", gb=50), _e("x/after", gb=1)), root, None, log=lambda m: None)
    assert calls == ["x/small"] and summary["done"] == ["x/small"]
    assert "x/big" in summary["stopped"] and "reserve 200" in summary["stopped"]


def test_gated_is_reported_failed_is_recorded_and_the_rest_carry_on(fake):
    calls, state, root = fake
    GatedRepoError = type("GatedRepoError", (Exception,), {})
    state["errors"] = {"x/gated": GatedRepoError("accept the licence"), "x/broken": RuntimeError("boom")}
    summary = mb.run(_data(_e("x/gated"), _e("x/broken"), _e("x/fine")), root, None, log=lambda m: None)
    assert calls == ["x/gated", "x/broken", "x/fine"]
    assert summary["gated"] == ["x/gated"] and summary["done"] == ["x/fine"]
    assert [f["name"] for f in summary["failed"]] == ["x/broken"] and "boom" in summary["failed"][0]["error"]
    assert not mb.is_done(_e("x/gated"), root) and not mb.is_done(_e("x/broken"), root)


def test_hf_token_from_the_vault_never_needs_the_environment(monkeypatch, tmp_path):
    vault = tmp_path / ".env"
    vault.write_text("OTHER=1\nHF_TOKEN='hf_abc'\n")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("VAULT_ENV", str(vault))
    assert mb.hf_token() == "hf_abc"
    monkeypatch.setenv("HF_TOKEN", "hf_env")
    assert mb.hf_token() == "hf_env"
    monkeypatch.delenv("HF_TOKEN")
    monkeypatch.setenv("VAULT_ENV", str(tmp_path / "missing"))
    assert mb.hf_token() is None
