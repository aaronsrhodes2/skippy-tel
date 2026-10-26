"""The shell inside rollback.yml, run for real against a stub `gcloud` and `curl`.

test_workflows.py reads the workflow files; this runs the exact `run:` text a runner would run (taken out of the YAML, not copied
here), with `gcloud` and `curl` replaced by stubs that record their arguments, so the quoting, the order of calls and the exit
codes are exercised. It cannot prove Google's side (the JSON gcloud really prints, that update-traffic really pins): those parts
were checked against `gcloud help` and the SDK's source on 2026-09-30, and are named in the commit message as untested.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bashrun import BASH, bash_env, posix

yaml = pytest.importorskip("yaml")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

GCLOUD = """#!/usr/bin/env bash
echo "gcloud $*" >> "$STUB_DIR/calls.txt"
case "$*" in
  *"services describe"*"status.traffic[0].revisionName"*) cat "$STUB_DIR/serving"; echo ;;
  *"services describe"*"status.url"*) echo "http://stub.invalid" ;;
  *"revisions list"*) cat "$STUB_DIR/revisions.json" ;;
  *"services update-traffic"*)
    if [ -n "${STUB_UPDATE_FAIL:-}" ]; then echo "ERROR: permission denied" >&2; exit 1; fi
    for a in "$@"; do case "$a" in *=100) echo "${a%=100}" > "$STUB_DIR/serving" ;; esac; done ;;
  *) echo "unexpected gcloud call: $*" >&2; exit 99 ;;
