"""The toolchain behind each reply (toolchain.py): the calls recorded from the run's events, and every mismatch rule both ways."""
import json

import toolchain as tc
import workshop_engine as engine


def bash(cmd, ms=100, ok=True):
    return {"n": "Bash", "d": cmd, "k": tc.classify("Bash", {"command": cmd})[0], "ms": ms, "ok": ok,
            "tags": tc.classify("Bash", {"command": cmd})[1]}


def call(name, args):
    kind, tags = tc.classify(name, args)
    return {"n": name, "d": "", "k": kind, "tags": tags}


def rules(text, chain, shipped=False):
    return [f["rule"] for f in tc.check(text, chain, shipped)]


# ── classify ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_each_call_gets_its_kind():
    assert tc.classify("Read", {"file_path": "a.py"})[0] == "read"
    assert tc.classify("Grep", {"pattern": "x"})[0] == "read"
    assert tc.classify("Edit", {"file_path": "a.py"}) == ("edit", ["edit"])
    assert tc.classify("Write", {"file_path": "notes/plan.md"}) == ("edit", ["note"])          # notes want no test run
    assert tc.classify("WebFetch", {"url": "https://x"})[0] == "web"
    assert tc.classify("Bash", {"command": "ls -la"}) == ("shell", [])
    assert tc.classify("Bash", {"command": "git status"}) == ("git", [])
    assert tc.classify("Bash", {"command": "git add -A && git commit -m x && git push"}) == ("git", ["commit", "push"])
    assert tc.classify("Bash", {"command": "git -C ../x push origin main"})[1] == ["push"]
    assert tc.classify("Bash", {"command": "python -m pytest -q portal/tests"})[1] == ["test"]
    assert tc.classify("Bash", {"command": "python tools/affected_tests.py"})[1] == ["test"]
    assert tc.classify("Bash", {"command": "docker compose up --build -d flask-api"}) == ("deploy", ["deploy"])
    assert tc.classify("Bash", {"command": "curl -s http://localhost:3003/health"}) == ("deploy", ["live"])
    assert tc.classify("Bash", {"command": "curl -s https://makerportal.live/version"}) == ("deploy", ["live"])
    assert "restart" in tc.classify("Bash", {"command": "docker compose stop flask-api"})[1]
    assert tc.classify("Bash", {"command": "taskkill /PID 123 /F"})[1] == ["kill"]
    assert "destroy" in tc.classify("Bash", {"command": "git reset --hard origin/main"})[1]
    assert "destroy" in tc.classify("Bash", {"command": "rm -rf build"})[1]
    assert tc.classify("mcp__mentat__ask", {})[0] == "house"
    assert tc.classify("mcp__skippy-house__home_state", {})[0] == "house"
    assert tc.classify("Skill", {"skill": "ci-tests"}) == ("shell", ["test"])
    assert tc.classify("TaskStop", {})[1] == ["kill"]
    assert tc.classify("TodoWrite", {})[0] == "other"


def test_the_commit_word_inside_another_word_or_a_message_is_not_a_commit():
    assert tc.classify("Bash", {"command": "grep -rn commit portal"})[1] == []
    assert tc.classify("Bash", {"command": "git log --oneline -5"})[1] == []


# ── each rule, both ways ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_claimed_push_needs_a_push_or_commit_call():
    assert rules("Done. Pushed to main.", []) == ["push"]
    assert rules("Done. Pushed to main.", [bash("git push")]) == []
    assert rules("Committed the fix.", [bash("git commit -m fix")]) == []
    assert rules("Pushed it.", [], shipped=True) == []                           # the bridge saw the push land (a ship event)
    assert rules("Not pushed yet: waiting for the tests.", []) == []            # a negated claim is no claim
    assert rules("I'll push once the tests pass.", []) == []                   # nor a future one


def test_a_claimed_deploy_or_live_needs_a_deploy_or_a_look_at_the_live_service():
    assert rules("It's live now.", []) == ["deploy"]
    assert rules("Deployed.", [bash("docker compose up --build -d flask-api")]) == []
    assert rules("The portal is live.", [bash("curl -s https://makerportal.live/version")]) == []
    assert rules("It isn't live yet.", []) == []


def test_a_claimed_test_run_needs_a_test_call():
    assert rules("Tested it and the tests pass.", []) == ["test"]
    assert rules("All 40 passed.", []) == ["test"]
    assert rules("Tests pass.", [bash("python -m pytest -q")]) == []
    assert rules("I haven't tested it.", []) == []


def test_edits_with_no_test_run_are_flagged_and_notes_are_not():
    assert rules("Changed the colour.", [call("Edit", {"file_path": "portal/static/x.css"})]) == ["untested"]
    assert rules("Changed the colour.", [call("Edit", {"file_path": "x.css"}), bash("node --test tests/js")]) == []
    assert rules("Wrote the notes.", [call("Write", {"file_path": "docs/plan.md"})]) == []
    assert rules("Read it.", [call("Read", {"file_path": "x.py"})]) == []


