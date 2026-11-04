"""The permanent Home chat (housechat.py): the hub's sync, what the Captain types, his off switch, and that it is Captain-only and
cannot be reached through the chats the page saves, renames, expires or deletes."""
import hashlib
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import home as home_mod  # noqa: E402
import housechat  # noqa: E402
import tabletop  # noqa: E402
from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS, _add_friend, login  # noqa: E402

import app as portal  # noqa: E402

TOKEN = "the-home-token"
HUB = {"Authorization": "Bearer " + TOKEN}


class Clock:
    now = 1_800_000_000.0

    def __call__(self):
        return self.now


def make(clock=None):
    return housechat.HouseChat(tabletop.MemoryStore(), clock=clock or Clock())


def ev(kind="heard", room="office", **kw):
    return {"kind": kind, "room": room, "at": Clock.now, **kw}


def test_the_hubs_events_are_kept_in_order_and_cleaned():
    c = make()
    c.sync({"rooms": ["office"], "events": [
        ev("heard", text="  turn on the lamp "), ev("actions", tools=["home: Desk Lamp", 5]), ev("said", text="Done.", mood="m", typed=True, junk="x"),
        {"kind": "heard", "room": "bad room!", "text": "x"}, {"kind": "audio", "room": "office", "text": "x"}, ev("heard", text="   "), "nonsense"]})
    got = c.view()["events"]
    assert [(e["kind"], e.get("text") or e.get("tools")) for e in got] == [("heard", "turn on the lamp"), ("actions", ["home: Desk Lamp"]), ("said", "Done.")]
    assert got[2]["mood"] == "m" and got[2]["typed"] is True and "junk" not in got[2]
    assert [e["seq"] for e in got] == [1, 2, 3]
    assert [e["seq"] for e in c.view(after=2)["events"]] == [3]
    assert c.view()["rooms"] == ["office"] and c.view()["hub"] is True


def test_it_survives_a_restart_and_is_capped():
    store = tabletop.MemoryStore()
    a = housechat.HouseChat(store, clock=Clock())
    a.sync({"events": [ev("heard", text=f"n{i}") for i in range(3)]})
    b = housechat.HouseChat(store, clock=Clock())
    assert [e["text"] for e in b.view()["events"]] == ["n0", "n1", "n2"]
    b.sync({"events": [ev("heard", text="n3")]})
    assert b.view()["seq"] == 4                                     # numbering carries on across the restart
    housechat.MAX_EVENTS, keep = 3, housechat.MAX_EVENTS
    try:
        b.sync({"events": [ev("heard", text="n4")]})
        assert [e["text"] for e in housechat.HouseChat(store, clock=Clock()).view()["events"]] == ["n2", "n3", "n4"]
    finally:
        housechat.MAX_EVENTS = keep


def test_typed_say_shows_the_words_marked_typed_and_goes_to_the_hub_once():
    c = make()
    c.sync({"rooms": ["office", "den"], "events": []})
    c.send("say", "what time is it", "den", True)
    [e] = c.view()["events"]
    assert (e["kind"], e["room"], e["text"], e["typed"]) == ("heard", "den", "what time is it", True)
    out = c.sync({"events": []})
    assert out["commands"] == [{"id": out["commands"][0]["id"], "kind": "say", "room": "den", "text": "what time is it", "speak": True}]
    assert c.sync({"events": []})["commands"] == []                 # handed over once


def test_make_skippy_say_is_a_command_and_with_speech_off_only_a_line_in_the_thread():
    c = make()
    c.sync({"rooms": ["office"], "events": []})
    c.send("announce", "Dinner is ready.", "*", True)
    assert c.view()["events"] == []                                 # the hub reports it when it speaks it
    assert c.sync({"events": []})["commands"][0]["kind"] == "announce"
    c.send("announce", "Quietly.", "office", False)
    assert c.sync({"events": []})["commands"] == []
    [e] = c.view()["events"]
    assert e["kind"] == "announce" and e["silent"] and e["typed"]


def test_bad_typed_messages_are_refused():
    c = make()
    for args in (("shout", "x", "office", True), ("say", "  ", "office", True), ("say", "x", "bad room!", True), ("say", "x", "", True)):
        with pytest.raises(ValueError):
            c.send(*args)


def test_the_off_switch_stops_forwarding_says_so_and_drops_what_still_arrives():
    c = make()
    c.sync({"events": [ev("heard", text="before")]})
    c.set_forward(False)
    assert c.sync({"events": [ev("heard", text="late")]})["forward"] is False
    c.send("say", "hello", "office", True)
    assert c.sync({"events": []})["commands"] == []                 # not sent to the hub either
    texts = [e["text"] for e in c.view()["events"]]
    assert "late" not in texts and any("Forwarding is off" in t for t in texts)
    c.set_forward(True)
    assert c.sync({"events": []})["forward"] is True
    assert housechat.HouseChat(c.store, clock=Clock()).view()["forward"] is True    # kept across a restart


