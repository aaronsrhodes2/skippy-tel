"""The Core brain's run engine, on the Claude Agent SDK (the Captain, 2026-10-26: no shims, no prompt injection, no timers,
no text-stripping passes; fix it right).

The CLI engine in workshop_engine.py reads Claude Code's output as lines of text and watches the clock. This one drives Claude
Code through the Agent SDK instead, so the run is typed events and callbacks all the way down:

  * EVENTS   Claude Code's messages arrive as objects (text, tool call, result). normalize() turns them into the few events
             the engine understands (Said, Did, Opened, Retry, Ended); nothing is parsed out of model text.
  * HOOKS    the lane rule and the stop gate are callbacks in this process (lane_guard.Lane, run_state), not scripts that
             read JSON on stdin and exit with a code.
  * SEGMENTS Claude Code ends a segment itself at its turn limit (an `error_max_turns` result); the next segment is a fresh
             session opened with run_state.handoff(): the request, the repository as it is, the agent's own last words. The
             model is never asked to summarize its conversation (the Core coder does that badly: run_state.py).
  * GATE     before a run may end, the Stop hook looks at the repository: paths this run changed and did not commit send the
             agent back once or twice with the list, as facts.
  * CANCEL   Stop in the chat cancels the run's task; the session is closed (the CLI process with it). No kill.

No quiet timer: Claude Code's own tools have their limits, and a model call that fails says so (api_retry events). The one
limit left is the run's total ceiling (MAX_RUN_S), a cost cap and not a way to find out what the run is doing.

Switch: engines.json in the bridge's state folder, {"core": ["f-shifting-prophesy-purple"]} (or ["*"]), read at the start of
every run, so the engine can be changed back with no restart. The Claude brain and every project not listed keep the CLI engine.
What this engine does not do yet: the SKIPPY_PICTURE / POPUP / IMAGE text conventions (a Core run on this engine gets no
pictures made from its replies) until they are in-process tools.
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

SEGMENT_TURNS = int(os.getenv("WORKSHOP_CORE_SEGMENT_TURNS", "70"))     # a segment's turn budget (Claude Code --max-turns)
MAX_SEGMENTS = int(os.getenv("WORKSHOP_CORE_MAX_SEGMENTS", "8"))
MAX_BUFFER = 16 * 1024 * 1024                                           # one line of Claude Code's output (a big tool result)
LANE_TOOLS = "Edit|Write|MultiEdit|NotebookEdit|Bash"
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
    from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolUseBlock
    if isinstance(message, AssistantMessage):
        if getattr(message, "parent_tool_use_id", None):
            return []                                   # a subagent's own steps: the run shows what the main agent did
        out: list = [Trouble(message.error)] if getattr(message, "error", None) else []
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


async def drive(run, *, clone: str, request: str, resume: str | None, open_session: OpenSession,
                remember: Callable[[str], None], detail: Callable[[str, dict], str], lane: Lane,
                segments: int = MAX_SEGMENTS) -> None:
    """Run `request` to its end in bounded segments; every outcome ends in run.finish()."""
    journal = run_state.Journal()
    baseline = set(run_state.dirty_paths(run_state.git_text(clone, "status", "--porcelain", "--untracked-files=all")))
    say = lambda text: run.emit({"t": "text", "text": text})            # noqa: E731 - a note in the chat, in parentheses
    gate = StopGate(clone, baseline, say=say)
    hooks = {"PreToolUse": [(LANE_TOOLS, lane_hook(lane))], "Stop": [(None, gate.hook)]}
    session_id, pending = resume, None

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
        try:
            async for ev in session.send(prompt):
                if isinstance(ev, Opened):
                    session_id = ev.session or session_id
                elif isinstance(ev, Said):
                    journal.said(ev.text)
                    flush()
                    pending = ev.text
                elif isinstance(ev, Did):
                    flush()
                    journal.did(ev.name, detail(ev.name, ev.args))
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
        if session_id:
            remember(session_id)
        if ended.subtype == "error_max_turns":
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


def segment_turns(state_dir: str) -> int:
    """A segment's turn budget: engines.json's `segment_turns` when it is a positive number (so it can be tuned with no restart)."""
    value = _engines(state_dir).get("segment_turns")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else SEGMENT_TURNS


def native_cli(claude_cmd: list[str]) -> str:
    """The claude.exe the SDK launches (it refuses npm's .cmd shim): the native binary beside the shim's package."""
    first = claude_cmd[0] if claude_cmd else ""
    if first.lower().endswith(".exe"):
        return first
    base = Path(first).resolve().parent / "node_modules" / "@anthropic-ai" / "claude-code" / "node_modules" / "@anthropic-ai" / "claude-code-win32-x64" / "claude.exe"
    if base.is_file():
        return str(base)
    raise FileNotFoundError(f"no native claude.exe beside {first}")


def sdk_kwargs(*, cwd: str, env: dict, model: str, cli: str, prompt_file: str, primary: str, settings: str,
               mcp: str | None, resume: str | None, max_turns: int = SEGMENT_TURNS) -> dict:
    """ClaudeAgentOptions' arguments for one segment: the same launch the CLI engine makes, as data."""
    kwargs: dict = dict(
        cwd=cwd, env=env, model=model, cli_path=cli, max_turns=max_turns, max_buffer_size=MAX_BUFFER,
        system_prompt={"type": "preset", "preset": "claude_code"},
        extra_args={"append-system-prompt-file": prompt_file, "system-prompt-snapshot": "off", "dangerously-skip-permissions": None},
        add_dirs=[primary], settings=settings)
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
        settings, mcp = shop.sdk_settings(), shop.house_mcp_config()
        prompt_file = str(shop.prompt_path(run.project))
        turns = segment_turns(shop.state_dir)
        sessions: list[SdkSession] = []

        async def open_session(n: int, resume_id: str | None, hooks: dict):
            kwargs = sdk_kwargs(cwd=project.clone, env={**removed, **env}, model=we.CORE_MODEL, cli=cli, prompt_file=prompt_file,
                                primary=project.primary, settings=settings, mcp=mcp, resume=resume_id, max_turns=turns)
            s = SdkSession(kwargs, hooks)
            sessions.append(s)
            return await s.open()

        async def main() -> None:
            await drive(run, clone=project.clone, request=run.message, resume=resume, open_session=open_session,
                        remember=lambda sid: shop._remember(shop.session_key(run.project, run.conv, run.brain), sid),
                        detail=we.tool_detail, lane=Lane(project.clone, env["SKIPPY_LANE_DEPLOY"]))

        loop = asyncio.new_event_loop()
        task = loop.create_task(asyncio.wait_for(main(), timeout=shop.max_run_s))
        run.abort = lambda: loop.call_soon_threadsafe(task.cancel)
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            run.finish(False, "stopped")
        except asyncio.TimeoutError:
            run.finish(False, f"Claude Code was still going after {we._span(shop.max_run_s)}, so I stopped it. "
                              "Send the message again, or a smaller one, to carry on.")
        finally:
            run.abort = None
            loop.close()
    except Exception as exc:  # noqa: BLE001 - a crash must end the run, or the chat waits forever
        if not run.done:
            run.finish(False, f"the SDK engine hit a snag: {exc}"[:300])
