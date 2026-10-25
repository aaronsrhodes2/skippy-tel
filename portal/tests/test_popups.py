"""Pop-ups (popups.py, docs/popups.md): one registry entry and its own container make a model a tool of Skippy's. The registry is checked;
a ready pop-up becomes a chat tool for its audience, a patient job on its machine (the quiet rule, one per machine) and an agent line;
an attached picture or sound rides along; its result is kept like a picture and the card knows what it shows. The last test runs the
whole path for real: the agent's line, the portal over HTTP, the home reporter's own code and the echo service."""
import base64
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import attachments  # noqa: E402
import chat_tools  # noqa: E402
import media  # noqa: E402
import patient as pm  # noqa: E402
import popups  # noqa: E402
from test_app import ADMIN, FRIEND, login  # noqa: E402
from test_patient import PNG, handed, quiet, world  # noqa: E402,F401
from test_workshop import CONV  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ECHO = popups.load()["echo"]
WAV = b"RIFF" + bytes(4) + b"WAVEfmt " + bytes(32)


def entry(**over):
    """A pop-up as a family would register it (a chat one, unlike echo)."""
    return {**ECHO, "name": "remove_bg", "family": "vision", "chat": True, "audience": "everyone", "media_in": ["image/*"],
            "outputs": ["image/png"], "est_seconds": 120, "vram_gb": 3, "mem_gb": 3,
            "description": "Cuts the subject out of a picture the user attached, on a transparent background.", **over}


# ── the registry ──
def test_every_registered_popup_in_the_repo_is_usable_and_the_sample_is_there():
    assert popups.problems() == []
    assert "echo" in popups.names() and ECHO["box"] == "pc" and ECHO["chat"] is False      # a test service: never a chat tool
    for path in popups.files(ROOT):
        for raw in json.loads(path.read_text(encoding="utf-8"))["popups"]:
            assert popups.check(raw) is None, (path, raw.get("name"))


@pytest.mark.parametrize("bad, why", [
    ({"name": "make_image"}, "taken"), ({"name": "Bad-Name"}, "name"), ({"box": "mac"}, "box"), ({"tier": "fast"}, "tier"),
    ({"url_env": "ECHO"}, "url_env"), ({"outputs": ["image/svg+xml"]}, "output"), ({"outputs": []}, "outputs"),
    ({"media_in": ["application/pdf"]}, "media_in"), ({"est_seconds": 0}, "est_seconds"), ({"audience": "friends"}, "audience"),
    ({"description": "short"}, "description"), ({"inputs": {"type": "string"}}, "inputs"), ({"endpoint": "run"}, "endpoint"),
    ({"media_required": True, "media_in": []}, "media_required"),
    ({"inputs": {"type": "object", "properties": {"attachment": {"type": "string"}}}}, "attachment"),
])
def test_a_bad_entry_says_what_is_wrong(bad, why):
    assert why in (popups.check({**ECHO, **bad}) or "")


def test_a_bad_or_duplicate_entry_is_skipped_never_a_crash(tmp_path):
    (tmp_path / "services" / "popups").mkdir(parents=True)
    (tmp_path / "services" / "popups" / "registry.json").write_text(json.dumps({"popups": [ECHO]}))
    (tmp_path / "services" / "audio-popups").mkdir()
    (tmp_path / "services" / "audio-popups" / "popups.json").write_text(json.dumps({"popups": [
        {**ECHO}, {**ECHO, "name": "stems", "box": "nowhere"}, {**ECHO, "name": "stems_ok", "family": "audio"}]}))
    (tmp_path / "services" / "vision-popups").mkdir()
    (tmp_path / "services" / "vision-popups" / "popups.json").write_text("{not json")
    try:
        assert sorted(popups.load(tmp_path)) == ["echo", "stems_ok"]
        assert len(popups.problems(tmp_path)) == 3
    finally:
        popups.load()                                       # the repo's again, for the other tests


def test_the_patient_queue_and_the_media_hand_off_keep_every_output_type_and_the_card_knows_each():
    assert set(pm.TYPES) == media.TYPES == set(popups.OUTPUT_TYPES)
    for ctype in ("model/gltf-binary", "video/mp4", "audio/wav", "audio/flac", "application/json", "text/plain"):
        assert ctype in pm.TYPES
    assert [popups.card_kind(t) for t in ("image/png", "audio/flac", "video/mp4", "model/gltf-binary", "application/json",
                                          "audio/midi", "application/zip")] == ["image", "audio", "video", "model", "text", "file", "file"]


