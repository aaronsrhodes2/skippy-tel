"""The runbook (docs/portal/remote-recovery.md) and the files it names, as they are. GitHub has no workflows any more: the deploy,
rollback, ops and domains all run from skippy-core (core/cd/cd.py; docs/no-github.md), and their tests are in core/tests."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI_PLAN = (ROOT / "core" / "ci" / "ci.py").read_text(encoding="utf-8")       # what skippy-core's CI runs


def test_nothing_is_left_in_githubs_workflows_folder():
    assert not (ROOT / ".github" / "workflows").exists() or not any((ROOT / ".github" / "workflows").iterdir())


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


def test_ci_checks_the_scripts_with_bash_dash_n_and_has_pyyaml_for_these_tests():
    assert "tools/*.sh" in CI_PLAN and '"shell-syntax"' in CI_PLAN                 # skippy-core's CI: bash -n on the scripts
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
    for needle in ("Read the card on `/me`", "cd.py ops home", "cd.py rollback", "What only the PC can fix",
                   "Start Docker Desktop when you sign in to Windows",
                   "powercfg /change standby-timeout-ac 0", "bash tools/install_bridge_startup.sh --apply",
                   "bash tools/bridge_supervisor.sh --status", "claude auth login", "docker logs"):
        assert needle in text, needle
    assert text.index("## 1.") < text.index("## 2.") < text.index("## 3.") < text.index("## 4.")      # the order of the checklist


def test_every_command_the_runbook_gives_exists():
    """The words in a runbook rot first. The workflows, make targets and script options it names must be real."""
    text = RUNBOOK.read_text(encoding="utf-8")
    cd = (ROOT / "core" / "cd" / "cd.py").read_text(encoding="utf-8")
    for command in ("rollback", "ops", "domains"):
        assert f'cmd == "{command}"' in cd, command            # the commands the runbook gives for skippy-core
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in re.findall(r"make (bridge[\w-]*)", text):
        assert re.search(rf"^{re.escape(target)}:", makefile, re.M), target
    supervisor = (ROOT / "tools" / "bridge_supervisor.sh").read_text(encoding="utf-8")
    installer = (ROOT / "tools" / "install_bridge_startup.sh").read_text(encoding="utf-8")
    for opt in re.findall(r"tools/bridge_supervisor\.sh (--[a-z]+)", text):
        assert f"{opt})" in supervisor, opt
    for opt in re.findall(r"tools/install_bridge_startup\.sh (--[a-z]+)", text):
        assert f"{opt})" in installer, opt
    remote = (ROOT / "scripts" / "remote.py").read_text(encoding="utf-8")
    assert '"home"' in remote and '"status"' in remote or "status" in remote and "home" in remote


def test_no_secret_value_is_in_any_of_the_new_files():
    patterns = [r"SKIPPY_HOME_TOKEN\s*=\s*[^\s\"'$]", r"Bearer\s+[A-Za-z0-9._-]{16,}", r"sk-ant-[A-Za-z0-9_-]{10,}", r"AIza[0-9A-Za-z_-]{20,}",
                r"ghp_[A-Za-z0-9]{20,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"(?i)password\s*[:=]\s*[^\s\"'$<]{4,}"]
    for path in NEW_FILES + [ROOT / "core" / "cd" / "cd.py", ROOT / "core" / "cd" / "deploy.json"]:
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            assert not re.search(pat, text), f"{path.name} matches {pat}"
