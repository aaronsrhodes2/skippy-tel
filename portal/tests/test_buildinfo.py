"""Which build is this: /version (what tools/loop_timer.py polls) and the footer stamp (what the Captain reads).

The loop timer needs a public, uncached, secret-free answer shaped exactly as its contract says; the stamp must show on signed-in pages
only, carry the full commit and revision in its hover text, and stay out of the layout (portal colour tokens, its own line, wraps at 375 px).
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import buildinfo  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402
from test_telemetry import events, login as tel_login, make as tel_make  # noqa: E402

SHA = "8adcd7ee35c3bf0096bb2f95babb6db113eb5ef6"
STAMP = {"GIT_SHA": SHA, "BUILD_TIME": "2026-09-30T19:03:12Z", "K_REVISION": "skippy-portal-00042-abc"}
BASE_HTML = Path(__file__).resolve().parents[1] / "templates" / "base.html"


def build(monkeypatch, **env):
    """A portal as the image runs it: the three variables set (or removed) in the process environment."""
    for name in ("GIT_SHA", "BUILD_TIME", "K_REVISION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    return app, app.test_client()


def sign_in(client, email=ADMIN):
    with client.session_transaction() as s:
        s["user"] = {"email": email, "name": "Captain", "picture": None}


# ── buildinfo.info(): pure ─────────────────────────────────────────────────────────

def test_info_follows_the_contract_exactly():
    got = buildinfo.info(STAMP, started_at="2026-09-30T19:05:00Z")
    assert got == {"commit": SHA, "short": "8adcd7e", "built_at": "2026-09-30T19:03:12Z",
                   "revision": "skippy-portal-00042-abc", "started_at": "2026-09-30T19:05:00Z"}


def test_outside_the_image_everything_says_dev_or_is_empty():
    got = buildinfo.info({}, started_at="2026-09-30T19:05:00Z")
    assert got == {"commit": "dev", "short": "dev", "built_at": "", "revision": "", "started_at": "2026-09-30T19:05:00Z"}


@pytest.mark.parametrize("raw, shown", [
    ("2026-09-30T19:03:12Z", "2026-09-30T19:03:12Z"),
    ("2026-09-30T19:03:12+00:00", "2026-09-30T19:03:12Z"),
    ("2026-09-30T12:03:12-07:00", "2026-09-30T19:03:12Z"),            # always UTC in the answer
    ("2026-09-30T19:03:12.987654Z", "2026-09-30T19:03:12Z"),
    ("2026-09-30T19:03:12", ""),                                       # no zone: UTC or local? not guessed
    ("yesterday", ""), ("", ""), ("2026-09-30", ""),
])
def test_the_build_time_is_a_utc_instant_or_nothing(raw, shown):
    assert buildinfo.info({"BUILD_TIME": raw})["built_at"] == shown


@pytest.mark.parametrize("raw", ["unknown", "", "HEAD", "abc", "g" * 40, "8adcd7e; rm -rf /", "<script>", "x" * 70])
def test_a_commit_that_is_not_a_hex_id_is_dev(raw):
    got = buildinfo.info({"GIT_SHA": raw})
    assert got["commit"] == "dev" and got["short"] == "dev"


def test_the_commit_is_lower_cased_and_shortened_to_seven():
    got = buildinfo.info({"GIT_SHA": SHA.upper()})
    assert got["commit"] == SHA and got["short"] == SHA[:7]


@pytest.mark.parametrize("raw", ["a b", "Skippy-Portal", "../x", "x;y", "-lead", "9starts-with-digit", "a" * 70])
def test_a_revision_that_is_not_a_cloud_run_name_is_dropped(raw):
    assert buildinfo.info({"K_REVISION": raw})["revision"] == ""


def test_started_at_is_this_process_start_in_utc_and_does_not_change():
    first, second = buildinfo.info({})["started_at"], buildinfo.info({})["started_at"]
    assert first == second == buildinfo.STARTED_AT
    moment = datetime.strptime(first, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - moment).total_seconds()) < 24 * 3600


def test_the_label_and_the_tooltip():
    assert buildinfo.label("2026-09-30T19:03:12Z") == "2026-09-30 19:03 UTC" and buildinfo.label("") == ""
    tip = buildinfo.tooltip(buildinfo.info(STAMP))
    assert SHA in tip and "skippy-portal-00042-abc" in tip and "2026-09-30T19:03:12Z" in tip
    assert "local build" in buildinfo.tooltip(buildinfo.info({}))


# ── GET /version ───────────────────────────────────────────────────────────────────

def test_version_is_public_json_and_never_cached(monkeypatch):
    app, client = build(monkeypatch, **STAMP)
    resp = client.get("/version")                                      # signed out
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    assert resp.headers["Cache-Control"] == "no-store"
    body = resp.get_json()
    assert set(body) == {"commit", "short", "built_at", "revision", "started_at"}
    assert body["commit"] == SHA and body["short"] == "8adcd7e"
    assert body["built_at"] == "2026-09-30T19:03:12Z" and body["revision"] == "skippy-portal-00042-abc"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["started_at"])


def test_version_without_the_image_variables_says_dev(monkeypatch):
    app, client = build(monkeypatch)
    body = client.get("/version").get_json()
    assert body["commit"] == "dev" and body["short"] == "dev" and body["built_at"] == "" and body["revision"] == ""
    assert client.get("/version").headers["Cache-Control"] == "no-store"


def test_version_follows_the_environment_it_is_asked_in(monkeypatch):
    """Read per request, not cached at import: a rolled-back revision reports its own build because ITS container has its own variables."""
    app, client = build(monkeypatch, GIT_SHA="a" * 40)
    assert client.get("/version").get_json()["commit"] == "a" * 40
    monkeypatch.setenv("GIT_SHA", "b" * 40)
    assert client.get("/version").get_json()["commit"] == "b" * 40


def test_version_holds_no_secret(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-sentinel-key")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "sentinel-discord-token")
    monkeypatch.setenv("FLASK_SECRET_KEY", "sentinel-flask-secret")
    app, client = build(monkeypatch, **STAMP)
    text = client.get("/version").get_data(as_text=True)
    for secret in ("sentinel", "sk-ant", SETTINGS["SECRET_KEY"] + "x", SETTINGS["GOOGLE_CLIENT_SECRET"]):
        assert secret not in text
    assert set(json.loads(text)) == {"commit", "short", "built_at", "revision", "started_at"}   # a closed list, not "whatever is in the environment"


def test_a_signed_in_poll_of_version_is_not_a_page_view():
    """The timer polls it every second; JSON answers are never pages, so it stays out of the Captain's usage numbers."""
    app, client, tel, _ = tel_make()
    tel_login(client, ADMIN)
    for _ in range(3):
        assert client.get("/version").status_code == 200
    assert events(tel) == []


