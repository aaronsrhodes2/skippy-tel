"""Each control's own theme, picked on the control by the Captain (themes.py; keys.js: a black, invisible button in a tile's cut-off
corner, a long press on a key): stored as live data, shown for everyone, his alone to change."""
import json
import re
from pathlib import Path

import pytest

import modules as modules_mod
import themes as T

PORTAL = Path(__file__).resolve().parents[1]


class Vm:
    def __init__(self):
        self.md = {}

    def get_metadata(self, k):
        return self.md.get(k, "")

    def set_metadata(self, k, v):
        self.md[k] = v


def test_a_theme_is_kept_changed_and_put_back():
    vm = Vm(); t = T.Themes(vm)
    assert t.set("music:prev", "yellow") == {"music:prev": "yellow"}
    assert t.set("drive:shuffle-library", "psychedelic")["drive:shuffle-library"] == "psychedelic"
    assert t.set("music:prev", "default") == {"drive:shuffle-library": "psychedelic"}
    assert json.loads(vm.md[T.KEY]) == {"drive:shuffle-library": "psychedelic"} and t.all() == {"drive:shuffle-library": "psychedelic"}


def test_only_known_themes_and_ids_and_an_unreadable_store_changes_nothing():
    t = T.Themes(Vm())
    for bad in (("music:prev", "chartreuse"), ("<script>", "yellow"), ("", "yellow")):
        with pytest.raises(ValueError):
            t.set(*bad)
    vm = Vm(); vm.md[T.KEY] = "{oops"
    assert T.Themes(vm).all() == {}
    with pytest.raises(modules_mod.Unavailable):
        T.Themes(vm).set("music:prev", "yellow")


def test_every_theme_has_a_look_and_the_picker_has_its_doors():
    css = (PORTAL / "static" / "bridge.css").read_text(encoding="utf-8")
    js = (PORTAL / "static" / "keys.js").read_text(encoding="utf-8")
    for c in T.COLOURS:
        assert f'[data-theme="{c}"]:not(#_) {{ --accent: {T.COLOURS[c]} !important; }}' in css
    fancy = (PORTAL / "static" / "themes-fancy.css").read_text(encoding="utf-8")
    for f in T.FANCY:
        assert f'.unit[data-theme="{f}"]:not(#_)' in fancy and f"{f}:" in js      # its look, and its swatch (bars, no gradient)
    assert not re.search(r"(?:linear|radial|conic)-gradient", css)                # the bridge itself draws none
    assert 'b.className = "theme-corner"' in js and "picker(k, k); }, 550)" in js        # the corner door, and a long press on a key
    base = (PORTAL / "templates" / "base.html").read_text(encoding="utf-8")
    assert "window.SKIPPY_THEMES = {{ control_themes | tojson }}" in base and "{% if is_admin %} window.SKIPPY_THEME_PICK" in base


pytest.importorskip("flask")
pytest.importorskip("authlib")
import app as portal  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402


def test_the_captain_sets_a_theme_and_every_page_carries_it():
    vm = FakeVm()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy())
    c = app.test_client(); login(c, ADMIN)
    assert c.post("/themes", json={"id": "music:prev", "theme": "blue"}).get_json() == {"themes": {"music:prev": "blue"}}
    assert c.post("/themes", json={"id": "music:prev", "theme": "plaid"}).status_code == 400
    page = c.get("/apps").get_data(as_text=True)
    assert 'window.SKIPPY_THEMES = {"music:prev": "blue"}' in page and "SKIPPY_THEME_PICK" in page
    friend = app.test_client(); login(friend, FRIEND)
    assert friend.post("/themes", json={"id": "music:prev", "theme": "red"}).status_code in (302, 401, 403)   # his alone to change
    fp = friend.get("/apps").get_data(as_text=True)
    assert 'window.SKIPPY_THEMES = {"music:prev": "blue"}' in fp and "SKIPPY_THEME_PICK" not in fp           # but everyone sees it


def test_the_fancy_sheet_loads_only_where_a_control_wears_one_or_for_the_picker():
    vm = FakeVm()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy())
    friend = app.test_client(); login(friend, FRIEND)
    assert "themes-fancy.css" not in friend.get("/apps").get_data(as_text=True)
    vm.metadata[T.KEY] = json.dumps({"tile:music": "psychedelic"}); app.extensions["themes"]._cache = None
    assert "themes-fancy.css" in friend.get("/apps").get_data(as_text=True)


def test_the_palette_has_the_captains_colours_in_order():
    assert list(T.COLOURS)[:5] == ["red", "orange", "yellow", "green", "blue"] and T.COLOURS["yellow"] == "#FFE600"
    assert T.COLOURS["blue"] == "#2E8BFF" and "purple" in T.COLOURS and "amber" not in T.COLOURS and "cyan" not in T.COLOURS
