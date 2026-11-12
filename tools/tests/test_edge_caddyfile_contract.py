"""The direct front door's files agree with each other and with the contract (docs/direct-door.md): the Caddyfile's path prefixes and the gates they
reach, the compose `edge` service that runs it, the selftest that proves it, the status card, the docs. Nothing here starts Docker (the proof that
the Caddyfile WORKS is `bash edge/selftest.sh`); this keeps the pieces from drifting apart afterwards."""
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bashrun import BASH, posix, run_bash  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CADDYFILE = (REPO / "edge" / "Caddyfile").read_text(encoding="utf-8")
SELFTEST = (REPO / "edge" / "selftest.sh").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((REPO / "docker-compose.override.yml").read_text(encoding="utf-8"))
EDGE = COMPOSE["services"]["edge"]

# the contract: path prefix on the edge -> the compose service the gate runs as (all listen on 8080 inside the compose network)
PREFIXES = {"music": "music-gate", "video": "video-gate", "books": "audiobook-gate", "voice": "voice-gate", "in": "popup-gate"}
MEDIA = {"music", "video", "books"}                     # long streams: a paused player must not be cut after a minute


def block_after(text, open_brace_index):
    """The text between the `{` at this index and its matching `}`."""
    depth = 0
    for i in range(open_brace_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index + 1:i]
    raise AssertionError("unbalanced braces")


def handle_paths():
    blocks = {}
    for prefix, body in re.findall(r"\n\thandle_path /(\w+)/\*\s*\{(.*?)\n\t\}", CADDYFILE, re.S):
        blocks[prefix] = body
    return blocks


# ── the Caddyfile ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_prefixes_reach_the_gates_the_contract_names():
    got = {}
    for prefix, body in handle_paths().items():
        m = re.search(r"reverse_proxy\s+([\w-]+):(\d+)", body)
        got[prefix] = (m.group(1), int(m.group(2))) if m else None
    assert got == {p: (svc, 8080) for p, svc in PREFIXES.items()}


def test_every_gate_behind_a_prefix_is_a_compose_service_that_listens_on_8080():
    for svc in PREFIXES.values():
        assert svc in COMPOSE["services"], svc
        assert any(str(p).endswith(":8080") for p in COMPOSE["services"][svc]["ports"]), svc          # published on 127.0.0.1:<n>:8080: the gate's own port is 8080
        assert "network_mode" not in COMPOSE["services"][svc]                                          # so the name resolves on the default compose network
        assert "networks" not in COMPOSE["services"][svc] or "default" in COMPOSE["services"][svc]["networks"]


def test_the_edge_is_on_its_own_network_shared_only_with_the_five_gates_it_names():
    """Review 2026-11-05: the one container the internet reaches must not sit on the network with flask-api, whisper and the home reporter."""
    assert "network_mode" not in EDGE and list(EDGE["networks"]) == ["edge-net"]
    gates = ("music-gate", "video-gate", "audiobook-gate", "voice-gate", "popup-gate")
    for name in gates:                                                                          # each upstream is on it (and on default, where its own upstreams are)
        assert "edge-net" in COMPOSE["services"][name]["networks"] and "default" in COMPOSE["services"][name]["networks"], name
        assert re.search(rf"reverse_proxy {name}:8080", CADDYFILE), name
    on_edge_net = sorted(n for n, svc in COMPOSE["services"].items() if "edge-net" in (svc.get("networks") or []))
    assert on_edge_net == sorted(("edge", *gates))                                              # nothing else shares it
    assert "internal" not in (COMPOSE["networks"]["edge-net"] or {})                           # (it is reached from outside and asks Let's Encrypt)


def test_the_edge_is_off_until_it_is_turned_on_so_a_bare_compose_up_never_asks_lets_encrypt_early():
    assert EDGE["profiles"] == ["edge"]
    assert not any("profiles" in svc and "edge" in svc["profiles"] for n, svc in COMPOSE["services"].items() if n != "edge")


def test_streaming_is_never_buffered_and_only_the_long_streams_are_set_apart():
    blocks = handle_paths()
    assert set(blocks) == set(PREFIXES)
    for prefix, body in blocks.items():
        assert "flush_interval -1" in body, prefix
        assert ("import media" in body) == (prefix in MEDIA), prefix
    assert re.search(r"\(media\)\s*\{\s*timeouts\s*\{\s*write_timeout 15m", CADDYFILE)


def test_the_put_to_the_popup_gate_has_no_size_limit_at_the_edge():
    assert "request_body" not in CADDYFILE and "max_size" not in CADDYFILE


