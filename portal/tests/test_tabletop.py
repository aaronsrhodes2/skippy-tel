"""The Tabletop (portal/tabletop.py, /tabletop): Heroes of Chance. One page: a card per hero (sheet, project, folder, avatar), the GM's
Fear, and the cork board where everyone pins pictures and notes. Only a card's author (or the GM) can move, edit or take it down."""
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


# ── the page ──

def test_the_page_is_the_campaign_with_its_heroes_and_its_one_board_and_no_picker_of_either(world):
    app, _ = world
    page = as_(app, ERIC).get("/tabletop").data.decode()
    assert "Heroes of Chance" in page and 'id="tt-strip"' in page and 'id="tt-view"' in page      # the heroes along the top, the cork board below
    assert 'data-gm="0"' in page and 'data-ready="1"' in page and "tt-hero-new" not in page      # a player can't add heroes
    for gone in ('id="tt-boards"', 'id="tt-boards-key"', 'id="tc"', "tt-new-title"):               # no board list, no campaign deck
        assert gone not in page, gone
    gm = as_(app, ADMIN).get("/tabletop").data.decode()
    assert 'data-gm="1"' in gm and "tt-hero-new" in gm
    paths = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/tabletop")}
    assert not [p for p in paths if "/campaigns" in p or "/boards" in p or "/b/" in p or p.startswith("/tabletop/c/")], paths


def test_the_launcher_shows_the_tabletop_tile_only_to_those_granted_it(world):
    app, _ = world
    assert 'href="/tabletop"' in as_(app, ERIC).get("/").data.decode()
    assert 'href="/tabletop"' not in as_(app, OUTSIDER).get("/").data.decode()


def test_someone_without_the_grant_gets_nothing(world):
    app, _ = world
    outsider = as_(app, OUTSIDER)
    assert outsider.get("/tabletop").status_code == 403
    assert outsider.get("/tabletop/board").status_code == 403
    assert outsider.post("/tabletop/board/text", json={"text": "x"}, headers=H).status_code == 403
    assert outsider.get("/tabletop/heroes").status_code == 403


def test_without_a_bucket_the_page_says_so_and_nothing_is_written(world):
    app = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(None))
    gm = as_(app, ADMIN)
    assert 'data-ready="0"' in gm.get("/tabletop").data.decode()
    assert gm.post("/tabletop/board/text", json={"text": "x"}, headers=H).status_code == 503


# ── the cork board ──

def test_a_player_pins_a_note_and_a_picture_and_everyone_sees_them(world):
    app, store = world
    eric = as_(app, ERIC)
    note = eric.post("/tabletop/board/text", json={"text": "The coin always lands heads", "ink": "amber", "x": 100, "y": 200}, headers=H).json["card"]
    assert note["by"] == ERIC and note["ink"] == "amber" and (note["x"], note["y"]) == (100, 200)
    pic = eric.post("/tabletop/board/image", data={"img": (io.BytesIO(PNG), "map.png"), "ratio": "0.5", "text": "the platforms"},
                    headers=H, content_type="multipart/form-data").json["card"]
    assert pic["kind"] == "image" and pic["img"].endswith(".png") and pic["ratio"] == 0.5
    gregg = as_(app, GREGG)
    view = gregg.get("/tabletop/board").json
    assert view["title"] == "Heroes of Chance" and [c["id"] for c in view["cards"]] == [note["id"], pic["id"]]
    img = gregg.get(f"/tabletop/board/img/{pic['img']}")
    assert img.status_code == 200 and img.data == PNG and img.mimetype == "image/png"
    assert "immutable" in img.headers["Cache-Control"]
    assert "board.json" in store.files and f"images/{pic['img']}" in store.files


def test_the_poll_answers_just_the_rev_while_nothing_moved(world):
    app, _ = world
    eric = as_(app, ERIC)
    rev = eric.get("/tabletop/board").json["rev"]
    assert eric.get(f"/tabletop/board?since={rev}").json == {"rev": rev, "fear": 0}               # the Fear rides along
    as_(app, GREGG).post("/tabletop/board/text", json={"text": "hi"}, headers=H)
    changed = eric.get(f"/tabletop/board?since={rev}").json
    assert changed["rev"] > rev and len(changed["cards"]) == 1


def test_only_the_author_or_the_gm_can_move_edit_or_remove_a_card(world):
    app, store = world
    eric, gregg, gm = as_(app, ERIC), as_(app, GREGG), as_(app, ADMIN)
    card = eric.post("/tabletop/board/image", data={"img": (io.BytesIO(JPEG), "x.jpg")}, headers=H, content_type="multipart/form-data").json["card"]
    base = f"/tabletop/board/c/{card['id']}"
    assert gregg.post(base + "/move", json={"x": 5, "y": 5}, headers=H).status_code == 400
    assert gregg.post(base + "/edit", json={"text": "vandal"}, headers=H).status_code == 400
    assert gregg.post(base + "/delete", headers=H).status_code == 400
    moved = eric.post(base + "/move", json={"x": 50, "y": 60, "w": 99999}, headers=H).json["card"]
    assert (moved["x"], moved["y"], moved["w"]) == (50, 60, tabletop.CARD_W[1])         # clamped to the board's limits
    assert gm.post(base + "/edit", json={"text": "GM's caption"}, headers=H).json["card"]["text"] == "GM's caption"
    assert gm.post(base + "/delete", headers=H).status_code == 200
    assert f"images/{card['img']}" not in store.files                                     # the picture goes with its card
    assert eric.get("/tabletop/board").json["cards"] == []
    assert gm.post("/tabletop/board/c/abcdef123456/delete", headers=H).status_code == 404


