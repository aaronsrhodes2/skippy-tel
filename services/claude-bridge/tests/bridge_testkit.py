"""Shared stand-ins for the bridge's tests: a fake `claude`, a Workshop wired to it in a tmp dir (its runs directory
included, so no test ever writes to the real one), and the bridge's HTTP door on an ephemeral port.

The fixtures that use these (`shop`, `door`) live in conftest.py; plain helpers are imported by the test files."""
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge  # noqa: E402
import workshop_engine as engine  # noqa: E402

FAKE = r'''
import json, os, sys

if sys.argv[1:3] == ["auth", "status"]:          # `claude auth status`: JSON, exit 1 when signed out (like the real CLI)
    login = os.environ.get("FAKE_LOGIN", "in")
    if login == "hang":
        import time
        beat = os.environ.get("FAKE_HEARTBEAT")           # proof of life: a dot every 0.1 s, so a test can see it was killed
        for _ in range(600):
            if beat:
                with open(beat, "a") as f:
                    f.write(".")
            time.sleep(0.1)
    elif login == "junk":
        print("this is not json at all")
    elif login == "fail":
        sys.exit(1)
    else:
        if login == "banner":
            print("A new version of Claude Code is available")
        print(json.dumps({"loggedIn": login != "out", "argv": sys.argv[1:], "leak": "SKIPPY_HOME_TOKEN" in os.environ,
                          "email": "captain@example.com"}))
        sys.exit(1 if login == "out" else 0)
    sys.exit(0)

mode = os.environ.get("FAKE_MODE", "ok")
rec = os.environ["FAKE_RECORD"]
prompt_file = None                                   # what --append-system-prompt-file pointed at, read at launch
if "--append-system-prompt-file" in sys.argv:
    try:
        with open(sys.argv[sys.argv.index("--append-system-prompt-file") + 1], encoding="utf-8") as pf:
            prompt_file = pf.read()
    except (OSError, IndexError) as exc:
        prompt_file = "ERROR: " + str(exc)
with open(rec, "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read(), "env": sorted(os.environ), "cwd": os.getcwd(),
                        "prompt_file": prompt_file,
                        "git_env": {k: os.environ.get(k) for k in ("GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE")}}) + "\n")

def out(**e):
    print(json.dumps(e), flush=True)

def beat_forever(path, every=0.1, lines=False):      # proof of life: a dot per beat in `path`, so a test can see it was killed
    import time
    n = 0
    while n < 6000:
        n += 1
        if path:
            with open(path, "a") as hb:
                hb.write(".")
        if lines:
            out(type="assistant", message={"content": [{"type": "text", "text": f"tick {n}"}]})
        time.sleep(every)

resuming = "--resume" in sys.argv
out(type="system", subtype="init", session_id="sess-1", model="claude-opus-5-5")
if mode == "auth":
    msg = "Failed to authenticate: OAuth session expired and could not be refreshed"
    out(type="assistant", message={"content": [{"type": "text", "text": msg}]})
    out(type="result", is_error=True, result=msg, session_id="sess-1")
elif mode == "noresume" and resuming:
    out(type="result", is_error=True, result="No conversation found with session ID: sess-old", session_id="x")
elif mode == "noresume-errors" and resuming:        # claude 2.1.285's real shape for a session it cannot find: no `result` at all
    out(type="result", subtype="error_during_execution", is_error=True, num_turns=0, session_id="sess-old",
        errors=["No conversation found with session ID: sess-old"])
elif mode == "errors-only":                         # an error whose words are only in `errors`
    out(type="result", subtype="error_during_execution", is_error=True, session_id="sess-1",
        errors=["The repository is on fire", "", "also the stapler is missing"])
elif mode == "slow":
    import time
    out(type="assistant", message={"content": [{"type": "text", "text": "starting"}]})
    time.sleep(60)
elif mode == "hang":                                  # one line, then silence for good (a stuck command, a credential prompt)
    out(type="assistant", message={"content": [{"type": "text", "text": "starting"}]})
    beat_forever(os.environ.get("FAKE_HEARTBEAT"))
elif mode == "chatty":                                # never silent, never done: only the wall clock can stop it
    beat_forever(os.environ.get("FAKE_HEARTBEAT"), lines=True)
elif mode == "steady":                                # quiet for stretches, but never for long: must NOT trip the idle limit
    import time
    out(type="assistant", message={"content": [{"type": "text", "text": "starting"}]})
    for n in range(8):
        time.sleep(0.3)
        out(type="system", subtype="progress", n=n)
    out(type="assistant", message={"content": [{"type": "text", "text": "Done."}]})
    out(type="result", is_error=False, result="Done.", session_id="sess-1")
elif mode == "orphan":                                # hangs, and a grandchild of its own holds the output pipe too
    import subprocess
    subprocess.Popen([sys.executable, "-c",
                      "import time\n"
                      "p = " + repr(os.environ.get("FAKE_CHILD_HEARTBEAT")) + "\n"
                      "for _ in range(6000):\n"
                      "    open(p, 'a').write('.')\n"
                      "    time.sleep(0.1)\n"])
    out(type="assistant", message={"content": [{"type": "text", "text": "starting"}]})
    beat_forever(os.environ.get("FAKE_HEARTBEAT"))
else:
    out(type="assistant", message={"content": [{"type": "text", "text": "Working on it."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "curl -H 'Authorization: Bearer abcdef123456' http://x"}},
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "portal/templates/home.html", "new_string": "SECRET BODY"}}]})
    out(type="user", message={"content": [{"type": "tool_result", "content": "TOOL OUTPUT MUST NOT LEAK"}]})
    out(type="assistant", message={"content": [{"type": "text", "text": "Done."}]})
    out(type="result", is_error=False, result="Done.", session_id="sess-1")
'''