def test_ping_is_ok_from_caddy_itself_and_everything_else_is_404():
    ping = re.search(r"\n\thandle /ping\s*\{(.*?)\n\t\}", CADDYFILE, re.S).group(1)
    assert 'respond "ok" 200' in ping and "reverse_proxy" not in ping
    assert re.search(r'\n\thandle\s*\{\s*respond "not found" 404\s*\}', CADDYFILE)                      # the catch-all for the right name
    assert re.search(r'\n:443\s*\{\s*import plain\s*respond "not found" 404\s*\}', CADDYFILE)           # and for any other name behind a right SNI


def test_the_global_options_are_the_ones_the_contract_asks_for():
    head = CADDYFILE[:CADDYFILE.index("{$EDGE_HOST")]
    assert re.search(r"\n\{\n\tadmin off\n", head)                                      # no admin API
    assert re.search(r"protocols h1 h2\n", head) and "h3" not in head                  # the router forwards TCP only
    assert "skip_install_trust" in head and "auto_https disable_redirects" in head
    assert re.search(r"cert_issuer acme\s*\{\s*dir \{\$EDGE_ACME_DIR:https://acme-v02\.api\.letsencrypt\.org/directory\}\s*disable_http_challenge", head)
    assert re.search(r"\n\tlog \{\s*output stdout", head)                              # the runtime log goes to docker logs
    assert re.search(r"\{\$EDGE_HOST:localhost\} \{", CADDYFILE)                         # the site is the name in EDGE_HOST


def test_the_server_header_is_removed_also_from_the_answers_caddy_writes_itself():
    plain = re.search(r"\(plain\)\s*\{(.*?)\n\}", CADDYFILE, re.S).group(1)
    assert "header -Server" in plain and plain.count("header -Server") == 2           # on a normal answer and inside handle_errors
    assert CADDYFILE.count("import plain") == 2                                          # the right name and the catch-all site


def test_the_pass_is_cut_from_every_log_the_edge_writes():
    redact = re.search(r"\(redact\)\s*\{(.*?)\n\}", CADDYFILE, re.S).group(1)
    for field in ("request>uri", "error", "uri"):
        assert f"{field} regexp /t/[^/?]+ /t/-" in redact, field
    assert CADDYFILE.count("import redact") == 2                                          # the runtime log and the access log
    logs = [block_after(CADDYFILE, m.end() - 1) for m in re.finditer(r"(?m)^\t*log \{", CADDYFILE)]
    assert len(logs) == 2 and all("format filter" in l and "import redact" in l for l in logs)        # neither log can be written unfiltered
    assert "output file /data/access.log" in CADDYFILE and "roll_size 10MiB" in CADDYFILE


def test_the_health_listener_is_bound_to_the_containers_own_loopback():
    site = re.search(r"\nhttp://127\.0\.0\.1:8081 \{(.*?)\n\}", CADDYFILE, re.S).group(1)
    assert "bind 127.0.0.1" in site and 'respond /ping "ok" 200' in site


def test_the_caddyfile_names_no_secret_and_no_credential():
    assert not re.search(r"(?i)api[_-]?key|password|secret|token|bearer", re.sub(r"#.*", "", CADDYFILE))


# ── the compose service ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_edge_service_is_the_pinned_caddy_published_on_47443():
    assert re.fullmatch(r"caddy:2\.\d+\.\d+@sha256:[0-9a-f]{64}", EDGE["image"]), EDGE["image"]    # a concrete 2.x tag AND its digest, never `latest` or `2`
    assert tuple(int(x) for x in EDGE["image"].split(":")[1].split("@")[0].split(".")) >= (2, 11, 8)     # the per-route write_timeout needs 2.11.8 (docs/direct-door.md)
    assert EDGE["container_name"] == "skippytel-edge" and EDGE["restart"] == "unless-stopped"
    assert EDGE["ports"] == ["0.0.0.0:47443:443"]                                                # the router's 443 lands on this PC's 47443
    assert "home.makerportal.live" in EDGE["environment"]["EDGE_HOST"] and EDGE["environment"]["EDGE_HOST"].startswith("${EDGE_HOST:-")
    assert "EDGE_ACME_DIR" in EDGE["environment"] and "acme-v02.api.letsencrypt.org" in EDGE["environment"]["EDGE_ACME_DIR"]


def test_the_edge_holds_no_secret_and_no_way_out_of_its_container():
    assert "env_file" not in EDGE                                                                # not the vault: every other service has it
    assert set(EDGE["environment"]) == {"EDGE_HOST", "EDGE_ACME_DIR"}
    flat = yaml.safe_dump(EDGE)
    for needle in ("docker.sock", "SKIPPY_HOME_TOKEN", "vault", "privileged", "devices", "network_mode", "extra_hosts"):
        assert needle not in flat, needle
    assert EDGE["read_only"] is True and EDGE["cap_drop"] == ["ALL"] and EDGE["cap_add"] == ["NET_BIND_SERVICE"]
    assert "no-new-privileges:true" in EDGE["security_opt"] and "/tmp" in EDGE["tmpfs"]


