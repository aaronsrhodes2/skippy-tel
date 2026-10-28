"""The Core brain's run engine, on the Claude Agent SDK (the Captain, 2026-10-26: no shims, no prompt injection, no timers,
no text-stripping passes; fix it right).

The CLI engine in workshop_engine.py reads Claude Code's output as lines of text and watches the clock. This one drives Claude
Code through the Agent SDK instead, so the run is typed events and callbacks all the way down:

  * EVENTS   Claude Code's messages arrive as objects (text, tool call, result). normalize() turns them into the few events
             the engine understands (Said, Did, Opened, Retry, Ended); nothing is parsed out of model text.
  * HOOKS    the lane rule and the stop gate are callbacks in this process (lane_guard.Lane, run_state), not scripts that
             read JSON on stdin and exit with a code.
  * SEGMENTS a segment ends when its context, measured from the usage on every model reply, reaches ROTATE_TOKENS (the engine
             interrupts it after the next tool result), or at the turn limit (an `error_max_turns` result). The next segment is
             a fresh session opened with run_state.handoff(): the request, the repository as it is, the agent's own last
             words. The model is never asked to summarize its conversation (the Core coder does that badly: run_state.py).
             A fresh session has to Read what it edits again (Claude Code requires it); that is the price of not summarizing.
  * GATE     before a run may end, the Stop hook looks at the repository: paths this run changed and did not commit send the
             agent back once or twice with the list, as facts.
  * CANCEL   Stop in the chat cancels the run's task; the session is closed (the CLI process with it). No kill.

No timers at all (the Captain, 2026-10-26: a modest local model can take many hours, and a clock that stops it only stops
the work). Claude Code's own tools have their limits, and a model call that fails says so (api_retry events); Stop in the chat is
the only way a run ends early.

BASE   a run starts with what Claude Code puts in front of the model: measured with its own get_context_usage on purple's project,
       36,800 tokens, of which the built-in tools were 24,700 and the Captain's CLAUDE.md for the Skippy ecosystem (not this game's) 4,500.
       Offering the coding tools only (CODING_TOOLS) and leaving that file out (engines.json `claude_md_excludes`) makes it 14,000, so a
       segment has about 86,000 tokens to work in instead of 59,000.

Switch: engines.json in the bridge's state folder, {"core": ["f-shifting-prophesy-purple"]} (or ["*"]), read at the start of
every run, so the engine can be changed back with no restart. The Claude brain and every project not listed keep the CLI engine.
Pictures and pop-ups: the same text conventions as the CLI engine (SKIPPY_PICTURE[_HQ], SKIPPY_POPUP, SKIPPY_IMAGE lines in what the
model says, workshop_prompt.md): `conventions` takes them out of each Said and keeps them on the run; the bridge's end-of-run step
(Workshop._attach_images, run.before_finish) turns them into picture / popup / image events, exactly as for a CLI run.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import run_state
from lane_guard import Lane

SEGMENT_TURNS = int(os.getenv("WORKSHOP_CORE_SEGMENT_TURNS", "120"))    # a segment's hard turn limit (Claude Code --max-turns): a backstop
ROTATE_TOKENS = int(os.getenv("WORKSHOP_CORE_ROTATE_TOKENS", "100000"))  # the context at which a segment ends. The window is 131,072 and the
                                                                         # reply reserve 16,000, so a request may hold 115,000: this leaves 15,000
                                                                         # for the one tool result that can still arrive. Claude Code's own compaction
                                                                         # is off (DISABLE_AUTO_COMPACT): the coder fails its summary half the time
CODING_TOOLS = ("Bash", "Read", "Edit", "Write", "Grep", "Glob", "TodoWrite", "Task", "WebFetch")   # Claude Code's built-ins this engine offers
MAX_SEGMENTS = int(os.getenv("WORKSHOP_CORE_MAX_SEGMENTS", "12"))  # raised from 8: complex debugging (solver, generator) needs more segments before hitting the hard cap
MAX_BUFFER = 16 * 1024 * 1024                                           # one line of Claude Code's output (a big tool result)
LANE_TOOLS = "Edit|Write|MultiEdit|NotebookEdit|Bash|Read|Glob|Grep"      # (the read tools are here for the walled folders: Lane.deny_read)
ENGINES_FILE = "engines.json"


# ── what the engine understands, whatever produced it ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Opened:
    session: str | None


@dataclass(frozen=True)
class Said:
    text: str


@dataclass(frozen=True)
class Did:
    name: str
    args: dict


@dataclass(frozen=True)
class Usage:
    tokens: int                     # the context the model just read (and wrote): what the next request starts from


@dataclass(frozen=True)
class ToolDone:
    pass                            # a tool call finished and its result is on its way to the model


@dataclass(frozen=True)
class Retry:
    attempt: int
    status: int | None
    error: str


@dataclass(frozen=True)
class Trouble:
    kind: str                       # an assistant message that carries an API error: authentication_failed, rate_limit, ...


@dataclass(frozen=True)
class Ended:
    subtype: str                    # success | error_max_turns | error_during_execution | ...
    is_error: bool
    session: str | None
    turns: int
    text: str | None
    errors: list[str] = field(default_factory=list)
    reason: str | None = None       # terminal_reason


def normalize(message: Any) -> list:
    """Claude Agent SDK message -> engine events. Unknown kinds give nothing (the SDK adds kinds over time)."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
    if isinstance(message, UserMessage):
        if getattr(message, "parent_tool_use_id", None) or not isinstance(message.content, list):
            return []
        return [ToolDone()] if any(isinstance(b, ToolResultBlock) for b in message.content) else []
    if isinstance(message, AssistantMessage):
        if getattr(message, "parent_tool_use_id", None):
            return []                                   # a subagent's own steps: the run shows what the main agent did
        out: list = [Trouble(message.error)] if getattr(message, "error", None) else []
        usage = message.usage if isinstance(getattr(message, "usage", None), dict) else {}
        used = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
        if used:
            out.append(Usage(used))
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                out.append(Said(block.text))
            elif isinstance(block, ToolUseBlock):
                out.append(Did(block.name, block.input if isinstance(block.input, dict) else {}))
        return out
    if isinstance(message, SystemMessage):
        data = message.data if isinstance(message.data, dict) else {}
        if message.subtype == "init":
            return [Opened(data.get("session_id"))]
        if message.subtype == "api_retry":
            return [Retry(int(data.get("attempt") or 0), data.get("error_status"), str(data.get("error") or ""))]
        return []
    if isinstance(message, ResultMessage):
        return [Ended(message.subtype, bool(message.is_error), message.session_id, int(message.num_turns or 0), message.result,
                      list(message.errors or []), getattr(message, "terminal_reason", None))]
    return []


