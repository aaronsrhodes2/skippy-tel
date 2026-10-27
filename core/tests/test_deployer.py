"""The pull deployer's promises, with real git (a bare origin and the box's clone) and fake models/compose/smoke:
deploy a green commit once, wait for CI, never retry a bad key, refuse missing models without touching the running
stack, and put the last good commit back when a deploy goes red."""
import json
import subprocess
from pathlib import Path

import pytest

from core_testlib import load

dep = load("deployer/deployer.py", "core_deployer")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Fake(dep.Deployer):
    """Each commit carries fake.json saying how its deploy goes: {"models": 0|1|2, "compose": bool, "smoke": bool}."""

    def __init__(self, settings):
        super().__init__(settings, log=lambda m: None)
        self.ci, self.deployed, self.running = {}, [], None

    def ci_state(self, commit):
        return self.ci.get(commit, ("success", "green"))

    def _fake(self):
        return json.loads((self.repo / "fake.json").read_text())

    def models(self, cmd, full):
        rc = self._fake().get("models", 0) if cmd == "check" else 0
        if full == "general" and cmd == "check":
            rc = 1                                           # the big general model's files aren't there
        return subprocess.CompletedProcess([], rc, "", "missing /srv/models/x" if rc else "")

    def compose_up(self, full, recreate=False):
        if not self._fake().get("compose", True):
            raise subprocess.CalledProcessError(1, "compose", "", "build failed")
        self.running = (git(self.repo, "rev-parse", "HEAD"), full)
        self.deployed.append(self.running)
        self.recreated = recreate

    def sh(self, cmd, *, full="", timeout=1800, check=True):
        if cmd[0] != "docker":                               # git is real here; docker is the one thing faked
            return super().sh(cmd, full=full, timeout=timeout, check=check)
        self.ran = getattr(self, "ran", []) + [cmd]
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def compose_build(self, full):
        if not self._fake().get("build", True):
            raise subprocess.CalledProcessError(1, "build", "", "COPY .vendor failed")

    def smoke(self, full):
        ok = self._fake().get("smoke", True)
        return [{"check": "chat", "ok": ok, "detail": "" if ok else "no reply"}]


@pytest.fixture
def box(tmp_path):
    origin, work, clone = tmp_path / "origin.git", tmp_path / "work", tmp_path / "clone"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "--quiet", str(origin), str(work))
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")

    counter = {"n": 0}

    def commit(**behaviour):
        (work / "fake.json").write_text(json.dumps(behaviour))
        counter["n"] += 1
        (work / "n").write_text(str(counter["n"]))       # every commit differs
        git(work, "add", "-A")
        git(work, "commit", "--quiet", "-m", "c")
        git(work, "push", "--quiet", "origin", "HEAD:main")
        return git(work, "rev-parse", "HEAD")

    first = commit()
    git(tmp_path, "clone", "--quiet", str(origin), str(clone))
    settings = {**dep.DEFAULTS, "REPO_DIR": str(clone), "CORE_STATE": str(tmp_path / "state"), "QUIET": "0"}
    return Fake(settings), commit, first


def test_a_green_commit_deploys_once(box):
    d, commit, first = box
    assert d.run()["state"] == "ok"
    assert d.deployed == [(first, "")]
    row = d.run()
    assert row["state"] == "ok" and row["detail"] == "up to date" and len(d.deployed) == 1
    good = json.loads((d.dir / "status.json").read_text())["last_good"]
    assert good["commit"] == first and good["started"] <= good["at"]          # when it began, for tools/loop_timer.py


def test_it_waits_for_ci_and_never_deploys_a_red_one(box):
    d, commit, first = box
    d.run()
    second = commit()
    d.ci[second] = ("pending", "running")
    assert d.run()["state"] == "waiting-ci" and d.running == (first, "")
    d.ci[second] = ("failure", "red")
    assert d.run()["state"] == "ci-failed"
    d.ci[second] = ("success", "")                          # a re-run going green doesn't resurrect a bad key...
    assert d.run()["state"] == "known-bad" and d.running == (first, "")
    d._write("state.json", {**d.state(), "bad": {}})        # ...`deployer.py forget` does
    assert d.run()["state"] == "ok" and d.running == (second, "")