esac
"""
CURL = """#!/usr/bin/env bash
echo "curl $*" >> "$STUB_DIR/calls.txt"
echo '{"status": "ok"}'
exit "${STUB_CURL_RC:-0}"
"""
PYTHON3 = """#!/usr/bin/env bash
exec "%s" "$@"
"""


def name(n: int) -> str:
    return f"skippy-portal-{n:05d}-abc"


def revisions(count: int, newest_unready: bool = False) -> list[dict]:
    out = []
    for n in range(1, count + 1):
        ok = not (newest_unready and n == count)
        out.append({"metadata": {"name": name(n), "creationTimestamp": f"2026-09-{n:02d}T10:00:00.000000Z"},
                    "spec": {"containers": [{"image": f"us-west1-docker.pkg.dev/skippy-tel/skippy/portal:{n:x}" + "0" * 39}]},
                    "status": {"conditions": [{"type": "Ready", "status": "True" if ok else "False"}]}})
    return out


class Rig:
    def __init__(self, tmp_path: Path, serving: str, revs: list[dict] | None = None, **extra_env):
        if " " in posix(tmp_path):
            pytest.skip("stub paths with spaces are not worth the quoting")
        self.dir = tmp_path / "stubs"
        self.dir.mkdir()
        for fname, body in (("gcloud", GCLOUD), ("curl", CURL), ("python3", PYTHON3 % posix(sys.executable))):
            f = self.dir / fname
            f.write_text(body, encoding="utf-8", newline="\n")
            f.chmod(0o755)
        (self.dir / "serving").write_text(serving + "\n", encoding="utf-8")
        (self.dir / "revisions.json").write_text(json.dumps(revs if revs is not None else revisions(5)), encoding="utf-8")
        self.summary = tmp_path / "summary.md"
        self.temp = tmp_path / "runner-temp"
        self.temp.mkdir()
        github_env = tmp_path / "github_env"
        github_env.write_text("", encoding="utf-8")
        self.env = {"STUB_DIR": posix(self.dir), "REGION": "us-west1", "RUNNER_TEMP": posix(self.temp),
                    "GITHUB_STEP_SUMMARY": posix(self.summary), "GITHUB_ENV": posix(github_env), "GITHUB_SHA": "f" * 40, **extra_env}
        self.script = tmp_path / "step.sh"

    def run(self, text: str, timeout: float = 120):
        # The stubs must shadow the real gcloud and curl. Setting PATH from outside isn't enough on Windows: Git's bash puts
        # /mingw64/bin (the real curl) in front of whatever it inherits. So the script itself puts the stub folder first.
        preamble = f'PATH="$(cygpath -u "{posix(self.dir)}" 2>/dev/null || echo "{posix(self.dir)}"):$PATH"\n'
        self.script.write_text(preamble + text, encoding="utf-8", newline="\n")
        return subprocess.run([BASH, "-e", posix(self.script)], cwd=ROOT, env=bash_env(self.env), capture_output=True, text=True,
                              timeout=timeout)

    @property
    def calls(self) -> list[str]:
        f = self.dir / "calls.txt"
        return f.read_text(encoding="utf-8").splitlines() if f.exists() else []

    def moved(self) -> list[str]:
        return [c for c in self.calls if "update-traffic" in c]

    @property
    def summary_text(self) -> str:
        return self.summary.read_text(encoding="utf-8") if self.summary.exists() else ""


def step_run(workflow: str, job: str, needle: str) -> str:
    wf = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    found = [s["run"] for s in wf["jobs"][job]["steps"] if needle in (s.get("name") or "")]
    assert len(found) == 1
    return found[0]


MOVE = step_run("rollback.yml", "rollback", "Move the traffic")


# (deploy.yml's own rollback step went with GitHub's deploy: skippy-core's cd.py rolls back, tested in core/tests/test_cd.py)

def test_blank_goes_back_one_revision_lists_them_and_checks_health(tmp_path):
    r = Rig(tmp_path, serving=name(5), REVISION="")
    done = r.run(MOVE)
    assert done.returncode == 0, done.stdout + done.stderr
    assert r.moved() == [f"gcloud run services update-traffic skippy-portal --region us-west1 --to-revisions {name(4)}=100"]
    summary = r.summary_text
    assert "### Revisions (newest first)" in summary and name(5) in summary and "**serving**" in summary
    assert f"- Asked for: `{name(4)}`" in summary and f"- Serving now: `{name(4)}`" in summary and "- /health: **ok**" in summary
    assert any("curl" in c and "http://stub.invalid/health" in c for c in r.calls)


def test_blank_after_a_failed_deploy_rolled_itself_back_does_not_go_to_the_broken_newest(tmp_path):
    r = Rig(tmp_path, serving=name(5), revs=revisions(6, newest_unready=True), REVISION="")
    done = r.run(MOVE)
    assert done.returncode == 0, done.stdout + done.stderr
    assert r.moved()[0].endswith(f"--to-revisions {name(4)}=100")             # not 6, the broken one


def test_a_named_revision_is_served(tmp_path):
    r = Rig(tmp_path, serving=name(5), REVISION=name(2))
    assert r.run(MOVE).returncode == 0
    assert r.moved()[0].endswith(f"--to-revisions {name(2)}=100")


def test_latest_sends_traffic_back_to_the_newest_revision(tmp_path):
    r = Rig(tmp_path, serving=name(3), REVISION="latest")
    assert r.run(MOVE).returncode == 0
    assert r.moved() == ["gcloud run services update-traffic skippy-portal --region us-west1 --to-latest"]


def test_asking_for_what_is_already_serving_moves_nothing(tmp_path):
    r = Rig(tmp_path, serving=name(5), REVISION=name(5))
    done = r.run(MOVE)
    assert done.returncode == 0 and r.moved() == []
    assert "already serving" in done.stdout


def test_with_nothing_older_it_fails_red_changes_nothing_and_still_shows_the_list(tmp_path):
    r = Rig(tmp_path, serving=name(1), revs=revisions(3), REVISION="")
    done = r.run(MOVE)
    assert done.returncode == 1
    assert r.moved() == [] and "Nothing was changed" in done.stdout
    assert "nothing earlier" in done.stderr and name(3) in r.summary_text


@pytest.mark.parametrize("typed", ["x; touch pwned", "$(touch pwned)", "`touch pwned`", "../../etc/passwd", "--help"])
def test_a_typed_revision_can_never_run_a_command_or_become_an_option(tmp_path, typed):
    r = Rig(tmp_path, serving=name(5), REVISION=typed)
    done = r.run(MOVE)
    assert done.returncode == 1, done.stdout + done.stderr
    assert r.moved() == []
    assert not (ROOT / "pwned").exists() and not (tmp_path / "pwned").exists()


def test_traffic_that_moved_but_a_health_check_that_fails_leaves_the_job_red_with_the_reason(tmp_path):
    r = Rig(tmp_path, serving=name(5), REVISION="", STUB_CURL_RC="22")
    done = r.run(MOVE)
    assert done.returncode == 1
    assert len(r.moved()) == 1 and "- /health: **FAILING**" in r.summary_text
    assert "::error::Traffic moved, but /health does not answer" in done.stdout
