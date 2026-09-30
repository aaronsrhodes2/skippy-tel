"""The per-game "Server admin" card: installed versions, image age, mods, update policy. Admin only.

Three layers, each pinned to reality where it can be (fixtures/admin_panel/README.md says how each sample was captured):
  1. infra/gcp-games/game_facts.py (runs on the VM) against real logs, files and `docker inspect` output from the real
     Valheim and Factorio images, and against its own real end-to-end output;
  2. portal/diagnostics.py parsing those lines back out of a snapshot;
  3. the card, its settings and their compose wiring, and who may see any of it (the Captain only).
Satisfactory, Dragonwilds and EverQuest were too heavy to run: their tests say UNVERIFIED and use documentation.
"""
import importlib.util
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import admin_panel as ap  # noqa: E402
import autoconfig  # noqa: E402
import diagnostics  # noqa: E402
import app as portal  # noqa: E402
from catalog import GAMES, UPDATE_DEFAULTS, UPDATE_SETTINGS  # noqa: E402
from test_app import ADMIN, FRIEND, PAL, SETTINGS, FakeSkippy, FakeVm, _add_friend, login  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "admin_panel"
INFRA = ROOT / "infra" / "gcp-games"
spec = importlib.util.spec_from_file_location("game_facts", INFRA / "game_facts.py")
gf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gf)


def fixture(*parts) -> str:
    return FIX.joinpath(*parts).read_text(encoding="utf-8")


# ── 1. game_facts.py against real captures ────────────────────────────────────

def test_valheims_version_and_mod_loader_come_from_the_servers_own_log_lines():
    vanilla, modded = fixture("valheim", "docker.vanilla-first-start.txt"), fixture("valheim", "docker.bepinex-restart.txt")
    assert gf.valheim_version(vanilla) == "l-1.0.16"                     # `Valheim version: l-1.0.16 (network version 40)`
    assert gf.valheim_version(modded) == "l-1.0.16"                      # the last of two boots in one log
    assert gf.valheim_framework(vanilla) == "none" and gf.valheim_framework(modded) == "BepInEx"
    assert gf.valheim_version("nothing to see") == "" and gf.valheim_framework("") == ""


def test_steams_manifest_gives_the_build_and_when_the_files_last_changed():
    info = gf.steam_manifest(fixture("valheim", "appmanifest_896660.acf"))
    assert info == {"build": "25527701", "updated": "2026-09-30T06:48:47Z"}       # LastUpdated 1790750927
    assert gf.steam_manifest("not a manifest") == {"build": "", "updated": ""}


def test_bepinex_lists_the_pack_and_every_plugin_it_loaded():
    with_plugin = gf.bepinex_mods(fixture("valheim", "LogOutput.with-plugin.txt"))
    assert with_plugin == [("BepInExPack_Valheim", "5.4.2351"), ("Lab Plugin", "1.2.3")]     # a name with a space
    assert gf.bepinex_mods(fixture("valheim", "LogOutput.no-plugins.txt")) == [("BepInExPack_Valheim", "5.4.2351")]
    # the same lines inside `docker logs` (supervisord prefix, a byte-order mark, the header twice) give the same answer
    assert gf.bepinex_mods(fixture("valheim", "docker.bepinex-restart.txt")) == with_plugin
    assert gf.bepinex_mods("") == []


def test_factorios_version_and_loaded_mods_come_from_its_log_and_mods_folder():
    log = fixture("factorio", "factorio-current.txt")
    assert gf.factorio_version(log) == "2.0.77 (build 84539)"
    names = sorted(p.name for p in (FIX / "factorio" / "mods").iterdir())
    assert names == ["mod-list.json", "skippy-lab-control_1.0.0.zip", "skippy-lab-mod_0.1.3.zip"]
    mods = dict(gf.factorio_mods(log, names))
    assert mods == {"base": "2.0.77", "elevated-rails": "2.0.77", "quality": "2.0.77", "space-age": "2.0.77",
                    "skippy-lab-mod": "0.1.3",                   # named on a `Loading mod ... (data.lua)` line
                    "skippy-lab-control": "1.0.0"}               # control-only: the log names it but has no version: its file name does
    assert "core" not in mods
    assert dict(gf.factorio_mods(log, []))["skippy-lab-control"] == "?"      # nothing to read the version from: say so, don't guess