# ── the real session: one Claude Code process driven through the SDK ──────────────────────────────────────────────

class SdkSession:
    """open() -> send(prompt) yields engine events until the segment's result -> close(). Hooks are {event: [(matcher, async cb)]}."""

    def __init__(self, kwargs: dict, hooks: dict):
        self.kwargs, self.hooks = kwargs, hooks
        self.client = None
        self.stderr: deque = deque(maxlen=20)

    async def open(self) -> "SdkSession":
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
        matchers = {event: [HookMatcher(matcher=m, hooks=[cb]) for m, cb in items] for event, items in self.hooks.items()}
        options = ClaudeAgentOptions(**self.kwargs, hooks=matchers, stderr=self.stderr.append)
        self.client = ClaudeSDKClient(options)
        await self.client.connect()
        return self

    async def send(self, prompt: str) -> AsyncIterator:
        await self.client.query(prompt)
        ended = False
        try:
            async for message in self.client.receive_response():
                for event in normalize(message):
                    ended = ended or isinstance(event, Ended)
                    yield event
        except Exception:                               # noqa: BLE001 - Claude Code exits non-zero after an error result; that is not news
            if not ended:
                raise

    async def interrupt(self) -> None:
        """End the segment's current turn; the stream then closes with a result (terminal_reason aborted_*)."""
        await self.client.interrupt()

    async def close(self) -> None:
        if self.client is not None:
            try:
                await asyncio.shield(self.client.disconnect())
            except Exception:                           # noqa: BLE001 - a process that will not close is the OS's problem
                pass

    def tail(self) -> str:
        return " | ".join(list(self.stderr)[-5:])[:400]


# ── hooks, as callbacks ──────────────────────────────────────────────────────────────────────────────────────────

