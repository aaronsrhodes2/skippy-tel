"""The watchdog's plumbing: what main.tf ships is what startup.sh installs and the timer runs, diag.sh shows the
evidence, the backstop and the budget are wired, and nothing still points at the deleted bash script."""
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "gcp-games"


def text(path):
    return Path(path).read_text(encoding="utf-8")


TF, STARTUP, DIAG = text(INFRA / "main.tf"), text(INFRA / "startup.sh"), text(INFRA / "diag.sh")


def metadata_keys():
    block = re.search(r"metadata = \{(.*?)\n  \}", TF, re.S).group(1)
    return set(re.findall(r"^\s*([a-z][a-z0-9-]*)\s*=", block, re.M))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, INFRA / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(shutil.which("git") is None, reason="no git")
def test_no_fixture_is_swallowed_by_gitignore():
    # The repo ignores *.log: a fixture named that way passes on the machine that wrote it and is missing in CI.
    fixtures = ROOT / "portal" / "tests" / "fixtures" / "watchdog"
    files = sorted(fixtures.iterdir())
    assert len(files) >= 5
    for f in files:
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(f)], capture_output=True)
        if ignored.returncode == 128:
            pytest.skip("not a git checkout (an exported tree)")
        assert ignored.returncode == 1, f"{f.name} is matched by .gitignore"


# ── main.tf ships what startup.sh installs ───────────────────────────────────

def test_startup_installs_the_python_watchdog_that_main_tf_ships():
    assert 'idle-watchdog    = file("${path.module}/idle_watchdog.py")' in TF
    assert "meta idle-watchdog > /usr/local/bin/skippy-idle-watchdog" in STARTUP
    assert text(INFRA / "idle_watchdog.py").startswith("#!/usr/bin/env python3")
    assert not (INFRA / "idle-watchdog.sh").exists()                    # the bash version that never counted is gone


def test_the_unit_and_timer_are_the_ones_the_watchdog_assumes():
    iw = load("idle_watchdog_wiring", "idle_watchdog.py")
    assert "ExecStart=/usr/local/bin/skippy-idle-watchdog\n" in STARTUP          # no arguments: a plain check
    assert "OnBootSec=5min" in STARTUP and "OnUnitActiveSec=5min" in STARTUP
    assert iw.INTERVAL_MIN == 5                                                   # the 5 the idle clock counts in
    assert "chmod 755 /usr/local/bin/skippy-idle-watchdog" in STARTUP


def test_the_counters_are_armed_at_boot_before_the_timer_and_the_games():
    armed = STARTUP.index("skippy-idle-watchdog arm")
    assert STARTUP.index("meta idle-watchdog >") < armed < STARTUP.index("systemctl enable --now skippy-idle-watchdog.timer")
    assert armed < STARTUP.index("compose $PROFILES up -d")
    assert "WARNING: metadata idle-watchdog is not the Python watchdog" in STARTUP


def test_every_metadata_key_the_vm_scripts_fetch_is_shipped():
    fetched = set(re.findall(r"\bmeta ([a-z][a-z0-9-]*)\b", STARTUP))
    fetched |= set(re.findall(r'attributes/([a-z][a-z0-9-]*)"', STARTUP + DIAG))
    fetched -= {"games-env"}                                                       # uploaded by `games.sh env`, never by tofu
    missing = fetched - metadata_keys()
    assert not missing, f"startup.sh/diag.sh fetch metadata keys main.tf doesn't set: {missing}"
    assert {"idle-watchdog", "skippy-diag", "discord-notify", "max-hours", "idle-minutes"} <= metadata_keys()


