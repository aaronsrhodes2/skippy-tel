"""The recorder's scrubbing must cost a bounded, small amount of time whatever a signed-in person sends.

The defect this pins: portal/telemetry.py's redact() ran its label pattern on the WHOLE string (and only then cut to the
cap), and that pattern was polynomial on chains such as 'password_password_...' (cubic) and 'a.a.a....' (quadratic). Python's
`re` holds the GIL for the whole match and the portal is ONE gunicorn worker, so 8000 characters of chat froze the portal for
everyone for minutes. A friend could send it in a chat message, an unknown URL (the error event records the path) or the
tap beacon's `p` field.

Two guards, and the tests hold both:
  1. text is cut to (its cap + a little slack) BEFORE any pattern sees it, and redact() has a hard ceiling of its own;
  2. no pattern in the module is polynomial on the inputs that hurt: EVERY compiled pattern the module defines is run against
     every adversarial family below (so a regex added later is covered without anyone remembering to add a test).

The timed part runs in a child process: a regression is a failed assertion after a few seconds, not a hung test run.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import telemetry as tel_mod  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402

PORTAL = Path(__file__).resolve().parents[1]
MAX_SCAN = getattr(tel_mod, "MAX_SCAN", 10_000)        # (getattr: so these tests can run, and fail, against the code before the fix)
SCAN_SLACK = getattr(tel_mod, "SCAN_SLACK", 256)
BUDGET_S = 1.0            # a call may take this long, at the real sizes, on a slow CI box. Fixed code needs ~0.1 s at the worst.
CHILD_WALL_S = 60         # the whole probe; a regression that hangs is killed here and reported

PAL = "pal@example.com"

PROBE = r'''
import json, re, sys, time
sys.path.insert(0, sys.argv[1])
import telemetry as t

N = int(sys.argv[2])            # what one pattern is asked to scan
HUGE = int(sys.argv[3])         # what the module's own functions are handed, uncapped (0: skip)

def families(n):
    return {
        "password_ chain": "password_" * (n // 9), "password- chain": "password-" * (n // 9), "password. chain": "password." * (n // 9),
        "a. chain": "a." * (n // 2), "a- chain": "a-" * (n // 2), "a_ chain": "a_" * (n // 2), "pw. chain": "pw." * (n // 3),
        "token_ chain": "token_" * (n // 6), "api_key chain": "api_key_" * (n // 8), "long label tail": "password" + "_a" * (n // 2),
        "label + spaces": "password" + " " * n, "label + is": "password is " * (n // 12), "label + for": "password for x " * (n // 15),
        "label: quote": 'password: "' + "x" * n, "label:quote rep": "password:'" * (n // 9), "label:dquote rep": 'password:"' * (n // 10),
        "M run": "M" * n, "N run": "N" * n, "MN run": "MN" * (n // 2), "eyJ run": "eyJ" * (n // 3), "eyJ.eyJ": "eyJ" + "a" * 8 + "." + "eyJ" * (n // 3),
        "sk-": "sk-" * (n // 3), "sk-ant-": "sk-ant-" * (n // 7), "bearer spaces": "bearer" + " " * n, "bearer rep": "bearer " * (n // 7),
        "ghp_": "ghp_" * (n // 4), "xoxb-": "xoxb-" * (n // 5), "AKIA": "AKIA" * (n // 4), "AIza": "AIza" * (n // 4), "ya29.": "ya29." * (n // 5),
        "dash run": "-" * n, "a39 dashes": "a" * 39 + "-" * n, "a- long": "a-" * (n // 2) + "-", "spaces": " " * n, "words": "word " * (n // 5),
        "newlines": "password:\n" * (n // 10), "unicode": chr(233) * n, "surrogates": "\ud800" * n, "dots": "." * n, "underscores": "_" * n,
    }

def patterns():
    found = []
    for name, value in vars(t).items():
        if isinstance(value, re.Pattern):
            found.append((name, value))
        elif isinstance(value, (list, tuple)) and value and all(isinstance(v, re.Pattern) for v in value):
            found.extend((f"{name}[{i}]", v) for i, v in enumerate(value))
    return found

def timed(what, fn):
    print(json.dumps({"start": what}), flush=True)
    started = time.perf_counter()
    fn()
    print(json.dumps({"what": what, "s": round(time.perf_counter() - started, 4)}), flush=True)

pats = patterns()
print(json.dumps({"patterns": [n for n, _ in pats]}), flush=True)
if N:
    for fam, text in families(N).items():
        for name, pattern in pats:
            timed(f"{name} x {fam}", lambda: pattern.sub("[x]", text))

# the module's own entry points, on text nothing has capped: it is the module's job to bound its own work
if HUGE:
    rec = t.Telemetry()
    for fam, text in families(HUGE).items():
        timed(f"redact x {fam} x {HUGE}", lambda: t.redact(text))
        timed(f"clean x {fam} x {HUGE}", lambda: t.clean({"message": text, "reply": text, "path": text, "other": [text, {"k": text}]}))
        timed(f"record x {fam} x {HUGE}", lambda: rec.record("chat", "send", user="a@x.com", role="friend", sid="ab", message=text,
                                                              reply=text, page=text))
'''


def run_probe(size: int, huge: int):
    child = subprocess.Popen([sys.executable, "-c", PROBE, str(PORTAL), str(size), str(huge)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        out, err = child.communicate(timeout=CHILD_WALL_S)
    except subprocess.TimeoutExpired:
        child.kill()
        out, err = child.communicate()
        lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
        done = {line["what"] for line in lines if "what" in line}
        running = [line["start"] for line in lines if "start" in line and line["start"] not in done]
        pytest.fail(f"still running after {CHILD_WALL_S}s (catastrophic backtracking): {running[-1:]}")
    assert child.returncode == 0, err
    lines = [json.loads(line) for line in out.splitlines()]
    return ([n for line in lines for n in line.get("patterns", [])],
            {line["what"]: line["s"] for line in lines if "what" in line})


def test_no_pattern_in_the_recorder_backtracks_badly_on_adversarial_text_at_the_real_size_limits():
    names, timings = run_probe(MAX_SCAN, 0)
    assert len(names) >= 14, names            # the 11 key/token patterns, the label pattern, the long token, the sensitive key
    assert len(timings) >= 30 * len(names)    # every pattern really was run against every family
    slow = {what: s for what, s in timings.items() if s > BUDGET_S}
    assert not slow, slow


def test_redact_and_clean_and_record_bound_their_own_work_on_input_nobody_capped():
    _, timings = run_probe(0, 1_000_000)
    assert len(timings) >= 3 * 30, "the million-character cases did not run"
    slow = {what: s for what, s in timings.items() if s > BUDGET_S}
    assert not slow, slow


def test_a_string_is_cut_to_its_cap_plus_slack_before_any_pattern_sees_it(monkeypatch):
    seen = []
    monkeypatch.setattr(tel_mod, "redact", lambda text: seen.append(len(text)) or text)
    got = tel_mod.clean({"message": "m" * 1_000_000, "reply": "r" * 1_000_000, "path": "p" * 1_000_000, "list": ["l" * 1_000_000]})
    assert len(seen) == 4 and max(seen) <= 8000 + SCAN_SLACK
    assert (len(got["message"]), len(got["reply"]), len(got["path"]), len(got["list"][0])) == (4000, 8000, 300, 300)
    seen.clear()
    tel_mod.Telemetry().record("page", "n" * 1_000_000, user="a@x.com")
    assert max(seen) <= 80 + SCAN_SLACK


def test_redact_itself_has_a_ceiling_so_a_caller_that_forgot_to_cap_cannot_freeze_the_portal():
    code = ("import sys, time, json; sys.path.insert(0, sys.argv[1]); import telemetry as t; s = 'password_' * 500_000; "
            "a = time.perf_counter(); out = t.redact(s); print(json.dumps({'s': time.perf_counter() - a, 'n': len(out)}))")
    try:
        done = subprocess.run([sys.executable, "-c", code, str(PORTAL)], capture_output=True, text=True, timeout=CHILD_WALL_S)
    except subprocess.TimeoutExpired:
        pytest.fail("redact() of 4.5 million characters is still running after %ds" % CHILD_WALL_S)
    got = json.loads(done.stdout)
    assert got["s"] < BUDGET_S and got["n"] <= MAX_SCAN + len("[redacted]")


def test_a_secret_that_straddles_the_cut_is_still_scrubbed_not_left_half_showing():
    token = "ghp_" + "Ab3" * 20
    for pad in range(3960, 4000, 7):                    # the token starts before the 4000-character cap and ends after it
        text = "x " * (pad // 2) + token + " and the rest"
        got = tel_mod._short(text, 4000)
        assert "ghp_Ab3" not in got and "Ab3Ab3" not in got, pad
    assert tel_mod._short("say hi " * 1000, 4000) == ("say hi " * 1000)[:4000]      # ordinary text is only cut


@pytest.mark.parametrize("said, gone", [
    ("ADMIN_PASSWORD_HASH=deadbeef1234", "deadbeef1234"),
    ("app.db.password: s3cretvalue", "s3cretvalue"),
    ("x-api-key = 'a b c d'", "a b c d"),
    ("my_api_key_v2=zzzz9999", "zzzz9999"),
    ("token.user.name: bob", "bob"),
    ("junk_junk_junk password_and_more_words: pw123 tail", "pw123"),
    ("a" * 50 + "_password=hunter2", "hunter2"),
    ("password" + "_x" * 40 + " ok password=hunter2", "hunter2"),           # a name of very many parts, then a plain label that follows
])
def test_the_label_pattern_still_scrubs_names_made_of_parts(said, gone):
    scrubbed = tel_mod.redact(said)
    assert gone not in scrubbed and "[redacted]" in scrubbed


def test_the_label_pattern_keeps_the_name_and_only_replaces_the_value():
    assert tel_mod.redact("set VALHEIM_PASSWORD=hunter22 now") == "set VALHEIM_PASSWORD=[redacted] now"
    assert tel_mod.redact('{"a_token": "abc def", "n": 1}') == '{"a_token": [redacted], "n": 1}'
    assert tel_mod.redact("the password is hunter22.") == "the password is [redacted]"


# ── end to end: the three ways a signed-in friend could reach it ────────────────────────────────────

def make():
    vm = FakeVm()
    vm.metadata["portal-friends"] = json.dumps({PAL: {"portals": ["valheim", "skippy"], "added_by": ADMIN}})
    settings = {**SETTINGS, "ALLOWED_EMAILS": frozenset({ADMIN}), "TELEMETRY_ENABLED": True}
    app = portal.create_app(settings, vm=vm, skippy=FakeSkippy())
    client = app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"email": PAL, "name": "Pal", "picture": None}
    return app, client, app.extensions["telemetry"]


def test_an_unknown_path_of_the_longest_length_gunicorn_allows_does_not_freeze_the_portal():
    app, client, tel = make()
    path = "/" + "password_" * 440                      # 3961 characters: under gunicorn's 4094 request-line limit
    started = time.perf_counter()
    assert client.get(path).status_code == 404
    assert time.perf_counter() - started < 2.0
    (err,) = [e for e in tel.since(tel.boot, 0)["events"] if e["kind"] == "error"]
    assert len(err["detail"]["path"]) <= 300 and err["name"] == "404"        # recorded (a run that long reads as a token: scrubbed), and cut


def test_a_long_chat_message_does_not_freeze_the_portal():
    app, client, tel = make()
    message = "password_" * 444                          # 3996 characters
    started = time.perf_counter()
    assert client.post("/chat/send", json={"message": message}).status_code == 200
    assert time.perf_counter() - started < 2.0
    (ev,) = [e for e in tel.since(tel.boot, 0)["events"] if e["kind"] == "chat"]
    assert len(ev["detail"]["message"]) <= 4000


def test_the_beacons_page_field_of_the_largest_size_it_accepts_does_not_freeze_the_portal():
    app, client, tel = make()
    page = "/" + "password_" * 200                      # the beacon's whole body is capped at 2 KB
    started = time.perf_counter()
    for _ in range(5):
        assert client.post("/telemetry/tap", json={"e": "tile:games", "p": page}).status_code == 204
    assert time.perf_counter() - started < 2.0
