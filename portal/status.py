"""The Status map: one tree of the house, built from what each machine reports out (docs/status-design.md).

Every machine reports on its own and none depends on another being up, which is what makes this a quorum and not a chain:
skippy-pc through the home report and the starter, skippy-core through `core/reporter` (POST /home/core-report, the home token),
the games VM through the portal's own VM status, and the portal itself. This module only reads and shapes; it never calls out.

A lamp is one of: ok, degraded, down, stale (no report within STALE_S: grey, never green), off (stopped on purpose).
"""
from __future__ import annotations

import threading
import time

STALE_S = 120                 # the core reporter posts every 30 s; four missed reports is stale
MAX_ITEMS = 120
LAMPS = ("ok", "degraded", "down", "stale", "off", "hung")      # hung: a job is running and nothing new has come out of it (PLATINUM §24)
HUNG_S = 90                                                       # no new words for this long, with a job running


def _s(value, limit: int = 80) -> str:
    return str(value if value is not None else "")[:limit]


def _num(value, lo=0.0, hi=1e12):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi else None


def clean_containers(rows) -> list[dict]:
    """A machine's container list as reported (skippy-core's reporter, skippy-pc's starter): names and numbers only, bounded."""
    out = []
    for r in (rows if isinstance(rows, list) else [])[:MAX_ITEMS]:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        out.append({"name": _s(r["name"], 60), "state": _s(r.get("state"), 20), "health": _s(r.get("health"), 20),
                    "status": _s(r.get("status"), 60), "image": _s(r.get("image"), 80),
                    "restarts": int(_num(r.get("restarts"), 0, 99999) or 0), "cpu": _num(r.get("cpu"), 0, 10000),
                    "mem_mb": _num(r.get("mem_mb"), 0, 10_000_000)})
    return out


def clean_models(rows) -> list[dict]:
    out = []
    for r in (rows if isinstance(rows, list) else [])[:MAX_ITEMS]:
        if isinstance(r, dict) and r.get("name"):
            out.append({"name": _s(r["name"], 60), "state": _s(r.get("state"), 20), "detail": _s(r.get("detail"), 120),
                        "jobs": int(_num(r.get("jobs"), 0, 999) or 0), "decoded": int(_num(r.get("decoded"), 0, 10**9) or 0),
                        "stuck_s": int(_num(r.get("stuck_s"), 0, 10**7) or 0)})
    return out


def container_lamp(c: dict) -> str:
    state, health = c.get("state", ""), c.get("health", "")
    if state in ("exited", "dead", "removing") or state == "":
        return "off" if state == "exited" and c.get("restarts", 0) == 0 else "down"
    if state == "restarting":
        return "down"
    if health == "unhealthy":
        return "degraded"
    if state == "running":
        return "degraded" if c.get("restarts", 0) >= 5 else "ok"
    return "degraded"


KNOWN_CORE_MODELS = ("chat", "coder", "router", "vision")      # llama-swap's slots (core/models.json): shown even when one is not loaded right now


class Probes:
    """A tiny test question to a model, to see that it answers (the Captain, 2026-10-27: "send a test prompt to dormant models to see if they
    respond"). Straight to Skippy Core's Messages API through core_test (doctor.py), one model at a time, in the background; the answer is
    "responded in N ms" or why not. A sweep never starts while the private Skippy is working on the box, and never two at once."""

    def __init__(self, test, clock=time.time):
        self.test, self.clock = test, clock           # test(model) -> (ok, why, ms)
        self.results: dict[str, dict] = {}
        self.running: str | None = None
        self._lock = threading.Lock()

    def start(self, names, busy: bool, spawn=None) -> tuple[bool, str]:
        names = [n for n in (names if isinstance(names, list) else []) if n in KNOWN_CORE_MODELS]
        if not names:
            return False, "Nothing to ask: name one of " + ", ".join(KNOWN_CORE_MODELS) + "."
        if busy:
            return False, "The private Skippy is working on skippy-core: probes wait until it is idle (they would load models under it)."
        with self._lock:
            if self.running:
                return False, f"Already asking {self.running}."
            self.running = names[0]
        (spawn or self._thread)(names)
        return True, "Asking " + ", ".join(names) + "."

    def _thread(self, names) -> None:
        threading.Thread(target=self.run, args=(names,), daemon=True).start()

    def run(self, names) -> None:
        try:
            for name in names:
                with self._lock:
                    self.running = name
                try:
                    ok, why, ms = self.test(name)
                except Exception as e:  # noqa: BLE001 — a probe that blows up is a failed probe
                    ok, why, ms = False, f"error ({type(e).__name__})", 0
                with self._lock:
                    self.results[name] = {"ok": bool(ok), "why": _s(why, 120), "ms": int(ms), "at": int(self.clock())}
        finally:
            with self._lock:
                self.running = None

    def view(self) -> dict:
        with self._lock:
            return {"results": {k: dict(v) for k, v in self.results.items()}, "running": self.running}


