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


def campaign(app):
    """The test's campaign (Heroes of Chance), made the first time it's asked for."""
    if "_campaign" not in app.config:
        resp = as_(app, ADMIN).post("/tabletop/campaigns", json={"title": "Heroes of Chance"}, headers=H)
        assert resp.status_code == 200, resp.data
        app.config["_campaign"] = resp.json["campaign"]["id"]
    return app.config["_campaign"]


def make_board(app, title="Ep 1 — The Endless Fall"):
    resp = as_(app, ADMIN).post("/tabletop/boards", json={"title": title, "campaign": campaign(app)}, headers=H)
    assert resp.status_code == 200, resp.data
    return resp.json["board"]["id"]


def test_the_gm_makes_boards_and_players_see_them_but_cannot_make_their_own(world):
    app, store = world
    bid = make_board(app)
    eric = as_(app, ERIC)
    assert [b["title"] for b in eric.get(f"/tabletop/boards?c={campaign(app)}").json["boards"]] == ["Ep 1 — The Endless Fall"]
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
    assert eric.get(f"/tabletop/b/{bid}?since={rev}").json == {"rev": rev, "fear": 0}     # the Fear rides along
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
    page = as_(app, ERIC).get(f"/tabletop/c/{campaign(app)}").data.decode()
    assert 'data-gm="0"' in page and 'data-ready="1"' in page and "tt-new" not in page   # a player has no board-making form
    assert 'data-gm="1"' in as_(app, ADMIN).get(f"/tabletop/c/{campaign(app)}").data.decode()


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
    assert [b["id"] for b in as_(app, ERIC).get(f"/tabletop/boards?c={campaign(app)}").json["boards"]] == [keep]


def test_a_fresh_portal_reads_the_boards_back_from_the_bucket(world):
    app, store = world
    bid = make_board(app)
    as_(app, ERIC).post(f"/tabletop/b/{bid}/text", json={"text": "survives a restart"}, headers=H)
    again = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    _add_friend(again.test_client(), ERIC, ["tabletop"])
    assert as_(again, ERIC).get(f"/tabletop/b/{bid}").json["cards"][0]["text"] == "survives a restart"


# ── heroes: the sheet, the folder, the Fear ──

def make_hero(app, name="Resirdar", sheet=None, player=""):
    resp = as_(app, ADMIN).post("/tabletop/heroes", json={"name": name, "sheet": sheet or {}, "player": player, "campaign": campaign(app)},
                                headers=H)
    assert resp.status_code == 200, resp.data
    return resp.json["id"]


def test_the_gm_adds_a_hero_with_a_sheet_and_unknown_fields_are_left_out(world):
    app, _ = world
    resp = as_(app, ADMIN).post("/tabletop/heroes", headers=H, json={"name": "Resirdar", "player": ERIC, "campaign": campaign(app), "sheet": {
        "class": "Wizard", "level": 3, "traits": {"knowledge": {"value": 3, "marked": True}}, "experiences": [{"name": "Sleepless", "bonus": 2}],
        "secret_admin": True, "hp": {"max": 99}}})
    assert resp.json["skipped"] == ["secret_admin"]
    hid = resp.json["id"]
    view = as_(app, GREGG).get(f"/tabletop/h/{hid}").json
    s = view["sheet"]
    assert s["name"] == "Resirdar" and s["class"] == "Wizard" and s["traits"]["knowledge"] == {"value": 3, "marked": True}
    assert s["hp"]["max"] == 12                                      # clamped to the sheet's 12 slots
    assert s["experiences"][0] == {"name": "Sleepless", "bonus": 2} and len(s["experiences"]) == 5
    assert len(s["inventory"]) == 12 and set(s["levelups"]["tier3"]) >= {"proficiency", "multiclass"}
    assert view["can_edit"] is False and "history" not in view       # Gregg doesn't play Resirdar
    assert as_(app, ERIC).get(f"/tabletop/h/{hid}").json["can_edit"] is True
    assert as_(app, ERIC).post("/tabletop/heroes", json={"name": "x"}, headers=H).status_code == 403


