"""Pictures that take their time (pictures.py): skippy-core draws for quality, so a picture may arrive minutes or hours after the
chat asked, after the portal restarted; it is written down in the bucket as it starts, kept there when it arrives, served to its
owner only, and the chat's "drawing" card finds it."""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import chat_tools  # noqa: E402
import pictures as pictures_mod  # noqa: E402
import tabletop  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, _advertise, _pair, login  # noqa: E402

PNG = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + bytes(32)


def test_a_picture_is_written_down_kept_and_served_to_its_owner_only():
    clock = [1000.0]
    p = pictures_mod.Pictures(forever=lambda e: e == ADMIN, clock=lambda: clock[0])
    p.expect("job123", FRIEND, "a red barn")
    assert p.state("job123", FRIEND) == "drawing" and p.state("job123", ADMIN) == "unknown" and p.state("job123", ADMIN, True) == "drawing"
    assert p.keep("job123", PNG, "image/png", "barn") and p.state("job123", FRIEND) == "done"
    assert p.get("job123", FRIEND) == (PNG, "image/png") and p.get("job123", "someone@else.com") is None
    assert any(k.startswith("chat-pictures/guest/job123") for k in p.store.files) and not any("pending" in k for k in p.store.files)
    assert FRIEND not in json.dumps({k: v[0].decode("latin-1") for k, v in p.store.files.items()})     # never the address
    assert not p.keep("nojob99", PNG, "image/png") and not p.keep("job123", b"<svg/>", "image/svg+xml")
    p.expect("job456", ADMIN, "his picture")
    p.keep("job456", PNG, "image/png")
    assert any(k.startswith("chat-pictures/keep/job456") for k in p.store.files)              # the Captain's: kept like his chats
    p.expect("old789", FRIEND, "slow")
    clock[0] += pictures_mod.JOB_TTL + 1
    assert p.state("old789", FRIEND) == "unknown"                                            # given up on after 6 hours


def test_a_picture_that_outlives_the_portal_still_reaches_the_chat(monkeypatch):
    monkeypatch.setattr(chat_tools, "IMAGE_WAIT_S", 0.05)
    store = tabletop.MemoryStore()
    skippy = FakeSkippy()
    calls = []

    def reply(system, messages, tools=None, run_tool=None, model=None, force_tool=None):
        if tools and any(t["name"] == "make_image" for t in tools) and not calls:
            calls.append(json.loads(run_tool("make_image", {"prompt": "a red barn at dusk"})))
        return "Drawing it now."
    skippy.reply = reply
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy, pictures=pictures_mod.Pictures(store))
    client = app.test_client()
    token = _pair(client)
    _advertise(client, token, make_image=True)
    d = client.post("/chat/send", json={"message": "draw me a red barn"}).get_json()
    assert calls[0]["status"] == "drawing"
    (item,) = d["media"]
    assert item["kind"] == "image" and item["pending"] is True
    job = item["job_id"]
    assert client.get(f"/chat/media/{job}/state").get_json() == {"state": "drawing"}

    # the portal restarts (a deploy) while skippy-core is still drawing: a new app, the same bucket
    app2 = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), pictures=pictures_mod.Pictures(store))
    c2 = app2.test_client()
    token2 = _pair(c2)
    put = c2.put(f"/home/media/{job}?title=barn", data=PNG, headers={"Authorization": f"Bearer {token2}", "Content-Type": "image/png"})
    assert put.status_code == 200
    login(c2, ADMIN)
    assert c2.get(f"/chat/media/{job}/state").get_json() == {"state": "done"}
    got = c2.get(f"/chat/media/{job}")
    assert got.status_code == 200 and got.data == PNG and got.headers["Content-Type"] == "image/png"
    login(c2, FRIEND)
    assert c2.get(f"/chat/media/{job}").status_code == 404 and c2.get(f"/chat/media/{job}/state").get_json() == {"state": "unknown"}
    assert c2.put("/home/media/neverasked1", data=PNG, headers={"Authorization": f"Bearer {token2}", "Content-Type": "image/png"}).status_code == 404


def test_the_chat_keeps_a_pictures_place_in_the_history_and_its_card_waits():
    from pathlib import Path
    page = (Path(portal.__file__).parent / "templates" / "_chat.html").read_text(encoding="utf-8")
    assert "pictureNote(data.media)" in page and "[picture:" in page and "function pictureCard" in page
    assert "chat.media_state" in page
