"""Toggles and clickers look different (the Captain, 2026-11-02): a toggle (aria-pressed) is a hexagon with two looks, a clicker
keeps the cut corner and breathes while busy (static/keys.js); a lamp with no state is not shown; a key never goes black on hover.
The behaviour is in tests/js/keys.test.js; these check the pieces are wired into the pages."""
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
CSS = (PORTAL / "static" / "bridge.css").read_text(encoding="utf-8")


def test_toggles_are_hexagons_with_two_looks():
    assert '.key[aria-pressed]::before, .key[aria-pressed]::after { clip-path: polygon(25% 0, 75% 0, 100% 50%' in CSS
    assert ".key[aria-pressed] .lamp, .keybar .key[aria-pressed] .lamp { display: none; }" in CSS
    assert '.keybar .key[aria-pressed="false"].lit::after' in CSS          # "lit" alone can't make a third look


def test_keys_never_go_black_on_hover():
    assert ".key:hover, button.key:hover { background: transparent; color: var(--accent); }" in CSS


def test_a_stateless_lamp_is_hidden_and_busy_shows():
    assert ".unit:not([data-s]):not([data-busy]) > .lamp { visibility: hidden; }" in CSS
    assert ".key[data-busy] .g, .unit[data-busy] .g" in CSS


def test_every_page_loads_keys_js():
    assert "filename='keys.js'" in (PORTAL / "templates" / "base.html").read_text(encoding="utf-8")


def test_the_voice_key_keeps_one_glyph():
    js = (PORTAL / "static" / "skippy-voice.js").read_text(encoding="utf-8")
    assert 'use.setAttribute("href", "#g-ear")' in js and 'hands ? "#g-mic" : "#g-ear"' not in js
    assert "if (twoMode) paintKey(); else paint(mic, null);" in js     # Hands-off ends on Typing's own look


def test_drive_has_a_favorites_key():
    html = (PORTAL / "templates" / "drive.html").read_text(encoding="utf-8")
    js = (PORTAL / "static" / "drive-app.js").read_text(encoding="utf-8")
    # one trio key down the left edge: play/pause, next, the star (the Captain, 2026-11-02), first in the deck
    deck = html[html.index('<div class="deck">'):]
    assert deck.index('id="k-play"') < deck.index('id="k-book"') and "#k-play { grid-column: 1; grid-row: 1 / span 2; }" in html
    assert all(f'class="hit {h}"' in html for h in ("play", "next", "fav")) and "data-fav-add=" in html and "music.favorites_add" in html
    assert "postJSON(cfg.favAdd" in js and 'busy("#k-lib"' in js and 'data-tap="' in html and "sendBeacon(cfg.tap" in js


def test_the_steering_wheel_knob_skips_whichever_signal_the_car_sends():
    js = (PORTAL / "static" / "drive-app.js").read_text(encoding="utf-8")
    for action in ("nexttrack", "seekforward", "previoustrack", "seekbackward"):
        assert f'set("{action}"' in js
    assert "MediaTrackNext: next" in js and "MediaFastForward: next" in js
    assert '"drive:in-" + what' in js and 'heard("system-pause")' in js      # one drive tells which signal the knob sends
