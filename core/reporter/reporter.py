#!/usr/bin/env python3
"""skippy-core reports itself to the portal's Status map (docs/status-design.md): POST /home/core-report every 30 s, the home token.

It carries what the portal cannot see from the cloud: the containers (docker ps, stats, restart counts), which models llama-swap has
loaded, the deployer's and the doctor's own state, the box's vitals, and whether the private Skippy is working on it (the quiet gate's
flag). Outbound only; one failed post is forgotten and the next tick sends fresh state. Stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    import quiet
except ImportError:
    quiet = None
try:
    import shipper
except ImportError:
    shipper = None

UPS_ENV = "/etc/nut/skippy-ups.env"            # SKIPPY_HOME_TOKEN, PORTAL_URL (core/install/ups.sh)
CORE_ENV = "/etc/skippy-core/core.env"
STATE = Path("/var/lib/skippy-core")


def read_env(path: str) -> dict:
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def sh(cmd: list[str], timeout: float = 20) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _pct(text) -> float | None:
    try:
        return float(str(text).rstrip("%"))
    except ValueError:
        return None


def _mem_mb(text) -> float | None:
    """'1.2GiB / 62GiB' -> 1228.8"""
    used = str(text).split("/")[0].strip()
    for unit, mul in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1048576)):
        if used.endswith(unit):
            try:
                return round(float(used[:-len(unit)]) * mul, 1)
            except ValueError:
                return None
    return None


def containers(run=sh) -> list[dict]:
    rows = lines(run(["docker", "ps", "-a", "--format", "{{json .}}"]))
    stats = {r.get("Name"): r for r in lines(run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], 60))}
    names = [r.get("Names", "") for r in rows if r.get("Names")]
    detail = {}
    if names:
        fmt = "{{.Name}} {{.RestartCount}} {{if .State.Health}}{{.State.Health.Status}}{{end}}"
        for line in run(["docker", "inspect", "--format", fmt, *names]).splitlines():
            parts = line.strip().lstrip("/").split(" ")
            if len(parts) >= 2 and parts[1].isdigit():
                detail[parts[0]] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    out = []
    for r in rows:
        name = r.get("Names", "")
        s = stats.get(name, {})
        restarts, health = detail.get(name, (0, ""))
        out.append({"name": name, "state": r.get("State", ""), "status": r.get("Status", ""), "image": r.get("Image", ""),
                    "restarts": restarts, "health": health, "cpu": _pct(s.get("CPUPerc")), "mem_mb": _mem_mb(s.get("MemUsage"))})
    return out


def http_get(url: str, key: str = "") -> dict:
    req = urllib.request.Request(url, headers={"x-api-key": key, "Authorization": "Bearer " + key} if key else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def models(get, key: str) -> list[dict]:
    try:
        got = get("http://127.0.0.1:8080/running", key)
    except (OSError, ValueError):
        return []
    return [{"name": m.get("model", ""), "state": m.get("state", ""),
             "detail": "loaded in llama-swap" if m.get("state") == "ready" else m.get("state", "")}
            for m in got.get("running") or [] if isinstance(m, dict) and m.get("model")]


def read_json(path: Path) -> dict:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def vitals() -> dict:
    out = {}
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0])
        out["mem_pct"] = round(100 * (1 - mem["MemAvailable"] / mem["MemTotal"]), 1)
        out["load1"] = round(os.getloadavg()[0], 2)
        out["uptime_h"] = round(float(Path("/proc/uptime").read_text().split()[0]) / 3600, 1)
        d = shutil.disk_usage("/")
        out["disk_pct"] = round(100 * d.used / d.total, 1)
    except (OSError, KeyError, ValueError):
        pass
    return out


def gather(run=sh, get=http_get, state: Path = STATE, key: str = "") -> dict:
    dep = read_json(state / "deployer" / "status.json")
    doc = read_json(state / "deployer" / "doctor.json")
    return {"containers": containers(run), "models": models(get, key),
            "deployer": {"state": dep.get("state", ""), "commit": str(dep.get("commit") or "")[:10], "detail": str(dep.get("detail") or "")[:160]},
            "doctor": {"ok": not (doc.get("fails", 0) >= 2), "last": str((doc.get("last") or {}).get("did", ""))[:120]},
            "vitals": vitals(), "busy": bool(quiet and quiet.busy())}


UNITS = ("skippy-core-deployer", "skippy-core-doctor", "skippy-core-ci", "skippy-core-cd", "skippy-core-backup", "skippy-core-reporter", "docker")
LOG_STATE = STATE / "reporter" / "logs"


def journal_records(run=sh, state: Path = LOG_STATE) -> list[dict]:
    """Warnings and errors from skippy-core's own services (the deployer, the doctor, CI, CD, the backup, docker) and the kernel's out-of-memory
    kills, since the last cursor (the first time: the last two minutes). The cursor is saved after, so a line is shipped once."""
    state.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    jobs = (("units", ["-p", "warning", *[a for u in UNITS for a in ("-u", u)]], "journal/"),
            ("kernel", ["-k", "-g", "Out of memory|oom-kill|Killed process"], "kernel/"))
    for name, args, prefix in jobs:
        cur_file = state / f"journal-{name}.cursor"
        cursor = cur_file.read_text().strip() if cur_file.exists() else ""
        cmd = ["journalctl", "-o", "json", "--no-pager", "-n", "200", *args, *(["--after-cursor", cursor] if cursor else ["--since", "-2min"])]
        last = cursor
        for line in run(cmd, 20).splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or not e.get("MESSAGE") or not isinstance(e.get("MESSAGE"), str):
                continue
            last = e.get("__CURSOR") or last
            prio = int(e.get("PRIORITY") or 6) if str(e.get("PRIORITY") or "6").isdigit() else 6
            unit = str(e.get("_SYSTEMD_UNIT") or e.get("SYSLOG_IDENTIFIER") or name).replace(".service", "")
            kind = "crash" if name == "kernel" else ("error" if prio <= 3 else "warn")
            out.append({"source": prefix + unit, "level": "error" if prio <= 3 else "warn", "kind": kind, "msg": e["MESSAGE"][:600], "fields": {"priority": prio}, "trace": ""})
        if last and last != cursor:
            cur_file.write_text(last)
    return out


def post_logs(url: str, token: str, rows: list[dict]) -> bool:
    req = urllib.request.Request(url + "/home/logs", data=json.dumps({"host": "skippy-core", "records": rows}).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except OSError:
        return False


def ship_logs(url: str, token: str, containers_now: list[dict], run=sh, state: Path = LOG_STATE, post=None) -> int:
    """Everything worth saying since last time goes to the portal's log intake, spooled first so a crash's last lines survive the network."""
    if shipper is None:
        return 0
    send = post or (lambda rows: post_logs(url, token, rows))
    s = shipper.Shipper("skippy-core", state, run, send)
    records = s.collect(containers_now) + journal_records(run, state)
    return s.flush(records)


def post(url: str, token: str, body: dict) -> bool:
    req = urllib.request.Request(url + "/home/core-report", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> int:
    env = {**read_env(UPS_ENV), **{k: v for k, v in os.environ.items() if k in ("SKIPPY_HOME_TOKEN", "PORTAL_URL")}}
    token, url = env.get("SKIPPY_HOME_TOKEN", ""), env.get("PORTAL_URL", "").rstrip("/")
    if not token or not url:
        print("core reporter: SKIPPY_HOME_TOKEN / PORTAL_URL missing in " + UPS_ENV, file=sys.stderr)
        return 1
    key = read_env(CORE_ENV).get("LLM_API_KEY", "")
    report = gather(key=key)
    try:
        ship_logs(url, token, report["containers"])           # never lets a log problem stop the report
    except Exception as e:  # noqa: BLE001
        print(f"core reporter: logs: {e}", file=sys.stderr)
    return 0 if post(url, token, report) else 2


if __name__ == "__main__":
    sys.exit(main())
