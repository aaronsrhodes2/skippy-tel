"""sdk_engine: the Core brain's run engine on the Agent SDK: typed events, hooks as callbacks, bounded segments, a stop gate.

The driver is tested against scripted sessions (no Claude Code, no Core); the adapter against the SDK's own message classes."""
import asyncio
import json
import subprocess
from pathlib import Path

import pytest

import run_state
import sdk_engine as sdk
import workshop_engine as engine
from bridge_testkit import finish
from lane_guard import Lane
from sdk_engine import Did, Ended, Opened, Retry, Said, ToolDone, Trouble, Usage


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "work"
    r.mkdir()
    git(r, "init", "-q")
    (r / "a.txt").write_text("a")
    git(r, "add", "a.txt")
    git(r, "commit", "-q", "-m", "first")
    return r


class FakeSession:
    """One segment: yields its scripted events. `hold` makes it wait forever (a run that is cancelled mid-stream)."""

    def __init__(self, events, hold=False):
        self.events, self.hold, self.prompt, self.closed, self.interrupted = events, hold, None, False, False

    async def send(self, prompt):
        self.prompt = prompt
        for ev in self.events:
            yield ev
            if self.interrupted:                        # what Claude Code does: the turn ends with an aborted result
                yield Ended("error_during_execution", True, "s-int", 5, None, ["interrupted"], "aborted_tools")
                return
        if self.hold:
            await asyncio.Event().wait()

    async def interrupt(self):
        self.interrupted = True

    async def close(self):
        self.closed = True

    def tail(self):
        return "boom on stderr"


def new_run():
    return engine.Run("aaaaaaaa", "conv-12345678", "Turn 1: build it.\nCommit when the tests pass.", brain="core")


def go(run, repo, scripts, **kw):
    """Drive `run` over scripted segments; returns (sessions opened, resume values, ids remembered)."""
    opened, resumes, kept = [], [], []

    async def open_session(n, resume, hooks):
        resumes.append(resume)
        s = FakeSession(*scripts[n - 1]) if isinstance(scripts[n - 1], tuple) else FakeSession(scripts[n - 1])
        opened.append(s)
        return s

    async def main():
        await sdk.drive(run, clone=str(repo), request=run.message, resume=kw.pop("resume", None), open_session=open_session,
                        remember=kept.append, detail=lambda name, args: str(args.get("command") or args.get("file_path") or ""),
                        lane=Lane(str(repo)), **kw)
    asyncio.run(main())
    return opened, resumes, kept


def ok(session="s1", turns=3):
    return Ended("success", False, session, turns, "done")


def maxed(session):
    return Ended("error_max_turns", True, session, 70, None, ["Reached maximum number of turns (70)"], "max_turns")


def texts(run):
    return [e["text"] for e in run.events if e["t"] == "text"]


def test_one_segment_to_the_end(repo):
    run = new_run()
    opened, resumes, kept = go(run, repo, [[Opened("s1"), Said("Looking."), Did("Bash", {"command": "ls"}), Said("All done."), ok()]])
    assert run.done and run.ok
    assert [(e["t"], e.get("text") or e.get("detail")) for e in run.events] == [("text", "Looking."), ("tool", "ls"), ("text", "All done.")]
    assert opened[0].prompt == run.message and resumes == [None] and kept == ["s1"] and opened[0].closed      # (once: it is not said twice)


def test_a_session_to_resume_goes_to_the_first_segment_only(repo):
    run = new_run()
    _, resumes, _ = go(run, repo, [[maxed("s1")], [ok("s2")]], resume="old-session")
    assert resumes == ["old-session", None]


def test_a_segment_that_uses_its_turns_is_followed_by_a_fresh_one_built_from_facts(repo):
    run = new_run()
    (repo / "gen.mjs").write_text("wip")
    opened, resumes, kept = go(run, repo, [
        [Opened("s1"), Said("The bug is the lock direction."), Did("Edit", {"file_path": "src/solver.mjs"}), maxed("s1")],
        [Opened("s2"), Said("Fixed."), ok("s2")]])
    assert run.ok and kept == ["s1", "s2"] and resumes == [None, None]
    second = opened[1].prompt
    assert second.startswith(run.message.strip() + "\n")                         # the request, word for word, first
    assert "segment 2 of the job above" in second and "?? gen.mjs" in second and "first" in second
    assert "The bug is the lock direction." in second and "Edit: src/solver.mjs" in second
    assert any("segment 2" in t for t in texts(run)) and all(s.closed for s in opened)


