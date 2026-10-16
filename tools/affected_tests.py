#!/usr/bin/env python3
"""Run just the tests a change can break, before pushing. CI stays the full gate.

    python3 tools/affected_tests.py                    # what differs from origin/main (committed, uncommitted, untracked)
    python3 tools/affected_tests.py --list             # show the plan, run nothing
    python3 tools/affected_tests.py --files a.py b.md  # pretend exactly these changed
    python3 tools/affected_tests.py --full             # the two CI commands, whatever changed
    python3 tools/affected_tests.py --base main        # compare with another ref

The private Skippy runs this before every push (tools/loop_timer.py times what that saves). The rule that keeps it honest: a change
may get a NARROWER check than CI only when the mapper knows what it can break. Anything it doesn't (`requirements*.txt`, a Dockerfile,
`conftest.py`, `.github/**`, `pytest.ini`/`pyproject.toml`, `portal/catalog.py`, an unknown file nothing names, or a base it can't
find) gets the FULL suite: the same two commands as CI.

How a changed file finds its tests:
  1. a file inside a test directory                      -> that directory
  2. portal/, memory/, mentat/, tools/, services/<name>/ -> that area's own tests (flask-api in its own session: other requirements)
  3. any test file that NAMES the file (its file name, and its folder's name when it has one: `REPO / "portal" / "telemetry.py"`):
     this is how a docs page, a shell script, a compose file or a module a test loads by path reaches the tests that read it
  4. docs and images nothing names                       -> nothing to run
  5. anything else                                       -> FULL
Changed .py files are also compiled and changed .sh files get `bash -n`, whatever else applies (that is what CI's test.yml does too).

pytest-xdist is used (`-n auto --dist loadfile`: one test file stays on one worker) when it is importable and the selection is big
enough to repay starting workers; otherwise plain pytest. Exit status is pytest's (the first non-zero session), 1 when a syntax check
fails, 2 for usage or git trouble.
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The two CI commands (.github/workflows/deploy.yml `test` job). tools/tests/test_affected_tests.py fails if CI names a directory
# that is not here, so "FULL" can never become narrower than the gate.
MAIN = ("portal/tests", "memory/tests", "tools/tests", "services/home-reporter/tests", "services/claude-bridge/tests", "services/wa-trickle/tests", "mentat/tests")
FLASK = ("services/flask-api/tests",)             # its `app` package can't share a session with the portal's app.py, and it has its own requirements
AREAS = {"portal": "portal/tests", "memory": "memory/tests", "mentat": "mentat/tests", "tools": "tools/tests"}
GENERIC_NAMES = {"app.py", "__init__.py", "config.py", "main.py", "version.py", "utils.py", "README.md", "index.py"}
XDIST_MIN_FILES = 6                             # below this, starting workers costs more than it saves
SKIP_DIRS = {".git", ".claude", "node_modules", "__pycache__", ".pytest_cache", "state", ".venv", "venv"}
CROSS_CUTTING_NAMES = {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}
CROSS_CUTTING_PATHS = {"portal/catalog.py"}      # the game list every suite and the infra read
DOC_SUFFIXES = {".md", ".txt", ".rst", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp"}


def posix(path) -> str:
    text = str(path).replace("\\", "/")
    return text[2:] if text.startswith("./") else text


# ── what changed ─────────────────────────────────────────────────────────────────

def git(root: Path, *args: str) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return done.returncode, done.stdout if done.returncode == 0 else (done.stderr or done.stdout)


def changed_files(root: Path, base: str | None) -> tuple[list[str] | None, str]:
    """(files, description), or (None, why) when what changed can't be told. Files = everything that differs from the merge-base
    with `base` (commits on this branch AND uncommitted edits, both sides of a rename, deletions) plus untracked files."""
    candidates = [base] if base else ["origin/main", "main"]
    for ref in candidates:
        code, out = git(root, "merge-base", ref, "HEAD")
        if code == 0 and out.strip():
            mb = out.strip()
            break
    else:
        return None, f"base {' / '.join(candidates)} not found (no such ref, or not a git checkout)"
    code, tracked = git(root, "diff", "--name-only", "--no-renames", "-z", mb)
    code2, untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if code or code2:
        return None, f"git could not list the changes: {(tracked if code else untracked).strip()[:200]}"
    files = sorted({f for f in (tracked + "\0" + untracked).split("\0") if f})
    return files, f"{len(files)} changed vs {ref} ({mb[:7]})"


# ── where the tests are, and which of them name a file ──────────────────────────────

def test_dirs(root: Path) -> list[str]:
    """Every directory holding tests: the CI ones, plus any other `tests` folder with test_*.py (services/music-gate/tests)."""
    found = set(MAIN) | set(FLASK)
    for here, subdirs, names in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]            # never descend into .git, node_modules, other checkouts' litter
        if Path(here).name == "tests" and any(n.startswith("test_") and n.endswith(".py") for n in names):
            found.add(Path(here).relative_to(root).as_posix())
    return sorted(found)


def session_of(test_path: str, dirs: list[str]) -> str:
    """Which pytest session a test path runs in. CI's main session, flask-api's own, and any other test folder alone
    (an unlisted folder can share module names with the others, and CI doesn't run it at all)."""
    for d in MAIN:
        if test_path == d or test_path.startswith(d + "/"):
            return "main"
    for d in FLASK:
        if test_path == d or test_path.startswith(d + "/"):
            return "flask-api"
    for d in dirs:
        if test_path == d or test_path.startswith(d + "/"):
            return d.split("/tests")[0].rsplit("/", 1)[-1] or d
    return "main"


class Readers:
    """Test files that name a changed file. A file `a/b/c.py` is named by a test whose text holds `c.py` AND `b` (just `c.py` for a
    file at the root): enough to catch `REPO / "portal" / "telemetry.py"` and `"docs/cicd.md"` without matching every `app.py`."""

    def __init__(self, root: Path, dirs: list[str]):
        self.root = root
        self.files: dict[str, str] = {}
        for d in dirs:
            for path in (root / d).rglob("*.py"):
                rel = path.relative_to(root).as_posix()
                if "__pycache__" not in rel:
                    try:
                        self.files[rel] = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass

    def naming(self, rel: str) -> set[str]:
        parts = rel.split("/")
        base, parent = parts[-1], (parts[-2] if len(parts) > 1 else "")
        word = re.compile(r"(?<![\w-])" + re.escape(base) + r"(?![\w])")
        # A name every folder has (app.py, config.py, README.md) must stand right after its folder (`portal/app.py`,
        # `"portal" / "app.py"`): "app.py" and "portal" somewhere in the same test would name half the repo.
        near = re.compile(re.escape(parent) + r"""["'\s/\\]{1,8}""" + re.escape(base)) if parent and base in GENERIC_NAMES else None
        hits = set()
        for path, text in self.files.items():
            if path == rel or not word.search(text) or (parent and parent not in text) or (near and not near.search(text)):
                continue
            hits.add(path if Path(path).name.startswith("test_") else path.rsplit("/", 1)[0])
        return hits


# ── the plan ─────────────────────────────────────────────────────────────────────

@dataclass
class Plan:
    full: bool = False
    reason: str = ""                                            # why FULL, when it is
    targets: dict[str, set[str]] = field(default_factory=dict)  # session -> pytest paths
    why: dict[str, str] = field(default_factory=dict)           # changed file -> one phrase
    syntax_py: list[str] = field(default_factory=list)
    syntax_sh: list[str] = field(default_factory=list)

    def add(self, session: str, path: str) -> None:
        self.targets.setdefault(session, set()).add(path)


def _inside(rel: str, d: str) -> bool:
    return rel == d or rel.startswith(d + "/")


def classify(rel: str, root: Path, readers: Readers, dirs: list[str]) -> tuple[str, set[str], str]:
    """One changed file -> (kind, test paths, phrase). kind: ignore | full | tests | nothing."""
    parts = rel.split("/")
    name = parts[-1]
    if parts[0] in SKIP_DIRS or name.endswith(".pyc"):
        return "ignore", set(), "not source"
    if (name.startswith("requirements") and name.endswith(".txt")) or name in CROSS_CUTTING_NAMES or rel in CROSS_CUTTING_PATHS \
            or parts[0] == ".github" or name.startswith("Dockerfile") or name.startswith("docker-compose"):
        return "full", set(), "cross-cutting: it can change how every suite runs"
    for d in dirs:
        if _inside(rel, d):
            return "tests", {d}, f"inside {d}"
    paths: set[str] = set()
    owned = ""
    if parts[0] in AREAS:
        paths.add(AREAS[parts[0]])
        owned = parts[0]
    elif parts[0] == "services" and len(parts) > 2:
        service = f"services/{parts[1]}"
        if (root / service / "tests").is_dir():
            paths.add(f"{service}/tests")
            owned = service
        elif parts[1] == "flask-api":
            paths.add(FLASK[0])
            owned = service
    named = readers.naming(rel)
    paths |= named
    if paths:
        extra = f" + {len(named)} test file(s) that name it" if named and owned else ""
        return "tests", paths, (owned + " tests" if owned else "named by tests") + extra
    if Path(name).suffix.lower() in DOC_SUFFIXES or parts[0] == "docs":
        return "nothing", set(), "docs/media: no test names it"
    if parts[0] == "services":
        return "nothing", set(), f"services/{parts[1]}: it has no tests, and none names it"
    return "full", set(), "no rule for it and no test names it"


def make_plan(files: list[str], root: Path) -> Plan:
    plan = Plan()
    dirs = test_dirs(root)
    readers = Readers(root, dirs)
    for rel in files:
        kind, paths, phrase = classify(rel, root, readers, dirs)
        plan.why[rel] = phrase if kind != "full" else f"FULL ({phrase})"
        if kind == "full":
            plan.full, plan.reason = True, plan.reason or f"{rel}: {phrase}"
        for path in paths:
            plan.add(session_of(path, dirs), path)
        if rel.endswith(".py") and (root / rel).is_file():
            plan.syntax_py.append(rel)
        if rel.endswith(".sh") and (root / rel).is_file():
            plan.syntax_sh.append(rel)
    if plan.full:
        plan.targets = {"main": set(MAIN), "flask-api": set(FLASK)}
    plan.targets = {s: prune(p) for s, p in plan.targets.items()}
    return plan


def prune(paths: set[str]) -> set[str]:
    """Drop a path that lies inside another selected one (`portal/tests` already runs `portal/tests/test_x.py`)."""
    return {p for p in paths if not any(q != p and p.startswith(q + "/") for q in paths)}


def count_tests(root: Path, paths: set[str]) -> int:
    total = 0
    for p in paths:
        target = root / p
        total += 1 if target.is_file() else len(list(target.rglob("test_*.py"))) if target.is_dir() else 0
    return total


def pytest_argv(paths: set[str], xdist: bool, files: int) -> list[str]:
    argv = [sys.executable, "-m", "pytest", *sorted(paths), "-q", "--tb=short", "-p", "no:cacheprovider"]
    if xdist and files >= XDIST_MIN_FILES:
        argv += ["-n", "auto", "--dist", "loadfile"]
    return argv


def has_xdist() -> bool:
    return importlib.util.find_spec("xdist") is not None


# ── checks and running ───────────────────────────────────────────────────────────

def syntax_errors(root: Path, plan: Plan) -> list[str]:
    bad = []
    for rel in plan.syntax_py:
        try:
            compile((root / rel).read_bytes(), rel, "exec")
        except (SyntaxError, ValueError) as exc:
            bad.append(f"{rel}: {exc}")
    for rel in plan.syntax_sh:
        try:
            done = subprocess.run(["bash", "-n", rel], cwd=root, capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue                                              # no bash here: CI's bash -n step still checks it
        if done.returncode:
            bad.append(f"{rel}: {done.stderr.strip()[:200]}")
    return bad


def render_plan(plan: Plan, described: str, xdist: bool, root: Path) -> list[str]:
    out = [f"affected_tests: {described}"]
    shown = list(plan.why.items())
    for rel, phrase in shown[:8]:
        out.append(f"  {rel}  ->  {phrase}")
    if len(shown) > 8:
        out.append(f"  ... and {len(shown) - 8} more")
    if plan.full:
        out.append(f"FULL suite (the two CI commands): {plan.reason}")
    if not plan.targets:
        out.append("no tests to run for this change")
    for session in sorted(plan.targets, key=lambda s: (s != "main", s)):
        paths = sorted(plan.targets[session])
        n = count_tests(root, plan.targets[session])
        out.append(f"  {session}: {' '.join(paths[:6])}{' ...' if len(paths) > 6 else ''}  [{n} test file(s)"
                   + (", xdist" if xdist and n >= XDIST_MIN_FILES else "") + "]")
    return out


def run(plan: Plan, root: Path, xdist: bool, out=print, call=subprocess.call) -> int:
    """Syntax checks, then each session in turn (all of them, even after one fails: the person sees every red at once).
    `call(argv, cwd=...)` is the one place pytest is started, so a test can stand in for it."""
    started = time.time()
    bad = syntax_errors(root, plan)
    if bad:
        out("affected_tests: SYNTAX ERROR, nothing run:")
        for line in bad:
            out(f"  {line}")
        return 1
    worst = 0
    results = []
    for session in sorted(plan.targets, key=lambda s: (s != "main", s)):
        paths = plan.targets[session]
        argv = pytest_argv(paths, xdist, count_tests(root, paths))
        out(f"--- {session}: {' '.join(['pytest', *sorted(paths)])}")
        code = call(argv, cwd=root)
        if code == 5:                                             # pytest's "no tests collected": an empty folder is not a failure
            code = 0
        results.append((session, code, argv))
        worst = worst or code
    took = time.time() - started
    if worst == 0:
        out(f"affected_tests: OK ({', '.join(s for s, _, _ in results) or 'syntax only'}) in {took:.0f}s. "
            "CI still runs the full suite on the push.")
    else:
        failed = [(s, c, a) for s, c, a in results if c]
        out(f"affected_tests: FAILED in {took:.0f}s: " + ", ".join(f"{s} (exit {c})" for s, c, _ in failed))
        out("  re-run just that: python -m " + " ".join(failed[0][2][2:]))
    return worst


def main(argv: list[str] | None = None, call=subprocess.call) -> int:
    ap = argparse.ArgumentParser(prog="affected_tests.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", help="the ref to compare with (default origin/main, then main)")
    ap.add_argument("--files", nargs="+", metavar="PATH", help="treat exactly these (repo-relative) files as the change")
    ap.add_argument("--list", action="store_true", help="print the plan and run nothing")
    ap.add_argument("--full", action="store_true", help="run the two CI commands whatever changed")
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)       # a test points this at a temporary repo
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    xdist = has_xdist()

    if args.full:
        files, described = [], "--full"
    elif args.files:
        files, described = [posix(f) for f in args.files], f"{len(args.files)} file(s) given with --files"
    else:
        files, described = changed_files(root, args.base)
    plan = make_plan(files or [], root)
    if args.full or files is None:                              # --full, or can't tell what changed: the safe answer is everything
        plan.full, plan.reason = True, "--full" if args.full else described
        plan.targets = {"main": set(MAIN), "flask-api": set(FLASK)}
    if files is not None and not files and not args.full:
        print(f"affected_tests: {described}: nothing differs from the base, nothing to test")
        return 0
    for line in render_plan(plan, described, xdist, root):
        print(line)
    if not xdist:
        print("  (pytest-xdist isn't installed: running serially. `pip install pytest-xdist` for -n auto --dist loadfile)")
    return 0 if args.list else run(plan, root, xdist, call=call)


if __name__ == "__main__":
    sys.exit(main())