def test_an_outward_call_the_words_never_mention_is_flagged():
    assert rules("Fixed the bug.", [bash("git push origin main")]) == ["unsaid-push"]
    assert rules("Fixed the bug and pushed it.", [bash("git push origin main")]) == []
    assert rules("All good.", [bash("docker compose up --build -d flask-api")]) == ["unsaid-deploy"]
    assert rules("Rebuilt flask-api, live at :3003.", [bash("docker compose up --build -d flask-api")]) == []
    assert rules("Looked at it.", [bash("taskkill /PID 4242 /F")]) == ["unsaid-kill"]
    assert rules("Stopped the stray server.", [bash("taskkill /PID 4242 /F")]) == []
    assert rules("Tidied up.", [bash("git reset --hard origin/main")]) == ["unsaid-destroy"]
    assert rules("Reset the clone to origin.", [bash("git reset --hard origin/main")]) == []
    flag = tc.check("Fixed.", [bash("git push origin main")])[-1]
    assert "git push origin main" in flag["text"]                              # it names the call


# ── recording: events and the engines' timings → the chain event ─────────────────────────────────────────────────

def test_a_run_records_each_call_with_its_time_and_outcome_and_ends_with_one_chain_event():
    clock = iter([10.0, 12.5, 13.0, 13.2]).__next__
    run = engine.Run("aaaaaaaa", "conv-12345678", "fix it")
    run.calls = tc.Calls(clock=clock)
    run.emit({"t": "tool", "name": "git", "detail": "pulled 2 commits", "by": "bridge"})     # the bridge's own sync is not a call
    run.tool_call("Bash", {"command": "python -m pytest -q\necho done"}, "t1", "python -m pytest -q")
    run.calls.ended("t1", False)
    run.tool_call("Edit", {"file_path": "a.py"}, "t2", "a.py")
    run.calls.ended("t2", True)
    run.emit({"t": "text", "text": "Fixed it and pushed."})
    run.finish(True)
    chain = run.events[-1]
    assert chain["t"] == "chain"
    assert chain["calls"] == [{"n": "Bash", "d": "python -m pytest -q", "k": "shell", "ms": 2500, "ok": True},
                              {"n": "Edit", "d": "a.py", "k": "edit", "ms": 200, "ok": False}]
    assert [f["rule"] for f in chain["flags"]] == ["push"]
    assert [e["t"] for e in run.events].count("chain") == 1
    json.dumps(chain)                                                            # it travels as JSON


def test_a_call_with_no_result_keeps_no_time_and_an_old_run_is_classified_from_its_lines():
    events = [{"t": "tool", "name": "Bash", "detail": "git push origin main"}, {"t": "text", "text": "Done."}]
    chain = tc.chain_event(events)
    assert chain["calls"] == [{"n": "Bash", "d": "git push origin main", "k": "git"}]
    assert [f["rule"] for f in chain["flags"]] == ["unsaid-push"]
    assert tc.chain_event([]) is None


def test_the_cli_stream_times_each_call_from_its_tool_result(monkeypatch):
    """The CLI engine's own loop: a tool_use in an assistant line, its tool_result (is_error) in the next user line."""
    run = engine.Run("bbbbbbbb", "conv-12345678", "go")
    run.calls = tc.Calls(clock=iter([1.0, 1.75]).__next__)
    lines = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "false"}}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "u1", "is_error": True}]}}]
    for evt in lines:                                                           # the same handling as Workshop._attempt
        if evt["type"] == "assistant":
            for b in evt["message"]["content"]:
                run.tool_call(b["name"], b["input"], b["id"], engine.tool_detail(b["name"], b["input"]))
        else:
            for b in evt["message"]["content"]:
                run.calls.ended(b["tool_use_id"], b["is_error"])
    run.finish(True)
    assert run.events[-1]["calls"] == [{"n": "Bash", "d": "false", "k": "shell", "ms": 750, "ok": False}]


def test_a_long_run_keeps_its_first_calls_and_says_how_many_more():
    events = [{"t": "tool", "name": "Read", "detail": f"f{i}.py"} for i in range(tc.MAX_CALLS + 5)]
    chain = tc.chain_event(events)
    assert len(chain["calls"]) == tc.MAX_CALLS and chain["more"] == 5


def test_the_cli_engine_end_to_end_times_each_call_and_flags_the_claimed_push(shop, monkeypatch):
    """The fake claude's stream (bridge_testkit "chain"): two calls, the second failing, then "Tests pass, and I pushed it."."""
    from bridge_testkit import finish
    monkeypatch.setenv("FAKE_MODE", "chain")
    run = finish(shop.start("cccccccc", "conv-12345678", "go"))
    chain = run.events[-1]
    assert chain["t"] == "chain"
    assert [(c["n"], c["k"], c.get("ok")) for c in chain["calls"]] == [("Bash", "shell", True), ("Read", "read", False)]
    assert all(isinstance(c.get("ms"), int) for c in chain["calls"])
    assert [f["rule"] for f in chain["flags"]] == ["push"]                     # the tests ran; the push never did