def test_a_segment_ends_when_its_context_is_measured_full_and_the_next_one_starts_from_facts(repo):
    run = new_run()
    # 82k tokens after the second reply: the interrupt comes after the next tool result, not before
    opened, resumes, kept = go(run, repo, [
        [Opened("s1"), Usage(30000), Said("Reading."), Did("Read", {"file_path": "a.mjs"}), ToolDone(),
         Usage(82000), Said("Found it."), Did("Edit", {"file_path": "a.mjs"}), ToolDone(), Said("never seen"), ok("never")],
        [Opened("s2"), Usage(31000), Said("Carrying on."), ok("s2")]], rotate_at=80000)
    assert run.ok and len(opened) == 2 and opened[0].interrupted and all(s.closed for s in opened)
    assert kept == ["s1", "s-int", "s2"]                               # each session as it opens; the interrupted one's own id at its end
    assert "Found it." in opened[1].prompt and "Edit: a.mjs" in opened[1].prompt and "never seen" not in opened[1].prompt
    assert any("segment 2" in t for t in texts(run))


def test_a_context_under_the_limit_is_never_interrupted(repo):
    run = new_run()
    opened, _, _ = go(run, repo, [[Usage(79999), Did("Bash", {"command": "ls"}), ToolDone(), Usage(60000), ToolDone(), ok()]], rotate_at=80000)
    assert run.ok and len(opened) == 1 and not opened[0].interrupted


def test_the_interrupt_is_asked_once_per_segment(repo):
    run = new_run()
    calls = []

    class Counting(FakeSession):
        async def interrupt(self):
            calls.append(1)
            await super().interrupt()
    events = [Usage(90000), ToolDone(), ToolDone(), ToolDone(), ok()]

    async def open_session(n, resume, hooks):
        return Counting(events) if n == 1 else FakeSession([ok("s2")])

    async def main():
        await sdk.drive(run, clone=str(repo), request=run.message, resume=None, open_session=open_session, remember=lambda sid: None,
                        detail=lambda n, a: "", lane=Lane(str(repo)), rotate_at=80000)
    asyncio.run(main())
    assert len(calls) == 1 and run.ok


def test_a_run_that_never_finishes_stops_at_the_segment_cap_and_says_so(repo):
    run = new_run()
    opened, _, _ = go(run, repo, [[maxed("s1")], [maxed("s2")], [maxed("s3")]], segments=3)
    assert run.done and run.ok is False and "3 segments" in run.error and len(opened) == 3


def test_an_error_result_fails_the_run_with_its_words_and_does_not_repeat_them(repo):
    run = new_run()
    go(run, repo, [[Said("boom"), Ended("error_during_execution", True, "s1", 2, None, ["boom"])]])
    assert run.ok is False and run.error == "boom" and texts(run) == []        # the text that was the error is not also an answer
    run2 = new_run()
    go(run2, repo, [[Said("I tried."), Ended("error_during_execution", True, "s1", 2, "it broke", [])]])
    assert run2.error == "it broke" and texts(run2) == ["I tried."]


def test_no_result_is_a_failure_with_the_stderr_tail_and_the_api_trouble(repo):
    run = new_run()
    go(run, repo, [[Opened("s1"), Trouble("rate_limit")]])
    assert run.ok is False and "rate_limit" in run.error and "boom on stderr" in run.error


def test_api_retries_show_in_the_chat_as_a_note(repo):
    run = new_run()
    go(run, repo, [[Retry(2, 502, "server_error"), Said("ok"), ok()]])
    assert texts(run)[0] == "(the model call failed (502); retry 2)"


def test_the_stop_gate_sends_a_run_back_for_its_own_loose_ends_twice_then_lets_it_go(repo):
    (repo / "old-leftover.txt").write_text("earlier task")
    baseline = set(run_state.dirty_paths(run_state.git_text(str(repo), "status", "--porcelain", "--untracked-files=all")))
    said = []
    gate = sdk.StopGate(str(repo), baseline, say=said.append)
    assert asyncio.run(gate.hook({"hook_event_name": "Stop"}, None, {})) == {}                  # nothing of this run's
    (repo / "gen.mjs").write_text("mine")
    first = asyncio.run(gate.hook({"hook_event_name": "Stop", "stop_hook_active": False}, None, {}))
    assert first == {"decision": "block", "reason": "These paths changed during this run and are not committed: gen.mjs."}
    assert asyncio.run(gate.hook({"stop_hook_active": True}, None, {}))["decision"] == "block"
    assert asyncio.run(gate.hook({"stop_hook_active": True}, None, {})) == {}                  # the limit: the agent has been told
    assert len(said) == 2 and "gen.mjs" not in said[0]


