"""Skippy-vision, one flash of everything (vision.py): a moment is a folder of files in the bucket, made by the page's own stills,
skippy-pc's screens (a `flash` job), the glasses' clip and the Tesla's page; the Captain's alone."""
import base64
import json
import time

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import tabletop  # noqa: E402
import vision  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, _pair, login  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 200
ZIP = b"PK\x03\x04" + b"z" * 200


def data_url(raw: bytes, mime="image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


@pytest.fixture
def env(monkeypatch):
    store = tabletop.MemoryStore()
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    dms = []
    monkeypatch.setattr(app.extensions["notifier"], "alert", lambda text, image=None: dms.append((text, image)) or True)
    app.extensions["home"].accept({"capabilities": {}})                        # skippy-pc is reporting: a flash may ask it for screens
    c = app.test_client()
    login(c, ADMIN)
    return app, c, store, dms, {"Authorization": f"Bearer {_pair(app.test_client())}"}


def flash(c, **body):
    return c.post("/vision/flash", json={"by": "key", "hold": False, **body})


def test_a_flash_saves_the_pages_stills_asks_skippy_pc_for_the_screens_and_tells_the_captain(env):
    app, c, store, dms, H = env
    r = flash(c, images=[{"name": "hud-page.jpg", "data": data_url(JPEG)}, {"name": "phone-cam-1.jpg", "data": data_url(JPEG + b"2")},
                         {"name": "glasses-rgb.jpg", "data": data_url(JPEG + b"3")}])
    ix = r.get_json()
    assert r.status_code == 200 and vision.ID.fullmatch(ix["id"]) and ix["by"] == "key" and ix["pending"] == ["pods", "pc"]
    assert [(f["name"], f["from"]) for f in ix["files"]] == [("glasses-rgb.jpg", "glasses"), ("hud-page.jpg", "page"), ("phone-cam-1.jpg", "page")]
    assert store.get(f"moments/{ix['id']}/hud-page.jpg") == JPEG and store.get(f"moments/{ix['id']}/index.json")
    assert dms == [("Your moment is saved.", JPEG)]                              # the page's own picture goes in the DM
    jobs = app.extensions["home"].jobs_reply()["jobs"]
    assert [(j["kind"], j["moment"]) for j in jobs] == [("flash", ix["id"])]       # skippy-pc is asked for the pods and the PC


def test_a_flash_with_nothing_to_ask_skippy_pc_for_is_not_pending(env):
    app, c, *_ = env
    app.extensions["home"].received_at = None                                   # skippy-pc isn't reporting: home is silent
    assert flash(c, images=[]).get_json()["pending"] == [] and app.extensions["home"].jobs_reply()["jobs"] == []


def test_a_bad_flash_is_refused_whole(env):
    app, c, store, dms, H = env
    good = {"name": "hud-page.jpg", "data": data_url(JPEG)}
    for bad in ({"name": "../x.jpg", "data": data_url(JPEG)},                  # a name no moment takes
                {"name": "hud-page.jpg", "data": data_url(PNG, "image/png")},   # a PNG where a JPEG is wanted
                {"name": "hud-page.jpg", "data": "data:image/jpeg;base64,@@@"},
                {"name": "hud-page.jpg", "data": data_url(b"not an image at all")},
                {"name": "phone-cam-1.jpg", "data": data_url(JPEG + b"x" * vision.MAX_IMAGE)}):
        assert flash(c, images=[good, bad]).status_code == 400
    assert flash(c, images=[good] * 13).status_code == 400
    assert not [k for k in store.files if k.startswith("moments/")] and dms == []   # nothing was kept, nobody was told


def test_ids_sort_by_time_and_two_flashes_in_one_second_get_two_ids(env):
    app, c, store, *_ = env
    m = app.extensions["vision"]
    m.clock = lambda: 1_800_000_000
    a, b, c3 = (flash(c, images=[]).get_json()["id"] for _ in range(3))
    assert len({a, b, c3}) == 3 and a == "20270115-080000" and b.startswith(a + "-")
    m.clock = lambda: 1_800_000_100
    d = flash(c, images=[]).get_json()["id"]
    listing = c.get("/vision/moments").get_json()["moments"]
    assert [x["id"] for x in listing][0] == d and {x["id"] for x in listing} == {a, b, c3, d}      # newest first


def test_the_hosts_clip_and_the_teslas_page_arrive_later_as_raw_files(env):
    app, c, store, dms, H = env
    mid = flash(c, images=[]).get_json()["id"]
    zipped = c.post(f"/vision/moment/{mid}/file?name=glasses-rgb-7s.zip", data=ZIP, content_type="application/zip")
    assert zipped.status_code == 200 and {f["name"] for f in zipped.get_json()["files"]} == {"glasses-rgb-7s.zip"}
    car = c.post(f"/vision/moment/{mid}/file?name=drive-page.jpg", data=JPEG, content_type="image/jpeg").get_json()
    assert [(f["name"], f["from"]) for f in car["files"]] == [("drive-page.jpg", "drive"), ("glasses-rgb-7s.zip", "glasses")]
    assert c.post(f"/vision/moment/{mid}/file?name=glasses-rgb-7s.zip", data=JPEG, content_type="application/zip").status_code == 400   # not a zip
    assert c.post(f"/vision/moment/{mid}/file?name=hud-page.jpg", data=ZIP, content_type="image/jpeg").status_code == 400                 # not a JPEG
    assert c.post(f"/vision/moment/{mid}/file?name=pod-office.png", data=PNG, content_type="image/png").status_code == 400     # skippy-pc's names
    assert c.post(f"/vision/moment/{mid}/file?name=hud-page.jpg", data=JPEG).status_code == 200
    assert c.post("/vision/moment/20270101-000000/file?name=hud-page.jpg", data=JPEG).status_code == 404


def test_skippy_pcs_screens_come_back_through_the_media_door_and_the_job_ending_clears_pending(env):
    app, c, store, dms, H = env
    home = app.extensions["home"]
    mid = flash(c, images=[]).get_json()["id"]
    job = home.jobs_reply()["jobs"][0]["id"]
    put = lambda name, data, ctype="image/png": app.test_client().put(f"/home/media/{job}?name={name}", data=data, headers={**H, "Content-Type": ctype})  # noqa: E731
    assert put("pod-theater.png", PNG).status_code == 200 and put("pc-screen-1.png", PNG).status_code == 200
    assert put("pc-screen-2.png", JPEG, "image/jpeg").status_code == 200
    assert put("pod-theater.png", b"not an image").status_code == 400 and put("chat.png", PNG).status_code == 400 and put("../pc.png", PNG).status_code == 400
    ix = c.get("/vision/moments").get_json()["moments"][0]
    assert ix["pending"] == ["pods", "pc"] and [(f["name"], f["from"]) for f in ix["files"]] == [
        ("pc-screen-1.png", "pc"), ("pc-screen-2.png", "pc"), ("pod-theater.png", "pod:theater")]
    done = app.test_client().post(f"/home/jobs/{job}", json={"result": {"files": 3}}, headers=H)
    assert done.get_json() == {"ok": True}
    assert c.get("/vision/moments").get_json()["moments"][0]["pending"] == []
    assert not [k for k in store.files if k.startswith("chat-pictures/")]          # never the chat's picture store


def test_a_moment_still_waiting_after_two_minutes_is_not_waiting_any_more(env):
    app, c, *_ = env
    m = app.extensions["vision"]
    flash(c, images=[])
    assert c.get("/vision/moments").get_json()["moments"][0]["pending"] == ["pods", "pc"]
    m.clock = lambda: time.time() + vision.PENDING_FOR_S + 1
    assert c.get("/vision/moments").get_json()["moments"][0]["pending"] == []        # skippy-pc never answered: don't promise it


def test_a_file_is_served_with_its_own_type_and_never_sniffed(env):
    app, c, *_ = env
    mid = flash(c, images=[{"name": "hud-page.jpg", "data": data_url(JPEG)}]).get_json()["id"]
    r = c.get(f"/vision/moment/{mid}/hud-page.jpg")
    assert r.status_code == 200 and r.data == JPEG and r.mimetype == "image/jpeg"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and "immutable" in r.headers["Cache-Control"] and r.headers["Content-Security-Policy"] == "sandbox"
    assert c.get(f"/vision/moment/{mid}/phone-cam-1.jpg").status_code == 404 and c.get(f"/vision/moment/{mid}/index.json").status_code == 404
    assert c.get("/vision/moment/20270101-000000/hud-page.jpg").status_code == 404


def test_since_lists_only_the_moments_after_it(env):
    app, c, *_ = env
    m = app.extensions["vision"]
    m.clock = lambda: 1_800_000_000
    flash(c, images=[])
    m.clock = lambda: 1_800_000_050
    new = flash(c, images=[]).get_json()["id"]
    assert [x["id"] for x in c.get("/vision/moments?since=1800000010").get_json()["moments"]] == [new]


def test_forget_deletes_every_file_and_keep_marks_the_clip_to_stay(env):
    app, c, store, *_ = env
    mid = flash(c, images=[{"name": "hud-page.jpg", "data": data_url(JPEG)}]).get_json()["id"]
    assert c.post(f"/vision/moment/{mid}/keep", json={"on": True}).get_json()["kept"] is True
    assert c.post(f"/vision/moment/{mid}/keep", json={"on": False}).get_json()["kept"] is False
    assert c.post(f"/vision/moment/{mid}/delete").status_code == 200
    assert not [k for k in store.files if k.startswith("moments/")] and c.get("/vision/moments").get_json()["moments"] == []
    assert c.post(f"/vision/moment/{mid}/delete").status_code == 404


def test_clips_go_after_ninety_days_unless_kept_and_the_stills_never_go(env):
    app, c, store, dms, H = env
    m = app.extensions["vision"]
    day = 86400
    m.clock = lambda: 1_800_000_000
    old = flash(c, images=[{"name": "hud-page.jpg", "data": data_url(JPEG)}]).get_json()["id"]
    kept = flash(c, images=[]).get_json()["id"]
    for mid in (old, kept):
        c.post(f"/vision/moment/{mid}/file?name=glasses-rgb-7s.zip", data=ZIP)
    c.post(f"/vision/moment/{kept}/keep", json={"on": True})
    m.clock = lambda: 1_800_000_000 + 91 * day
    young = flash(c, images=[]).get_json()["id"]
    c.post(f"/vision/moment/{young}/file?name=glasses-rgb-7s.zip", data=ZIP)
    swept = app.test_client().post("/home/vision/sweep", headers=H).get_json()
    assert swept == {"clips_deleted": 1}
    have = lambda mid, name: store.get(f"moments/{mid}/{name}") is not None   # noqa: E731
    assert not have(old, "glasses-rgb-7s.zip") and have(old, "hud-page.jpg")             # the old clip went, its still stayed
    assert have(kept, "glasses-rgb-7s.zip") and have(young, "glasses-rgb-7s.zip")        # kept, and too young
    assert [f["name"] for f in c.get("/vision/moments").get_json()["moments"][2]["files"]] == ["hud-page.jpg"]


def test_an_index_survives_a_restart_and_the_listing_reads_the_bucket(env):
    app, c, store, *_ = env
    mid = flash(c, images=[{"name": "hud-page.jpg", "data": data_url(JPEG)}]).get_json()["id"]
    again = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))   # a fresh portal, the same bucket
    c2 = again.test_client()
    login(c2, ADMIN)
    got = c2.get("/vision/moments").get_json()["moments"]
    assert [x["id"] for x in got] == [mid] and got[0]["files"][0]["name"] == "hud-page.jpg"
    assert c2.get(f"/vision/moment/{mid}/hud-page.jpg").data == JPEG


