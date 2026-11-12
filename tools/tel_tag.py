"""Give every control in the portal's templates a telemetry id (data-tel), so the nightly tidy (portal/adaptive.py) can count it
and, when nobody uses it, retire it. Run once over the templates (2026-11-02); portal/tests/test_tel_coverage.py keeps it true after.

    python tools/tel_tag.py            # show what it would add
    python tools/tel_tag.py --write    # add them

An id is <page>:<name>. The name comes from the control's own id attribute, else the fixed part of its aria-label/title, else its
glyph; the page is the template's name. Controls repeated per row in a loop share one id per action (a tap on any friend's
"remove" is admin:remove), so no names or titles from the page ever reach telemetry."""
from __future__ import annotations

import re
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "portal" / "templates"
CONTROL = re.compile(r"<(button|a|select|input)\b[^>]*>", re.S)
GENERIC = {"", "a", "button", "k", "input", "select", "submit"}
SKIP_FILES = {"_glyphs.html", "tabletop_print.html", "privacy.html"}


def is_control(tag: str, kind: str) -> bool:
    if kind == "input":
        return bool(re.search(r'type="(checkbox|range|radio|submit|button)"', tag))
    if kind == "a":
        return bool(re.search(r'class="[^"]*\b(key|unit|hit|tile|chip|btn)\b', tag))
    return True


def slug(text: str) -> str:
    text = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", text)          # the dynamic parts never become an id
    text = re.sub(r"\(.*?\)|:.*$", " ", text)
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:4])[:40]


def name_for(tag: str) -> str:
    for attr in ("id", "aria-label", "title", "name", "value"):
        m = re.search(rf'\b{attr}="([^"]*)"', tag)
        if m and slug(m.group(1)):
            return slug(m.group(1))
    m = re.search(r'data-(open|action|mode|close|go)(?:="([^"]*)")?', tag)
    if m:
        return slug(m.group(1) + " " + (m.group(2) or ""))
    return ""


def glyph_after(src: str, end: int) -> str:
    m = re.match(r'\s*(?:<[^>]*>\s*){0,3}?<svg[^>]*>\s*<use[^>]*href="#g-([a-z0-9-]+)"', src[end:end + 400], re.S)
    return m.group(1) if m else ""


def inside_tagged_unit(src: str, at: int) -> bool:
    idx = src.rfind('class="unit', 0, at)
    if idx < 0 or at - idx > 700 or src.rfind("</div>", idx, at) > src.rfind("<i class=\"plate", idx, at) + 10**9:
        return False
    start, end = src.rfind("<", 0, idx), src.find(">", idx)
    return "data-tel" in src[start:end]


# names the markup can't give (a control built in a loop whose label is data): (template, generated name) -> the id
HAND = {
    ("admin.html", ""): "",
}


def plan(path: Path) -> tuple[str, list[str]]:
    src = path.read_text(encoding="utf-8")
    page = path.stem.lstrip("_").replace("_", "-")
    out, added, pos = [], [], 0
    for m in CONTROL.finditer(src):
        tag, kind = m.group(0), m.group(1)
        if "data-tel" in tag or "data-act=" in tag or not is_control(tag, kind):
            continue                                       # data-act controls are named <app>:<act> by base.html's beacon
        if inside_tagged_unit(src, m.start()):
            continue                                       # the card around it has the id (tile:x, play:x): never steal its taps
        name = HAND.get((path.name, name_for(tag)), "") or name_for(tag)
        pane = re.search(r'data-pane="(\{\{\s*\w+\s*\}\})"', tag)
        if pane:                                           # a deck built in a loop: the pane's own id (template data, not people's)
            name = pane.group(1)
        if name in GENERIC:
            text = re.match(r"([^<]{1,40})</(button|a)>", src[m.end():m.end() + 60])
            name = glyph_after(src, m.end()) or (slug(text.group(1)) if text else "") or name or kind
        tel = f"{page}:{name}"
        new = tag[:len(kind) + 1] + f' data-tel="{tel}"' + tag[len(kind) + 1:]
        out.append(src[pos:m.start()] + new)
        pos = m.end()
        added.append(tel)
    out.append(src[pos:])
    return "".join(out), added


def main(argv=None) -> int:
    write = "--write" in (argv if argv is not None else sys.argv[1:])
    for path in sorted(TEMPLATES.glob("*.html")):
        if path.name in SKIP_FILES:
            continue
        new, added = plan(path)
        if added:
            print(f"{path.name}: {', '.join(added)}")
            if write:
                path.write_text(new, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
