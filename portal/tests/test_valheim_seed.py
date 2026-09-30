"""infra/gcp-games/valheim_seed.py: grows a Valheim world from a seed by writing its metadata file.

The byte layout and the seed hash are pinned to real files: five worlds from the Captain's Steam saves
(seed text -> number), a v33 world rebuilt byte for byte, and a seed-only file the real 1.0.16 server loaded
and saved back as `Skippy/_main.1.fwl2` with the same seed (checked in Docker, 2026-09-29).
"""
import importlib.util
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import autoconfig

PATH = Path(__file__).resolve().parents[2] / "infra" / "gcp-games" / "valheim_seed.py"
spec = importlib.util.spec_from_file_location("valheim_seed", PATH)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)

# What the real 1.0.16 server wrote for our seed-only file (a `.fwl2`: version 41, and "has a save" set).
REAL_FWL2 = bytes.fromhex("370000002900000006536b6970707912736b69707079686173796f75726d6f6e6579"
                          "b3a6d691b241defc0000000002000000010000000000000000")
GOODWILL = bytes.fromhex("2d0000002100000008676f6f6477696c6c0a49437a6b777759783959e8fb770cb041202c"
                         "00000000020000000000000000")           # a real v33 world from Steam
NOW = datetime(2026, 9, 30, 6, 0, 0, tzinfo=timezone.utc)


def test_the_seed_hash_matches_real_worlds():
    real = {"chedder": -1215520143, "groggy": 1063433239, "ICzkwwYx9Y": 209189864,
            "fRjLLwUWkq": -1057898579, "dCH77NsiuF": 1381893640}
    assert {seed: vs.stable_hash(seed) for seed in real} == real
    assert struct.pack("<i", vs.stable_hash("skippyhasyourmoney")) in REAL_FWL2      # the number the real server kept


def test_the_metadata_layout_is_rebuilt_byte_for_byte():
    assert vs.build_fwl("goodwill", "ICzkwwYx9Y", 740311472, version=33) == GOODWILL
    ours = vs.build_fwl("Skippy", "skippyhasyourmoney", 4242424242)
    assert ours.hex() == ("330000002400000006536b6970707912736b69707079686173796f75726d6f6e6579"
                          "b3a6d691b241defc00000000020000000000000000")     # the file the real server accepted


def test_it_reads_what_the_real_server_saved(tmp_path):
    p = tmp_path / "_main.1.fwl2"
    p.write_bytes(REAL_FWL2)
    assert vs.read_meta(p) == ("Skippy", "skippyhasyourmoney")
    for junk in (b"", b"\x00" * 3, REAL_FWL2[:-3], REAL_FWL2 + b"x", b"\xff" * 40):
        p.write_bytes(junk)
        assert vs.read_meta(p) is None


def config_with_world(tmp_path, seed, layout="folder", world="Skippy", uid=7):
    """A config dir holding a world of `seed`, in the 1.0 folder format or the legacy files."""
    worlds = tmp_path / "config" / "worlds_local"
    worlds.mkdir(parents=True)
    fwl = vs.build_fwl(world, seed, uid)
    if layout == "folder":
        (worlds / world).mkdir()
        (worlds / world / "_main.1.fwl2").write_bytes(fwl)
        (worlds / world / "_main.1.db2").write_bytes(b"BUILT-BASE " * 50)
    else:
        (worlds / f"{world}.fwl").write_bytes(fwl)
        (worlds / f"{world}.db").write_bytes(b"BUILT-BASE " * 50)
    return tmp_path / "config"


def test_a_new_server_gets_the_seed(tmp_path):
    config = tmp_path / "config"
    line = vs.ensure_seed(config, "Skippy", "skippyhasyourmoney", NOW, uid=1)
    assert "will be generated from 'skippyhasyourmoney'" in line
    assert vs.read_meta(config / "worlds_local" / "Skippy.fwl") == ("Skippy", "skippyhasyourmoney")
    assert not (config / "replaced-worlds").exists()                # nothing to set aside