# ── the footer stamp ───────────────────────────────────────────────────────────────

SIGNED_IN_PAGES = ("/", "/games", "/games/valheim", "/me", "/apps", "/music", "/experiments", "/privacy", "/admin")


def test_signed_in_pages_carry_the_stamp_with_a_utc_fallback_and_the_full_commit_on_hover(monkeypatch):
    app, client = build(monkeypatch, **STAMP)
    sign_in(client)
    for path in SIGNED_IN_PAGES:
        page = client.get(path).data.decode()
        if 'class="foot"' in page:                  # a page on the bridge: the strip carries the short commit, nothing else printed
            assert re.search(r'<footer class="foot">\s*<a [^>]*href="/privacy"[^>]*>.*?</a>\s*<span class="build" title="[^"]*">8adcd7e</span>', page, re.S), path
        else:
            assert re.search(r'<footer class="sitefoot"><a href="/privacy">Privacy: what this site records</a>\s*<span class="build" title="', page), path
            assert "build 8adcd7e" in page, path
            assert '<time id="build-time" datetime="2026-09-30T19:03:12Z">2026-09-30 19:03 UTC</time>' in page, path
        tip = re.search(r'<span class="build" title="([^"]*)"', page).group(1)
        assert SHA in tip and "skippy-portal-00042-abc" in tip, path


def test_the_stamp_is_swapped_for_local_time_by_a_few_lines_of_inline_script(monkeypatch):
    app, client = build(monkeypatch, **STAMP)
    sign_in(client)
    page = client.get("/privacy").data.decode()
    script = re.search(r'<script>\s*/\* The footer\'s build time.*?</script>', page, re.S).group(0)
    assert 'getElementById("build-time")' in script and "toLocaleTimeString" in script
    assert script.count("\n") <= 12                                    # "a few lines"
    assert "fetch(" not in script and "XMLHttpRequest" not in script and "sendBeacon" not in script   # it never calls home


def test_a_local_build_says_dev_and_has_no_time_or_script(monkeypatch):
    app, client = build(monkeypatch)
    sign_in(client)
    page = client.get("/privacy").data.decode()
    assert "build dev" in page and 'id="build-time"' not in page and "toLocaleTimeString" not in page


def test_the_login_page_and_every_public_page_have_no_stamp(monkeypatch):
    app, client = build(monkeypatch, **STAMP)                          # signed out
    for path in ("/", "/experiments", "/privacy", "/request-access"):
        page = client.get(path, follow_redirects=True).data.decode()
        assert 'class="build"' not in page and "build-time" not in page and "build 8adcd7e" not in page, path
        assert 'href="/privacy"' in page                               # the footer itself is still there
    assert "Sign in with Google" in client.get("/").data.decode()


def test_someone_signed_in_but_no_longer_on_the_list_sees_the_login_page_without_the_stamp(monkeypatch):
    app, client = build(monkeypatch, **STAMP)
    sign_in(client, "removed.friend@example.com")
    page = client.get("/").data.decode()
    assert "Sign in with Google" in page and 'class="build"' not in page and SHA not in page


def test_the_stamp_is_inert_markup_even_for_a_hostile_looking_environment(monkeypatch):
    """info() only passes shapes it recognises, so nothing in the environment can put markup in the footer."""
    app, client = build(monkeypatch, GIT_SHA='"><script>alert(1)</script>', BUILD_TIME="<b>", K_REVISION="<i>x</i>")
    sign_in(client)
    page = client.get("/privacy").data.decode()
    foot = page[page.index('<footer class="sitefoot"'):]                       # the stamp's own footer (the page above it may have <b> of its own)
    assert "alert(1)" not in foot and "<b>" not in foot and "<i>x" not in foot and "build dev" in foot


def test_the_stamp_uses_only_portal_tokens_and_cannot_widen_a_375px_screen():
    css = re.search(r"footer\.sitefoot \.build \{(.*?)\}", BASE_HTML.read_text(encoding="utf-8"), re.S).group(1)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css), "a colour literal: use the tokens (var(--muted) is inherited from the footer)"
    assert "display: block" in css and "overflow-wrap: anywhere" in css       # its own line; long text wraps instead of scrolling
    assert not re.search(r"white-space:\s*nowrap|position:\s*(fixed|absolute)|\bwidth:|min-width", css)
    assert "var(--mono)" in css                                                # a token, as the code chips use
