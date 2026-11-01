"""The Status map: one tree of the house, built from what each machine reports out (docs/status-design.md).

Every machine reports on its own and none depends on another being up, which is what makes this a quorum and not a chain:
skippy-pc through the home report and the starter, skippy-core through `core/reporter` (POST /home/core-report, the home token),
the games VM through the portal's own VM status, and the portal itself. This module only reads and shapes; it never calls out.

A lamp is one of: ok, degraded, down, stale (no report within STALE_S: grey, never green), off (stopped on purpose).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

STALE_S = 120                 # the core reporter posts every 30 s; four missed reports is stale
MAX_ITEMS = 120
LAMPS = ("ok", "degraded", "down", "stale", "off", "hung", "call")   # call (blue): on call, a tool that loads when asked and is not warm right now      # hung: a job is running and nothing new has come out of it (PLATINUM §24)
HUNG_S = 90                                                       # no new words for this long, with a job running


NODES = json.loads((Path(__file__).resolve().parent / "status_nodes.json").read_text(encoding="utf-8"))
USAGE_KEYS = ("asks", "direct", "tools_called", "tools_total", "schema_tokens", "since",
              "calls", "tokens_in", "tokens_out", "haiku_usd", "power_usd", "net_usd")      # what a machine may report about how a node is used


def node_key(name: str) -> str:
    """A node's catalog key: a container's name without its project prefix and replica number, a model's or service's own name."""
    return re.sub(r"-\d+$", "", re.sub(r"^(skippycore|skippytel)-", "", str(name or "")))


def about(name: str, usage: dict | None = None) -> dict | None:
    """The catalog entry for a node (what it is for, what uses it, an example), with live usage numbers when the machine reported any.
    A capability's key (make_image) finds the entry of the same name with hyphens (make-image)."""
    entry = NODES.get(node_key(name)) or NODES.get(node_key(name).replace("_", "-"))
    if not entry:
        return None
    out = {k: entry[k] for k in ("for", "used_by", "example", "note") if k in entry}
    mine = (usage or {}).get(node_key(name))
    if isinstance(mine, dict) and mine:
        out["usage"] = mine
    return out


def _s(value, limit: int = 80) -> str:
    return str(value if value is not None else "")[:limit]


def _num(value, lo=0.0, hi=1e12):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi else None


HUB_STALE_S = 180            # the hub writes its heartbeat every minute (services/satellite/hub.py)


def clean_hub(v) -> dict | None:
    """The room hub's heartbeat as the starter sent it: {age, rooms: {name: bool}, house: {backend: {ok, things?, why?}}}, bounded."""
    if not isinstance(v, dict) or not isinstance(v.get("age"), int):
        return None
    rooms = {_s(k, 40): bool(x) for k, x in list((v.get("rooms") or {}).items())[:20]} if isinstance(v.get("rooms"), dict) else {}
    house = {}
    for k, x in list((v.get("house") or {}).items())[:20] if isinstance(v.get("house"), dict) else []:
        if isinstance(x, dict):
            house[_s(k, 40)] = {"ok": bool(x.get("ok")), **({"things": x["things"]} if _num(x.get("things"), 0, 10000) is not None else {}),
                                **({"why": _s(x["why"], 160)} if x.get("why") else {})}
    return {"age": max(0, min(v["age"], 10**7)), "rooms": rooms, "house": house}


def row(kind: str, name: str, lamp: str, detail: str, key: str | None = None) -> dict:
    """One line on a machine: a service, an app, a device or a link; its two dots follow its lamp."""
    ready = lamp if lamp in ("ok", "down", "off", "hung", "call") else ("down" if lamp in ("degraded", "stale") else "off")
    return {"kind": kind, "name": name, "lamp": lamp, "detail": detail, "about": about(key or name), "reach": "down" if lamp == "down" else ready, "ready": ready}