def test_factorios_mod_updater_is_summarised_per_start_from_the_real_log():
    text = fixture("factorio", "docker.update-mods-four-boots.txt")
    first = text.split("[... omitted")[0]
    assert gf.factorio_update_summary(first.splitlines()) == {
        "checked": 2, "downloaded": 0, "current": 0, "failed": 1, "skipped": 1, "nocreds": 1, "code": "403"}
    assert gf.factorio_update_summary(text.splitlines()) == {        # four starts in one log: the last one counts
        "checked": 2, "downloaded": 0, "current": 0, "failed": 0, "skipped": 2, "nocreds": 1}
    assert gf.factorio_update_summary(["a line from a server that never ran the updater"]) == {}
    ok = ["+ //docker-update-mods.sh", "Checking for update of mod a for factorio 2.0.77 ...", "  Already up-to-date.",
          "Checking for update of mod b for factorio 2.0.77 ...", "  Downloading b_1.2.3.zip", "  Download complete.",
          "  Deleting old version: /factorio/mods/b_1.0.0.zip", "Skipping mod c (listed in UPDATE_IGNORE)"]
    assert gf.factorio_update_summary(ok) == {"checked": 2, "downloaded": 1, "current": 1, "failed": 0, "skipped": 1}


def test_dates_come_out_in_utc_whatever_docker_or_steam_wrote():
    assert gf.iso_utc("2026-09-19T19:48:18.589333296Z") == "2026-09-19T19:48:18Z"            # docker, nanoseconds
    assert gf.iso_utc("2025-02-27T19:35:09.786664332-06:00") == "2025-02-28T01:35:09Z"       # docker, the eqemu image's offset
    assert gf.iso_utc("1790750927") == "2026-09-30T06:48:47Z"                                # Steam, epoch seconds
    assert gf.iso_utc("yesterday") == "" and gf.iso_utc("") == ""


def test_the_mods_field_survives_odd_names_and_the_portal_reads_it_back():
    mods = [("A;B", "1.0"), ("Name with @ sign", "2.1.0"), ("plain", "3")]
    field = gf.mods_field(mods)
    assert field.count(";") == 2                                  # the ';' inside a name can't split it in two
    assert diagnostics.parse_mods(field) == [("A,B", "1.0"), ("Name with @ sign", "2.1.0"), ("plain", "3")]
    assert gf.safe_version("1.2.3\nJOIN_CODE valheim=999999") == "?"                # nothing can add a line to the snapshot
    assert "\n" not in gf.clean("two\nlines\x1b[31m")


def test_akkstack_is_versioned_by_its_pinned_commit():
    pinned = re.search(r"^AKK_REF=([0-9a-f]{40})$", (INFRA / "everquest.sh").read_text(encoding="utf-8"), re.M).group(1)
    assert gf.akkstack_commit(pinned + "\n") == pinned[:7]         # UNVERIFIED on the VM: a detached checkout's .git/HEAD is the bare hash
    assert gf.akkstack_commit("ref: refs/heads/master") == ""


def test_satisfactory_mods_are_read_from_uplugin_files_UNVERIFIED():
    """The format is from the modding documentation, not from a run (Satisfactory is too heavy to run here)."""
    files = {"SomeMod": '{"FriendlyName": "Some Mod", "VersionName": "1.4.2"}', "Broken": "{not json"}
    assert gf.uplugin_mods(files) == [("Some Mod", "1.4.2")]


# The VM as game_facts.py sees it, rebuilt from the real captures, with docker answered by the real `inspect` outputs.

class FakeDocker:
    def __init__(self, containers):
        self.containers = containers                                       # name -> {inspect, created, log}

    def run(self, *argv, timeout=20):
        if argv[:3] == ("docker", "ps", "--format"):
            return "\n".join(self.containers)
        if argv[:2] == ("docker", "inspect"):
            assert argv[2] == "-f", "docker inspect must only ever be asked for fixed fields, never its environment"
            row = self.containers.get(argv[4])
            return "" if not row else (row.get("label", "") if "factorio.version" in argv[3] else row["inspect"].strip())
        if argv[:3] == ("docker", "image", "inspect"):
            for row in self.containers.values():
                if row["inspect"].split("\t")[1] == argv[5]:
                    return row["created"].strip()
            return ""
        raise AssertionError(f"unexpected command {argv}")

    def scan_log(self, container, patterns):
        lines = self.containers[container]["log"].splitlines()
        return {key: [ln for ln in lines if pattern.search(ln)] for key, pattern in patterns.items()}


def vm_disk(tmp_path, bepinex_log="LogOutput.no-plugins.txt"):
    v = tmp_path / "valheim" / "server"
    (v / "dl" / "server" / "steamapps").mkdir(parents=True)
    (v / "dl" / "server" / "steamapps" / "appmanifest_896660.acf").write_text(fixture("valheim", "appmanifest_896660.acf"))
    if bepinex_log:
        (v / "bepinex" / "BepInEx").mkdir(parents=True)
        (v / "bepinex" / "BepInEx" / "LogOutput.log").write_text(fixture("valheim", bepinex_log), encoding="utf-8")
    f = tmp_path / "factorio"
    (f / "mods").mkdir(parents=True)
    (f / "factorio-current.log").write_text(fixture("factorio", "factorio-current.txt"), encoding="utf-8")
    for p in (FIX / "factorio" / "mods").iterdir():
        (f / "mods" / p.name).write_bytes(p.read_bytes())
    return tmp_path