def test_a_red_smoke_rolls_back_and_is_never_retried(box):
    d, commit, first = box
    d.run()
    broken = commit(smoke=False)
    row = d.run()
    assert row["state"] == "rolled-back" and row["stage"] == "smoke" and "no reply" in row["detail"]
    assert d.deployed[-2:] == [(broken, ""), (first, "")] and d.running == (first, "")
    assert git(d.repo, "rev-parse", "HEAD") == first
    assert d.run()["state"] == "known-bad" and len(d.deployed) == 3
    fixed = commit()
    assert d.run()["state"] == "ok" and d.running == (fixed, "")


def test_a_failed_build_rolls_back_but_is_retried(box):
    """PyPI or GHCR being down is not a verdict on the commit: never mark it bad for that."""
    d, commit, first = box
    d.run()
    flaky = commit(compose=False)
    row = d.run()
    assert row["state"] == "rolled-back" and row["will_retry"] is True and d.running == (first, "")
    assert f"{flaky}|" not in d.state()["bad"]
    assert d.run()["state"] == "retry-later"            # but not every two minutes
    d.s["RETRY_S"] = "0"
    (d.repo / "fake.json").write_text("{}")             # the network is back (the fake reads the checkout)
    d._fake = lambda: {}
    assert d.run()["state"] == "ok" and d.running == (flaky, "")


def test_a_broken_manifest_is_bad_but_missing_models_are_only_refused(box):
    d, commit, first = box
    d.run()
    commit(models=2)
    assert d.run()["state"] == "rolled-back"
    good = commit()
    assert d.run()["state"] == "ok"
    d.s["CORE_FULL"] = "general"
    row = d.run()
    assert row["state"] == "refused" and "missing" in row["detail"]
    assert d.running == (good, "") and git(d.repo, "rev-parse", "HEAD") == good, "the running stack is left alone"
    assert f"{good}|general" not in d.state()["bad"], "it deploys once the files land"


def test_onlining_a_slot_is_a_new_key(box):
    d, commit, first = box
    d.run()
    d.s["CORE_FULL"] = "coder, chat"
    assert d.run()["state"] == "ok" and d.running == (first, "chat,coder")


def test_nothing_to_roll_back_to(box):
    d, commit, first = box
    d._fake = lambda: {"smoke": False}
    row = d.run()
    assert row["state"] == "failed" and row["rollback"] == "nothing to roll back to"


def test_settings_file_then_environment(tmp_path, monkeypatch):
    f = tmp_path / "core.env"
    f.write_text("# c\nCORE_FULL=chat\nCI_LOCAL='/var/lib/ci'\n")
    for k in dep.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    s = dep.load_settings(str(f))
    assert s["CORE_FULL"] == "chat" and s["CI_LOCAL"] == "/var/lib/ci" and s["BRANCH"] == "main" and "GITHUB_TOKEN" not in s
    monkeypatch.setenv("CORE_FULL", "all")
    assert dep.load_settings(str(f))["CORE_FULL"] == "all"
    assert dep.norm_full(" coder,chat,") == "chat,coder" and dep.norm_full("chat,all") == "all"


def test_the_llm_key_reaches_render_compose_and_smoke(tmp_path, monkeypatch):
    f = tmp_path / "core.env"
    f.write_text("LLM_API_KEY=sk-core-abc\nCORE_GPU=0\n")
    for k in dep.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    d = dep.Deployer(dep.load_settings(str(f)), log=lambda m: None)
    assert d.env("")["LLM_API_KEY"] == "sk-core-abc", "every child (models.py render, compose, smoke.py) needs it"


