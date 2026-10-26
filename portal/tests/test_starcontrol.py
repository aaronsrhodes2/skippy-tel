"""Star Control Zero in the browser (/starcontrol): the portal hands the game's bundled files to every signed-in person, and to
nobody else.

The game itself runs in a browser (WebAssembly) and is tested in its own repo. What can be checked here without one is: who gets
in, that the page carries the portal's app bar, that each kind of file goes out with the type and caching the browser needs (the
bundle parts untouched, the music with Range), that nothing outside the folder can be read, and that a build without the game
says so instead of breaking."""
import json
import re

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import starcontrol  # noqa: E402
from test_app import ADMIN, FRIEND, PAL, FakeSkippy, FakeVm, SETTINGS, _add_friend, login  # noqa: E402

import app as portal  # noqa: E402

BUILD = "0123abcd4567"
PART = bytes(range(256)) * 8                      # stands in for a slice of web.tar.gz: must arrive byte for byte
MP3 = b"ID3" + bytes(1000)


def _client():
    return portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy()).test_client()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A portal whose image carries the game."""
    (tmp_path / "index.html").write_text(
        '<html><head><link rel="icon" href="favicon.png"></head>'
        "<body><div id='gate'></div><iframe id='game'></iframe></body></html>", encoding="utf-8")
    (tmp_path / "game.html").write_text("<html><body>the game</body></html>", encoding="utf-8")
    (tmp_path / "web.tar.gz.part00").write_bytes(PART)
    (tmp_path / "assets" / "music" / "hyperspace").mkdir(parents=True)
    (tmp_path / "assets" / "music" / "hyperspace" / "bass.mp3").write_bytes(MP3)
    (tmp_path / "version.json").write_text(json.dumps({
        "build": BUILD, "commit": "abc1234", "built_at": "2026-10-01T00:00:00Z", "bundle_bytes": len(PART),
        "bundle_parts": 1, "music_tracks": 1, "music_bytes": len(MP3), "not_for_the_public": "x"}), encoding="utf-8")
    monkeypatch.setattr(starcontrol, "ROOT", tmp_path)
    return _client()


@pytest.fixture
def bare(tmp_path, monkeypatch):
    """A portal built without the game (tests, local runs, a release that failed to download)."""
    monkeypatch.setattr(starcontrol, "ROOT", tmp_path / "nothing-here")
    return _client()


# ── who gets in ────────────────────────────────────────────────────────────────

def test_signed_out_gets_sign_in_not_the_game(client):
    for path in ("/starcontrol/", "/starcontrol/game.html", "/starcontrol/web.tar.gz.part00",
                 "/starcontrol/assets/music/hyperspace/bass.mp3"):
        r = client.get(path)
        assert r.status_code == 302, path
        assert b"the game" not in r.data and PART[:64] not in r.data


def test_a_visitor_waiting_for_access_does_not_get_the_game(client):
    with client.session_transaction() as s:
        s["visitor"] = {"email": "stranger@example.com", "name": "Stranger", "picture": None}
    assert client.get("/starcontrol/").status_code == 302
    assert client.get("/starcontrol/game.html").status_code == 302


def test_every_signed_in_person_can_play_including_a_friend_with_one_game(client):
    for who in (ADMIN, FRIEND):
        login(client, who)
        assert client.get("/starcontrol/").status_code == 200
    _add_friend(client, PAL, ["dragonwilds"])                 # a friend granted one server game and nothing else
    login(client, PAL)
    assert client.get("/starcontrol/").status_code == 200
    assert client.get("/starcontrol/game.html").data == b"<html><body>the game</body></html>"


# ── the page ───────────────────────────────────────────────────────────────────

def test_the_page_is_the_games_own_with_the_portals_app_bar_on_top(client):
    login(client, FRIEND)
    r = client.get("/starcontrol/")
    page = r.data.decode()
    assert "<iframe id='game'>" in page                                        # the game's page, as shipped
    assert '<a class="back" href="/play">&lsaquo; Browser Games</a>' in page and 'id="home-btn"' in page   # back to its app, and Home
    assert '<h1 class="apptitle">Star Control Zero</h1>' in page and 'data-app="play"' in page
    assert page.index('id="sp-bar"') < page.index("</body>")
    assert "top: 44px" in page                                                 # the bar sits above the game, not over it
    assert not re.search(r"gradient", page)                                    # black base, no gradients
    assert r.headers["Cache-Control"] == "no-cache"                            # a new release shows on the next visit
    assert 'rel="icon" href="/static/favicon-32.png"' in page                  # the portal's public icon, not a gated file
    assert r.mimetype == "text/html"


def test_the_page_takes_no_query_string(client):
    login(client, FRIEND)
    r = client.get("/starcontrol/?scene=<script>")
    assert r.status_code == 302 and r.headers["Location"].endswith("/starcontrol/")


def test_without_the_trailing_slash_it_redirects_so_relative_files_resolve(client):
    login(client, FRIEND)
    r = client.get("/starcontrol")
    assert r.status_code in (301, 308) and r.headers["Location"].endswith("/starcontrol/")


# ── the files ──────────────────────────────────────────────────────────────────

def test_a_bundle_part_arrives_untouched_and_is_cached_only_when_it_names_this_build(client):
    login(client, FRIEND)
    r = client.get(f"/starcontrol/web.tar.gz.part00?v={BUILD}")
    assert r.status_code == 200 and r.data == PART
    assert r.mimetype == "application/octet-stream"
    assert "Content-Encoding" not in r.headers                # never "gzip": the browser must not unpack it itself
    assert "max-age=31536000" in r.headers["Cache-Control"]
    for stale in ("/starcontrol/web.tar.gz.part00", "/starcontrol/web.tar.gz.part00?v=someotherbuild"):
        assert "max-age=3600" in client.get(stale).headers["Cache-Control"]


def test_music_is_served_as_mp3_and_answers_range_requests(client):
    login(client, FRIEND)
    r = client.get("/starcontrol/assets/music/hyperspace/bass.mp3")
    assert r.status_code == 200 and r.mimetype == "audio/mpeg" and r.data == MP3
    part = client.get("/starcontrol/assets/music/hyperspace/bass.mp3", headers={"Range": "bytes=0-2"})
    assert part.status_code == 206 and part.data == b"ID3"


def test_nothing_outside_the_games_folder_can_be_read(client):
    login(client, ADMIN)
    for path in ("/starcontrol/../app.py", "/starcontrol/..%2Fapp.py", "/starcontrol/assets/../../app.py",
                 "/starcontrol/no-such-file.png"):
        assert client.get(path).status_code == 404, path


def test_file_types(client):
    assert starcontrol.mimetype_for("game.html").startswith("text/html")
    assert starcontrol.mimetype_for("assets/music/a/LEAD.MP3") == "audio/mpeg"
    assert starcontrol.mimetype_for("favicon.png") == "image/png"
    assert starcontrol.mimetype_for("web.tar.gz.part07") == "application/octet-stream"
    assert starcontrol.mimetype_for("web.tar.gz") == "application/octet-stream"


# ── the version probe (public: the deploy's smoke test reads it) ────────────────

def test_version_is_public_and_says_which_build_without_anything_else(client):
    r = client.get("/starcontrol/version")                    # signed out
    assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"]
    assert r.get_json() == {"bundled": True, "build": BUILD, "commit": "abc1234", "built_at": "2026-10-01T00:00:00Z",
                            "bundle_bytes": len(PART), "bundle_parts": 1, "music_tracks": 1, "music_bytes": len(MP3)}


def test_a_damaged_version_file_is_not_an_error(client, tmp_path):
    (tmp_path / "version.json").write_text("{not json", encoding="utf-8")
    assert client.get("/starcontrol/version").get_json() == {"bundled": True}
    (tmp_path / "version.json").write_text('["a list"]', encoding="utf-8")
    assert client.get("/starcontrol/version").get_json() == {"bundled": True}


# ── the card in Browser Games (/play), where the launcher sends people ──────────

def test_browser_games_links_the_game_when_it_is_bundled_for_everyone_signed_in(client):
    page = client.get("/play").data.decode()                                   # the list is public, like the experiments
    assert 'class="hit" href="/starcontrol/"' in page
    assert 'data-s="off" data-tel="play:star-control-zero"' not in page                # this game's card is lit, not another's
    assert 'data-app="starcontrol"' not in client.get("/").data.decode()       # no tile of its own on the launcher
    login(client, ADMIN)
    assert 'class="hit" href="/starcontrol/"' in client.get("/play").data.decode()
    _add_friend(client, PAL, ["dragonwilds"])                                  # a friend granted one server game and nothing else
    login(client, PAL)
    assert 'class="hit" href="/starcontrol/"' in client.get("/play").data.decode()
    assert client.get("/starcontrol/").status_code == 200


# ── a build without the game ───────────────────────────────────────────────────

def test_without_the_bundle_the_card_says_coming_soon_and_the_page_says_so(bare):
    assert bare.get("/starcontrol/version").get_json() == {"bundled": False}
    login(bare, ADMIN)
    card = bare.get("/play").data.decode()
    assert 'data-s="off" data-tel="play:star-control-zero"' in card                 # dark, with a diamond lamp: coming soon
    assert "/starcontrol/" not in card                                         # no link to nowhere
    r = bare.get("/starcontrol/")
    assert r.status_code == 503 and b"isn't bundled in this build yet" in r.data.replace(b"&#39;", b"'")
    assert b"&lsaquo; Home" in r.data                         # still a portal page with a way back
    assert bare.get("/starcontrol/game.html").status_code == 404


# ── the build-time fetch (portal/Dockerfile runs starcontrol_fetch.py) ──────────

def _archive(tmp_path, files, top="scz-web"):
    import hashlib
    import tarfile
    src = tmp_path / "src" / top
    for name, data in files.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(data)
    path = tmp_path / "scz-web.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        tar.add(src, arcname=top)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


GOOD = {"index.html": b"<html>", "game.html": b"<html>", "version.json": b"{}",
        "assets/music/hyperspace/bass.mp3": MP3, "web.tar.gz.part00": PART}


def test_fetch_unpacks_a_release_whose_checksum_matches(tmp_path):
    import starcontrol_fetch as sf
    path, sha = _archive(tmp_path, GOOD)
    dest = tmp_path / "image" / "starcontrol_static"
    assert sf.fetch(path.as_uri(), sha.upper() + "\n", dest) == len(GOOD)      # the hash's case and a newline don't matter
    assert (dest / "web.tar.gz.part00").read_bytes() == PART
    assert (dest / "assets" / "music" / "hyperspace" / "bass.mp3").read_bytes() == MP3
    assert not (dest / "scz-web").exists()                                     # the top folder became the destination


def test_fetch_refuses_a_release_that_was_swapped_and_leaves_nothing_behind(tmp_path):
    import starcontrol_fetch as sf
    path, _ = _archive(tmp_path, GOOD)
    dest = tmp_path / "starcontrol_static"
    with pytest.raises(sf.FetchError, match="checksum mismatch"):
        sf.fetch(path.as_uri(), "0" * 64, dest)
    assert not dest.exists()


def test_fetch_refuses_an_archive_that_is_not_the_game(tmp_path):
    import starcontrol_fetch as sf
    path, sha = _archive(tmp_path, {"index.html": b"<html>"})                  # no game.html, no version.json
    with pytest.raises(sf.FetchError, match="not a Star Control Zero web build"):
        sf.fetch(path.as_uri(), sha, tmp_path / "starcontrol_static")
    path, sha = _archive(tmp_path, GOOD, top="something-else")
    with pytest.raises(sf.FetchError, match="not a Star Control Zero web build"):
        sf.fetch(path.as_uri(), sha, tmp_path / "starcontrol_static")


def test_fetch_replaces_an_older_copy(tmp_path):
    import starcontrol_fetch as sf
    dest = tmp_path / "starcontrol_static"
    dest.mkdir()
    (dest / "left-over-from-the-last-release.txt").write_text("old")
    path, sha = _archive(tmp_path, GOOD)
    sf.fetch(path.as_uri(), sha, dest)
    assert not (dest / "left-over-from-the-last-release.txt").exists() and (dest / "game.html").is_file()


def test_the_fetch_command_builds_without_the_game_when_no_release_is_pinned(tmp_path, capsys):
    import starcontrol_fetch as sf
    assert sf.main(["starcontrol_fetch.py", "", "", str(tmp_path / "x")]) == 0
    assert "building without the game" in capsys.readouterr().out and not (tmp_path / "x").exists()
    assert sf.main(["starcontrol_fetch.py"]) == 2                              # wrong arguments: usage, non-zero


def test_the_image_and_the_deploy_pin_the_same_release(tmp_path):
    """portal/Dockerfile fetches the game; core/cd/deploy.json (skippy-core's deployer) says which release and its checksum, and its
    smoke test proves it shipped."""
    import json
    from pathlib import Path
    root = Path(portal.__file__).resolve().parents[1]
    docker = (root / "portal" / "Dockerfile").read_text(encoding="utf-8")
    cfg = json.loads((root / "core" / "cd" / "deploy.json").read_text(encoding="utf-8"))
    cd = (root / "core" / "cd" / "cd.py").read_text(encoding="utf-8")
    assert "starcontrol_fetch.py" in docker and "ARG STARCONTROL_REF" in docker and "ARG STARCONTROL_SHA256" in docker
    assert "github.com" not in docker and "source=.vendor" in docker           # the build reads what the deployer prepared, not the internet
    assert docker.index("starcontrol_fetch.py") < docker.index("COPY portal/ .")     # before the portal's files: stays cached
    assert cfg["pins"]["STARCONTROL_REF"] and re.fullmatch(r"[0-9a-f]{64}", cfg["pins"]["STARCONTROL_SHA256"])
    assert "/starcontrol/version" in cd                                        # the smoke test checks the game is bundled