def real_docker(valheim_log):
    row = lambda game, image_of, log: {"inspect": fixture(f"docker.inspect-container.{game}.txt"),        # noqa: E731
                                        "created": fixture(f"docker.inspect-image.{image_of}.txt"), "log": log}
    return FakeDocker({"valheim": row("valheim", "valheim", fixture("valheim", valheim_log)),
                       "factorio": row("factorio", "factorio", fixture("factorio", "docker.update-mods-four-boots.txt"))})


def run_facts(monkeypatch, tmp_path, docker, **kw):
    monkeypatch.setattr(gf, "run", docker.run)
    monkeypatch.setattr(gf, "scan_log", docker.scan_log)
    facts = gf.collect(vm_disk(tmp_path, **kw))
    return [line for game in gf.ALL_GAMES for line in gf.render(game, facts.get(game, {}))]


def test_the_facts_script_reproduces_its_real_run_line_for_line(monkeypatch, tmp_path):
    """facts.lab-live-bepinex.txt is what game_facts.py printed when run for real against live Valheim (BepInEx on, no
    plugin) and Factorio containers. The same files and `docker inspect` answers, saved, give the same lines."""
    lines = run_facts(monkeypatch, tmp_path, real_docker("docker.bepinex-restart.txt"))
    assert lines == fixture("facts.lab-live-bepinex.txt").splitlines()


def test_with_a_plugin_installed_the_mods_line_lists_it(monkeypatch, tmp_path):
    lines = run_facts(monkeypatch, tmp_path, real_docker("docker.bepinex-restart.txt"), bepinex_log="LogOutput.with-plugin.txt")
    assert "MODS valheim=BepInExPack_Valheim@5.4.2351;Lab Plugin@1.2.3" in lines


def test_a_stale_bepinex_log_is_ignored_when_the_server_ran_vanilla(monkeypatch, tmp_path):
    """Observed for real: turn BepInEx off and its LogOutput.log stays on the disk. The image's `mod: none` line wins."""
    lines = run_facts(monkeypatch, tmp_path, real_docker("docker.vanilla-first-start.txt"))
    assert "MODS valheim=" in lines                                     # known, and none loaded
    assert not any("Lab Plugin" in ln or "BepInExPack" in ln for ln in lines if ln.startswith("MODS valheim"))
    assert "VERSION valheim=l-1.0.16 (Steam build 25527701)" in lines


def test_the_real_vanilla_run_parses_back_into_facts():
    facts = diagnostics.game_facts(fixture("facts.lab-live-vanilla.txt"))
    assert facts["valheim"] == {"version": "l-1.0.16 (Steam build 25527701)",
                                "image": "ghcr.io/lloesche/valheim-server:latest", "image_built": "2026-09-19T19:48:18Z",
                                "updated": "2026-09-30T06:48:47Z", "started": "2026-09-30T07:06:08Z", "mods": []}
    assert facts["factorio"]["version"] == "2.0.77 (build 84539)"
    assert ("skippy-lab-control", "1.0.0") in facts["factorio"]["mods"]
    assert facts["factorio"]["modupdate"] == {"checked": 2, "downloaded": 0, "current": 0, "failed": 0, "skipped": 2, "nocreds": 1}
    assert "mods" not in diagnostics.game_facts("VERSION dragonwilds=Steam build 1\n")["dragonwilds"]   # no MODS line: no list to read


def test_the_script_never_prints_settings_or_secrets(monkeypatch, tmp_path):
    canary = "hunter2-CANARY"
    docker = real_docker("docker.bepinex-restart.txt")
    docker.containers["valheim"]["log"] += f"\nServer config is name: X, password: {canary}\nJOIN_CODE valheim=123456 {canary}\n"
    (tmp_path / "factorio").mkdir()
    lines = run_facts(monkeypatch, tmp_path, docker)
    assert canary not in "\n".join(lines) and "JOIN_CODE" not in "\n".join(lines)
    source = (INFRA / "game_facts.py").read_text(encoding="utf-8")
    calls = re.findall(r'run\("docker", "(?:image", "inspect|inspect)", ([^)]*)\)', source)
    assert len(calls) == 3 and "{{.Config.Env" not in source and "{{json" not in source and "{{.Config}}" not in source
    assert all('"-f"' in call for call in calls)                       # only ever fixed fields, never the whole inspect (which holds the env)
    for line in lines:
        assert re.match(r"^(VERSION|IMAGE|UPDATED|STARTED|MODS|MODUPDATE) [a-z]+=", line), line