def test_ci_state_is_this_boxs_own_ci_or_no_gate_at_all(box, tmp_path):
    d, _, _ = box
    real = dep.Deployer({**d.s, "CI_LOCAL": ""}, log=lambda m: None)
    assert real.ci_state("abc") == ("success", "no CI gate")                           # the simulator
    gated = dep.Deployer({**d.s, "CI_LOCAL": str(tmp_path / "ci"), "CI_NAME": "skippy-tel"}, log=lambda m: None)
    assert gated.ci_state("abc")[0] == "pending"                                        # no result yet: wait, never deploy
    done = tmp_path / "ci" / "skippy-tel" / "results"
    done.mkdir(parents=True)
    (done / "abc.json").write_text(json.dumps({"state": "success", "seconds": 3}))
    (done / "bad.json").write_text(json.dumps({"state": "failure", "jobs": [{"name": "suite", "state": "failure"}]}))
    assert gated.ci_state("abc")[0] == "success" and gated.ci_state("bad") == ("failure", "this box's CI failed: suite")
    assert "github" not in Path(dep.__file__).read_text(encoding="utf-8").lower().replace("docs/no-github.md", "")      # the gate reads no GitHub

def test_repair_recreates_the_last_good_deploy_and_smoke_tests_it(box):
    d, commit, first = box
    d.run()
    commit(smoke=False)                                       # a newer commit that would go red: repair stays on the last good one
    row = d.repair()
    assert row["state"] == "ok" and row["detail"] == "repaired" and d.running == (first, "") and d.recreated is True


def test_a_repair_that_still_fails_its_smoke_says_so(box):
    d, commit, first = box
    (d.repo / "fake.json").write_text(json.dumps({"smoke": False}))
    row = d.repair()
    assert row["state"] == "repair-failed" and row["stage"] == "smoke"


def test_the_doctor_restarts_llama_swap_after_two_failures_twice_an_hour_then_repairs_once(box):
    d, commit, first = box
    d.run()
    t = [1_000_000.0]
    look = lambda ok: d.doctor(answers=lambda: (ok, "" if ok else "no answer: TimeoutError"), now=t[0])   # noqa: E731
    assert look(True) == {"ok": True}
    assert look(False)["did"] == "watching"                  # one failure: a blip
    assert look(False)["did"] == "restarted llama-swap" and d.ran[-1][-2:] == ["restart", "llm"]
    assert look(False)["did"] == "restarted llama-swap"
    assert look(False)["did"].startswith("repaired: ok") and d.recreated is True
    assert look(False)["did"].startswith("still failing")     # and then it waits for the next hour
    assert look(True) == {"ok": True}
    t[0] += 3601
    look(False)
    assert look(False)["did"] == "restarted llama-swap"       # a new hour: it starts again


def test_a_slow_llama_swap_with_every_model_loaded_is_left_alone(box):
    """Busy, not dead: a video pop-up shares the GPU and replies crawl; restarting would reload every model mid-job (2026-10-24)."""
    d, commit, first = box
    d.run()
    ran = len(getattr(d, "ran", []))
    slow = lambda: (False, "no answer: TimeoutError")   # noqa: E731
    for _ in range(4):
        assert d.doctor(answers=slow, loaded=lambda: True, now=1_000_000.0) == {"ok": True, "busy": True}
    assert len(getattr(d, "ran", [])) == ran                  # nothing restarted
    assert d.doctor(answers=slow, loaded=lambda: False, now=1_000_000.0)["did"] == "watching"   # not loaded: the old rules apply


def test_with_no_deploy_of_record_a_repair_only_restarts_what_runs(box):
    d, commit, first = box                                    # never deployed: the stack was set up by hand
    row = d.repair()
    assert row["state"] == "ok" and d.deployed == [] and d.ran[-1][-1] == "restart"   # nothing rendered, nothing rebuilt


def test_matter_joins_the_stack_only_when_core_env_says_so(tmp_path, monkeypatch):
    for k in dep.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "core.env"
    f.write_text("CORE_GPU=0\n")
    assert "core/compose.matter.yml" not in dep.Deployer(dep.load_settings(str(f)), log=lambda m: None).compose_files()
    f.write_text('CORE_GPU=1\nCORE_MATTER=1\nMATTER_ARGS="--primary-interface eno1 --bluetooth-adapter 0"\n')
    d = dep.Deployer(dep.load_settings(str(f)), log=lambda m: None)
    assert d.compose_files() == ["-f", "core/compose.core.yml", "-f", "core/compose.gpu.yml", "-f", "core/compose.matter.yml"]
    assert d.env("")["MATTER_ARGS"] == "--primary-interface eno1 --bluetooth-adapter 0" and d.env("")["CORE_MATTER"] == "1"