def test_nothing_still_points_at_the_deleted_bash_watchdog():
    stale = []
    for base in ("infra", "scripts", "docs", ".github", "portal", "README.md", "CLAUDE.md", "Makefile"):
        path = ROOT / base
        files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
        for f in files:
            if f.suffix in (".sh", ".py", ".md", ".tf", ".yml", ".yaml", ".html", "") and "fixtures" not in f.parts \
                    and f.name != "test_watchdog_wiring.py" and f.name != "test_idle_watchdog.py":
                try:
                    if "idle-watchdog.sh" in f.read_text(encoding="utf-8"):
                        stale.append(str(f.relative_to(ROOT)))
                except UnicodeDecodeError:
                    pass
    assert not stale, f"still mention idle-watchdog.sh: {stale}"


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32",
                    reason="CI runs bash -n on these (a Windows PATH's bash may be WSL's, which can't read these paths)")
def test_the_shell_scripts_parse():
    for name in ("diag.sh", "startup.sh"):
        assert subprocess.run(["bash", "-n", str(INFRA / name)], capture_output=True).returncode == 0, name
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "games.sh")], capture_output=True).returncode == 0


# ── diag.sh: the evidence, on the live VM, without a reboot ──────────────────

def test_diag_shows_the_timer_the_journals_the_state_files_the_counters_and_the_report():
    block = DIAG[DIAG.index("── idle watchdog"):DIAG.index("── end idle watchdog")]
    for wanted in ("systemctl is-active skippy-idle-watchdog.timer",
                   "systemctl show skippy-idle-watchdog.service",
                   "journalctl -t skippy-idle -n 20 --no-pager",             # the watchdog's own lines
                   "journalctl -u skippy-idle-watchdog.service",             # the unit's: where "Invalid rule number" lands
                   "/run/skippy-games", "packets idle",
                   "iptables -w 5 -t mangle -L PREROUTING -v -x -n",
                   "attributes/idle-watchdog", "python3 \"$wd\" report"):
        assert wanted in block, wanted
    assert 'grep -q python' in block                        # never runs the old bash script as if it were the report
    assert "skippy-idle-watchdog report" not in block       # the installed copy on a running VM may still be the old one
    # This script arrives on bash's stdin: a command that reads stdin would swallow the rest of it.
    for ln in block.splitlines():
        runs_something = re.search(r"\b(systemctl|journalctl|iptables|python3) ", ln)
        if runs_something and not ln.lstrip().startswith("#") and (not ln.lstrip().startswith("echo") or "$(" in ln):
            assert "</dev/null" in ln, f"reads stdin: {ln.strip()}"
    assert "cat /srv/games/.env" not in block and "games-env" not in block          # no settings, no secrets
    assert DIAG.index("--- data disk") < DIAG.index("── idle watchdog") < DIAG.index("=== end ===")


def test_diag_sections_keep_the_portals_parsers_working():
    import diagnostics
    fake = ("=== skippy-diag t ===\n--- memory\n               total        used\nMem:           16002        14500\n"
            "--- data disk\n/dev/sda         59G   50G  9G  85% /srv/games/data\n"
            "--- idle watchdog: evidence (idle_watchdog.py report, run from metadata)\n"
            "PLAYERS valheim=1\n"
            'WATCHDOG verdict=active idle_min=0 limit=30 up_min=61 max_hours=8 unknown_min=0 reason="1 players"\n'
            "=== end ===\n")
    assert diagnostics.players_online(fake) == {"valheim": 1}
    assert diagnostics.resources(fake)["memory_pct"] == 91 and diagnostics.resources(fake)["disk_pct"] == 85


# ── the backstop: max-hours ──────────────────────────────────────────────────

def test_max_hours_is_set_by_terraform_default_8_and_not_ignored():
    variables = text(INFRA / "variables.tf")
    assert re.search(r'variable "max_hours" \{.*?default\s*=\s*8\b', variables, re.S)
    assert "max-hours = tostring(var.max_hours)" in TF
    ignored = re.search(r"ignore_changes = \[(.*?)\n    \]", TF, re.S).group(1)
    assert 'metadata["idle-minutes"]' in ignored                                     # the portal writes this one
    assert "max-hours" not in ignored                                                # nothing else does: Terraform owns it


