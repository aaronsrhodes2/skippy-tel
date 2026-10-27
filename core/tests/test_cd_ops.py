"""skippy-core's deployer (core/cd/cd.py) doing what the `rollback`, `ops` and `domains` GitHub workflows did: the rollback rule, the
operator commands run in a container with this box's Google identity, and the installed copy updating itself from the tested commit.
Docker, gcloud and the network are faked."""
import json
import subprocess

import pytest

pytest.importorskip("fcntl")

from core_testlib import load  # noqa: E402

cd_mod = load("cd/cd.py", "core_cd_ops")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def revision(n, ready=True):
    return {"metadata": {"name": f"skippy-portal-{n:05d}-abc", "creationTimestamp": f"2026-10-2{n}T10:00:00Z"},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
            "spec": {"containers": [{"image": f"us-west1-docker.pkg.dev/skippy-tel/skippy/portal:{'a' * 40}"}]}}


class RollbackCD(cd_mod.CD):
    """gcloud is a script: `serving` is what describe answers, `revisions` what list answers; every traffic move is recorded."""

    def __init__(self, s, serving, revisions, health=200):
        super().__init__(s, log=lambda m: None)
        self.cfg = json.loads((cd_mod.HERE / "deploy.json").read_text())
        self.serving_name, self.revisions, self.moves, self.health = serving, revisions, [], health
        cd_mod.oidc.token = lambda: None

    def gcloud(self, *args, cwd=None, timeout=900, quiet=False):
        if args[:3] == ("run", "services", "describe") and "status.traffic[0].revisionName" in " ".join(args):
            return self.serving_name + "\n"
        if args[:3] == ("run", "services", "describe"):
            return "https://portal.example\n"
        if args[:3] == ("run", "revisions", "list"):
            return json.dumps(self.revisions)
        if args[:3] == ("run", "services", "update-traffic"):
            self.moves.append([a for a in args if a.startswith("--to")])
            return ""
        raise AssertionError(args)

    def http(self, method, url, body=None, follow=True, timeout=30, tries=1, headers=None):
        return self.health, ""


def settings(tmp_path, **more):
    return {**cd_mod.DEFAULTS, "CD_STATE": str(tmp_path / "cd"), "CI_LOCAL": str(tmp_path / "ci"), **more}


def test_a_blank_rollback_steps_back_from_the_serving_revision_never_onto_the_broken_newest(tmp_path):
    revs = [revision(3, ready=False), revision(2), revision(1)]                    # a failed deploy: the newest is broken
    b = RollbackCD(settings(tmp_path), serving="skippy-portal-00002-abc", revisions=revs)
    assert b.rollback("") == "skippy-portal-00001-abc"
    assert b.moves == [["--to-revisions=skippy-portal-00001-abc=100"]]


def test_latest_undoes_a_rollback_and_a_named_revision_is_served(tmp_path):
    revs = [revision(3), revision(2), revision(1)]
    b = RollbackCD(settings(tmp_path), serving="skippy-portal-00001-abc", revisions=revs)
    assert b.rollback("latest") == "latest" and b.moves == [["--to-latest"]]
    assert b.rollback("skippy-portal-00002-abc") == "skippy-portal-00002-abc"
    assert b.moves[-1] == ["--to-revisions=skippy-portal-00002-abc=100"]


def test_nothing_safe_to_go_back_to_changes_nothing_and_says_so(tmp_path):
    b = RollbackCD(settings(tmp_path), serving="skippy-portal-00001-abc", revisions=[revision(1)])
    with pytest.raises(cd_mod.Failed, match="nothing was changed"):
        b.rollback("")
    with pytest.raises(cd_mod.Failed, match="nothing was changed"):
        b.rollback("rm -rf /")                                                    # a typed value is a name, or it is refused
    assert b.moves == []


def test_a_rollback_that_leaves_the_portal_unhealthy_says_it_is_not_the_revision(tmp_path):
    b = RollbackCD(settings(tmp_path), serving="skippy-portal-00002-abc", revisions=[revision(2), revision(1)], health=503)
    with pytest.raises(cd_mod.Failed, match="not the revision"):
        b.rollback("")