def test_a_game_that_is_not_up_prints_nothing_and_a_broken_docker_prints_nothing(monkeypatch, tmp_path):
    down = FakeDocker({})
    assert run_facts(monkeypatch, tmp_path, down) == []
    monkeypatch.setattr(gf, "run", lambda *a, **k: "")
    assert gf.collect(tmp_path) == {}


def test_everquest_is_read_from_its_own_compose_project(monkeypatch, tmp_path):
    head = tmp_path / "everquest" / "akk-stack" / ".git"
    head.mkdir(parents=True)
    (head / "HEAD").write_text("6e62b54886b9a77234b638e200a9991f5bd51a69\n")
    row = {"inspect": fixture("docker.inspect-container.everquest.txt"), "created": fixture("docker.inspect-image.everquest.txt"), "log": ""}
    monkeypatch.setattr(gf, "run", FakeDocker({"akk-stack-eqemu-server-1": row}).run)
    lines = [ln for g in gf.ALL_GAMES for ln in gf.render(g, gf.collect(tmp_path).get(g, {}))]
    assert lines == ["VERSION everquest=AkkStack 6e62b54",
                     "IMAGE everquest=eqemulator/eqemu-server:v16 2025-02-28T01:35:09Z",
                     "STARTED everquest=2026-07-16T02:34:08Z"]


def test_diag_runs_the_script_from_metadata_and_terraform_ships_it():
    diag = (INFRA / "diag.sh").read_text(encoding="utf-8")
    start, end = diag.index("# ── game facts"), diag.index("# ── end game facts ──")
    block = diag[start:end]
    assert "attributes/game-facts" in block and "python3 -" in block and "timeout" in block and "|| true" in block
    assert block.index('echo "--- game facts"') < block.index("python3 -")     # its own section, so no game's log view picks it up
    assert start < diag.index('echo "--- udp listeners')                       # inside the snapshot group, before its end marker
    assert 'game-facts       = file("${path.module}/game_facts.py")' in (INFRA / "main.tf").read_text(encoding="utf-8")


# ── 2. the portal reads the snapshot ───────────────────────────────────────────

def snapshot(facts_text, taken="2026-09-30T07:10:00+00:00", tail="=== end ===\n"):
    return (f"=== skippy-diag {taken} ===\nNAMES STATUS PORTS\nvalheim Up 3 minutes\n--- valheim: last 40 log lines\nsome log\n"
            f"--- game facts\n{facts_text}--- udp listeners (game ports)\n(none)\n{tail}")


def test_facts_are_parsed_out_of_a_serial_snapshot_with_crlf_line_ends():
    snap = snapshot(fixture("facts.lab-live-bepinex.txt")).replace("\n", "\r\n")
    facts, taken = diagnostics.game_facts(diagnostics.latest_snapshot(snap)), diagnostics.snapshot_stamp(diagnostics.latest_snapshot(snap))
    assert taken == "2026-09-30T07:10:00+00:00"
    assert facts["valheim"]["mods"] == [("BepInExPack_Valheim", "5.4.2351")]
    assert facts["factorio"]["image"] == "factoriotools/factorio:stable"


def test_the_last_line_of_a_kind_wins_and_junk_is_ignored():
    text = ("VERSION valheim=old\nVERSION valheim=new\nIMAGE valheim=some/image:tag not-a-date\nMODS valheim=a@1;b\n"
            "UPDATED valheim=garbage\nMODUPDATE factorio=checked:3,code:403,,x:\nverbose VERSION x=1\nVERSION Bad Name=1\n")
    f = diagnostics.game_facts(text)
    assert f["valheim"]["version"] == "new"
    assert (f["valheim"]["image"], f["valheim"]["image_built"]) == ("some/image:tag not-a-date", "")
    assert f["valheim"]["mods"] == [("a", "1"), ("b", "?")] and f["valheim"]["updated"] == ""
    assert f["factorio"]["modupdate"] == {"checked": 3, "code": 403}           # empty pieces are dropped
    assert set(f) == {"valheim", "factorio"}


