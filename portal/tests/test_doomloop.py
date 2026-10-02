"""Doom Loop dev channel (/doomloop): the portal hands the newest build of the Captain's work-in-progress game to every
signed-in person, and to nobody else.

The game is built and tested in its own repo and uploaded to a GCS folder; the portal reads that folder at request time.
What can be checked here without a bucket is: who gets in, that the page carries the portal's app bar and a dev badge, that
each kind of file goes out with the type the browser needs, that nothing outside the folder can be read, that a new build
is picked up without a redeploy, and that a portal with no build says so instead of breaking."""
import json
import re

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import doomloop  # noqa: E402
from test_app import ADMIN, FRIEND, PAL, FakeSkippy, FakeVm, SETTINGS, _add_friend, login  # noqa: E402

import app as portal  # noqa: E402

BUILD = "abc1234def56"
COMMIT = "abc1234def567890"
JS = b"export const x = 1;\n"
WASM = b"\x00asm\x01\x00\x00\x00"


class FakeStore:
    """Stands in for the bucket folder: a dict of path -> bytes."""

    def __init__(self, files):
        self.files = dict(files)
        self.reads = []

    def read(self, path):
        self.reads.append(path)
        return self.files.get(path)


def _files(build=BUILD, commit=COMMIT):
    return {
        "index.html": b"<html><head></head><body><canvas id='c'></canvas><script type='module' src='assets/game.js'></script></body></html>",
        "assets/game.js": JS,
        "assets/sim.wasm": WASM,
        "version.json": json.dumps({"build": build, "commit": commit, "built_at": "2026-10-02T00:00:00Z",
                                    "branch": "main", "secret_path": "/etc/passwd"}).encode(),
    }


def _client():
    return portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy()).test_client()


@pytest.fixture(autouse=True)
def _fresh_cache():
    doomloop.reset_cache()
    yield
    doomloop.reset_cache()


@pytest.fixture
def store(monkeypatch):
    s = FakeStore(_files())
    monkeypatch.setattr(doomloop, "get_store", lambda: s)
    return s


@pytest.fixture
def client(store):
    """A portal whose dev channel holds a build."""
    return _client()


@pytest.fixture
def bare(monkeypatch):
    """A portal with no bucket configured (tests, local runs, before the first build)."""
    monkeypatch.delenv(doomloop.BUCKET_ENV, raising=False)
    return _client()


# ── who gets in ────────────────────────────────────────────────────────────────

def test_signed_out_gets_sign_in_not_the_game(client):
    for path in ("/doomloop/", "/doomloop/assets/game.js", "/doomloop/version.json", "/doomloop/assets/sim.wasm"):
        r = client.get(path)
        assert r.status_code == 302, path
        assert JS not in r.data and WASM not in r.data


def test_a_visitor_waiting_for_access_does_not_get_the_game(client):
    with client.session_transaction() as s:
        s["visitor"] = {"email": "stranger@example.com", "name": "Stranger", "picture": None}
    assert client.get("/doomloop/").status_code == 302
    assert client.get("/doomloop/assets/game.js").status_code == 302


def test_every_signed_in_person_can_play_including_a_friend_with_one_game(client):
    for who in (ADMIN, FRIEND):
        login(client, who)
        assert client.get("/doomloop/").status_code == 200
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    assert client.get("/doomloop/").status_code == 200
    assert client.get("/doomloop/assets/game.js").data == JS


# ── the page ───────────────────────────────────────────────────────────────────

def test_the_page_is_the_builds_own_with_the_portals_app_bar_and_dev_badge(client):
    login(client, FRIEND)
    r = client.get("/doomloop/")
    page = r.data.decode()
    assert "<canvas id='c'>" in page                                                   # the game's page, as built
    assert '<a class="back" href="/play">&lsaquo; Browser Games</a>' in page and 'id="home-btn"' in page
    assert '<h1 class="apptitle">Doom Loop</h1>' in page and 'data-app="play"' in page
    assert '<span id="sp-badge">dev build abc1234</span>' in page                       # which commit this is
    assert page.index('id="sp-bar"') < page.index("</body>")
    assert not re.search(r"gradient", page)
    assert r.headers["Cache-Control"] == "no-cache"                                    # a new build shows on the next visit
    assert r.mimetype == "text/html"


def test_the_badge_text_comes_from_a_file_so_it_is_checked_and_escaped(store, client):
    store.files["version.json"] = json.dumps({"build": BUILD, "commit": "<script>alert(1)</script>"}).encode()
    login(client, FRIEND)
    page = client.get("/doomloop/").data.decode()
    assert "<script>alert(1)" not in page
    assert '<span id="sp-badge">dev build</span>' in page                               # no valid commit: a plain badge


def test_the_page_takes_no_query_string(client):
    login(client, FRIEND)
    r = client.get("/doomloop/?x=<script>")
    assert r.status_code == 302 and r.headers["Location"].endswith("/doomloop/")


def test_without_the_trailing_slash_it_redirects_so_relative_files_resolve(client):
    login(client, FRIEND)
    r = client.get("/doomloop")
    assert r.status_code in (301, 308) and r.headers["Location"].endswith("/doomloop/")


# ── the files ──────────────────────────────────────────────────────────────────