def test_a_sound_can_be_attached_and_no_brain_is_handed_the_bytes():
    url = "data:audio/wav;base64," + base64.b64encode(WAV).decode()
    (f,) = attachments.clean([{"name": "riff.wav", "data": url}])
    assert f.kind == "audio" and f.mime == "audio/wav"
    assert "can't hear it" in attachments.anthropic_blocks([f], "hi")[0]["text"] and "can't hear" in attachments.text_for([f], "hi", None)
    with pytest.raises(ValueError):
        attachments.clean([{"name": "fake.wav", "data": "data:audio/wav;base64," + base64.b64encode(b"nope" * 8).decode()}])


# ── the chat tool, its audience and its attachment ──
@pytest.fixture
def registered(monkeypatch):
    found = {"remove_bg": popups.load()["echo"] | entry(), "secret_lab": entry(name="secret_lab", audience="admin", media_in=[])}
    monkeypatch.setattr(popups, "load", lambda root=None: found)
    return found


def tools(app, email):
    ctx = app.app_context()                    # (the tools read the app's settings: who is an admin)
    ctx.push()
    _contexts.append(ctx)
    return app.extensions["chat_tools_for"]({"email": email})


_contexts: list = []


@pytest.fixture(autouse=True)
def _pop_contexts():
    yield
    while _contexts:
        _contexts.pop().pop()


def test_a_ready_popup_is_a_tool_for_its_audience_only(world, registered):
    from test_app import _advertise
    assert "remove_bg" not in tools(world["app"], ADMIN).available()              # not ready at home: not offered
    _advertise(world["client"], world["token"], remove_bg=True, secret_lab=True)
    captain, friend = tools(world["app"], ADMIN), tools(world["app"], FRIEND)
    assert {"remove_bg", "secret_lab", "popup_result"} <= set(captain.available())
    assert "remove_bg" in friend.available() and "secret_lab" not in friend.available()
    schema = next(t for t in friend.schemas() if t["name"] == "remove_bg")
    assert schema["input_schema"]["properties"]["attachment"]["type"] == "integer" and "Cuts the subject out" in schema["description"]
    assert "attachment" not in next(t for t in captain.schemas() if t["name"] == "secret_lab")["input_schema"]["properties"]


def test_the_attached_picture_rides_with_the_job_and_waits_for_a_quiet_machine(world, registered, monkeypatch):
    from test_app import _advertise
    monkeypatch.setattr(chat_tools, "POPUP_WAIT_S", 0.01)
    _advertise(world["client"], world["token"], remove_bg=True)
    pic = "data:image/png;base64," + base64.b64encode(PNG).decode()
    world["asked"].update(tool="remove_bg", args={"text": "cut out the dog"})
    d = world["client"].post("/chat/send", json={"message": "cut the dog out", "files": [{"name": "dog.png", "data": pic}]}).get_json()
    (item,) = d["media"]
    assert item["kind"] == "popup" and item["pending"] is True
    assert handed(world) == []                                               # someone just chatted
    state = world["client"].get(f"/chat/media/{item['job_id']}/state").get_json()
    assert state["state"] == "queued" and state["popup"] == "remove_bg" and state["est_s"] == 120
    quiet(world)
    (job,) = handed(world)
    assert job["kind"] == "popup" and job["name"] == "remove_bg" and job["box"] == "pc"
    assert json.loads(job["args"]) == {"text": "cut out the dog"} and base64.b64decode(job["input_b64"]) == PNG
    assert job["input_type"] == "image/png" and job["input_name"] == "dog.png"
    assert world["pats"].pending(item["job_id"])["job"]["input_b64"] == job["input_b64"]    # a restarted portal can put it back whole