def test_the_snapshot_still_being_written_is_skipped_for_the_last_finished_one():
    done = snapshot("VERSION valheim=1.0\n", taken="2026-09-30T07:08:00+00:00")
    partial = "=== skippy-diag 2026-09-30T07:10:00+00:00 ===\nNAMES STATUS\n"          # no facts yet, no end marker
    assert "1.0" in diagnostics.latest_complete_snapshot(done + partial)
    assert diagnostics.snapshot_stamp(diagnostics.latest_complete_snapshot(done + partial)) == "2026-09-30T07:08:00+00:00"
    assert diagnostics.latest_complete_snapshot(partial) == partial            # all there is: better than nothing
    assert diagnostics.latest_complete_snapshot("") == ""


def test_a_facts_section_does_not_leak_into_any_games_log_view():
    snap = snapshot("VERSION valheim=1.0\n")
    assert "VERSION" not in diagnostics.for_game(snap, "valheim")               # its own `--- game facts` section
    assert diagnostics.join_codes(snap) == {} and diagnostics.players_online(snap) == {}


# ── 3. the card ─────────────────────────────────────────────────────────────────

def test_ago_reads_like_a_person():
    from datetime import datetime, timezone
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    assert ap.ago("2026-09-30T11:59:30Z", now) == "just now"
    assert ap.ago("2026-09-30T11:15:00Z", now) == "45 min ago"
    assert ap.ago("2026-09-30T06:00:00Z", now) == "6 hours ago"
    assert ap.ago("2026-09-19T19:48:18Z", now) == "11 days ago"          # 10 days 16 hours
    assert ap.ago("2026-09-30T11:00:00+00:00", now) == "60 min ago" and ap.ago("", now) == "" and ap.ago("soon", now) == ""


def running(vm_env=None, games=("valheim",)):
    status = FakeVm("RUNNING").state
    status.games = list(games)
    status.env = {**status.env, **(vm_env or {})}
    return status


def test_the_card_for_a_running_modded_valheim_says_what_is_installed_and_what_will_happen():
    facts = diagnostics.game_facts(fixture("facts.lab-live-bepinex.txt"))
    facts["valheim"]["mods"] = [("Lab Plugin", "1.2.3"), ("BepInExPack_Valheim", "5.4.2351")]
    from datetime import datetime, timezone
    now = datetime(2026, 9, 30, 7, 12, tzinfo=timezone.utc)
    panel = ap.build(GAMES["valheim"], running({"VALHEIM_BEPINEX": "true"}), facts, "2026-09-30T07:10:00+00:00", now)
    assert panel["on"] and panel["version"] == "l-1.0.16 (Steam build 25527701)"
    assert (panel["image_age"], panel["image_built"]) == ("10 days ago", "2026-09-19")
    assert (panel["updated_ago"], panel["updated"]) == ("23 min ago", "2026-09-30")
    assert panel["as_of"] == "2 min ago" and not panel["stale"]
    assert [(m["name"], m["role"]) for m in panel["mods"]] == [("Lab Plugin", "installed"), ("BepInExPack_Valheim", "mod loader")]
    assert panel["mods_note"] == ""
    by_what = {p["what"]: p for p in panel["policy"]}
    assert by_what["Mods (BepInEx)"]["state"] == "on" and by_what["Server files"]["state"] == "auto"
    assert ap.build(GAMES["valheim"], running(), facts, "2026-09-30T06:00:00+00:00", now)["stale"]


def test_a_vanilla_valheim_says_mod_support_is_off_and_how_to_turn_it_on():
    facts = diagnostics.game_facts(fixture("facts.lab-live-vanilla.txt"))
    panel = ap.build(GAMES["valheim"], running(), facts, "2026-09-30T07:10:00+00:00")
    assert panel["mods"] == [] and "Mod support is off" in panel["mods_note"]
    assert {p["what"]: p["state"] for p in panel["policy"]}["Mods (BepInEx)"] == "off"


def test_factorios_card_lists_built_in_mods_apart_from_installed_ones_and_reports_the_updater():
    facts = diagnostics.game_facts(fixture("facts.lab-live-vanilla.txt"))
    panel = ap.build(GAMES["factorio"], running(games=("factorio",)), facts, "2026-09-30T07:10:00+00:00")
    roles = {m["name"]: m["role"] for m in panel["mods"]}
    assert roles["space-age"] == "built in" and roles["skippy-lab-mod"] == "installed"
    assert [m["role"] for m in panel["mods"]] == ["installed", "installed", "built in", "built in", "built in", "built in"]   # installed first
    assert panel["mod_update"] == ("Last start: checked 2, updated 0, already current 0, 2 skipped. "
                                   "No Factorio username/token was given, so downloads can't succeed.")
    assert "HTTP 403" in ap.mod_update_text({"checked": 1, "failed": 1, "code": "403"})