def test_each_file_goes_out_with_the_type_the_browser_needs(client):
    login(client, FRIEND)
    js = client.get("/doomloop/assets/game.js")
    assert js.data == JS and js.mimetype == "text/javascript"
    wasm = client.get("/doomloop/assets/sim.wasm")
    assert wasm.data == WASM and wasm.mimetype == "application/wasm"
    assert "Content-Encoding" not in wasm.headers


def test_files_are_revalidated_so_a_new_build_needs_no_cache_clearing(client):
    login(client, FRIEND)
    first = client.get("/doomloop/assets/game.js")
    assert first.headers["Cache-Control"] == "no-cache" and first.headers.get("ETag")
    again = client.get("/doomloop/assets/game.js", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304


def test_a_missing_file_is_a_404(client):
    login(client, FRIEND)
    assert client.get("/doomloop/assets/nothing.js").status_code == 404


@pytest.mark.parametrize("path", [
    "../app.py", "assets/../../app.py", "%2e%2e/app.py", "assets/%2e%2e/%2e%2e/app.py",
    "..%2fapp.py", "assets\\..\\app.py", "assets//game.js",
])
def test_nothing_outside_the_folder_can_be_read(client, store, path):
    login(client, FRIEND)
    store.reads.clear()
    r = client.get("/doomloop/" + path)
    assert r.status_code in (301, 308, 404), path
    assert not [p for p in store.reads if ".." in p or p.startswith("/") or "//" in p]   # storage was never asked for such a path


@pytest.mark.parametrize("path", ["./assets/game.js", "assets/./game.js"])
def test_dot_segments_never_reach_storage_unclean(client, store, path):
    """Werkzeug may tidy these before the route sees them; either way storage is only ever asked for a clean path."""
    login(client, FRIEND)
    store.reads.clear()
    r = client.get("/doomloop/" + path)
    assert r.status_code in (200, 301, 308, 404)
    assert all(doomloop.clean_path(p) == p for p in store.reads)


def test_clean_path_unit_cases():
    assert doomloop.clean_path("assets/game.js") == "assets/game.js"
    for bad in ("", "/etc/passwd", "../x", "a/../b", "a//b", "a\\b", "a\x00b", ".", "a/./b"):
        assert doomloop.clean_path(bad) is None, repr(bad)


# ── versions and new builds ────────────────────────────────────────────────────

def test_version_is_public_and_shows_only_the_public_fields(client):
    r = client.get("/doomloop/version")                       # no sign-in: the deploy's smoke test reads it
    body = r.get_json()
    assert r.status_code == 200 and body["bundled"] is True and body["build"] == BUILD and body["commit"] == COMMIT
    assert "secret_path" not in body
    assert r.headers["Cache-Control"] == "no-store"


def test_a_new_build_shows_after_the_short_version_cache_without_any_redeploy(store, client, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(doomloop.time, "monotonic", lambda: clock["t"])
    login(client, FRIEND)
    assert "dev build abc1234" in client.get("/doomloop/").data.decode()
    store.files.update(_files(build="newbuild9999", commit="9999999aaaaaaaa"))
    assert "dev build abc1234" in client.get("/doomloop/").data.decode()          # still the cached version: no flapping
    clock["t"] += doomloop.VERSION_TTL + 1
    assert "dev build 9999999" in client.get("/doomloop/").data.decode()


def test_a_damaged_version_file_means_no_build(store, client):
    store.files["version.json"] = b"not json {"
    login(client, FRIEND)
    assert client.get("/doomloop/").status_code == 503
    assert client.get("/doomloop/version").get_json()["bundled"] is False


def test_a_version_without_a_safe_build_id_means_no_build(store, client):
    store.files["version.json"] = json.dumps({"build": "../../etc", "commit": "abc1234"}).encode()
    login(client, FRIEND)
    assert client.get("/doomloop/").status_code == 503


# ── no build yet ───────────────────────────────────────────────────────────────

def test_a_portal_with_no_bucket_says_there_is_no_build_instead_of_breaking(bare):
    login(bare, FRIEND)
    r = bare.get("/doomloop/")
    assert r.status_code == 503 and b"no Doom Loop build yet" in r.data
    assert bare.get("/doomloop/assets/game.js").status_code == 404
    v = bare.get("/doomloop/version")
    assert v.status_code == 200 and v.get_json() == {"bundled": False}


def test_an_unreadable_bucket_is_logged_not_raised(monkeypatch):
    monkeypatch.setenv(doomloop.BUCKET_ENV, "no-such-bucket-here")
    monkeypatch.setattr(doomloop.GcsStore, "read", lambda self, path: None)       # what read() does when it can't reach it
    c = _client()
    login(c, FRIEND)
    assert c.get("/doomloop/").status_code == 503


def test_gcs_store_turns_any_failure_into_none(monkeypatch):
    s = doomloop.GcsStore("b", "dev/")
    monkeypatch.setitem(__import__("sys").modules, "google.cloud.storage", None)   # library missing: ImportError
    assert s.read("index.html") is None


# ── the Browser Games card ─────────────────────────────────────────────────────

def test_the_card_says_coming_soon_until_there_is_a_build_then_links_to_it(client, store):
    login(client, FRIEND)
    with_build = client.get("/play").data.decode()
    assert 'href="/doomloop/"' in with_build and "Doom Loop" in with_build
    store.files.clear()
    doomloop.reset_cache()
    without = client.get("/play").data.decode()
    assert "Doom Loop" in without and 'href="/doomloop/"' not in without
