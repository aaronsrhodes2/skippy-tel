"""The deploy workflow's rollback, and rollback.yml, as the files say they are.

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

yaml = pytest.importorskip("yaml")          # CI installs it (test.yml / deploy.yml); a bare machine skips these

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


DEPLOY = load("deploy.yml")
ROLLBACK = load("rollback.yml")
PORTAL = steps_of(DEPLOY, "portal")


# ── deploy.yml: the portal job ───────────────────────────────────────────────

def test_the_previous_revision_is_recorded_before_the_deploy_and_the_rollback_comes_last():
    order = [index_of(PORTAL, n) for n in ("Note the revision serving now", "Deploy to Cloud Run", "Send all traffic",
                                          "Smoke test", "Roll back to the previous revision")]
    assert order == sorted(order) and order[-1] == len(PORTAL) - 1


def test_the_recording_step_asks_for_the_serving_revision_and_writes_it_to_the_job_environment():
    run = step(PORTAL, "Note the revision serving now")["run"]
    assert "gcloud run services describe skippy-portal" in run
    assert "--format='value(status.traffic[0].revisionName)'" in run
    assert "PREV_REVISION=$prev" in run and "$GITHUB_ENV" in run
    assert "|| true" in run                                      # a new service has none: that must not fail the deploy
    assert re.search(r"\^skippy-portal-\[a-z0-9-\]\+\$", run)    # only something shaped like a revision name is accepted


def test_the_deploy_step_has_an_id_so_the_rollback_can_tell_whether_it_ever_started():
    assert step(PORTAL, "Deploy to Cloud Run")["id"] == "deploy"


def test_the_rollback_runs_on_failure_or_cancel_with_a_recorded_revision_and_after_the_deploy_step_began():
    rollback = step(PORTAL, "Roll back to the previous revision")
    cond = rollback["if"]
    # A cancelled job makes failure() false: without cancelled() a deploy cancelled after the traffic step leaves the new,
    # possibly broken revision serving everyone (audit A2). The parentheses matter: `&&` binds tighter than `||`.
    assert cond.startswith("(failure() || cancelled()) && ")
    assert "env.PREV_REVISION != ''" in cond
    assert "steps.deploy.outcome != 'skipped'" in cond           # a failed build/push changed nothing: nothing to undo
    assert rollback["timeout-minutes"] == 5                       # short enough to finish inside a cancel


# What each deploy job may take at most (audit A1). GitHub's default is 6 hours, and a hung job holds the `deploy`
# concurrency group, so every later push waits behind it.
DEPLOY_TIMEOUTS = {"changes": 5, "mentat-test": 10, "gate": 5, "infra": 15, "mentat": 15, "portal-image": 15, "portal": 20}


def test_every_deploy_job_has_its_timeout_and_the_reusable_test_jobs_carry_theirs():
    jobs = DEPLOY["jobs"]
    assert {j: b.get("timeout-minutes") for j, b in jobs.items() if "uses" not in b} == DEPLOY_TIMEOUTS
    # `test` calls run-tests.yml, and GitHub allows no timeout-minutes on a job that `uses:` a workflow, so the
    # called jobs must each have one.
    assert "timeout-minutes" not in jobs["test"]
    for name, body in load("run-tests.yml")["jobs"].items():
        assert body.get("timeout-minutes") == 15, name
    for name, body in load("test.yml")["jobs"].items():
        assert "uses" in body or 0 < body.get("timeout-minutes", 0) <= 15, name
    assert 0 < ROLLBACK["jobs"]["rollback"]["timeout-minutes"] <= 10


def curl_calls(script: str) -> list[str]:
    """Every curl command line in a shell block (backslash continuations joined)."""
    return [line.strip() for line in script.replace("\\\n", " ").splitlines() if re.search(r"(^|[\s(|$])curl\s", line)]


def test_every_curl_in_the_deploy_and_rollback_workflows_has_a_time_limit():
    """--retry alone is not a limit: each try could wait out Cloud Run's 900 s request timeout (audit A1)."""
    seen = 0
    for wf, name in ((DEPLOY, "deploy.yml"), (ROLLBACK, "rollback.yml")):
        for job, body in wf["jobs"].items():
            for s in body.get("steps", []):
                for call in curl_calls(s.get("run", "")):
                    seen += 1
                    limit = re.search(r"(?:\s-m\s+|--max-time[\s=]+)(\d+)", call)
                    assert limit, f"{name}:{job}:{s.get('name')}: curl without -m/--max-time: {call}"
                    assert int(limit.group(1)) <= 300, call
    assert seen >= 15                                             # the scan really found them