def test_purple_skippy_can_flash_list_and_read_back_through_the_home_token(env):
    app, c, store, dms, H = env
    http = app.test_client()
    assert http.post("/home/vision/flash").status_code == 401 and http.get("/home/vision/moments").status_code == 401
    made = http.post("/home/vision/flash", headers=H).get_json()
    assert made["by"] == "tool" and made["pending"] == ["pods", "pc"]
    c.post(f"/vision/moment/{made['id']}/file?name=hud-page.jpg", data=JPEG)
    got = http.get("/home/vision/moments?limit=1", headers=H).get_json()["moments"]
    assert got[0]["id"] == made["id"]
    assert http.get(f"/home/vision/moment/{made['id']}/hud-page.jpg", headers=H).data == JPEG
    assert http.get(f"/home/vision/moment/{made['id']}/secret.txt", headers=H).status_code == 404


def test_it_is_the_captains_alone_and_says_so_without_storage():
    store = tabletop.MemoryStore()
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    friend = app.test_client()
    login(friend, FRIEND)
    for method, url in (("post", "/vision/flash"), ("get", "/vision/moments"), ("get", "/vision/moment/20270101-000000/hud-page.jpg"),
                        ("post", "/vision/moment/20270101-000000/delete"), ("post", "/vision/moment/20270101-000000/keep")):
        assert getattr(friend, method)(url).status_code in (302, 401, 403), url
    bare = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(None))
    c = bare.test_client()
    login(c, ADMIN)
    r = c.post("/vision/flash", json={"images": []})
    assert r.status_code == 503 and "TABLETOP_BUCKET" in r.get_json()["error"]


def test_the_pure_parts():
    assert vision.sniff(JPEG) == "image/jpeg" and vision.sniff(PNG) == "image/png" and vision.sniff(ZIP) == "application/zip"
    assert vision.sniff(b"") is None and vision.sniff(b"GIF89a") is None and vision.sniff(b"<html>") is None
    assert vision.decode_data_url(data_url(JPEG)) == JPEG
    for bad in (None, 7, "", "http://x/y.jpg", "data:image/jpeg,plain", "data:image/jpeg;base64,@@@"):
        with pytest.raises(ValueError):
            vision.decode_data_url(bad)
    assert json.dumps(vision.Moments(tabletop.MemoryStore()).list()) == "[]"
