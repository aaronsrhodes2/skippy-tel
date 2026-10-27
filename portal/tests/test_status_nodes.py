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
