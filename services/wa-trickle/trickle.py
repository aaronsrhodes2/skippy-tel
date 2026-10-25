"""wa-trickle: a slow, free comparison of Wolfram|Alpha and Mentat on the same questions.

Wolfram|Alpha's free API tier allows about 2,000 calls a month, non-commercial. This service spends a small, fixed
daily budget of them (default 60) on a queue of questions, one call per question, ever: the answer is frozen the moment it
arrives. The same question is also put to Mentat (POST /ask on Mentat, on skippy-core), and put again whenever Mentat's commit changes
(GET /version), so Mentat's progress is measured against a frozen Wolfram answer without spending another call.

    POST /enqueue        {"suite": "math329", "items": [{"id": "...", "question": "...", "expected": {...}, "priority": 5}]}
    POST /import_answers {"suite": "...", "items": [{"id", "question", "wolfram": {"answer_text": "...", ...}}]}   (historic answers, never re-asked)
    GET  /status         counts, budget, next ask, Mentat version
    GET  /export?suite=  JSON lines: question, expected, the frozen Wolfram answer, Mentat's latest answer
    GET  /health         "ok"

State is one SQLite file (/state/trickle.sqlite). Scoring lives on the sigma-ground side (tools/wa_trickle_score.py), so
the extractors can improve without a new Wolfram call. Environment: WOLFRAM_ALPHA_APP_ID, WA_DAILY_BUDGET=60,
WA_MONTHLY_CAP=1900, WA_INTERVAL_S=900, MENTAT_URL=http://skippy-core:7350, WA_STATE=/state, WA_TIMEOUT_S=40.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY, suite TEXT NOT NULL, question TEXT NOT NULL, expected_json TEXT, priority INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wa_answers (
  question_id TEXT PRIMARY KEY REFERENCES questions(id), status TEXT NOT NULL, pods_json TEXT, primary_text TEXT,
  source TEXT NOT NULL, asked_at TEXT NOT NULL, elapsed_s REAL);
CREATE TABLE IF NOT EXISTS mentat_answers (
  question_id TEXT NOT NULL REFERENCES questions(id), sigma_ground_ref TEXT NOT NULL, ok INTEGER NOT NULL, result_json TEXT,
  error TEXT, asked_at TEXT NOT NULL, elapsed_s REAL, PRIMARY KEY (question_id, sigma_ground_ref));
CREATE TABLE IF NOT EXISTS budget (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS q_suite ON questions(suite, priority);
"""


class Unavailable(Exception):
    """Mentat cannot answer right now (its Qwen is down, it is rate-limiting): try later, store nothing."""


class WolframQuota(Exception):
    """Wolfram refused: out of quota or a bad AppID. Pause until the month rolls over."""


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def qid(question: str) -> str:
    return hashlib.sha1(question.strip().encode("utf-8")).hexdigest()[:12]


# ── the two askers (injected in tests) ─────────────────────────────────────────────────────────────

