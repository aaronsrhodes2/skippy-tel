"""The portal tidies its own controls from what people tap (the Captain, 2026-11-02: "auto-pruning / promoting / merging UI
elements based on usage telemetry ... a built-in feature").

Once a night skippy-pc (tools/adaptive_ui.py, from tools/nightly.py) reads the portal's telemetry (taps, and the days each control
was on screen: the `seen:` beacon in base.html) and POSTs a summary to /home/adaptive. `apply()` then decides, with the rules below,
and acts through the Graveyard (portal/modules.py), so every change is undone the same way as a hand-made one:

  prune    a control on screen at least MIN_DAYS of the last WINDOW_DAYS and tapped on at most PRUNE_RATE of those days goes to the
           Graveyard. One per surface (the chat's keys, the launcher, the /me panes) per COOLDOWN_DAYS, the least used first.
  promote  the launcher's order follows use: a tile tapped more than twice as often as the one before it (and at least PROMOTE_MIN
           times) moves up one place, one move per COOLDOWN_DAYS. Skippy's tile stays first.
  merge    two controls nearly always tapped one after the other (B within seconds of A on at least MERGE_SHARE of A's taps) are
           written down as a candidate to fold together. A merge changes code, so it is a suggestion in the ledger, never automatic.

Never touched: what is not retirable (Admin), Skippy's tile, and anything the Captain revived by hand (a pin: reviving says "keep
it"). Every change goes into the ledger with its reason, shown in Admin > Graveyard with its Revive key and in the overnight report.

State lives in the games VM's metadata (`portal-adaptive`, JSON) beside the retired list, with modules.py's rules: a failed read
changes nothing, a failed write is reported.
"""
from __future__ import annotations

import json
import re
import time

import modules as modules_mod

KEY = "portal-adaptive"
WINDOW_DAYS = 30            # what "recently" means
MIN_DAYS = 14               # a control must have been on screen this many days before it can be judged
PRUNE_RATE = 0.15           # taps per day on screen at or under which a control is pruned (4 in 30 days is 0.13)
COOLDOWN_DAYS = 7           # one change per surface per week: the UI must not shuffle under his fingers
PROMOTE_MIN = 5
MERGE_MIN, MERGE_SHARE = 5, 0.6
LEDGER_MAX = 80
DAY = 86400.0
FLOOR = frozenset({"skippy", "admin"})
SURFACES = ("chat", "launcher", "me")
# Where a control's own days on screen may be stood in for by its surface's days in use, before the seen beacon (2026-11-02) has
# counted them: only the chat's keys, which have all been on its bar for months. A launcher tile can be a day old (Alarms was, the
# night this started: "never tapped, on screen 27 days" was wrong), so tiles and panes wait for their own MIN_DAYS of seen.
STAND_IN = frozenset({"chat"})

# Every other control with a telemetry id (Bilby's back-one-song key, a sheet's key, a deck tile built by script) can be retired too
# (the Captain, 2026-11-02): it is hidden on every page (base.html, from hidden()) and listed in the Graveyard as ctl:<id>. Its own
# days on screen must reach MIN_DAYS (no stand-in), and one per page goes per week. Never: media and what a page needs to work.
MEDIA = re.compile(r"^(tile|tray|pane|play|demo|copy|seen):")   # content (game cards, games, demos, rows) and its helpers: "media does NOT
                                                                # fall off" (the Captain); tile/tray/pane are the Graveyard's modules
FLOOR_CTL = re.compile(r"^(base|pager|admin|login|request-access|unauthorized|recovery|join|voice|skippy3d|annotate):"
                       r"|(?:^|[:-])(back|close|home|send|stop|play|pause|toggle|cancel|esc|sign-in|sign-out|privacy|fs-key)$"
                       r"|:back-to-|:(voice|mode|chats|apps)$")
APP_NAMES = {"music": "Bilby", "video": "Nagatha", "books": "Audiobooks", "audiobooks": "Audiobooks", "drive": "Drive", "chat": "Chat",
             "workshop": "Private Skippy", "alarms": "Alarms", "tabletop": "Tabletop", "game": "Game page", "games": "Game servers",
             "me": "Dashboard", "status": "Status", "experiments": "Experiments", "camera": "Camera"}
CTL_ID = re.compile(r"[a-z0-9:_.\-]{1,60}")


