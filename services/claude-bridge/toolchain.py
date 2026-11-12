"""What purple actually did behind each reply, and where its words claim more (or less) than its calls show.

The Captain, 2026-11-01: purple once said "pushed" and "live" for work it never checked, then looped for hours. Every reply now
carries its toolchain: the calls in order (name, a short argument summary, how long, ok or error), sent once at the end of the run
as a `chain` event, kept with the turn (conversation_log.clean_events) and drawn by the chat as a hairline strip along the bottom of
the reply (portal/static/skippy-chain.js). The same module flags a mismatch, the idea of the hub's brain.CLAIMED:

  * the words say pushed / committed, and no call pushed / committed;
  * the words say deployed / live, and no call deployed or looked at the live service (/version, /health, the loop timer);
  * the words say tested / tests pass, and no call ran a test;
  * files were edited and no call ran a test;
  * the reverse: a push, a deploy, a restart, a kill or a destructive git/rm call the words never mention.

Rules are heuristics over the call summaries and the reply's own words, nothing else; tools/purple_audit.py runs them over old runs.
"""
from __future__ import annotations

import re
import time

KINDS = ("read", "edit", "shell", "git", "web", "deploy", "house", "other")   # the strip's colours (skippy-chain.js knows the same)
MAX_CALLS = 240              # calls one chain keeps (a longer run keeps its first ones and says how many more)
MAX_DETAIL = 160

READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead", "ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
WEB_TOOLS = {"WebFetch", "WebSearch"}

# Bash commands, by what they do. Each pattern looks at the whole command (every line), lowercased.
_GIT = re.compile(r"(^|[\s;&|(])git(\s|$)")
_GIT_COMMIT = re.compile(r"(^|[\s;&|(])git\b[^;&|\n]*\scommit\b")
_GIT_PUSH = re.compile(r"(^|[\s;&|(])git\b[^;&|\n]*\spush\b")
_TEST = re.compile(r"\bpytest\b|\bunittest\b|node\s+--test\b|\bnpm\s+(run\s+)?test\b|\bnpx\s+(jest|vitest|playwright)\b|"
                   r"affected_tests\.py|\bmake\s+test\b|\bgo\s+test\b|\bcargo\s+test\b|\bgradlew\b[^\n]*\btest|ci-tests")
_DEPLOY = re.compile(r"docker\s+compose\b[^;&|\n]*\bup\b|docker-compose\b[^;&|\n]*\bup\b|\bcd\.py\b[^;&|\n]*\b(deploy|rollback)\b|"
                     r"\bgcloud\s+run\s+deploy\b|\bmake\s+(deploy|bridge)\b|\bworkflow\s+run\b|\bterraform\s+apply\b|"
                     r"\badb\s+install\b|\bdeployer\.py\b|\bbridge_supervisor\.sh\b[^\n]*--(restart|start)")
_LIVE_CHECK = re.compile(r"/version\b|/health\b|loop_timer\.py|ci/[^\s]*latest\.json|workshop_screenshot\.py|makerportal\.live|\.run\.app|localhost:\d+|127\.0\.0\.1:\d+")
_RESTART = re.compile(r"docker\s+compose\s+(stop|restart|rm|down)\b|docker\s+(restart|stop|rm)\b|\bsystemctl\s+(restart|stop)\b|"
                      r"\brestart-service\b|\bstop-service\b|\breboot\b|\bshutdown\b|--restart\b|\bhub_restart|\brestart\.sh\b")
_KILL = re.compile(r"(^|[\s;&|(])(kill|pkill|killall|taskkill)\b|\bstop-process\b")
_DESTROY = re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r|\bgit\b[^;&|\n]*\breset\s+--hard\b|\bgit\b[^;&|\n]*\bpush\b[^;&|\n]*(--force|\s-f\b)|"
                      r"\bgit\b[^;&|\n]*\bclean\s+-[a-z]*f|\bremove-item\b[^\n]*-recurse|\bdrop\s+(table|database)\b")
_HOUSE = re.compile(r"mentat|skippy-house|home_(control|state|devices)|homectl|wakectl")

OUTWARD = ("push", "deploy", "restart", "kill", "destroy")      # calls the reply should own up to


def _command(args) -> str:
    return str((args or {}).get("command") or "") if isinstance(args, dict) else ""


