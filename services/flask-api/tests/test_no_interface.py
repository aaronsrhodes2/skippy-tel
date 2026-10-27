"""Skippy core (flask-api) has no HTML interface: the Captain's browser UI lives on the
cloud portal, and core only serves data (JSON, SSE, images, audio, APK downloads).

These pin both halves of that decision:

* the pages are gone, and nothing can quietly grow another one (a source scan), and
* every data endpoint something still depends on is still there: the home reporter
  (read straight from reporter.py, so a new reporter call can't be missed), the shelved
  phone/glasses apps (SkippyChat / SkippyDroid), the cloud portal's Core widget.

The app is booted for real (`app.main`, the way gunicorn loads it), with its SQLite files
in a temp dir and its two background threads switched off. That is the check that a
deleted module's blueprint registration can't dangle.
"""
import importlib
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

API_ROOT = Path(__file__).resolve().parents[1]          # services/flask-api
REPO_ROOT = API_ROOT.parents[1]
APP_DIR = API_ROOT / "app"

# The HTML pages core used to serve. A GET on any of them must now be a plain 404.
REMOVED_PAGES = [
    "/status",            # the dashboard (its data is /status.json)
    "/portal",            # the zPhone mirror page
    "/portal/hud",        # the legacy JSON snapshot only that page read
    "/phone",             # the APK recovery page (the APKs are still /apk/<app>)
    "/bilby/bigcard",     # the DJ big-card display
    "/bilby/ui",          # the Mac Bilby UI passthrough
    "/bilby/ui/public",   # ... and its sanitised twin
]

# Data endpoints that must survive (method, a concrete example path). Grouped by who needs them.
KEPT = [
    # home reporter -> portal (also checked against reporter.py itself below)
    ("GET", "/health"), ("GET", "/status.json"), ("GET", "/services"), ("GET", "/music/session"),
    ("GET", "/telemetry/site_summary"), ("POST", "/telemetry/site_events"),
    ("GET", "/bilby/status"), ("POST", "/bilby/push"), ("POST", "/bilby/next"), ("POST", "/bilby/stop"),
    ("POST", "/music/session/skip"), ("POST", "/music/session/control"),
    # the shelved phone / glasses apps
    ("POST", "/intent/unmatched"), ("GET", "/bilby/karaoke"), ("GET", "/vault/bootstrap"),
    ("GET", "/apk/skippy-chat"), ("GET", "/services/intent/icon"), ("GET", "/telemetry/ui_summary"),
    ("POST", "/telemetry/ui_event"),
    ("GET", "/portal/history"), ("GET", "/portal/events"), ("GET", "/portal/image/1"),
    ("GET", "/portal/hud-state"), ("POST", "/portal/hud-state"), ("GET", "/portal/hud-state/events"),
    ("GET", "/portal/chat-state"), ("POST", "/portal/chat-state"), ("GET", "/portal/chat-state/events"),
    ("GET", "/portal/droid-state"), ("POST", "/portal/droid-state"),
    ("POST", "/portal/hud-frame"), ("GET", "/portal/hud-frame.jpg"), ("GET", "/portal/hud-stream"),
    ("GET", "/dropship/list"), ("GET", "/dropship/image/x"), ("POST", "/image"),
]


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    """The real flask-api app, booted through `app.main` like gunicorn does."""
    mp = pytest.MonkeyPatch()
    state = tmp_path_factory.mktemp("drive-state")
    from app import conversation_store, keyword_memory, photo_tags
    from app.routes import telemetry
    mp.setattr(photo_tags, "_DB_PATH", state / "photo_tags.db")
    mp.setattr(conversation_store, "DB_PATH", state / "portal_chat.db")
    mp.setattr(telemetry, "_DB_PATH", state / "ui_telemetry.db")
    mp.setattr(keyword_memory, "_DB_PATH", str(state / "keyword_memory.db"))
    real_start = threading.Thread.start

    def start(self, *args, **kwargs):                                     # the 8 s Ollama warm-up
        if self.name == "ollama-warmup":
            return None
        return real_start(self, *args, **kwargs)

    mp.setattr(threading.Thread, "start", start)
    sys.modules.pop("app.main", None)
    try:
        main = importlib.import_module("app.main")
        yield main.app
    finally:
        mp.undo()
        sys.modules.pop("app.main", None)


@pytest.fixture
def client(core):
    return core.test_client()


def _allowed_methods(app, path: str) -> set[str]:
    """Methods the app routes for `path` (empty when no rule matches it)."""
    from werkzeug.exceptions import MethodNotAllowed, NotFound
    from werkzeug.routing import RequestRedirect
    adapter = app.url_map.bind("localhost")
    allowed = set()
    for method in ("GET", "POST", "PUT", "DELETE"):
        try:
            adapter.match(path, method=method)
            allowed.add(method)
        except RequestRedirect:                    # a trailing-slash rule: the route exists
            allowed.add(method)
        except MethodNotAllowed:
            continue
        except NotFound:
            return set()
    return allowed


