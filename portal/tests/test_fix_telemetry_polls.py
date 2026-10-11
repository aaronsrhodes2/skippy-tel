"""A page that polls must never be counted as a page view.

The defect: telemetry_web records every signed-in GET that answers text/html as a 'page' event, and lets the polls through only
because they answer JSON or are named in SKIP_ENDPOINTS. The Server admin card's 60-second refresh (server_admin.fragment, an HTML
fragment) was written in another branch and never added to that list, so an open admin tab recorded a fake page view a minute:
they topped 'Top pages', filled the buffer, and made /privacy ("polls never show up") untrue.

Two layers, so the class cannot come back:
  1. the named endpoint is in SKIP_ENDPOINTS (and a poll of it records nothing);
  2. a scan of every template finds every endpoint a script asks for by itself (fetch / sendBeacon / setInterval targets built
     with url_for, data-* attributes a script reads, literal paths), asks the running app what each one answers, and fails unless it
     is skipped or is not a page. A new poller that answers HTML fails here until it is added to SKIP_ENDPOINTS.
"""
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

from flask import url_for  # noqa: E402

import app as portal  # noqa: E402
import experiments  # noqa: E402
import telemetry_web  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402

PORTAL = Path(__file__).resolve().parents[1]
TEMPLATES = PORTAL / "templates"
PAL = "pal@example.com"

SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
URL_FOR = re.compile(r"""url_for\(\s*['"]([\w.]+)['"]""")
DATA_ATTR = re.compile(r"""\bdata-([a-z][\w-]*)\s*=\s*"([^"]*)\"""")
LITERAL_CALL = re.compile(r"""(?:fetch|sendBeacon)\(\s*["'](/[^"'{}]*)["']|\.open\(\s*["'][A-Z]+["']\s*,\s*["'](/[^"'{}]*)["']""")
NETWORK = re.compile(r"\b(?:fetch|sendBeacon|XMLHttpRequest|EventSource|WebSocket)\b|setInterval\s*\(")
SAMPLE_ARGS = {"gid": "valheim", "game": "valheim", "game_id": "valheim", "slug": experiments.EXPERIMENTS[0].slug,
               "job_id": "abcdef12", "task": "abcdef12"}


def camel(name: str) -> str:
    head, *rest = name.split("-")
    return head + "".join(part.title() for part in rest)


def asked_for_by_scripts(text: str, url_map=None) -> set[str]:
    """Endpoints a template's own scripts request without a click: url_for() inside a <script>, a data-* URL the script reads
    (dataset.live / data-live), and a literal path in fetch()/sendBeacon()/open() (resolved through `url_map` when given)."""
    scripts = "\n".join(SCRIPT.findall(text))
    found = set(URL_FOR.findall(scripts))
    for attr, value in DATA_ATTR.findall(text):
        if URL_FOR.search(value) and (f"dataset.{camel(attr)}" in scripts or f"data-{attr}" in scripts):
            found.update(URL_FOR.findall(value))
    if url_map is not None:
        adapter = url_map.bind("localhost")
        for first, second in LITERAL_CALL.findall(scripts):
            path = first or second
            try:
                found.add(adapter.match(path, method="GET")[0])
            except Exception:   # noqa: BLE001 — a path nothing serves: name it so the test says so
                found.add(f"<no route for {path}>")
    return found


# A static script that makes network calls hides its URLs from a scan of the templates, UNLESS the page hands them over as data-*
# attributes: then the scan can read them as long as it is given the script too. These are the allowed ones: the script, and the
# template whose data-*-url attributes (url_for) are the portal URLs it uses. Anything else gets the failure below.
# (paths under static/; Nagatha's scripts and its self-hosted hls.js only talk to the video gate, whose address and pass come from
# the page's data-gate-url route, which the scan sees)
STATIC_SCRIPTS = {"nbody-flipbook.js": "demo_nbody.html", "bilby-app.js": "music.html", "nagatha-app.js": "video.html", "vendor/hls-1.6.15.min.js": "video.html"}


def template_text(template: Path) -> str:
    """A template, with the static script it carries as an inline one, so the scan sees what that script reads from data-*."""
    text = template.read_text(encoding="utf-8")
    for js, owner in STATIC_SCRIPTS.items():
        if template.name == owner:
            text += "\n<script>" + (PORTAL / "static" / js).read_text(encoding="utf-8") + "</script>"
    return text


def make(templates=None):
    vm = FakeVm()
    vm.metadata["portal-friends"] = json.dumps({PAL: {"portals": ["valheim", "skippy"], "added_by": ADMIN}})
    app = portal.create_app({**SETTINGS, "ALLOWED_EMAILS": frozenset({ADMIN}), "TELEMETRY_ENABLED": True},
                            vm=vm, skippy=FakeSkippy())
    client = app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
    return app, client, app.extensions["telemetry"]


def sample_url(app, endpoint):
    rules = [r for r in app.url_map.iter_rules() if r.endpoint == endpoint and "GET" in r.methods]
    if not rules:
        return None
    with app.test_request_context():
        return url_for(endpoint, **{a: SAMPLE_ARGS.get(a, "x") for a in rules[0].arguments})


