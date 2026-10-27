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
import time
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


def http_get(url: str, key: str = "") -> dict | list:
    req = urllib.request.Request(url, headers={"x-api-key": key, "Authorization": "Bearer " + key} if key else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def slot_view(slots) -> tuple[int, int]:
    """llama-server's /slots -> (jobs running, words written so far by them)."""
    jobs = decoded = 0
    for s in slots if isinstance(slots, list) else []:
        if isinstance(s, dict) and s.get("is_processing"):
            jobs += 1
            nt = s.get("next_token")
            nt = nt[0] if isinstance(nt, list) and nt else nt
            decoded += int((nt or {}).get("n_decoded") or 0) if isinstance(nt, dict) else 0
    return jobs, decoded


def progress(memory: dict, name: str, jobs: int, decoded: int, now: float) -> int:
    """Seconds this model has had a job running with no new words (0 = nothing running, or it is getting somewhere). The memory dict
    ({model: {decoded, at}}) is what the last round saw; a model hung on a job never advances `decoded`."""
    prev = memory.get(name) or {}
    if not jobs:
        memory[name] = {"decoded": 0, "at": now}
        return 0
    if decoded != prev.get("decoded"):
        memory[name] = {"decoded": decoded, "at": now}
        return 0
    return int(now - prev.get("at", now))


def models(get, key: str, memory: dict | None = None, now: float | None = None) -> list[dict]:
    """What llama-swap has loaded, and for each: ready or not, jobs running, and `stuck_s` (a job with no new words for that long: HUNG).
    A 200 from the port says nothing about that (docs: PLATINUM §24), so the slots are read."""
    import time as _t
    try:
        got = get("http://127.0.0.1:8080/running", key)
    except (OSError, ValueError):
        return []
    memory = memory if memory is not None else {}
    now = _t.time() if now is None else now
    out = []
    for m in got.get("running") or []:
        if not isinstance(m, dict) or not m.get("model"):
            continue
        name = m["model"]
        jobs = decoded = stuck = 0
        if m.get("state") == "ready":
            try:
                jobs, decoded = slot_view(get(f"http://127.0.0.1:8080/upstream/{name}/slots", key))
            except (OSError, ValueError):
                pass
            stuck = progress(memory, name, jobs, decoded, now)
        out.append({"name": name, "state": m.get("state", ""), "jobs": jobs, "decoded": decoded, "stuck_s": stuck,
                    "detail": "loaded in llama-swap" if m.get("state") == "ready" else m.get("state", "")})
    return out


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


MENTAT_URL = os.environ.get("MENTAT_URL", "http://127.0.0.1:7350")
MENTAT_LOG = Path(os.environ.get("MENTAT_RESEARCH", str(STATE / "mentat-research")))
_usage_cache: dict = {"sig": None, "usage": {}, "schema_at": 0.0, "schema": None}


def mentat_usage(log_dir: Path = MENTAT_LOG, post=None, now=time.time) -> dict:
    """How Mentat is used this month, read from its own research log: plain questions (/ask) against tools called by name, and what showing every
    tool to a chat costs (the size of its tools/list, asked about once an hour). Re-read only when the log file changed."""
    files = sorted(log_dir.glob("*.jsonl")) if log_dir.is_dir() else []
    if not files:
        return {}
    latest = files[-1]
    sig = (latest.name, latest.stat().st_size, int(latest.stat().st_mtime))
    if _usage_cache["sig"] != sig:
        asks, direct, names = 0, 0, set()
        for line in latest.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            endpoint = str(row.get("endpoint") or "")
            if endpoint == "/ask" or (endpoint == "/mcp" and row.get("tool") == "ask"):
                asks += 1
            elif endpoint.startswith("/tools/") or endpoint == "/mcp":          # a tool called by name, over REST or through MCP's call_tool
                direct += 1
                names.add(endpoint[7:] if endpoint != "/mcp" else str(row.get("tool") or "?"))
        _usage_cache.update(sig=sig, usage={"asks": asks, "direct": direct, "tools_called": len(names), "since": latest.stem})
    if now() - _usage_cache["schema_at"] > 3600 and post is not None:
        _usage_cache["schema_at"] = now()
        try:
            tools = post(MENTAT_URL + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
            _usage_cache["schema"] = (len(tools), len(json.dumps(tools)) // 4)
        except Exception:  # noqa: BLE001 — an unreachable Mentat just means no schema number this hour
            pass
    out = dict(_usage_cache["usage"])
    if _usage_cache["schema"]:
        out["tools_total"], out["schema_tokens"] = _usage_cache["schema"]
    return out


def _post_json(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def gather(run=sh, get=http_get, state: Path = STATE, key: str = "") -> dict:
    dep = read_json(state / "deployer" / "status.json")
    doc = read_json(state / "deployer" / "doctor.json")
    memory = read_json(state / "reporter" / "progress.json")
    models_now = models(get, key, memory)
    try:
        (state / "reporter").mkdir(parents=True, exist_ok=True)
        (state / "reporter" / "progress.json").write_text(json.dumps(memory), encoding="utf-8")
    except OSError:
        pass
    return {"containers": containers(run), "models": models_now,
            "deployer": {"state": dep.get("state", ""), "commit": str(dep.get("commit") or "")[:10], "detail": str(dep.get("detail") or "")[:160]},
            "doctor": {"ok": not (doc.get("fails", 0) >= 2), "last": str((doc.get("last") or {}).get("did", ""))[:120]},
            "vitals": vitals(), "busy": bool(quiet and quiet.busy()),
            "usage": {"mentat": _safe(lambda: mentat_usage(post=_post_json))}}


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 — a usage number is never worth a missed report
        return {}


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
