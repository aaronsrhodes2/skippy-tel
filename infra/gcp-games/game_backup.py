#!/usr/bin/env python3
"""Per-game backups on the games VM: the flagged files, archived, so a bad day on one game can be rolled back.

TARGETS below is the one list of what matters for each game (paths under the data disk, /srv/games/data).
startup.sh runs `backup` at every boot with the game containers down, so each archive is the state the
session *started* from (the state after the last session, whole and consistent): the rollback point for a bad
day. Archives are `<data>/backups/<game>/<UTC time>-<fingerprint>.tar.gz`. Nothing is archived twice: if the
files are unchanged since the newest archive, nothing is written. The newest KEEP are kept.

The daily disk snapshot (infra/gcp-games/main.tf) stays the disaster copy; these sit on the same disk, so they
protect against a bad day in a game, not against losing the disk.

    game_backup.py backup [game ...]          archive the games (default: all); safe to run any time the game is stopped
    game_backup.py list [game]                what there is
    game_backup.py restore <game> <archive>   put one back (`latest` = the newest); the game must be stopped.
                                              The current files are set aside in backups/<game>/_replaced/ and stay there
                                              until someone deletes them (`list` shows how much room they take), and are put
                                              back if the restore fails part way.
    game_backup.py check <game> <archive>     the checks restore makes, changing nothing (the ops agent runs it first)

The ops agent (ops_agent.py: `backups`, `restore-game`) is how Claude drives it, through the `ops` workflow.
Stdlib only; runs as root (so the ownership inside the archives is kept).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import posixpath
import re
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("/srv/games/data")

# What to protect, per game: the world and the settings that go with it (never the game's own install, which
# is re-downloaded). A path that doesn't exist yet is skipped and named in the output, so a wrong path shows.
TARGETS: dict[str, tuple[str, ...]] = {
    "valheim": ("valheim/config/worlds_local", "valheim/config/adminlist.txt",
                "valheim/config/bannedlist.txt", "valheim/config/permittedlist.txt"),
    "factorio": ("factorio/saves", "factorio/config", "factorio/mods"),
    "satisfactory": ("satisfactory/config/saved",),
    "dragonwilds": ("dragonwilds/RSDragonwilds/Saved/SaveGames", "dragonwilds/RSDragonwilds/Saved/SaveCharacters",
                    "dragonwilds/RSDragonwilds/Saved/Config"),
    # Garry's Mod: what the server writes (sv.db, sandbox saves, addon data) and the addons put there by hand.
    "garrysmod": ("garrysmod/data", "garrysmod/addons"),
    # EverQuest's whole world is its database (characters, everything), plus the keys that go with it.
    "everquest": ("everquest/akk-stack/data/mariadb", "everquest/akk-stack/server/eqemu_config.json",
                  "everquest/akk-stack/.env"),
}
KEEP = {"everquest": 3, "satisfactory": 6}     # the rest: DEFAULT_KEEP (a database and big saves take room)
DEFAULT_KEEP = 10
MIN_FREE = 3 * 1024 ** 3                       # won't start an archive with less than this free on the data disk
_ARCHIVE = re.compile(r"^(\d{8}T\d{6}Z)-([0-9a-f]{8})\.tar\.gz$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _dir(data: Path, game: str) -> Path:
    return data / "backups" / game


def fingerprint(data: Path, targets: list[str]) -> str:
    """Changes when any flagged file is added, removed, resized or touched."""
    h = hashlib.sha1()
    for rel in sorted(targets):
        top = data / rel
        walk = [(str(top.parent), [], [top.name])] if not top.is_dir() or top.is_symlink() else os.walk(top)
        for root, _dirs, files in sorted(walk, key=lambda t: t[0]):
            for name in sorted(files):
                path = Path(root) / name
                try:
                    st = path.lstat()
                except OSError:
                    continue
                h.update(f"{path.relative_to(data).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:8]


def archives(data: Path, game: str) -> list[Path]:
    """This game's archives, oldest first (the name starts with the time)."""
    folder = _dir(data, game)
    return sorted(p for p in folder.iterdir() if _ARCHIVE.match(p.name)) if folder.is_dir() else []


def _rm(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def backup(data: Path, game: str, now: datetime | None = None) -> str:
    if game not in TARGETS:
        return f"backup {game}: no backup targets flagged for this game"
    present = [t for t in TARGETS[game] if os.path.lexists(data / t)]
    missing = [t for t in TARGETS[game] if t not in present]
    note = f" (not there yet: {', '.join(missing)})" if missing else ""
    if not present:
        return f"backup {game}: nothing to archive, none of its files exist yet{note}"
    fp = fingerprint(data, present)
    have = archives(data, game)
    if have and _ARCHIVE.match(have[-1].name).group(2) == fp:
        return f"backup {game}: unchanged since {have[-1].name}{note}"
    if shutil.disk_usage(data).free < MIN_FREE:
        return f"backup {game}: SKIPPED, the data disk has less than {MIN_FREE // 1024 ** 3} GB free"
    folder = _dir(data, game)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder.parent, 0o700)
    name = f"{_stamp(now)}-{fp}.tar.gz"
    part = folder / f"{name}.part"
    try:
        with tarfile.open(part, "w:gz") as tar:
            for rel in present:
                tar.add(data / rel, arcname=rel)
        os.replace(part, folder / name)
    finally:
        if part.exists():
            part.unlink()
    have = archives(data, game)
    for old in have[:max(0, len(have) - KEEP.get(game, DEFAULT_KEEP))]:
        old.unlink()
    size = (folder / name).stat().st_size / 1024 ** 2
    return f"backup {game}: {name} ({size:.1f} MB), {len(archives(data, game))} kept{note}"


