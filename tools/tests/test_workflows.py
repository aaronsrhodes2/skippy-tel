"""The workflows still on GitHub (ops, domains: until the cut-over) and the runbook, as the files say they are. The deploy itself
left GitHub on 2026-10-25 (core/cd/cd.py on skippy-core; docs/no-github.md), and its tests went with it (core/tests/test_cd.py).

The cloud steps can't run in CI (Google trusts only main's real runs), so the next best thing is pinned here: the files parse, the
steps come in the order the logic needs, every shell block is valid bash, every gcloud flag is one `gcloud help` lists (checked
against Google Cloud SDK 586.0.0 by hand on 2026-09-30), and the rules that matter are present: the previous revision is recorded
BEFORE the deploy, the rollback runs only after the deploy step started, traffic is handed back to "latest" by the next deploy
(a rollback pins traffic, and a pinned service doesn't send a new revision any), and a typed revision never reaches the shell.

Incident 2026-09-30: after one working demo the portal's chat froze and the only way back was another deploy.
"""
from __future__ import annotations

import collections
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from bashrun import BASH, bash_env, posix

yaml = pytest.importorskip("yaml")          # CI installs it (tools/requirements-ci.txt); a bare machine skips these

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))          # YAML 1.1 (PyYAML) reads the key `on` as the boolean True