def test_one_popup_job_per_machine_and_a_core_popup_waits_for_core(world, registered):
    from test_app import _advertise
    _advertise(world["client"], world["token"], remove_bg=True, make_image=True)
    registered["remove_bg"]["box"] = "pc"
    first = tools(world["app"], ADMIN)
    first.home_popup.start("popup", owner=ADMIN, **first.popup_payload(registered["remove_bg"], {"text": "a"}, None))
    first.home_popup.start("popup", owner=ADMIN, **first.popup_payload(registered["remove_bg"], {"text": "b"}, None))
    quiet(world)
    assert len(handed(world)) == 1                                           # the second waits behind the first on skippy-pc


def test_a_guests_popups_are_ten_a_day(world, registered, monkeypatch):
    from test_app import _advertise
    monkeypatch.setattr(chat_tools, "POPUP_WAIT_S", 0.01)
    _advertise(world["client"], world["token"], remove_bg=True)
    t = tools(world["app"], FRIEND)
    for _ in range(10):
        t.run("remove_bg", {"text": "x"})
    with pytest.raises(chat_tools.ToolError, match="10 a day"):
        t.run("remove_bg", {"text": "x"})


def test_a_required_file_that_is_missing_or_the_wrong_kind_is_said_plainly(world, registered):
    from test_app import _advertise
    registered["remove_bg"]["media_required"] = True
    _advertise(world["client"], world["token"], remove_bg=True)
    t = tools(world["app"], ADMIN)
    with pytest.raises(chat_tools.ToolError, match="needs a file attached"):
        t.run("remove_bg", {"text": "x"})
    t.files = attachments.clean([{"name": "riff.wav", "data": "data:audio/wav;base64," + base64.b64encode(WAV).decode()}])
    with pytest.raises(chat_tools.ToolError, match="isn't a file remove_bg takes"):
        t.run("remove_bg", {"text": "x", "attachment": 1})
    with pytest.raises(chat_tools.ToolError, match="no attached file with that number"):
        t.run("remove_bg", {"text": "x", "attachment": 3})


# ── the agent ──
def test_an_agent_popup_is_a_patient_job_under_the_bridges_id_once(world):
    from test_app import _advertise
    _advertise(world["client"], world["token"], echo=True)
    ev = {"t": "popup", "id": "00112233445566ff", "name": "echo", "args": {"text": "hello"},
          "input": {"type": "audio/wav", "name": "riff.wav", "b64": base64.b64encode(WAV).decode()}}
    push = lambda: world["client"].post("/home/workshop/abcd1234", headers=world["H"], json={    # noqa: E731
        "who": ADMIN, "conv": CONV, "start": 0, "events": [ev], "state": "running"})
    push()
    push()
    shown = world["app"].extensions["workshop"].read(ADMIN, "abcd1234")["events"]
    assert shown == [{"t": "popup", "id": ev["id"], "name": "echo", "args": {"text": "hello"}}]       # never the file in the transcript
    quiet(world)
    (job,) = handed(world)
    assert job["id"] == ev["id"] and job["name"] == "echo" and job["input_type"] == "audio/wav"


@pytest.mark.parametrize("bad", [{"name": "Echo!"}, {"id": "xyz"}, {"args": "text"}, {"args": {"x": "y" * 5000}}])
def test_a_malformed_popup_event_is_dropped(bad):
    import workshop as workshop_mod
    assert workshop_mod._clean({"t": "popup", "id": "00112233445566ff", "name": "echo", "args": {}, **bad}) is None


def test_an_unregistered_or_not_ready_popup_from_the_agent_is_not_queued(world):
    for ev in ({"t": "popup", "id": "aa112233445566ff", "name": "nosuch", "args": {}},
               {"t": "popup", "id": "bb112233445566ff", "name": "echo", "args": {}}):      # echo not advertised in this world
        world["client"].post("/home/workshop/abcd1234", headers=world["H"], json={
            "who": ADMIN, "conv": CONV, "start": 0, "events": [ev], "state": "running"})
    quiet(world)
    assert handed(world) == []


def test_the_page_shows_every_output_type_and_the_agents_popups():
    chat = (ROOT / "portal/templates/_chat.html").read_text(encoding="utf-8")
    shop = (ROOT / "portal/templates/_workshop.html").read_text(encoding="utf-8")
    art = (ROOT / "portal/static/skippy-artifacts.js").read_text(encoding="utf-8")
    assert "done(d.kind)" in chat and "if (m.job_id)" in chat and "result" in chat.split("const PIC")[1].split("\n")[0]
    assert 'p.t === "popup"' in shop and 'kind: "popup"' in shop
    assert 'text: "text"' in art and 'k === "text"' in art and 'setAttribute("sandbox", "")' in art and 'k === "model"' in art and 'k === "video"' in art