def test_the_smoke_and_rollback_curls_give_up_within_half_a_minute_each():
    for needle in ("Smoke test", "Roll back to the previous revision"):
        for call in curl_calls(step(PORTAL, needle)["run"]):
            assert int(re.search(r"\s-m\s+(\d+)", call).group(1)) <= 30, call


def test_the_rollback_moves_all_traffic_checks_health_and_is_loud():
    run = step(PORTAL, "Roll back to the previous revision")["run"]
    assert 'gcloud run services update-traffic skippy-portal --region "$REGION" --to-revisions "$PREV_REVISION=100"' in run
    assert '"$URL/health"' in run and "--retry" in run            # the old revision may be scaled to zero: wait for it
    assert "::error::DEPLOY FAILED and was ROLLED BACK" in run
    assert "::error::ROLLBACK FAILED" in run and "exit 1" in run  # and if even that fails, the job says so
    assert "rollback" in run and "remote-recovery.md" in run      # the way forward for a person who reads the failure on a phone


def test_the_job_still_ends_red_after_a_rollback():
    """A step that succeeds after a failed one doesn't turn the job green; nothing may hide the failure."""
    for s in PORTAL:
        assert not s.get("continue-on-error"), s.get("name")
    assert not DEPLOY["jobs"]["portal"].get("continue-on-error")


def test_the_next_deploy_hands_traffic_back_to_latest_because_a_rollback_pins_it():
    """Without this, the deploy after a rollback goes green and serves nothing new."""
    run = step(PORTAL, "Send all traffic to the new revision")["run"]
    assert "gcloud run services update-traffic skippy-portal" in run and "--to-latest" in run
    assert index_of(PORTAL, "Send all traffic") == index_of(PORTAL, "Deploy to Cloud Run") + 1


def test_the_smoke_test_covers_the_reconciler_door_the_public_page_and_the_signed_out_dashboard():
    run = step(PORTAL, "Smoke test")["run"]
    assert re.search(r'curl -s -m 30 --retry 3 --retry-delay 5 -o /dev/null -w \'%\{http_code\}\' "\$URL/home/workshop"\)', run)
    assert run.count("--retry 3") >= 4                                                    # a flaky 503 must not trigger a rollback
    # locked (401) once portal/app.py defines the route; 404 tolerated only while it doesn't exist yet
    assert "grep -q '\"/home/workshop\")' portal/app.py" in run and 'want=401' in run
    assert re.search(r'\[\[ "\$code" =~ \^\(\$want\)\$ \]\] \|\| \{ echo "GET /home/workshop', run)
    assert '"$URL/privacy"' in run and "curl -fsS" in run.split("/privacy")[0].splitlines()[-1]   # 200 or the step fails
    assert '"$URL/me"' in run and "200|301|302|303|307|308" in run                        # a redirect or the sign-in page, never 5xx
    # what was already checked is still checked
    for kept in ('"$URL/health"', "/home/report", '"$URL/experiments/benchmark"', "viewer.js"):
        assert kept in run


def test_the_smoke_test_never_lets_a_5xx_from_me_pass():
    run = step(PORTAL, "Smoke test")["run"]
    case = re.search(r'case "\$code" in (.*?) esac', run).group(1)
    allowed = re.match(r"([\d|]+)\)", case).group(1).split("|")
    assert all(not c.startswith("5") and c not in ("401", "403", "404") for c in allowed)


def test_the_workflow_still_deploys_only_from_main_and_the_job_set_is_the_one_the_docs_describe():
    assert triggers(DEPLOY)["push"]["branches"] == ["main"]
    assert list(DEPLOY["jobs"]) == ["test", "changes", "mentat-test", "gate", "infra", "mentat", "portal-image", "portal"]
    assert "gcloud run deploy skippy-portal" in step(PORTAL, "Deploy to Cloud Run")["run"]