def test_the_stop_gate_is_quiet_once_the_work_is_committed(repo):
    gate = sdk.StopGate(str(repo), set())
    (repo / "gen.mjs").write_text("mine")
    git(repo, "add", "gen.mjs")
    git(repo, "commit", "-q", "-m", "mine")
    assert asyncio.run(gate.hook({}, None, {})) == {}


def test_the_lane_hook_denies_with_the_reason_and_has_no_opinion_otherwise(repo, tmp_path):
    hook = sdk.lane_hook(Lane(str(repo)))
    outside = asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": "D:/not-the-lane/x.txt"}}, "t1", {}))
    out = outside["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
    assert "outside this conversation's folder" in out["permissionDecisionReason"] and "ask him" in out["permissionDecisionReason"]
    assert asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": str(repo / "x.txt")}}, "t2", {})) == {}
    assert asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "C:/anywhere"}}, "t3", {})) == {}


def test_cancelling_the_run_closes_the_session(repo):
    run = new_run()
    box = {"kept": []}

    async def main():
        async def open_session(n, resume, hooks):
            box["s"] = FakeSession([Opened("s1"), Said("working")], hold=True)
            return box["s"]
        task = asyncio.ensure_future(sdk.drive(run, clone=str(repo), request=run.message, resume=None, open_session=open_session,
                                               remember=box["kept"].append, detail=lambda n, a: "", lane=Lane(str(repo))))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(main())
    assert box["s"].closed and not run.done          # the caller (run_core) says "stopped"
    assert box["kept"] == ["s1"]                      # ... and the next message resumes THIS segment, not an older one


def test_the_switch_is_a_file_that_names_projects_and_defaults_to_the_cli(tmp_path):
    assert sdk.engine_for(str(tmp_path), "core", "p") == "cli"
    (tmp_path / "engines.json").write_text(json.dumps({"core": ["p", "q"]}))
    assert sdk.engine_for(str(tmp_path), "core", "p") == "sdk"
    assert sdk.engine_for(str(tmp_path), "core", "other") == "cli"
    assert sdk.engine_for(str(tmp_path), "claude", "p") == "cli"                    # the Claude brain is never on this engine
    (tmp_path / "engines.json").write_text(json.dumps({"core": ["*"]}))
    assert sdk.engine_for(str(tmp_path), "core", "anything") == "sdk" and sdk.engine_for(str(tmp_path), "claude", "anything") == "cli"
    (tmp_path / "engines.json").write_text("not json")
    assert sdk.engine_for(str(tmp_path), "core", "p") == "cli"
    (tmp_path / "engines.json").write_text("[]")
    assert sdk.engine_for(str(tmp_path), "core", "p") == "cli"
    assert sdk.segment_turns(str(tmp_path)) == sdk.SEGMENT_TURNS and sdk.rotate_tokens(str(tmp_path)) == sdk.ROTATE_TOKENS
    assert sdk.engine_options(str(tmp_path)) == {"tools": list(sdk.CODING_TOOLS), "claude_md_excludes": [], "house_tools": False, "purple_tools": False, "purple_tools_only": [], "deny_read": []}


def test_a_segments_turn_budget_can_be_tuned_in_the_same_file(tmp_path):
    (tmp_path / "engines.json").write_text(json.dumps({"core": ["*"], "segment_turns": 3, "rotate_tokens": 40000}))
    assert sdk.segment_turns(str(tmp_path)) == 3 and sdk.rotate_tokens(str(tmp_path)) == 40000
    for bad in (0, -5, "9", True, 2.5, None):
        (tmp_path / "engines.json").write_text(json.dumps({"segment_turns": bad, "rotate_tokens": bad}))
        assert sdk.segment_turns(str(tmp_path)) == sdk.SEGMENT_TURNS and sdk.rotate_tokens(str(tmp_path)) == sdk.ROTATE_TOKENS