def lane_hook(lane: Lane):
    """PreToolUse: refuse a change outside the conversation's folder, with the reason the model is shown."""
    async def hook(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        reason = lane.check(str(input_data.get("tool_name") or ""), input_data.get("tool_input") or {})
        if not reason:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": f"Refused by the Captain's lane rule: {reason} If this really "
                                                                   "needs doing, say so and ask him."}}
    return hook


class StopGate:
    """Stop: a run that changed files and did not commit them is sent back, up to `limit` times, with the list as facts."""

    def __init__(self, clone: str, baseline: set[str], limit: int = run_state.GATE_LIMIT, say: Callable[[str], None] = lambda t: None):
        self.clone, self.baseline, self.limit, self.say = clone, baseline, limit, say
        self.sent_back = 0

    async def hook(self, input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        if self.sent_back >= self.limit:
            return {}
        paths = await asyncio.to_thread(run_state.uncommitted_since, self.clone, self.baseline)
        if not paths:
            return {}
        self.sent_back += 1
        self.say(f"(stop gate: {len(paths)} path(s) changed and not committed; sent back to deal with them)")
        return {"decision": "block", "reason": run_state.gate_reason(paths)}


# ── the driver ──────────────────────────────────────────────────────────────────────────────────────────────────

OpenSession = Callable[[int, "str | None", dict], Any]        # (segment, resume, hooks) -> awaitable of an open session


def context_overflow(ended: "Ended") -> bool:
    """The request was too long for the model (a tool result landed after the last measurement): Claude Code's own error result, with
    compaction off. It is a rotation, not a failure: the next segment starts from the handoff."""
    if not ended.is_error:
        return False
    said = " ".join([*ended.errors, ended.text or "", ended.reason or ""]).lower()
    return "prompt_too_long" in said or "prompt is too long" in said or "context length" in said or "context window" in said


def conventions(run, text: str) -> str:
    """`text` without its SKIPPY_IMAGE / SKIPPY_PICTURE[_HQ] / SKIPPY_POPUP lines; those go on the run (image_paths, picture_prompts,
    popups) for the end-of-run step, the CLI engine's own parsers and limits (workshop_engine.split_*)."""
    import workshop_engine as we                      # (here: workshop_engine imports this module)
    said, shots = we.split_images(text)
    said, wanted = we.split_pictures(said)
    said, jobs = we.split_popups(said)
    for attr, found in (("image_paths", shots), ("picture_prompts", wanted), ("popups", jobs)):
        kept = getattr(run, attr, None)
        if kept is not None:
            kept.extend(found)
    return said


async def drive(run, *, clone: str, request: str, resume: str | None, open_session: OpenSession,
                remember: Callable[[str], None], detail: Callable[[str, dict], str], lane: Lane,
                segments: int = MAX_SEGMENTS, rotate_at: int = ROTATE_TOKENS) -> None:
    """Run `request` to its end in bounded segments; every outcome ends in run.finish().

    A segment ends one of two ways, both told by Claude Code itself: its context (measured from the usage on each model
    reply) reaches `rotate_at`, and the engine interrupts it after the next tool result; or it uses its turn limit."""
    journal = run_state.Journal()
    baseline = set(run_state.dirty_paths(run_state.git_text(clone, "status", "--porcelain", "--untracked-files=all")))
    say = lambda text: run.emit({"t": "text", "text": text})            # noqa: E731 - a note in the chat, in parentheses
    gate = StopGate(clone, baseline, say=say)
    hooks = {"PreToolUse": [(LANE_TOOLS, lane_hook(lane))], "Stop": [(None, gate.hook)]}
    session_id, pending, kept = resume, None, None

    def keep(sid: str | None) -> None:
        """Remember the conversation's session the moment it is known, so a run that is stopped (or dies) mid-segment still
        leaves the NEXT message resuming the segment it was in, not an older one."""
        nonlocal kept
        if sid and sid != kept:
            kept = sid
            remember(sid)

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            run.emit({"t": "text", "text": pending})
            pending = None

    for n in range(1, segments + 1):
        prompt = request if n == 1 else run_state.handoff(request, n, clone, journal)
        session = await open_session(n, resume if n == 1 else None, hooks)
        ended: Ended | None = None
        problem: str | None = None
        context, rotating = 0, False
        try:
            async for ev in session.send(prompt):
                if isinstance(ev, Usage):
                    context = ev.tokens
                elif isinstance(ev, ToolDone):
                    if context >= rotate_at and not rotating:
                        rotating = True
                        await session.interrupt()
                elif isinstance(ev, Opened):
                    session_id = ev.session or session_id
                    keep(ev.session)
                elif isinstance(ev, Said):
                    said = conventions(run, ev.text)
                    journal.said(said)
                    if not said:                      # nothing but picture / pop-up lines: they're kept for the end of the run
                        continue
                    flush()
                    pending = said
                elif isinstance(ev, Did):
                    flush()
                    journal.did(ev.name, detail(ev.name, ev.args))
                    journal.touched(ev.name, ev.args)
                    run.emit({"t": "tool", "name": ev.name, "detail": detail(ev.name, ev.args)})
                elif isinstance(ev, Retry):
                    say(f"(the model call failed{f' ({ev.status})' if ev.status else ''}; retry {ev.attempt})")
                elif isinstance(ev, Trouble):
                    problem = ev.kind
                elif isinstance(ev, Ended):
                    ended = ev
        finally:
            await session.close()
        if ended is None:
            flush()
            stderr = getattr(session, "tail", lambda: "")()
            run.finish(False, (f"Claude Code stopped without a result ({problem})" if problem else "Claude Code stopped without a result")
                       + (f": {stderr}" if stderr else ""))
            return
        session_id = ended.session or session_id
        keep(session_id)
        if rotating or ended.subtype == "error_max_turns" or context_overflow(ended):
            flush()
            say(f"(fresh context: segment {n + 1}; the repository and my last messages carry over)")
            continue
        if ended.is_error:
            failure = "; ".join(ended.errors) or ended.text or ended.subtype
            if pending is not None and pending != failure:
                flush()
            run.finish(False, failure[:300])
            return
        flush()
        run.finish(True)
        return
    flush()
    run.finish(False, f"still going after {segments} segments, so I stopped it. Send the message again to carry on.")


# ── the bridge's side: options, the switch, the thread ──────────────────────────────────────────────────────────

def _engines(state_dir: str) -> dict:
    try:
        data = json.loads((Path(state_dir) / ENGINES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def engine_for(state_dir: str, brain: str, project_id: str) -> str:
    """'sdk' when engines.json lists this project (or '*') for this brain; 'cli' otherwise or when the file is unreadable."""
    listed = _engines(state_dir).get(brain) or []
    return "sdk" if isinstance(listed, list) and ("*" in listed or project_id in listed) else "cli"


def _positive(value, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def segment_turns(state_dir: str) -> int:
    """A segment's hard turn limit: engines.json's `segment_turns` when it is a positive number (tunable with no restart)."""
    return _positive(_engines(state_dir).get("segment_turns"), SEGMENT_TURNS)


def rotate_tokens(state_dir: str) -> int:
    """The context at which a segment ends: engines.json's `rotate_tokens`, else ROTATE_TOKENS."""
    return _positive(_engines(state_dir).get("rotate_tokens"), ROTATE_TOKENS)


def native_cli(claude_cmd: list[str]) -> str:
    """The claude.exe the SDK launches (it refuses npm's .cmd shim): the native binary beside the shim's package."""
    first = claude_cmd[0] if claude_cmd else ""
    if first.lower().endswith(".exe"):
        return first
    base = Path(first).resolve().parent / "node_modules" / "@anthropic-ai" / "claude-code" / "node_modules" / "@anthropic-ai" / "claude-code-win32-x64" / "claude.exe"
    if base.is_file():
        return str(base)
    raise FileNotFoundError(f"no native claude.exe beside {first}")


def engine_options(state_dir: str) -> dict:
    """The SDK engine's settings from engines.json (each optional, read at the start of every run): `tools` (Claude Code built-ins to
    offer), `claude_md_excludes` (CLAUDE.md files, by absolute path, that must not be loaded), `house_tools` (the house MCP tools; off)."""
    data = _engines(state_dir)
    names = data.get("tools")
    excl = data.get("claude_md_excludes")
    deny = data.get("deny_read")
    return {"tools": [t for t in names if isinstance(t, str) and t] if isinstance(names, list) and names else list(CODING_TOOLS),
            "claude_md_excludes": [p for p in excl if isinstance(p, str) and p] if isinstance(excl, list) else [],
            "house_tools": data.get("house_tools") is True, "purple_tools": data.get("purple_tools") is True,
            "purple_tools_only": [n for n in data["purple_tools_only"] if isinstance(n, str) and n] if isinstance(data.get("purple_tools_only"), list) else [],
            "deny_read": [p for p in deny if isinstance(p, str) and p] if isinstance(deny, list) else []}


def sdk_kwargs(*, cwd: str, env: dict, model: str, cli: str, prompt_file: str, primary: str, settings: str,
               mcp: str | None, resume: str | None, max_turns: int = SEGMENT_TURNS, tools: list[str] | None = None) -> dict:
    """ClaudeAgentOptions' arguments for one segment: the same launch the CLI engine makes, as data. Auto-compaction is off: a segment
    ends at ROTATE_TOKENS (drive), which does not depend on the model writing a good summary of itself."""
    kwargs: dict = dict(
        cwd=cwd, env={**env, "DISABLE_AUTO_COMPACT": "1"}, model=model, cli_path=cli, max_turns=max_turns, max_buffer_size=MAX_BUFFER,
        system_prompt={"type": "preset", "preset": "claude_code"},
        extra_args={"append-system-prompt-file": prompt_file, "system-prompt-snapshot": "off", "dangerously-skip-permissions": None},
        add_dirs=[primary], settings=settings)
    if tools:
        kwargs["tools"] = list(tools)
    if mcp:
        kwargs["mcp_servers"] = mcp
    if resume:
        kwargs["resume"] = resume
    return kwargs


def run_core(shop, run, session: str | None) -> None:
    """Run `run` on the SDK engine in this (the run's) thread. Called by Workshop._work."""
    import workshop_engine as we
    try:
        project = shop.project(run.project)
        env = shop.core_env()
        env["SKIPPY_LANE_DEPLOY"] = project.primary if project.deploy == "skippytel" and not project.inplace else ""
        shop.share_memory(env.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"), project.clone, project.primary)
        config = env.get("CLAUDE_CONFIG_DIR") or ""
        resume = session if run_state.can_resume(config, project.clone, session) else None
        removed = {k: "" for k in os.environ if k not in env}           # the SDK inherits os.environ: blank what child_env drops
        cli = native_cli(shop.claude_cmd)
        opts = engine_options(shop.state_dir)
        settings = shop.sdk_settings(opts["claude_md_excludes"])
        mcp = shop.core_mcp_config(opts["house_tools"], opts["purple_tools"], opts["purple_tools_only"])
        prompt_file = str(shop.prompt_path(run.project))
        turns, rotate = segment_turns(shop.state_dir), rotate_tokens(shop.state_dir)
        sessions: list[SdkSession] = []

        async def open_session(n: int, resume_id: str | None, hooks: dict):
            kwargs = sdk_kwargs(cwd=project.clone, env={**removed, **env}, model=we.CORE_MODEL, cli=cli, prompt_file=prompt_file,
                                primary=project.primary, settings=settings, mcp=mcp, resume=resume_id, max_turns=turns, tools=opts["tools"])
            s = SdkSession(kwargs, hooks)
            sessions.append(s)
            return await s.open()

        async def main() -> None:
            await drive(run, clone=project.clone, request=run.message, resume=resume, open_session=open_session,
                        remember=lambda sid: shop._remember(shop.session_key(run.project, run.conv, run.brain), sid),
                        detail=we.tool_detail, lane=Lane(project.clone, env["SKIPPY_LANE_DEPLOY"], deny_read=opts["deny_read"]), rotate_at=rotate)

        loop = asyncio.new_event_loop()
        task = loop.create_task(main())
        run.abort = lambda: loop.call_soon_threadsafe(task.cancel)
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            run.finish(False, "stopped")
        finally:
            run.abort = None
            loop.close()
    except Exception as exc:  # noqa: BLE001 - a crash must end the run, or the chat waits forever
        if not run.done:
            run.finish(False, f"the SDK engine hit a snag: {exc}"[:300])
