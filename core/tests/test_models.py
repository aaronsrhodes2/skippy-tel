"""The slot tool's promises: the manifest is validated, CORE_FULL picks variants (an unknown slot is an error), the
memory budget counts residents plus the biggest on-demand slot, check refuses missing/partial/wrong files, and render
gives llama-swap one persistent group for residents and one swap group for the rest."""
import hashlib
import json

import pytest

from core_testlib import load

m = load("llm/models.py", "core_llm_models")


def _manifest(tmp_path, **over):
    data = {
        "memory_budget_gb": 10,
        "slots": {
            "chat": {"resident": True, "aliases": ["qwen3:8b"],
                     "standin": {"repo": "Org/Chat-Tiny", "files": [{"path": "chat.gguf", "size": 4, "sha256": None}], "ctx": 2048},
                     "full": {"repo": "Org/Chat-Big", "files": [{"path": "big.gguf", "size": 3 * 1024 ** 3, "sha256": None}]}},
            "coder": {"resident": False, "aliases": ["skippy-coder"],
                      "standin": {"repo": "Org/Code-Tiny", "files": [{"path": "code.gguf", "size": 5, "sha256": None}]},
                      "full": {"repo": "Org/Code-Big", "files": [{"path": "p/a-00001-of-00002.gguf", "size": 1024 ** 3},
                                                                  {"path": "p/a-00002-of-00002.gguf", "size": 1024 ** 3}]}},
        },
    }
    data.update(over)
    p = tmp_path / "models.json"
    p.write_text(json.dumps(data))
    return p


def _put(root, chosen, slot, data: bytes):
    for f in chosen[slot]["files"]:
        path = root / f["rel"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def test_the_real_manifest_loads_and_its_standins_fit():
    data = m.load()
    chosen = m.plan(data, set())
    assert set(chosen) == {"chat", "vision", "general", "coder"}
    assert all(s["variant"] == "standin" for s in chosen.values())
    assert m.budget(data, chosen)["fits"]
    assert m.budget(data, m.plan(data, m.parse_full("all", data["slots"])))["fits"], "the full tier must fit the box"
    assert "qwen3:8b" in data["slots"]["chat"]["aliases"], "today's default model name must reach the chat slot"


@pytest.mark.parametrize("breakage, message", [
    (lambda d: d["slots"]["chat"]["standin"].pop("repo"), "repo and files"),
    (lambda d: d["slots"]["chat"]["standin"]["files"][0].update(size=0), "positive size"),
    (lambda d: d["slots"]["chat"]["standin"]["files"][0].update(path="../escape.gguf"), "under the repo folder"),
    (lambda d: d["slots"]["chat"]["standin"]["files"][0].update(sha256="ABC"), "64 lowercase hex"),
    (lambda d: d["slots"]["coder"]["aliases"].append("qwen3:8b"), "used by both"),
    (lambda d: d.update(slots={}), "no slots"),
    (lambda d: d["slots"]["chat"]["full"].update(parallel=9), "parallel must be"),
])
def test_a_broken_manifest_is_refused(tmp_path, breakage, message):
    data = json.loads(_manifest(tmp_path).read_text())
    breakage(data)
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(data))
    with pytest.raises(m.ManifestError, match=message):
        m.load(p)


def test_core_full_parsing(tmp_path):
    slots = m.load(_manifest(tmp_path))["slots"]
    assert m.parse_full("", slots) == set() and m.parse_full(None, slots) == set()
    assert m.parse_full("all", slots) == {"chat", "coder"}
    assert m.parse_full(" chat , ", slots) == {"chat"}
    with pytest.raises(m.ManifestError, match="unknown slot"):
        m.parse_full("chat,general", slots)


def test_plan_paths_follow_the_archive_layout(tmp_path):
    chosen = m.plan(m.load(_manifest(tmp_path)), {"coder"})
    assert chosen["chat"]["files"][0]["rel"] == "huggingface/Org__Chat-Tiny/chat.gguf"
    assert chosen["coder"]["variant"] == "full" and chosen["coder"]["size"] == 2 * 1024 ** 3
    assert chosen["coder"]["files"][1]["rel"] == "huggingface/Org__Code-Big/p/a-00002-of-00002.gguf"


def test_budget_is_residents_plus_the_biggest_on_demand(tmp_path):
    data = m.load(_manifest(tmp_path))
    b = m.budget(data, m.plan(data, {"chat", "coder"}))          # 3 + 4 overhead, + 2 + 4
    assert b["peak_gb"] == 13.0 and not b["fits"]
    assert m.budget(data, m.plan(data, set()))["fits"]


def test_check_reports_missing_partial_and_wrong_hash(tmp_path):
    root = tmp_path / "models"
    data = m.load(_manifest(tmp_path))
    chosen = m.plan(data, set())
    assert any("missing" in p for p in m.check(chosen, root))
    _put(root, chosen, "chat", b"1234")
    _put(root, chosen, "coder", b"123")                          # 3 of 5 bytes: a partial download
    problems = m.check(chosen, root)
    assert len(problems) == 1 and "partial download" in problems[0] and "coder" in problems[0]
    _put(root, chosen, "coder", b"12345")
    assert m.check(chosen, root) == []
    chosen["chat"]["files"][0]["sha256"] = "0" * 64
    assert m.check(chosen, root) == [], "a pinned hash is read only with --hash"
    assert "sha256" in m.check(chosen, root, hash_files=True)[0]
    chosen["chat"]["files"][0]["sha256"] = hashlib.sha256(b"1234").hexdigest()
    assert m.check(chosen, root, hash_files=True) == []