def test_a_world_that_already_has_the_seed_is_left_alone_every_boot(tmp_path):
    for layout in ("folder", "legacy"):
        config = config_with_world(tmp_path / layout, "skippyhasyourmoney", layout)
        assert "already grows from" in vs.ensure_seed(config, "Skippy", "skippyhasyourmoney", NOW)     # remembers the setting
        before = {str(p): p.read_bytes() for p in config.rglob("*") if p.is_file()}
        assert "already grows from" in vs.ensure_seed(config, "Skippy", "skippyhasyourmoney", NOW)
        assert {str(p): p.read_bytes() for p in config.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("layout", ["folder", "legacy"])
def test_a_world_with_another_seed_is_set_aside_never_deleted(tmp_path, layout):
    config = config_with_world(tmp_path, "someoldseed", layout)
    worlds = config / "worlds_local"
    (worlds / "Skippy_backup_20260101-000000.fwl").write_bytes(b"the game's own backup")
    line = vs.ensure_seed(config, "Skippy", "skippyhasyourmoney", NOW, uid=1)
    assert "old world (seed 'someoldseed') kept in replaced-worlds/20260930T060000Z-Skippy/" in line
    kept = config / "replaced-worlds" / "20260930T060000Z-Skippy"
    assert b"BUILT-BASE" in next(p for p in kept.rglob("*") if p.name in ("_main.1.db2", "Skippy.db")).read_bytes()
    assert vs.read_meta(worlds / "Skippy.fwl") == ("Skippy", "skippyhasyourmoney")
    assert not (worlds / "Skippy").exists() and not (worlds / "Skippy.db").exists()      # gone from where the server looks
    assert (worlds / "Skippy_backup_20260101-000000.fwl").exists()                        # the game's own backups stay
    assert "already grows from" in vs.ensure_seed(config, "Skippy", "skippyhasyourmoney", NOW)   # and now it's stable


def test_the_newest_save_in_a_folder_world_decides(tmp_path):
    config = config_with_world(tmp_path, "oldseed")
    folder = config / "worlds_local" / "Skippy"
    (folder / "_main.2.fwl2").write_bytes(vs.build_fwl("Skippy", "skippyhasyourmoney", 7, version=41))
    assert "already grows from" in vs.ensure_seed(config, "Skippy", "skippyhasyourmoney", NOW)


def test_a_world_it_cannot_read_is_never_touched(tmp_path):
    config = config_with_world(tmp_path, "x")
    (config / "worlds_local" / "Skippy" / "_main.1.fwl2").write_bytes(b"corrupt")
    assert "SKIPPED" in vs.ensure_seed(config, "Skippy", "skippyhasyourmoney", NOW)
    assert (config / "worlds_local" / "Skippy" / "_main.1.db2").exists() and not (config / "replaced-worlds").exists()
    legacy = config_with_world(tmp_path / "b", "x", "legacy")
    (legacy / "worlds_local" / "Skippy.fwl").write_bytes(b"corrupt")
    assert "SKIPPED" in vs.ensure_seed(legacy, "Skippy", "skippyhasyourmoney", NOW)
    assert (legacy / "worlds_local" / "Skippy.db").exists()


def test_keep_and_bad_input_do_nothing(tmp_path):
    config = config_with_world(tmp_path, "someoldseed")
    assert "leaving world" in vs.ensure_seed(config, "Skippy", " Keep ", NOW)
    for world in ("", "../x", "a/b", "a\\b", "..", ".", " Skippy", "x" * 100):
        assert "SKIPPED" in vs.ensure_seed(config, world, "skippyhasyourmoney", NOW), world
    assert "SKIPPED" in vs.ensure_seed(config, "Skippy", "   ", NOW)
    assert vs.read_meta(config / "worlds_local" / "Skippy" / "_main.1.fwl2")[1] == "someoldseed"
    assert not (config / "replaced-worlds").exists()


def test_two_runs_in_the_same_second_keep_both_worlds(tmp_path):
    config = config_with_world(tmp_path, "oldseed")
    vs.ensure_seed(config, "Skippy", "seed-a", NOW, uid=1)
    with pytest.raises(FileExistsError):                         # the second would reuse the same folder: stop, don't merge
        vs.ensure_seed(config, "Skippy", "seed-b", NOW, uid=1)
    assert vs.read_meta(config / "worlds_local" / "Skippy.fwl")[1] == "seed-a"


def test_settings_come_from_the_env_file_and_the_default_is_the_captains_seed(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# c\nVALHEIM_WORLD_NAME='Odin'\nVALHEIM_WORLD_SEED=  # blank\nOTHER=1\n", encoding="utf-8")
    assert vs.load_env(env) == {"VALHEIM_WORLD_NAME": "Odin", "VALHEIM_WORLD_SEED": "", "OTHER": "1"}
    assert vs.main(["--env", str(env), "--data", str(tmp_path / "data")]) == 0
    assert vs.read_meta(tmp_path / "data" / "valheim" / "config" / "worlds_local" / "Odin.fwl") == ("Odin", vs.DEFAULT_SEED)
    assert vs.DEFAULT_SEED == "skippyhasyourmoney" == autoconfig.DEFAULTS["VALHEIM_WORLD_SEED"]    # the portal shows what the VM uses
    assert vs.main(["--env", str(tmp_path / "nope"), "--data", str(tmp_path / "d2")]) == 0           # no env file: defaults


def test_it_is_wired_into_the_boot_and_the_metadata():
    root = PATH.parents[0]
    assert 'valheim-seed     = file("${path.module}/valheim_seed.py")' in (root / "main.tf").read_text(encoding="utf-8")
    startup = (root / "startup.sh").read_text(encoding="utf-8")
    assert "meta valheim-seed" in startup and "skippy-valheim-seed --env" in startup
    assert startup.index("skippy-game-backup backup") < startup.index("skippy-valheim-seed --env") < startup.index("up -d")


def test_a_restored_world_is_not_undone_at_the_next_boot(tmp_path):
    """The review's scenario: change the seed, then roll back to the old world. The rollback must survive a reboot."""
    config = config_with_world(tmp_path, "oldseed")
    worlds = config / "worlds_local"
    assert "will be generated from 'newseed'" in vs.ensure_seed(config, "Skippy", "newseed", NOW, uid=1)   # boot 1: applied
    (worlds / "Skippy.fwl").unlink()                                                    # (the game converted it; stand-in for a restore)
    (worlds / "Skippy").mkdir()
    (worlds / "Skippy" / "_main.1.fwl2").write_bytes(vs.build_fwl("Skippy", "oldseed", 7, version=41))   # put back: the old world
    line = vs.ensure_seed(config, "Skippy", "newseed", NOW + timedelta(hours=1), uid=2)                   # boot 2
    assert "already applied" in line and "stays" in line
    assert vs.read_meta(worlds / "Skippy" / "_main.1.fwl2")[1] == "oldseed"              # still the restored world
    assert len(list((config / "replaced-worlds").iterdir())) == 1                       # nothing else was set aside
    line = vs.ensure_seed(config, "Skippy", "thirdseed", NOW + timedelta(hours=2), uid=3)    # the setting changes: now it applies
    assert "will be generated from 'thirdseed'" in line and len(list((config / "replaced-worlds").iterdir())) == 2


def test_a_world_of_another_name_or_no_record_still_gets_the_seed(tmp_path):
    config = config_with_world(tmp_path, "oldseed", world="Odin")
    vs._write_state(config, "Skippy", "newseed")                                       # the record is for a different world
    assert "will be generated" in vs.ensure_seed(config, "Odin", "newseed", NOW, uid=1)
    (config / vs.STATE_FILE).write_text("not json")                                    # a damaged record counts as none
    config2 = config_with_world(tmp_path / "b", "oldseed")
    (config2 / vs.STATE_FILE).write_text("[1, 2]")
    assert "will be generated" in vs.ensure_seed(config2, "Skippy", "newseed", NOW, uid=1)


def test_a_legacy_file_beside_an_empty_folder_is_the_world(tmp_path):
    """Loading a legacy world makes an empty folder at once but converts the file only at the first save: after a hard
    stop the disk holds Skippy.fwl AND an empty Skippy/. The seed change must still apply (checked on the real server)."""
    config = tmp_path / "config"
    vs.ensure_seed(config, "Skippy", "seed-a", NOW, uid=1)
    (config / "worlds_local" / "Skippy").mkdir()                                       # what the server does at load
    line = vs.ensure_seed(config, "Skippy", "seed-b", NOW + timedelta(hours=1), uid=2)
    assert "will be generated from 'seed-b'" in line and "'seed-a'" in line
    assert vs.read_meta(config / "worlds_local" / "Skippy.fwl") == ("Skippy", "seed-b")
    kept = next((config / "replaced-worlds").iterdir())
    assert (kept / "Skippy").is_dir() and (kept / "Skippy.fwl").exists()               # the empty folder goes with the old world


def test_an_empty_folder_alone_is_no_world_and_a_full_unreadable_one_is_left_alone(tmp_path):
    config = tmp_path / "config"
    (config / "worlds_local" / "Skippy").mkdir(parents=True)
    assert vs.world_seed(config / "worlds_local", "Skippy") == ("none", "")
    assert "will be generated" in vs.ensure_seed(config, "Skippy", "seed-a", NOW, uid=1)
    (config / "worlds_local" / "Skippy.fwl").unlink()
    (config / "worlds_local" / "Skippy" / "stray.txt").write_text("x")                 # not empty, and no metadata
    assert vs.world_seed(config / "worlds_local", "Skippy")[0] == "unreadable"
