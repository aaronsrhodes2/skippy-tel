"""Alarms & timers (`/alarms`, the Captain only): the house's alarm clock (services/satellite/alarm_clock.py) as one bridge screen, and the
portal's backup for the one job the house must never miss: waking him.

The hub (skippy-pc) owns the alarms and rings them. It tells the portal everything every ~15 s (every second while one rings):
POST /home/alarms, the home token, with the schedule, the timers, what is ringing and what went off; the reply carries what the page asked
(stop, snooze, cancel, add), which the hub carries out and acknowledges on its next sync. Whatever he answers first wins: the ringing is the
hub's one object.

The backup (Board.tick, on any request at most every TICK_S, and on a thread): this process is up when skippy-pc is not.
  - an alarm's time came and went (GRACE_S) and the hub never said it went off: his phone, at once (the hub is down, hung, or deaf);
  - one was ringing when the hub went quiet (SILENT_RINGING_S): his phone;
  - the evening before (an alarm within EVENING_S), the hub silent for SILENT_EVENING_S: his phone, so he knows tonight.
Each is one Discord DM (notify.alert), once per alarm. The last schedule is kept in the bucket, so a redeployed portal still knows it.

  GET  /alarms            the page
  GET  /alarms/state      the page's data (JSON)
  POST /alarms/do         {op: stop|snooze|cancel|add|timer|cancel_timer, ...}: queued for the hub; X-Alarms: 1
  POST /home/alarms       the hub's sync (home token)
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

from flask import Blueprint, abort, jsonify, render_template, request

import auth

KEY = "alarms/state.json"
GRACE_S = 90                     # the hub rings within a second of the time; this long without its word, it isn't going to
SILENT_RINGING_S = 120
EVENING_S = 10 * 3600
SILENT_EVENING_S = 15 * 60
LATE_S = 3 * 3600                # an alarm this long past is history, not something to wake him for
TICK_S = 15
SAVE_EVERY_S = 300
COMMAND_TTL_S = 15 * 60
OPS = {"stop", "snooze", "cancel", "add", "timer", "cancel_timer"}
HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
WHERE = {"", "upstairs", "downstairs", "everywhere", "bedroom", "living room", "theater", "kitchen", "office"}
REPEAT = {"once", "daily", "weekdays", "weekends"}


class Board:
    def __init__(self, store=None, notify=None, clock=time.time, page_url: str = "https://makerportal.live/alarms", log=print):
        self.store, self.notify, self.clock, self.page_url, self.log = store, notify or (lambda text: False), clock, page_url, log
        self.view: dict | None = None            # the hub's last sync
        self.seen_at = 0.0                        # when it came
        self.commands: list[dict] = []            # asked on the page, not yet done by the hub
        self.results: dict = {}                   # command id -> what the hub said
        self.alerted: dict = {}                   # what his phone was already told: key -> when
        self._lock = threading.Lock()
        self._ticked = self._saved = 0.0
        self._saved_keys: tuple = ()
        self._load()

    # ── kept in the bucket ──
    def _load(self) -> None:
        if self.store is None:
            return
        try:
            raw = json.loads(self.store.get(KEY) or b"{}")
            self.view, self.seen_at = raw.get("view"), float(raw.get("seen_at") or 0)
            self.alerted = {str(k): float(v) for k, v in (raw.get("alerted") or {}).items()}
        except Exception as e:  # noqa: BLE001 — no bucket or a bad file: start from the hub's next sync
            self.log(json.dumps({"event": "alarms_load", "problem": type(e).__name__}))

    def _save(self, force: bool = False) -> None:
        if self.store is None:
            return
        keys = tuple(sorted(a.get("key", "") for a in (self.view or {}).get("alarms") or [])) + tuple(sorted(self.alerted))
        now = self.clock()
        if not force and keys == self._saved_keys and now - self._saved < SAVE_EVERY_S:
            return
        self._saved, self._saved_keys = now, keys
        body = json.dumps({"view": self.view, "seen_at": self.seen_at, "alerted": self.alerted}).encode()
        try:
            self.store.put(KEY, body, "application/json")
        except Exception as e:  # noqa: BLE001
            self.log(json.dumps({"event": "alarms_save", "problem": type(e).__name__}))

    # ── the hub ──
    def report(self, body: dict) -> dict:
        now = self.clock()
        with self._lock:
            self.view, self.seen_at = body, now
            for d in body.get("done") or []:
                if isinstance(d, dict) and d.get("id"):
                    self.results[str(d["id"])] = {"said": str(d.get("said") or "")[:200], "at": now}
            done = set(self.results)
            self.commands = [c for c in self.commands if c["id"] not in done and now - c["at"] < COMMAND_TTL_S]
            self.results = {k: v for k, v in self.results.items() if now - v["at"] < COMMAND_TTL_S}
            reply = {"commands": [{k: v for k, v in c.items() if k != "at"} for c in self.commands], "ack": sorted(done)}
        self._save()
        self.tick()
        return reply

    # ── the page ──
    def ask(self, body: dict) -> dict:
        op = str(body.get("op") or "")
        if op not in OPS:
            raise ValueError("unknown op")
        cmd = {"id": uuid.uuid4().hex[:12], "op": op, "at": self.clock()}
        if op == "cancel":
            cmd["alarm"] = int(body.get("id"))
        if op == "snooze":
            cmd["minutes"] = max(1, min(60, int(body.get("minutes") or 9)))
        if op == "add":
            t = str(body.get("time") or "")
            if not HHMM.match(t):
                raise ValueError("a time, HH:MM")
            rep = str(body.get("repeat") or "once")
            if rep not in REPEAT and not re.fullmatch(r"(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*", rep):
                raise ValueError("repeat")
            where = str(body.get("where") or "").strip().lower()
            if where not in WHERE:
                raise ValueError("where")
            cmd.update(time=t, repeat=rep, where=where or "upstairs", label=str(body.get("label") or "").strip()[:60], kind="alarm")
        if op == "timer":
            secs = int(body.get("seconds") or 0)
            if not 1 <= secs <= 24 * 3600:
                raise ValueError("seconds")
            cmd.update(seconds=secs, label=str(body.get("label") or "").strip()[:40])
        if op == "cancel_timer":
            cmd.update(label=str(body.get("label") or "")[:40], room=str(body.get("room") or "")[:40])
        with self._lock:
            self.commands.append(cmd)
        return {"id": cmd["id"]}

    def state(self) -> dict:
        now = self.clock()
        v = self.view or {}
        age = now - self.seen_at if self.seen_at else None
        return {"online": age is not None and age < 60, "age_s": None if age is None else round(age), "at": round(now),
                "alarms": v.get("alarms") or [], "timers": v.get("timers") or [], "ringing": v.get("ringing") if age is not None and age < 60 else None,
                "health": v.get("health") or {}, "pending": [c["id"] for c in self.commands], "results": self.results}

    # ── the backup ──
    def maybe_tick(self) -> None:
        if self.clock() - self._ticked >= TICK_S:
            threading.Thread(target=self.tick, daemon=True).start()

    def _tell(self, key: str, text: str) -> None:
        self.alerted[key] = self.clock()
        self.log(json.dumps({"event": "alarm_backup", "key": key}))
        try:
            self.notify(text)
        except Exception:  # noqa: BLE001
            pass

    def tick(self) -> list[str]:
        """The backup's checks (see the module's docstring); the keys told this time."""
        now = self.clock()
        told: list[str] = []
        with self._lock:
            self._ticked = now
            v = self.view
            if not v:
                return told
            silent = now - self.seen_at
            fired = {f.get("key") for f in v.get("fired") or [] if isinstance(f, dict)}
            ago = f"{round(silent / 60)} min ago" if silent >= 90 else "just now"
            for a in v.get("alarms") or []:
                if a.get("kind") != "alarm":
                    continue
                key, due = str(a.get("key") or ""), float(a.get("due") or 0)
                if due + GRACE_S <= now < due + LATE_S and key not in fired and "miss:" + key not in self.alerted:
                    told.append("miss:" + key)
                    self._tell("miss:" + key, f"⏰ WAKE UP: it's past {a.get('hhmm')}, your alarm" + (f" ({a['label']})" if a.get("label") else "")
                               + f", and the house hasn't rung it (skippy-pc last reported {ago}). {self.page_url}")
                if now < due <= now + EVENING_S and silent >= SILENT_EVENING_S and "eve:" + key not in self.alerted:
                    told.append("eve:" + key)
                    self._tell("eve:" + key, f"⏰ Your {a.get('hhmm')} alarm is set, but skippy-pc hasn't reported for {round(silent / 60)} min, "
                               f"so it may not ring. I'll message you at {a.get('hhmm')} if it's still quiet. {self.page_url}")
            r = v.get("ringing")
            if r and silent >= SILENT_RINGING_S and "ring:" + str(r.get("key")) not in self.alerted:
                told.append("ring:" + str(r.get("key")))
                self._tell("ring:" + str(r.get("key")), f"⏰ Your {r.get('hhmm')} alarm was ringing when the house went quiet ({ago}). "
                           f"Are you up? {self.page_url}")
            self.alerted = {k: t for k, t in self.alerted.items() if now - t < 2 * 86400}
        if told:
            self._save(force=True)
        return told


def init_app(app, home, notify, store=None) -> Board:
    board = Board(store=store, notify=notify, page_url=(app.config.get("PORTAL_URL") or "https://makerportal.live").rstrip("/") + "/alarms")
    app.extensions["alarms"] = board
    bp = Blueprint("alarms", __name__)

    @app.before_request
    def _alarm_backup():                     # any request at all (skippy-core reports every 30 s) gives the backup its turn
        board.maybe_tick()

    @bp.get("/alarms")
    @auth.admin_required
    def page():
        return render_template("alarms.html")

    @bp.get("/alarms/state")
    @auth.admin_required
    def state():
        resp = jsonify(board.state())
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @bp.post("/alarms/do")
    @auth.admin_required
    def do():
        if request.headers.get("X-Alarms") != "1":
            abort(400)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        try:
            return jsonify(board.ask(body))
        except (ValueError, TypeError):
            abort(400)

    @app.post("/home/alarms")
    def home_alarms():
        if request.content_length is not None and request.content_length > 200_000:
            abort(413)
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        return jsonify(board.report(body))

    app.register_blueprint(bp)
    return board