# ── the routes ──
@pytest.fixture
def world():
    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}))
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy(), home=home_mod.Home(vm))
    return app, app.test_client()


def test_only_the_hub_syncs_and_only_the_captain_reads_or_types(world):
    app, client = world
    assert client.post("/home/house/sync", json={}).status_code == 401
    assert client.post("/home/house/sync", json={"events": [ev("heard", text="hello")], "rooms": ["office"]}, headers=HUB).status_code == 200
    assert client.get("/home-chat/events").status_code in (302, 401)             # signed out
    _add_friend(client, "pal@example.com", ["skippy"])
    login(client, "pal@example.com")
    assert client.get("/home-chat/events").status_code == 403
    assert client.post("/home-chat/send", json={"mode": "say", "text": "x", "room": "office"}).status_code == 403
    assert client.post("/home-chat/forward", json={"on": False}).status_code == 403
    login(client, ADMIN)
    got = client.get("/home-chat/events").get_json()
    assert [e["text"] for e in got["events"]] == ["hello"] and got["rooms"] == ["office"] and got["forward"] is True
    assert client.post("/home-chat/send", json={"mode": "say", "text": "lights on", "room": "office"}).status_code == 200
    assert client.post("/home-chat/send", json={"mode": "say", "text": "", "room": "office"}).status_code == 400
    assert client.post("/home-chat/forward", json={"on": "no"}).status_code == 400
    reply = client.post("/home/house/sync", json={"events": []}, headers=HUB).get_json()
    assert reply["commands"][0]["text"] == "lights on" and reply["forward"] is True


def test_the_home_chat_is_not_in_the_chat_history_the_page_can_delete_or_expire(world):
    app, client = world
    login(client, ADMIN)
    client.post("/home/house/sync", json={"events": [ev("heard", text="hello")]}, headers=HUB)
    assert app.extensions["chat_history"].chats(ADMIN) == []
    assert client.delete("/talk/history/home").status_code in (200, 404)
    assert [e["text"] for e in client.get("/home-chat/events").get_json()["events"]] == ["hello"]


def test_the_drive_page_round_trip_a_silent_say_comes_back_as_the_typed_said_after_its_seq():
    """The car (portal/static/drive-app.js ask): send "say" to any room with speech off, the hub answers silently, and the page waits for the
    first typed "said" after the send's own seq (DrivePure.replyAfter)."""
    c = make()
    c.sync({"rooms": ["office"], "events": [ev("said", text="an older answer", typed=True)]})
    sent = c.send("say", "what's the weather", "*", False)
    cmd = c.sync({"events": []})["commands"][0]
    assert (cmd["kind"], cmd["room"], cmd["speak"]) == ("say", "*", False)            # the hub answers in its first room, out loud nowhere
    c.sync({"events": [ev("said", text="Rain by noon, Captain.", typed=True, silent=True)]})
    after = [e for e in c.view(after=sent["seq"] - 1)["events"] if e["seq"] > sent["seq"]]
    assert [(e["kind"], e["text"], e.get("typed")) for e in after] == [("said", "Rain by noon, Captain.", True)]


def test_a_slow_hub_clock_is_moved_onto_the_portals_by_the_time_it_reports():
    # 2026-11-04: skippy-pc's clock ran 18 s slow, so the replies were stamped "12:52" under the Captain's typed message stamped "12:53"
    clock = Clock()
    c = make(clock)
    c.sync({"rooms": ["office"], "events": []})
    c.send("say", "Play heavy metal", "office", True)                # stamped by the portal's own clock
    typed_at = clock.now
    clock.now += 7                                                   # the hub answers 7 s later in real time ...
    slow = clock.now - 18                                            # ... by a clock that is 18 s behind
    c.sync({"now": slow, "rooms": ["office"], "events": [{"kind": "actions", "room": "office", "at": slow - 3, "tools": ["music: heavy metal"]},
                                                        {"kind": "said", "room": "office", "at": slow - 1, "text": "Done."}]})
    typed, actions, said = c.view()["events"]
    assert typed["at"] == typed_at and typed["typed"] is True
    assert actions["at"] == clock.now - 3 and said["at"] == clock.now - 1 and typed["at"] < actions["at"] < said["at"]
    # no time sent (an older hub), a nonsense one, or a clock more than an hour off: the stamps are taken as they come
    for sent in ({}, {"now": "noon"}, {"now": True}, {"now": clock.now - 7200}):
        d = make(clock)
        d.sync({**sent, "events": [{"kind": "said", "room": "office", "at": clock.now - 5, "text": "x"}]})
        assert d.view()["events"][0]["at"] == clock.now - 5, sent
    d = make(clock)                                                  # a hub AHEAD of the portal is moved back, never into the future
    d.sync({"now": clock.now + 30, "events": [{"kind": "said", "room": "office", "at": clock.now + 28, "text": "x"}]})
    assert d.view()["events"][0]["at"] == clock.now - 2
