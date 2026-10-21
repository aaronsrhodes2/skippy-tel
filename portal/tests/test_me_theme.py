"""/me is deep purple on black with white text and Skippy's living background in purple. It is its own app (data-app="me") whose
tokens are redefined in bridge-panes.css, so nothing else on the portal changes; the colours must stay readable on black."""
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402

import app as portal  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "static"


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4   # noqa: E731
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def theme_block() -> dict:
    css = (STATIC / "bridge-panes.css").read_text(encoding="utf-8")
    block = re.search(r'\n\[data-app="me"\], html\.agent body\.captain \{(.*?)\}', css, re.S).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def test_me_is_its_own_app_with_the_living_background_in_purple():
    app = portal.create_app(SETTINGS, vm=FakeVm(), skippy=FakeSkippy())
    c = app.test_client()
    login(c, ADMIN)
    page = c.get("/me").data.decode()
    assert 'data-app="me"' in page and '<canvas class="alive"' in page
    front = c.get("/chat").data.decode()                                                     # the Captain's front page wears the same purple, but only in agent mode
    assert 'class="bridge captain"' in front and '<canvas class="alive"' in front and "#a64dff" in front
    assert re.search(r'Bridge\.alive\(document\.querySelector\("canvas\.alive"\), document\.documentElement\.classList\.contains\("agent"\)', front)
    assert page.index("bridge-alive.js") < page.index("Bridge.alive(")             # loaded before it is called


def test_the_palette_is_purple_the_words_are_white_and_both_are_readable_on_black():
    t = theme_block()
    assert t["--ink"].lower() == "#ffffff"
    assert contrast(t["--ink"], "#000000") >= 15
    for name in ("--accent", "--green", "--cyan", "--violet", "--pink", "--muted"):
        assert contrast(t[name], "#000000") >= 4.5, name                          # AA for text on the black base
    for name in ("--accent", "--green", "--violet"):                               # purple: blue channel above red, red above green
        r, g, b = (int(t[name][i:i + 2], 16) for i in (1, 3, 5))
        assert b > r > g, name


def test_only_me_is_themed_and_the_default_background_keeps_its_neon():
    css = (STATIC / "bridge-panes.css").read_text(encoding="utf-8")
    assert css.count('[data-app="me"]') >= 2 and "[data-app=\"admin\"]" not in css.split("/me: deep gonzo purple")[1]
    js = (STATIC / "bridge-alive.js").read_text(encoding="utf-8")
    assert 'var COLORS = ["#00ff88"' in js and "opts.colors" in js                  # Skippy's own page still gets the neon


def test_the_public_chat_stays_green_even_for_the_captain_purple_is_the_coding_backend_mode():
    css = (STATIC / "bridge-panes.css").read_text(encoding="utf-8")
    theme = css.split("/me: deep gonzo purple")[1].split("the front page: which Skippy")[0]
    assert re.search(r"(?<!html\.agent )body\.captain", theme) is None                  # no purple rule for body.captain on its own
    assert theme.count("html.agent body.captain") >= 6
    app = portal.create_app(SETTINGS, vm=FakeVm(), skippy=FakeSkippy())
    c = app.test_client()
    login(c, ADMIN)
    front = c.get("/chat").data.decode()
    assert 'classList.add("agent")' in front and 'localStorage.getItem("skippy-chat-brain")' in front and '.indexOf("agent-") === 0' in front   # an agent brain is purple before paint
    assert 'classList.toggle("agent", !!agent)' in front and "alive.setColors(agent ? PURPLES : null)" in front and "window.skippyTheme(agent)" in front   # any other brain goes green
    friend_page = c.get("/me").data.decode()
    assert 'data-app="me"' in friend_page                                                   # /me stays purple by its own app theme
    assert "setColors" in (STATIC / "bridge-alive.js").read_text(encoding="utf-8")