def control_label(name: str) -> str:
    app, _, rest = name.partition(":")
    return f"{APP_NAMES.get(app, app.title())}: {rest.replace('-', ' ')}"


def retirable_control(name: str) -> bool:
    """A telemetry name the tidy may retire as a control: not a module (the Graveyard's own), not media, not a floor."""
    return bool(CTL_ID.fullmatch(name)) and ":" in name and not module_of_name(name) and not MEDIA.match(name) and not FLOOR_CTL.search(name)


def surface_of(module_id: str) -> str:
    return "chat" if module_id.startswith("key-") else "me" if module_id.startswith("me-") else "launcher"


def tel_names(module_id: str) -> tuple[str, ...]:
    """The telemetry names that are taps on this module (base.html's data-tel ids)."""
    if module_id.startswith("key-"):
        return ("chat:" + module_id[4:],)
    if module_id.startswith("me-"):
        return ("pane:" + module_id[3:],)
    return ("tile:" + module_id, "tray:" + module_id)     # the launcher's tile, and the same app in the chat's tray


def surface_of_name(name: str) -> str | None:
    head = name.split(":", 1)[0]
    return {"chat": "chat", "pane": "me", "tile": "launcher", "tray": "launcher"}.get(head)


def module_of_name(name: str) -> str | None:
    for m in modules_mod.MODULES:
        if name in tel_names(m.id):
            return m.id
    return None


def empty_state() -> dict:
    return {"ledger": [], "pins": [], "order": [], "last": {}, "hidden": []}


def _usage(stats: dict, module_id: str, surface: str) -> tuple[int, int, float | None]:
    """(taps in the window, days on screen, days since the last tap) for a module. Before the seen beacon has a control's days,
    the days its surface was used at all stand in where STAND_IN allows (a key on the chat's bar is on screen whenever the chat is)."""
    controls = stats.get("controls") or {}
    taps, seen, last = 0, 0, None
    for n in tel_names(module_id):
        c = controls.get(n) or {}
        taps += int(c.get("taps") or 0)
        seen = max(seen, int(c.get("seen_days") or 0))
        if c.get("last_days") is not None:
            last = c["last_days"] if last is None else min(last, c["last_days"])
    if not seen and surface in STAND_IN:
        seen = int((stats.get("surface_days") or {}).get(surface) or 0)
    return taps, seen, last


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def decide(stats: dict, retired: frozenset, state: dict, now: float) -> list[dict]:
    """The changes to make tonight (pure: tests call it directly)."""
    window = int(stats.get("window_days") or WINDOW_DAYS)
    if float(stats.get("span_days") or 0) < MIN_DAYS:
        return []                                            # not enough history to judge anything
    pins, last = set(state.get("pins") or []), dict(state.get("last") or {})
    cooling = lambda key: now - float(last.get(key) or 0) < COOLDOWN_DAYS * DAY   # noqa: E731
    changes: list[dict] = []

    # prune: the least-used control of each surface, one a week
    for surface in SURFACES:
        if cooling(surface):
            continue
        cands = []
        for m in modules_mod.MODULES:
            if (surface_of(m.id) != surface or not m.retirable or m.id in FLOOR or m.id in retired or m.id in pins):
                continue
            taps, seen, last_tap = _usage(stats, m.id, surface)
            if seen < MIN_DAYS or taps / seen > PRUNE_RATE:
                continue
            cands.append((taps / seen, taps, m, seen, last_tap))
        if cands:
            rate, taps, m, seen, last_tap = min(cands, key=lambda c: (c[0], c[1], c[2].id))
            when = "never tapped" if not taps else f"{_plural(taps, 'tap')} in {window} days"
            ago = f", the last {round(last_tap)} days ago" if taps and last_tap is not None and last_tap >= 1 else ""
            changes.append({"kind": "retire", "id": m.id, "surface": surface, "label": m.label,
                            "why": f"{when}{ago}, on screen {seen} of them"})

    # any other control: the least used on each page, one a week, on its own days on screen only
    hidden = set(state.get("hidden") or [])
    per_page: dict[str, list] = {}
    for name, c in (stats.get("controls") or {}).items():
        if name in hidden or "ctl:" + name in pins or not retirable_control(name):
            continue
        seen, taps = int(c.get("seen_days") or 0), int(c.get("taps") or 0)
        if seen < MIN_DAYS or taps / seen > PRUNE_RATE:
            continue
        per_page.setdefault(name.split(":", 1)[0], []).append((taps / seen, taps, name, seen, c.get("last_days")))
    for page, cands in sorted(per_page.items()):
        if cooling("page:" + page):
            continue
        rate, taps, name, seen, last_tap = min(cands)
        when = "never tapped" if not taps else f"{_plural(taps, 'tap')} in {window} days"
        ago = f", the last {round(last_tap)} days ago" if taps and last_tap is not None and last_tap >= 1 else ""
        changes.append({"kind": "retire", "id": "ctl:" + name, "surface": "page:" + page, "label": control_label(name),
                        "why": f"{when}{ago}, on screen {seen} of them"})

    # promote: the launcher's order, one move a week
    if not cooling("launcher-order"):
        order = launcher_order(state)
        taps = {mid: _usage(stats, mid, "launcher")[0] for mid in order}
        live = [mid for mid in order if mid not in retired]
        for a, b in zip(live[1:], live[2:]):                  # live[0] is Skippy's tile: it stays first
            if taps[b] >= PROMOTE_MIN and taps[b] > 2 * taps[a]:
                label = modules_mod.BY_ID[b].label
                changes.append({"kind": "order", "id": b, "surface": "launcher-order", "label": label, "before": a,
                                "why": f"{_plural(taps[b], 'tap')} in {window} days against {taps[a]} for "
                                       f"{modules_mod.BY_ID[a].label}: moved ahead of it"})
                break

    # merge: written down, never done by itself
    suggested = {tuple(e.get("pair") or ()) for e in state.get("ledger") or [] if e.get("kind") == "merge"}
    controls = stats.get("controls") or {}
    for a, b, n in sorted(stats.get("pairs") or [], key=lambda p: -int(p[2])):
        ta = int((controls.get(a) or {}).get("taps") or 0)
        if a == b or (a, b) in suggested or n < MERGE_MIN or not ta or n < MERGE_SHARE * ta:
            continue
        if not (surface_of_name(a) and surface_of_name(b)):
            continue
        name = lambda x: modules_mod.BY_ID[module_of_name(x)].label if module_of_name(x) else x   # noqa: E731
        changes.append({"kind": "merge", "id": module_of_name(a) or a, "pair": [a, b], "surface": "merge",
                        "label": f"{name(a)} + {name(b)}",
                        "why": f"{name(b)} follows {name(a)} {n} of its {ta} taps: a candidate to fold into one control"})
        break
    return changes


