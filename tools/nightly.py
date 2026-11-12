"""The house's night shift on skippy-pc (the Captain, 2026-10-31): started by the hub's routines (services/satellite/daily.py) at
SKIPPY_NIGHTLY_AT (01:30) once the house has been quiet for 20 minutes, or by hand. It never talks; it writes the day's LEDGER and a one-line
overnight report that the morning briefing reads out.

  systems     main on the house git vs what CI tested vs what the portal serves; a commit CI passed that still isn't live an hour later is
              deployed again (`cd.py deploy` on skippy-core). Nothing is ever reverted here: skippy-core's deployer already sends all traffic
              back to the previous revision when a deploy fails its smoke test, and recovery never reverts code.
  lazy jobs   the night's own scheduled tasks (home learner 03:30, quip refresh 04:15, MPC live scoreboard 04:30, distiller 06:30, starters 06:40): did each run, and
              did it succeed (read from Windows' task scheduler).
  upkeep      free disk on C: and D:, dangling Docker images and old build cache pruned (`docker image prune`, `docker builder prune` older
              than a week: never a volume, never a running container), the hub restarted onto new code when its code changed since it
              started and nobody has spoken for 30 minutes (its own PIDs only), the vault's .env present.
  healing     once a week (SKIPPY_CHAOS_DAY, Sunday): ONE chaos drill (tools/chaos_drill.py --weekly), new services first; the morning
              report says whether it came back by itself.
  ledger      the day in numbers: what the house heard and did (skippy-home's log), routines, deploys and CI (skippy-core), into
              state/ledger/<date>.json and .md; and purple's day in one line (tools/purple_audit.py: replies whose words claimed a
              push, a deploy or a test run their calls don't show).

    python tools/nightly.py [--dry-run] [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import calendar
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
HOME_LOG = Path(os.environ.get("SKIPPY_HOME_DIR", ROOT.parent / "skippy-home")) / "log"
PORTAL = os.environ.get("PORTAL_URL", "https://skippy-portal-1015050363686.us-west1.run.app").rstrip("/")
VAULT_ENV = Path(r"G:\My Drive\vault\.env")
CHAOS_DAY = os.environ.get("SKIPPY_CHAOS_DAY", "sun")[:3].lower()     # the one night a week the healing check runs (tools/chaos_drill.py --weekly)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TASKS = ["SkippyHomeLearn", "Skippy quip refresh", "sigma-ground distill daily", "sigma-ground mpc live daily", "Skippy starters refresh", "SkippyModelsBaseline"]
LATE_S = 3600


def sh(args: list[str], timeout: float = 120, check: bool = False) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"{type(e).__name__}: {e}"
    if check and p.returncode:
        raise RuntimeError(f"{args[0]} failed: {p.stderr.strip()[:200]}")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def core(cmd: str, timeout: float = 60) -> tuple[int, str]:
    return sh(["ssh", "-o", "ConnectTimeout=10", "skippy-core", cmd], timeout)


def get_json(url: str, timeout: float = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:                 # noqa: S310 — our own portal
        return json.loads(r.read())


# ── systems ──
def systems(dry: bool, log) -> dict:
    out: dict = {}
    sh(["git", "-C", str(ROOT), "fetch", "-q", "origin"], 60)
    rc, main = sh(["git", "-C", str(ROOT), "rev-parse", "origin/main"])
    out["main"] = main.strip()[:40] if rc == 0 else None
    rc, raw = core("sudo cat /var/lib/skippy-core/ci/skippy-tel/latest.json")
    try:
        ci = json.loads(raw)
    except ValueError:
        ci = {}
    out["ci"] = {"sha": ci.get("sha"), "state": ci.get("state")}
    try:
        v = get_json(f"{PORTAL}/version")
        out["live"] = {"ok": True, "commit": v.get("commit"), "started_at": v.get("started_at")}
    except Exception as e:                                                  # noqa: BLE001
        out["live"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    rc, raw = core("sudo python3 -c \"import json;d=json.load(open('/var/lib/skippy-core/cd/state.json'));"
                   "r=sorted(d.get('runs') or [], key=lambda x: x.get('id', 0));print(json.dumps(r[-12:]))\"")
    try:
        runs = json.loads(raw)
    except ValueError:
        runs = []
    out["runs"] = [{"id": r.get("id"), "sha": (r.get("head_sha") or "")[:7], "conclusion": r.get("conclusion"), "status": r.get("status"),
                    "at": r.get("created_at")} for r in runs]
    main_sha, live = out["main"], (out["live"].get("commit") or "")
    out["behind"] = bool(main_sha and live and main_sha != live)
    passed = ci.get("sha") == main_sha and str(ci.get("state")).lower() in ("passed", "success", "green", "ok")
    running = any(r.get("status") not in ("completed", None) for r in runs)
    last = max((r.get("at") or "" for r in runs), default="")
    stale = not last or time.time() - calendar.timegm(time.strptime(last[:19], "%Y-%m-%dT%H:%M:%S")) > LATE_S     # the runs' times are UTC
    out["action"] = None
    if out["behind"] and passed and not running and stale:
        out["action"] = "deploy"
        if not dry:
            rc, txt = core("sudo python3 /opt/skippy-core/cd/cd.py deploy", 1800)
            out["deploy"] = {"rc": rc, "tail": txt.strip()[-300:]}
            log(f"systems: deployed again (rc {rc})")
    return out


# ── the night's own jobs ──
def task_report(name: str) -> dict:
    rc, txt = sh(["schtasks", "/query", "/tn", name, "/fo", "list", "/v"], 30)
    if rc:
        return {"task": name, "found": False}
    f = {}
    for line in txt.splitlines():
        k, _, v = line.partition(":")
        if v and k.strip() in ("Last Run Time", "Last Result", "Next Run Time", "Status"):
            f.setdefault(k.strip(), v.strip())
    result = f.get("Last Result", "")
    return {"task": name, "found": True, "last_run": f.get("Last Run Time"), "result": result, "ok": result in ("0", "267009", "267011"), "never_ran": result == "267011",
            "next": f.get("Next Run Time")}


# ── upkeep ──
def disks() -> dict:
    out = {}
    for d in ("C:\\", "D:\\"):
        try:
            u = shutil.disk_usage(d)
            out[d[0]] = {"free_gb": round(u.free / 1e9, 1), "free_pct": round(100 * u.free / u.total, 1)}
        except OSError:
            pass
    return out


def docker_prune(dry: bool) -> dict:
    if dry:
        return {"dry": True}
    a = sh(["docker", "image", "prune", "-f"], 600)
    b = sh(["docker", "builder", "prune", "-f", "--filter", "until=168h"], 900)
    freed = re.findall(r"Total reclaimed space:\s*([\d.]+\s*\w+)", a[1] + b[1])
    return {"images_rc": a[0], "builder_rc": b[0], "reclaimed": freed}


def hub_pids() -> list[int]:
    rc, txt = sh(["powershell.exe", "-NoProfile", "-Command",                  # the one Windows-only question: which python runs hub.py (PLATINUM 20)
                  "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | Where-Object { $_.CommandLine -match 'hub\\.py' } | "
                  "ForEach-Object { $_.ProcessId }"], 60)
    return [int(x) for x in txt.split() if x.isdigit()] if rc == 0 else []


def hub_restart(dry: bool, log) -> dict:
    """Restart the hub onto new code: only when its code changed since it started and the house has been quiet for 30 minutes."""
    text = (STATE / "hub.log").read_text(encoding="utf-8", errors="replace") if (STATE / "hub.log").exists() else ""
    starts = re.findall(r"^(\d\d:\d\d:\d\d) hub: rooms", text, re.M)
    started = (STATE / "hub.log").stat().st_mtime if not starts else None
    if starts:
        h, m, s = (int(x) for x in starts[-1].split(":"))
        t = time.localtime()
        started = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, m, s, 0, 0, -1))
        if started > time.time() + 60:
            started -= 86400
    newest = max((p.stat().st_mtime for p in (ROOT / "services" / "satellite").glob("*.py")), default=0)
    if newest <= (started or 0):
        return {"restarted": False, "why": "the hub already runs the newest code"}
    talk = [m for m in re.findall(r"^(\d\d:\d\d:\d\d) \[\w+\] (?:heard|woke|routine)", text, re.M)]
    if talk:
        h, m, s = (int(x) for x in talk[-1].split(":"))
        t = time.localtime()
        last = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, m, s, 0, 0, -1))
        if last > time.time() + 60:
            last -= 86400
        if time.time() - last < 1800:
            return {"restarted": False, "why": "someone spoke to Skippy in the last 30 minutes"}
    pids = hub_pids()
    if dry or not pids:
        return {"restarted": False, "why": "dry run" if dry else "no hub running", "pids": pids}
    for pid in pids:
        sh(["taskkill", "/F", "/PID", str(pid)], 30)                       # its supervisor (the SkippyHub task) starts it again in seconds
    log(f"upkeep: hub restarted onto new code (PIDs {pids})")
    return {"restarted": True, "pids": pids}


# ── the weekly healing check ──
def healing_check(dry: bool, log) -> dict | None:
    """Once a week (CHAOS_DAY): ONE chaos drill, the target drilled longest ago, new services first (the Captain, 2026-11-01: "once a week,
    but it also needs to auto-discover new additions"). Never on other nights, never more than one target."""
    if DAYS[time.localtime().tm_wday] != CHAOS_DAY:
        return None
    if dry:
        return {"dry": True}
    rc, txt = sh([sys.executable, str(ROOT / "tools" / "chaos_drill.py"), "--weekly"], 2400)
    try:
        res = json.loads((STATE / "chaos" / "weekly.json").read_text(encoding="utf-8"))
        if res.get("week_at", "")[:10] < time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400)):
            raise ValueError("stale")
    except (OSError, ValueError):
        res = {"error": txt.strip()[-200:] or f"rc {rc}"}
    log(f"healing check: {json.dumps(res)[:200]}")
    return res