# ── it boots, and it has no pages ────────────────────────────────────────────────────

def test_the_app_boots_and_every_routes_module_is_registered(core):
    modules = sorted(p.stem for p in (APP_DIR / "routes").glob("*.py") if p.stem != "__init__")
    assert "bilby_bigcard" not in modules                      # the module is gone, not just unregistered
    registered = {name: bp for name, bp in core.blueprints.items()}
    for stem in modules:
        mod = importlib.import_module(f"app.routes.{stem}")
        assert registered.get(mod.bp.name) is mod.bp, f"app.routes.{stem} has a blueprint main.py never registers"
    assert len(list(core.url_map.iter_rules())) > 50           # the check sees real routes


@pytest.mark.parametrize("path", REMOVED_PAGES)
def test_the_removed_pages_are_plain_404s(client, path):
    assert client.get(path).status_code == 404, f"{path} still answers"


def test_no_module_in_the_app_renders_html():
    """A source scan, on purpose: the next page would otherwise arrive as 'just one little route'.
    If core really needs a page again, change this test with that decision, not around it."""
    markers = re.compile(r"text/html|<!doctype|<html|render_template|send_from_directory|static_folder", re.IGNORECASE)
    offenders = []
    for py in APP_DIR.rglob("*.py"):
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if markers.search(line):
                offenders.append(f"{py.relative_to(API_ROOT)}:{n}: {line.strip()[:80]}")
    assert offenders == []


