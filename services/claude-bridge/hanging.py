"""A turn may not end on a promise (the Captain, 2026-11-11: "it doesn't come back to things ... this is something I want Purple
Skippy to be much better at ... We can just let things run for hours and hours").

Claude models end a turn with "I'll report once the suite finishes" or "want me to proceed?" and then nothing brings them back:
the session ends, the run rotates to a new segment (run_state.handoff: background commands die with the old one), or the Captain
is away. purple has no clock and no budget to protect, so the stop gate (finish_gate.py, sdk_engine.StopGate) asks two things
before a turn may end, and sends it back with the facts:

  * FACT  background commands this session started that have not finished (the transcript's own records: the harness's
          "running in background with ID: x" and its <task-notification> for x; nothing the model wrote is parsed). The
          turn goes back to wait for them and finish the work they were for.
  * WORDS how the turn's last words end, judged by Skippy Core's chat model (a model reads the words, never a regex):
          DONE (finished, or a report of what was done), PROMISE (says it will do or report something later), or ASK (ends on
          a question or an offer). A promise goes back to be kept now; a question goes back once to be decided (the Captain:
          decide, act, say it), and a second identical stop is let through: a real gate, or something destructive, is his.

Each has a limit per run (LIMITS) so a gate can never hold a run forever, and no answer from Core means no opinion.
"""
from __future__ import annotations

import json
import os
import re

LIMITS = {"background": 4, "promise": 2, "ask": 1}
LAST_WORDS_CHARS = 1500
REQUEST_CHARS = 1500

_LAUNCHED = re.compile(r"background with ID: ([A-Za-z0-9_-]+)")
_ENDED = re.compile(r"<task-id>([A-Za-z0-9_-]+)</task-id>")


def _records(transcript: str) -> list[dict]:
    out = []
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for c in content or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            out.append(str(c.get("text") or ""))
        elif c.get("type") == "tool_result":
            out.extend(_texts(c.get("content")) if not isinstance(c.get("content"), str) else [c["content"]])
    return out


def still_running(records: list[dict]) -> list[str]:
    """Background command ids launched in this session with no completion notice and no stop since: the harness's own words."""
    launched, ended = [], set()
    for r in records:
        if r.get("type") == "queue-operation":
            ended.update(_ENDED.findall(str(r.get("content") or "")))
            continue
        msg = r.get("message") or {}
        if r.get("type") == "user":
            for t in _texts(msg.get("content")):
                ended.update(_ENDED.findall(t))
                launched.extend(i for i in _LAUNCHED.findall(t) if i not in launched)
        elif r.get("type") == "assistant":
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in ("TaskStop", "KillShell"):
                    args = c.get("input") or {}
                    ended.update(str(v) for v in args.values() if isinstance(v, str))
    return [i for i in launched if i not in ended]


def last_words(records: list[dict]) -> str:
    """The text of the turn's final assistant message (what the Captain would read)."""
    for r in reversed(records):
        if r.get("type") != "assistant":
            continue
        said = " ".join(t for t in _texts((r.get("message") or {}).get("content")) if t.strip()).strip()
        if said:
            return said
    return ""


JUDGE = """An AI coding agent was given this request:

<request>
{request}
</request>

It is ending its turn with these last words:

<last_words>
{words}
</last_words>

Nobody will read these words soon or reply. How do the last words end? Answer with ONE word:
DONE    - it reports work as finished, or explains a result, with nothing left that it says it will do later
PROMISE - it says it will do, check, report or continue something later (once something finishes, next, after a wait)
ASK     - it ends with a question to the person, an offer ("want me to...?", "shall I...?", "let me know if..."), or asks for a decision"""


def judge(request: str, words: str, ask) -> str:
    """'done' | 'promise' | 'ask' | '' (no opinion), from Core's chat model. `ask(prompt) -> str`."""
    if not words.strip():
        return ""
    try:
        said = ask(JUDGE.format(request=request[:REQUEST_CHARS], words=words[-LAST_WORDS_CHARS:]))
    except Exception:                                    # noqa: BLE001 — Core can't answer: no opinion, the turn may end
        return ""
    found = re.findall(r"\b(DONE|PROMISE|ASK)\b", said.upper())
    return found[0].lower() if found else ""


def core_asker(env=os.environ):
    """A one-question call to Skippy Core's chat slot, or None when Core isn't configured (no opinion)."""
    import eyes
    import workshop_engine as we
    url = env.get("SKIPPY_CORE_URL") or we.vault_value("SKIPPY_CORE_URL")
    key = env.get("SKIPPY_CORE_API_KEY") or we.vault_value("SKIPPY_CORE_API_KEY")
    if not (url and key):
        return None
    return lambda prompt: eyes.ask(url, key, None, "", prompt, max_tokens=8, model=we.CORE_FAST_MODEL, timeout=60,
                                   system="You classify how a message ends. Answer with one word.")


def reason(transcript: str, counts: dict, ask=None, words: str = "", request: str = "") -> str:
    """Why this turn may not end yet, or '' to let it. `counts` (kept by the caller across stops) is updated."""
    records = _records(transcript)
    running = still_running(records)
    if running and counts.get("background", 0) < LIMITS["background"]:
        counts["background"] = counts.get("background", 0) + 1
        return (f"{len(running)} background command(s) you started are still running ({', '.join(running)}). Nothing will bring you "
                "back after this turn ends: wait for them now (read each one's output file until it says it finished, or run the "
                "command again in the foreground with a long enough timeout), then finish the work they were for and report the result.")
    if ask is None:
        return ""
    if not request:
        import eyes
        request = eyes.task_of(transcript) or ""
    kind = judge(request, words or last_words(records), ask)
    if kind == "promise" and counts.get("promise", 0) < LIMITS["promise"]:
        counts["promise"] = counts.get("promise", 0) + 1
        return ("Your last words promise something for later, but nothing will bring you back once this turn ends: there is no "
                "later. Do it now (wait for whatever you are waiting on, then check, fix or report it), and end only with the result.")
    if kind == "ask" and counts.get("ask", 0) < LIMITS["ask"]:
        counts["ask"] = counts.get("ask", 0) + 1
        return ("You ended on a question or an offer, and the Captain is away: he wants decisions made, not offered (decide, act, "
                "say what you did; he can undo it). Make the call that best fits his request and carry on. Only if it is truly his "
                "(something destructive or irreversible, outside this folder, or a gate he asked for), end again with the same question.")
    return ""


def check(transcript: str, counts: dict, words: str = "", env=os.environ) -> str:
    """The stop gate's call: reason() with Core as the judge, built only when there are last words to judge."""
    if not transcript and not words:
        return ""
    records_words = words or last_words(_records(transcript))
    asker = None
    if records_words.strip() and env.get("SKIPPY_HANGING_GATE", "1") != "0":
        try:
            asker = core_asker(env)
        except Exception:                                # noqa: BLE001 — no Core settings: judge nothing, the fact check still runs
            asker = None
    return reason(transcript, counts, ask=asker, words=records_words)
