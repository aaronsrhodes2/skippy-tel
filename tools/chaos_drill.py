"""The chaos drill (the Captain, 2026-11-01: "be resilient to loss of service with auto-healing and recovery", Netflix's chaos monkey):
take ONE thing down on purpose and time how long the stack takes to put it back with nobody's help.

    python tools/chaos_drill.py                         dry run: every target, what it would stop, and whether the gates are open
    python tools/chaos_drill.py flask-api               dry run of one target
    python tools/chaos_drill.py flask-api --go          the real drill: stop it, watch, report
    python tools/chaos_drill.py --history               the drills so far (state/chaos/drills.jsonl)

Targets (an allow-list; nothing else can be named):
    flask-api, voice-gate, music-gate   the container is stopped (`docker stop <container>`, so Docker's own restart policy does NOT
                                        bring it back). Healer: the doctor (portal/doctor.py) through the starter (recreate).
    bridge                              the newest bridge GENERATION (bridge.py behind front.py), by its PID from state/bridge-front.json,
                                        checked against its command line. Healer: front.py starts a new generation.
    core                                Skippy Core's models: `docker stop skippycore-llm-1` on skippy-core (llama-swap: chat, coder,
                                        vision). Healer: skippy-core's doctor (`deployer.py doctor`, every 3 min: `compose restart llm`).

What it reports per drill: when the thing went down, when a healer first ACTED on it (detect: the doctor's recreate gave it a new
container, front.py's generation number moved, Core's doctor.json says it restarted llama-swap), when a REAL request worked again
(heal: flask-api /health ok, a gate's readiness, the bridge's /health ok and ready, a Core chat reply), and whether a human was needed:
when nothing healed it within the target's limit the drill puts it back itself (`docker compose up -d <service>`, `docker start`,
nothing to do for the bridge's supervisor) and says so: that is a gap to fix, not a pass.

Never while someone is using the stack (every gate, checked twice 20 s apart):
    a purple run              the bridge's /health says busy, or holders.active_runs() lists one
    a chat or a Core run      a slot of Core's chat or coder model is processing (llama-server's /slots, through llama-swap)
    a voice turn              Whisper took a POST in the last 5 minutes
    a hub conversation        state/hub.log has woke/heard/said/routine/starter/filler/book: lines in the last 3 minutes
and only when the starter is running (the doctor's hands) and every target answers before it starts. Never by process name: a container
by its name/id, a process by PID. Never the games VM, never the hub.
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "claude-bridge"))
import holders  # noqa: E402  (the purple runs going, and the process list by PID)

BRIDGE = "http://127.0.0.1:9337"
FRONT_STATE = ROOT / "state" / "bridge-front.json"
HUB_LOG = ROOT / "state" / "hub.log"
REPORTS = ROOT / "state" / "chaos" / "drills.jsonl"
STARTER_LOCK_PORT = 9339                 # tools/skippy_starter.py's one-copy lock: listening = the starter is running
CORE_SSH = "skippy-core"
CORE_LLM = "skippycore-llm-1"
HUB_BUSY = re.compile(r"woke|heard|said|routine|starter|filler|book:", re.I)
POLL_S = 5

# name -> how it is stopped, probed and put back. limit_s: how long the healers get before the drill calls it a gap and restores it.
TARGETS = {
    "flask-api": {"kind": "container", "container": "skippytel-flask-api", "service": "flask-api", "probe": "http://127.0.0.1:3003/health",
                  "limit_s": 900},
    "voice-gate": {"kind": "container", "container": "skippytel-voice-gate", "service": "voice-gate", "probe": "http://127.0.0.1:3007/health",
                   "limit_s": 900},
    "music-gate": {"kind": "container", "container": "skippytel-music-gate", "service": "music-gate", "probe": "http://127.0.0.1:3005/health",
                   "limit_s": 900},
    "bridge": {"kind": "bridge", "limit_s": 300},
    "core": {"kind": "core", "limit_s": 900},
}


def run(cmd: list[str], timeout: float = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=str(ROOT))
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 127, str(e)


def get_json(url: str, timeout: float = 5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read(500_000) or b"{}")


def ssh(script: str, timeout: float = 60) -> tuple[int, str]:
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", CORE_SSH, script], timeout)


CORE_KEY = 'K=$(sudo grep -h "^LLM_API_KEY=" /etc/skippy-core/core.env | cut -d= -f2-); '


# ── the probes: does a real request work? ──
def probe(name: str) -> tuple[bool, str]:
    t = TARGETS[name]
    try:
        if t["kind"] == "container":
            b = get_json(t["probe"])
            ok = b.get("ready") is True if "ready" in b else b.get("status") == "ok"
            return ok, "" if ok else f"answers but not ready: {str(b.get('detail') or b)[:80]}"
        if t["kind"] == "bridge":
            b = get_json(BRIDGE + "/health")
            ok = b.get("status") == "ok" and (b.get("workshop") or {}).get("ready") is True
            return ok, "" if ok else "answers but not ready"
    except Exception as e:  # noqa: BLE001 — every way of not answering is a failed probe
        return False, f"no answer ({type(e).__name__})"
    # core: a real five-token chat reply, on the box itself (the doctor's own test)
    body = json.dumps({"model": "chat", "max_tokens": 8, "messages": [{"role": "user", "content": "Say OK. /no_think"}]})
    rc, out = ssh(CORE_KEY + "curl -s -m 120 -H \"Authorization: Bearer $K\" -H 'anthropic-version: 2023-06-01' "
                  f"-H 'Content-Type: application/json' -d '{body}' http://127.0.0.1:8080/v1/messages", 150)
    try:
        wrote = any(isinstance(c, dict) and (c.get("text") or c.get("thinking")) for c in json.loads(out).get("content") or [])
    except ValueError:
        wrote = False
    return wrote, "" if wrote else f"no reply ({out[-80:] or rc})"


# ── the gates: is anyone using the stack? ──
def purple_busy() -> str:
    try:
        w = get_json(BRIDGE + "/health").get("workshop") or {}
        if w.get("busy"):
            return "a purple run is going (the bridge says busy)"
    except Exception:  # noqa: BLE001 — a bridge that doesn't answer is caught by the bridge probe
        pass
    runs = holders.active_runs()
    return f"a purple run is going: {runs[0][:140]}" if runs else ""


def core_busy() -> str:
    rc, out = ssh(CORE_KEY + 'for m in chat coder; do echo "$m $(curl -s -m 8 -H "Authorization: Bearer $K" '
                  'http://127.0.0.1:8080/upstream/$m/slots)"; done', 40)
    if rc != 0:
        return f"can't see Skippy Core's slots (ssh said {rc})"
    for line in out.splitlines():
        model, _, raw = line.partition(" ")
        try:
            slots = json.loads(raw)
        except ValueError:
            continue
        if any(isinstance(s, dict) and s.get("is_processing") for s in slots):
            return f"Skippy Core's {model} model is answering someone right now" + (" (a chat)" if model == "chat" else " (a Core run)")
    return ""


def voice_busy() -> str:
    rc, out = run(["docker", "logs", "--since", "5m", "skippytel-whisper"], 30)
    posts = [line for line in out.splitlines() if '"POST ' in line]
    return f"Whisper heard {len(posts)} request(s) in the last 5 minutes (a voice turn)" if posts else ""


def hub_busy(now: datetime | None = None, lines: list[str] | None = None) -> str:
    now = now or datetime.now()
    if lines is None:
        try:
            with open(HUB_LOG, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 200_000))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return ""
    for line in reversed(lines[-3000:]):
        m = re.match(r"(\d\d):(\d\d):(\d\d) ", line)
        if not m:
            continue
        at = now.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3]), microsecond=0)
        age = (now - at).total_seconds() % 86400      # a stamp just before midnight seen just after it
        if age > 180:
            break
        if HUB_BUSY.search(line):
            return f"the hub is in a conversation or routine: {line.strip()[:120]}"
    return ""


def starter_running() -> bool:
    """The starter holds its one-copy lock (a bound loopback port): if this can bind it, no starter is running. (netstat doesn't list it.)"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", STARTER_LOCK_PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def gates(name: str) -> list[str]:
    """Every reason not to run now; [] = go."""
    why = [w for w in (purple_busy(), core_busy(), voice_busy(), hub_busy()) if w]
    if not starter_running():
        why.append("the starter (the doctor's hands on this PC) isn't running: nothing could heal it")
    for other in TARGETS:
        ok, what = probe(other)
        if not ok:
            why.append(f"{other} is already down ({what}): fix that first, one fault at a time")
    return why


# ── the target, by container or by PID ──
def container(name: str) -> dict | None:
    rc, out = run(["docker", "inspect", "-f", "{{.Id}} {{.State.Status}}", name], 20)
    if rc != 0:
        return None
    cid, _, state = out.partition(" ")
    return {"id": cid[:12], "state": state.strip()}


def bridge_generation() -> dict | None:
    """The newest generation: {n, pid, port}, with its PID checked against its command line (must be bridge.py)."""
    try:
        gens = json.loads(FRONT_STATE.read_text(encoding="utf-8")).get("gens") or []
    except (OSError, ValueError):
        return None
    live = [g for g in gens if not g.get("draining")]
    if not live:
        return None
    g = max(live, key=lambda g: g["n"])
    proc = next((p for p in holders.processes() if p["pid"] == g["pid"]), None)
    if not proc or "bridge.py" not in (proc["cmd"] or ""):
        return None
    return {"n": g["n"], "pid": g["pid"], "port": g["port"], "cmd": proc["cmd"][:120]}


def core_doctor() -> dict:
    """Core doctor's last action ({at, why, did}), plus the deployer's last run as `deploy` ({state, commit, at}): a routine deploy of a new
    commit runs `compose up` and brings llama-swap back too (the first Core drill, 2026-11-01: healed by luck, the doctor never saw it)."""
    rc, out = ssh("sudo cat /var/lib/skippy-core/deployer/doctor.json; echo @@@; sudo cat /var/lib/skippy-core/deployer/status.json", 30)
    docs = []
    for chunk in out.split("@@@"):
        try:
            docs.append(json.loads(chunk))
        except ValueError:
            docs.append({})
    last = dict((docs[0].get("last") or {}) if docs else {})
    if len(docs) > 1:
        last["deploy"] = {k: docs[1].get(k) for k in ("state", "commit", "at", "detail")}
    return last


def plan(name: str) -> str:
    """What the drill would stop, in words, with the exact command."""
    t = TARGETS[name]
    if t["kind"] == "container":
        c = container(t["container"])
        return (f"stop container {t['container']} ({c['id']}, {c['state']}): `docker stop {t['container']}`; "
                f"heals when {t['probe']} is ready; the doctor should recreate it within ~6 min") if c else f"{t['container']} doesn't exist"
    if t["kind"] == "bridge":
        g = bridge_generation()
        return (f"kill bridge generation {g['n']} (PID {g['pid']}, :{g['port']}, `{g['cmd']}`): `taskkill /PID {g['pid']} /T /F`; "
                "front.py should start the next generation within ~30 s") if g else "no live bridge generation found (state/bridge-front.json)"
    return (f"stop {CORE_LLM} on skippy-core (chat, coder and vision: Mentat and Core runs lose their models too): "
            f"`ssh {CORE_SSH} docker stop {CORE_LLM}`; skippy-core's doctor should restart it within ~6 min")


# ── the drill ──
def now_s() -> float:
    return time.time()


def drill(name: str, say=print) -> dict:
    t = TARGETS[name]
    row = {"target": name, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": plan(name)}
    before = {}
    if t["kind"] == "container":
        before = container(t["container"]) or {}
        rc, out = run(["docker", "stop", t["container"]], 120)
    elif t["kind"] == "bridge":
        before = bridge_generation() or {}
        if not before:
            return {**row, "error": "no live bridge generation to stop"}
        rc, out = run(["taskkill", "/PID", str(before["pid"]), "/T", "/F"], 30)
    else:
        before = core_doctor()
        rc, out = ssh(f"docker stop {CORE_LLM}", 120)
    t0 = now_s()
    say(f"{name}: down at {time.strftime('%H:%M:%S')} ({out[-80:] or rc})")
    if rc != 0:
        return {**row, "error": f"could not stop it: {out[-160:]}"}
    detect = heal = None
    healer = ""
    while now_s() - t0 < t["limit_s"]:
        time.sleep(POLL_S)
        if detect is None:
            if t["kind"] == "container":
                c = container(t["container"])
                if c is None or c["id"] != before.get("id") or c["state"] != "exited":
                    detect, healer = now_s() - t0, ("the doctor (recreated)" if c is None or c["id"] != before.get("id") else
                                                    "something started the same container")
            elif t["kind"] == "bridge":
                g = bridge_generation()
                if g and g["pid"] != before["pid"]:
                    detect, healer = now_s() - t0, f"front.py (generation {g['n']}, PID {g['pid']})"
            else:
                last = core_doctor()
                if last.get("at", 0) > t0 - 5 and last.get("at") != before.get("at"):
                    say(f"core: Core's doctor at +{int(last['at'] - t0)} s: {last.get('why')}; {last.get('did')}")
                    if str(last.get("did", "")).startswith(("restarted", "repaired")):
                        detect, healer = last["at"] - t0, f"skippy-core's doctor ({last.get('did')})"
                    before = last
                dep, dep0 = last.get("deploy") or {}, before.get("deploy") or {}
                if detect is None and dep.get("at") != dep0.get("at") and dep.get("detail") == "deployed":
                    detect, healer = now_s() - t0, f"skippy-core's deployer (a routine deploy of {str(dep.get('commit'))[:7]}, not the doctor)"
            if detect is not None:
                say(f"{name}: {healer} acted at +{int(detect)} s")
        ok, _ = probe(name)
        if ok:
            heal = now_s() - t0
            if detect is None:
                detect, healer = heal, healer or "unknown (it answered before a healer was seen)"
            break
    row.update(detect_s=None if detect is None else int(detect), heal_s=None if heal is None else int(heal), healer=healer,
               human_needed=heal is None)
    if heal is None:
        row["restored_by_drill"] = restore(name, say)
    say(f"{name}: " + (f"healed by itself in {int(heal)} s ({healer})" if heal is not None else
                       f"NOT healed within {t['limit_s']} s: {row['restored_by_drill']}"))
    REPORTS.parent.mkdir(parents=True, exist_ok=True)
    with REPORTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def restore(name: str, say=print) -> str:
    """Nothing healed it in time: put it back the documented way (the human's part, done by the drill so the stack isn't left down)."""
    t = TARGETS[name]
    if t["kind"] == "container":
        for cmd in (["docker", "compose", "stop", t["service"]], ["docker", "compose", "rm", "-f", t["service"]],
                    ["docker", "compose", "up", "-d", t["service"]]):
            rc, out = run(cmd, 900)
        return "recreated by the drill (docker compose stop, rm, up -d)" if rc == 0 else f"the drill's restore failed too: {out[-120:]}"
    if t["kind"] == "bridge":
        return "the drill can't start a bridge safely (the supervisor owns it): bash tools/bridge_supervisor.sh --status"
    rc, out = ssh(f"docker start {CORE_LLM}", 120)
    return f"started by the drill (docker start {CORE_LLM})" if rc == 0 else f"the drill's restore failed too: {out[-120:]}"


def history() -> list[dict]:
    try:
        return [json.loads(line) for line in REPORTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", nargs="?", choices=list(TARGETS))
    ap.add_argument("--go", action="store_true", help="really take it down (without it: a dry run)")
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args(argv)
    if a.history:
        for r in history():
            print(f"{r['at']}  {r['target']:<11} detect {r.get('detect_s')} s  heal {r.get('heal_s')} s  "
                  f"human {'YES' if r.get('human_needed') else 'no'}  {r.get('healer') or r.get('error', '')}")
        return 0
    names = [a.target] if a.target else list(TARGETS)
    if not a.go or not a.target:
        print("DRY RUN: nothing is stopped. Add --go with one target to run it.\n")
        for n in names:
            print(f"  {n}: {plan(n)}")
        why = gates(names[0])
        print("\nGates: " + ("open, a drill may run now." if not why else "CLOSED:\n  - " + "\n  - ".join(why)))
        return 0
    why = gates(a.target)
    if not why:
        time.sleep(20)                                   # twice, 20 s apart: a turn that was just starting shows up the second time
        why = gates(a.target)
    if why:
        print("Not now:\n  - " + "\n  - ".join(why))
        return 3
    row = drill(a.target)
    print(json.dumps(row, indent=2))
    return 0 if not row.get("human_needed") and not row.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
