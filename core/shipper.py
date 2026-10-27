"""Ship a machine's important lines to the portal's log intake (docs/status-design.md, Part 2). Stdlib only; used by skippy-core's reporter
(core/reporter/reporter.py) and by the starter on skippy-pc (tools/skippy_starter.py), which import this one file.

What it ships: a container that DIED or keeps restarting (a `crash`, with its last lines attached), and the error-looking lines containers
print (traceback, error, fatal, out of memory ...), a few per container per round. Everything is written to a local spool file BEFORE it is sent
and removed only after the portal accepted it, so what a crash said survives the crash, the network and the portal being down.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ERRORISH = re.compile(r"\b(error|errors|traceback|exception|critical|fatal|panic|segfault|out of memory|oom|killed)\b", re.I)
EXIT_CODE = re.compile(r"Exited \((\d+)\)")
MAX_PER_CONTAINER = 5            # error lines per container per round
MAX_SPOOL_BYTES = 5_000_000      # a long outage drops the oldest, never fills the disk
MAX_BATCH = 400
TAIL_LINES = 20


class Shipper:
    def __init__(self, host: str, state_dir: Path, run, post, clock=time.time, prefix: str = "docker/"):
        """run(cmd: list[str], timeout) -> str (docker's stdout); post(records: list[dict]) -> bool (the portal accepted them)."""
        self.host, self.dir, self.run, self.post, self.clock, self.prefix = host, Path(state_dir), run, post, clock, prefix
        self.dir.mkdir(parents=True, exist_ok=True)
        self.memory = self._read("memory.json", {"seen": {}, "since": None})

    # ── small state files ───────────────────────────────────────────────────────
    def _read(self, name: str, default):
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _write(self, name: str, data) -> None:
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.dir / name)

    # ── what happened ────────────────────────────────────────────────────────────
    def collect(self, containers: list[dict]) -> list[dict]:
        """containers: [{name, state, status, restarts?}] as the machine already gathers them. Returns the records for this round."""
        now = self.clock()
        since = self.memory.get("since") or int(now - 60)
        seen = self.memory.setdefault("seen", {})
        out: list[dict] = []
        for c in containers:
            name = str(c.get("name") or "")
            if not name:
                continue
            state, status = str(c.get("state") or ""), str(c.get("status") or "")
            restarts = int(c.get("restarts") or 0)
            before = seen.get(name)
            seen[name] = {"state": state, "restarts": restarts}
            code = EXIT_CODE.search(status)
            died = bool(before) and ((before["state"] == "running" and state in ("exited", "dead", "restarting"))
                                     or restarts > before.get("restarts", 0))
            if died:
                tail = self._tail(name)
                out.append({"source": self.prefix + name, "level": "error", "kind": "crash",
                            "msg": f"{name} {state or 'restarted'}" + (f" (exit {code.group(1)})" if code else "") + (": " + tail[-1] if tail else ""),
                            "fields": {"restarts": restarts, **({"exit_code": int(code.group(1))} if code else {})}, "trace": ""})
                for line in tail[-TAIL_LINES:-1]:
                    out.append({"source": self.prefix + name, "level": "info", "kind": "crash", "msg": line[:400], "fields": {}, "trace": ""})
            if state == "running":
                out.extend(self._error_lines(name, since))
        self.memory["since"] = int(now)
        self._write("memory.json", self.memory)
        return out

    def _tail(self, name: str) -> list[str]:
        text = self.run(["docker", "logs", "--tail", str(TAIL_LINES), name], 20)
        return [l.rstrip() for l in text.splitlines() if l.strip()]

    def _error_lines(self, name: str, since: int) -> list[dict]:
        text = self.run(["docker", "logs", "--since", str(since), name], 20)
        hits = [l.rstrip() for l in text.splitlines() if ERRORISH.search(l)]
        return [{"source": self.prefix + name, "level": "error", "kind": "error", "msg": l[:600], "fields": {}, "trace": ""}
                for l in hits[-MAX_PER_CONTAINER:]]

    # ── the spool: write first, send, remove what was accepted ─────────────────────
    def flush(self, records: list[dict]) -> int:
        """Spool `records`, then send what the spool holds. Returns how many reached the portal this call."""
        path = self.dir / "spool.jsonl"
        if records:
            with open(path, "a", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        if sum(len(l) + 1 for l in lines) > MAX_SPOOL_BYTES:                       # a long outage: keep the newest
            while lines and sum(len(l) + 1 for l in lines) > MAX_SPOOL_BYTES:
                lines.pop(0)
        sent = 0
        while lines:
            batch = lines[:MAX_BATCH]
            rows = []
            for l in batch:
                try:
                    rows.append(json.loads(l))
                except ValueError:
                    pass
            if rows and not self.post(rows):
                break
            lines = lines[len(batch):]
            sent += len(rows)
        if lines:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
        return sent