def test_writes_need_the_header_and_pictures_are_checked_by_their_bytes(world):
    app, _ = world
    eric = as_(app, ERIC)
    assert eric.post("/tabletop/board/text", json={"text": "no header"}).status_code == 400
    fake = eric.post("/tabletop/board/image", data={"img": (io.BytesIO(b"<svg onload=alert(1)>"), "evil.png")}, headers=H,
                     content_type="multipart/form-data")
    assert fake.status_code == 400 and "JPEG" in fake.json["error"]
    assert eric.post("/tabletop/board/text", json={"text": "   "}, headers=H).status_code == 400
    assert eric.get("/tabletop/board/img/../board.json").status_code == 404
    assert eric.get("/tabletop/board/img/abcdef123456.png").status_code == 404


def test_a_fresh_portal_reads_the_board_back_from_the_bucket(world):
    app, store = world
    as_(app, ERIC).post("/tabletop/board/text", json={"text": "survives a restart"}, headers=H)
    again = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    _add_friend(again.test_client(), ERIC, ["tabletop"])
    assert as_(again, ERIC).get("/tabletop/board").json["cards"][0]["text"] == "survives a restart"


# ── heroes: the sheet, the folder, the avatar, the Fear ──

def make_hero(app, name="Resirdar", sheet=None, player=""):
    resp = as_(app, ADMIN).post("/tabletop/heroes", json={"name": name, "sheet": sheet or {}, "player": player}, headers=H)
    assert resp.status_code == 200, resp.data
    return resp.json["id"]


