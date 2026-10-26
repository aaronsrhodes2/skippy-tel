"""rollback.yml (the phone's way back while GitHub is still the mirror) and the runbook, as the files say they are. The deploy itself
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


ROLLBACK = load("rollback.yml")
CI_PLAN = (ROOT / "core" / "ci" / "ci.py").read_text(encoding="utf-8")       # what skippy-core's CI runs


# ── portal/Dockerfile: the build identity, late in the file ──────────────────

def test_the_dockerfile_bakes_the_build_identity_in_last_so_the_dependency_layers_stay_cached():
    lines = (ROOT / "portal" / "Dockerfile").read_text(encoding="utf-8").splitlines()
    idx = {key: next(i for i, l in enumerate(lines) if l.startswith(key)) for key in
           ("COPY portal/requirements.txt", "RUN pip install", "ADD https://github.com", "COPY portal/ .", "ARG GIT_SHA", "ARG BUILD_TIME", "ENV GIT_SHA")}
    assert "ENV GIT_SHA=$GIT_SHA BUILD_TIME=$BUILD_TIME" in lines
    assert idx["RUN pip install"] < idx["ADD https://github.com"] < idx["COPY portal/ ."] < idx["ARG GIT_SHA"] < idx["ENV GIT_SHA"]
    assert idx["ARG BUILD_TIME"] < idx["ENV GIT_SHA"]
    copies_after = [l for l in lines[idx["ARG GIT_SHA"]:] if l.startswith(("COPY", "ADD", "RUN"))]
    assert copies_after == []                                                   # nothing after them is ever cached


# ── rollback.yml ─────────────────────────────────────────────────────────────

def test_rollback_is_a_manual_workflow_with_one_optional_blank_input_so_a_phone_needs_no_typing():
    on = triggers(ROLLBACK)
    assert list(on) == ["workflow_dispatch"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert list(inputs) == ["revision"]
    assert inputs["revision"]["required"] is False and inputs["revision"]["default"] == ""


def test_rollback_authenticates_as_the_deployer():
    assert ROLLBACK["env"]["DEPLOYER_SA"] == "skippy-deployer@skippy-tel.iam.gserviceaccount.com"
    assert ROLLBACK["env"]["WIF_PROVIDER"].endswith("/workloadIdentityPools/github/providers/github")
    assert ROLLBACK["permissions"] == {"contents": "read", "id-token": "write"}
    auth = [s for s in steps_of(ROLLBACK, "rollback") if str(s.get("uses", "")).startswith("google-github-actions/auth@")]
    assert len(auth) == 1
    assert any(str(s.get("uses", "")).startswith("google-github-actions/setup-gcloud@") for s in steps_of(ROLLBACK, "rollback"))


def test_rollback_has_its_own_concurrency_group_so_a_pending_deploy_cannot_cancel_it():
    assert ROLLBACK["concurrency"]["group"] == "rollback"
    assert ROLLBACK["concurrency"]["cancel-in-progress"] is False


def test_a_typed_revision_reaches_the_script_only_through_the_environment():
    """`${{ inputs.revision }}` pasted into a shell script would let a typed value run commands."""
    move = step(steps_of(ROLLBACK, "rollback"), "Move the traffic")
    assert move["env"]["REVISION"] == "${{ inputs.revision }}"
    assert "${{" not in move["run"]
    assert '--want="$REVISION"' in move["run"]


def test_rollback_lists_picks_moves_traffic_and_proves_health():
    run = step(steps_of(ROLLBACK, "rollback"), "Move the traffic")["run"]
    assert "gcloud run revisions list --service skippy-portal" in run and "--format=json" in run
    assert "tools/rollback_pick.py" in run and '--summary="$GITHUB_STEP_SUMMARY"' in run
    assert run.index("rollback_pick.py") < run.index("update-traffic")                   # decide first, then move
    assert '--to-revisions "$target=100"' in run and "--to-latest" in run
    assert '"$URL/health"' in run and "--retry" in run
    assert run.rstrip().endswith('[ "$health" = ok ]')                                   # unhealthy afterwards = a red job
    assert "set -euo pipefail" in run


def test_rollback_fails_the_job_and_changes_nothing_when_there_is_no_safe_revision():
    run = step(steps_of(ROLLBACK, "rollback"), "Move the traffic")["run"]
    block = run[run.index("if ! target="):run.index("fi", run.index("if ! target="))]
    assert "exit 1" in block and "Nothing was changed" in block


# ── every shell block, every gcloud flag ─────────────────────────────────────

def all_runs() -> list[tuple[str, str]]:
    out = []
    for wf, name in ((ROLLBACK, "rollback.yml"), (load("ops.yml"), "ops.yml"), (load("domains.yml"), "domains.yml")):
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


# What `gcloud ... --help` lists for each command used (SDK 586.0.0, 2026-09-30). A flag outside this table fails the test.
GCLOUD_FLAGS = {
    "run services describe": {"--region", "--format"},
    "run services update-traffic": {"--region", "--to-latest", "--to-revisions"},
    "run revisions list": {"--service", "--region", "--format"},
    "run deploy": {"--image", "--region", "--service-account", "--no-invoker-iam-check", "--timeout", "--max-instances",
                   "--memory", "--set-env-vars", "--set-secrets", "--cpu", "--concurrency"},
}


def gcloud_commands(script: str):
    joined = script.replace("\\\n", " ")
    for line in joined.splitlines():
        m = re.search(r"\bgcloud (run (?:services (?:describe|update-traffic)|revisions list|deploy))\b(.*)", line)
        if m:
            yield m.group(1), m.group(2)


def test_every_gcloud_flag_used_for_cloud_run_is_one_the_command_has():
    seen = 0
    for where, script in all_runs():
        for cmd, rest in gcloud_commands(script):
            allowed = GCLOUD_FLAGS[cmd]
            for flag in re.findall(r"(?<![\w$-])(--[a-z][a-z-]*)", re.sub(r'"[^"]*"|\'[^\']*\'', "", rest)):
                assert flag in allowed, f"{where}: `gcloud {cmd}` uses {flag}, not in the verified list {sorted(allowed)}"
                seen += 1
    assert seen >= 3                                            # the scan really found the commands


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
NEW_FILES = [RUNBOOK, WORKFLOWS / "rollback.yml", ROOT / "tools" / "bridge_supervisor.sh",
             ROOT / "tools" / "install_bridge_startup.sh", ROOT / "tools" / "rollback_pick.py"]


def test_the_runbook_is_linked_from_the_ci_doc_and_from_claude_md():
    assert "portal/remote-recovery.md" in (ROOT / "docs" / "cicd.md").read_text(encoding="utf-8")
    assert "docs/portal/remote-recovery.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_the_runbook_names_the_steps_the_captain_must_be_able_to_find():
    text = RUNBOOK.read_text(encoding="utf-8")
    for needle in ("Read the card on `/me`", "**ops**", "`command` = **home**", "**rollback**", "What only the PC can fix",
                   "Start Docker Desktop when you sign in to Windows",
                   "powercfg /change standby-timeout-ac 0", "bash tools/install_bridge_startup.sh --apply",
                   "bash tools/bridge_supervisor.sh --status", "claude auth login", "docker logs"):
        assert needle in text, needle
    assert text.index("## 1.") < text.index("## 2.") < text.index("## 3.") < text.index("## 4.")      # the order of the checklist


def test_every_command_the_runbook_gives_exists():
    """The words in a runbook rot first. The workflows, make targets and script options it names must be real."""
    text = RUNBOOK.read_text(encoding="utf-8")
    names = {yaml.safe_load((WORKFLOWS / f).read_text(encoding="utf-8"))["name"] for f in ("ops.yml", "rollback.yml")}
    assert {"ops", "rollback"} <= names
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