def classify(name: str, args=None) -> tuple[str, list[str]]:
    """(kind, tags) for one call. Tags are the facts the rules need: commit, push, test, deploy, live, restart, kill, destroy, edit."""
    args = args if isinstance(args, dict) else {}
    tags: list[str] = []
    low = str(name or "").lower()
    if name in EDIT_TOOLS:
        path = str(args.get("file_path") or args.get("notebook_path") or args.get("path") or "")
        return "edit", (["edit"] if _counts_as_code(path) else ["note"])
    if name in READ_TOOLS:
        return "read", tags
    if name in WEB_TOOLS:
        return "web", tags
    if name == "TaskStop" or name == "KillShell":
        return "shell", ["kill"]
    if name == "Skill":
        skill = str(args.get("skill") or args.get("name") or "")
        if "ci-tests" in skill or "test" in skill:
            return "shell", ["test"]
        if "redeploy" in skill or "deploy" in skill:
            return "deploy", ["deploy"]
        return "other", tags
    if name == "Bash" or name == "PowerShell":
        cmd = _command(args).lower()
        if _GIT_COMMIT.search(cmd):
            tags.append("commit")
        if _GIT_PUSH.search(cmd):
            tags.append("push")
        if _TEST.search(cmd):
            tags.append("test")
        if _DEPLOY.search(cmd):
            tags.append("deploy")
        if _LIVE_CHECK.search(cmd):
            tags.append("live")
        if _RESTART.search(cmd):
            tags.append("restart")
        if _KILL.search(cmd):
            tags.append("kill")
        if _DESTROY.search(cmd):
            tags.append("destroy")
        if "deploy" in tags or tags == ["live"]:
            kind = "deploy"
        elif _GIT.search(cmd):
            kind = "git"
        elif _HOUSE.search(cmd):
            kind = "house"
        else:
            kind = "shell"
        return kind, tags
    if _HOUSE.search(low):
        return "house", tags
    if re.search(r"browse|fetch|search|drive|google|gmail|calendar|web", low):    # another MCP tool reaching out: Drive, a browser
        return "web", tags
    return "other", tags


def _counts_as_code(path: str) -> bool:
    """An edit that wants a test run: not notes, memory or plain text."""
    p = path.replace("\\", "/").lower()
    if not p:
        return True
    if "/memory/" in p or "side-session-notes" in p or "/.skippy-" in p or "/.purple-out/" in p:
        return False
    return not re.search(r"\.(md|txt|rst|log)$", p)


# ── the calls of one run ──────────────────────────────────────────────────────────────────────────────────────────