def healing_words(c: dict | None) -> str:
    if not c or c.get("dry"):
        return ""
    if c.get("error"):
        return "the weekly healing check couldn't run"
    if c.get("skipped"):
        words = "the weekly healing check waited: the house was busy"
    elif c.get("human_needed"):
        words = f"the weekly healing check found a gap: {c['target']} didn't come back by itself, so I put it back"
    else:
        words = f"the weekly healing check took down {c['target']} and it came back by itself in {c.get('heal_s')} seconds"
    new = c.get("new") or []
    return words + (f" ({len(new)} new service{'s' if len(new) != 1 else ''} to test: {', '.join(new[:4])})" if new else "")


def ui_tidy(dry: bool, log) -> dict:
    """The portal's controls tidied from their telemetry (tools/adaptive_ui.py, portal/adaptive.py): the portal decides, at most one
    change per surface a week; this says what it did."""
    if dry:
        return {"dry": True}
    rc, txt = sh([sys.executable, str(ROOT / "tools" / "adaptive_ui.py")], 120)
    res = {"ok": rc == 0, "said": txt.strip().splitlines()[-1][:400] if txt.strip() else f"rc {rc}"}
    log(f"ui tidy: {res}")
    return res


def tidy_words(t: dict | None) -> str:
    if not t or t.get("dry"):
        return ""
    if not t.get("ok"):
        return "the portal's nightly tidy couldn't run"
    said = t.get("said") or ""
    return "" if said.startswith("the portal's controls: nothing") else "the portal tidied its controls: " + said


