#!/usr/bin/env python3
"""Grows Valheim's world from a chosen seed. Runs on the games VM at boot, just before the Valheim
container starts (startup.sh fetches it from metadata `valheim-seed`, so a deploy updates it).

Valheim has no "seed" flag, but a world is a small metadata file (`<World>.fwl`: name, seed, ids) and
the server builds the map from the seed in it: with no save next to the file it generates a fresh
world, which is what a brand-new world does. So to (re)start a world from a seed, this writes that
one file. Verified against Valheim 1.0.16 (Sep 2026): the server loads the lone .fwl, converts it to
the folder format (`<World>/_main.1.fwl2`) on its first save, and keeps the seed.

If the world already grows from the wanted seed, nothing happens (every boot, harmlessly). If it differs,
the old world is set aside, never deleted, in `replaced-worlds/<UTC time>-<World>/` beside `worlds_local`,
then the new metadata is written. A world this can't read is left alone.

One small state file, `.seed-state.json` beside `worlds_local`, remembers which setting was last applied to
which world. It exists so that a world put back on purpose (`restore-game`, or a copied-in import) isn't
undone at the next boot: a world whose seed differs from a setting that was ALREADY applied is left alone;
only a setting that has changed since (or one never applied, like the first boot after this shipped)
replaces the world.

Settings (from /srv/games/.env, written by the portal):
  VALHEIM_WORLD_NAME   which world the server loads (default Skippy)
  VALHEIM_WORLD_SEED   the seed text (default DEFAULT_SEED); `keep` = leave the world alone
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_WORLD = "Skippy"
DEFAULT_SEED = "skippyhasyourmoney"            # portal/autoconfig.py mirrors it (a test keeps them equal)
KEEP = "keep"
FILE_VERSION = 36                              # legacy metadata version; the server upgrades it on its first save
WORLD_GEN_VERSION = 2                          # what the 1.0 server itself gives a new world
SERVER_UID = 1000                              # the lloesche image runs Valheim as this user


def stable_hash(text: str) -> int:
    """Valheim's string.GetStableHashCode(): a djb2 variant, two characters per step, as a signed int32."""
    num = num2 = 5381
    i = 0
    while i < len(text) and text[i] != "\0":
        num = (((num << 5) + num) ^ ord(text[i])) & 0xFFFFFFFF
        if i == len(text) - 1 or text[i + 1] == "\0":
            break
        num2 = (((num2 << 5) + num2) ^ ord(text[i + 1])) & 0xFFFFFFFF
        i += 2
    result = (num + (num2 * 1566083941 & 0xFFFFFFFF)) & 0xFFFFFFFF
    return result - (1 << 32) if result >= (1 << 31) else result


def _string(value: str) -> bytes:
    """A .NET BinaryWriter string: 7-bit-encoded byte length, then UTF-8."""
    data = value.encode("utf-8")
    n, prefix = len(data), b""
    while True:
        byte = n & 0x7F
        n >>= 7
        prefix += bytes([byte | (0x80 if n else 0)])
        if not n:
            return prefix + data


def _read_string(buf: bytes, pos: int) -> tuple[str, int]:
    length = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        length |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
        if shift > 28:
            raise ValueError("bad string length")
    if pos + length > len(buf):
        raise ValueError("string runs past the end")
    return buf[pos:pos + length].decode("utf-8"), pos + length


def build_fwl(name: str, seed_name: str, uid: int, version: int = FILE_VERSION,
              world_gen_version: int = WORLD_GEN_VERSION) -> bytes:
    """A world's metadata with no save behind it. (Layout decoded from real worlds; the bytes for
    goodwill.fwl, a v33 world from Steam, are rebuilt exactly by the tests.)"""
    body = (struct.pack("<i", version) + _string(name) + _string(seed_name) + struct.pack("<i", stable_hash(seed_name))
            + struct.pack("<q", uid) + struct.pack("<i", world_gen_version))
    if version >= 33:
        body += b"\x00" + struct.pack("<i", 0)       # "has a save": no; starting keys and modifiers: none
    return struct.pack("<i", len(body)) + body


def read_meta(path: Path) -> tuple[str, str] | None:
    """(world name, seed text) from a .fwl (legacy) or _main.N.fwl2 (1.0), or None if it isn't one."""
    try:
        data = path.read_bytes()
        length, version = struct.unpack_from("<ii", data)
        if length != len(data) - 4 or not 1 <= version <= 1000:
            return None
        name, pos = _read_string(data, 8)
        seed, _ = _read_string(data, pos)
        return name, seed
    except (OSError, ValueError, IndexError, struct.error, UnicodeDecodeError):
        return None


_FWL2 = re.compile(r"_main\.(\d+)\.fwl2$")


def _newest_folder_meta(folder: Path) -> Path | None:
    saves = [(int(m.group(1)), p) for p in folder.iterdir() if (m := _FWL2.fullmatch(p.name))]
    return max(saves)[1] if saves else None


