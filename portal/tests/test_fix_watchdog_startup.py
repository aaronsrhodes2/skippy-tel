"""Boot-script fixes around FACTORIO_VERSION.

`games/docker-compose.yml` uses `image: factoriotools/factorio:${FACTORIO_VERSION:-stable}`, and the admin card accepts
any `x.y.z`. A well-formed version that has no Docker Hub tag (a typo: 2.0.777) made `compose pull` fail (survived: "pull
failed; starting with cached images") and then `compose up -d` fail for EVERY game in the command, creating no container
at all, and `set -e` ended the boot script before EverQuest, the "started:" line and the Discord "online" post: the VM
sat there with nothing running and no message about it.

  * portal/catalog.py: the check accepts ASCII digits only and a sane length (Unicode digits and 40-digit numbers passed).
  * infra/gcp-games/startup.sh: a failing `compose up -d` is retried game by game, so one bad image only costs its own
    game, and the games that did not start are printed.
The boot script's tail is run for real under bash here, with `docker` faked to behave the way the real compose did when
it was reproduced (a bad image in the command: exit 1 and nothing created, for the good games too).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from catalog import _factorio_version

ROOT = Path(__file__).resolve().parents[2]
STARTUP = ROOT / "infra" / "gcp-games" / "startup.sh"
MARKER = "# >>> games"


# ── the tag check ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,normal", [("stable", "stable"), (" LATEST ", "latest"), ("2.0.77", "2.0.77"),
                                        ("1.1.110", "1.1.110"), ("0.17.79", "0.17.79"), (" 2.0.77\n", "2.0.77")])
def test_the_tags_that_exist_are_accepted_and_normalised(raw, normal):
    assert _factorio_version(raw) == (normal, None)


@pytest.mark.parametrize("raw", ["٢.٠.٧٧",                                           # Arabic-Indic digits: \d matches them
                                 "２.０.７７",                                        # full-width digits
                                 "2.0.77٣",                                    # one Unicode digit among ASCII ones
                                 "2.0.77777", "12345.0.1", "1.12345.1",             # not a Factorio version's shape
                                 "1" * 40 + ".0.1", "2." + "0" * 40 + ".77",        # absurdly long
                                 "2.0", "2.0.77.1", "v2.0.77", "2.0.77-alpine", "2.0.x", "", "  ", "stable​"])
def test_what_cannot_be_a_tag_is_refused(raw):
    value, problem = _factorio_version(raw)
    assert problem and "exact version" in problem and value == raw


# ── the boot script's start-the-games section, run for real ──────────────────

def _bash_can_run_it() -> bool:
    bash = shutil.which("bash")
    if not bash:
        return False
    try:
        out = subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "ok"


needs_bash = pytest.mark.skipif(not _bash_can_run_it(), reason="needs bash")


def games_section() -> str:
    text = STARTUP.read_text(encoding="utf-8")
    assert text.count(MARKER) == 1, "startup.sh needs one marker where the tested tail starts"
    return text[text.index(MARKER):]


PRELUDE = r"""
set -euo pipefail
ROOT="$FAKE_DIR/root"; DATA="$FAKE_DIR/data"
ALL_PROFILES="--profile valheim --profile factorio --profile satisfactory --profile dragonwilds"
compose() { docker compose --project-directory "$ROOT" "$@"; }
meta() {
  case "$1" in
    games)            echo "$FAKE_GAMES" ;;
    everquest-script) printf '#!/bin/bash\necho "skippy-everquest $*" >> "$FAKE_LOG"\n' ;;
    *)                echo "# $1" ;;
  esac
}
python3() { echo "python3 $*" >> "$FAKE_LOG"; }
# What the real compose did when reproduced: a service whose image cannot be pulled or found makes `up -d` exit 1 and
# create NOTHING, not even the services of the same command whose images are there.
docker() {
  echo "docker $*" >> "$FAKE_LOG"
  [ "$1" = compose ] || return 0
  shift 3
  local profiles=() action=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile) profiles+=("$2"); shift 2 ;;
      pull|up|down) action="$1"; break ;;
      *) shift ;;
    esac
  done
  local p
  if [ "$action" = pull ] && [ -n "${FAKE_PULL_FAIL:-}" ]; then
    echo "failed to resolve reference: dial tcp: lookup registry-1.docker.io: no such host" >&2
    return 1
  fi
  for p in ${profiles[@]+"${profiles[@]}"}; do
    if [ "$p" = "${FAKE_BAD:-}" ] && [ "$action" != down ]; then
      echo "Error response from daemon: manifest for factoriotools/factorio:2.0.777 not found: manifest unknown" >&2
      return 1
    fi
  done
  if [ "$action" = up ]; then for p in "${profiles[@]}"; do echo "$p" >> "$FAKE_UP"; done; fi
  return 0
}
"""


def run_boot(tmp_path, games, bad="", pull_fails=False, expect_ok=True):
    """The tail of startup.sh (from the marker to the end) with everything on the machine faked. Returns
    (exit status, stdout+stderr, the fake tools' call log lines, the games whose containers were created)."""
    tools = tmp_path / "bin"
    (tmp_path / "root").mkdir(exist_ok=True)
    tools.mkdir(exist_ok=True)
    (tools / "skippy-discord").write_text('#!/bin/bash\necho "skippy-discord $*" >> "$FAKE_LOG"\n', encoding="utf-8")
    (tools / "skippy-discord").chmod(0o755)
    log, up = tmp_path / "calls.log", tmp_path / "up.log"
    log.write_text("", encoding="utf-8")
    up.write_text("", encoding="utf-8")
    section = games_section().replace("/usr/local/bin/", tools.as_posix() + "/")
    env = {**os.environ, "FAKE_DIR": tmp_path.as_posix(), "FAKE_GAMES": games, "FAKE_BAD": bad,
           "FAKE_LOG": log.as_posix(), "FAKE_UP": up.as_posix(), "FAKE_PULL_FAIL": "1" if pull_fails else ""}
    done = subprocess.run([shutil.which("bash"), "-c", PRELUDE + section], env=env, capture_output=True, text=True, timeout=120)
    calls = log.read_text(encoding="utf-8").splitlines()
    created = up.read_text(encoding="utf-8").split()
    if expect_ok:
        assert done.returncode == 0, done.stdout + done.stderr
    return done.returncode, done.stdout + done.stderr, calls, created


def compose_ups(calls):
    return [c for c in calls if c.startswith("docker compose") and " up " in c + " "]


@needs_bash
def test_a_healthy_boot_is_still_one_compose_command_and_the_same_started_line(tmp_path):
    _, out, calls, created = run_boot(tmp_path, "valheim everquest factorio")
    assert len(compose_ups(calls)) == 1 and "--profile valheim" in compose_ups(calls)[0] and "--profile factorio" in compose_ups(calls)[0]
    assert sorted(created) == ["factorio", "valheim"]
    assert "started: valheim everquest factorio" in out.splitlines()               # the line the logs have always had
    assert "STARTUP FAILED" not in out
    assert "skippy-everquest start" in calls and "skippy-discord online" in calls


@needs_bash
def test_a_game_whose_image_does_not_exist_only_costs_its_own_game(tmp_path):
    code, out, calls, created = run_boot(tmp_path, "valheim factorio satisfactory", bad="factorio")
    assert code == 0
    assert sorted(created) == ["satisfactory", "valheim"], "the other games must still be started"
    failed = [ln for ln in out.splitlines() if "STARTUP FAILED" in ln]
    assert len(failed) == 1 and "factorio" in failed[0] and "valheim" not in failed[0]
    assert "started: valheim satisfactory" in out.splitlines()                      # not a lie about factorio
    assert "manifest for factoriotools/factorio:2.0.777 not found" in out           # compose's own words are kept
    assert "skippy-discord online" in calls                                         # the games that are up are announced


@needs_bash
def test_everquest_and_the_announcement_still_come_after_a_failed_game(tmp_path):
    code, out, calls, created = run_boot(tmp_path, "factorio everquest", bad="factorio")
    assert code == 0 and created == []
    assert "skippy-everquest start" in calls and "skippy-discord online" in calls
    assert "started: everquest" in out.splitlines() and "STARTUP FAILED" in out


@needs_bash
def test_when_nothing_could_be_started_it_says_so_and_does_not_announce_a_game_that_is_not_there(tmp_path):
    code, out, calls, created = run_boot(tmp_path, "factorio", bad="factorio")
    assert code == 0 and created == []
    assert "STARTUP FAILED" in out and "factorio" in out
    assert "skippy-discord online" not in calls and "skippy-everquest start" not in calls
    assert not any(ln.startswith("started:") for ln in out.splitlines())


@needs_bash
def test_a_failed_pull_alone_still_starts_from_the_cached_images(tmp_path):
    # `docker compose pull` failing (no network) is not a failed start: the cached images are used, as before.
    code, out, calls, created = run_boot(tmp_path, "valheim factorio", pull_fails=True)
    assert code == 0 and sorted(created) == ["factorio", "valheim"]
    assert "pull failed; starting with cached images" in out and "STARTUP FAILED" not in out
    assert len(compose_ups(calls)) == 1


def test_the_section_keeps_the_order_the_other_tests_rely_on():
    text = STARTUP.read_text(encoding="utf-8")
    assert text.index("skippy-game-backup backup") < text.index("compose $PROFILES up -d")
    assert text.index("compose $PROFILES up -d") > text.index(MARKER)
    assert text.index("compose $ALL_PROFILES down") > text.index(MARKER)              # everything is stopped first, in the tested tail