def test_the_launch_is_the_cli_engines_launch_as_data():
    k = sdk.sdk_kwargs(cwd="D:/w", env={"A": "1"}, model="coder", cli="C:/claude.exe", prompt_file="D:/state/prompt.txt",
                       primary="D:/w", settings="D:/state/sdk-settings.json", mcp="D:/state/house.json", resume="abc")
    assert k["max_turns"] == sdk.SEGMENT_TURNS == 120 and k["model"] == "coder" and k["cli_path"] == "C:/claude.exe" and k["resume"] == "abc"
    assert k["system_prompt"] == {"type": "preset", "preset": "claude_code"}
    assert k["extra_args"] == {"append-system-prompt-file": "D:/state/prompt.txt", "system-prompt-snapshot": "off",
                               "dangerously-skip-permissions": None}
    assert k["add_dirs"] == ["D:/w"] and k["settings"] == "D:/state/sdk-settings.json" and k["mcp_servers"] == "D:/state/house.json"
    bare = sdk.sdk_kwargs(cwd="D:/w", env={}, model="coder", cli="c", prompt_file="p", primary="D:/w", settings="s", mcp=None, resume=None)
    assert "resume" not in bare and "mcp_servers" not in bare and "tools" not in bare
    withtools = sdk.sdk_kwargs(cwd="D:/w", env={"A": "1"}, model="coder", cli="c", prompt_file="p", primary="D:/w", settings="s", mcp=None, resume=None,
                               tools=["Read", "Edit"])
    assert withtools["tools"] == ["Read", "Edit"]
    assert withtools["env"] == {"A": "1", "DISABLE_AUTO_COMPACT": "1"}                # a segment ends at a measured context, not a summary


def test_the_native_claude_is_found_beside_npms_shim(tmp_path):
    exe = tmp_path / "node_modules/@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-win32-x64/claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    assert Path(sdk.native_cli([str(tmp_path / "claude.CMD")])) == exe.resolve()
    assert sdk.native_cli(["C:/x/claude.exe", "--flag"]) == "C:/x/claude.exe"
    with pytest.raises(FileNotFoundError):
        sdk.native_cli([str(tmp_path / "nothing" / "claude.cmd")])


def test_the_sdk_engine_has_its_own_settings_with_only_the_script_hooks(shop):
    data = json.loads(Path(shop.sdk_settings()).read_text(encoding="utf-8"))
    pre = data["hooks"]["PreToolUse"]
    assert [m["matcher"] for m in pre] == ["Read"] and "eyes.py" in pre[0]["hooks"][0]["command"]
    assert "lane_guard.py" not in json.dumps(data) and "restate_request.py" in json.dumps(data)
    assert "lane_guard.py" in Path(shop.lane_settings()).read_text(encoding="utf-8")                 # the CLI engine's file is as it was


# ── the adapter, on the SDK's own classes ───────────────────────────────────────────────────────────────────────────

def test_normalize_turns_sdk_messages_into_engine_events():
    sdk_types = pytest.importorskip("claude_agent_sdk")
    A, R, S, T, U = (sdk_types.AssistantMessage, sdk_types.ResultMessage, sdk_types.SystemMessage, sdk_types.TextBlock,
                     sdk_types.ToolUseBlock)
    U_ = sdk_types.UserMessage
    thinking = sdk_types.ThinkingBlock("hmm", "sig")
    msg = A(content=[thinking, T("Hello"), T("   "), U("t1", "Bash", {"command": "ls"})], model="coder")
    assert sdk.normalize(msg) == [Said("Hello"), Did("Bash", {"command": "ls"})]
    assert sdk.normalize(A(content=[T("x")], model="coder", error="rate_limit")) == [Trouble("rate_limit"), Said("x")]
    assert sdk.normalize(A(content=[T("sub")], model="coder", parent_tool_use_id="t9")) == []
    used = A(content=[T("x")], model="coder", usage={"input_tokens": 100, "cache_read_input_tokens": 60000, "cache_creation_input_tokens": 5, "output_tokens": 50})
    assert sdk.normalize(used) == [Usage(60155), Said("x")]
    assert sdk.normalize(U_(content=[sdk_types.ToolResultBlock("t1", "ok")])) == [ToolDone()]
    assert sdk.normalize(U_(content="plain words")) == [] and sdk.normalize(U_(content=[sdk_types.ToolResultBlock("t1", "ok")], parent_tool_use_id="t9")) == []
    assert sdk.normalize(S(subtype="init", data={"session_id": "s1"})) == [Opened("s1")]
    assert sdk.normalize(S(subtype="api_retry", data={"attempt": 2, "error_status": 502, "error": "server_error"})) == [Retry(2, 502, "server_error")]
    assert sdk.normalize(S(subtype="something_new", data={})) == []
    res = R(subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=70, session_id="s1",
            errors=["Reached maximum number of turns (70)"], terminal_reason="max_turns")
    assert sdk.normalize(res) == [Ended("error_max_turns", True, "s1", 70, None, ["Reached maximum number of turns (70)"], "max_turns")]