def test_no_dependency_is_kept_only_for_the_removed_pages():
    reqs = (API_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for gone in ("beautifulsoup4", "bs4", "qrcode"):           # the proxy's sanitiser and the big card's QR
        assert gone not in reqs
    assert "pillow" in reqs and "requests" in reqs             # what still imports them


# ── what stays ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", KEPT)
def test_the_data_endpoints_survive(core, method, path):
    assert method in _allowed_methods(core, path), f"{method} {path} is gone"


def test_the_home_reporter_only_calls_routes_that_exist(core):
    """Read the paths straight out of reporter.py: the portal's whole Core view hangs on them."""
    src = (REPO_ROOT / "services" / "home-reporter" / "reporter.py").read_text(encoding="utf-8")
    paths = sorted(set(re.findall(r'\{API\}(/[A-Za-z0-9_./-]+)', src)))
    assert "/status.json" in paths and "/health" in paths      # the scan sees the calls at all
    missing = [p for p in paths if not _allowed_methods(core, p)]
    assert missing == []


def test_status_json_is_the_shape_the_reporter_and_portal_read(core, monkeypatch):
    from app.routes import status
    monkeypatch.setattr(status, "_ping", lambda url, path="/health", timeout=1.5: (True, "HTTP 200"))
    monkeypatch.setattr(status, "_ollama_models", lambda: ["qwen3:8b"])
    monkeypatch.setattr(status, "_ups_status", lambda: None)
    monkeypatch.setattr(status, "_drive_ok", lambda: False)
    monkeypatch.setattr(status, "_tags_stats", lambda: {"total": 0, "tagged": 0, "db": "missing"})
    monkeypatch.setattr(status, "_telem_stats", lambda: [])
    resp = core.test_client().get("/status.json")
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    body = resp.get_json()
    assert set(body) == {"services", "ollama_models", "ups", "photo_tags", "telemetry"}
    assert [s["name"] for s in body["services"]] == ["flask-api", "ollama", "whisper", "google-drive"]
    assert body["ollama_models"] == ["qwen3:8b"]


def test_the_service_registry_advertises_no_removed_page(core, monkeypatch):
    from app.routes import services
    monkeypatch.setattr(services, "_tcp_reachable", lambda host, port, timeout=0.8: False)
    body = core.test_client().get("/services").get_json()
    by_id = {s["id"]: s for s in body["services"]}
    advertised = {v for s in body["services"] for v in s.get("endpoints", {}).values()}
    for page in REMOVED_PAGES:
        assert page not in advertised
    assert "ui" not in by_id["bilby"]["endpoints"] and "ui_public" not in by_id["bilby"]["endpoints"]
    assert "phone" not in by_id["vault"]["endpoints"]
    # what the entries we touched (and their neighbours served by core itself) still advertise is real
    for sid in ("bilby", "vault", "music", "dropship", "translate", "ups", "starmap", "image"):
        for name, path in by_id[sid]["endpoints"].items():
            example = re.sub(r"<[^>]+>", "x", path)
            assert _allowed_methods(core, example), f"{sid}.{name} = {path} points at nothing"


def test_vault_bootstrap_keeps_its_contract_but_no_longer_points_at_the_phone_page(client):
    resp = client.get("/vault/bootstrap")
    body = resp.get_json()                                    # envelope(): spec_version + the payload, flat
    assert body["spec_version"] == "1"
    assert body["apk_base_url"].endswith("/apk") and body["skippy_tel_url"].startswith("http://")
    assert set(body["features"]) >= {"sms", "bilby", "translate"}
    assert "phone_url" not in body and "/phone" not in resp.get_data(as_text=True)


# ── /bilby/karaoke: the lyric line is data, and the phone apps poll it twice a second ──

@pytest.fixture
def karaoke(core, monkeypatch):
    from app.routes import bilby_proxy
    state = SimpleNamespace(mac_url="http://skippy-mac:7334", reply=None, called=[])
    monkeypatch.setattr(bilby_proxy, "settings", SimpleNamespace(bilby_mac_url=state.mac_url))

    def fake_get(url, timeout=None):
        state.called.append((url, timeout))
        if isinstance(state.reply, Exception):
            raise state.reply
        return state.reply

    monkeypatch.setattr(bilby_proxy.requests, "get", fake_get)
    state.module = bilby_proxy
    state.client = core.test_client()
    return state


class _Reply:
    def __init__(self, data, ok=True):
        self._data, self.ok = data, ok

    def json(self):
        return self._data


def test_karaoke_passes_the_line_through(karaoke):
    karaoke.reply = _Reply({"line": "all you need is love", "next_line": "love is all you need", "elapsed_pct": "42.5", "extra": 1})
    resp = karaoke.client.get("/bilby/karaoke")
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    assert resp.get_json() == {"line": "all you need is love", "next_line": "love is all you need", "elapsed_pct": 42.5}
    assert karaoke.called == [("http://skippy-mac:7334/api/karaoke", 1.0)]


@pytest.mark.parametrize("failure", [ConnectionError("mac asleep"), _Reply({}, ok=False), _Reply({"elapsed_pct": "not-a-number"})])
def test_karaoke_degrades_to_nulls_never_an_error(karaoke, failure):
    karaoke.reply = failure
    resp = karaoke.client.get("/bilby/karaoke")
    assert resp.status_code == 200
    assert resp.get_json() == {"line": None, "next_line": None, "elapsed_pct": 0.0}


def test_karaoke_without_a_mac_url_does_not_even_try(karaoke, monkeypatch):
    monkeypatch.setattr(karaoke.module, "settings", SimpleNamespace(bilby_mac_url=""))
    resp = karaoke.client.get("/bilby/karaoke")
    assert resp.get_json() == {"line": None, "next_line": None, "elapsed_pct": 0.0}
    assert karaoke.called == []


def test_health_says_ready_and_what_it_is_doing_not_just_ok(client, monkeypatch):
    """PLATINUM section 24: a 200 from /health is not enough. It says ready (Ollama answers AND offers the default model), accepting, the jobs."""
    from app.routes import health as health_mod
    monkeypatch.setattr(health_mod, "_ollama_status", lambda: {"available": True, "models": ["qwen3:8b"], "default": "qwen3:8b", "default_present": True})
    good = client.get("/health").get_json()
    assert good["status"] == "ok" and good["ready"] is True and good["accepting"] is True and good["jobs"]["running"] == 0 and good["detail"] == ""
    monkeypatch.setattr(health_mod, "_ollama_status", lambda: {"available": True, "models": [], "default": "qwen3:8b", "default_present": False})
    missing = client.get("/health").get_json()
    assert missing["ready"] is False and "qwen3:8b" in missing["detail"] and missing["deps"]["ollama"]["ready"] is False   # `ok` used to be said here
    monkeypatch.setattr(health_mod, "_ollama_status", lambda: {"available": False, "models": [], "default": "qwen3:8b", "default_present": False})
    away = client.get("/health").get_json()
    assert away["ready"] is False and "isn't answering" in away["detail"]


def test_a_post_in_flight_counts_as_a_job_and_is_gone_afterwards(client, monkeypatch):
    from app.routes import health as health_mod
    began = []
    real_begin = health_mod.READY.begin
    monkeypatch.setattr(health_mod.READY, "begin", lambda label="": began.append(label) or real_begin(label))
    client.post("/battery", json={})                                         # any POST is a call that does work
    assert began == ["/battery"]
    assert health_mod.READY.report()["jobs"]["running"] == 0                  # and it is gone once the call has ended
    client.get("/health")
    assert began == ["/battery"]                                              # a GET (a health poll) is never a job