# ── the split: image built while the tests run, the rollback logic intact ────

IMAGE_JOB = DEPLOY["jobs"]["portal-image"]
IMAGE = IMAGE_JOB["steps"]


def test_the_image_is_built_in_its_own_job_that_needs_only_the_gate_so_it_runs_beside_the_tests():
    assert IMAGE_JOB["needs"] == ["gate"]
    assert IMAGE_JOB["if"] == "needs.gate.outputs.ready == 'true'"
    assert "test" not in IMAGE_JOB["needs"]                                   # the whole point: it does not wait for them


def test_the_image_build_pushes_the_sha_tag_the_deploy_uses_with_the_build_identity_and_a_layer_cache():
    build = step(IMAGE, "Build and push")
    assert build["uses"].startswith("docker/build-push-action@")
    w = build["with"]
    assert w["push"] is True and w["file"] == "portal/Dockerfile" and w["context"] == "."
    assert w["tags"] == "${{ env.IMAGE }}:${{ github.sha }}"
    deploy_run = step(PORTAL, "Deploy to Cloud Run")["run"]
    assert '--image "$IMAGE:$GITHUB_SHA"' in deploy_run                        # the very tag the build job pushed
    args = w["build-args"].splitlines()
    assert "GIT_SHA=${{ github.sha }}" in args and "SIGMA_GROUND_REF=${{ env.SIGMA_GROUND_REF }}" in args
    assert any(a.startswith("BUILD_TIME=${{ steps.") and a.endswith(".outputs.utc }}") for a in args)
    assert w["cache-from"] == "type=gha" and w["cache-to"] == "type=gha,mode=max"
    assert w["provenance"] is False                                            # one plain manifest, like `docker build` made
    assert any(str(s.get("uses", "")).startswith("docker/setup-buildx-action@") for s in IMAGE)
    assert index_of(IMAGE, "Build and push") > max(i for i, s in enumerate(IMAGE) if str(s.get("uses", "")).startswith("docker/setup-buildx"))


def test_the_registry_login_happens_before_the_build():
    runs = [s["run"] for s in IMAGE if "run" in s]
    login = next(i for i, s in enumerate(IMAGE) if "configure-docker" in s.get("run", ""))
    assert login < index_of(IMAGE, "Build and push") and any("configure-docker" in r for r in runs)


def test_the_deploy_job_builds_nothing_and_waits_for_the_tests_the_image_and_the_gate():
    job = DEPLOY["jobs"]["portal"]
    assert sorted(job["needs"]) == ["gate", "portal-image", "test"]
    assert job["if"] == "needs.gate.outputs.ready == 'true'"
    assert not any("docker " in s.get("run", "") or str(s.get("uses", "")).startswith("docker/") for s in PORTAL)


def test_every_step_the_rollback_depends_on_is_in_the_one_job_so_the_environment_and_step_outcomes_are_visible():
    """PREV_REVISION goes through $GITHUB_ENV and `steps.deploy` is a step context: both exist only inside one job."""
    cond = step(PORTAL, "Roll back to the previous revision")["if"]
    assert "env.PREV_REVISION" in cond and "steps.deploy.outcome" in cond
    assert "$GITHUB_ENV" in step(PORTAL, "Note the revision serving now")["run"]
    assert step(PORTAL, "Deploy to Cloud Run")["id"] == "deploy"
    for other, body in DEPLOY["jobs"].items():
        if other != "portal":
            text = str(body)
            assert "PREV_REVISION" not in text and "steps.deploy" not in text, other