def test_games_without_mod_support_say_so_plainly_and_why():
    for gid in ("dragonwilds", "everquest"):
        panel = ap.build(GAMES[gid], running(games=(gid,)), {gid: {"version": "x"}}, "2026-09-30T07:10:00+00:00")
        assert panel["mods"] is None and panel["mods_note"] == ap.MOD_SUPPORT[gid]["why"], gid
    assert "no mod support" in ap.MOD_SUPPORT["dragonwilds"]["why"] and "database" in ap.MOD_SUPPORT["everquest"]["why"]


def test_an_asleep_or_other_game_has_no_facts_but_still_shows_its_policy():
    asleep = FakeVm("TERMINATED").state
    panel = ap.build(GAMES["factorio"], asleep, {"factorio": {"version": "stale from another life"}})
    assert not panel["on"] and panel["version"] == "" and panel["mods"] is None
    assert panel["policy"] and "asleep" in panel["mods_note"]
    other = ap.build(GAMES["factorio"], running(games=("valheim",)), {"factorio": {"version": "x"}})
    assert other["version"] == ""


def test_the_policy_follows_the_saved_settings():
    p = lambda gid, env: {r["what"]: r for r in ap.policy(gid, env)}       # noqa: E731
    assert p("factorio", {})["Server version"]["state"] == "auto" and "stable" in p("factorio", {})["Server version"]["text"]
    assert p("factorio", {"FACTORIO_VERSION": "2.0.77"})["Server version"]["state"] == "pinned"
    assert p("factorio", {})["Mod updates"]["state"] == "off"
    on = p("factorio", {"FACTORIO_UPDATE_MODS": "TRUE", "FACTORIO_UPDATE_IGNORE": "a,b"})["Mod updates"]
    assert on["state"] == "on" and "403" in on["text"] and "a,b" in on["text"]                       # no credentials saved: says so
    creds = p("factorio", {"FACTORIO_UPDATE_MODS": "true", "FACTORIO_USERNAME": "u", "FACTORIO_TOKEN": "t0ken-CANARY"})["Mod updates"]
    assert "403" not in creds["text"] and "t0ken-CANARY" not in creds["text"]
    assert p("satisfactory", {})["Server files"]["state"] == "auto" and p("satisfactory", {"SATISFACTORY_SKIP_UPDATE": "true"})["Server files"]["state"] == "pinned"
    assert p("dragonwilds", {})["Server files"]["state"] == "auto" and p("dragonwilds", {"DRAGONWILDS_UPDATE_ON_START": "false"})["Server files"]["state"] == "pinned"
    assert p("everquest", {})["Server version"]["state"] == "pinned" and "AKK_REF" in p("everquest", {})["Server version"]["text"]
    assert p("valheim", {"VALHEIM_BEPINEX": "true"})["Mods (BepInEx)"]["state"] == "on"


# ── 4. keeping current: the settings, and their wiring on the VM ──────────────

COMPOSE = (ROOT / "games" / "docker-compose.yml").read_text(encoding="utf-8")


def test_every_new_setting_has_a_default_in_compose_and_says_what_blank_means():
    """Like Valheim's crossplay default: what the portal calls 'blank' and what the VM does must be the same word."""
    keys = {s.key for group in UPDATE_SETTINGS.values() for s in group}
    assert keys == set(UPDATE_DEFAULTS)
    for key, default in UPDATE_DEFAULTS.items():
        assert "${%s:-%s}" % (key, default) in COMPOSE, f"{key}: compose must default to {default!r}"
    settings = {s.key: s for game in GAMES.values() for s in game.settings}
    for key in ("VALHEIM_BEPINEX", "FACTORIO_VERSION", "FACTORIO_UPDATE_MODS", "SATISFACTORY_SKIP_UPDATE", "DRAGONWILDS_UPDATE_ON_START"):
        assert f"blank means {UPDATE_DEFAULTS[key]}" in settings[key].help, key
    for gid, group in UPDATE_SETTINGS.items():
        assert all(s in GAMES[gid].settings for s in group), gid


def test_the_defaults_keep_todays_behaviour():
    """Nothing changes until the Captain turns something on: these are what the compose file said before this feature."""
    assert UPDATE_DEFAULTS["FACTORIO_VERSION"] == "stable" and "image: factoriotools/factorio:${FACTORIO_VERSION:-stable}" in COMPOSE
    assert UPDATE_DEFAULTS["DRAGONWILDS_UPDATE_ON_START"] == "true"           # was the literal "true"
    assert UPDATE_DEFAULTS["VALHEIM_BEPINEX"] == "false" and UPDATE_DEFAULTS["FACTORIO_UPDATE_MODS"] == "false"
    assert UPDATE_DEFAULTS["SATISFACTORY_SKIP_UPDATE"] == "false"
    assert "UPDATE_MODS_ON_START: ${FACTORIO_UPDATE_MODS:-false}" in COMPOSE and "BEPINEX: ${VALHEIM_BEPINEX:-false}" in COMPOSE
    assert "SKIPUPDATE: ${SATISFACTORY_SKIP_UPDATE:-false}" in COMPOSE
    assert "USERNAME: ${FACTORIO_USERNAME:-}" in COMPOSE and "TOKEN: ${FACTORIO_TOKEN:-}" in COMPOSE     # the image's own names (read from its scripts)


