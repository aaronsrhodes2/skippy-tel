"""New folder (the Captain's design, 2026-10-23; no GitHub since 2026-10-25, docs/no-github.md): a new project for the agent is a
folder directly under the development root (D:\\Aaron\\development\\<name>), a git repository in it (main, a short CLAUDE.md, a
first commit), and its home: a bare repository D:\\trunk\\git\\<name>.git (GIT_HOME), its origin, pushed to. When the home can't
be made (the D: drive away), the folder is still made and a marker file (.skippy-remote-pending) says the home is owed; retry()
settles it (the bridge tries again whenever a conversation starts in that folder).

Everything runs as the Captain on skippy-pc, git with his identity. Nothing here talks to GitHub.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

OWNER = os.getenv("WORKSHOP_GITHUB_OWNER", "aaronsrhodes2")       # only the project id's owner/name shape (projects.py)
GIT_HOME = os.getenv("SKIPPY_GIT_HOME", "D:/trunk/git")            # the house's bare repositories (docs/no-github.md)
MARKER = ".skippy-remote-pending"
_NAME = re.compile(r"[a-z][a-z0-9-]{1,38}[a-z0-9]")
CLAUDE_MD = """# {name}

A project in the Captain's development folder (D:\\Aaron\\development\\{name}), made from the portal's agent chat on {day}.
Home: D:\\trunk\\git\\{name}.git (its origin; pushed there, never to GitHub).

## What it is

(Say what this project is for, the first time you know.)

## How to run and test it

(Fill in with the first working command.)

This project follows D:\\Aaron\\development\\PLATINUM_RULES.md.
"""


class Refused(ValueError):
    """A bad or taken name: the message says which."""


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    if not _NAME.fullmatch(s):
        raise Refused("A folder name is 3 to 40 letters, digits or hyphens, starting with a letter.")
    return s


def _run(cmd: list[str], cwd: Path, env: dict | None, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL, env=env)


def create(root: str, name: str, env: dict | None = None, git_home: str | None = None) -> dict:
    """Make the folder, its repository and its home (a bare repository in GIT_HOME). {"name", "path", "repo", "remote": "created"|"pending",
    "why"}. Raises Refused for a bad or taken name; anything after the folder exists is reported, never raised."""
    name = slug(name)
    path = Path(root) / name
    if path.exists():
        raise Refused(f"D:\\Aaron\\development\\{name} already exists: open it from the folder list instead.")
    path.mkdir(parents=False)
    (path / "CLAUDE.md").write_text(CLAUDE_MD.format(name=name, owner=OWNER, day=time.strftime("%Y-%m-%d")), encoding="utf-8")
    for cmd in (["git", "init", "-b", "main"], ["git", "add", "CLAUDE.md"], ["git", "commit", "-m", f"Start {name}"]):
        done = _run(cmd, path, env)
        if done.returncode != 0:
            return {"name": name, "path": str(path), "repo": f"{OWNER}/{name}", "remote": "pending",
                    "why": f"git said: {(done.stderr or done.stdout).strip()[:300]}"}
    return {"name": name, "path": str(path), "repo": f"{OWNER}/{name}", **remote(path, name, env, git_home)}


def remote(path: Path, name: str, env: dict | None = None, git_home: str | None = None) -> dict:
    """Make (or, if it already exists, reuse) GIT_HOME/<name>.git, make it origin, and push main. The marker file records a failure."""
    home = Path(git_home or GIT_HOME)
    bare = home / f"{name}.git"
    why = ""
    if not bare.is_dir():
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            why = f"the git home {home} isn't there: {e}"
        else:
            made = _run(["git", "init", "--bare", "-b", "main", str(bare)], home, env)
            if made.returncode != 0:
                why = (made.stderr or made.stdout).strip()[:300]
    if not why:
        url = bare.as_posix()
        have = _run(["git", "remote", "get-url", "origin"], path, env)
        _run(["git", "remote", "set-url" if have.returncode == 0 else "add", "origin", url], path, env)
        pushed = _run(["git", "push", "-u", "origin", "main"], path, env, 120)
        if pushed.returncode == 0:
            _clear(path)
            return {"remote": "created", "why": ""}
        why = (pushed.stderr or pushed.stdout).strip()[:300]
    (path / MARKER).write_text(json.dumps({"repo": str(bare), "why": why, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    _exclude_marker(path)
    return {"remote": "pending", "why": why}


def pending(path: str | Path) -> bool:
    return (Path(path) / MARKER).is_file()


def retry(path: str | Path, env: dict | None = None, git_home: str | None = None) -> dict | None:
    """Settle a folder's owed home repository; None when nothing was owed."""
    path = Path(path)
    if not pending(path):
        return None
    return remote(path, path.name, env, git_home)


def _clear(path: Path) -> None:
    try:
        (path / MARKER).unlink()
    except FileNotFoundError:
        pass


def _exclude_marker(path: Path) -> None:
    """Keep the marker out of commits (.git/info/exclude: local, never pushed)."""
    exclude = path / ".git" / "info" / "exclude"
    try:
        text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        if MARKER not in text:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + MARKER + "\n", encoding="utf-8")
    except OSError:
        pass