def moments_sweep(dry: bool, log) -> dict:
    """Skippy-vision (portal/vision.py): the 10-second clips of moments older than 90 days go, unless he kept them; the stills never do."""
    if dry:
        return {"dry": True}
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import adaptive_ui
        token = adaptive_ui.vault_value("SKIPPY_HOME_TOKEN")
        if not token:
            return {"ok": False, "why": "no home token"}
        req = urllib.request.Request(f"{PORTAL}/home/vision/sweep", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:                    # noqa: S310 — our own portal
            res = {"ok": True, "clips_deleted": int(json.loads(r.read() or b"{}").get("clips_deleted") or 0)}
    except Exception as e:  # noqa: BLE001 — the ledger never fails over it (a portal with no bucket answers 503)
        res = {"ok": False, "why": type(e).__name__}
    log(f"moments sweep: {res}")
    return res


# ── the ledger ──
def house_day(date: str) -> dict:
    events = []
    path = HOME_LOG / f"{date}.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    kinds = collections.Counter(e.get("kind") for e in events)
    tools = collections.Counter(e.get("tool") for e in events if e.get("kind") == "action")
    routines = [f"{e.get('time')} {e.get('routine')} {e.get('outcome')}" for e in events if e.get("kind") == "routine"]
    failed = sum(1 for e in events if e.get("kind") == "command" and e.get("outcome") not in ("ok", "sign_off", None))
    return {"events": len(events), "kinds": dict(kinds), "tools": dict(tools.most_common(10)), "routines": routines, "failed_commands": failed}


def overnight_line(report: dict) -> str:
    s, jobs, up = report["systems"], report["jobs"], report["upkeep"]
    bits = []
    if not s.get("live", {}).get("ok"):
        bits.append("the portal didn't answer its live check")
    elif s.get("behind") and s.get("ci", {}).get("sha") == s.get("main") and str(s.get("ci", {}).get("state")).lower() == "running":
        bits.append("the newest commit is still going through CI")
    elif s.get("behind"):
        bits.append("the newest commit isn't live yet" + (" so I deployed it again" if s.get("action") == "deploy" else ""))
    else:
        bits.append("everything pushed is live")
    if str(s.get("ci", {}).get("state")).lower() in ("failed", "failure", "red"):
        bits.append("CI is failing")
    bad = [j["task"] for j in jobs if j.get("found") and not j.get("ok")]
    bits.append(f"{len(bad)} night job{'s' if len(bad) != 1 else ''} failed ({', '.join(bad)})" if bad else "the night jobs ran clean")
    low = [f"{d}: {v['free_gb']} GB free" for d, v in up.get("disks", {}).items() if v["free_pct"] < 10]
    if low:
        bits.append("disk is getting full (" + "; ".join(low) + ")")
    if up.get("hub", {}).get("restarted"):
        bits.append("I restarted myself onto new code")
    if healing_words(report.get("chaos")):
        bits.append(healing_words(report.get("chaos")))
    if tidy_words(report.get("ui_tidy")):
        bits.append(tidy_words(report.get("ui_tidy")))
    if (report.get("moments") or {}).get("clips_deleted"):
        bits.append(f"{report['moments']['clips_deleted']} old moment clip{'s' if report['moments']['clips_deleted'] != 1 else ''} cleared")
    return "Overnight: " + ", ".join(bits) + "."


def purple_day(date: str) -> str:
    """purple's day in one line (tools/purple_audit.py), or why it couldn't be read: the ledger never fails over it."""
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import purple_audit
        return purple_audit.ledger_line(purple_audit.day_rows(date))
    except Exception as e:  # noqa: BLE001
        return f"purple: couldn't read its runs ({type(e).__name__})"


def ledger_md(date: str, r: dict) -> str:
    h, s = r["house"], r["systems"]
    lines = [f"# Ledger {date}", "", f"**{r['overnight']}**", "", "## House",
             f"- {h['events']} events: " + ", ".join(f"{k} {v}" for k, v in sorted(h["kinds"].items(), key=lambda kv: -kv[1])),
             f"- tools: " + (", ".join(f"{k} {v}" for k, v in h["tools"].items()) or "none"),
             f"- routines: " + ("; ".join(h["routines"]) or "none"), f"- commands that failed: {h['failed_commands']}", "", "## Systems",
             f"- main {str(s.get('main'))[:7]}, CI {s.get('ci', {}).get('state')} on {str(s.get('ci', {}).get('sha'))[:7]}, live {str(s.get('live', {}).get('commit'))[:7]}",
             "- deploys today: " + (", ".join(f"#{x['id']} {x['sha']} {x['conclusion'] or x['status']}" for x in s.get("runs", []) if str(x.get("at", "")).startswith(date)) or "none"),
             "", "## Purple", f"- {r.get('purple', 'purple: not read')}",
             "", "## Night jobs"] + [f"- {j['task']}: " + (f"last {j.get('last_run')}, result {j.get('result')}" if j.get("found") else "not found") for j in r["jobs"]]
    lines += ["", "## Upkeep", "- disks: " + ", ".join(f"{d} {v['free_gb']} GB ({v['free_pct']}%)" for d, v in r["upkeep"]["disks"].items()),
              f"- docker prune: {r['upkeep']['docker']}", f"- hub: {r['upkeep']['hub']}", f"- vault .env present: {r['upkeep']['vault']}"]
    if r.get("chaos"):
        lines += ["", "## Weekly healing check", f"- {healing_words(r['chaos']) or r['chaos']}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None, help="the day the ledger is for (default: yesterday before 06:00, else today)")
    args = ap.parse_args(argv)
    now = time.time()
    date = args.date or time.strftime("%Y-%m-%d", time.localtime(now - (86400 if time.localtime(now).tm_hour < 6 else 0)))
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)     # noqa: E731
    log(f"nightly for {date}{' (dry run)' if args.dry_run else ''}")
    report = {"date": date, "at": now}
    report["systems"] = systems(args.dry_run, log)
    report["jobs"] = [task_report(t) for t in TASKS]
    report["upkeep"] = {"disks": disks(), "docker": docker_prune(args.dry_run), "hub": hub_restart(args.dry_run, log), "vault": VAULT_ENV.is_file()}
    report["chaos"] = healing_check(args.dry_run, log)
    report["ui_tidy"] = ui_tidy(args.dry_run, log)
    report["moments"] = moments_sweep(args.dry_run, log)
    report["house"] = house_day(date)
    report["purple"] = purple_day(date)
    report["overnight"] = overnight_line(report)
    folder = STATE / "ledger"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (folder / f"{date}.md").write_text(ledger_md(date, report), encoding="utf-8")
    if not args.dry_run:
        (STATE / "daily").mkdir(parents=True, exist_ok=True)
        (STATE / "daily" / "overnight.json").write_text(json.dumps({"date": date, "at": now, "line": report["overnight"]}), encoding="utf-8")
    log(report["overnight"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