def test_the_factorio_token_is_a_secret_nobody_makes_up_and_nothing_echoes():
    token = next(s for s in GAMES["factorio"].settings if s.key == "FACTORIO_TOKEN")
    assert token.secret and token.external and not next(s for s in GAMES["factorio"].settings if s.key == "FACTORIO_USERNAME").secret
    plan = autoconfig.plan({}, {"factorio": GAMES["factorio"]})
    assert "FACTORIO_TOKEN" not in plan.set and "FACTORIO_TOKEN" not in plan.generated           # other secrets are generated; this one is issued
    text = diagnostics.redact("curl ...&token=t0ken-CANARY-9 ...", {"FACTORIO_TOKEN": "t0ken-CANARY-9"})
    assert "t0ken-CANARY-9" not in text


@pytest.fixture
def env():
    vm = FakeVm()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy())
    return app.test_client(), vm, app


def test_saving_settings_checks_the_new_ones_and_normalises_them(env):
    client, vm, _ = env
    login(client, ADMIN)
    saved = lambda: [c for c in vm.calls if c[0] == "update_env"]           # noqa: E731
    bad = client.post("/admin/settings", data={"FACTORIO_UPDATE_MODS": "yes", "FACTORIO_VERSION": "2.0", "VALHEIM_BEPINEX": "maybe",
                                               "FACTORIO_UPDATE_IGNORE": "ok-mod, bad/mod"}, follow_redirects=True).data.decode()
    assert "must be true or false" in bad and "must be stable, latest, or an exact version" in bad and "must be mod names" in bad
    assert saved() == []                                                      # one bad field: nothing saved
    client.post("/admin/settings", data={"FACTORIO_UPDATE_MODS": " True ", "FACTORIO_VERSION": "STABLE", "VALHEIM_BEPINEX": "FALSE",
                                         "FACTORIO_UPDATE_IGNORE": " a-mod ,b_mod ,", "FACTORIO_USERNAME": "captain",
                                         "FACTORIO_TOKEN": "t0ken-CANARY", "SATISFACTORY_SKIP_UPDATE": "", "DRAGONWILDS_UPDATE_ON_START": "true"})
    got = saved()[-1][1]
    assert got["FACTORIO_UPDATE_MODS"] == "true" and got["FACTORIO_VERSION"] == "stable" and got["VALHEIM_BEPINEX"] == "false"
    assert got["FACTORIO_UPDATE_IGNORE"] == "a-mod,b_mod" and got["FACTORIO_TOKEN"] == "t0ken-CANARY"
    assert got["SATISFACTORY_SKIP_UPDATE"] == ""                             # cleared: back to the default
    page = client.get("/games/factorio").data.decode()
    assert "t0ken-CANARY" not in page and "(set — leave blank to keep)" in page          # the token is never shown again
    assert "t0ken-CANARY" not in client.get("/admin").data.decode()
    client.post("/admin/settings", data={"FACTORIO_TOKEN": ""})                          # blank = keep
    assert vm.state.env["FACTORIO_TOKEN"] == "t0ken-CANARY"


def test_the_docs_cover_every_game_and_every_new_setting():
    doc = (ROOT / "docs" / "games" / "MODS_AND_UPDATES.md").read_text(encoding="utf-8")
    for game in GAMES.values():
        assert f"## {game.name}" in doc, game.name
    for key in UPDATE_DEFAULTS:
        assert key in doc, key
    assert doc.count("UNVERIFIED") >= 6                                      # the games too heavy to run say so, where they say it


# ── 5. only the Captain ─────────────────────────────────────────────────────────

FACTS = fixture("facts.lab-live-bepinex.txt")
TELLTALES = ("Server admin", "server-admin", "/admin/games/", "Steam build", "BepInExPack", "Update policy", "Installed version")


def live_vm(vm, games, facts=FACTS):
    vm.state.status, vm.state.games = "RUNNING", list(games)
    vm.serial[2] = snapshot(facts)