# ── through the Workshop: the switch, the thread, the cancel ───────────────────────────────────────────────────────────

def switch(shop, listing):
    """engines.json in the shop's state folder (made on the first run in real life; the test makes it)."""
    Path(shop.state_dir).mkdir(parents=True, exist_ok=True)
    (Path(shop.state_dir) / "engines.json").write_text(json.dumps({"core": listing}))


@pytest.fixture
def core_shop(shop, tmp_path, monkeypatch):
    monkeypatch.setenv("SKIPPY_CORE_URL", "https://core.example")
    monkeypatch.setenv("SKIPPY_CORE_API_KEY", "sk-core-test")
    monkeypatch.setattr(engine.Workshop, "core_ready", lambda self: True)
    monkeypatch.setattr(sdk, "native_cli", lambda cmd: "C:/fake/claude.exe")
    made = []

    class Fake(FakeSession):
        script = [[Opened("sdk-1"), Said("Built it."), ok("sdk-1")]]

        def __init__(self, kwargs, hooks):
            super().__init__(Fake.script[len(made)])
            self.kwargs, self.hooks = kwargs, hooks
            made.append(self)

        async def open(self):
            return self
    monkeypatch.setattr(sdk, "SdkSession", Fake)
    shop.made, shop.Fake = made, Fake
    return shop


def test_a_core_run_on_a_listed_project_goes_through_the_sdk_engine(core_shop, tmp_path):
    shop = core_shop
    switch(shop, ["skippytel"])
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi", brain="core"))
    assert run.ok and [e["text"] for e in run.events if e["t"] == "text"] == ["Built it."]
    (s,) = shop.made
    assert s.kwargs["cwd"] == shop.project("skippytel").clone and s.kwargs["model"] == engine.CORE_MODEL
    assert s.kwargs["cli_path"] == "C:/fake/claude.exe" and s.kwargs["max_turns"] == sdk.SEGMENT_TURNS
    assert s.kwargs["env"]["ANTHROPIC_BASE_URL"] == "https://core.example" and s.kwargs["env"]["SKIPPY_HOME_TOKEN"] == ""
    assert set(s.hooks) == {"PreToolUse", "Stop"} and s.prompt == "hi"
    assert s.kwargs["tools"] == list(sdk.CODING_TOOLS) and s.kwargs["env"]["DISABLE_AUTO_COMPACT"] == "1" and "mcp_servers" not in s.kwargs
    assert shop._sessions()[shop.session_key("skippytel", "conv-12345678", "core")] == "sdk-1"
    assert not shop.record.exists()                                  # the CLI engine (the fake claude) was never started


def test_a_run_is_never_stopped_for_taking_long(core_shop):
    shop = core_shop
    switch(shop, ["skippytel"])
    shop.max_run_s = 0.2                                              # the CLI engine's 2-hour limit, made tiny: the SDK engine ignores it
    shop.idle_s = 0.2

    class Slow(shop.Fake):
        async def send(self, prompt):
            await asyncio.sleep(0.8)
            async for ev in super().send(prompt):
                yield ev
    import sdk_engine
    sdk_engine.SdkSession = Slow
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "a very long job", brain="core"))
    assert run.ok and run.error is None


def test_an_unlisted_project_and_the_claude_brain_keep_the_cli_engine(core_shop):
    shop = core_shop
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi", brain="core"))                       # no engines.json
    assert shop.made == [] and shop.record.exists()
    switch(shop, ["*"])
    shop.record.unlink()
    finish(shop.start("bbbbbbbb", "conv-12345678", "hi"))                                      # the Claude brain
    assert shop.made == [] and shop.record.exists()


def test_stop_in_the_chat_cancels_an_sdk_run_and_closes_its_session(core_shop):
    shop = core_shop
    switch(shop, ["*"])
    shop.Fake.script = [[Opened("sdk-1"), Said("working")]]
    original = shop.Fake.__init__

    def hold(self, kwargs, hooks):
        original(self, kwargs, hooks)
        self.hold = True
    shop.Fake.__init__ = hold
    run = shop.start("aaaaaaaa", "conv-12345678", "long job", brain="core")
    import time
    deadline = time.time() + 20
    while not (shop.made and run.abort) and time.time() < deadline:
        time.sleep(0.02)
    assert shop.cancel("aaaaaaaa") is True
    finish(run)
    assert run.ok is False and run.error == "stopped" and shop.made[0].closed


