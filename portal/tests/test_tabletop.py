"""The Tabletop (portal/tabletop.py, /tabletop): the GM makes boards, players pin pictures and notes, and only a card's
author (or the GM) can move, edit or take it down."""
import io

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import tabletop  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, login, _add_friend  # noqa: E402

ERIC = "eric@example.com"
GREGG = "gregg@example.com"
OUTSIDER = "chatonly@example.com"
H = {"X-Tabletop": "1"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64


@pytest.fixture
def world():
    store = tabletop.MemoryStore()
    app = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    c = app.test_client()
    _add_friend(c, ERIC, ["tabletop"])
    _add_friend(c, GREGG, ["tabletop"])
    _add_friend(c, OUTSIDER, ["skippy"])
    return app, store


def as_(app, email):
    c = app.test_client()
    login(c, email)
    return c


def make_board(app, title="Ep 1 — The Endless Fall"):
    resp = as_(app, ADMIN).post("/tabletop/boards", json={"title": title}, headers=H)
    assert resp.status_code == 200, resp.data
    return resp.json["board"]["id"]


def test_the_gm_makes_boards_and_players_see_them_but_cannot_make_their_own(world):
    app, store = world
    bid = make_board(app)
    eric = as_(app, ERIC)
    assert [b["title"] for b in eric.get("/tabletop/boards").json["boards"]] == ["Ep 1 — The Endless Fall"]
    assert eric.post("/tabletop/boards", json={"title": "mine"}, headers=H).status_code == 403
    assert eric.post(f"/tabletop/b/{bid}/delete", headers=H).status_code == 403
    assert "boards.json" in store.files and f"boards/{bid}.json" in store.files


def test_a_player_pins_a_note_and_a_picture_and_everyone_sees_them(world):
    app, store = world
    bid = make_board(app)
    eric = as_(app, ERIC)
    note = eric.post(f"/tabletop/b/{bid}/text", json={"text": "The coin always lands heads", "ink": "amber", "x": 100, "y": 200}, headers=H).json["card"]
    assert note["by"] == ERIC and note["ink"] == "amber" and (note["x"], note["y"]) == (100, 200)
    pic = eric.post(f"/tabletop/b/{bid}/image", data={"img": (io.BytesIO(PNG), "map.png"), "ratio": "0.5", "text": "the platforms"},
                    headers=H, content_type="multipart/form-data").json["card"]
    assert pic["kind"] == "image" and pic["img"].endswith(".png") and pic["ratio"] == 0.5
    gregg = as_(app, GREGG)
    view = gregg.get(f"/tabletop/b/{bid}").json
    assert [c["id"] for c in view["cards"]] == [note["id"], pic["id"]]
    img = gregg.get(f"/tabletop/b/{bid}/img/{pic['img']}")
    assert img.status_code == 200 and img.data == PNG and img.mimetype == "image/png"
    assert "immutable" in img.headers["Cache-Control"]


def test_the_poll_answers_just_the_rev_while_nothing_moved(world):
    app, _ = world
    bid = make_board(app)
    eric = as_(app, ERIC)
    rev = eric.get(f"/tabletop/b/{bid}").json["rev"]
    assert eric.get(f"/tabletop/b/{bid}?since={rev}").json == {"rev": rev}
    as_(app, GREGG).post(f"/tabletop/b/{bid}/text", json={"text": "hi"}, headers=H)
    changed = eric.get(f"/tabletop/b/{bid}?since={rev}").json
    assert changed["rev"] > rev and len(changed["cards"]) == 1


def test_only_the_author_or_the_gm_can_move_edit_or_remove_a_card(world):
    app, store = world
    bid = make_board(app)
    eric, gregg, gm = as_(app, ERIC), as_(app, GREGG), as_(app, ADMIN)
    card = eric.post(f"/tabletop/b/{bid}/image", data={"img": (io.BytesIO(JPEG), "x.jpg")}, headers=H,
                     content_type="multipart/form-data").json["card"]
    base = f"/tabletop/b/{bid}/c/{card['id']}"
    assert gregg.post(base + "/move", json={"x": 5, "y": 5}, headers=H).status_code == 400
    assert gregg.post(base + "/edit", json={"text": "vandal"}, headers=H).status_code == 400
    assert gregg.post(base + "/delete", headers=H).status_code == 400
    moved = eric.post(base + "/move", json={"x": 50, "y": 60, "w": 99999}, headers=H).json["card"]
    assert (moved["x"], moved["y"], moved["w"]) == (50, 60, tabletop.CARD_W[1])         # clamped to the board's limits
    assert gm.post(base + "/edit", json={"text": "GM's caption"}, headers=H).json["card"]["text"] == "GM's caption"
    assert gm.post(base + "/delete", headers=H).status_code == 200
    assert f"images/{bid}/{card['img']}" not in store.files                               # the picture goes with its card
    assert eric.get(f"/tabletop/b/{bid}").json["cards"] == []


def test_writes_need_the_header_and_pictures_are_checked_by_their_bytes(world):
    app, _ = world
    bid = make_board(app)
    eric = as_(app, ERIC)
    assert eric.post(f"/tabletop/b/{bid}/text", json={"text": "no header"}).status_code == 400
    fake = eric.post(f"/tabletop/b/{bid}/image", data={"img": (io.BytesIO(b"<svg onload=alert(1)>"), "evil.png")}, headers=H,
                     content_type="multipart/form-data")
    assert fake.status_code == 400 and "JPEG" in fake.json["error"]
    assert eric.post(f"/tabletop/b/{bid}/text", json={"text": "   "}, headers=H).status_code == 400
    assert eric.get(f"/tabletop/b/{bid}/img/../boards.json").status_code == 404
    assert eric.get("/tabletop/b/nope/img/abcdef123456.png").status_code == 404


def test_someone_without_the_grant_gets_nothing(world):
    app, _ = world
    bid = make_board(app)
    outsider = as_(app, OUTSIDER)
    assert outsider.get("/tabletop").status_code == 403
    assert outsider.get(f"/tabletop/b/{bid}").status_code == 403
    assert outsider.post(f"/tabletop/b/{bid}/text", json={"text": "x"}, headers=H).status_code == 403


def test_the_launcher_shows_the_tabletop_tile_only_to_those_granted_it(world):
    app, _ = world
    assert 'href="/tabletop"' in as_(app, ERIC).get("/").data.decode()
    assert 'href="/tabletop"' not in as_(app, OUTSIDER).get("/").data.decode()
    page = as_(app, ERIC).get("/tabletop").data.decode()
    assert 'data-gm="0"' in page and 'data-ready="1"' in page and "tt-new" not in page   # a player has no board-making form
    assert 'data-gm="1"' in as_(app, ADMIN).get("/tabletop").data.decode()


def test_without_a_bucket_the_page_says_so_and_nothing_is_written(world):
    app = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(None))
    gm = as_(app, ADMIN)
    assert 'data-ready="0"' in gm.get("/tabletop").data.decode()
    assert gm.post("/tabletop/boards", json={"title": "x"}, headers=H).status_code == 503


def test_deleting_a_board_takes_its_pictures_with_it(world):
    app, store = world
    bid = make_board(app)
    keep = make_board(app, "Ep 2 — Mirror Shatter")
    as_(app, ERIC).post(f"/tabletop/b/{bid}/image", data={"img": (io.BytesIO(PNG), "a.png")}, headers=H, content_type="multipart/form-data")
    assert as_(app, ADMIN).post(f"/tabletop/b/{bid}/delete", headers=H).status_code == 200
    assert not [p for p in store.files if bid in p]
    assert [b["id"] for b in as_(app, ERIC).get("/tabletop/boards").json["boards"]] == [keep]


def test_a_fresh_portal_reads_the_boards_back_from_the_bucket(world):
    app, store = world
    bid = make_board(app)
    as_(app, ERIC).post(f"/tabletop/b/{bid}/text", json={"text": "survives a restart"}, headers=H)
    again = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    _add_friend(again.test_client(), ERIC, ["tabletop"])
    assert as_(again, ERIC).get(f"/tabletop/b/{bid}").json["cards"][0]["text"] == "survives a restart"
