"""Skippy proposes, the Captain decides: proposals/ and automations/ in the skippy-home workspace.

A confident, automatable routine (routines.py) becomes a proposal file. The hub asks it aloud at a sensible moment (daytime, at most one ask a day), his
yes / no / never is stored beside it in his own words, and only a yes creates an automation, which the hub then fires through the house tools that already
exist (house.run_tool). A no is left alone for 30 days; a never is never asked again; a yes is never asked twice.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from routines import PROPOSE_AT, to_min

ASK_GAP_S = 20 * 3600                                           # at most one question a day, wherever he is
RETRY_NO_S = 30 * 86400
MAX_ASKS = 3                                                    # asked and not answered three times: let it be
GRACE_MIN = 10                                                  # an automation due while the hub was down is skipped, not fired hours late
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
EVIDENCE = ("n", "observed_days", "confidence", "first_date", "last_date", "spread_min")

_NEVER = re.compile(r"\bnever\b|don'?t (ask|offer|suggest)|stop (asking|suggesting)|quit asking", re.I)
_NO = re.compile(r"^\W*(no|nope|nah|not now|not really|not yet|negative|don'?t|do not)\b", re.I)
_YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok|okay|alright|please|do it|go ahead|go for it|sounds good|absolutely|definitely|why not|fine)\b", re.I)


def parse_answer(words: str) -> str | None:
    """"yes" / "no" / "never" from what he said back, or None when it was something else (then it is an ordinary command)."""
    words = (words or "").strip()
    if _NEVER.search(words) and len(words) < 60:
        return "never"
    if _NO.search(words):
        return "no"
    if _YES.search(words):
        return "yes"
    return None


def say_time(hhmm_: str) -> str:
    h, mm = divmod(to_min(hhmm_), 60)
    return f"{(h % 12) or 12}:{mm:02d} {'AM' if h < 12 else 'PM'}"


def proposal_text(r: dict) -> str:
    return (f"By the way: you turned the {r['target']} {r['act']} around {say_time(r['median_time'])} on {r['n']} of the last {r['observed_days']} {r['weekday']}s. "
            f"Want me to do that for you at {say_time(r['time'])} on {r['weekday']}s? Say yes, no, or never.")


class Workspace:
    def __init__(self, root: Path | str, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.proposals, self.automations = self.root / "proposals", self.root / "automations"

    def _read(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, path: Path, rec: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def all_proposals(self) -> list[dict]:
        return [r for r in (self._read(p) for p in sorted(self.proposals.glob("*.json"))) if r]

    # ── making proposals (the nightly run) ──
    def refresh(self, routines: list[dict]) -> list[str]:
        """Proposals for the confident, automatable routines; returns the ids of the new (or re-opened) ones."""
        made = []
        for r in routines:
            if not r.get("automatable") or r["confidence"] < PROPOSE_AT:
                continue
            path = self.proposals / f"{r['id']}.json"
            old = self._read(path)
            if old and old["status"] in ("yes", "never"):
                continue
            if old and old["status"] == "no" and self.clock() - old.get("answered_ts", 0) < RETRY_NO_S:
                continue
            if old and old["status"] in ("pending", "asked"):
                rec = old                                       # keep its asking history; refresh the words and the evidence
            else:
                rec = {"id": r["id"], "routine_id": r["id"], "status": "pending", "created": time.strftime("%Y-%m-%d", time.localtime(self.clock())), "asked_ts": []}
                made.append(r["id"])
            rec.update(text=proposal_text(r), evidence={k: r[k] for k in EVIDENCE}, weekday=r["weekday"], time=r["time"],
                       action={"tool": "home_control", "args": {"name": r["target"], "action": r["act"]}})
            self._write(path, rec)
        return made

    # ── asking (the hub) ──
    def next_to_ask(self) -> dict | None:
        """The proposal to put to him now, or None: daytime only, one question a day, soonest weekday first, then the surest."""
        now = self.clock()
        lt = time.localtime(now)
        if not 7 <= lt.tm_hour < 22:
            return None
        every = self.all_proposals()
        todo = [p for p in every if p["status"] in ("pending", "asked") and len(p.get("asked_ts", [])) < MAX_ASKS]
        if not todo or any(now - t < ASK_GAP_S for p in every for t in p.get("asked_ts", [])):
            return None
        return min(todo, key=lambda p: ((DAYS.index(p["weekday"]) - lt.tm_wday) % 7, -p["evidence"]["confidence"]))

    def mark_asked(self, pid: str) -> None:
        path = self.proposals / f"{pid}.json"
        p = self._read(path)
        if p:
            p["status"] = "asked"
            p.setdefault("asked_ts", []).append(self.clock())
            self._write(path, p)

    # ── his answer ──
    def answer(self, pid: str, answer: str, words: str) -> dict | None:
        """Store his answer in his words. A yes creates the automation (returned); None if the proposal is gone or the answer isn't one."""
        path = self.proposals / f"{pid}.json"
        p = self._read(path)
        if not p or answer not in ("yes", "no", "never"):
            return None
        p.update(status=answer, answered_ts=self.clock(), answered_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.clock())), answer_words=words[:200])
        auto = None
        if answer == "yes":
            auto = {"id": f"auto-{pid}", "proposal_id": pid, "weekday": p["weekday"], "time": p["time"], "action": p["action"], "enabled": True,
                    "approved_by": "the Captain, aloud", "approved_words": words[:200], "approved_at": p["answered_at"], "last_fired_date": None}
            self._write(self.automations / f"{auto['id']}.json", auto)
            p["automation_id"] = auto["id"]
        self._write(path, p)
        return auto

    # ── firing (the hub's tick) ──
    def due(self) -> list[dict]:
        """Automations due this minute (and within GRACE_MIN after), each once per date. They only exist after a recorded yes."""
        lt = time.localtime(self.clock())
        today, minute = time.strftime("%Y-%m-%d", lt), lt.tm_hour * 60 + lt.tm_min
        out = []
        for path in sorted(self.automations.glob("*.json")):
            a = self._read(path)
            if not a or not a.get("enabled") or not a.get("approved_at") or a.get("weekday") != DAYS[lt.tm_wday] or a.get("last_fired_date") == today:
                continue
            if 0 <= minute - to_min(a["time"]) < GRACE_MIN:
                a["last_fired_date"] = today
                self._write(path, a)
                out.append(a)
        return out
