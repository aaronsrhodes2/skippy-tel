"""Skippy's ears and mouth for the portal's chats (/chat and the /me assistant): the portal signs passes the home voice-gate
accepts (same home token, its own label and scopes, so a music or video pass never opens it), the Captain alone gets the pass that
lets the gate speak in the cloned voice, and both chat pages carry the mic and speaker keys that static/skippy-voice.js drives.

The gate's own code runs in a SEPARATE Python process (services/voice-gate/gate.py, like the video gate's tests)."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import home as home_mod  # noqa: E402
import voice as voice_mod  # noqa: E402
from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402

import app as portal  # noqa: E402

TOKEN = "the-home-token"
GATE = "https://skippy-pc.tailab2cc7.ts.net:10000"
PORTAL = Path(__file__).resolve().parents[1]
STATIC = PORTAL / "static"
VOICE_GATE = PORTAL.parent / "services" / "voice-gate"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def paired_home(clock, caps=None):
    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}))
    home = home_mod.Home(vm, clock=clock)
    home.accept({"capabilities": caps if caps is not None else {"voice_gate": {"ready": True, "url": GATE}}})
    return home


def gate_says(token, passes, now):
    """Ask services/voice-gate/gate.py itself, in its own process, whether it takes each pass: its scope or False."""
    code = ("import json, sys; sys.path.insert(0, '.'); import gate; a = json.loads(sys.argv[1]); "
            "print(json.dumps([gate.valid(gate.gate_key(a['token']), p, a['now']) or False for p in a['passes']]))")
    run = subprocess.run([sys.executable, "-c", code, json.dumps({"token": token, "passes": passes, "now": now})],
                         cwd=str(VOICE_GATE), capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(run.stdout)


def test_the_pass_opens_the_voice_gate_with_the_right_scope():
    pytest.importorskip("requests")
    clock = Clock()
    v = voice_mod.Voice(paired_home(clock), clock=clock)
    listen, speak = v.gate(), v.gate(speak=True)
    assert listen["ok"] and listen["url"] == GATE and not listen["speak"] and speak["speak"]
    assert gate_says(TOKEN, [listen["pass"], speak["pass"]], clock.now) == ["listen", "speak"]
    assert listen["pass"] != speak["pass"]
    assert gate_says("another-token", [listen["pass"]], clock.now) == [False]
    assert gate_says(TOKEN, [listen["pass"]], listen["exp"] + 1) == [False]


def test_other_gates_passes_never_open_the_voice_gate():
    pytest.importorskip("requests")
    import player as player_mod
    import video as video_mod
    clock = Clock()
    sha = hashlib.sha256(TOKEN.encode()).hexdigest()
    exp = int(clock.now) + 600
    music = player_mod.sign(player_mod.gate_key(sha), exp)
    video = video_mod.sign(video_mod.gate_key(sha), exp)
    assert gate_says(TOKEN, [music, video], clock.now) == [False, False]


def test_no_gate_when_the_pc_is_silent_unpaired_or_the_address_is_off_the_tailnet():
    clock = Clock()
    assert not voice_mod.Voice(paired_home(clock, caps={}), clock=clock).gate()["ok"]
    assert not voice_mod.Voice(paired_home(clock, caps={"voice_gate": {"ready": False, "url": GATE}}), clock=clock).gate()["ok"]
    for url in ("http://skippy-pc.x.ts.net:10000", "https://evil.example.com:10000", "https://skippy-pc.x.ts.net:8443"):
        caps = {"voice_gate": {"ready": True, "url": url}}
        assert not voice_mod.Voice(paired_home(clock, caps=caps), clock=clock).gate()["ok"], url
    h = paired_home(clock)
    clock.now += home_mod.STALE_AFTER + 5
    assert not voice_mod.Voice(h, clock=clock).gate()["ok"]


def test_the_gate_route_needs_sign_in_and_says_why_when_home_is_unpaired():
    app = portal.create_app(SETTINGS, vm=FakeVm(), skippy=FakeSkippy())
    c = app.test_client()
    assert c.get("/chat/voice/gate").status_code in (302, 401, 403)
    login(c, ADMIN)
    r = c.get("/chat/voice/gate")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store" and r.get_json()["ok"] is False


def test_both_chats_carry_the_mic_and_speaker_keys():
    chat = (PORTAL / "templates" / "_chat.html").read_text(encoding="utf-8")
    shop = (PORTAL / "templates" / "_workshop.html").read_text(encoding="utf-8")
    page = (PORTAL / "templates" / "chat.html").read_text(encoding="utf-8")
    me = (PORTAL / "templates" / "me.html").read_text(encoding="utf-8")
    for tpl in (chat, shop):
        assert "SkippyVoice.attach" in tpl and "#g-mic" in tpl and "#g-vol" in tpl and "chat.voice_gate" in tpl
        assert "voice.say(" in tpl
    assert "skippy-voice.js" in page and "skippy-voice.js" in me
    # the chats' inline scripts run as they are parsed: the client must be loaded before the pages include them
    assert page.index("skippy-voice.js") < page.index('include "_chat.html"')
    assert me.index("skippy-voice.js") < me.index('include "_workshop.html"')
    assert 'id="g-mic"' in (PORTAL / "templates" / "_glyphs.html").read_text(encoding="utf-8")


def test_the_client_uses_no_browser_vendor_speech_cloud():
    js = (STATIC / "skippy-voice.js").read_text(encoding="utf-8")
    assert "SpeechRecognition" not in js and "webkitSpeechRecognition" not in js
    assert not re.search(r"""["']https?://""", js)                    # the only host it talks to is the one the portal names


def _node():
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        return exe if int(out.lstrip("v").split(".")[0]) >= 18 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


@pytest.mark.skipif(_node() is None, reason="Node 18+ isn't installed here")
def test_the_voice_clients_helpers_pass_their_node_tests():
    d = PORTAL / "tests" / "js" / "voice"
    run = subprocess.run([_node(), "--test", *sorted(str(p) for p in d.glob("*.test.js"))], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300, cwd=str(d))
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1500:]