def test_the_gm_adds_a_hero_with_a_sheet_and_unknown_fields_are_left_out(world):
    app, _ = world
    resp = as_(app, ADMIN).post("/tabletop/heroes", headers=H, json={"name": "Resirdar", "player": ERIC, "sheet": {
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


def test_a_heros_avatar_is_a_picture_from_their_folder_chosen_by_their_player_or_the_gm(world):
    app, _ = world
    hid = make_hero(app, "Kettleblack", player=ERIC)
    eric, gregg = as_(app, ERIC), as_(app, GREGG)

    def add(name, data):
        return eric.post(f"/tabletop/h/{hid}/files", headers=H, content_type="multipart/form-data", data={"file": (io.BytesIO(data), name)}).json["file"]
    pic, pdf = add("kettleblack.png", PNG), add("packet.pdf", b"%PDF-1.7\n" + b"0" * 40)
    assert eric.get("/tabletop/heroes").json["heroes"][0]["avatar"] == ""
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
    seen = gregg.get("/tabletop/heroes").json["heroes"][0]
    assert seen["avatar"] == pic["id"] and seen["avatar_focus"] == {"x": 55, "y": 26, "zoom": 2.3}              # everyone sees the face
    assert gregg.get(f"/tabletop/h/{hid}").json["avatar"] == pic["id"]
    assert eric.post(f"/tabletop/h/{hid}/files/{pic['id']}/delete", headers=H).status_code == 200                  # the file goes, the avatar clears
    assert eric.get(f"/tabletop/h/{hid}").json["avatar"] == ""
    again = add("again.png", PNG)
    assert as_(app, ADMIN).post(f"/tabletop/h/{hid}/avatar", json={"file": again["id"]}, headers=H).status_code == 200   # the GM can too
    assert as_(app, ADMIN).post(f"/tabletop/h/{hid}/avatar", json={"file": ""}, headers=H).json == {"avatar": "", "avatar_focus": default}   # and clear it


def test_the_fear_is_the_gms_kept_between_sessions_and_rides_on_every_board_poll(world):
    app, store = world
    gm, eric = as_(app, ADMIN), as_(app, ERIC)
    assert gm.post("/tabletop/fear", json={"delta": 3}, headers=H).json["fear"] == 3
    assert gm.post("/tabletop/fear", json={"value": 99}, headers=H).json["fear"] == 12
    assert gm.post("/tabletop/fear", json={"delta": -5}, headers=H).json["fear"] == 7
    assert eric.post("/tabletop/fear", json={"delta": -7}, headers=H).status_code == 403
    rev = eric.get("/tabletop/board").json["rev"]
    assert eric.get(f"/tabletop/board?since={rev}").json == {"rev": rev, "fear": 7}
    again = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    assert as_(again, ADMIN).get("/tabletop/fear").json["fear"] == 7                      # the next session starts where this one ended


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


def test_the_hero_cards_show_class_level_player_and_project(world):
    app, _ = world
    hid = make_hero(app, "Chickenhead", sheet={"class": "Bard", "project": {"name": "Open the letter", "clock_max": 6, "clock": 2}})
    hero = as_(app, ERIC).get("/tabletop/heroes").json["heroes"][0]
    assert hero["id"] == hid and hero["class"] == "Bard" and hero["project"] == "Open the letter" and (hero["clock"], hero["clock_max"]) == (2, 6)


def test_the_gms_quick_reference_never_reaches_a_player(world):
    app, _ = world
    hid = make_hero(app, "Kettleblack", player=ERIC)
    gm = as_(app, ADMIN)
    assert gm.post(f"/tabletop/h/{hid}/gm", json={"gm_ref": ["Never call him Copperbottom", "", "Misses the wagon"]}, headers=H).status_code == 200
    assert gm.get(f"/tabletop/h/{hid}").json["gm_ref"] == ["Never call him Copperbottom", "Misses the wagon"]
    assert gm.get("/tabletop/heroes").json["heroes"][0]["gm_ref"][0] == "Never call him Copperbottom"
    eric = as_(app, ERIC)
    for url in (f"/tabletop/h/{hid}", "/tabletop/heroes", f"/tabletop/h/{hid}/print"):
        assert b"Copperbottom" not in eric.get(url).data, url
    assert eric.post(f"/tabletop/h/{hid}/gm", json={"gm_ref": ["mine now"]}, headers=H).status_code == 403


# ── a failed save must not leave a hero or a card that only this process believes in ──

class FlakyStore(tabletop.MemoryStore):
    """A store whose saves can be made to fail, like a bucket that is missing or unreachable."""
    broken = False

    def put(self, path, data, content_type):
        if self.broken:
            raise OSError("the bucket can't be reached")
        super().put(path, data, content_type)


def test_a_card_whose_save_fails_is_not_kept_in_memory_so_pressing_the_key_again_pins_one_not_two():
    store = FlakyStore()
    app = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    gm = as_(app, ADMIN)
    store.broken = True
    first = gm.post("/tabletop/board/text", json={"text": "The coin lands heads"}, headers=H)
    assert first.status_code == 503 and "storage" in first.json["error"]
    store.broken = False
    assert gm.get("/tabletop/board").json["cards"] == []                                  # no phantom card on the board
    assert gm.post("/tabletop/board/text", json={"text": "The coin lands heads"}, headers=H).status_code == 200
    assert [c["text"] for c in gm.get("/tabletop/board").json["cards"]] == ["The coin lands heads"]       # one, not two


def test_a_hero_whose_save_fails_is_not_kept_in_memory_either():
    store = FlakyStore()
    app = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    gm = as_(app, ADMIN)
    store.broken = True
    assert gm.post("/tabletop/heroes", json={"name": "Resirdar"}, headers=H).status_code == 503
    store.broken = False
    assert gm.get("/tabletop/heroes").json["heroes"] == []
    assert gm.post("/tabletop/heroes", json={"name": "Resirdar"}, headers=H).status_code == 200
    assert [h["name"] for h in gm.get("/tabletop/heroes").json["heroes"]] == ["Resirdar"]


def test_a_new_hero_shows_up_in_the_list_straight_away_without_a_reload(world):
    app, _ = world
    gm = as_(app, ADMIN)
    assert gm.get("/tabletop/heroes").json["heroes"] == []
    hid = make_hero(app, "Resirdar")
    assert [h["id"] for h in gm.get("/tabletop/heroes").json["heroes"]] == [hid]
    assert [h["id"] for h in as_(app, ERIC).get("/tabletop/heroes").json["heroes"]] == [hid]


def test_a_missing_bucket_is_an_error_not_an_empty_table_but_a_missing_file_is_just_absent():
    class NotFound(Exception):                                                            # google.api_core.exceptions.NotFound, by name
        pass

    class Blob:
        def __init__(self, message):
            self.message = message

        def download_as_bytes(self):
            raise NotFound(self.message)

    class Client:
        def __init__(self, message):
            self.message = message

        def bucket(self, name):
            return type("B", (), {"blob": lambda _s, path: Blob(self.message)})()

    gone = tabletop.GcsStore("skippy-tel-tabletop", client=Client("404 GET .../b/skippy-tel-tabletop/o/board.json: The specified bucket does not exist."))
    with pytest.raises(tabletop.StorageMissing):
        gone.get("board.json")
    fine = tabletop.GcsStore("skippy-tel-tabletop", client=Client("404 GET .../o/board.json: No such object: skippy-tel-tabletop/board.json"))
    assert fine.get("board.json") is None
    app = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(gone))
    resp = as_(app, ADMIN).get("/tabletop/board")
    assert resp.status_code == 503 and "bucket" in resp.json["error"]                     # the page says so, instead of showing an empty table