def host_rows(st: dict, have: set[str]) -> list[dict]:
    """What runs on skippy-pc outside Docker, from the starter: OpenAudible, Spotify, VB-Cable, the bridge, and the room hub with its pods
    and its house backends (HomeKit, Matter, the garage, Leviton, the Nest)."""
    out = []
    if isinstance(st.get("openaudible"), bool):
        out.append(row("app", "openaudible", "ok" if st["openaudible"] else "down",
                       "web server answers" if st["openaudible"] else ("the app runs, its web server is off" if st.get("openaudible_app") else "the app isn't running")))
    if isinstance(st.get("spotify"), bool):
        out.append(row("app", "spotify desktop", "ok" if st["spotify"] else "down", "running" if st["spotify"] else "not running", "spotify-desktop"))
    if isinstance(st.get("vbcable"), bool):
        out.append(row("app", "vb-cable", "ok" if st["vbcable"] else "down", "installed" if st["vbcable"] else "missing: Spotify has no sound path"))
    if isinstance(st.get("bridge"), bool) and "bridge" not in have:
        out.append(row("app", "bridge", "ok" if st["bridge"] else "down", "answers" if st["bridge"] else "not answering"))
    hub = st.get("hub")
    if isinstance(hub, dict):
        fresh = hub["age"] <= HUB_STALE_S
        rooms = hub.get("rooms") or {}
        away = [r for r, up in rooms.items() if not up]
        lamp = ("degraded" if away else "ok") if fresh else "stale"
        out.append(row("app", "hub", lamp, (f"rooms {', '.join(r for r, up in rooms.items() if up) or 'none'}" + (f"; away: {', '.join(away)}" if away else ""))
                       if fresh else f"no heartbeat for {hub['age'] // 60} min: the hub may be stopped"))
        for r, up in rooms.items():
            out.append(row("device", f"pod {r}", ("ok" if up else "down") if fresh else "stale", "connected" if up else "not connected", "pod"))
        for b, h in (hub.get("house") or {}).items():
            out.append(row("link", b, ("ok" if h["ok"] else "down") if fresh else "stale",
                           (f"{h['things']} things" if h["ok"] and "things" in h else "answers") if h["ok"] else h.get("why", "not answering")))
    return out


