"""The compaction guard takes the tools away from a summary request, and touches nothing else."""
import json

from core_testlib import load

guard = load("ollama-shim/compaction_guard.py", "core_compaction_guard")

TOOLS = [{"name": "Bash", "input_schema": {"type": "object"}}, {"name": "Read", "input_schema": {"type": "object"}}]
SUMMARY_ASK = ("CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n\nYour task is to create a detailed summary of the "
               "conversation so far...\n\nREMINDER: Do NOT call any tools. Respond with plain text only")
HISTORY = [
    {"role": "user", "content": "Build the generator."},
    {"role": "assistant", "content": [{"type": "text", "text": "On it."}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
]


def body(last, tools=TOOLS, **extra):
    return json.dumps({"model": "coder", "max_tokens": 16000, "stream": True, "messages": HISTORY + [last],
                       **({"tools": tools} if tools is not None else {}), **extra}).encode()


def test_a_summary_request_loses_its_tools_and_tool_choice_and_nothing_else():
    raw = body({"role": "user", "content": [{"type": "text", "text": SUMMARY_ASK}]}, tool_choice={"type": "auto"}, system="s")
    out, rec = guard.guard_request(raw)
    sent = json.loads(out)
    assert "tools" not in sent and "tool_choice" not in sent
    assert sent["system"] == "s" and sent["model"] == "coder" and sent["stream"] is True
    assert sent["messages"] == json.loads(raw)["messages"]               # the history is whole, tool calls and all
    assert rec["summarization"] is True and rec["stripped"] == 2 and rec["tools"] == 2 and rec["tool_uses_in_history"] == 1


def test_each_marker_alone_is_enough_and_a_string_message_works():
    for marker in guard.MARKERS:
        out, rec = guard.guard_request(body({"role": "user", "content": f"blah {marker} blah"}))
        assert rec["summarization"] and "tools" not in json.loads(out)


def test_any_other_request_is_forwarded_byte_for_byte():
    for last in ({"role": "user", "content": "Run the tests."},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": SUMMARY_ASK}]},   # a file that quotes it
                 {"role": "assistant", "content": SUMMARY_ASK}):
        raw = body(last)
        out, rec = guard.guard_request(raw)
        assert out == raw and rec["summarization"] is False and rec["tools"] == 2


def test_only_the_last_message_counts():
    earlier = [{"role": "user", "content": SUMMARY_ASK}, {"role": "assistant", "content": "A summary."}]
    raw = json.dumps({"model": "coder", "messages": earlier + [{"role": "user", "content": "Go on."}], "tools": TOOLS}).encode()
    out, rec = guard.guard_request(raw)
    assert out == raw and rec["summarization"] is False


def test_a_summary_request_with_no_tools_is_forwarded_as_it_came_and_logged_so():
    raw = body({"role": "user", "content": SUMMARY_ASK}, tools=None)
    out, rec = guard.guard_request(raw)
    assert out == raw and rec["summarization"] is True and rec["stripped"] == 0


def test_a_body_that_is_not_a_request_passes_and_never_raises():
    for raw in (b"", b"not json", b"[]", b"null", b'{"messages": 7}', b'{"messages": [3]}', None):
        out, rec = guard.guard_request(raw)
        assert out == raw and not rec.get("summarization")


def test_the_log_holds_counts_and_never_the_prompt(tmp_path):
    log = guard.GuardLog(tmp_path / "g" / "guard.jsonl")
    out, rec = guard.guard_request(body({"role": "user", "content": SUMMARY_ASK}))
    log.append(rec)
    text = (tmp_path / "g" / "guard.jsonl").read_text(encoding="utf-8")
    assert "Build the generator" not in text and "CRITICAL" not in text and "Let me" not in text
    assert json.loads(text)["summarization"] is True


def test_tail_reads_since_a_time_and_across_a_rotation(tmp_path):
    log = guard.GuardLog(tmp_path / "guard.jsonl", max_bytes=120)
    for ts in (1000, 2000, 3000, 4000):
        log.append({"ts": ts, "summarization": ts % 2000 == 0, "stripped": 5 if ts % 2000 == 0 else 0, "pad": "x" * 60})
    assert (tmp_path / "guard.jsonl.1").exists()
    assert [r["ts"] for r in log.tail(2000)] == [2000, 3000, 4000]
    assert guard.summarize(log.tail(2000)) == {"requests": 3, "summary_requests": 2, "stripped": 10, "summary_requests_without_tools": 0}


def test_a_log_that_cannot_be_written_does_not_fail_the_request(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    guard.GuardLog(blocker / "sub" / "guard.jsonl").append({"ts": 1})        # a directory can't be made under a file