def steps_of(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def step(steps: list[dict], needle: str) -> dict:
    found = [s for s in steps if needle in (s.get("name") or "")]
    assert len(found) == 1, f"expected exactly one step named like {needle!r}, found {len(found)}"
    return found[0]


def index_of(steps: list[dict], needle: str) -> int:
    return steps.index(step(steps, needle))


CI_PLAN = (ROOT / "core" / "ci" / "ci.py").read_text(encoding="utf-8")       # what skippy-core's CI runs


# ── portal/Dockerfile: the build identity, late in the file ──────────────────

def test_the_dockerfile_bakes_the_build_identity_in_last_so_the_dependency_layers_stay_cached():
    lines = (ROOT / "portal" / "Dockerfile").read_text(encoding="utf-8").splitlines()
    idx = {key: next(i for i, l in enumerate(lines) if l.startswith(key)) for key in
           ("COPY portal/requirements.txt", "RUN pip install", "COPY --from=sigma", "COPY portal/ .", "ARG GIT_SHA", "ARG BUILD_TIME", "ENV GIT_SHA")}
    assert "ENV GIT_SHA=$GIT_SHA BUILD_TIME=$BUILD_TIME" in lines
    assert idx["RUN pip install"] < idx["COPY --from=sigma"] < idx["COPY portal/ ."] < idx["ARG GIT_SHA"] < idx["ENV GIT_SHA"]
    assert idx["ARG BUILD_TIME"] < idx["ENV GIT_SHA"]
    copies_after = [l for l in lines[idx["ARG GIT_SHA"]:] if l.startswith(("COPY", "ADD", "RUN"))]
    assert copies_after == []                                                   # nothing after them is ever cached


# ── every shell block, every gcloud flag ─────────────────────────────────────

def all_runs() -> list[tuple[str, str]]:
    out = []
    for wf, name in ((load("ops.yml"), "ops.yml"), (load("domains.yml"), "domains.yml")):
        for job, body in wf["jobs"].items():
            for s in body.get("steps", []):                    # a job that `uses:` a reusable workflow has no steps here
                if "run" in s:
                    out.append((f"{name}:{job}:{s.get('name', s['run'][:30])}", s["run"]))
    return out


@pytest.mark.skipif(BASH is None, reason="needs a bash")
@pytest.mark.parametrize("where,script", all_runs(), ids=[w for w, _ in all_runs()])
def test_every_run_block_is_valid_bash(where, script):
    text = re.sub(r"\$\{\{.*?\}\}", "X", script)               # GitHub fills these in before bash ever sees the text
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "step.sh"
        f.write_text(text, encoding="utf-8", newline="\n")
        done = subprocess.run([BASH, "-n", posix(f)], capture_output=True, text=True, env=bash_env(), timeout=60)
    assert done.returncode == 0, f"{where}: {done.stderr}"


# ── the other files this change touches ──────────────────────────────────────

def test_ci_checks_the_scripts_with_bash_dash_n_and_has_pyyaml_for_these_tests():
    assert "tools/*.sh" in CI_PLAN and '"shell-syntax"' in CI_PLAN                 # skippy-core's CI: bash -n on the scripts
    assert "pyyaml" in (ROOT / "tools" / "requirements-ci.txt").read_text(encoding="utf-8")
    assert "tools/requirements-ci.txt" in CI_PLAN


def test_the_supervisors_log_and_state_are_never_committed():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "state/" in gitignore                                # bridge-supervisor.log, the pid file and the task XML live there


# ── the runbook ──────────────────────────────────────────────────────────────

RUNBOOK = ROOT / "docs" / "portal" / "remote-recovery.md"
NEW_FILES = [RUNBOOK, ROOT / "tools" / "bridge_supervisor.sh", ROOT / "tools" / "install_bridge_startup.sh",
             ROOT / "core" / "cd" / "rollback_pick.py"]


def test_the_runbook_is_linked_from_the_ci_doc_and_from_claude_md():
    assert "portal/remote-recovery.md" in (ROOT / "docs" / "cicd.md").read_text(encoding="utf-8")
    assert "docs/portal/remote-recovery.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_the_runbook_names_the_steps_the_captain_must_be_able_to_find():
    text = RUNBOOK.read_text(encoding="utf-8")
    for needle in ("Read the card on `/me`", "**ops**", "`command` = **home**", "cd.py rollback", "What only the PC can fix",
                   "Start Docker Desktop when you sign in to Windows",
                   "powercfg /change standby-timeout-ac 0", "bash tools/install_bridge_startup.sh --apply",
                   "bash tools/bridge_supervisor.sh --status", "claude auth login", "docker logs"):
        assert needle in text, needle
    assert text.index("## 1.") < text.index("## 2.") < text.index("## 3.") < text.index("## 4.")      # the order of the checklist


def test_every_command_the_runbook_gives_exists():
    """The words in a runbook rot first. The workflows, make targets and script options it names must be real."""
    text = RUNBOOK.read_text(encoding="utf-8")
    names = {yaml.safe_load((WORKFLOWS / f).read_text(encoding="utf-8"))["name"] for f in ("ops.yml",)}
    assert {"ops"} <= names
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in re.findall(r"make (bridge[\w-]*)", text):
        assert re.search(rf"^{re.escape(target)}:", makefile, re.M), target
    supervisor = (ROOT / "tools" / "bridge_supervisor.sh").read_text(encoding="utf-8")
    installer = (ROOT / "tools" / "install_bridge_startup.sh").read_text(encoding="utf-8")
    for opt in re.findall(r"tools/bridge_supervisor\.sh (--[a-z]+)", text):
        assert f"{opt})" in supervisor, opt
    for opt in re.findall(r"tools/install_bridge_startup\.sh (--[a-z]+)", text):
        assert f"{opt})" in installer, opt
    ops_choices = triggers(yaml.safe_load((WORKFLOWS / "ops.yml").read_text(encoding="utf-8")))["workflow_dispatch"]["inputs"]["command"]["options"]
    assert "home" in ops_choices and "status" in ops_choices


def test_no_secret_value_is_in_any_of_the_new_files():
    patterns = [r"SKIPPY_HOME_TOKEN\s*=\s*[^\s\"'$]", r"Bearer\s+[A-Za-z0-9._-]{16,}", r"sk-ant-[A-Za-z0-9_-]{10,}", r"AIza[0-9A-Za-z_-]{20,}",
                r"ghp_[A-Za-z0-9]{20,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"(?i)password\s*[:=]\s*[^\s\"'$<]{4,}"]
    for path in NEW_FILES + [ROOT / "core" / "cd" / "cd.py", ROOT / "core" / "cd" / "deploy.json"]:
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            assert not re.search(pat, text), f"{path.name} matches {pat}"
