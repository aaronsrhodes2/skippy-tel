"""A turn may not end short of the goal (the Captain, 2026-11-12: "we can't wait around staring at a prompt, we need to trust it
to get to the goal with the luxury of time").

purple is slower than a paid model, so nobody watches it work: what it says at the end has to be the goal reached, not a stretch
of the way there. After hanging.py (no promise, no unanswered background command) the stop gate asks two more things:

  * FACT  the run's own task list (the harness's TaskCreate / TaskUpdate records, replayed: nothing the model wrote in prose is
          parsed). A task still pending or in progress sends the turn back to finish it, or to close it with the reason.
          The list exists because child_env switches the task tools on (CLAUDE_CODE_ENABLE_TODO_TOOLS: headless Claude Code has
          none otherwise, and in the CodeBench gates run 0 of 16 runs kept a list).
  * WORDS whether the request was met, judged by Skippy Core's chat model from the request and the closing report: MET, or OPEN
          with the one thing still missing. OPEN sends the turn back with that thing named.

Limits per run (LIMITS) so the gate can never hold a run for ever; no answer from Core is no opinion. On only where the run's
environment says so (SKIPPY_GOAL_GATE=1, set by child_env), so a process started before this existed is not changed under it.
"""
from __future__ import annotations

import os
import re

import hanging

LIMITS = {"tasks": 3, "goal": 3}
REPORT_CHARS = 4000
OPEN_STATES = ("pending", "in_progress")

_ID = re.compile(r"#(\d+)")


def tasks(records: list[dict]) -> dict[str, dict]:
    """The run's task list as the harness recorded it: {id: {"subject", "status"}}, creations and updates replayed in order."""
    out: dict[str, dict] = {}
    waiting: dict[str, str] = {}                         # tool_use id of a TaskCreate -> its subject, until the result names the id
    for r in records:
        content = (r.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use" and c.get("name") == "TaskCreate":
                args = c.get("input") or {}
                waiting[str(c.get("id") or "")] = str(args.get("subject") or args.get("description") or "")[:200]
            elif c.get("type") == "tool_result" and str(c.get("tool_use_id") or "") in waiting:
                subject = waiting.pop(str(c.get("tool_use_id")))
                found = _ID.search(" ".join(hanging._texts(c.get("content"))))
                out[found.group(1) if found else str(len(out) + 1)] = {"subject": subject, "status": "pending"}
            elif c.get("type") == "tool_use" and c.get("name") == "TaskUpdate":
                args = c.get("input") or {}
                task = out.get(str(args.get("taskId") or args.get("id") or ""))
                if task and args.get("status"):
                    task["status"] = str(args["status"])
    return out


def open_tasks(records: list[dict]) -> list[str]:
    return [f"#{i} {t['subject']}" for i, t in tasks(records).items() if t["status"] in OPEN_STATES]


JUDGE = """An AI coding agent, working alone with nobody watching, was given this request:

<request>
{request}
</request>

It is ending its work with this report:

<report>
{words}
</report>

Going only by what the report says was done: is everything the request asked for done? Answer on ONE line:
MET            - the report says every part of the request was done (and checked, where the request needs it to work)
OPEN: <thing>  - a part of the request is, by the report's own account, not done, skipped, left for later, only partly done,
                 or untested; name that one thing in a few words
If the report says a part cannot be done and gives the reason (a real block, or something only the person can decide), that
part counts as closed."""


def judge(request: str, words: str, ask) -> str:
    """'' when the goal is met or there is no opinion; otherwise the thing still open, in Core's words."""
    if not (request.strip() and words.strip()):
        return ""
    try:
        said = ask(JUDGE.format(request=request[:hanging.REQUEST_CHARS], words=words[-REPORT_CHARS:]))
    except Exception:                                    # noqa: BLE001 — Core can't answer: no opinion
        return ""
    found = re.search(r"\bOPEN\s*:\s*(.+)", said)
    return found.group(1).strip()[:300] if found else ""


def core_asker(env=os.environ):
    import eyes
    import workshop_engine as we
    url = env.get("SKIPPY_CORE_URL") or we.vault_value("SKIPPY_CORE_URL")
    key = env.get("SKIPPY_CORE_API_KEY") or we.vault_value("SKIPPY_CORE_API_KEY")
    if not (url and key):
        return None
    return lambda prompt: eyes.ask(url, key, None, "", prompt, max_tokens=60, model=we.CORE_FAST_MODEL, timeout=90,
                                   system="You check whether a report says a request was fully done. Answer on one line.")


def reason(transcript: str, counts: dict, ask=None, words: str = "", request: str = "") -> str:
    """Why this turn may not end yet, or '' to let it. `counts` (kept by the caller across stops) is updated."""
    records = hanging._records(transcript)
    left = open_tasks(records)
    if left and counts.get("tasks", 0) < LIMITS["tasks"]:
        counts["tasks"] = counts.get("tasks", 0) + 1
        return (f"{len(left)} task(s) on your own list are not done: {'; '.join(left[:8])}. Nobody is watching this run and "
                "there is time: finish each one now. If one truly cannot or should not be done, mark it completed or deleted "
                "with TaskUpdate and say why in your report.")
    if ask is None or counts.get("goal", 0) >= LIMITS["goal"]:
        return ""
    if not request:
        import eyes
        request = eyes.task_of(transcript) or ""
    missing = judge(request, words or hanging.last_words(records), ask)
    if not missing:
        return ""
    counts["goal"] = counts.get("goal", 0) + 1
    return (f"The request is not fully met yet, by your own report: {missing}. Nobody is watching this run and there is time: "
            "do that now, check it works, and end only when the whole request is done. If it truly cannot be done, say exactly "
            "what blocks it.")


def check(transcript: str, counts: dict, words: str = "", env=os.environ) -> str:
    """The stop gate's call: off unless the run's environment switches it on."""
    if env.get("SKIPPY_GOAL_GATE") != "1" or not (transcript or words):
        return ""
    try:
        asker = core_asker(env)
    except Exception:                                    # noqa: BLE001 — no Core settings: the task-list fact still runs
        asker = None
    return reason(transcript, counts, ask=asker, words=words)