def launcher_order(state: dict) -> list[str]:
    """The launcher's modules in the order shown: the adaptive order where it has one, then the rest in MODULES order."""
    base = [m.id for m in modules_mod.MODULES if surface_of(m.id) == "launcher"]
    saved = [i for i in state.get("order") or [] if i in base]
    return saved + [i for i in base if i not in saved]


class Adaptive:
    def __init__(self, vm, modules):
        self.vm, self.modules = vm, modules
        self._cache: tuple[float, dict] | None = None

    # ── state ──
    def state(self) -> dict:
        """What is stored (cached a few seconds); a failed read shows the empty state and changes nothing."""
        if self._cache and time.monotonic() - self._cache[0] < modules_mod.CACHE_SECONDS:
            return self._cache[1]
        try:
            st = self._fresh()
        except modules_mod.Unavailable:
            st = empty_state()
        self._cache = (time.monotonic(), st)
        return st

    def _fresh(self) -> dict:
        try:
            raw = self.vm.get_metadata(KEY)
        except Exception as exc:   # noqa: BLE001
            raise modules_mod.Unavailable("couldn't read the adaptive UI's state") from exc
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            raise modules_mod.Unavailable("the adaptive UI's state is unreadable") from None
        st = empty_state()
        if isinstance(data, dict):
            for k in st:
                if isinstance(data.get(k), type(st[k])):
                    st[k] = data[k]
        return st

    def _save(self, st: dict) -> None:
        st["ledger"] = st["ledger"][-LEDGER_MAX:]
        try:
            self.vm.set_metadata(KEY, json.dumps(st, separators=(",", ":")))
        except Exception as exc:   # noqa: BLE001
            raise modules_mod.Unavailable("couldn't save the adaptive UI's state") from exc
        self._cache = None

    # ── what the pages read ──
    def order(self) -> list[str]:
        return launcher_order(self.state())

    def hidden(self) -> list[str]:
        """The controls (telemetry ids) retired outside the module list: base.html hides them on every page."""
        return [h for h in self.state().get("hidden") or [] if CTL_ID.fullmatch(h)]

    def ledger(self, limit: int = 12) -> list[dict]:
        """The latest changes, newest first, each with `when` for the page ("Nov 02")."""
        return [{**e, "when": time.strftime("%b %d", time.localtime(float(e.get("at") or 0)))}
                for e in reversed(self.state()["ledger"][-limit:])]

    # ── the nightly run ──
    def apply(self, stats: dict, now: float | None = None, approved: list | None = None) -> list[dict]:
        """Decide and act. `approved`: module ids the Captain said to retire (in chat): retired now, outside the weekly pace."""
        now = time.time() if now is None else now
        st = self._fresh()
        retired = self.modules.retired()
        changes = []
        for mid in approved or []:
            if mid.startswith("ctl:"):
                name = mid[4:]
                if retirable_control(name) and name not in st["hidden"]:
                    c = (stats.get("controls") or {}).get(name) or {}
                    changes.append({"kind": "retire", "id": mid, "surface": None, "label": control_label(name),
                                    "why": f"the Captain approved it ({_plural(int(c.get('taps') or 0), 'tap')} in {int(stats.get('window_days') or WINDOW_DAYS)} days)"})
                continue
            m = modules_mod.BY_ID.get(mid)
            if m and m.retirable and mid not in FLOOR and mid not in retired:
                taps, seen, _ = _usage(stats, mid, surface_of(mid))
                changes.append({"kind": "retire", "id": mid, "surface": None, "label": m.label,
                                "why": f"the Captain approved it ({_plural(taps, 'tap')} in {int(stats.get('window_days') or WINDOW_DAYS)} days)"})
        for c in changes:                                      # what he approved is this week's change on its surface
            st["last"]["page:" + c["id"][4:].split(":", 1)[0] if c["id"].startswith("ctl:") else surface_of(c["id"])] = now
        changes += decide(stats, retired | {c["id"] for c in changes}, st, now)
        done = []
        for c in changes:
            if c["kind"] == "retire" and c["id"].startswith("ctl:"):
                st["hidden"].append(c["id"][4:])
            elif c["kind"] == "retire":
                self.modules.retire(c["id"])
            elif c["kind"] == "order":
                order = launcher_order(st)
                order.remove(c["id"])
                order.insert(order.index(c["before"]), c["id"])
                st["order"] = order
            if c.get("surface"):
                st["last"][c["surface"]] = now
            entry = {"at": now, **{k: c[k] for k in ("kind", "id", "label", "why", "pair") if k in c}}
            st["ledger"].append(entry)
            done.append(entry)
        st["last"]["run"] = now
        self._save(st)
        return done

    def revive_control(self, ctl_id: str) -> None:
        """Back on every page (ctl:<telemetry id>), and pinned: the tidy never retires it again."""
        name = ctl_id[4:] if ctl_id.startswith("ctl:") else ctl_id
        st = self._fresh()
        st["hidden"] = [h for h in st["hidden"] if h != name]
        if "ctl:" + name not in st["pins"]:
            st["pins"].append("ctl:" + name)
        self._save(st)

    def retire_control(self, ctl_id: str) -> None:
        """By hand from the Graveyard: hidden everywhere, and the pin taken away."""
        name = ctl_id[4:] if ctl_id.startswith("ctl:") else ctl_id
        if not retirable_control(name):
            raise KeyError(ctl_id)
        st = self._fresh()
        if name not in st["hidden"]:
            st["hidden"].append(name)
        st["pins"] = [p for p in st["pins"] if p != "ctl:" + name]
        self._save(st)

    def pin(self, module_id: str) -> None:
        """He revived it by hand: never pruned again. (Retiring it by hand takes the pin away.)"""
        st = self._fresh()
        if module_id not in st["pins"]:
            st["pins"].append(module_id)
            self._save(st)

    def unpin(self, module_id: str) -> None:
        st = self._fresh()
        if module_id in st["pins"]:
            st["pins"].remove(module_id)
            self._save(st)