def test_only_the_heros_player_or_the_gm_changes_the_sheet_and_every_change_is_logged(world):
    app, _ = world
    hid = make_hero(app, player=ERIC)
    ok = as_(app, ERIC).post(f"/tabletop/h/{hid}/set", headers=H, json={"ops": [
        {"path": "hp.marked", "value": 2}, {"path": "inventory.3", "value": "a letter, never opened"}, {"path": "traits.agility.marked", "value": True}]})
    assert ok.status_code == 200
    assert as_(app, GREGG).post(f"/tabletop/h/{hid}/set", headers=H, json={"ops": [{"path": "hp.marked", "value": 0}]}).status_code == 400
    assert as_(app, ERIC).post(f"/tabletop/h/{hid}/set", headers=H, json={"ops": [{"path": "inventory.12", "value": "x"}]}).status_code == 400
    assert as_(app, ERIC).post(f"/tabletop/h/{hid}/set", headers=H, json={"ops": [{"path": "__class__", "value": "x"}]}).status_code == 400
    assert as_(app, ERIC).post(f"/tabletop/h/{hid}/set", json={"ops": [{"path": "hope", "value": 1}]}).status_code == 400   # no header
    gm = as_(app, ADMIN).get(f"/tabletop/h/{hid}").json
    assert gm["sheet"]["hp"]["marked"] == 2 and gm["sheet"]["inventory"][3] == "a letter, never opened"
    assert {(c["path"], c["old"], c["new"]) for c in gm["history"]} >= {("hp.marked", 0, 2), ("traits.agility.marked", False, True)}
    rev = gm["rev"]
    assert as_(app, ERIC).get(f"/tabletop/h/{hid}?since={rev}").json == {"rev": rev}


def test_a_player_adds_documents_and_pictures_to_their_folder(world):
    app, _ = world
    hid = make_hero(app, player=ERIC)
    eric = as_(app, ERIC)
    pdf = eric.post(f"/tabletop/h/{hid}/files", headers=H, content_type="multipart/form-data",
                    data={"file": (io.BytesIO(b"%PDF-1.7\n" + b"0" * 50), "player-packet.pdf")}).json["file"]
    pic = eric.post(f"/tabletop/h/{hid}/files", headers=H, content_type="multipart/form-data",
                    data={"file": (io.BytesIO(PNG), "portrait.png")}).json["file"]
    assert pdf["ext"] == "pdf" and pic["ext"] == "png"
    got = as_(app, GREGG).get(f"/tabletop/h/{hid}/files/{pdf['id']}?dl=1")
    assert got.status_code == 200 and got.mimetype == "application/pdf"
    assert 'attachment; filename="player-packet.pdf"' in got.headers["Content-Disposition"]
    assert got.headers["Content-Security-Policy"] == "sandbox"
    html = eric.post(f"/tabletop/h/{hid}/files", headers=H, content_type="multipart/form-data",
                     data={"file": (io.BytesIO(b"<html><script>x</script>"), "evil.pdf")})
    assert html.status_code == 400
    assert as_(app, GREGG).post(f"/tabletop/h/{hid}/files", headers=H, content_type="multipart/form-data",
                                data={"file": (io.BytesIO(PNG), "x.png")}).status_code == 400          # not Gregg's hero
    assert as_(app, GREGG).post(f"/tabletop/h/{hid}/files/{pic['id']}/delete", headers=H).status_code == 400
    assert eric.post(f"/tabletop/h/{hid}/files/{pic['id']}/delete", headers=H).status_code == 200
    assert [f["name"] for f in eric.get(f"/tabletop/h/{hid}").json["files"]] == ["player-packet.pdf"]


def test_the_fear_is_the_gms_kept_between_sessions_and_rides_on_every_board_poll(world):
    app, store = world
    bid = make_board(app)
    gm, eric = as_(app, ADMIN), as_(app, ERIC)
    cid = campaign(app)
    assert gm.post("/tabletop/fear", json={"campaign": cid, "delta": 3}, headers=H).json["fear"] == 3
    assert gm.post("/tabletop/fear", json={"campaign": cid, "value": 99}, headers=H).json["fear"] == 12
    assert gm.post("/tabletop/fear", json={"campaign": cid, "delta": -5}, headers=H).json["fear"] == 7
    assert eric.post("/tabletop/fear", json={"campaign": cid, "delta": -7}, headers=H).status_code == 403
    rev = eric.get(f"/tabletop/b/{bid}").json["rev"]
    assert eric.get(f"/tabletop/b/{bid}?since={rev}").json == {"rev": rev, "fear": 7}
    again = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    assert as_(again, ADMIN).get(f"/tabletop/fear?c={cid}").json["fear"] == 7                        # the next session starts where this one ended