class OpsCD(cd_mod.CD):
    """The container run is captured, not run; the checkout of main is a throwaway folder."""

    def __init__(self, s):
        super().__init__(s, log=lambda m: None)
        self.cfg = json.loads((cd_mod.HERE / "deploy.json").read_text())
        self.ran = []
        cd_mod.oidc.token = lambda: None

    def checkout_main(self):
        work = self.home / "main"
        (work / "src" / "portal").mkdir(parents=True, exist_ok=True)
        (work / "src" / "portal" / "requirements.txt").write_text("flask\n")
        return work

    def ops_image(self, src):
        return "skippy-ops:test"

    def gcloud(self, *args, cwd=None, timeout=900, quiet=False):
        self.ran.append(("gcloud", args))
        return "an-id-token\n"


@pytest.fixture
def ops(tmp_path, monkeypatch):
    cd = OpsCD(settings(tmp_path))

    class Done:
        returncode = 0
    monkeypatch.setattr(cd_mod.subprocess, "run", lambda cmd, *a, **k: (cd.ran.append(("docker", cmd)) or Done()))
    return cd


def docker_cmd(cd):
    (_, cmd), = [r for r in cd.ran if r[0] == "docker"]
    return cmd


def test_ops_runs_remote_py_in_a_container_with_this_boxs_identity_and_the_args_as_separate_items(ops):
    assert ops.ops(["restart-game", "valheim; rm -rf /"]) == 0
    cmd = docker_cmd(ops)
    assert cmd[-3:] == ["scripts/remote.py", "restart-game", "valheim; rm -rf /"]    # separate argv items, never a shell line
    assert f"GOOGLE_APPLICATION_CREDENTIALS={cd_mod.oidc.CRED_FILE}" in cmd and "skippy-ops:test" in cmd
    assert not any(a.startswith("OPS_ID_TOKEN") for a in cmd)                        # only home and discord-setup get a token


def test_home_and_discord_setup_get_an_id_token_for_the_portals_ops_audience(ops, monkeypatch):
    asked = []
    monkeypatch.setattr(cd_mod.oidc, "id_token", lambda audience: asked.append(audience) or "an-id-token")
    ops.ops(["home"])
    assert asked == ["skippy-portal-ops"] and "OPS_ID_TOKEN=an-id-token" in docker_cmd(ops)


def test_the_id_token_is_made_the_way_githubs_auth_step_made_it(tmp_path):
    """Our signed token is exchanged at Google's STS for a federated one, which asks IAM Credentials for the deployer's ID token."""
    token_file = tmp_path / "token.jwt"
    token_file.write_text("our.signed.jwt")
    calls = []

    class Resp:
        def __init__(self, body):
            self.body = json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    def urlopen(req, timeout):
        body = json.loads(req.data)
        calls.append((req.full_url, body, req.get_header("Authorization")))
        if "sts.googleapis.com" in req.full_url:
            return Resp({"access_token": "federated"})
        return Resp({"token": "the-id-token"})
    assert cd_mod.oidc.id_token("skippy-portal-ops", token_file, urlopen=urlopen) == "the-id-token"
    (sts_url, sts, _), (iam_url, iam, auth) = calls
    assert sts["subjectToken"] == "our.signed.jwt" and sts["audience"].endswith("/providers/skippy-core")
    assert iam_url.endswith(f"/serviceAccounts/{cd_mod.oidc.DEPLOYER}:generateIdToken")
    assert iam == {"audience": "skippy-portal-ops", "includeEmail": True} and auth == "Bearer federated"


def test_domains_mints_a_token_with_the_siteverification_scope_and_runs_domains_py(ops):
    ops.domains()
    cmd = docker_cmd(ops)
    code = cmd[-1]
    assert cmd[-2] == "-c" and "siteverification" in code and "tools/domains.py" in code and "TOKEN" in code
    compile(code, "domains_run", "exec")                                              # the snippet is valid Python


@pytest.fixture
def tested_commit(tmp_path):
    """A mirror whose newest commit holds new versions of the deployer's files, and an installed folder holding old ones."""
    work = tmp_path / "w"
    git(tmp_path, "init", "--quiet", "-b", "main", str(work))
    for k, v in (("user.name", "T"), ("user.email", "t@example.com")):
        git(work, "config", k, v)
    (work / "core" / "cd").mkdir(parents=True)
    for name in cd_mod.CD.SELF_FILES:
        (work / "core" / "cd" / name).write_text(f"new {name}\n")
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "x")
    ci = tmp_path / "ci" / "skippy-tel"
    ci.mkdir(parents=True)
    git(tmp_path, "clone", "--quiet", "--mirror", str(work), str(ci / "mirror.git"))
    installed = tmp_path / "opt" / "cd"
    installed.mkdir(parents=True)
    for name in cd_mod.CD.SELF_FILES:
        (installed / name).write_text("old\n")
    return git(work, "rev-parse", "HEAD"), installed