def clean_containers(rows) -> list[dict]:
    """A machine's container list as reported (skippy-core's reporter, skippy-pc's starter): names and numbers only, bounded."""
    out = []
    for r in (rows if isinstance(rows, list) else [])[:MAX_ITEMS]:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        status_text = _s(r.get("status"), 60)
        health = _s(r.get("health"), 20) or ("unhealthy" if "(unhealthy)" in status_text else "healthy" if "(healthy)" in status_text
                                              else "starting" if "health: starting" in status_text else "")     # docker's own words, when the machine sent only its status text
        out.append({"name": _s(r["name"], 60), "state": _s(r.get("state"), 20), "health": health,
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


def container_dots(c: dict) -> tuple[str, str]:
    """(reach, ready) for a container, the two dots on the Status page: is it up at all, and does it say it is well. A container only has
    Docker's own health check to go on, so ready is grey (unknown) unless there is one."""
    state, health = c.get("state", ""), c.get("health", "")
    reach = "ok" if state == "running" else ("off" if state == "exited" and not c.get("restarts") else "down")
    if reach != "ok":
        return reach, "off"
    ready = "ok" if health == "healthy" else ("down" if health == "unhealthy" else "off")
    return reach, ready


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
                 "busy": bool(body.get("busy")),
                 "usage": {_s(k, 40): {u: (_s(v.get(u), 20) if u == "since" else _num(v.get(u), 0, 1e12)) for u in USAGE_KEYS if v.get(u) is not None}
                           for k, v in list((body.get("usage") or {}).items())[:20] if isinstance(v, dict)} if isinstance(body.get("usage"), dict) else {}}
        with self._lock:
            self.core, self.core_at = clean, self.clock()

    def core_age(self) -> int | None:
        return None if self.core_at is None else int(self.clock() - self.core_at)

    # ── the map ────────────────────────────────────────────────────────────────
    def tree(self, *, home_online: bool, home_age: int | None, home_report: dict | None, starter_view: dict, portal: dict,
             games_vm: dict | None, core_verdict: dict | None, activity: dict | None = None, probes: dict | None = None,
             links: list[dict] | None = None) -> dict:
        """The whole map as plain data. Every argument is read from the portal's own objects by the route; nothing here calls out."""
        report = home_report or {}
        st = starter_view.get("status") or {}
        core, core_age = self.core, self.core_age()
        core_fresh = core is not None and core_age is not None and core_age <= STALE_S

        pc_containers = clean_containers(st.get("containers"))
        pc_state = "ok" if home_online else ("stale" if home_age is not None else "off")
        if home_online and not st.get("docker", True):
            pc_state = "degraded"
        pc_services = []
        for k, v in sorted((report.get("capabilities") or {}).items()):
            if isinstance(v, bool):
                v = {"ready": v}
            if not isinstance(v, dict):
                continue
            up = v.get("ready") is True
            on_call = up and v.get("loaded") is False                  # a service that loads its model when asked, and has not: on call (blue)
            lamp = "call" if on_call else ("ok" if up else "down")
            pc_services.append({"kind": "endpoint", "name": _s(k).replace("_", " "), "lamp": lamp, "about": about(k),
                                "detail": "on call: loads when asked" if on_call else ("ready" if up else "not ready"),
                                "reach": "ok" if up else "down", "ready": "call" if on_call else ("ok" if up else "down")})
        for s in report.get("services") or []:
            if isinstance(s, dict) and s.get("name"):
                lamp = "ok" if s.get("available") else ("off" if s.get("planned") else "down")
                pc_services.append({"kind": "service", "name": _s(s["name"], 40), "lamp": lamp, "detail": _s(s.get("status_detail"), 100), "about": about(s["name"]),
                                    "reach": lamp, "ready": lamp})
        for name, d in sorted((report.get("doctor") or {}).items()):          # what each service SAID about itself (PLATINUM section 24)
            if not isinstance(d, dict):
                continue
            jobs, stuck, state = int(d.get("jobs") or 0), int(d.get("stuck_s") or 0), d.get("state")
            plural = "s" if jobs != 1 else ""
            if state == "ready":
                lamp, detail = "ok", "ready: accepting work, no jobs"
            elif state == "busy":
                lamp, detail = "ok", f"busy: {jobs} job{plural} running"
            elif state == "hung":
                lamp, detail = "hung", f"HUNG: {jobs} job{plural} running, no progress for {stuck // 60} min {stuck % 60} s"
            elif state == "not-ready":
                lamp, detail = "down", _s(d.get("why"), 120) or "it says it isn't ready"
            else:                                                     # a plain answer: up, but it cannot say whether it is ready
                lamp = "down" if d.get("ok") is False else "off"
                detail = _s(d.get("why"), 120) if d.get("ok") is False else "answers, but cannot yet say whether it is ready"
            reach = "ok" if d.get("reachable") is not False and (d.get("ok") or state or d.get("reachable")) else "down"
            ready = {"ready": "ok", "busy": "ok", "hung": "hung", "not-ready": "down"}.get(state, "off" if reach == "ok" else "down")
            pc_services.append({"kind": "service", "name": _s(name, 40), "lamp": lamp, "detail": detail, "jobs": jobs, "reach": reach, "ready": ready,
                                "about": about(name)})
        pc_services += host_rows(st, {s["name"] for s in pc_services})
        if any(s.get("lamp") == "hung" for s in pc_services) and pc_state == "ok":
            pc_state = "degraded"
        pc = {"id": "skippy-pc", "label": "skippy-pc", "state": pc_state, "age": home_age, "about": about("skippy-pc"),
              "vitals": {k: st[k] for k in ("gpu_util_pct", "gpu_free_pct", "ups_charge") if k in st},
              "busy": bool(st.get("busy")),
              "containers": [{**c, "lamp": container_lamp(c), "about": about(c["name"])} for c in pc_containers],
              "serves": pc_services}

        core_state = "stale" if core is not None and not core_fresh else ("off" if core is None else "ok")
        core_serves = []
        containers = []
        if core:
            containers = [{**c, "lamp": container_lamp(c), "about": about(c["name"], core.get("usage"))} for c in core["containers"]]
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
                loaded = m["state"] == "ready"
                core_serves.append({"kind": "model", "name": m["name"], "lamp": lamp, "detail": detail, "jobs": m["jobs"], "stuck_s": m["stuck_s"], "about": about(m["name"]),
                                    "reach": "ok" if loaded else ("down" if m["state"] else "off"),
                                    "ready": "hung" if lamp == "hung" else ("ok" if lamp == "ok" else ("down" if m["state"] else "off"))})
            have = {s["name"] for s in core_serves}
            for name in KNOWN_CORE_MODELS:                 # a slot that is not loaded right now is still a model he can ask
                if name not in have:
                    core_serves.append({"kind": "model", "name": name, "lamp": "call", "detail": "on call: loads when asked", "reach": "ok", "ready": "call", "about": about(name)})
            for s in core_serves:
                if s["kind"] == "model":
                    s["probe"] = ((probes or {}).get("results") or {}).get(s["name"])
                    if s["probe"] and s["probe"]["ok"]:
                        s["lamp"] = "ok"
                    if s["probe"]:                              # a real question was asked: what it did with it is the truest "ready"
                        s["reach"], s["ready"] = ("ok", "ok") if s["probe"]["ok"] else ("ok", "down")
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
        core_node = {"id": "skippy-core", "label": "skippy-core", "state": core_state, "age": core_age, "about": about("skippy-core"),
                     "vitals": (core or {}).get("vitals", {}), "busy": bool((core or {}).get("busy")),
                     "deployer": (core or {}).get("deployer"), "doctor": (core or {}).get("doctor"),
                     "containers": containers, "serves": core_serves}

        vm_state = "off"
        if games_vm is not None:
            vm_state = {"RUNNING": "ok", "TERMINATED": "off", "STOPPED": "off"}.get(_s(games_vm.get("status")).upper(), "degraded")
        vm = {"id": "games-vm", "label": "games VM", "state": vm_state, "age": None, "vitals": {}, "busy": False, "containers": [], "about": about("games-vm"),
              "serves": [{"kind": "service", "name": "game servers", "lamp": vm_state if vm_state in ("ok", "off") else "degraded",
                          "detail": _s((games_vm or {}).get("status"), 20), "about": about("game servers")}]}

        portal_node = {"id": "skippy-portal", "label": "skippy-portal", "state": "ok", "age": 0, "vitals": {}, "busy": False, "about": about("skippy-portal"),
                       "containers": [], "serves": [{"kind": "service", "name": "portal", "lamp": "ok",
                                                     "detail": _s(portal.get("short") or portal.get("commit"), 12), "about": about("portal")},
                                                    {"kind": "service", "name": "revision", "lamp": "ok", "detail": _s(portal.get("revision"), 40),
                                                     "about": about("revision")}]
                       + [row("link", _s(x.get("name"), 40), x.get("lamp") if x.get("lamp") in LAMPS else "off", _s(x.get("detail"), 120), x.get("key"))
                          for x in (links or []) if isinstance(x, dict) and x.get("name")]}

        for node in (pc, core_node):                      # what each container said lately: {n, errors, last} (portal/logs.py activity)
            for c in node["containers"]:
                c["logs"] = (activity or {}).get(f"{node['id']}|docker/{c['name']}")
        for node in (pc, core_node):                      # the two dots of every container
            for c in node["containers"]:
                c["reach"], c["ready"] = container_dots(c)
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