def test_the_print_view_has_every_field_of_the_official_sheet(world):
    app, _ = world
    hid = make_hero(app, sheet={"class": "Bard", "weapons": {"primary": {"name": "Pistol"}}, "project": {"name": "Fix the wagon"}})
    page = as_(app, ERIC).get(f"/tabletop/h/{hid}/print").data.decode()
    for label in ("Evasion", "Armor", "agility", "knowledge", "Damage &amp; health", "MINOR", "SEVERE", "STRESS", "Hope", "Experience",
                  "Handfuls", "Bags", "Chest", "Class feature", "Proficiency", "PRIMARY", "SECONDARY", "Trait &amp; range",
                  "Damage dice &amp; type", "Active armor", "Base thresholds", "Inventory", "INVENTORY WEAPON", "Character description",
                  "Clothes that are", "Attitude like", "Background questions", "Connections", "Tier 2", "Tier 3", "Tier 4",
                  "Multiclass", "Pistol", "Fix the wagon"):
        assert label in page, label


def test_the_hub_lists_heroes_with_their_projects(world):
    app, _ = world
    hid = make_hero(app, "Chickenhead", sheet={"class": "Bard", "project": {"name": "Open the letter", "clock_max": 6, "clock": 2}})
    hero = as_(app, ERIC).get(f"/tabletop/heroes?c={campaign(app)}").json["heroes"][0]
    assert hero["id"] == hid and hero["project"] == "Open the letter" and (hero["clock"], hero["clock_max"]) == (2, 6)


def test_the_gms_quick_reference_never_reaches_a_player(world):
    app, _ = world
    hid = make_hero(app, "Kettleblack", player=ERIC)
    gm = as_(app, ADMIN)
    assert gm.post(f"/tabletop/h/{hid}/gm", json={"gm_ref": ["Never call him Copperbottom", "", "Misses the wagon"]}, headers=H).status_code == 200
    assert gm.get(f"/tabletop/h/{hid}").json["gm_ref"] == ["Never call him Copperbottom", "Misses the wagon"]
    assert gm.get(f"/tabletop/heroes?c={campaign(app)}").json["heroes"][0]["gm_ref"][0] == "Never call him Copperbottom"
    eric = as_(app, ERIC)
    for url in (f"/tabletop/h/{hid}", f"/tabletop/heroes?c={campaign(app)}", f"/tabletop/h/{hid}/print"):
        assert b"Copperbottom" not in eric.get(url).data, url
    assert eric.post(f"/tabletop/h/{hid}/gm", json={"gm_ref": ["mine now"]}, headers=H).status_code == 403


# ── campaigns: Tabletops is a deck of campaigns; each has its own boards, heroes and Fear ──

def test_campaigns_keep_their_boards_heroes_and_fear_apart(world):
    app, _ = world
    gm = as_(app, ADMIN)
    hoc = campaign(app)
    other = gm.post("/tabletop/campaigns", json={"title": "One-shots"}, headers=H).json["campaign"]["id"]
    make_board(app)
    make_hero(app, "Chickenhead")
    gm.post("/tabletop/fear", json={"campaign": hoc, "value": 4}, headers=H)
    eric = as_(app, ERIC)
    deck = {c["title"]: c for c in eric.get("/tabletop/campaigns").json["campaigns"]}
    assert deck["Heroes of Chance"]["heroes"] == ["Chickenhead"] and deck["Heroes of Chance"]["boards"] == 1
    assert deck["One-shots"]["heroes"] == [] and deck["One-shots"]["boards"] == 0
    assert eric.get(f"/tabletop/heroes?c={other}").json == {"heroes": [], "fear": 0}
    assert eric.get(f"/tabletop/boards?c={other}").json["boards"] == []
    assert eric.post("/tabletop/campaigns", json={"title": "mine"}, headers=H).status_code == 403
    assert gm.post("/tabletop/boards", json={"title": "x", "campaign": "nope00000000"}, headers=H).status_code == 404


def test_the_tabletops_page_is_the_campaign_deck_and_each_campaign_has_its_own_tabletop(world):
    app, _ = world
    cid = campaign(app)
    eric = as_(app, ERIC)
    assert 'id="tc"' in eric.get("/tabletop").data.decode()
    page = eric.get(f"/tabletop/c/{cid}").data.decode()
    assert f'data-campaign="{cid}"' in page and "Heroes of Chance" in page and 'id="tt-strip"' in page
    assert eric.get("/tabletop/c/abcdef123456").status_code == 404


