"""infra/gcp-games/game_backup.py (flagged backup targets per game) and the ops-agent commands that drive it."""
import importlib.util
import io
import re
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra" / "gcp-games"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, INFRA / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gb = load("game_backup", "game_backup.py")
agent = load("ops_agent", "ops_agent.py")
T0 = datetime(2026, 9, 30, 6, 0, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def data(tmp_path):
    """A data disk with a Valheim world on it."""
    root = tmp_path / "data"
    world = root / "valheim" / "config" / "worlds_local" / "Skippy"
    world.mkdir(parents=True)
    (world / "_main.1.db2").write_bytes(b"day one " * 100)
    (world / "_main.1.fwl2").write_bytes(b"meta")
    (root / "valheim" / "config" / "adminlist.txt").write_text("76561198000000000\n")
    (root / "valheim" / "server").mkdir()
    (root / "valheim" / "server" / "valheim_server.x86_64").write_bytes(b"the game install: never archived")
    return root


def names(tar_path):
    with tarfile.open(tar_path) as tar:
        return sorted(m.name for m in tar.getmembers() if m.isfile())


def test_an_archive_holds_only_the_flagged_files(data):
    line = gb.backup(data, "valheim", at(0))
    (archive,) = gb.archives(data, "valheim")
    assert archive.name in line
    assert names(archive) == ["valheim/config/adminlist.txt", "valheim/config/worlds_local/Skippy/_main.1.db2",
                              "valheim/config/worlds_local/Skippy/_main.1.fwl2"]        # not the 'server' install
    assert "not there yet: valheim/config/bannedlist.txt" in line                       # a missing target is named, not fatal


def test_nothing_is_archived_twice_but_a_change_gets_a_new_archive(data):
    gb.backup(data, "valheim", at(0))
    assert "unchanged since" in gb.backup(data, "valheim", at(5))
    assert len(gb.archives(data, "valheim")) == 1
    (data / "valheim/config/worlds_local/Skippy/_main.1.db2").write_bytes(b"night two, a bad night " * 100)
    gb.backup(data, "valheim", at(10))
    assert len(gb.archives(data, "valheim")) == 2


def test_only_the_newest_are_kept(data, monkeypatch):
    monkeypatch.setattr(gb, "DEFAULT_KEEP", 3)
    for i in range(6):
        (data / "valheim/config/adminlist.txt").write_text(f"change {i}\n" * (i + 1))
        gb.backup(data, "valheim", at(i))
    kept = gb.archives(data, "valheim")
    assert [p.name[:16] for p in kept] == [at(i).strftime("%Y%m%dT%H%M%SZ") for i in (3, 4, 5)]


def test_a_game_with_no_files_yet_or_no_targets(data):
    assert "none of its files exist yet" in gb.backup(data, "factorio", at(0))
    assert "no backup targets" in gb.backup(data, "minecraft", at(0))
    assert not (data / "backups" / "factorio").exists()


def test_it_will_not_fill_the_disk(data, monkeypatch):
    monkeypatch.setattr(gb.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    assert "SKIPPED" in gb.backup(data, "valheim", at(0))
    assert gb.archives(data, "valheim") == []


def test_restore_puts_the_start_of_session_back_and_sets_the_bad_day_aside(data):
    gb.backup(data, "valheim", at(0))
    world = data / "valheim/config/worlds_local/Skippy"
    (world / "_main.1.db2").write_bytes(b"a bad day: everything burned")
    (data / "valheim/config/worlds_local/Griefer.fwl").write_bytes(b"made after the backup")
    out = gb.restore(data, "valheim", "latest", at(60))
    assert (world / "_main.1.db2").read_bytes() == b"day one " * 100
    assert not (data / "valheim/config/worlds_local/Griefer.fwl").exists()          # the world folder is exactly as archived
    aside = data / "backups/valheim/_replaced/20260930T070000Z"
    assert (aside / "valheim/config/worlds_local/Skippy/_main.1.db2").read_bytes() == b"a bad day: everything burned"
    assert (aside / "valheim/config/worlds_local/Griefer.fwl").exists()              # nothing deleted
    assert "is back in place" in out
    assert (data / "valheim/server/valheim_server.x86_64").exists()                  # the install is never touched


def test_restore_by_name_prefix_and_bad_names(data):
    gb.backup(data, "valheim", at(0))
    (data / "valheim/config/adminlist.txt").write_text("x" * 50)
    gb.backup(data, "valheim", at(10))
    first, second = gb.archives(data, "valheim")
    assert gb.restore(data, "valheim", first.name, at(20)) and (data / "valheim/config/adminlist.txt").read_text() == "76561198000000000\n"
    gb.restore(data, "valheim", first.name[:16], at(30))                                # a unique prefix is enough
    for bad in ("../x", "a/b", "", "20260930", "nope"):                                # ambiguous, unknown or unsafe
        with pytest.raises(ValueError):
            gb.restore(data, "valheim", bad, at(40))
    with pytest.raises(ValueError):
        gb.restore(data, "everquest", "latest", at(40))                                # nothing archived for it


def make_archive(data, game, members):
    """An archive of chosen members (name, bytes|None for a symlink) planted where restore looks."""
    folder = data / "backups" / game
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "20260930T050000Z-deadbeef.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, content in members:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return path


@pytest.mark.parametrize("evil", [
    [("valheim/config/adminlist.txt", b"ok"), ("../escape.txt", b"x")],
    [("valheim/config/adminlist.txt", b"ok"), ("/etc/cron.d/pwn", b"x")],
    [("valheim/config/adminlist.txt", b"ok"), ("valheim/server/valheim_server.x86_64", b"swapped binary")],
    [("valheim/config/worlds_local/link", None)],
    [("valheim/config/worlds_local/../../../escape", b"x")],
])
def test_a_hostile_archive_is_refused_before_anything_moves(data, evil):
    make_archive(data, "valheim", evil)
    before = sorted(str(p.relative_to(data)) for p in data.rglob("*") if "backups" not in p.parts)
    with pytest.raises(ValueError, match="refusing"):
        gb.restore(data, "valheim", "latest", at(0))
    assert sorted(str(p.relative_to(data)) for p in data.rglob("*") if "backups" not in p.parts) == before
    assert not (data / "backups/valheim/_replaced").exists() and not (data.parent / "escape.txt").exists()


def test_a_restore_that_fails_part_way_puts_everything_back(data, monkeypatch):
    gb.backup(data, "valheim", at(0))
    (data / "valheim/config/worlds_local/Skippy/_main.1.db2").write_bytes(b"today's world")
    calls = []

    def flaky(tar, member, dest):
        calls.append(member.name)
        if len(calls) == 2:
            raise OSError("disk full")
        real(tar, member, dest)

    real = gb._extract
    monkeypatch.setattr(gb, "_extract", flaky)
    with pytest.raises(OSError):
        gb.restore(data, "valheim", "latest", at(1))
    assert (data / "valheim/config/worlds_local/Skippy/_main.1.db2").read_bytes() == b"today's world"
    assert (data / "valheim/config/adminlist.txt").read_text() == "76561198000000000\n"


def test_set_aside_copies_are_never_deleted_by_the_tool_and_list_says_how_much_room_they_take(data):
    gb.backup(data, "valheim", at(0))
    for i in range(5):
        gb.restore(data, "valheim", "latest", at(10 + i))
    assert len(list((data / "backups/valheim/_replaced").iterdir())) == 5                # "never deleted" is true
    assert "set aside by restores (not deleted automatically): 5 copy(ies)" in gb.listing(data, "valheim")


def test_a_failure_while_setting_files_aside_never_touches_the_ones_not_yet_moved(data, monkeypatch):
    """The review's data-loss case: the second move fails (disk full). The target still in place is the live original."""
    gb.backup(data, "valheim", at(0))
    (data / "valheim/config/worlds_local/Skippy/_main.1.db2").write_bytes(b"today's world")
    (data / "valheim/config/adminlist.txt").write_text("today's admins\n")
    real_move, calls = gb.shutil.move, []

    def failing_move(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real_move(src, dst)

    monkeypatch.setattr(gb.shutil, "move", failing_move)
    with pytest.raises(OSError):
        gb.restore(data, "valheim", "latest", at(1))
    monkeypatch.undo()
    assert (data / "valheim/config/worlds_local/Skippy/_main.1.db2").read_bytes() == b"today's world"   # the moved one came back
    assert (data / "valheim/config/adminlist.txt").read_text() == "today's admins\n"                    # the never-moved one is intact


def test_a_failure_making_the_set_aside_folder_touches_nothing(data, monkeypatch):
    gb.backup(data, "valheim", at(0))
    (data / "valheim/config/adminlist.txt").write_text("today's admins\n")
    real_mkdir = gb.Path.mkdir

    def no_room(self, *a, **k):
        if "_replaced" in self.parts:
            raise OSError(28, "No space left on device")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(gb.Path, "mkdir", no_room)
    with pytest.raises(OSError):
        gb.restore(data, "valheim", "latest", at(1))
    monkeypatch.undo()
    assert (data / "valheim/config/adminlist.txt").read_text() == "today's admins\n"
    assert (data / "valheim/config/worlds_local/Skippy/_main.1.db2").read_bytes() == b"day one " * 100


def test_check_says_yes_or_no_without_changing_anything(data, capsys):
    gb.backup(data, "valheim", at(0))
    (data / "valheim/config/adminlist.txt").write_text("today's admins\n")
    before = sorted(str(p) for p in data.rglob("*"))
    assert "is fine to restore" in gb.check(data, "valheim", "latest")
    assert sorted(str(p) for p in data.rglob("*")) == before and (data / "valheim/config/adminlist.txt").read_text() == "today's admins\n"
    assert gb.main(["--data", str(data), "check", "valheim", "latest"]) == 0
    assert gb.main(["--data", str(data), "check", "valheim", "nope"]) == 1
    make_archive(data, "factorio", [("valheim/config/adminlist.txt", b"wrong game's file")])
    assert gb.main(["--data", str(data), "check", "factorio", "latest"]) == 1            # it isn't one of factorio's targets
    assert "refusing" in capsys.readouterr().err


def test_listing_names_the_targets_and_the_archives(data):
    gb.backup(data, "valheim", at(0))
    text = gb.listing(data, "valheim")
    assert text.startswith("valheim: 1 archive(s); protects valheim/config/worlds_local")
    assert re.search(r"20260930T060000Z-[0-9a-f]{8}\.tar\.gz +2026-09-30 06:00 UTC", text)
    assert "everquest: 0 archive(s)" in gb.listing(data)


def test_the_command_line_survives_one_games_failure(data, monkeypatch, capsys):
    real = gb.backup
    monkeypatch.setattr(gb, "backup", lambda d, g, now=None: (_ for _ in ()).throw(OSError("boom")) if g == "factorio" else real(d, g))
    assert gb.main(["--data", str(data), "backup", "factorio", "valheim"]) == 1
    captured = capsys.readouterr()
    assert "backup factorio: FAILED: boom" in captured.err and "backup valheim:" in captured.out
    assert gb.main(["--data", str(data), "restore", "valheim", "nope"]) == 1
    assert gb.main(["--data", str(data), "list"]) == 0


# ── the flag list itself ───────────────────────────────────────────────────────

def test_every_game_we_run_has_backup_targets_under_its_own_folder():
    compose = (INFRA.parents[1] / "games" / "docker-compose.yml").read_text(encoding="utf-8")
    games = set(re.findall(r"profiles: \[(\w+)\]", compose)) | {"everquest"}         # EverQuest is its own compose project
    assert games <= set(gb.TARGETS), f"add backup targets for {games - set(gb.TARGETS)}"
    for game, targets in gb.TARGETS.items():
        assert targets and all(t.startswith(f"{game}/") and ".." not in t and not t.startswith("/") for t in targets), game
    for game in games - {"everquest"}:
        assert f"${{DATA_DIR:-./data}}/{game}" in compose, f"{game}'s data isn't mounted from the data disk"
    from catalog import GAMES  # noqa: PLC0415
    assert set(GAMES) == set(gb.TARGETS)                                             # the portal's games and the flagged games agree


def test_backups_run_at_boot_before_the_games_start_and_are_shipped_in_metadata():
    startup = (INFRA / "startup.sh").read_text(encoding="utf-8")
    assert startup.index("skippy-game-backup backup") < startup.index("compose $PROFILES up -d")
    assert startup.index("compose $ALL_PROFILES down") < startup.index("skippy-game-backup backup")   # containers are stopped first
    assert 'game-backup      = file("${path.module}/game_backup.py")' in (INFRA / "main.tf").read_text(encoding="utf-8")


# ── ops agent: backups / restore-game ─────────────────────────────────────────

class Recorder:
    """Stands in for the agent's run() and run_rc(): records every command, answers `docker ps`, and can fail
    the `check` or `restore` step of the backup tool with a non-zero exit code."""

    def __init__(self, up=("valheim",), fail_check=False, fail_restore=False):
        self.calls, self.up, self.fail_check, self.fail_restore = [], up, fail_check, fail_restore

    def __call__(self, *argv, timeout=60):
        self.calls.append(argv[:3] if argv[0] == "docker" else argv[:4])
        return "\n".join(self.up) if argv[:3] == ("docker", "ps", "--format") else "ok"

    def rc(self, *argv, timeout=60):
        self.calls.append(argv[:4])
        step = argv[2]
        if (step == "check" and self.fail_check) or (step == "restore" and self.fail_restore):
            return 1, f"{step} FAILED: reasons"
        return 0, f"{step} ok"


@pytest.fixture
def ops_run(monkeypatch, tmp_path):
    tool = tmp_path / "skippy-game-backup"                       # the tool startup.sh installs; it must exist on the VM
    tool.write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr(agent, "BACKUP", str(tool))

    def make(**kw):
        run = Recorder(**kw)
        monkeypatch.setattr(agent, "run", run)
        monkeypatch.setattr(agent, "run_rc", run.rc)
        return run
    return make


def docker_steps(run):
    return [c[:2] for c in run.calls if c[0] == "docker"]


def test_ops_restore_checks_first_then_stops_restores_and_restarts_the_game(ops_run):
    run = ops_run()
    out = agent.cmd_restore_game(["valheim", "latest"])
    assert [c[2] for c in run.calls if c[0] == "python3"] == ["check", "restore"]            # checked before anything stopped
    assert run.calls.index(next(c for c in run.calls if c[:3] == ("python3", agent.BACKUP, "check"))) < \
        run.calls.index(("docker", "stop", "--time"))
    assert docker_steps(run) == [("docker", "ps"), ("docker", "stop"), ("docker", "start")] and "started again" in out


def test_ops_restore_with_a_bad_archive_stops_nothing_and_says_so(ops_run):
    run = ops_run(fail_check=True)
    with pytest.raises(ValueError, match="nothing was stopped or changed"):
        agent.cmd_restore_game(["valheim", "nope"])
    assert docker_steps(run) == []                                                            # nobody was kicked off


def test_ops_restore_that_fails_is_an_error_not_a_success_and_the_game_comes_back(ops_run):
    run = ops_run(fail_restore=True)
    with pytest.raises(RuntimeError, match="files are as they were.*valheim was started again"):
        agent.cmd_restore_game(["valheim", "latest"])
    assert ("docker", "start", "valheim") in run.calls


def test_ops_restore_needs_the_tool_on_the_vm(ops_run, monkeypatch, tmp_path):
    run = ops_run()
    monkeypatch.setattr(agent, "BACKUP", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="restart the server once"):
        agent.cmd_restore_game(["valheim", "latest"])
    assert run.calls == []


def test_ops_restore_of_a_stopped_game_and_of_everquest(ops_run):
    run = ops_run(up=())
    assert "next start" in agent.cmd_restore_game(["valheim", "latest"])
    assert not [c for c in run.calls if c[:2] in (("docker", "stop"), ("docker", "start"))]
    run = ops_run(up=())
    assert "restart" in agent.cmd_restore_game(["everquest", "latest"])
    assert (agent.EVERQUEST, "stop") in [c[:2] for c in run.calls] and ("docker", "start") not in [c[:2] for c in run.calls]


@pytest.mark.parametrize("args", [[], ["valheim"], ["Val heim", "latest"], ["valheim", "../x"], ["valheim", "a b"]])
def test_ops_restore_rejects_bad_input_before_touching_anything(ops_run, args):
    run = ops_run()
    with pytest.raises(ValueError):
        agent.cmd_restore_game(args)
    assert run.calls == []


def test_an_agent_error_makes_the_workflow_step_fail():
    """remote.py exits non-zero when the VM's reply starts with `error:` (how the agent reports a failure)."""
    remote = load("remote", "../../scripts/remote.py")

    class FakeVm:
        def __init__(self, reply):
            self.reply, self.sent = reply, None

        def status(self):
            return type("S", (), {"status": "RUNNING"})()

        def set_metadata(self, key, value):
            self.sent = __import__("json").loads(value)["id"]

        def serial_output(self, port):
            rid = self.sent
            return f"=== ops-reply {rid} ===\n{self.reply}\n=== ops-end {rid} ===\n"

    remote.time.sleep = lambda s: None
    assert remote.agent(FakeVm("error: the restore failed and the files are as they were"), "restore-game", ["valheim", "latest"], {}) == 1
    assert remote.agent(FakeVm("restore valheim: fine"), "restore-game", ["valheim", "latest"], {}) == 0


def test_the_new_ops_commands_are_exposed_everywhere():
    assert {"backups", "restore-game"} <= set(agent.COMMANDS)
    remote = (INFRA.parents[1] / "scripts" / "remote.py").read_text(encoding="utf-8")
    assert '"backups", "restore-game")' in remote
    ops_yml = (INFRA.parents[1] / ".github" / "workflows" / "ops.yml").read_text(encoding="utf-8")
    assert "backups, restore-game" in ops_yml
