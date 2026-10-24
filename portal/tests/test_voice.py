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
import time
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


def test_the_chat_has_one_voice_key_and_the_agent_keeps_its_own():
    chat = (PORTAL / "templates" / "_chat.html").read_text(encoding="utf-8")
    shop = (PORTAL / "templates" / "_workshop.html").read_text(encoding="utf-8")
    page = (PORTAL / "templates" / "front.html").read_text(encoding="utf-8")
    # the chat: one key, two modes (Typing listens only for "Skippy"; Hands-off listens and speaks until "thanks")
    assert 'class="key voice"' in chat and 'key: form.querySelector("button.voice")' in chat and "#g-ear" in chat
    assert 'class="key ear"' not in chat and 'class="key mic"' not in chat and 'class="key spk"' not in chat
    assert "SkippyVoice.attach" in chat and "chat.voice_gate" in chat and "voice.say(" in chat
    assert 'class="vstat"' in chat and "box:" in chat and "onSend:" in chat and "form.requestSubmit()" in chat
    # the agent's own composer (hidden inside the chat) keeps its three keys
    assert "SkippyVoice.attach" in shop and "#g-mic" in shop and "#g-vol" in shop and "ear:" in shop and "voice.say(" in shop
    assert "skippy-voice.js" in page
    # the chats' inline scripts run as they are parsed: the client must be loaded before the pages include them
    assert page.index("skippy-voice.js") < page.index('include "_chat.html"')
    assert 'include "_workshop.html"' in chat                                            # the agent lives inside the chat now
    glyphs = (PORTAL / "templates" / "_glyphs.html").read_text(encoding="utf-8")
    assert 'id="g-mic"' in glyphs and 'id="g-ear"' in glyphs


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


def test_who_gets_which_voice_is_the_portals_call_and_only_the_captain_gets_a_clone():
    for mode in (None, "", "public", "private", "auto", "AUTO", "elevenlabs", "skippy"):
        assert voice_mod.voice_for(False, mode) == "stock", mode                 # a guest is stock, whatever the page says
    assert voice_mod.voice_for(True, "auto") == "elevenlabs" and voice_mod.voice_for(True, " Auto ") == "elevenlabs"
    assert voice_mod.voice_for(True, "private") == "skippy" and voice_mod.voice_for(True, None) == "skippy"
    assert voice_mod.voice_for(True, "public") == "stock"
    assert voice_mod.voice_for(True, "nonsense") == "skippy"                       # only the word "auto" reaches the cloud voice


def test_each_voice_pass_opens_the_gate_as_that_voice_and_the_older_passes_are_unchanged():
    pytest.importorskip("requests")
    clock = Clock()
    v = voice_mod.Voice(paired_home(clock), clock=clock)
    passes = {name: v.gate(voice=name) for name in voice_mod.VOICES}
    for name, g in passes.items():
        assert g["ok"] and g["speak"] is True and g["voice"] == name and g["url"] == GATE
    assert gate_says(TOKEN, [g["pass"] for g in passes.values()], clock.now) == list(voice_mod.VOICES)
    assert v.gate()["voice"] is None and v.gate(speak=True)["voice"] == "elevenlabs"
    assert gate_says(TOKEN, [v.gate()["pass"], v.gate(speak=True)["pass"]], clock.now) == ["listen", "speak"]
    with pytest.raises(ValueError):
        v.gate(voice="kaelen")


def test_the_gate_takes_a_stock_pass_for_stock_only():
    """End to end through the gate's own code: a guest's pass speaks stock and is refused the clones."""
    pytest.importorskip("requests")
    clock = Clock()
    g = voice_mod.Voice(paired_home(clock), clock=clock).gate(voice=voice_mod.voice_for(False, "auto"))
    code = ("import json, sys; sys.path.insert(0, '.'); import gate; a = json.loads(sys.argv[1]); s = gate.valid(gate.gate_key(a['token']), a['p'], a['now']); "
            "print(json.dumps([gate.allowed(s, w) for w in (None, 'stock', 'skippy', 'elevenlabs')]))")
    run = subprocess.run([sys.executable, "-c", code, json.dumps({"token": TOKEN, "p": g["pass"], "now": clock.now})],
                         cwd=str(VOICE_GATE), capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr[-2000:]
    assert json.loads(run.stdout) == ["stock", "stock", None, None]


def test_the_gate_route_signs_the_captain_the_voice_of_his_chat_mode():
    vm = FakeVm()
    app = portal.create_app(SETTINGS, vm=vm, skippy=FakeSkippy())
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}))
    app.extensions["home"].accept({"capabilities": {"voice_gate": {"ready": True, "url": GATE}}})
    c = app.test_client()
    login(c, ADMIN)
    got = {m: c.get("/chat/voice/gate" + (f"?mode={m}" if m else "")).get_json() for m in ("", "private", "auto", "public", "nonsense")}
    assert {m: g["voice"] for m, g in got.items()} == {"": "skippy", "private": "skippy", "auto": "elevenlabs", "public": "stock",
                                                       "nonsense": "skippy"}
    assert all(g["ok"] and g["speak"] for g in got.values())
    assert gate_says(TOKEN, [got["private"]["pass"], got["auto"]["pass"]], time.time()) == ["skippy", "elevenlabs"]


def test_the_chat_tells_the_voice_client_its_mode():
    chat = (PORTAL / "templates" / "_chat.html").read_text(encoding="utf-8")
    js = (STATIC / "skippy-voice.js").read_text(encoding="utf-8")
    assert "mode: () =>" in chat and '"?mode=" + encodeURIComponent(mode)' in js and "pass.mode_ === mode" in js


def test_a_reply_is_never_silent_any_voice_failure_falls_back_to_the_device_voice():
    """The Captain's rule (2026-10-23): guests get the cheap local voice, or the device's own, but always a live one."""
    js = (STATIC / "skippy-voice.js").read_text(encoding="utf-8")
    say = js[js.index("async function say("):js.index("function setWanted(")]
    assert "if (!r.ok) throw" in say and "if (!blob.size) throw" in say                       # a refused or empty reply
    assert "audio.onerror = function () { URL.revokeObjectURL(url); if (playing === token) { playing = null; builtin(t, done); } };" in say
    assert "builtin(t, done);" in say.split("catch (e)")[-1]                                       # any other failure


def test_the_one_chat_takes_the_agents_reply_into_its_own_voice():
    """In Private and Auto the reply arrives in the embedded agent card (templates/_workshop.html); the chat must hand it to
    ITS voice (two modes: Hands-off always speaks, summarised when long), or a Hands-off conversation with the agent is
    silent. test_workshop_ui.py pins the card's side of the handshake."""
    from pathlib import Path
    chat = (Path(__file__).resolve().parents[1] / "templates" / "_chat.html").read_text(encoding="utf-8")
    assert "window.SkippyAgent.onReply = (t) => { if (t == null) voice.resume(); else voice.say(t); }" in chat
    assert 'addEventListener("skippy-agent-ready", hearAgent)' in chat