def offenders(app, client, tel, endpoints, skip=None):
    """Polled endpoints that a signed-in person's poll would record as a page: not skipped, and they answer a page."""
    skip = telemetry_web.SKIP_ENDPOINTS if skip is None else skip
    bad = []
    for endpoint in sorted(endpoints):
        url = sample_url(app, endpoint)
        if url is None:
            continue                                             # not a GET: a poll by POST is an 'action', a different rule
        before = len(tel.since(tel.boot, 0, 500)["events"])
        resp = client.get(url, headers={"Accept": "text/html"})
        pages = [e for e in tel.since(tel.boot, 0, 500)["events"][before:] if e["kind"] == "page"]
        if endpoint not in skip and not url.startswith(telemetry_web.SKIP_PREFIXES) and (pages or (
                resp.mimetype == "text/html" and resp.status_code >= 300)):
            bad.append((endpoint, url, resp.status_code, resp.mimetype))
    return bad


# ── the named defect ────────────────────────────────────────────────────────────────────

def test_the_server_admin_cards_poll_is_not_recorded_as_a_page_view():
    app, client, tel = make()
    assert "server_admin.fragment" in telemetry_web.SKIP_ENDPOINTS
    for _ in range(3):
        assert client.get("/admin/games/valheim/panel").status_code == 200
    assert tel.since(tel.boot, 0)["events"] == []


def test_the_game_page_itself_is_still_a_page_view_and_its_polls_are_not():
    app, client, tel = make()
    client.get("/games/valheim")
    for path in ("/games/valheim/live", "/games/valheim/discord", "/admin/games/valheim/panel"):
        client.get(path)
    assert [(e["kind"], e["name"]) for e in tel.since(tel.boot, 0)["events"]] == [("page", "/games/valheim")]


# ── the class ─────────────────────────────────────────────────────────────────────────────

def test_the_scan_finds_every_kind_of_poll_the_templates_use():
    """It has to see the pollers that exist today, or a green result below would mean nothing."""
    app, *_ = make()
    found = {t.name: asked_for_by_scripts(template_text(t), app.url_map) for t in TEMPLATES.glob("*.html")}
    every = set().union(*found.values())
    assert "server_admin.fragment" in found["_server_admin.html"]                     # url_for inside a <script>
    assert {"dashboard.game_live", "dashboard.game_discord"} <= found["game.html"]
    assert "dashboard.game_live" in found["games.html"]                                # a data-live attribute the script reads
    assert {"experiments.wva_catalog", "experiments.wva_solve"} <= found["demo_weapon_vs_armor.html"]     # data-catalog / data-solve
    assert {"chat.badges", "telemetry_tap"} <= every
    assert {"music.player_gate", "music.player_checkin", "music.player_command"} <= found["music.html"]   # a STATIC script's data-*-url
    assert "dashboard.game" not in every and "admin.index" not in every              # a link is a page, not a poll


def test_the_scan_reads_the_three_ways_a_page_names_what_it_polls():
    app, *_ = make()
    text = ("""<a href="{{ url_for('dashboard.games') }}">back</a><div data-live="{{ url_for('dashboard.game_live', gid='v') }}" data-x="{{ url_for('privacy') }}">
              <script>const u = "{{ url_for('chat.send') }}"; fetch("/chat/badges"); x.open("GET", "/games/valheim/discord");
              tile.dataset.live; setInterval(poll, 1000)</script>""")
    assert asked_for_by_scripts(text, app.url_map) == {"chat.send", "chat.badges", "dashboard.game_discord", "dashboard.game_live"}
    assert asked_for_by_scripts('<script>fetch("/no/such/path")</script>', app.url_map) == {"<no route for /no/such/path>"}


def test_every_endpoint_a_template_polls_is_skipped_or_is_not_a_page():
    app, client, tel = make()
    polled = set()
    for template in TEMPLATES.glob("*.html"):
        polled |= asked_for_by_scripts(template_text(template), app.url_map)
    assert not {e for e in polled if e.startswith("<no route")}, polled
    assert len(polled) >= 10, polled                                                   # the scan is really finding them
    bad = offenders(app, client, tel, polled)
    assert not bad, (
        f"{bad}: a template's script requests these by itself and they answer a page, so every poll would be recorded as a page view. "
        "Add the endpoint to telemetry_web.SKIP_ENDPOINTS (or make it answer JSON).")


def test_the_scan_would_have_caught_the_original_defect_and_any_future_poller(monkeypatch):
    app, client, tel = make()
    app.add_url_rule("/ticker", "ticker", lambda: "<p>tick</p>")                      # a poller somebody adds next month
    polled = {"ticker"}
    for template in TEMPLATES.glob("*.html"):
        polled |= asked_for_by_scripts(template_text(template), app.url_map)
    assert [b[0] for b in offenders(app, client, tel, polled)] == ["ticker"]           # the list as it is: only the new one
    monkeypatch.setattr(telemetry_web, "SKIP_ENDPOINTS",                                 # the list as it was: the recorder and the scan
                        frozenset(telemetry_web.SKIP_ENDPOINTS - {"server_admin.fragment"}))     # both see the gap
    assert [b[0] for b in offenders(app, client, tel, polled)] == ["server_admin.fragment", "ticker"]


def test_no_script_file_in_static_makes_network_calls_the_template_scan_cannot_see():
    """The scan reads templates. A static .js file that fetches/polls would hide its URLs from it: add them to the scan first."""
    for js in (PORTAL / "static").rglob("*.js"):
        rel = js.relative_to(PORTAL / "static").as_posix()
        if rel in STATIC_SCRIPTS:                          # allowed: its URLs are data-*-url attributes, which template_text() scans
            owner = (TEMPLATES / STATIC_SCRIPTS[rel]).read_text(encoding="utf-8")
            assert f"filename='{rel}'" in owner, f"{rel} isn't loaded by {STATIC_SCRIPTS[rel]}"
            continue
        assert not NETWORK.search(js.read_text(encoding="utf-8")), f"{js.name} makes network calls: teach this test about its URLs"