def test_gcloud_comes_from_the_runner_image_and_only_for_commands_that_are_in_it():
    """skip_install saves ~25 s per job. It is safe only for plain, GA gcloud commands: no components, no alpha/beta."""
    for name in ("portal-image", "portal"):
        steps = DEPLOY["jobs"][name]["steps"]
        sg = [s for s in steps if str(s.get("uses", "")).startswith("google-github-actions/setup-gcloud@")]
        assert len(sg) == 1 and sg[0]["with"]["skip_install"] is True, name
        for s in steps:
            run = s.get("run", "")
            assert "gcloud components" not in run and "gcloud alpha" not in run and "gcloud beta" not in run, name
    # The jobs that were NOT changed keep the installing action.
    assert "with" not in [s for s in DEPLOY["jobs"]["mentat"]["steps"] if str(s.get("uses", "")).startswith("google-github-actions/setup-gcloud@")][0]


# ── the needs/if graph, evaluated the way GitHub does ────────────────────────

def simulate(workflow: dict, outputs: dict[str, dict], outcomes: dict[str, str] | None = None) -> dict[str, str]:
    """Each job's result ('success' / 'failure' / 'skipped') given what the jobs that run will do.

    GitHub's rule: a job whose `if` has no status function gets an implicit success(), so it is SKIPPED when any job it needs
    failed or was skipped; its own `if` is evaluated only after that. (The workflow uses no always()/!cancelled(), asserted below.)
    """
    outcomes = outcomes or {}
    ctx = collections.defaultdict(lambda: collections.defaultdict(str))     # an output nobody set reads as "" in GitHub
    for job_name, outs in outputs.items():
        ctx[job_name].update(outs)
    jobs = workflow["jobs"]
    result: dict[str, str] = {}

    def run(job: str) -> str:
        if job in result:
            return result[job]
        body = jobs[job]
        needs = body.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        cond = body.get("if")
        if cond is not None:
            assert not re.search(r"always\(|cancelled\(|failure\(|success\(", str(cond)), f"{job}: a status function changes the skip rules"
        if any(run(n) != "success" for n in needs):
            result[job] = "skipped"
        elif cond is not None:
            expr = re.sub(r"needs\.([\w-]+)\.outputs\.(\w+)", r'O["\1"]["\2"]', str(cond)).replace("&&", " and ").replace("||", " or ")
            assert re.fullmatch(r"""[\sA-Za-z"\[\]'=!_.-]*""", expr), expr      # nothing the evaluator doesn't understand
            result[job] = outcomes.get(job, "success") if eval(expr, {"__builtins__": {}}, {"O": ctx}) else "skipped"
        else:
            result[job] = outcomes.get(job, "success")
        return result[job]

    for job in jobs:
        run(job)
    return result


READY = {"gate": {"ready": "true"}}


def test_a_normal_push_tests_builds_and_deploys_the_portal_and_skips_mentat():
    r = simulate(DEPLOY, {**READY, "changes": {"mentat": "false"}})
    assert r == {"test": "success", "changes": "success", "mentat-test": "skipped", "gate": "success", "infra": "success",
                 "mentat": "skipped", "portal-image": "success", "portal": "success"}


def test_a_docs_only_push_is_the_same_as_a_normal_one_for_the_portal():
    """changes says mentat=false for it; the portal and infra still run (nothing in the spec asks to skip them)."""
    r = simulate(DEPLOY, {**READY, "changes": {"mentat": "false"}})
    assert r["portal"] == r["infra"] == r["test"] == "success" and r["mentat"] == "skipped"


def test_a_push_touching_mentat_or_a_manual_run_runs_everything():
    r = simulate(DEPLOY, {**READY, "changes": {"mentat": "true"}})
    assert set(r.values()) == {"success"}


def test_failing_tests_deploy_nothing_but_the_image_may_already_exist():
    r = simulate(DEPLOY, {**READY, "changes": {"mentat": "true"}}, {"test": "failure"})
    assert r["portal"] == r["infra"] == r["mentat"] == "skipped"
    assert r["portal-image"] == "success"                                      # harmless: nothing deploys it


def test_a_failed_image_build_stops_the_portal_deploy_only():
    r = simulate(DEPLOY, {**READY, "changes": {"mentat": "true"}}, {"portal-image": "failure"})
    assert r["portal"] == "skipped" and r["infra"] == "success" and r["mentat"] == "success"