def listing(data: Path, game: str | None = None) -> str:
    lines = []
    for g in ([game] if game else sorted(TARGETS)):
        found = archives(data, g)
        lines.append(f"{g}: {len(found)} archive(s); protects {', '.join(TARGETS.get(g, ())) or '(nothing flagged)'}")
        for p in reversed(found):
            when = datetime.strptime(p.name[:16], "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"  {p.name}   {when}   {p.stat().st_size / 1024 ** 2:.1f} MB")
        aside = _dir(data, g) / "_replaced"
        kept = sorted(p for p in aside.iterdir() if p.is_dir()) if aside.is_dir() else []
        if kept:
            mb = sum(f.stat().st_size for d in kept for f in d.rglob("*") if f.is_file()) / 1024 ** 2
            lines.append(f"  set aside by restores (not deleted automatically): {len(kept)} copy(ies), {mb:.1f} MB "
                         f"in backups/{g}/_replaced/")
    return "\n".join(lines)


def _resolve(data: Path, game: str, name: str) -> Path:
    found = archives(data, game)
    if not found:
        raise ValueError(f"{game} has no archives")
    if name == "latest":
        return found[-1]
    if not _SAFE_NAME.match(name):
        raise ValueError(f"bad archive name {name!r}")
    hits = [p for p in found if p.name == name or p.name == f"{name}.tar.gz" or p.name.startswith(name)]
    if len(hits) != 1:
        raise ValueError(f"{name!r} matches {len(hits)} archives of {game}; `list {game}` shows them")
    return hits[0]


def _checked_members(tar: tarfile.TarFile, game: str) -> list[tarfile.TarInfo]:
    """Only plain files and folders inside this game's flagged paths; anything else and nothing is touched."""
    targets = TARGETS[game]
    members = tar.getmembers()
    for m in members:
        path = posixpath.normpath(m.name)                 # tar names always use "/"
        inside = any(path == t or path.startswith(t + "/") for t in targets)
        if not inside or m.name.startswith("/") or ".." in m.name.split("/") or not (m.isfile() or m.isdir()):
            raise ValueError(f"the archive has {m.name!r}, which isn't one of {game}'s backup targets; refusing")
    return members


def _extract(tar: tarfile.TarFile, member: tarfile.TarInfo, dest: Path) -> None:
    try:
        tar.extract(member, path=dest, filter="tar")    # keeps owners (the database's, the game user's)
    except TypeError:                                    # an older Python with no extraction filters
        tar.extract(member, path=dest)


def check(data: Path, game: str, name: str) -> str:
    """Everything restore() would check before it touches a file, and nothing else: is there such an archive,
    and does it hold only this game's flagged files? The ops agent runs this before it stops the game."""
    if game not in TARGETS:
        raise ValueError(f"no backup targets flagged for {game!r}")
    archive = _resolve(data, game, name)
    with tarfile.open(archive, "r:gz") as tar:
        members = _checked_members(tar, game)
    return f"check {game}: {archive.name} is fine to restore ({sum(1 for m in members if m.isfile())} file(s))"


def restore(data: Path, game: str, name: str, now: datetime | None = None) -> str:
    if game not in TARGETS:
        raise ValueError(f"no backup targets flagged for {game!r}")
    archive = _resolve(data, game, name)
    with tarfile.open(archive, "r:gz") as tar:
        members = _checked_members(tar, game)             # validated before anything moves
        aside = _dir(data, game) / "_replaced" / _stamp(now)
        existed = [rel for rel in TARGETS[game] if os.path.lexists(data / rel)]
        moved: list[str] = []
        try:
            for rel in existed:
                (aside / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(data / rel), str(aside / rel))
                moved.append(rel)
            for m in members:
                _extract(tar, m, data)
        except BaseException:
            # Undo. A target that was moved aside, or that wasn't there before, may hold half-extracted files: drop
            # those. A target that existed but was never moved is still the live original: never touch it.
            for rel in TARGETS[game]:
                if (rel in moved or rel not in existed) and os.path.lexists(data / rel):
                    _rm(data / rel)
            for rel in moved:
                shutil.move(str(aside / rel), str(data / rel))
            raise
    return (f"restore {game}: {archive.name} is back in place; the files it replaced are in "
            f"{aside.relative_to(data).as_posix()}/ ({len(moved)} item(s), kept until someone deletes them)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, default=DATA)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup").add_argument("games", nargs="*")
    sub.add_parser("list").add_argument("game", nargs="?")
    for name in ("restore", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("game")
        sp.add_argument("archive")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "backup":
            failed = False
            for game in args.games or sorted(TARGETS):
                try:
                    print(backup(args.data, game))
                except Exception as exc:   # noqa: BLE001 — one game's failure mustn't stop the others (or the boot)
                    failed = True
                    print(f"backup {game}: FAILED: {exc}", file=sys.stderr)
            return 1 if failed else 0
        if args.cmd == "list":
            print(listing(args.data, args.game))
        elif args.cmd == "check":
            print(check(args.data, args.game, args.archive))
        else:
            print(restore(args.data, args.game, args.archive))
    except (ValueError, OSError, tarfile.TarError) as exc:
        print(f"{args.cmd} FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