def test_a_heros_avatar_is_a_picture_from_their_folder_chosen_by_their_player_or_the_gm(world):
    app, _ = world
    hid = make_hero(app, "Kettleblack", player=ERIC)
    eric, gregg = as_(app, ERIC), as_(app, GREGG)

    def add(name, data):
        return eric.post(f"/tabletop/h/{hid}/files", headers=H, content_type="multipart/form-data", data={"file": (io.BytesIO(data), name)}).json["file"]
    pic, pdf = add("kettleblack.png", PNG), add("packet.pdf", b"%PDF-1.7\n" + b"0" * 40)
    assert eric.get(f"/tabletop/heroes?c={campaign(app)}").json["heroes"][0]["avatar"] == ""
    assert eric.post(f"/tabletop/h/{hid}/avatar", json={"file": pdf["id"]}, headers=H).status_code == 400       # not a PDF
    assert eric.post(f"/tabletop/h/{hid}/avatar", json={"file": "nope00000000"}, headers=H).status_code == 404   # not in the folder
    assert gregg.post(f"/tabletop/h/{hid}/avatar", json={"file": pic["id"]}, headers=H).status_code == 400       # not Gregg's hero
    default = {"x": 50, "y": 30, "zoom": 1.0}
    assert eric.post(f"/tabletop/h/{hid}/avatar", json={"file": pic["id"]}, headers=H).json == {"avatar": pic["id"], "avatar_focus": default}
    crop = eric.post(f"/tabletop/h/{hid}/avatar", json={"file": pic["id"], "focus": {"x": 55, "y": 26, "zoom": 2.3}}, headers=H).json
    assert crop["avatar_focus"] == {"x": 55, "y": 26, "zoom": 2.3}
    wild = eric.post(f"/tabletop/h/{hid}/avatar", json={"file": pic["id"], "focus": {"x": 900, "y": -5, "zoom": 99}}, headers=H).json
    assert wild["avatar_focus"] == {"x": 100, "y": 0, "zoom": 4.0}                                              # kept inside the picture
    assert eric.post(f"/tabletop/h/{hid}/avatar", json={"file": pic["id"], "focus": {"zoom": "lots"}}, headers=H).json["avatar_focus"]["zoom"] == 1.0
    eric.post(f"/tabletop/h/{hid}/avatar", json={"file": pic["id"], "focus": {"x": 55, "y": 26, "zoom": 2.3}}, headers=H)
    assert gregg.get(f"/tabletop/heroes?c={campaign(app)}").json["heroes"][0]["avatar_focus"] == {"x": 55, "y": 26, "zoom": 2.3}
    assert gregg.get(f"/tabletop/heroes?c={campaign(app)}").json["heroes"][0]["avatar"] == pic["id"]               # everyone sees the face
    assert gregg.get(f"/tabletop/h/{hid}").json["avatar"] == pic["id"]
    assert eric.post(f"/tabletop/h/{hid}/files/{pic['id']}/delete", headers=H).status_code == 200                  # the file goes, the avatar clears
    assert eric.get(f"/tabletop/h/{hid}").json["avatar"] == ""
    again = add("again.png", PNG)
    assert as_(app, ADMIN).post(f"/tabletop/h/{hid}/avatar", json={"file": again["id"]}, headers=H).status_code == 200   # the GM can too
    assert as_(app, ADMIN).post(f"/tabletop/h/{hid}/avatar", json={"file": ""}, headers=H).json == {"avatar": "", "avatar_focus": default}   # and clear it


def test_the_gm_removes_a_duplicate_campaign_but_never_one_that_still_holds_heroes_or_boards(world):
    app, _ = world
    gm = as_(app, ADMIN)
    hoc = campaign(app)
    assert gm.post("/tabletop/campaigns", json={"title": "heroes of chance"}, headers=H).status_code == 400      # the same name, whatever its case
    other = gm.post("/tabletop/campaigns", json={"title": "One-shots"}, headers=H).json["campaign"]["id"]
    assert gm.post(f"/tabletop/campaigns/{other}/edit", json={"title": "Heroes of Chance"}, headers=H).status_code == 400   # nor by renaming
    assert gm.post(f"/tabletop/campaigns/{other}/edit", json={"title": "Odd Jobs"}, headers=H).json["campaign"]["title"] == "Odd Jobs"
    assert as_(app, ERIC).post(f"/tabletop/campaigns/{other}/delete", headers=H).status_code == 403            # only the GM
    make_hero(app, "Resirdar")
    make_board(app)
    assert gm.post(f"/tabletop/campaigns/{hoc}/delete", headers=H).status_code == 400                           # it has a hero and a board
    gm.post("/tabletop/fear", json={"campaign": other, "value": 5}, headers=H)
    assert gm.post(f"/tabletop/campaigns/{other}/delete", headers=H).status_code == 200                         # an empty one goes
    assert [c["title"] for c in gm.get("/tabletop/campaigns").json["campaigns"]] == ["Heroes of Chance"]
    assert gm.post(f"/tabletop/campaigns/{other}/delete", headers=H).status_code == 404