def test_the_admin_sees_the_card_on_a_running_game(env):
    client, vm, _ = env
    live_vm(vm, ["valheim"])
    vm.state.env["VALHEIM_BEPINEX"] = "true"
    login(client, ADMIN)
    page = client.get("/games/valheim").data.decode()
    assert "Server admin" in page and "l-1.0.16 (Steam build 25527701)" in page
    assert "ghcr.io/lloesche/valheim-server:latest" in page and "BepInExPack_Valheim" in page and "mod loader" in page
    assert "Update policy" in page and "Restart &amp; update" in page                  # the existing button lives in the same card
    assert page.index("Server admin") < page.index("Restart &amp; update") < page.index("Save Valheim settings")
    assert "/admin/games/valheim/panel" in page                                       # the poll that keeps it fresh
    assert "hunter22" not in page.split("Server admin")[1].split("Restart &amp; update")[0]     # the card itself carries no secrets


def test_the_admin_sees_the_card_for_a_sleeping_server_too(env):
    client, vm, _ = env
    login(client, ADMIN)
    vm.state.status = "TERMINATED"
    page = client.get("/games/factorio").data.decode()
    assert "Server admin" in page and "isn't running Factorio" in page and "Update policy" in page
    assert "Installed version" not in page                                            # nothing to report while it sleeps


def test_the_refresh_route_serves_the_same_card_body_to_the_admin(env):
    client, vm, _ = env
    live_vm(vm, ["factorio"])
    login(client, ADMIN)
    resp = client.get("/admin/games/factorio/panel")
    body = resp.data.decode()
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert "2.0.77 (build 84539)" in body and "skippy-lab-control" in body and "built in" in body and "<html" not in body
    assert client.get("/admin/games/nope/panel").status_code == 404
    page = client.get("/games/factorio").data.decode()                                # the page embeds the same body
    for part in ("2.0.77 (build 84539)", "skippy-lab-control", "Update policy", "Installed version"):
        assert part in body and part in page, part


def test_a_friend_never_gets_the_card_or_any_route_behind_it(env):
    client, vm, app = env
    live_vm(vm, ["valheim"])
    vm.state.env["VALHEIM_BEPINEX"] = "true"
    for who in (FRIEND, PAL):
        if who == PAL:
            _add_friend(client, PAL, ["valheim", "factorio"])
        login(client, who)
        page = client.get("/games/valheim").data.decode()
        assert "Restart" not in page                                                     # the whole admin block is absent for them
        for trace in TELLTALES:
            assert trace not in page, (who, trace)
        assert "l-1.0.16" not in page and "Lab Plugin" not in page
        for gid in GAMES:
            assert client.get(f"/admin/games/{gid}/panel").status_code == 403, (who, gid)
        assert client.get("/admin/games/nope/panel").status_code == 403              # not even which games exist
        live = client.get("/games/valheim/live")
        # The friends' poll carries what a player needs (is it up, is it joinable, the code, how many are on) and
        # nothing from the admin card: no version, image, mods or update fields. (`ready` comes from the readiness log check.)
        assert set(live.get_json()) <= {"on", "starting", "ready", "code", "players"}
        assert not {"version", "image", "mods", "updated", "facts"} & set(live.get_json())
    new_rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("server_admin.")]
    assert [r.rule for r in new_rules] == ["/admin/games/<gid>/panel"]               # the only new route, and it was refused above
    assert new_rules[0].methods - {"HEAD", "OPTIONS"} == {"GET"}                     # nothing that changes anything


def test_signed_out_visitors_get_nothing(env):
    client, vm, _ = env
    live_vm(vm, ["valheim"])
    assert client.get("/games/valheim").status_code == 302
    resp = client.get("/admin/games/valheim/panel")
    assert resp.status_code == 302 and "/admin" not in resp.headers["Location"] and "Server admin" not in resp.data.decode()
    with client.session_transaction() as s:                                          # a verified Google account that isn't on any list
        s["visitor"] = {"email": "stranger@example.com", "name": "S"}
    assert client.get("/admin/games/valheim/panel").status_code in (302, 403)
    assert "Server admin" not in client.get("/admin/games/valheim/panel", follow_redirects=True).data.decode()


def test_nothing_writes_through_the_new_route_and_the_admin_cannot_post_to_it(env):
    client, vm, _ = env
    login(client, ADMIN)
    before = list(vm.calls)
    for method in (client.post, client.put, client.delete):
        assert method("/admin/games/valheim/panel").status_code == 405
    assert vm.calls == before


def test_reading_the_facts_never_breaks_the_page(env):
    client, vm, _ = env
    live_vm(vm, ["valheim"])

    def boom(port):
        raise RuntimeError("serial port unreadable")
    login(client, ADMIN)
    vm.serial_output = boom
    page = client.get("/games/valheim")
    assert page.status_code == 200 and "Server admin" in page.data.decode() and "Waiting for the server" in page.data.decode()
