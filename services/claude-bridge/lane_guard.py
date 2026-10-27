"""The agent's lane (the Captain's rule, 2026-10-23: Private and Auto must "stay in their lanes and stay on-project").

Claude Code runs this before every Edit, Write, MultiEdit, NotebookEdit and Bash call of a Workshop run (a PreToolUse hook the
bridge passes with --settings; hooks apply even with --dangerously-skip-permissions). The LANE is the conversation's working
folder (CLAUDE_PROJECT_DIR: the clone, or the folder opened in place). Reading anywhere is fine; changing anything outside the
lane is refused, with the reason, which Claude Code shows the model so it can say so instead of trying another way.

  * Edit/Write/MultiEdit/NotebookEdit: the file must be inside the lane (or a temp folder).
  * Bash: refused when it moves into another folder (cd/pushd/git -C) or runs a command that changes files (rm, mv, cp,
    mkdir, touch, tee, sed -i, a > redirect, PowerShell's *-Item/Set-Content/Out-File) and names a path outside the lane.
  * SKIPPY_LANE_DEPLOY (the project's primary checkout, the place SkippyTel's skippy-pc services deploy from): it may go there
    to run git pull/fetch/status/log, docker compose and make. Never an edit there.

It is a guard rail for a model that wanders, not a sandbox: a determined command can get past a text check, and the prompt
(workshop_prompt.md) tells it the lane is the Captain's rule. Exit 2 = refused (the reason on stderr); 0 = allowed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_WRITE_CMD = re.compile(r"(?:^|[\s;&|(])(?:rm|rmdir|mv|cp|mkdir|touch|tee|ln|chmod|truncate|dd|install|rsync|del|erase|copy|move|"
                        r"remove-item|set-content|add-content|out-file|new-item|copy-item|move-item|rename-item)(?=\s)", re.I)
_SED_I = re.compile(r"\bsed\s+(?:-[a-z]*i|--in-place)", re.I)
_REDIRECT = re.compile(r"(?<![0-9&])>>?\s*([^\s;&|]+)")
_MOVE = re.compile(r"(?:^|[\s;&|(])(?:cd|pushd|set-location|sl)\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)", re.I)
_GIT_C = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)\s+(\S+)", re.I)
_DEPLOY_OK = re.compile(r"^\s*(?:git\s+(?:pull|fetch|status|log|diff|show|rev-parse)\b|docker(?:-compose|\s+compose)\b|make\b|curl\b|ls\b)", re.I)
_PATH = re.compile(r"(\"[^\"]+\"|'[^']+'|(?<![\w/.-])(?:[A-Za-z]:[\\/]|/[a-zA-Z]/|~[\\/]|\.\.(?:[\\/]|$))[^\s;&|'\"<>]*)")


def norm(path: str, base: str) -> str:
    """A comparable absolute path: msys /d/x → d:/x, ~ → home, relative → under base; lower case, forward slashes, no '..'."""
    p = path.strip().strip("\"'")
    m = re.match(r"^/([a-zA-Z])(/.*|$)", p)
    if m:
        p = f"{m.group(1)}:{m.group(2) or '/'}"
    if p.startswith("~"):
        p = os.path.expanduser("~") + p[1:]
    if not re.match(r"^[A-Za-z]:[\\/]", p) and not p.startswith("/"):
        p = base.rstrip("\\/") + "/" + p
    parts = []
    for part in re.split(r"[\\/]+", p.replace("\\", "/")):
        if part in ("", "."):
            continue
        if part == "..":
            if len(parts) > 1:
                parts.pop()
            continue
        parts.append(part)
    out = "/".join(parts)
    return (out if re.match(r"^[A-Za-z]:", out) else "/" + out).lower()


def inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


class Lane:
    def __init__(self, lane: str, deploy: str = "", temps=None, deny_read=()):
        self.lane = norm(lane, lane)
        self.deny_read = [norm(d, lane) for d in deny_read if isinstance(d, str) and d]      # folders no tool may touch, not even to read
        self.deploy = norm(deploy, lane) if deploy else ""
        temps = temps if temps is not None else [tempfile.gettempdir(), os.environ.get("TEMP", ""), os.environ.get("TMP", ""), "/tmp"]
        self.temps = [norm(t, lane) for t in temps if t]

    def ok_to_write(self, path: str) -> bool:
        p = norm(path, self.lane)
        return inside(p, self.lane) or any(inside(p, t) for t in self.temps) or p.startswith("/dev/")

    def check(self, tool: str, args: dict) -> str | None:
        """None if allowed, else the reason it isn't."""
        if self.deny_read:
            for p in self._touched(tool, args):
                if any(inside(norm(p, self.lane), d) for d in self.deny_read):
                    return f"{p} is in a folder this project may not read or change (it belongs to another run, which this one must not see)."
        if tool in FILE_TOOLS:
            path = str(args.get("file_path") or args.get("notebook_path") or "")
            if path and not self.ok_to_write(path):
                return f"{path} is outside this conversation's folder ({self.lane}); only that folder may be changed."
            return None
        if tool != "Bash":
            return None
        cmd = str(args.get("command") or "")
        for seg in re.split(r"&&|\|\||;|\n", cmd):
            reason = self._segment(seg)
            if reason:
                return reason
        return None

    @staticmethod
    def _touched(tool: str, args: dict):
        """The paths a tool call names: its file arguments, and for a shell command every path-like word and every cd target."""
        for key in ("file_path", "notebook_path", "path", "pattern"):
            if isinstance(args.get(key), str) and re.match(r"^(?:[A-Za-z]:[\\/]|/[a-zA-Z]/|~[\\/])", args[key]):
                yield args[key]
        if tool == "Bash":
            cmd = str(args.get("command") or "")
            yield from _paths(cmd)
            for m in _MOVE.finditer(cmd):
                yield m.group(1).strip("\"'")

    def _segment(self, seg: str) -> str | None:
        for m in _MOVE.finditer(seg):
            where = norm(m.group(1), self.lane)
            if not (inside(where, self.lane) or any(inside(where, t) for t in self.temps)):
                if self.deploy and inside(where, self.deploy):
                    continue                                    # the deploy checkout: what it may run there is checked below
                return f"`{m.group(0).strip()}` leaves this conversation's folder ({self.lane})."
        for m in _GIT_C.finditer(seg):
            where = norm(m.group(1), self.lane)
            if inside(where, self.lane):
                continue
            if self.deploy and inside(where, self.deploy) and m.group(2).lower() in ("pull", "fetch", "status", "log", "diff", "show", "rev-parse"):
                continue
            return f"`git -C {m.group(1)}` works on a repository outside this conversation's folder ({self.lane})."
        if self.deploy and _MOVE.search(seg) is None and any(inside(norm(p, self.lane), self.deploy) for p in _paths(seg)):
            if not _DEPLOY_OK.search(seg) and (_WRITE_CMD.search(seg) or _SED_I.search(seg) or _REDIRECT.search(seg)):
                return f"the primary checkout ({self.deploy}) is only for pulling and deploying, never for changing files."
        writes = _WRITE_CMD.search(seg) or _SED_I.search(seg)
        targets = list(_paths(seg)) if writes else []
        targets += [m.group(1) for m in _REDIRECT.finditer(seg)]
        for t in targets:
            if not self.ok_to_write(t):
                return f"`{seg.strip()[:120]}` changes {t.strip(chr(34) + chr(39))}, outside this conversation's folder ({self.lane})."
        return None


def _paths(seg: str):
    for m in _PATH.finditer(seg):
        p = m.group(1)
        q = p.strip("\"'")
        if re.match(r"^(?:[A-Za-z]:[\\/]|/[a-zA-Z]/|~[\\/]|\.\.)", q):
            yield q


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    lane = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ""
    if not lane:
        return 0
    reason = Lane(lane, os.environ.get("SKIPPY_LANE_DEPLOY", "")).check(str(event.get("tool_name") or ""), event.get("tool_input") or {})
    if reason:
        print(f"Refused by the Captain's lane rule: {reason} If this really needs doing, say so and ask him.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