def test_the_edge_keeps_its_certificates_in_named_volumes_and_the_caddyfile_read_only():
    assert EDGE["volumes"] == ["./edge/Caddyfile:/etc/caddy/Caddyfile:ro", "edge-data:/data", "edge-config:/config"]
    assert {"edge-data", "edge-config"} <= set(COMPOSE["volumes"])
    assert (REPO / "edge" / "Caddyfile").is_file()


def test_the_health_check_asks_the_listener_the_caddyfile_opens():
    assert EDGE["healthcheck"]["test"][-1] == "http://127.0.0.1:8081/ping" and EDGE["healthcheck"]["test"][0] == "CMD"
    assert "http://127.0.0.1:8081 {" in CADDYFILE


def test_only_the_edge_is_published_beyond_loopback():
    for name, svc in COMPOSE["services"].items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:") or name == "edge", (name, port)


def test_the_status_page_has_a_card_for_it():
    import json
    nodes = json.loads((REPO / "portal" / "status_nodes.json").read_text(encoding="utf-8"))
    assert len(nodes["edge"]["for"]) > 30 and nodes["edge"]["used_by"] and nodes["edge"]["example"]["ask"].endswith("/ping")


# ── the selftest ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_selftest_runs_the_container_the_way_compose_does():
    harden = re.search(r"harden=\((.*?)\)", SELFTEST).group(1)
    assert "--cap-drop ALL" in harden and "--cap-add NET_BIND_SERVICE" in harden and "--read-only" in harden and "--security-opt no-new-privileges:true" in harden
    assert "--tmpfs /tmp" in harden
    assert re.search(r"-e EDGE_HOST=localhost", SELFTEST)                                      # the real Caddyfile, a name Caddy can sign itself
    assert "$here/Caddyfile:/etc/caddy/Caddyfile:ro" in SELFTEST                              # the file itself, not a copy
    image = re.search(r"^\s*image:\s*(caddy:\S+)", (REPO / "docker-compose.override.yml").read_text(encoding="utf-8"), re.M)
    assert image and image.group(1) == EDGE["image"]                                           # the selftest finds the tag the same way


def test_the_selftest_stands_up_all_five_gate_names_and_checks_every_prefix():
    assert 'gates="music-gate video-gate audiobook-gate voice-gate popup-gate"' in SELFTEST
    for pair in (f"{p}:{svc}" for p, svc in PREFIXES.items()):
        assert pair in SELFTEST, pair


def test_the_selftest_mounts_its_stand_in_gate_and_that_file_is_python():
    import ast
    assert "$here/selftest_upstream.py:/srv/selftest_upstream.py:ro" in SELFTEST and "python /srv/selftest_upstream.py" in SELFTEST
    tree = ast.parse((REPO / "edge" / "selftest_upstream.py").read_text(encoding="utf-8"))
    paths = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert {"/big", "/sha", "/trickle", "/range", "/abort"} <= paths                                # the paths the selftest's checks ask for
    for needle in ("/video/t/p/big?mb=40", "/video/t/p/sha?mb=40", "/voice/t/p/trickle", "/books/t/p/range", "/music/t/SECRETPASS.abc/abort"):
        assert needle in SELFTEST, needle


def test_the_selftest_is_valid_bash():
    if not BASH:
        pytest.skip("no bash that can read this checkout")
    done = run_bash(["-n", posix(REPO / "edge" / "selftest.sh")])
    assert done.returncode == 0, done.stderr


# ── the line endings and the docs ────────────────────────────────────────────────────────────────────────────────────────

def test_the_caddyfile_is_lf_on_every_checkout():
    assert "edge/Caddyfile text eol=lf" in (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert b"\r" not in (REPO / "edge" / "Caddyfile").read_bytes()


def test_the_docs_say_how_to_check_and_turn_off_the_door():
    doc = (REPO / "docs" / "direct-door.md").read_text(encoding="utf-8")
    for needle in ("home.makerportal.live", "47443", "tools/upnp_port.py", "SKIPPY_EDGE_UPNP", "/ping", "edge/selftest.sh", "Funnel", "docker compose stop edge",
                   "EDGE_HOST", "EDGE_ACME_DIR", "edge-data", "access.log", "--dry-run"):
        assert needle in doc, needle
    for prefix in PREFIXES:
        assert f"/{prefix}" in doc, prefix
    readme = (REPO / "edge" / "README.md").read_text(encoding="utf-8")
    assert "docs/direct-door.md" in readme and "selftest.sh" in readme and "Caddyfile" in readme