def test_render_groups_and_names(tmp_path):
    chosen = m.plan(m.load(_manifest(tmp_path)), {"coder"})
    config, shim = m.render(chosen, gpu_layers=0, extra_args="--api-key k")
    assert config["groups"]["resident"] == {"swap": False, "exclusive": False, "persistent": True, "members": ["chat"]}
    assert config["groups"]["on-demand"]["swap"] is True and config["groups"]["on-demand"]["members"] == ["coder"]
    chat, coder = config["models"]["chat"], config["models"]["coder"]
    assert "ttl" not in chat and coder["ttl"] == m.IDLE_TTL_S
    assert "-fa on --load-mode none" in chat["cmd"] and "-fa on --load-mode none" in coder["cmd"], "Strix Halo needs both, every slot"
    assert "--parallel 4 " in chat["cmd"]
    assert "-ngl 0" in chat["cmd"] and "--device none" in chat["cmd"], "no GPU: keep a Vulkan build off every device"
    assert "--device" not in m.render(chosen, gpu_layers=999)[0]["models"]["chat"]["cmd"]
    assert config["hooks"] == {"on_startup": {"preload": ["chat"]}}, "the resident slot loads at start, not on the first reply"
    assert "--api-key k" in chat["cmd"] and "--ctx-size 2048" in chat["cmd"] and "--port ${PORT} " in chat["cmd"]
    assert "/models/huggingface/Org__Code-Big/p/a-00001-of-00002.gguf" in coder["cmd"], "a split GGUF starts at part 1"
    assert chat["aliases"] == ["qwen3:8b"]
    assert "apiKeys" not in config, "no key asked for: an open port (the simulator)"
    keyed = m.render(chosen, 0, api_key=True)[0]
    assert keyed["apiKeys"] == ["${env.LLM_API_KEY}"], "llama-swap reads the key from its environment, never the file"
    import shlex
    with_json = m.render({**chosen, "chat": {**chosen["chat"], "args": ["--chat-template-kwargs", '{"enable_thinking": false}']}}, 0)[0]
    assert shlex.split(with_json["models"]["chat"]["cmd"])[-4:-2] == ["--chat-template-kwargs", '{"enable_thinking": false}']
    names = {n["name"]: n for n in shim["models"]}
    assert set(names) == {"chat", "qwen3:8b", "coder", "skippy-coder"}
    assert names["qwen3:8b"]["slot"] == "chat" and names["skippy-coder"]["variant"] == "full"
    assert names["qwen3:8b"]["resident"] is True and names["coder"]["resident"] is False


def test_cli_render_refuses_missing_files_and_writes_when_ready(tmp_path, capsys):
    manifest, root, out = _manifest(tmp_path), tmp_path / "models", tmp_path / "out"
    args = ["render", "--manifest", str(manifest), "--root", str(root), "--full", "", "--out", str(out)]
    assert m.main(args) == 1 and not out.exists()
    chosen = m.plan(m.load(manifest), set())
    _put(root, chosen, "chat", b"1234")
    _put(root, chosen, "coder", b"12345")
    assert m.main(args) == 0
    assert json.loads((out / "config.yaml").read_text())["models"]["chat"]
    assert json.loads((out / "shim.json").read_text())["models"]
    assert m.main(["plan", "--manifest", str(manifest), "--full", "all"]) == 1, "the full tier is over this budget"
    assert m.main(["plan", "--manifest", str(manifest), "--full", "nope"]) == 2


def test_bench_candidates_serve_beside_the_slots_on_demand(tmp_path):
    cand = {"try": {"for": ["chat", "coder"], "repo": "Org/New-Model",
                    "files": [{"path": "new.gguf", "size": 2 * 1024 ** 3, "sha256": None}], "ctx": 8192}}
    data = m.load(_manifest(tmp_path, candidates={"_doc": "notes", **cand}))
    chosen = {**m.plan(data, set()), **m.bench(data, "try")}
    assert chosen["try"]["variant"] == "candidate" and chosen["try"]["resident"] is False
    assert chosen["try"]["files"][0]["rel"] == "huggingface/Org__New-Model/new.gguf"
    config, _ = m.render(chosen, gpu_layers=0)
    assert config["groups"]["on-demand"]["members"] == ["coder", "try"], "a candidate swaps with the coder"
    assert "--alias try" in config["models"]["try"]["cmd"] and config["models"]["try"]["ttl"] == m.IDLE_TTL_S
    assert m.bench(data, "") == {}
    with pytest.raises(m.ManifestError):
        m.bench(data, "nope")
    with pytest.raises(m.ManifestError):
        m.load(_manifest(tmp_path, candidates={"bad": {**cand["try"], "for": ["nope"]}}))
    with pytest.raises(m.ManifestError):
        m.load(_manifest(tmp_path, candidates={"chat": cand["try"]}))