def test_the_3d_popups_join_the_stack_only_when_core_env_says_so(tmp_path, monkeypatch):
    for k in dep.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "core.env"
    f.write_text("CORE_GPU=1\n")
    assert "core/popups-3d/compose.yml" not in dep.Deployer(dep.load_settings(str(f)), log=lambda m: None).compose_files()
    f.write_text("CORE_GPU=1\nCORE_POPUPS3D=1\n")
    assert dep.Deployer(dep.load_settings(str(f)), log=lambda m: None).compose_files()[-2:] == ["-f", "core/popups-3d/compose.yml"]


def test_the_matter_compose_file_is_pinned_on_the_host_network_and_keeps_its_fabric():
    from pathlib import Path
    text = (Path(dep.__file__).resolve().parents[1] / "compose.matter.yml").read_text(encoding="utf-8")
    assert "python-matter-server:8.1.0@sha256:" in text                         # pinned by digest, like llm
    assert "network_mode: host" in text and "/run/dbus:/run/dbus:ro" in text
    assert "${CORE_STATE:-/var/lib/skippy-core}/matter:/data" in text and "${MATTER_ARGS:-}" in text
    assert "ports:" not in text                                                  # host network: the firewall decides (install/matter.sh)


def test_a_failed_build_touches_nothing_and_waits_before_trying_again(box):
    d, commit, first = box
    d.run()
    broken = commit(build=False)
    row = d.run()
    assert row["state"] == "refused" and row["stage"] == "build" and d.running == (first, "")   # no rollback, nothing redeployed
    assert d.deployed == [(first, "")]
    assert d.run()["state"] == "retry-later"                                                    # not every two minutes
    d._write("retry.json", {f"{broken}|": 0})                                                   # the wait is over
    fixed = commit()
    assert d.run()["state"] == "ok" and d.running == (fixed, "")


def test_it_holds_a_deploy_while_the_private_skippy_is_working(box, monkeypatch):
    d, commit, first = box
    d.s["QUIET"] = "1"
    monkeypatch.setattr(dep.quiet, "busy", lambda *a, **k: True)
    assert d.run()["state"] == "deferred" and d.running is None
    monkeypatch.setattr(dep.quiet, "busy", lambda *a, **k: False)
    assert d.run()["state"] == "ok" and d.running == (first, "")


def _doctor_box(tmp_path, monkeypatch, doctor_dir=True):
    for k in dep.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    repo, state = tmp_path / "repo", tmp_path / "state"
    (repo / "core" / "deployer").mkdir(parents=True)
    state.mkdir()
    (repo / "core" / "deployer" / "deployer.py").write_text("# this commit's deployer\n")
    copy = tmp_path / "doctor" / "deployer.py"
    if doctor_dir:
        copy.parent.mkdir()
        copy.write_text("# yesterday's deployer, no CORE_TESLA\n")
    f = tmp_path / "core.env"
    f.write_text(f"REPO_DIR={repo}\nCORE_STATE={state}\nDOCTOR_COPY={copy}\n")
    return dep.Deployer(dep.load_settings(str(f)), log=lambda m: None), copy


def test_a_good_deploy_brings_the_doctors_copy_up_to_date(tmp_path, monkeypatch):
    d, copy = _doctor_box(tmp_path, monkeypatch)
    d.mark_good("abc123", "")
    assert copy.read_text() == "# this commit's deployer\n"         # a repair from the doctor now knows the stack this deploy knows
    assert not copy.with_name("deployer.py.new").exists()


def test_no_doctor_folder_no_copy(tmp_path, monkeypatch):
    d, copy = _doctor_box(tmp_path, monkeypatch, doctor_dir=False)  # CI and the simulator: nothing installed the doctor
    d.mark_good("abc123", "")
    assert not copy.parent.exists()