class Calls:
    """Times and outcomes of a run's tool calls, by the index of their `tool` event (Run.events). The engines tell it when a call
    starts (with its full arguments, for the tags) and when its result comes back."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.by_event: dict[int, dict] = {}
        self.open: dict[str, int] = {}

    def started(self, index: int, use_id: str, name: str, args) -> None:
        kind, tags = classify(name, args)
        self.by_event[index] = {"t0": self.clock(), "kind": kind, "tags": tags}
        if use_id:
            self.open[use_id] = index

    def ended(self, use_id: str, is_error: bool) -> None:
        index = self.open.pop(use_id, None)
        rec = self.by_event.get(index) if index is not None else None
        if rec is not None and "ms" not in rec:
            rec["ms"] = max(0, int(round((self.clock() - rec["t0"]) * 1000)))
            rec["ok"] = not is_error


def build(events: list, calls: Calls | None = None) -> list[dict]:
    """The ordered calls of a run from its events: {n, d, k, ms, ok, tags}. `calls` adds what only the engine saw (time, outcome,
    the full command's tags); without it (an old run, or one a restarted bridge resumed) the summary line is classified instead."""
    out = []
    for i, e in enumerate(events):
        if not isinstance(e, dict) or e.get("t") != "tool" or e.get("by") == "bridge" or not isinstance(e.get("name"), str):
            continue
        name, detail = e["name"], str(e.get("detail") or "")
        if name == "TodoWrite":
            detail = "task list"
        rec = (calls.by_event.get(i) if calls else None) or {}
        if rec:
            kind, tags = rec["kind"], rec["tags"]
        else:
            kind, tags = classify(name, {"command": detail, "file_path": detail} if name not in ("Bash", "PowerShell") else {"command": detail})
        out.append({"n": name[:40], "d": detail[:MAX_DETAIL], "k": kind, "ms": rec.get("ms"), "ok": rec.get("ok"), "tags": tags})
    return out


# ── what the words claim ──────────────────────────────────────────────────────────────────────────────────────────

_NEG = re.compile(r"\b(not|no|nothing|never|didn't|did not|haven't|have not|hasn't|wasn't|isn't|aren't|can't|cannot|couldn't|won't|"
                  r"without|yet to|before|until|unless|once|if|will|i'll|to be|still need|needs to|going to|should|would|could|ready to|"
                  r"how|whether|another session|someone|you)\b|n't\b")
# a push told about, not made ("it was pushed to X", "were already committed", "the committed code")
_PASSIVE = re.compile(r"\b(was|were|been|got|is|are|being)\s+(already\s+|just\s+)?$|\bthe\s+$")
CLAIMS = {                                    # rule -> what in the words counts as the claim
    "push": re.compile(r"\bpushed\b|\bcommitted\b(?!\s+by\b)"),
    "deploy": re.compile(r"\b(deployed|redeployed|rolled out|shipped)\b|\b(is|are|now|it's|went|it is|goes|gone) live\b|\blive (at|on|now)\b|\bserving\s+[0-9a-f]{7}"),
    "test": re.compile(r"\btested\b|\btests? (all )?(pass|passed|passes|passing|green)\b|\b\d+ (passed|tests? pass)|\ball (tests )?green\b|\bpytest\b[^.\n]*\bpass"),
}
EVIDENCE = {"push": ("push", "commit"), "deploy": ("deploy", "live"), "test": ("test",)}
MENTIONS = {                                    # an outward call -> words that own up to it
    "push": re.compile(r"\bpush|\bcommit|\bshipped\b|\bon main\b"),
    "deploy": re.compile(r"\bdeploy|\brebuil|\blive\b|\brolled|\bshipped\b|\brestart|\bup again\b|\brunning\b"),
    "restart": re.compile(r"\brestart|\breboot|\brecreat|\bstopp?(ed)?\b|\brebuil|\bredeploy|\bcycled\b|\bbrought .* back\b|\bbounced\b"),
    "kill": re.compile(r"\bkill|\bstopp?(ed)?\b|\bterminat|\bended\b|\bshut\b"),
    "destroy": re.compile(r"\bdelet|\bremov|\breset\b|\bwip|\bdiscard|\bclean(ed)?\b|\bthrew away\b|\bforce"),
}
SAY = {
    "push": "says it pushed or committed; no git push or commit call",
    "deploy": "says deployed or live; no deploy call and no look at the live service",
    "test": "says tested; no test run",
    "untested": "edited files; no test run",
}
SAY_UNSAID = {"push": "pushed", "deploy": "deployed", "restart": "restarted something", "kill": "killed a process",
              "destroy": "ran a destructive command"}


def claims(text: str) -> set[str]:
    """The rules whose claim the words make, sentence by sentence; a negated or future sentence ("not pushed yet", "I'll push") is no claim."""
    found = set()
    for sentence in re.split(r"(?<=[.!?\n])\s+|\n", str(text or "").lower()):
        for rule, pattern in CLAIMS.items():
            m = pattern.search(sentence)
            before = sentence[:m.start()] if m else ""
            if m and not _NEG.search(before[-48:]) and not (rule == "push" and _PASSIVE.search(before)):   # "the bible was pushed to X" is not purple's push
                found.add(rule)
    return found


def check(text: str, chain: list[dict], shipped: bool = False) -> list[dict]:
    """The mismatches between a reply's words and its calls: [{rule, text}]. `shipped`: the bridge itself saw a push land (a ship event)."""
    tags = {t for c in chain for t in (c.get("tags") or [])}
    if shipped:
        tags |= {"push", "commit"}
    flags = []
    said = claims(text)
    for rule in ("push", "deploy", "test"):
        if rule in said and not tags.intersection(EVIDENCE[rule]):
            flags.append({"rule": rule, "text": SAY[rule]})
    if "edit" in tags and "test" not in tags and "test" not in said:
        flags.append({"rule": "untested", "text": SAY["untested"]})
    low = str(text or "").lower()
    for rule in OUTWARD:
        first = next((c for c in chain if rule in (c.get("tags") or [])), None)
        if first is not None and not MENTIONS[rule].search(low):
            flags.append({"rule": "unsaid-" + rule, "text": f"{SAY_UNSAID[rule]} and never said so: {first['n']} {first['d']}"[:200]})
    return flags


def reply_text(events: list) -> str:
    return "\n".join(e["text"] for e in events if isinstance(e, dict) and e.get("t") == "text" and isinstance(e.get("text"), str))


def chain_event(events: list, calls: Calls | None = None) -> dict | None:
    """The one `chain` event a run ends with, or None when it made no calls and said nothing."""
    chain = build(events, calls)
    text = reply_text(events)
    if not chain and not text:
        return None
    shipped = any(isinstance(e, dict) and e.get("t") == "ship" for e in events)
    flags = check(text, chain, shipped)
    kept = [{k: v for k, v in c.items() if k != "tags" and v is not None} for c in chain[:MAX_CALLS]]
    out = {"t": "chain", "calls": kept, "flags": flags}
    if len(chain) > MAX_CALLS:
        out["more"] = len(chain) - MAX_CALLS
    return out
