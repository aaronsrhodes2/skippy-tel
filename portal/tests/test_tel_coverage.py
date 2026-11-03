"""Every control is counted (the Captain, 2026-11-02: "make sure we have telemetry on every control"), so the nightly tidy
(adaptive.py) can see what nobody uses and retire it. A template control carries data-tel (tools/tel_tag.py adds one); a key a
script builds carries data-tel, or a data-act / data-u that keys.js turns into <app>:<act>. A new control without one fails here."""
import re
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORTAL.parent / "tools"))
import tel_tag  # noqa: E402


def test_every_template_control_has_a_telemetry_id():
    missing = {}
    for path in sorted((PORTAL / "templates").glob("*.html")):
        if path.name in tel_tag.SKIP_FILES:
            continue
        _, added = tel_tag.plan(path)
        if added:
            missing[path.name] = added
    assert not missing, f"controls without data-tel (run python tools/tel_tag.py --write): {missing}"


MEDIA_CLASSES = {"tr-main", "nv-main", "q-main", "film", "tt-herocard"}


def test_every_key_a_script_builds_is_named():
    bad = []
    for path in sorted((PORTAL / "static").glob("*.js")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r'createElement\("button"\)|\bel\("button", "([^"]*)"', src):
            if (m.group(1) or "").split(" ")[0] in MEDIA_CLASSES:
                continue                                     # a row, a cell, a poster, a hero card, a menu entry: media, never named
            near = src[m.end():m.end() + 400]
            if not re.search(r'data-tel|dataset\.tel|dataset\.act|dataset\.u\b|dataset\.r\b|"cell"', near):
                bad.append(f"{path.name}:{src.count(chr(10), 0, m.start()) + 1}")
        for m in re.finditer(r"<button\b[^>`]*>", src):
            in_comment = src.rfind("/*", 0, m.start()) > src.rfind("*/", 0, m.start())
            if not in_comment and not re.search(r"data-(tel|act|u)=", m.group(0)):
                bad.append(f"{path.name}:{src.count(chr(10), 0, m.start()) + 1}")
    assert not bad, f"script-built buttons with no data-tel, data-act or data-u: {bad}"


def test_keys_js_names_acted_keys_and_deck_units():
    js = (PORTAL / "static" / "keys.js").read_text(encoding="utf-8")
    assert '"deck-" + el.dataset.u' in js and 'el.setAttribute("data-tel", app + ":"' in js


def test_retired_controls_are_hidden_on_every_page():
    base = (PORTAL / "templates" / "base.html").read_text(encoding="utf-8")
    assert '[data-tel="{{ h }}"], .unit:has(> [data-tel="{{ h }}"])' in base
