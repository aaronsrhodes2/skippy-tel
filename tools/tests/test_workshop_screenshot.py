"""tools/workshop_screenshot.py: the private Skippy's "show me the result". It renders the checkout's portal with the
test fakes, signed in as the tests' scratch admin, on loopback only, and has a headless browser already on the PC take
the shot. Here the browser is a stand-in script, so nothing real is launched or installed."""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

from tools import workshop_screenshot as shot  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_find_browser_takes_chrome_env_then_the_usual_places_then_path(tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    assert shot.find_browser({"CHROME": str(exe)}, (), lambda n: None) == str(exe)
    assert shot.find_browser({"CHROME": str(tmp_path / "missing")}, (str(exe),), lambda n: "x") is None   # a wrong CHROME is not guessed around
    assert shot.find_browser({}, ("/nope", str(exe)), lambda n: None) == str(exe)
    assert shot.find_browser({}, (), lambda n: "/usr/bin/chromium" if n == "chromium" else None) == "/usr/bin/chromium"
    assert shot.find_browser({}, (), lambda n: None) is None


@pytest.mark.parametrize("argv", [["me", "--out", "x.jpg"], ["//evil.example/", "--out", "x.png"],
                                  ["C:/Program Files/Git/me", "--out", "x.png"], ["me", "--out", "x.png", "--width", "5"]])
def test_bad_arguments_exit_2(argv):
    assert shot.main(argv) == 2


def test_no_browser_exits_3_and_installs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(shot, "find_browser", lambda: None)
    assert shot.main(["me", "--out", "x.png"]) == 3
    assert "Nothing was installed" in capsys.readouterr().err


def test_the_page_is_the_checkouts_portal_signed_in_as_the_scratch_admin():
    app = shot.make_app()
    client = app.test_client()
    page = client.get("/me")
    assert page.status_code == 200 and b'id="private-skippy"' in page.data       # the admin dashboard, no Google sign-in
    assert app.config["SECRET_KEY"] != "test" and len(app.config["SECRET_KEY"]) == 64   # a fresh key, not the tests' known one


FAKE_BROWSER = textwrap.dedent("""
    import sys, urllib.request
    args = sys.argv[1:]
    url = args[-1]
    out = next(a.split("=", 1)[1] for a in args if a.startswith("--screenshot="))
    assert "--headless=new" in args and any(a.startswith("--user-data-dir=") for a in args)
    assert url.startswith("http://127.0.0.1:")                     # loopback only
    body = urllib.request.urlopen(url, timeout=10).read()
    assert b'id="private-skippy"' in body
    open(out, "wb").write(PNG_BYTES)
""")


def fake_browser(tmp_path, png=PNG):
    script = tmp_path / "fake_browser.py"
    script.write_text(FAKE_BROWSER.replace("PNG_BYTES", repr(png)), encoding="utf-8")
    return [sys.executable, str(script)]


def test_a_shot_is_taken_from_the_live_local_page_and_its_path_printed(tmp_path, capsys):
    out = tmp_path / "shots" / "me.png"
    assert shot.shoot("/me", out, 390, 844, fake_browser(tmp_path)) == 0
    assert out.read_bytes() == PNG and str(out.resolve()) in capsys.readouterr().out


def test_a_shot_that_is_not_a_png_or_too_big_fails(tmp_path):
    assert shot.shoot("/me", tmp_path / "a.png", 390, 844, fake_browser(tmp_path, b"<html>")) == 4
    big = PNG + b"\x00" * shot.MAX_BYTES
    assert shot.shoot("/me", tmp_path / "b.png", 390, 844, fake_browser(tmp_path, big)) == 4


def test_a_browser_that_cannot_start_fails_cleanly(tmp_path):
    assert shot.shoot("/me", tmp_path / "c.png", 390, 844, [str(tmp_path / "no-such-browser")]) == 4
