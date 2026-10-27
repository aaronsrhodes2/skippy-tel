"""The node catalog (portal/status_nodes.json): every node gets what it is for and what uses it, usage numbers ride in from the machine's report, and
what a machine sends can't break it."""
import json
from pathlib import Path

import pytest

pytest.importorskip("flask")

import status as status_mod  # noqa: E402


def tree(st):
    return st.tree(home_online=True, home_age=5, home_report={}, starter_view={"status": {}}, portal={"short": "abc1234", "revision": "r1"},
                   games_vm={"status": "TERMINATED"}, core_verdict=None)


def test_keys_drop_the_project_prefix_and_the_replica_number():
    assert [status_mod.node_key(n) for n in ("skippycore-mentat-1", "skippycore-tesla-db-1", "skippytel-flask-api", "chat")] == ["mentat", "tesla-db", "flask-api", "chat"]


def test_every_entry_says_what_it_is_for_and_what_uses_it():
    nodes = json.loads((Path(status_mod.__file__).parent / "status_nodes.json").read_text(encoding="utf-8"))
    for key, e in nodes.items():
        if key.startswith("_"):
            continue
        assert len(e["for"]) > 30, key
        assert e["used_by"] and all(isinstance(u, str) for u in e["used_by"]), key
        if "example" in e:
            assert e["example"]["ask"] and e["example"]["answer"], key


def test_core_nodes_carry_their_card_and_mentat_carries_its_usage_numbers():
    st = status_mod.Status(clock=lambda: 1000.0)
    st.accept_core({"containers": [{"name": "skippycore-mentat-1", "state": "running"}, {"name": "skippycore-unknown-1", "state": "running"}],
                    "models": [{"name": "chat", "state": "ready"}],
                    "usage": {"mentat": {"asks": 958, "direct": 31, "tools_called": 13, "tools_total": 221, "schema_tokens": 73317, "since": "2026-10", "evil": "x"},
                              "junk": "no", "other": {"asks": "NaN"}}})
    core = next(m for m in tree(st)["machines"] if m["id"] == "skippy-core")
    mentat = next(c for c in core["containers"] if c["name"] == "skippycore-mentat-1")
    assert "physics" in mentat["about"]["for"].lower() and mentat["about"]["used_by"]
    assert mentat["about"]["usage"] == {"asks": 958.0, "direct": 31.0, "tools_called": 13.0, "tools_total": 221.0, "schema_tokens": 73317.0, "since": "2026-10"}
    assert next(c for c in core["containers"] if c["name"] == "skippycore-unknown-1")["about"] is None            # no entry: no card text, no error
    assert next(s for s in core["serves"] if s["name"] == "chat")["about"]["for"]
    assert all("about" in s for s in core["serves"])


def test_a_bad_usage_block_is_ignored():
    st = status_mod.Status(clock=lambda: 1000.0)
    st.accept_core({"containers": [], "models": [], "usage": "nope"})
    st.accept_core({"containers": [], "models": [], "usage": {"mentat": [1, 2]}})


ROOT = Path(status_mod.__file__).resolve().parents[1]


def test_every_node_the_dev_fixture_shows_has_a_card():
    import dev_status
    from test_app import ADMIN, login
    client = dev_status.build_app().test_client()
    login(client, ADMIN)
    state = client.get("/status/state").get_json()
    missing = [m["id"] for m in state["machines"] if not m.get("about")]
    for m in state["machines"]:
        missing += [f"{m['id']}/{p['name']}" for p in m["containers"] + m["serves"] if not p.get("about")]
    assert not missing, missing


def compose_services(path: Path) -> list[str]:
    """The service names in a compose file (no YAML library needed: the two-space keys under `services:`)."""
    names, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            inside = line.rstrip().startswith("services:")
        elif inside and line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":") and not line.lstrip().startswith("#"):
            names.append(line.strip()[:-1])
    return names


def test_every_container_and_popup_that_can_appear_has_a_card():
    files = [ROOT / "docker-compose.yml", ROOT / "docker-compose.override.yml", ROOT / "core/popups-3d/compose.yml", *sorted((ROOT / "core").glob("compose*.yml"))]
    names = [n for f in files if f.is_file() for n in compose_services(f)]
    assert "flask-api" in names and "mentat" in names                    # the reader works
    for path in [ROOT / "services/popups/registry.json", *sorted(ROOT.glob("services/*-popups/popups.json"))]:
        names += [p["name"] for p in json.loads(path.read_text(encoding="utf-8")).get("popups") or []]
    names += ["make_music", "make_image", "make_image_hq", "workshop", "music_gate", "video_gate", "voice_gate", "house", "car",   # the reporter's built-in capabilities
              "ollama", "whisper", "mentat-home", "bridge", "music-studio", "image-studio"]                                     # the doctor's checks
    assert not [n for n in names if status_mod.about(n) is None]


def test_every_node_on_every_machine_carries_its_card():
    st = status_mod.Status(clock=lambda: 1000.0)
    st.accept_core({"containers": [{"name": "skippycore-llm-1", "state": "running"}], "models": []})
    t = st.tree(home_online=True, home_age=5, home_report={"capabilities": {"make_image": {"ready": True}}, "services": [{"name": "Bilby", "available": True}],
                                                          "doctor": {"whisper": {"ok": True, "state": "ready"}}},
                starter_view={"status": {"containers": [{"name": "skippytel-voice-gate", "state": "running"}]}}, portal={"short": "abc"},
                games_vm={"status": "RUNNING"}, core_verdict=None)
    for m in t["machines"]:
        assert m["about"]["for"], m["id"]
        for p in m["containers"] + m["serves"]:
            assert p["about"]["for"], (m["id"], p["name"])
