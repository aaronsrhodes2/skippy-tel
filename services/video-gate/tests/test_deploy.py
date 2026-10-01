"""The shipping files agree with the code: the image holds every module the gate imports, the compose entry exposes only 127.0.0.1:3006,
the requirements cover what is imported, and the docs name the real routes and commands. (Nothing here starts a container.)"""
import ast
import re
from pathlib import Path

import pytest
import yaml

import gate

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parents[1]
STDLIB = set(__import__("sys").stdlib_module_names)


def local_modules():
    return {p.stem for p in SERVICE.glob("*.py") if p.stem not in ("smoke_real",)}


def imports_of(stem: str) -> set[str]:
    tree = ast.parse((SERVICE / f"{stem}.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def closure(start="gate"):
    seen, todo = set(), [start]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        todo += [m for m in imports_of(mod) if m in local_modules()]
    return seen


def dockerfile():
    return (SERVICE / "Dockerfile").read_text(encoding="utf-8")


def compose_entry():
    data = yaml.safe_load((REPO / "docker-compose.override.yml").read_text(encoding="utf-8"))
    return data["services"]["video-gate"], data["services"]


# ── the image ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_image_copies_exactly_the_modules_the_gate_needs():
    copied = set()
    for line in dockerfile().splitlines():
        m = re.match(r"COPY\s+((?:[\w.]+\.py\s+)+)\./?\s*$", line.strip())
        if m:
            copied |= {Path(n).stem for n in m.group(1).split()}
    assert copied == closure(), (copied, closure())              # a missing one is an ImportError in the container; a stray one is dead weight


def test_the_third_party_imports_are_all_in_the_requirements():
    reqs = {}
    for line in (SERVICE / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            name, _, version = line.partition("==")
            assert re.fullmatch(r"[0-9]+(\.[0-9]+)+", version), f"{line!r} is not pinned with =="
            reqs[name.lower()] = version
    third_party = set().union(*(imports_of(m) for m in closure())) - STDLIB - local_modules()
    transitive = {"werkzeug"}                                    # comes with flask
    assert third_party - transitive <= set(reqs), third_party
    assert {"flask", "gunicorn", "requests", "urllib3"} <= set(reqs)
    portal = {l.split("==")[0].lower(): l.split("==")[1] for l in (REPO / "portal/requirements.txt").read_text().splitlines() if "==" in l}
    for name in ("flask", "requests", "gunicorn"):
        assert reqs[name] == portal[name], f"{name} drifts from the portal's pin"


def test_the_container_runs_gunicorn_with_enough_threads_as_a_normal_user():
    full = dockerfile()
    text = full[full.index("CMD"):]                              # (the comment above it names the flag it leaves out)
    assert "gate:from_env()" in text and "--worker-class" in text and "gthread" in text and "--workers" in text
    assert re.search(r'"--workers",\s*"1"', text), "one process: it holds the catalog and the transcode sessions"
    threads = int(re.search(r'"--threads",\s*"(\d+)"', text).group(1))
    assert threads >= 32 + 8, "MAX_STREAMS streams plus room for the catalog, posters and progress"
    assert "--bind" in text and "0.0.0.0:8080" in text
    assert "access-logfile" not in text and "access-logformat" not in text          # the pass is in every URL
    assert full.index("USER gate") < full.index("CMD") and "useradd" in full
    assert full.startswith("FROM python:3.12")


# ── the compose entry ───────────────────────────────────────────────────────────────────────────────────────────────

def test_compose_publishes_only_localhost_3006():
    svc, _ = compose_entry()
    assert svc["ports"] == ["127.0.0.1:3006:8080"]
    assert svc["container_name"] == "skippytel-video-gate" and svc["restart"] == "unless-stopped"
    assert (REPO / svc["build"].lstrip("./") / "Dockerfile").is_file()


def test_compose_mounts_only_its_own_state_and_reaches_jellyfin_on_the_host():
    svc, services = compose_entry()
    assert svc["volumes"] == ["./state/video-gate:/state"]                    # no library mount, no docker.sock, nothing read-write on the host but its cache
    assert "host.docker.internal:host-gateway" in svc["extra_hosts"]
    assert svc["environment"]["JELLYFIN_URL"] == "http://host.docker.internal:8096"
    assert svc["env_file"] == services["music-gate"]["env_file"]               # the vault, same as the other services
    assert "network_mode" not in svc and "privileged" not in svc and "cap_add" not in svc and "devices" not in svc


def test_compose_has_no_secret_in_it():
    svc, _ = compose_entry()
    flat = yaml.safe_dump(svc)
    for needle in ("JELLYFIN_API_KEY", "SKIPPY_HOME_TOKEN", "api_key", "Token="):
        assert needle not in flat.replace("# ", ""), needle
    assert set(svc["environment"]) <= {"JELLYFIN_URL", "JELLYFIN_USER", "HLS_MAX_TRANSCODES", "MAX_STREAMS", "IMG_CACHE_MB", "CATALOG_REFRESH_S", "HLS_IDLE_S"}


def test_no_other_service_publishes_a_port_on_the_network():
    _, services = compose_entry()
    for name, svc in services.items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (name, port)


def test_the_comment_in_compose_has_the_funnel_command_the_readme_has():
    raw = (REPO / "docker-compose.override.yml").read_text(encoding="utf-8")
    readme = (SERVICE / "README.md").read_text(encoding="utf-8")
    assert "tailscale funnel --https=8443 --bg 3006" in raw and "tailscale funnel --https=8443 --bg 3006" in readme
    assert "docker compose up --build -d video-gate" in readme and "127.0.0.1:3006/health" in readme


# ── the docs ────────────────────────────────────────────────────────────────────────────────────────────────────────

DOCUMENTED = {                                   # route rule → what the docstring and the README must show for it
    "/t/<token>/catalog.json": ["/t/<pass>/catalog.json"],
    "/t/<token>/img/<id>/<kind>": ["/t/<pass>/img/<id>/<poster|backdrop|logo|thumb>"],
    "/t/<token>/v/<id>/direct": ["/t/<pass>/v/<id>/direct"],
    "/t/<token>/v/<id>/hls/<path:rest>": ["/t/<pass>/v/<id>/hls/master.m3u8", "/t/<pass>/v/<id>/hls/<file>"],
    "/t/<token>/sub/<id>/<name>": ["/t/<pass>/sub/<id>/<index>.vtt"],
    "/t/<token>/progress": ["/t/<pass>/progress"],
}


def test_every_route_is_in_the_docstring_and_the_readme(stack):
    rules = {r.rule for r in stack.app.url_map.iter_rules()} - {"/health"}
    assert rules == set(DOCUMENTED), "a new route has to be documented (here, in gate.py's docstring and in the README)"
    doc, readme = gate.__doc__, (SERVICE / "README.md").read_text(encoding="utf-8").replace("\\|", "|")
    assert "/health" in doc and "/health" in readme
    for rule, snippets in DOCUMENTED.items():
        for snippet in snippets:
            assert snippet in doc, (rule, snippet, "docstring")
            assert snippet in readme, (rule, snippet, "README")


def test_the_readme_has_the_pass_test_vector_the_tests_pin():
    readme = (SERVICE / "README.md").read_text(encoding="utf-8")
    assert "1800003600.d8383a1cefb72b136b01fb22ff9e87fb" in readme
    assert "d9b49845ccf7825b16d8a6026f24f283a700ec94ad6e750e0ff344db365934a0" in readme
    assert gate.sign(gate.gate_key("test-home-token"), 1800003600) == "1800003600.d8383a1cefb72b136b01fb22ff9e87fb"


@pytest.mark.parametrize("name", ["gate.py", "jellyfin.py", "catalog.py", "hls.py", "images.py", "smoke_real.py"])
def test_no_file_contains_a_secret_looking_literal(name):
    text = (SERVICE / name).read_text(encoding="utf-8")
    assert not re.search(r"(?i)(api[_-]?key|token)\s*=\s*[\"'][A-Za-z0-9]{20,}[\"']", text)
