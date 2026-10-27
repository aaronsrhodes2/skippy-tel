"""Re-run prompts that failed in the private Skippy's history, on a chosen brain, one at a time, and record what happens.

The Captain's loop (2026-10-24): find the turns that failed on a hand-off, a limit or a timeout, run them again once the
chain is fixed, and keep each result as the home AI's baseline for the later side-by-side with Claude Haiku (distillation).

    python tools/retest_failures.py cases.json results.json [--brain core] [--only name,name]

cases.json: [{"name", "message", "project" ("f-skippy-retest", the sandbox, unless the case needs its own conversation),
"conv" (optional: resume that conversation), "images" (optional: paths to pictures to attach)}]. It drives the bridge the way
the home reporter does (POST /workshop, then GET /workshop/run/<task>), with the home token read from the reporter's
container, never printed. It waits as long as a run takes (up to --max-min; 0 = for as long as it runs; it only stops WAITING, the run goes on), and
writes results.json after every case.
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BRIDGE = "http://localhost:9337"
WHO = "aaron.s.rhodes@gmail.com"


def token() -> str:
    out = subprocess.run(["docker", "exec", "skippytel-home-reporter", "printenv", "SKIPPY_HOME_TOKEN"],
                         capture_output=True, text=True, check=True).stdout.strip()
    if not out:
        sys.exit("no SKIPPY_HOME_TOKEN in the home reporter")
    return out


def call(method: str, path: str, body: dict | None, tok: str, timeout: float = 60) -> tuple[int, dict]:
    req = urllib.request.Request(BRIDGE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def idle(tok: str) -> None:
    """Wait for the bridge to be free: the Captain's own runs go first."""
    while True:
        with urllib.request.urlopen(BRIDGE + "/health", timeout=10) as r:
            if not json.loads(r.read())["workshop"]["busy"]:
                return
        time.sleep(15)


def run_case(case: dict, brain: str, tok: str, max_min: float) -> dict:
    idle(tok)
    task, conv = secrets.token_hex(8), case.get("conv") or f"retest-{uuid.uuid4().hex[:12]}"
    images = []
    for p in case.get("images") or []:
        data = Path(p).read_bytes()
        images.append({"mime": "image/png" if data[:4] == b"\x89PNG" else "image/jpeg", "b64": base64.b64encode(data).decode()})
    body = {"task": task, "conv": conv, "message": case["message"], "who": WHO, "project": case.get("project", "f-skippy-retest"),
            "brain": brain, **({"images": images} if images else {})}
    t0 = time.time()
    status, started = call("POST", "/workshop", body, tok)
    out = {"name": case["name"], "task": task, "conv": conv, "brain": brain, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
           "start_status": status}
    if status != 200:
        return {**out, "ok": False, "error": started.get("message") or started.get("error") or f"HTTP {status}", "seconds": 0, "events": []}
    return follow(out, task, tok, t0, max_min)


def follow(out: dict, task: str, tok: str, t0: float, max_min: float) -> dict:
    """Read a run's events, from the first, until it is done (the bridge holds each request until there is news), and
    return `out` with the outcome: the same result whether this tool started the run or joined it."""
    events, after, done = [], 0, {}
    while max_min <= 0 or time.time() - t0 < max_min * 60:
        s, got = call("GET", f"/workshop/run/{task}?after={after}&wait=20", None, tok, timeout=40)
        if s != 200:
            time.sleep(5)
            continue
        events += got.get("events") or []
        after = got.get("next", after)
        if got.get("done"):
            done = got
            break
    secs = round(time.time() - t0)
    tools = [e for e in events if e.get("t") == "tool"]
    texts = [e.get("text", "") for e in events if e.get("t") == "text"]
    return {**out, "ok": bool(done.get("ok")), "done": bool(done), "error": done.get("error") if done else f"still running after {max_min} min",
            "seconds": secs, "tool_calls": len(tools), "tools": [t.get("name") for t in tools],
            "pictures": [e for e in events if e.get("t") == "picture"], "images_shown": sum(e.get("t") == "image" for e in events),
            "answer": "\n".join(texts)[-6000:], "events": [{k: v for k, v in e.items() if k != "b64"} for e in events]}


def attach_case(case: dict, task: str, brain: str, tok: str, max_min: float) -> dict:
    """Join a run that is already going (its driver died, or was never this tool): its events from the first, its age as
    the time it has taken. The run was started by someone else, so nothing here posts or cancels anything."""
    status, listing = call("GET", "/workshop/runs", None, tok)
    mine = next((r for r in (listing.get("runs") or []) if r.get("task") == task), None) if status == 200 else None
    out = {"name": case["name"], "task": task, "conv": case.get("conv") or (mine or {}).get("conv"), "brain": brain,
           "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - float((mine or {}).get("age_s") or 0))),
           "start_status": "attached"}
    if mine is None:
        return {**out, "ok": False, "error": f"the bridge does not know a run {task}", "seconds": 0, "events": []}
    return follow(out, task, tok, time.time() - float(mine.get("age_s") or 0), max_min)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("results")
    ap.add_argument("--brain", default="core")
    ap.add_argument("--only", default="")
    ap.add_argument("--max-min", type=float, default=45, help="stop waiting after this many minutes (0 = never)")
    ap.add_argument("--attach", default="", metavar="TASK", help="follow a run that is already going (the case's first entry names it) instead of starting one")
    a = ap.parse_args()
    cases = json.loads(Path(a.cases).read_text(encoding="utf-8"))
    only = {n for n in a.only.split(",") if n}
    results_path = Path(a.results)
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else []
    tok = token()
    for case in cases:
        if only and case["name"] not in only:
            continue
        print(f"-> {case['name']} ({a.brain})" + (f" attached to {a.attach}" if a.attach else ""), flush=True)
        r = attach_case(case, a.attach, a.brain, tok, a.max_min) if a.attach else run_case(case, a.brain, tok, a.max_min)
        print(f"   {'OK' if r['ok'] else 'FAILED'} in {r['seconds']} s, {r.get('tool_calls', 0)} tool calls"
              + ("" if r["ok"] else f": {str(r.get('error'))[:200]}"), flush=True)
        results.append(r)
        results_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