# ── the whole path, for real ──
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_end_to_end_an_agent_line_runs_the_echo_service_through_the_real_reporter(world, monkeypatch):
    from werkzeug.serving import make_server
    sys.path.insert(0, str(ROOT / "services/home-reporter"))
    reporter = _load("reporter_e2e", ROOT / "services/home-reporter/reporter.py")
    echo = _load("echo_e2e", ROOT / "services/popups/echo/echo.py")
    service = echo.serve(0)
    portal = make_server("127.0.0.1", 0, world["app"], threaded=True)
    for srv in (service, portal):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(reporter, "PORTAL", f"http://127.0.0.1:{portal.server_port}")
        monkeypatch.setattr(reporter, "POPUPS_DIR", str(ROOT / "services"))
        monkeypatch.setenv("ECHO_POPUP_URL", f"http://127.0.0.1:{service.server_address[1]}")
        caps = reporter.capabilities()
        assert caps["echo"]["ready"] is True and caps["echo"]["box"] == "pc"           # the reporter advertises it ...
        world["client"].post("/home/report", json={"status": {}, "capabilities": caps}, headers=world["H"])
        ev = {"t": "popup", "id": "0a1b2c3d4e5f6a7b", "name": "echo", "args": {"text": "ping"}}
        world["client"].post("/home/workshop/abcd1234", headers=world["H"], json={    # ... the agent asks for it ...
            "who": ADMIN, "conv": CONV, "start": 0, "events": [ev], "state": "running"})
        quiet(world)
        (job,) = handed(world)                                                          # ... the portal hands it out when quiet ...
        outcome = reporter.run_job(job, world["token"])                                # ... the reporter runs it and hands it in
        assert outcome == {"result": {"title": "ping", "type": "application/json", "bytes": outcome["result"]["bytes"]}}
        world["client"].post(f"/home/jobs/{job['id']}", json=outcome, headers=world["H"])
        login(world["client"], ADMIN)
        state = world["client"].get(f"/chat/media/{ev['id']}/state").get_json()
        assert state == {"state": "done", "kind": "text"}                              # the card becomes a JSON block
        got = world["client"].get(f"/chat/media/{ev['id']}")
        assert got.mimetype == "application/json" and got.get_json() == {"echo": {"text": "ping"}, "input": None}
        # a picture handed to it comes back as the picture (mirror): the card becomes an image
        mirror = {"id": "mirror01", "kind": "popup", "name": "echo", "args": json.dumps({"text": "x", "mirror": True}),
                  "input_b64": base64.b64encode(PNG).decode(), "input_type": "image/png"}
        world["pats"].expect("mirror01", ADMIN, "popup", "pc", mirror)
        assert reporter.run_job(mirror, world["token"])["result"]["type"] == "image/png"
        assert world["client"].get("/chat/media/mirror01").data == PNG
        assert world["client"].get("/chat/media/mirror01/state").get_json()["kind"] == "image"
    finally:
        service.shutdown()
        portal.shutdown()
        sys.path.remove(str(ROOT / "services/home-reporter"))


def test_a_midi_or_zip_result_is_a_download_never_opened_in_the_page(world):
    zipped = b"PK\x03\x04" + bytes(40)
    world["pats"].expect("stems001", ADMIN, "popup", "pc", {"name": "echo", "args": "{}"})
    world["client"].put("/home/media/stems001?title=stems", data=zipped, headers={**world["H"], "Content-Type": "application/zip"})
    login(world["client"], ADMIN)
    got = world["client"].get("/chat/media/stems001")
    assert got.data == zipped and got.headers["Content-Disposition"] == 'attachment; filename="stems001.zip"'
    assert world["client"].get("/chat/media/stems001/state").get_json()["kind"] == "file"
    art = (ROOT / "portal/static/skippy-artifacts.js").read_text(encoding="utf-8")
    assert 'k === "file"' in art and "a.download" in art
