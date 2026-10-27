"""A request that names no brain is purple (Skippy Core's coder); what was written before brains existed is still a Claude run.
And a request may carry an opaque client_id, which the run state echoes (tracing only: the bridge never dedupes on it)."""
import inspect
import json

import workshop_engine as engine

REAL_START = engine.Workshop.start             # before conftest's fixture swaps in the Claude-asking wrapper


def test_a_new_run_and_a_new_request_default_to_core():
    assert inspect.signature(engine.Run.__init__).parameters["brain"].default == "core"
    assert inspect.signature(REAL_START).parameters["brain"].default == "core"
    assert engine.Run("t1", "conversation-1", "hi").brain == "core"


def test_an_unknown_brain_still_falls_back_to_claude_in_a_run_object():
    assert engine.Run("t1", "conversation-1", "hi", brain="nonsense").brain == "claude"


def test_session_keys_for_claude_are_still_the_bare_ones():
    assert engine.Workshop.session_key("skippytel", "abc") == "abc"                 # no brain given: the old key, so old conversations resume
    assert engine.Workshop.session_key("skippytel", "abc", "core") == "abc@core"


def test_a_run_file_from_before_brains_existed_reads_as_a_claude_run(tmp_path):
    f = tmp_path / ("a" * 16 + ".json")
    f.write_text(json.dumps({"task": "a" * 16, "conv": "conversation-1", "who": "x@y.z", "project": "skippytel", "created": 1.0, "done": True, "ok": True, "events": []}))
    run = engine.Workshop._read(f, 1.0)
    assert run is not None and run.brain == "claude"
    f.write_text(json.dumps({"task": "a" * 16, "conv": "conversation-1", "brain": "core", "created": 1.0, "done": True, "ok": True, "events": []}))
    assert engine.Workshop._read(f, 1.0).brain == "core"


def test_client_id_is_echoed_in_the_run_state_and_survives_a_restart(tmp_path):
    run = engine.Run("b" * 16, "conversation-1", "hi", client_id="page-7f3a")
    assert run.snapshot()["client_id"] == "page-7f3a" and run.summary(0.0)["client_id"] == "page-7f3a"
    f = tmp_path / ("b" * 16 + ".json")
    f.write_text(json.dumps(run.snapshot()))
    assert engine.Workshop._read(f, 1.0).client_id == "page-7f3a"
    assert engine.Run("c" * 16, "conversation-1", "hi").snapshot()["client_id"] == ""