def test_engine_options_come_from_engines_json_and_are_checked(tmp_path):
    (tmp_path / "engines.json").write_text(json.dumps({"core": ["*"], "tools": ["Read", "Bash", 7, ""], "claude_md_excludes": ["D:/x/CLAUDE.md", None],
                                                       "house_tools": True, "deny_read": ["D:/x/walled", 5, ""]}))
    assert sdk.engine_options(str(tmp_path)) == {"tools": ["Read", "Bash"], "claude_md_excludes": ["D:/x/CLAUDE.md"], "house_tools": True, "purple_tools": False, "purple_tools_only": [], "deny_read": ["D:/x/walled"]}
    for bad in ({"tools": []}, {"tools": "Read"}, {"tools": None, "house_tools": "yes", "claude_md_excludes": "x"}):
        (tmp_path / "engines.json").write_text(json.dumps(bad))
        assert sdk.engine_options(str(tmp_path)) == {"tools": list(sdk.CODING_TOOLS), "claude_md_excludes": [], "house_tools": False, "purple_tools": False, "purple_tools_only": [], "deny_read": []}


def test_the_sdk_settings_name_the_claude_md_files_to_leave_out(shop):
    plain = json.loads(Path(shop.sdk_settings()).read_text(encoding="utf-8"))
    assert "claudeMdExcludes" not in plain
    left = json.loads(Path(shop.sdk_settings(["D:/Aaron/development/CLAUDE.md"])).read_text(encoding="utf-8"))
    assert left["claudeMdExcludes"] == ["D:/Aaron/development/CLAUDE.md"] and left["hooks"] == plain["hooks"]


def test_a_request_too_long_for_the_model_is_a_rotation_not_a_failure(repo):
    run = new_run()
    long = Ended("error_during_execution", True, "s1", 5, None, ["Prompt is too long"], None)
    opened, _, kept = go(run, repo, [[Opened("s1"), Said("Reading."), long], [Opened("s2"), ok("s2")]])
    assert run.ok and len(opened) == 2 and kept == ["s1", "s2"]
    assert any("segment 2" in t for t in texts(run)) and "segment 2 of the job above" in opened[1].prompt


def test_other_errors_still_fail_and_only_a_too_long_error_counts_as_overflow():
    assert sdk.context_overflow(Ended("error", True, "s", 1, None, ["Prompt is too long"], None))
    assert sdk.context_overflow(Ended("error", True, "s", 1, None, [], "prompt_too_long"))
    assert sdk.context_overflow(Ended("error", True, "s", 1, "Context window exceeded", [], None))
    assert not sdk.context_overflow(Ended("error", True, "s", 1, None, ["rate limited"], None))
    assert not sdk.context_overflow(Ended("success", False, "s", 1, "the prompt is too long, he said", [], None))     # a success is never an overflow


def test_the_second_segments_handoff_lists_what_the_first_edited_first(repo):
    (repo / "b.py").write_text("def later():\n    pass\n")
    (repo / "a.txt").write_text("one\ntwo\n")                                  # newer than b.py, but b.py is the one it edited
    run = new_run()
    opened, _, _ = go(run, repo, [[Opened("s1"), Did("Edit", {"file_path": str(repo / "b.py")}), maxed("s1")], [ok("s2")]])
    listing = opened[1].prompt.split("where their parts are")[1]
    assert listing.index("b.py (2 lines): later:1") < listing.index("a.txt (2 lines)")


def test_picture_and_popup_lines_are_kept_for_the_end_of_the_run_and_never_shown(repo):
    # what the CLI engine does with the same lines (workshop_prompt.md): the end-of-run step makes the jobs (Workshop._attach_images)
    run = new_run()
    go(run, repo, [[Opened("s1"), Said("Sprites coming.\nSKIPPY_PICTURE: a pixel-art knight, 32x32, transparent background"),
                    Said("SKIPPY_POPUP: remove_background {\"_file\": \"art/knight.png\"}"),
                    Said("SKIPPY_PICTURE_HQ: the title screen, a ruined tower at dusk\nSKIPPY_IMAGE: .skippy-shots/front.png"), ok()]])
    assert texts(run) == ["Sprites coming."]
    assert run.picture_prompts == [("a pixel-art knight, 32x32, transparent background", ""), ("the title screen, a ruined tower at dusk", "hq")]
    assert run.popups == [("remove_background", {"_file": "art/knight.png"})] and run.image_paths == [".skippy-shots/front.png"]