def test_an_unbootstrapped_project_deploys_nothing():
    r = simulate(DEPLOY, {"gate": {"ready": "false"}, "changes": {"mentat": "true"}})
    assert r["portal"] == r["portal-image"] == r["infra"] == r["mentat"] == "skipped"
    assert r["test"] == "success"


def test_nothing_in_the_mentat_chain_can_hold_up_or_skip_the_portal():
    """A failed or silent `changes`, or a failing Mentat test, must leave the portal deploying."""
    for broken in ({"changes": "failure"}, {"mentat-test": "failure"}, {"mentat": "failure"}):
        r = simulate(DEPLOY, {**READY, "changes": {"mentat": "true"}}, broken)
        assert r["portal"] == "success", broken
    r = simulate(DEPLOY, {**READY, "changes": {}})                              # no output at all
    assert r["portal"] == "success" and r["mentat"] == "skipped"
    # structurally: the portal's whole ancestry never touches a Mentat job or `changes`
    seen, todo = set(), ["portal"]
    while todo:
        j = todo.pop()
        for n in DEPLOY["jobs"][j].get("needs") or []:
            if n not in seen:
                seen.add(n)
                todo.append(n)
    assert seen == {"test", "portal-image", "gate"}


def test_the_mentat_jobs_run_only_when_changes_says_so_and_mentat_needs_the_tests_too():
    assert DEPLOY["jobs"]["mentat-test"]["needs"] == ["changes"]
    assert DEPLOY["jobs"]["mentat-test"]["if"] == "needs.changes.outputs.mentat == 'true'"
    m = DEPLOY["jobs"]["mentat"]
    assert {"test", "mentat-test", "changes", "gate"} <= set(m["needs"])
    assert "needs.changes.outputs.mentat == 'true'" in m["if"] and "needs.gate.outputs.ready == 'true'" in m["if"]


def test_the_changes_job_fails_safe_and_asks_for_the_last_successful_deploy_not_before():
    job = DEPLOY["jobs"]["changes"]
    assert job["outputs"]["mentat"] == "${{ steps.decide.outputs.mentat }}"
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    base = job["steps"][1]["run"]
    assert "gh run list" in base and "--workflow deploy.yml" in base and "--status success" in base
    assert "github.event.before" not in str(job)                               # `before` loses cancelled and red runs' changes
    assert "|| true" in base and "^[0-9a-f]{40}$" in base                     # a failure or junk is "no base" = run everything
    decide = job["steps"][2]["run"]
    assert "tools/ci_changes.py" in decide and "mentat=true" in decide          # the script itself failing means "run it"
    assert "workflow_dispatch" in triggers(DEPLOY)                              # a manual run is passed as the event: the script runs everything


# ── run-tests.yml: the suites, once ──────────────────────────────────────────

RUN_TESTS = load("run-tests.yml")
TEST_YML = load("test.yml")


def test_deploy_and_the_pull_request_workflow_call_the_one_shared_test_workflow():
    assert triggers(RUN_TESTS) == {"workflow_call": None}
    assert DEPLOY["jobs"]["test"] == {"uses": "./.github/workflows/run-tests.yml"}
    assert TEST_YML["jobs"]["test"] == {"uses": "./.github/workflows/run-tests.yml"}
    assert list(RUN_TESTS["jobs"]) == ["suite", "flask-api"]                    # two parallel jobs: no `needs` between them
    assert not any(j.get("needs") for j in RUN_TESTS["jobs"].values())
    assert RUN_TESTS["permissions"] == {"contents": "read"}                    # never more than the callers grant


def test_the_two_ci_pytest_commands_are_the_ones_the_definition_of_done_names_run_in_parallel_by_file():
    suite = [s["run"] for s in RUN_TESTS["jobs"]["suite"]["steps"] if "pytest" in s.get("run", "")]
    flask = [s["run"] for s in RUN_TESTS["jobs"]["flask-api"]["steps"] if "pytest" in s.get("run", "")]
    assert suite == ["python -m pytest portal/tests memory/tests tools/tests services/home-reporter/tests "
                     "services/claude-bridge/tests mentat/tests -q -n auto --dist loadfile --durations=10"]
    assert flask == ["python -m pytest services/flask-api/tests -q -n auto --dist loadfile --durations=10"]


