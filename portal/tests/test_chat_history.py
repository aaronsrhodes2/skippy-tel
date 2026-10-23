"""Skippy's chats on the portal for 30 days (chat_history.py): each person reads and writes only their own file, keyed by who is
signed in; a chat untouched for 30 days is gone; only role/content turns are kept; size limits hold."""
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import chat_history  # noqa: E402
import tabletop  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402


@pytest.fixture
def kept():
    store = tabletop.MemoryStore()
    h = chat_history.History(store)
    client = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), history=h).test_client()
    return client, h, store


TURNS = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Obviously."}]


def test_each_person_has_their_own_chats(kept):
    client, _, store = kept
    login(client, FRIEND)
    assert client.put("/chat/history/abc123", json={"title": "hello", "history": TURNS}).status_code == 200
    assert [c["title"] for c in client.get("/chat/history").get_json()["chats"]] == ["chat-chat-chat-chat"]   # named (fallback)
    login(client, ADMIN)
    assert client.get("/chat/history").get_json()["chats"] == []                    # the Captain doesn't see a guest's chats
    client.put("/chat/history/abc123", json={"title": "mine", "history": [{"role": "user", "content": "deploy the portal"}]})
    login(client, FRIEND)                                                            # the same id was his own chat, not theirs
    assert [c["history"] for c in client.get("/chat/history").get_json()["chats"]] == [TURNS]
    assert sorted(store.files) == sorted(chat_history.PREFIX + chat_history.owner_id(e) + ".json" for e in (FRIEND, ADMIN))
    assert all(FRIEND not in p and ADMIN not in p for p in store.files)           # never the address itself


def test_forget_deletes_it_and_the_last_one_removes_the_file(kept):
    client, _, store = kept
    login(client, FRIEND)
    client.put("/chat/history/abc123", json={"title": "a", "history": TURNS})
    client.put("/chat/history/def456", json={"title": "b", "history": TURNS})
    client.delete("/chat/history/abc123")
    assert [c["id"] for c in client.get("/chat/history").get_json()["chats"]] == ["def456"]
    client.delete("/chat/history/def456")
    assert store.files == {}


def _old(store, email, chat_id="old111", days=31):
    old = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = chat_history.PREFIX + chat_history.owner_id(email) + ".json"
    store.put(path, json.dumps([{"id": chat_id, "title": "barn-gets-red-picture", "updated": old, "history": TURNS}]).encode(),
              "application/json")
    return path


def test_a_guests_chat_untouched_for_30_days_is_hidden_but_kept_for_its_analysis(kept):
    _, h, store = kept
    path = _old(store, FRIEND)
    assert h.chats(FRIEND) == []
    h.save(FRIEND, "new222", "y", TURNS)
    assert sorted(c["id"] for c in json.loads(store.get(path))) == ["new222", "old111"]     # still owed its analysis


def test_the_captains_chats_never_expire(kept):
    _, h, store = kept
    _old(store, ADMIN, days=400)
    assert [c["id"] for c in h.chats(ADMIN)] == ["old111"]


def test_the_sweep_analyses_records_then_deletes_and_skips_the_captain(kept):
    _, h, store = kept
    guest = _old(store, FRIEND)
    _old(store, ADMIN, days=400)
    h.save(FRIEND, "new222", "y", TURNS)
    seen = []
    analyse = chat_history.analyse_with(lambda system, text: (seen.append(text), '<think>hm</think>{"right": ["answered fast"], '
                                                              '"wrong": [], "bugs": ["the picture never came"], "improvements": []}')[1])
    done = h.sweep(analyse, h.record_insight, keep_owners={chat_history.owner_id(ADMIN)})
    assert done == 1 and "USER: hi" in seen[0]
    assert [c["id"] for c in json.loads(store.get(guest))] == ["new222"]                     # analysed, then gone
    assert [c["id"] for c in h.chats(ADMIN)] == ["old111"]                                    # the Captain's: untouched
    (month,) = [p for p in store.files if p.startswith(chat_history.INSIGHTS)]
    (item,) = json.loads(store.get(month))
    assert item["chat"] == "barn-gets-red-picture" and item["bugs"] == ["the picture never came"] and FRIEND not in json.dumps(item)


