"""core/quiet.py: builders keep off the box while the private Skippy is working, but never for more than four hours."""
from core_testlib import load

quiet = load("quiet.py", "core_quiet")


def test_not_busy_clears_the_stamp_and_never_defers(tmp_path):
    f = tmp_path / "busy-since"
    f.write_text("1")
    assert quiet.should_defer(f, now=100, is_busy=lambda: False) is False and not f.exists()


def test_busy_defers_then_gives_up_after_the_limit(tmp_path):
    f = tmp_path / "busy-since"
    assert quiet.should_defer(f, now=1000, is_busy=lambda: True) is True and f.exists()
    assert quiet.should_defer(f, now=1000 + quiet.MAX_DEFER_S - 1, is_busy=lambda: True) is True
    assert quiet.should_defer(f, now=1000 + quiet.MAX_DEFER_S + 1, is_busy=lambda: True) is False   # a stuck flag can't hold deploys forever


def test_an_unreachable_bridge_is_not_busy():
    assert quiet.busy("http://127.0.0.1:9/health", timeout=1) is False


def test_busy_reads_the_bridges_nested_workshop_flag(monkeypatch):
    import io
    import json
    for body, want in (({"workshop": {"busy": True}}, True), ({"workshop": {"busy": False}}, False), ({"busy": True}, True)):
        monkeypatch.setattr(quiet.urllib.request, "urlopen", lambda *a, _b=body, **k: io.BytesIO(json.dumps(_b).encode()))
        assert quiet.busy("http://x/health") is want


# ── Mentat-only changes are exempt from the gate (the Captain, 2026-10-27) ──
import json  # noqa: E402
import subprocess  # noqa: E402

from core_testlib import CORE  # noqa: E402

COMPOSE = (CORE / "compose.core.yml").read_text(encoding="utf-8")
DEPLOY = json.dumps({"pins": {"SIGMA_GROUND_REF": "a" * 40, "STARCONTROL_REF": "v1"}, "portal": {"service": "p"}}, indent=1)


def _only(changed, old=None, new=None):
    return quiet.mentat_only(changed, lambda p: (old or {}).get(p), lambda p: (new or {}).get(p))


def test_files_under_mentat_are_mentat_only_and_anything_else_is_not():
    assert _only(["mentat/app.py", "mentat/tests/test_x.py", "mentat/Dockerfile"])
    assert _only([])                                                        # nothing differs: nothing to disturb
    assert not _only(["mentat/app.py", "portal/app.py"])
    assert not _only(["core/quiet.py"], {"core/quiet.py": "a"}, {"core/quiet.py": "b"})
    assert not _only(["mentatx/app.py"])                                    # a prefix, not a lookalike directory


def test_only_the_sigma_ground_pin_counts_in_deploy_json():
    p = "core/cd/deploy.json"
    bumped = DEPLOY.replace("a" * 40, "b" * 40)
    assert _only([p], {p: DEPLOY}, {p: bumped})
    assert not _only([p], {p: DEPLOY}, {p: DEPLOY.replace('"v1"', '"v2"')})            # another pin is the portal's
    assert not _only([p], {p: DEPLOY}, {p: "{not json"})
    assert not _only([p], {p: DEPLOY}, {})                                              # file removed


def test_only_the_mentat_block_counts_in_the_compose_file():
    p = "core/compose.core.yml"
    assert "      - MENTAT_CASCADE=before" in COMPOSE
    mentat_changed = COMPOSE.replace("      - MENTAT_CASCADE=before", "      - MENTAT_CASCADE=after\n      - MENTAT_X=1")
    assert mentat_changed != COMPOSE and _only([p], {p: COMPOSE}, {p: mentat_changed})
    assert _only([p], {p: COMPOSE}, {p: COMPOSE + "\n\n# a trailing comment\n"})                # comments and blank lines do not count
    other = COMPOSE.replace("      - LLM_URL=http://llm:8080", "      - LLM_URL=http://other:8080")
    assert other != COMPOSE and not _only([p], {p: COMPOSE}, {p: other})              # the shim's block
    searx = COMPOSE.replace('"127.0.0.1:7360:8080"', '"127.0.0.1:7361:8080"')
    assert searx != COMPOSE and not _only([p], {p: COMPOSE}, {p: searx})
    llm = COMPOSE.replace("restart: unless-stopped", "restart: always", 1)
    assert not _only([p], {p: COMPOSE}, {p: llm})                                      # the first service is not Mentat's


def _repo(tmp_path):
    def run(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    run("init", "--quiet", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")

    def commit(files):
        for path, text in files.items():
            f = tmp_path / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        run("add", "-A")
        run("commit", "--quiet", "-m", "c")
        return run("rev-parse", "HEAD")
    return commit


def test_git_mentat_only_compares_two_commits(tmp_path):
    commit = _repo(tmp_path)
    base = commit({"mentat/app.py": "1", "portal/app.py": "1", "core/cd/deploy.json": DEPLOY, "core/compose.core.yml": COMPOSE})
    m = commit({"mentat/app.py": "2", "core/cd/deploy.json": DEPLOY.replace("a" * 40, "c" * 40)})
    p = commit({"portal/app.py": "2"})
    assert quiet.git_mentat_only(tmp_path, base, m) is True
    assert quiet.git_mentat_only(tmp_path, base, p) is False
    assert quiet.git_mentat_only(tmp_path, base, p) is False and quiet.git_mentat_only(tmp_path, m, p) is False
    assert quiet.git_mentat_only(tmp_path, None, m) is False                         # no base: the gate applies
    assert quiet.git_mentat_only(tmp_path, "0" * 40, m) is False                     # an unknown base: the gate applies