def test_each_test_job_caches_pip_on_every_requirements_file_it_installs():
    for name, job in RUN_TESTS["jobs"].items():
        py = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-python@")][0]["with"]
        assert py["cache"] == "pip" and py["python-version"] == "3.12"
        cached = py["cache-dependency-path"].split()
        install = [s["run"] for s in job["steps"] if s.get("run", "").startswith("pip install")]
        assert len(install) == 1
        wanted = re.findall(r"-r (\S+)", install[0])
        assert wanted and set(wanted) == set(cached), name


def test_the_flask_api_job_installs_its_own_requirements_and_the_suite_job_does_not():
    installs = {n: [s["run"] for s in j["steps"] if s.get("run", "").startswith("pip install")][0] for n, j in RUN_TESTS["jobs"].items()}
    assert "services/flask-api/requirements.txt" in installs["flask-api"] and "portal/requirements.txt" not in installs["flask-api"]
    assert "portal/requirements.txt" in installs["suite"] and "flask-api" not in installs["suite"]


def test_the_ci_requirements_file_has_what_the_tests_import_and_xdist():
    text = (ROOT / "tools" / "requirements-ci.txt").read_text(encoding="utf-8")
    names = {re.split(r"[\s>=<]", line.strip())[0] for line in text.splitlines() if line.strip() and not line.startswith("#")}
    assert {"pytest", "pytest-xdist", "pillow", "pyyaml"} <= names


def test_the_pull_request_workflow_still_syntax_checks_the_shell_scripts():
    run = TEST_YML["jobs"]["shell-syntax"]["steps"][1]["run"]
    assert run == "bash -n infra/gcp-games/*.sh infra/bootstrap/*.sh scripts/games.sh tools/*.sh"


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


def test_rollback_authenticates_exactly_as_deploy_does():
    for key in ("PROJECT_ID", "REGION", "WIF_PROVIDER", "DEPLOYER_SA"):
        assert ROLLBACK["env"][key] == DEPLOY["env"][key], key
    assert ROLLBACK["permissions"] == DEPLOY["permissions"] == {"contents": "read", "id-token": "write"}
    auth = [s for s in steps_of(ROLLBACK, "rollback") if str(s.get("uses", "")).startswith("google-github-actions/auth@")]
    deploy_auth = [s for s in PORTAL if str(s.get("uses", "")).startswith("google-github-actions/auth@")]
    assert len(auth) == 1 and auth[0] == deploy_auth[0]
    assert any(str(s.get("uses", "")).startswith("google-github-actions/setup-gcloud@") for s in steps_of(ROLLBACK, "rollback"))


def test_rollback_has_its_own_concurrency_group_so_a_pending_deploy_cannot_cancel_it():
    assert ROLLBACK["concurrency"]["group"] == "rollback" != DEPLOY["concurrency"]["group"]
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
    for wf, name in ((DEPLOY, "deploy.yml"), (ROLLBACK, "rollback.yml"), (load("run-tests.yml"), "run-tests.yml"), (load("test.yml"), "test.yml")):
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
    assert seen >= 12                                           # the scan really found the commands


# ── the other files this change touches ──────────────────────────────────────

def test_ci_checks_the_new_scripts_with_bash_dash_n_and_has_pyyaml_for_these_tests():
    test_yml = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
    assert "tools/*.sh" in test_yml
    # pyyaml (and the rest) come from tools/requirements-ci.txt, which the shared run-tests.yml installs for BOTH callers
    assert "pyyaml" in (ROOT / "tools" / "requirements-ci.txt").read_text(encoding="utf-8")
    assert "tools/requirements-ci.txt" in (WORKFLOWS / "run-tests.yml").read_text(encoding="utf-8")


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
    names = {yaml.safe_load((WORKFLOWS / f).read_text(encoding="utf-8"))["name"] for f in ("ops.yml", "rollback.yml", "deploy.yml")}
    assert {"ops", "rollback", "deploy"} <= names
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
    for path in NEW_FILES + [WORKFLOWS / "deploy.yml"]:
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            assert not re.search(pat, text), f"{path.name} matches {pat}"