def make_shop(tmp_path, monkeypatch, name="clone"):
    """A Workshop on the fake claude, with its clone, sessions file and runs directory all under tmp_path.
    Calling it again with another `name` over the same tmp_path shares the sessions file and the runs directory,
    which is exactly what a bridge restart is."""
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "top-secret-token")
    origin = tmp_path / "origin"
    if not origin.exists():
        origin.mkdir()
        for cmd in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first"]):
            subprocess.run(["git", *cmd], cwd=origin, check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(tmp_path / "clone")], check=True, capture_output=True)
    w = engine.Workshop([sys.executable, str(fake)], repo=str(tmp_path / "clone"), primary=str(tmp_path / "primary"),
                        sessions=str(tmp_path / "sessions.json"), remote=str(origin))
    assert Path(w.state_dir).parent == tmp_path          # the runs directory is in the tmp dir, never the real one
    w.record, w.origin, w.fake = record, origin, fake
    return w


def make_door(shop, monkeypatch):
    """The bridge's real HTTP handler on 127.0.0.1, serving `shop`. Returns (call, server)."""
    monkeypatch.setattr(bridge, "WORKSHOP", shop)
    monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, "door-token"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(method, path, body=None, token="door-token"):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    call.base, call.server = base, server
    return call, server


def finish(run, timeout=30):
    """Wait for the run to end AND for its final save to be over (Run.settled). A test that then reads the run's file
    reads it with no writer left: on Windows a reader that polls while the bridge is replacing the file makes the
    replace fail ("Access is denied"), which on a loaded PC defeated the writer's retries and made these tests flaky."""
    deadline = time.time() + timeout
    while not run.done and time.time() < deadline:
        run.since(0, wait=0.5)
    assert run.done, "the run never finished"
    assert run.settled.wait(max(1.0, deadline - time.time())), "the run finished but never settled"
    return run


def calls(shop):
    return [json.loads(line) for line in shop.record.read_text().splitlines()]


def saved(shop, task, until=lambda data: True, timeout=10):
    """The run's file on disk, parsed, once `until(data)` holds. Normally the first read is the answer: use finish() (it
    waits for the final save) before reading a finished run's file, rather than polling here against the writer."""
    path = Path(shop.state_dir) / f"{task}.json"
    deadline = time.time() + timeout
    while True:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if until(data):
                return data
        except (OSError, ValueError):
            data = None                                  # not there yet, or caught between the tmp write and the replace
        if time.time() > deadline:
            raise AssertionError(f"{path} never reached the wanted state; last read: {data!r}")
        time.sleep(0.02)


def wait_for(condition, timeout=20, what="the condition"):
    deadline = time.time() + timeout
    while not condition():
        assert time.time() < deadline, f"timed out waiting for {what}"
        time.sleep(0.02)
