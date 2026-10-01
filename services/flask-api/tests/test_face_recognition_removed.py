"""Face recognition (InsightFace, "Hey Buddy") was dropped by the Captain on
2026-09-29. These pin the behavior that matters: nothing probes it, nothing
lists it, and nothing that still exists points at it. The home reporter sends
/status.json and /services to the portal, whose Core widget turns any service
that is down into a problem, so a leftover probe would be a permanently red
"insightface" row.
"""
import ast
import importlib.util
from pathlib import Path

import pytest
from flask import Flask

API_ROOT = Path(__file__).resolve().parents[1]          # services/flask-api
REPO_ROOT = API_ROOT.parents[1]


@pytest.fixture
def probed(monkeypatch):
    """/status.json with every outside call stubbed; records each URL that gets pinged."""
    from app.routes import status
    urls: list[str] = []

    def fake_ping(url, path="/health", timeout=1.5):
        urls.append(f"{url}{path}")
        return True, "HTTP 200"

    monkeypatch.setattr(status, "_ping", fake_ping)
    monkeypatch.setattr(status, "_ollama_models", lambda: ["qwen2.5:7b"])
    monkeypatch.setattr(status, "_ups_status", lambda: None)
    monkeypatch.setattr(status, "_drive_ok", lambda: False)
    app = Flask(__name__)
    app.register_blueprint(status.bp)
    return app.test_client(), urls


def test_status_json_lists_only_services_that_exist(probed):
    client, urls = probed
    body = client.get("/status.json").get_json()
    assert [s["name"] for s in body["services"]] == ["flask-api", "ollama", "whisper", "google-drive"]
    assert not any("insightface" in u for u in urls)          # and nothing pinged it either


def test_services_manifest_has_no_hey_buddy(monkeypatch):
    from app.routes import services
    monkeypatch.setattr(services, "_tcp_reachable", lambda host, port, timeout=0.8: False)
    app = Flask(__name__)
    app.register_blueprint(services.bp)
    resp = app.test_client().get("/services")
    assert resp.status_code == 200
    assert "heybuddy" not in [s["id"] for s in resp.get_json()["services"]]
    assert "heybuddy" not in resp.get_data(as_text=True).lower()


def test_vault_bootstrap_advertises_no_heybuddy_feature():
    from app.routes import vault
    app = Flask(__name__)
    app.register_blueprint(vault.bp)
    resp = app.test_client().get("/vault/bootstrap")
    assert resp.status_code == 200
    features = resp.get_json()["features"]
    assert features["translate"] is True                      # the flags that remain are still there
    assert "heybuddy" not in features


def test_the_route_and_the_client_are_gone():
    assert importlib.util.find_spec("app.routes.heybuddy") is None
    assert importlib.util.find_spec("app.clients.insightface") is None
    assert not (REPO_ROOT / "services" / "insightface").exists()


def test_compose_does_not_define_or_depend_on_it():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8").lower()
    for gone in ("insightface", "face-db", "heybuddy"):
        assert gone not in compose


def test_every_blueprint_main_imports_exists():
    """The cheap static twin of test_no_interface.py's real boot of app.main: check the
    way it would break on boot, an import that points at a module that isn't there."""
    tree = ast.parse((API_ROOT / "app" / "main.py").read_text(encoding="utf-8"))
    modules = [n.module for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith(("app.routes.", "app.clients."))]
    assert "app.routes.status" in modules                     # the check sees the imports at all
    missing = [m for m in modules if importlib.util.find_spec(m) is None]
    assert missing == []