class Status:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self.core: dict | None = None
        self.core_at: float | None = None

    # ── skippy-core's side: POST /home/core-report ───────────────────────────────
    def accept_core(self, body: dict) -> None:
        deployer = body.get("deployer") if isinstance(body.get("deployer"), dict) else {}
        doctor = body.get("doctor") if isinstance(body.get("doctor"), dict) else {}
        vitals = body.get("vitals") if isinstance(body.get("vitals"), dict) else {}
        clean = {"containers": clean_containers(body.get("containers")), "models": clean_models(body.get("models")),
                 "aliases": {_s(k, 40): _s(v, 40) for k, v in list((body.get("aliases") or {}).items())[:40]} if isinstance(body.get("aliases"), dict) else {},
                 "deployer": {"state": _s(deployer.get("state"), 30), "commit": _s(deployer.get("commit"), 12), "detail": _s(deployer.get("detail"), 160)},
                 "doctor": {"ok": doctor.get("ok") is not False, "last": _s(doctor.get("last"), 120)},
                 "vitals": {k: _num(vitals.get(k), 0, 1e9) for k in ("mem_pct", "disk_pct", "load1", "uptime_h", "ups_charge") if _num(vitals.get(k), 0, 1e9) is not None},
                 "busy": bool(body.get("busy"))}
        with self._lock:
            self.core, self.core_at = clean, self.clock()

    def core_age(self) -> int | None:
        return None if self.core_at is None else int(self.clock() - self.core_at)

    # ── the map ────────────────────────────────────────────────────────────────
    def tree(self, *, home_online: bool, home_age: int | None, home_report: dict | None, starter_view: dict, portal: dict,
             games_vm: dict | None, core_verdict: dict | None, activity: dict | None = None, probes: dict | None = None) -> dict:
        """The whole map as plain data. Every argument is read from the portal's own objects by the route; nothing here calls out."""
        report = home_report or {}
        st = starter_view.get("status") or {}
        core, core_age = self.core, self.core_age()
        core_fresh = core is not None and core_age is not None and core_age <= STALE_S

        pc_containers = clean_containers(st.get("containers"))
        pc_state = "ok" if home_online else ("stale" if home_age is not None else "off")
        if home_online and not st.get("docker", True):
            pc_state = "degraded"
        pc_services = [{"kind": "endpoint", "name": _s(k).replace("_", " "), "lamp": "ok" if v else "down"}
                       for k, v in sorted((report.get("capabilities") or {}).items()) if isinstance(v, bool)]
        for s in report.get("services") or []:
            if isinstance(s, dict) and s.get("name"):
                pc_services.append({"kind": "service", "name": _s(s["name"], 40),
                                    "lamp": "ok" if s.get("available") else ("off" if s.get("planned") else "down"), "detail": _s(s.get("status_detail"), 100)})
        pc = {"id": "skippy-pc", "label": "skippy-pc", "state": pc_state, "age": home_age,
              "vitals": {k: st[k] for k in ("gpu_util_pct", "gpu_free_pct", "ups_charge") if k in st},
              "busy": bool(st.get("busy")),
              "containers": [{**c, "lamp": container_lamp(c)} for c in pc_containers],
              "serves": pc_services}

        core_state = "stale" if core is not None and not core_fresh else ("off" if core is None else "ok")
        core_serves = []
        containers = []
        if core:
            containers = [{**c, "lamp": container_lamp(c)} for c in core["containers"]]
            for m in core["models"]:
                if m["state"] != "ready":
                    lamp, detail = ("degraded" if m["state"] else "off"), (m["detail"] or m["state"])
                elif m["jobs"] and m["stuck_s"] >= HUNG_S:
                    lamp = "hung"
                    detail = f"HUNG: {m['jobs']} job{'s' if m['jobs'] != 1 else ''} running, no new words for {m['stuck_s'] // 60} min {m['stuck_s'] % 60} s"
                elif m["jobs"]:
                    lamp, detail = "ok", f"busy: {m['jobs']} job{'s' if m['jobs'] != 1 else ''} running, {m['decoded']} words so far"
                else:
                    lamp, detail = "ok", "ready: accepting work, no jobs"
                core_serves.append({"kind": "model", "name": m["name"], "lamp": lamp, "detail": detail, "jobs": m["jobs"], "stuck_s": m["stuck_s"]})
            have = {s["name"] for s in core_serves}
            for name in KNOWN_CORE_MODELS:                 # a slot that is not loaded right now is still a model he can ask
                if name not in have:
                    core_serves.append({"kind": "model", "name": name, "lamp": "off", "detail": "not loaded: it loads when asked"})
            for s in core_serves:
                if s["kind"] == "model":
                    s["probe"] = ((probes or {}).get("results") or {}).get(s["name"])
                    s["asking"] = (probes or {}).get("running") == s["name"]
            if core["deployer"]["state"] in ("failed", "rolled-back", "refused", "ci-failed"):
                core_state = "degraded" if core_fresh else core_state
            if any(s["lamp"] == "hung" for s in core_serves) and core_fresh:
                core_state = "degraded"
            if not core["doctor"]["ok"] and core_fresh:
                core_state = "degraded"
            if any(c["lamp"] == "down" for c in containers) and core_fresh:
                core_state = "degraded"
        if core_verdict and core_verdict.get("verdict") == "down" and not core_fresh:
            core_state = "down"
        core_node = {"id": "skippy-core", "label": "skippy-core", "state": core_state, "age": core_age,
                     "vitals": (core or {}).get("vitals", {}), "busy": bool((core or {}).get("busy")),
                     "deployer": (core or {}).get("deployer"), "doctor": (core or {}).get("doctor"),
                     "containers": containers, "serves": core_serves}

        vm_state = "off"
        if games_vm is not None:
            vm_state = {"RUNNING": "ok", "TERMINATED": "off", "STOPPED": "off"}.get(_s(games_vm.get("status")).upper(), "degraded")
        vm = {"id": "games-vm", "label": "games VM", "state": vm_state, "age": None, "vitals": {}, "busy": False, "containers": [],
              "serves": [{"kind": "service", "name": "game servers", "lamp": vm_state if vm_state in ("ok", "off") else "degraded",
                          "detail": _s((games_vm or {}).get("status"), 20)}]}

        portal_node = {"id": "skippy-portal", "label": "skippy-portal", "state": "ok", "age": 0, "vitals": {}, "busy": False,
                       "containers": [], "serves": [{"kind": "service", "name": "portal", "lamp": "ok",
                                                     "detail": _s(portal.get("short") or portal.get("commit"), 12)},
                                                    {"kind": "service", "name": "revision", "lamp": "ok", "detail": _s(portal.get("revision"), 40)}]}

        for node in (pc, core_node):                      # what each container said lately: {n, errors, last} (portal/logs.py activity)
            for c in node["containers"]:
                c["logs"] = (activity or {}).get(f"{node['id']}|docker/{c['name']}")
        machines = [portal_node, pc, core_node, vm]
        by = {m["id"]: m for m in machines}

        def edge(a, b, label, ok, kind="link"):
            lamp = "ok" if ok else ("stale" if by[a]["state"] == "stale" or by[b]["state"] == "stale" else "down")
            if by[a]["state"] == "off" or by[b]["state"] == "off":
                lamp = "off"
            return {"from": a, "to": b, "label": label, "lamp": lamp, "kind": kind}

        edges = [edge("skippy-portal", "skippy-pc", "home report", home_online),
                 edge("skippy-portal", "skippy-core", "core report + Funnel", core_fresh),
                 edge("skippy-pc", "skippy-core", "data trunk + tailnet", home_online and core_fresh, "trunk"),
                 edge("skippy-portal", "games-vm", "ops", vm_state == "ok")]
        problems = [{"machine": m["id"], "what": m["label"] + " is " + m["state"]} for m in machines if m["state"] in ("down", "degraded", "stale")]
        for m in machines:
            problems += [{"machine": m["id"], "what": s["name"] + " is hung: " + s["detail"]} for s in m["serves"] if s.get("lamp") == "hung"]
        for m in machines:
            problems += [{"machine": m["id"], "what": c["name"] + " is " + c["lamp"]} for c in m["containers"] if c["lamp"] in ("down", "degraded")]
        return {"machines": machines, "edges": edges, "problems": problems, "at": int(self.clock())}