def test_the_installed_deployer_takes_the_tested_commits_version_and_starts_over(tmp_path, tested_commit, monkeypatch):
    sha, installed = tested_commit
    execs = []
    monkeypatch.setattr(cd_mod.os, "execv", lambda exe, argv: execs.append(argv))
    monkeypatch.delenv("CD_SELF_UPDATED", raising=False)
    monkeypatch.setattr(cd_mod.CD, "INSTALLED", installed)
    cd = cd_mod.CD(settings(tmp_path), log=lambda m: None)
    cd.self_update(sha, here=installed)
    assert [(installed / n).read_text() for n in cd_mod.CD.SELF_FILES] == [f"new {n}\n" for n in cd_mod.CD.SELF_FILES]
    assert len(execs) == 1 and execs[0][-2:] == [str(installed / "cd.py"), "run"]
    assert cd_mod.os.environ["CD_SELF_UPDATED"] == "1"                                # it starts over once, never in a loop
    monkeypatch.delenv("CD_SELF_UPDATED")
    execs.clear()
    cd.self_update(sha, here=installed)                                               # already current: nothing to do
    assert execs == []


def test_a_copy_that_is_not_the_installed_one_is_never_overwritten(tmp_path, tested_commit, monkeypatch):
    sha, installed = tested_commit
    monkeypatch.delenv("CD_SELF_UPDATED", raising=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cd_mod.CD(settings(tmp_path), log=lambda m: None).self_update(sha, here=elsewhere)
    assert list(elsewhere.iterdir()) == [] and (installed / "cd.py").read_text() == "old\n"


def test_the_checkout_of_main_for_ops_is_a_real_working_tree(tmp_path, tested_commit):
    sha, _ = tested_commit
    cd = cd_mod.CD(settings(tmp_path), log=lambda m: None)
    work = cd.checkout_main()
    assert (work / "src" / "core" / "cd" / "cd.py").read_text() == "new cd.py\n"
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=work / "src", capture_output=True, text=True).stdout.strip() == sha


class BootstrapCD(cd_mod.CD):
    def __init__(self, s, tmp, code=0):
        super().__init__(s, log=lambda m: None)
        self.ran, self.code, self.tmp = [], code, tmp
        cd_mod.oidc.token = lambda: None

    def checkout_main(self):
        work = self.tmp / "work"
        (work / "src").mkdir(parents=True)
        return work

    def terraform(self, cmd):
        self.ran.append(cmd)
        return self.code if "init" in cmd else 0


def test_bootstrap_plans_by_default_applies_only_when_typed_and_signs_in_as_skippy_bootstrap(tmp_path):
    plan = BootstrapCD(settings(tmp_path), tmp_path)
    assert plan.bootstrap() == 0
    init, last = plan.ran
    assert "init" in init and "-backend-config=bucket=skippy-tel-tfstate" in init
    assert "plan" in last and "apply" not in last and "-auto-approve" not in last          # nothing changes without the word
    assert "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT=skippy-bootstrap@skippy-tel.iam.gserviceaccount.com" in last
    assert "/src/infra/bootstrap" in last and "TF_IN_AUTOMATION=1" in last
    assert not (tmp_path / "work").exists()                                                  # the checkout is cleaned up
    go = BootstrapCD(settings(tmp_path), tmp_path)
    assert go.bootstrap(apply=True) == 0 and "apply" in go.ran[-1] and "-auto-approve" in go.ran[-1]


def test_a_failed_init_stops_before_any_plan_or_apply(tmp_path):
    cd = BootstrapCD(settings(tmp_path), tmp_path, code=3)
    assert cd.bootstrap(apply=True) == 3
    assert len(cd.ran) == 1 and "init" in cd.ran[0]


def test_the_bootstrap_command_is_plan_unless_apply_is_typed(monkeypatch):
    seen = []
    monkeypatch.setattr(cd_mod.CD, "bootstrap", lambda self, apply=False: seen.append(apply) or 0)
    assert cd_mod.main(["bootstrap"]) == 0 and cd_mod.main(["bootstrap", "apply"]) == 0 and cd_mod.main(["bootstrap", "please"]) == 0
    assert seen == [False, True, False]