def world_seed(worlds: Path, world: str) -> tuple[str, str]:
    """('none', '') no world yet · ('seed', text) the world's seed · ('unreadable', why)."""
    folder, legacy = worlds / world, worlds / f"{world}.fwl"

    def from_legacy() -> tuple[str, str]:
        meta = read_meta(legacy)
        return ("seed", meta[1]) if meta else ("unreadable", f"{legacy.name} isn't readable")

    if folder.is_dir():                              # 1.0 keeps a world in a folder and converts old files into it
        newest = _newest_folder_meta(folder)
        meta = read_meta(newest) if newest else None
        if meta:
            return "seed", meta[1]
        # Loading a legacy world makes an EMPTY folder at once but only converts the file at the first save, so a
        # hard stop before then leaves the seed-only .fwl beside an empty folder: the .fwl is the world.
        if os.path.lexists(legacy):
            return from_legacy()
        if not any(folder.iterdir()):
            return "none", ""                        # an empty folder is nothing
        return "unreadable", f"{folder.name}/ has no readable metadata"
    if os.path.lexists(legacy):
        return from_legacy()
    return "none", ""


STATE_FILE = ".seed-state.json"


def _read_state(config: Path) -> dict:
    try:
        state = json.loads((config / STATE_FILE).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(config: Path, world: str, setting: str) -> None:
    config.mkdir(parents=True, exist_ok=True)
    path = config / STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"world": world, "setting": setting}), encoding="utf-8")
    os.replace(tmp, path)
    _own(path)


def _world_files(worlds: Path, world: str) -> list[Path]:
    """The world itself. The game's own `<World>_backup_*` copies stay where they are."""
    names = [world, f"{world}.fwl", f"{world}.db", f"{world}.fwl.old", f"{world}.db.old"]
    return [worlds / n for n in names if os.path.lexists(worlds / n)]


def _own(path: Path) -> None:
    if hasattr(os, "chown"):
        try:
            os.chown(path, SERVER_UID, SERVER_UID)
        except OSError:
            pass


def safe_world_name(world: str) -> bool:
    """The name becomes a file name under worlds_local; nothing that could climb out of it."""
    return bool(world) and world == world.strip() and not re.search(r'[\\/\0]|^\.\.?$', world) and len(world) < 100


def ensure_seed(config: Path, world: str, seed: str, now: datetime | None = None, uid: int | None = None) -> str:
    """Make `world` grow from `seed`. Returns one line saying what happened (or why nothing did)."""
    if seed.strip().lower() == KEEP:
        return f"seed: '{KEEP}', leaving world {world!r} alone"
    if not safe_world_name(world):
        return f"seed: SKIPPED, world name {world!r} can't be used as a file name"
    if not seed.strip() or "\0" in seed:
        return "seed: SKIPPED, the seed is blank or has a NUL in it"
    worlds = config / "worlds_local"
    state, have = world_seed(worlds, world)
    if state == "unreadable":
        return f"seed: SKIPPED, world {world!r} left as it is ({have})"
    if state == "seed" and have == seed:
        _write_state(config, world, seed)             # remembered, so a later restore of another world is honored
        return f"seed: world {world!r} already grows from {seed!r}"
    applied = _read_state(config)
    if state == "seed" and applied.get("world") == world and applied.get("setting") == seed:
        return (f"seed: world {world!r} has seed {have!r}, not the setting {seed!r}, but that setting was already "
                "applied to it before, so it stays (restored or imported on purpose?). Change the setting to start a new world")

    moved = ""
    if state == "seed":
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        aside = config / "replaced-worlds" / f"{stamp}-{world}"
        aside.mkdir(parents=True)                    # exists already = a second run in the same second: stop, keep both
        for path in _world_files(worlds, world):
            shutil.move(str(path), str(aside / path.name))
        _own(aside.parent)
        _own(aside)
        moved = f"; old world (seed {have!r}) kept in {aside.relative_to(config).as_posix()}/"
    worlds.mkdir(parents=True, exist_ok=True)
    target = worlds / f"{world}.fwl"
    tmp = worlds / f"{world}.fwl.tmp"
    tmp.write_bytes(build_fwl(world, seed, secrets.randbits(31) if uid is None else uid))
    os.replace(tmp, target)
    for path in (worlds, target):
        _own(path)
    _write_state(config, world, seed)
    return f"seed: world {world!r} will be generated from {seed!r}{moved}"


def load_env(path: Path) -> dict[str, str]:
    """KEY=value lines; the portal writes values quoted ('...'), which are literal."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        key, sep, val = line.partition("=")
        if not sep or key.lstrip().startswith("#"):
            continue
        val = val.strip()
        if val[:1] in ("'", '"') and val.find(val[0], 1) != -1:
            val = val[1:val.find(val[0], 1)]
        else:
            val = "" if val.startswith("#") else val.split(" #")[0].strip()     # `KEY=  # note` is blank, as in Compose
        out[key.strip()] = val
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", type=Path, default=Path("/srv/games/.env"))
    ap.add_argument("--data", type=Path, default=Path("/srv/games/data"))
    args = ap.parse_args(argv)
    env = load_env(args.env)
    world = env.get("VALHEIM_WORLD_NAME") or DEFAULT_WORLD
    seed = env.get("VALHEIM_WORLD_SEED") or DEFAULT_SEED
    print(ensure_seed(args.data / "valheim" / "config", world, seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