def test_the_portal_reads_max_hours_from_the_vm_metadata():
    from gcp import VmStatus
    assert VmStatus(status="RUNNING", machine_type="m", external_ip=None).max_hours == "8"


# ── the cost alarm: budget.tf ────────────────────────────────────────────────

BUDGET = text(INFRA / "budget.tf")


def test_the_budget_exists_only_when_a_billing_account_is_given():
    variables = text(INFRA / "variables.tf")
    assert re.search(r'variable "billing_account" \{.*?default\s*=\s*""', variables, re.S)
    assert re.search(r'variable "budget_monthly_usd" \{.*?default\s*=\s*30\b', variables, re.S)
    assert 'budget_on = var.billing_account != ""' in BUDGET
    resources = re.findall(r'^(resource|data) "[^"]+" "[^"]+" \{\n(.*?)^\}', BUDGET, re.S | re.M)
    assert len(resources) >= 4
    for kind, body in resources:
        assert re.search(r"^\s+count\s*=\s*.*budget_on", body, re.M), f"an ungated {kind} would be created without a billing account"


def test_the_budget_alerts_at_50_90_and_100_percent_of_the_monthly_amount():
    assert re.findall(r"threshold_percent = ([0-9.]+)", BUDGET) == ["0.5", "0.9", "1.0"]
    assert "units         = tostring(var.budget_monthly_usd)" in BUDGET
    assert 'currency_code = "USD"' in BUDGET
    assert "billingbudgets.googleapis.com" in BUDGET
    assert "projects/${data.google_project.this[0].number}" in BUDGET                # the API wants the project NUMBER


def test_the_deploy_passes_the_billing_account_from_the_vault_and_docs_say_what_is_needed():
    deploy = text(ROOT / "core" / "cd" / "cd.py")                                 # skippy-core's deployer runs the Terraform now
    assert "TF_VAR_billing_account=" in deploy and "BILLING_ACCOUNT" in deploy
    for doc in ("docs/cicd.md", "docs/games/README.md"):
        body = text(ROOT / doc)
        assert "BILLING_ACCOUNT" in body and "billing.budgets" in body, doc
        assert "roles/billing.costsManager" in body, doc
    assert "max-hours" in text(ROOT / "docs" / "games" / "README.md")
    assert "idle_watchdog.py" in text(ROOT / "docs" / "games" / "README.md")


# ── the Discord heads-up ─────────────────────────────────────────────────────

def test_discord_notice_posts_to_running_games_only_and_leaves_the_boards_alone(monkeypatch):
    notify = load("discord_notify_wiring", "discord-notify.py")
    posts = []
    monkeypatch.setattr(notify, "load_env", lambda path=None: {
        "DISCORD_WEBHOOK_VALHEIM": "https://hooks.example/valheim", "DISCORD_WEBHOOK_FACTORIO": "https://hooks.example/factorio",
        "DISCORD_BOARD_FACTORIO": "123"})
    monkeypatch.setattr(notify, "meta", lambda path, default="": "factorio" if path == "attributes/games" else default)
    monkeypatch.setattr(notify, "call", lambda method, url, payload=None: posts.append((method, url, payload)) or {})
    assert notify.main(["skippy-discord", "notice", "off in", "15 minutes"]) == 0
    assert [(m, u) for m, u, _ in posts] == [("POST", "https://hooks.example/factorio")]      # no PATCH: the board stays
    assert posts[0][2]["content"] == "off in 15 minutes"


def test_a_failing_webhook_is_reported_not_raised(monkeypatch, capsys):
    notify = load("discord_notify_wiring2", "discord-notify.py")
    monkeypatch.setattr(notify, "load_env", lambda path=None: {"DISCORD_WEBHOOK_VALHEIM": "https://hooks.example/v"})
    monkeypatch.setattr(notify, "meta", lambda path, default="": "valheim" if path == "attributes/games" else default)

    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(notify, "call", boom)
    assert notify.main(["skippy-discord", "notice", "x"]) == 1
    assert "connection refused" in capsys.readouterr().err