def ask_wolfram(app_id: str, question: str, timeout_s: float = 40.0) -> dict:
    """One call to the Full Results API (output=json). -> {status, pods, primary_text, http}. Raises WolframQuota on 403/401."""
    url = "https://api.wolframalpha.com/v2/query?" + urllib.parse.urlencode(
        {"input": question, "appid": app_id, "output": "json", "format": "plaintext"})
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise WolframQuota(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
        raise
    qr = body.get("queryresult", {})
    pods = []
    for pod in qr.get("pods", []) or []:
        pods.append({"id": pod.get("id", ""), "title": pod.get("title", ""),
                     "plaintext": [sp.get("plaintext", "") for sp in pod.get("subpods", []) or [] if sp.get("plaintext")]})
    primary = ""
    for pod in pods:
        if pod["id"] == "Result" or pod["title"] == "Result" or "Result" in pod["id"]:
            primary = "\n".join(pod["plaintext"])
            break
    if not primary and pods:
        primary = "\n".join(f"[{p['title']}] " + " | ".join(p["plaintext"]) for p in pods if p["plaintext"])
    err = qr.get("error")
    if err and not qr.get("success"):
        status = "error"
    else:
        status = "ok" if qr.get("success") else "no_result"
    extra = {k: qr[k] for k in ("assumptions", "didyoumeans", "tips", "error") if qr.get(k)}
    return {"status": status, "pods": pods, "primary_text": primary, "extra": extra}


def ask_mentat(base_url: str, question: str, timeout_s: float = 120.0) -> dict:
    """POST /ask on Mentat. -> {ok, result|error}. Raises Unavailable on 429/503 (its Qwen is down, or rate-limited)."""
    req = urllib.request.Request(base_url.rstrip("/") + "/ask", data=json.dumps({"question": question}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        if e.code in (429, 502, 503, 504):
            raise Unavailable(f"HTTP {e.code}: {text[:120]}") from e
        try:
            return json.loads(text)
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}: {text[:300]}"}
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise Unavailable(f"{type(e).__name__}: {e}") from e


def mentat_version(base_url: str) -> str:
    with urllib.request.urlopen(base_url.rstrip("/") + "/version", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8")).get("sigma_ground_ref") or "dev"


# ── the queue ─────────────────────────────────────────────────────────────────────────────────────

class Trickle:
    def __init__(self, db_path: str, wolfram_fn=None, mentat_fn=None, version_fn=None, now_fn=None,
                 daily_budget: int = 60, monthly_cap: int = 1900, interval_s: float = 900.0, app_id: str = "",
                 mentat_batch: int = 20, mentat_every_s: float = 60.0):
        self.db_path, self.app_id = db_path, app_id
        self.wolfram_fn, self.mentat_fn, self.version_fn = wolfram_fn, mentat_fn, version_fn
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self.daily_budget, self.monthly_cap, self.interval_s, self.mentat_batch = daily_budget, monthly_cap, interval_s, mentat_batch
        self.mentat_every_s = mentat_every_s
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.last_event = ""

    # -- bookkeeping --
    def _kv(self, key, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default

    def _set_kv(self, key, value):
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
        self.db.commit()

    def day(self):
        return self.now().astimezone(timezone.utc).strftime("%Y-%m-%d")

    def calls_today(self):
        row = self.db.execute("SELECT calls FROM budget WHERE day=?", (self.day(),)).fetchone()
        return row["calls"] if row else 0

    def calls_month(self):
        row = self.db.execute("SELECT COALESCE(SUM(calls),0) AS n FROM budget WHERE day LIKE ?", (self.day()[:7] + "%",)).fetchone()
        return row["n"]

    def _count_call(self):
        self.db.execute("INSERT INTO budget(day,calls) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1", (self.day(),))
        self.db.commit()

    def paused_until(self):
        v = self._kv("paused_until")
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if v else None

    # -- intake --
    def enqueue(self, suite: str, items: list) -> int:
        n = 0
        with self.lock:
            for it in items:
                question = str(it["question"]).strip()
                if not question:
                    continue
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO questions(id,suite,question,expected_json,priority,added_at) VALUES(?,?,?,?,?,?)",
                    (str(it.get("id") or qid(question)), suite, question, json.dumps(it.get("expected"), ensure_ascii=False),
                     int(it.get("priority", 0)), iso(self.now())))
                n += cur.rowcount
            self.db.commit()
        return n

    def import_answers(self, suite: str, items: list) -> int:
        """Historic Wolfram answers: stored as already asked (source 'historic'); they cost no call and are never re-asked."""
        n = 0
        with self.lock:
            self.enqueue(suite, items)
            for it in items:
                w = it.get("wolfram") or {}
                text = w.get("answer_text") or w.get("primary_result_pod") or ""
                if text.startswith("<ERROR"):
                    continue                                          # the call failed: Wolfram never answered, so it is still wanted
                status = "ok" if text.strip() else "no_result"        # an empty answer is Wolfram's answer: it could not parse the question
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO wa_answers(question_id,status,pods_json,primary_text,source,asked_at,elapsed_s) VALUES(?,?,?,?,?,?,?)",
                    (str(it.get("id") or qid(it["question"])), status, json.dumps(w, ensure_ascii=False),
                     w.get("primary_result_pod") or text, "historic", w.get("asked_at") or iso(self.now()), w.get("elapsed_s")))
                n += cur.rowcount
            self.db.commit()
        return n

    # -- the work --
    def _next_unasked_wolfram(self):
        return self.db.execute(
            "SELECT q.* FROM questions q LEFT JOIN wa_answers w ON w.question_id=q.id WHERE w.question_id IS NULL "
            "ORDER BY q.priority DESC, q.added_at, q.id LIMIT 1").fetchone()

    def wolfram_step(self) -> str:
        """At most one Wolfram call. -> what happened (for the log)."""
        with self.lock:
            if not self.app_id or self.wolfram_fn is None:
                return "idle: no WOLFRAM_ALPHA_APP_ID"
            pause = self.paused_until()
            if pause and self.now() < pause:
                return f"paused until {iso(pause)}"
            if self.calls_today() >= self.daily_budget:
                return "idle: today's budget is spent"
            if self.calls_month() >= self.monthly_cap:
                return "idle: this month's cap is reached"
            row = self._next_unasked_wolfram()
            if row is None:
                return "idle: every question has a Wolfram answer"
            t0 = time.time()
            try:
                ans = self.wolfram_fn(self.app_id, row["question"])
            except WolframQuota as e:
                nxt = self.now().astimezone(timezone.utc)
                nxt = nxt.replace(year=nxt.year + (nxt.month == 12), month=1 if nxt.month == 12 else nxt.month + 1, day=1, hour=0, minute=5, second=0, microsecond=0)
                self._set_kv("paused_until", iso(nxt))
                return f"quota or key refused ({e}); paused until {iso(nxt)}"
            except Exception as e:  # noqa: BLE001 — network trouble: no call counted, try again next tick
                return f"error, will retry: {type(e).__name__}: {str(e)[:100]}"
            self._count_call()                                         # a response arrived: it counts, whatever it says
            self.db.execute(
                "INSERT OR IGNORE INTO wa_answers(question_id,status,pods_json,primary_text,source,asked_at,elapsed_s) VALUES(?,?,?,?,?,?,?)",
                (row["id"], ans["status"], json.dumps({"pods": ans["pods"], **ans.get("extra", {})}, ensure_ascii=False),
                 ans["primary_text"], "api", iso(self.now()), time.time() - t0))
            self.db.commit()
            return f"asked Wolfram: {row['id']} ({ans['status']})"

    def mentat_step(self) -> str:
        """Put up to `mentat_batch` unanswered questions (at Mentat's current commit) to Mentat."""
        if self.mentat_fn is None or self.version_fn is None:
            return "idle: no Mentat configured"
        try:
            ref = self.version_fn()
        except Exception as e:  # noqa: BLE001
            return f"Mentat unreachable: {type(e).__name__}"
        with self.lock:
            rows = self.db.execute(
                "SELECT q.* FROM questions q WHERE NOT EXISTS (SELECT 1 FROM mentat_answers m WHERE m.question_id=q.id AND m.sigma_ground_ref=?) "
                "ORDER BY q.priority DESC, q.added_at, q.id LIMIT ?", (ref, self.mentat_batch)).fetchall()
        done = 0
        for row in rows:
            t0 = time.time()
            try:
                res = self.mentat_fn(row["question"])
            except Unavailable as e:
                return f"Mentat busy or its Qwen is down ({str(e)[:60]}); {done} asked, will retry"
            with self.lock:
                ok = bool(res.get("ok"))
                self.db.execute(
                    "INSERT OR IGNORE INTO mentat_answers(question_id,sigma_ground_ref,ok,result_json,error,asked_at,elapsed_s) VALUES(?,?,?,?,?,?,?)",
                    (row["id"], ref, int(ok), json.dumps(res.get("result"), ensure_ascii=False, default=str) if ok else None,
                     None if ok else str(res.get("error"))[:500], iso(self.now()), time.time() - t0))
                self.db.commit()
            done += 1
        return f"asked Mentat {ref[:12]}: {done} question(s)" if rows else f"Mentat {ref[:12]} has answered everything"

    def tick(self) -> str:
        """One cycle: Mentat first (free), then at most one Wolfram call."""
        msg = f"{self.mentat_step()} | {self.wolfram_step()}"
        self.last_event = f"{iso(self.now())} {msg}"
        return msg

    # -- reports --
    def status(self) -> dict:
        with self.lock:
            suites = {r["suite"]: r["n"] for r in self.db.execute("SELECT suite, COUNT(*) AS n FROM questions GROUP BY suite")}
            asked = self.db.execute("SELECT COUNT(*) AS n FROM wa_answers WHERE source='api'").fetchone()["n"]
            historic = self.db.execute("SELECT COUNT(*) AS n FROM wa_answers WHERE source='historic'").fetchone()["n"]
            total = sum(suites.values())
            waiting = total - asked - historic
            try:
                ref = self.version_fn() if self.version_fn else None
            except Exception:  # noqa: BLE001
                ref = None
            m_done = self.db.execute("SELECT COUNT(*) AS n FROM mentat_answers WHERE sigma_ground_ref=?", (ref,)).fetchone()["n"] if ref else None
            days_left = None if not self.daily_budget else round(waiting / self.daily_budget, 1)
            return {"questions": total, "suites": suites, "wolfram": {"asked_by_api": asked, "historic": historic, "waiting": waiting,
                    "calls_today": self.calls_today(), "daily_budget": self.daily_budget, "calls_this_month": self.calls_month(),
                    "monthly_cap": self.monthly_cap, "days_to_finish_at_budget": days_left,
                    "app_id_configured": bool(self.app_id), "paused_until": (iso(self.paused_until()) if self.paused_until() else None)},
                    "mentat": {"sigma_ground_ref": ref, "answered_at_this_ref": m_done}, "last_event": self.last_event}

    def export(self, suite: str | None = None):
        sql = ("SELECT q.id,q.suite,q.question,q.expected_json,q.priority,w.status AS wa_status,w.primary_text AS wa_primary,w.pods_json AS wa_pods,"
               "w.source AS wa_source,w.asked_at AS wa_asked_at FROM questions q LEFT JOIN wa_answers w ON w.question_id=q.id")
        args = ()
        if suite:
            sql += " WHERE q.suite=?"
            args = (suite,)
        with self.lock:
            for r in self.db.execute(sql + " ORDER BY q.priority DESC, q.id", args).fetchall():
                m = self.db.execute("SELECT * FROM mentat_answers WHERE question_id=? ORDER BY asked_at DESC, rowid DESC LIMIT 1", (r["id"],)).fetchone()
                yield {"id": r["id"], "suite": r["suite"], "question": r["question"],
                       "expected": json.loads(r["expected_json"]) if r["expected_json"] else None,
                       "wolfram": None if r["wa_status"] is None else {"status": r["wa_status"], "primary_text": r["wa_primary"],
                                                                       "pods": json.loads(r["wa_pods"]) if r["wa_pods"] else None,
                                                                       "source": r["wa_source"], "asked_at": r["wa_asked_at"]},
                       "mentat": None if m is None else {"sigma_ground_ref": m["sigma_ground_ref"], "ok": bool(m["ok"]),
                                                         "result": json.loads(m["result_json"]) if m["result_json"] else None,
                                                         "error": m["error"], "asked_at": m["asked_at"]}}


# ── HTTP ──────────────────────────────────────────────────────────────────────────────────────────

def create_app(trickle: Trickle, start_worker: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["trickle"] = trickle

    @app.get("/health")
    def health():
        return "ok"

    @app.get("/status")
    def status():
        return jsonify(trickle.status())

    @app.post("/enqueue")
    def enqueue():
        body = request.get_json(silent=True) or {}
        items = body.get("items")
        if not isinstance(items, list) or not body.get("suite"):
            return jsonify(ok=False, error="send {suite, items:[{question, id?, expected?, priority?}]}"), 400
        return jsonify(ok=True, added=trickle.enqueue(str(body["suite"]), items))

    @app.post("/import_answers")
    def import_answers():
        body = request.get_json(silent=True) or {}
        items = body.get("items")
        if not isinstance(items, list) or not body.get("suite"):
            return jsonify(ok=False, error="send {suite, items:[{question, id?, wolfram:{answer_text,...}}]}"), 400
        return jsonify(ok=True, imported=trickle.import_answers(str(body["suite"]), items))

    @app.get("/export")
    def export():
        lines = (json.dumps(row, ensure_ascii=False, default=str) for row in trickle.export(request.args.get("suite")))
        return Response("\n".join(lines) + "\n", mimetype="application/x-ndjson")

    if start_worker:
        # Two independent loops: Mentat can be slow or down (its Qwen shares the GPU); that must never delay the Wolfram budget.
        def loop(step, every_s, label):
            while True:
                try:
                    msg = step()
                    trickle.last_event = f"{iso(trickle.now())} [{label}] {msg}"
                except Exception as e:  # noqa: BLE001 — a loop must never die
                    trickle.last_event = f"{iso(trickle.now())} [{label}] failed: {type(e).__name__}: {e}"
                time.sleep(every_s)
        threading.Thread(target=loop, args=(trickle.wolfram_step, trickle.interval_s, "wolfram"), daemon=True, name="wolfram").start()
        threading.Thread(target=loop, args=(trickle.mentat_step, trickle.mentat_every_s, "mentat"), daemon=True, name="mentat").start()
    return app


def from_env() -> Trickle:
    state = os.environ.get("WA_STATE", "/state")
    os.makedirs(state, exist_ok=True)
    mentat = os.environ.get("MENTAT_URL", "http://skippy-core:7350")
    timeout = float(os.environ.get("WA_TIMEOUT_S", "40"))
    return Trickle(os.path.join(state, "trickle.sqlite"),
                   wolfram_fn=lambda app_id, q: ask_wolfram(app_id, q, timeout),
                   mentat_fn=lambda q: ask_mentat(mentat, q),
                   version_fn=lambda: mentat_version(mentat),
                   daily_budget=int(os.environ.get("WA_DAILY_BUDGET", "60")),
                   monthly_cap=int(os.environ.get("WA_MONTHLY_CAP", "1900")),
                   interval_s=float(os.environ.get("WA_INTERVAL_S", "900")),
                   app_id=os.environ.get("WOLFRAM_ALPHA_APP_ID", ""))


def build_app() -> Flask:
    """gunicorn entry point: `gunicorn "trickle:build_app()"` (one worker: the queue thread lives in the process)."""
    return create_app(from_env(), start_worker=True)
