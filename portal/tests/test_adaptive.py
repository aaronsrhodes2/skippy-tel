"""The portal tidies its own controls from what people tap (adaptive.py; skippy-pc's half is tools/adaptive_ui.py): the rules,
the weekly pace, the pins a hand-made revive leaves, the merge that is only ever a suggestion, and the home-token door."""
import json
import time

import pytest

import adaptive as A
import modules as modules_mod

NOW = 1_800_000_000.0
DAY = 86400


def stats(controls=None, surface=None, span=33, pairs=None):
    return {"window_days": 30, "span_days": span, "controls": controls or {},
            "surface_days": surface or {"chat": 27, "launcher": 27, "me": 7}, "pairs": pairs or []}


BUSY_CHAT = {"chat:apps": {"taps": 72}, "chat:new": {"taps": 26}, "chat:camera": {"taps": 7}, "chat:copy": {"taps": 6},
             "chat:notify": {"taps": 5}, "chat:tools": {"taps": 4}, "chat:attach": {"taps": 1, "last_days": 4.3}}


def test_the_least_used_chat_key_is_pruned_with_its_reason():
    out = A.decide(stats(BUSY_CHAT), frozenset(), A.empty_state(), NOW)
    assert [(c["kind"], c["id"]) for c in out] == [("retire", "key-attach")]
    assert out[0]["why"] == "1 tap in 30 days, the last 4 days ago, on screen 27 of them"


def test_one_change_per_surface_a_week():
    st = A.empty_state(); st["last"]["chat"] = NOW - 3 * DAY
    assert A.decide(stats(BUSY_CHAT), frozenset(), st, NOW) == []
    st["last"]["chat"] = NOW - 8 * DAY
    assert [c["id"] for c in A.decide(stats(BUSY_CHAT), frozenset(), st, NOW)] == ["key-attach"]


def test_never_with_too_little_history_and_never_a_pinned_or_floor_control():
    assert A.decide(stats(BUSY_CHAT, span=10), frozenset(), A.empty_state(), NOW) == []
    st = A.empty_state(); st["pins"] = ["key-attach"]
    assert [c["id"] for c in A.decide(stats(BUSY_CHAT), frozenset(), st, NOW)] == ["key-tools"]
    lonely = stats({"tile:skippy": {"seen_days": 20}, "tile:admin": {"seen_days": 20}}, surface={"launcher": 30})
    assert not [c for c in A.decide(lonely, frozenset(), A.empty_state(), NOW) if c["kind"] == "retire"]


def test_a_new_tile_waits_for_its_own_days_on_screen():
    # the night this began, Alarms was a day old: the launcher's 27 days in use must not stand in for its own
    tiles = lambda s: [c["id"] for c in A.decide(s, frozenset(), A.empty_state(), NOW) if c.get("surface") == "launcher"]   # noqa: E731
    assert tiles(stats()) == []
    assert tiles(stats({"tile:alarms": {"seen_days": 15}})) == ["alarms"]


def test_the_launcher_order_follows_use_one_move_a_week_and_skippy_stays_first():
    s = stats({"tile:games": {"taps": 9}, "tile:play": {"taps": 12}, "tray:play": {"taps": 8}, "tile:skippy": {"taps": 0}})
    out = [c for c in A.decide(s, frozenset(), A.empty_state(), NOW) if c["kind"] == "order"]
    assert len(out) == 1 and out[0]["id"] == "play" and out[0]["before"] == "games"
    st = A.empty_state(); st["last"]["launcher-order"] = NOW - DAY
    assert not [c for c in A.decide(s, frozenset(), st, NOW) if c["kind"] == "order"]
    assert A.launcher_order(A.empty_state())[0] == "skippy"


def test_a_merge_is_written_down_once_never_done():
    s = stats({"chat:tools": {"taps": 8}}, pairs=[["chat:tools", "tray:music", 6]])
    out = [c for c in A.decide(s, frozenset(), A.empty_state(), NOW) if c["kind"] == "merge"]
    assert out and out[0]["pair"] == ["chat:tools", "tray:music"] and "candidate" in out[0]["why"]
    st = A.empty_state(); st["ledger"] = [{"kind": "merge", "pair": ["chat:tools", "tray:music"]}]
    assert not [c for c in A.decide(s, frozenset(), st, NOW) if c["kind"] == "merge"]


class Vm:
    def __init__(self):
        self.md = {}

    def get_metadata(self, k):
        return self.md.get(k, "")

    def set_metadata(self, k, v):
        self.md[k] = v


def test_apply_retires_through_the_graveyard_and_keeps_a_ledger():
    vm = Vm(); mods = modules_mod.Modules(vm); ad = A.Adaptive(vm, mods)
    done = ad.apply(stats(BUSY_CHAT), now=NOW)
    assert [d["id"] for d in done] == ["key-attach"] and "key-attach" in mods.retired()
    led = ad.ledger()
    assert led[0]["kind"] == "retire" and led[0]["when"]
    # the next night inside the week: nothing more on the chat
    assert ad.apply(stats(BUSY_CHAT), now=NOW + DAY) == []


def test_what_he_approved_is_retired_now_and_counts_as_the_weeks_change():
    vm = Vm(); mods = modules_mod.Modules(vm); ad = A.Adaptive(vm, mods)
    done = ad.apply(stats(BUSY_CHAT), now=NOW, approved=["key-attach", "key-tools", "admin"])
    assert [d["id"] for d in done if d["kind"] == "retire"] == ["key-attach", "key-tools"]     # Admin can't be retired
    assert done[0]["why"].startswith("the Captain approved it")
    assert {"key-attach", "key-tools"} <= mods.retired() and "key-notify" not in mods.retired()


def test_a_hand_revive_pins_and_a_hand_retire_unpins():
    vm = Vm(); ad = A.Adaptive(vm, modules_mod.Modules(vm))
    ad.pin("key-attach")
    assert json.loads(vm.md[A.KEY])["pins"] == ["key-attach"]
    ad.unpin("key-attach")
    assert json.loads(vm.md[A.KEY])["pins"] == []


def test_an_unreadable_state_changes_nothing():
    vm = Vm(); vm.md[A.KEY] = "{not json"; mods = modules_mod.Modules(vm)
    with pytest.raises(modules_mod.Unavailable):
        A.Adaptive(vm, mods).apply(stats(BUSY_CHAT), now=NOW)
    assert not mods.retired()


# ── the doors ──
pytest.importorskip("flask")
pytest.importorskip("authlib")
import app as portal  # noqa: E402
from test_app import SETTINGS, FakeSkippy, FakeVm, _pair  # noqa: E402


def test_home_adaptive_takes_the_home_token_only_and_says_what_it_changed():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    c = app.test_client()
    assert c.post("/home/adaptive", json={"stats": stats(BUSY_CHAT)}).status_code == 401
    H = {"Authorization": f"Bearer {_pair(c)}"}
    assert c.post("/home/adaptive", json={"nope": 1}, headers=H).status_code == 400
    r = c.post("/home/adaptive", json={"stats": stats(BUSY_CHAT)}, headers=H)
    assert r.status_code == 200 and [x["id"] for x in r.get_json()["changes"]] == ["key-attach"]
    assert "key-attach" in app.extensions["modules"].retired()


def test_the_launcher_follows_the_adaptive_order():
    vm = FakeVm()
    vm.metadata[A.KEY] = json.dumps({"order": ["skippy", "tabletop", "games"]})
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy())
    assert app.extensions["adaptive"].order()[:3] == ["skippy", "tabletop", "games"]