def test_when_core_cant_analyse_the_chat_waits(kept):
    _, h, store = kept
    guest = _old(store, FRIEND)

    def away(chat):
        raise RuntimeError("Skippy Core isn't there")
    with pytest.raises(RuntimeError):
        h.sweep(away, h.record_insight)
    assert [c["id"] for c in json.loads(store.get(guest))] == ["old111"]
    with pytest.raises(ValueError):
        chat_history.parse_insight("I'd rather not")


def test_the_report_reply_carries_insights_home_until_acknowledged(kept, monkeypatch):
    client, h, store = kept
    from test_app import _pair
    token = _pair(client)
    maybe, for_home, state = client.application.extensions["chat_sweep"]
    state["outbox"] = [{"at": "2026-10-23T10:00:00Z", "chat": "barn-gets-red-picture", "bugs": ["x"]}]
    send = lambda body: client.post("/home/report", json=body, headers={"Authorization": f"Bearer {token}"}).get_json()
    assert [i["chat"] for i in send({"status": {}})["insights"]] == ["barn-gets-red-picture"]
    assert "insights" not in send({"status": {}, "insights_ack": ["2026-10-23T10:00:00Z|barn-gets-red-picture"]})


def test_only_turns_are_kept_and_bad_input_is_a_400(kept):
    client, h, _ = kept
    login(client, FRIEND)
    client.put("/chat/history/abc123", json={"title": "t", "history": TURNS + [{"role": "system", "content": "evil"},
                                                                               {"role": "user", "content": "x", "secret": 1}]})
    (c,) = h.chats(FRIEND)
    assert c["history"] == TURNS + [{"role": "user", "content": "x"}]
    for bad in ("../etc", "A" * 30, "x"):
        assert client.put(f"/chat/history/{bad}", json={"title": "t", "history": TURNS}).status_code in (400, 404), bad
    assert client.put("/chat/history/abc123", json={"history": []}).status_code == 400
    big = [{"role": "user", "content": "x" * (chat_history.MAX_CHAT_BYTES + 1)}]
    assert client.put("/chat/history/abc123", json={"history": big}).status_code == 400


def test_at_most_40_chats_newest_kept(kept):
    _, h, _ = kept
    for i in range(45):
        h.save(FRIEND, f"chat{i:03d}", str(i), TURNS)
    assert len(h.chats(FRIEND)) == chat_history.MAX_CHATS


def test_no_bucket_says_so_and_the_page_keeps_its_own_copy():
    class Missing(tabletop.MemoryStore):
        def get(self, path):
            raise tabletop.StorageMissing("no bucket")
    client = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(),
                               history=chat_history.History(Missing())).test_client()
    login(client, FRIEND)
    r = client.get("/chat/history")
    assert r.status_code == 503 and "browser" in r.get_json()["error"]


def test_rename_and_star_keep_and_dont_count_as_using_the_chat(kept):
    client, h, _ = kept
    login(client, FRIEND)
    client.put("/chat/history/abc123", json={"title": "x", "history": TURNS})
    before = h.chats(FRIEND)[0]["updated"]
    r = client.patch("/chat/history/abc123", json={"title": "  My  reactor notes ", "star": True}).get_json()
    assert r == {"id": "abc123", "title": "My reactor notes", "star": True}
    client.put("/chat/history/abc123", json={"title": "the page's first words", "history": TURNS + TURNS})
    (c,) = h.chats(FRIEND)
    assert c["title"] == "My reactor notes" and c["star"] is True                      # a rename and a star survive later saves
    assert client.patch("/chat/history/abc123", json={"star": False}).get_json()["star"] is False
    assert h.chats(FRIEND)[0].get("star") is None and h.chats(FRIEND)[0]["updated"] >= before
    assert client.patch("/chat/history/abc123", json={"title": "   "}).status_code == 400
    assert client.patch("/chat/history/abc123", json={"star": "yes"}).status_code == 400
    assert client.patch("/chat/history/zzz999", json={"star": True}).status_code == 400
    login(client, ADMIN)
    assert client.patch("/chat/history/abc123", json={"star": True}).status_code == 400        # someone else's chat: no such chat
