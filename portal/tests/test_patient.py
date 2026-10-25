"""The patient queue (patient.py): pictures and songs wait for a quiet machine and a mostly free GPU, one at a time per machine, oldest
first; they outlive the chat and the portal (written down in the bucket, put back in line after a restart with their own id), the card
says where they are, the result is served to its owner only, and the Captain gets a DM when his is ready."""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import chat_tools  # noqa: E402
import patient as pm  # noqa: E402
import tabletop  # noqa: E402
from test_workshop import CONV  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, _advertise, _pair, login  # noqa: E402

PNG = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + bytes(32)
MP3 = b"ID3" + bytes(64)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


# ── the store and the rules ──
def test_a_job_is_written_down_kept_and_served_to_its_owner_only():
    clock = Clock()
    p = pm.Patient(forever=lambda e: e == ADMIN, clock=clock)
    p.expect("job123", FRIEND, "make_image", "core", {"prompt": "a red barn", "width": 1024})
    assert p.pending("job123")["job"] == {"prompt": "a red barn", "width": 1024} and p.pending("job123")["box"] == "core"
    assert p.state("job123", FRIEND) == "waiting" and p.state("job123", ADMIN) == "unknown" and p.state("job123", ADMIN, True) == "waiting"
    kept = p.keep("job123", PNG, "image/png", "barn")
    assert kept["type"] == "image/png" and p.state("job123", FRIEND) == "done" and p.get("job123", FRIEND) == (PNG, "image/png")
    assert p.get("job123", "someone@else.com") is None and not any("pending" in k for k in p.store.files)
    assert FRIEND not in json.dumps({k: v[0].decode("latin-1") for k, v in p.store.files.items()})     # never the address
    assert p.keep("nojob99", PNG, "image/png") is None and p.keep("job123", b"<svg/>", "image/svg+xml") is None
    p.expect("song456", ADMIN, "make_music", "pc", {"prompt": "sea shanty"})
    assert p.keep("song456", MP3, "audio/mpeg")["keep"] is True and p.get("song456", ADMIN) == (MP3, "audio/mpeg")
    assert any(k.startswith("chat-pictures/keep/song456") for k in p.store.files)            # the Captain's: kept like his chats
    p.expect("old789", FRIEND, "make_image", "pc", {"prompt": "slow"})
    clock.t += pm.JOB_TTL + 1
    assert p.state("old789", FRIEND) == "unknown" and "old789" not in p.waiting()            # given up on after 24 hours


def test_a_machine_is_ready_only_after_five_quiet_minutes_and_with_its_gpu_mostly_free():
    clock = Clock()
    p = pm.Patient(clock=clock)
    assert p.ready("pc")[0] is False and p.ready("core")[0] is False    # a portal that just started counts from its own start
    clock.t += pm.QUIET_S
    assert p.ready("pc") == (True, "")
    p.touch("pc")
    ok, why = p.ready("pc")
    assert not ok and "quiet for 5 minutes" in why and p.ready("core")[0] is True    # each machine has its own clock
    clock.t += pm.QUIET_S
    assert p.ready("pc", {"free_pct": 22, "util_pct": 86}) == (False, "waiting for the GPU to free up (22% free)")
    assert p.ready("pc", {"free_pct": 70, "util_pct": 86}) == (False, "waiting for the GPU to settle (86% busy)")
    assert p.ready("pc", {"free_pct": 70, "util_pct": 5}) == (True, "")


# ── the queue, end to end ──
@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(chat_tools, "IMAGE_WAIT_S", 0.01)
    monkeypatch.setattr(chat_tools, "MUSIC_WAIT_S", 0.01)
    clock, store = Clock(), tabletop.MemoryStore()
    skippy = FakeSkippy()
    asked = {"tool": None, "args": None}

    def reply(system, messages, tools=None, run_tool=None, model=None, force_tool=None, **kw):
        if asked["tool"] and tools and any(t["name"] == asked["tool"] for t in tools):
            json.loads(run_tool(asked["tool"], asked["args"]))
        return "On it."
    skippy.reply = reply
    pats = pm.Patient(store, forever=lambda e: e == ADMIN, clock=clock)
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy, pictures=pats)
    client = app.test_client()
    token = _pair(client)
    _advertise(client, token, make_image=True, make_music=True)
    H = {"Authorization": f"Bearer {token}"}
    return {"app": app, "client": client, "H": H, "clock": clock, "pats": pats, "asked": asked, "store": store, "token": token}


def ask(w, tool, args, message="make something"):
    w["asked"].update(tool=tool, args=args)
    return w["client"].post("/chat/send", json={"message": message}).get_json()


def handed(w):
    return [j for j in w["client"].get("/home/jobs", headers=w["H"]).get_json()["jobs"] if j["kind"] in pm.KINDS]


def quiet(w, gpu=None):
    w["clock"].t += pm.QUIET_S + 1
    w["app"].extensions["starter"].poll({"status": {"docker": True, **(gpu or {})}})


def test_a_picture_waits_for_a_quiet_machine_and_the_card_says_why(world):
    d = ask(world, "make_image", {"prompt": "a red barn at dusk"}, "draw me a red barn")
    (item,) = d["media"]
    assert item["kind"] == "image" and item["pending"] is True
    assert handed(world) == []                                       # someone just chatted: it waits
    state = world["client"].get(f"/chat/media/{item['job_id']}/state").get_json()
    assert state["state"] == "queued" and "quiet for 5 minutes" in state["why"]
    quiet(world)
    (job,) = handed(world)
    assert job["id"] == item["job_id"] and job["prompt"] == "a red barn at dusk" and job["box"] == "pc"
    assert world["client"].get(f"/chat/media/{item['job_id']}/state").get_json()["state"] == "working"


def test_skippy_pcs_gpu_must_be_mostly_free_and_one_job_at_a_time_oldest_first(world):
    first = ask(world, "make_music", {"prompt": "sea shanty"})["media"][0]
    second = ask(world, "make_image", {"prompt": "a lighthouse"})["media"][0]
    assert first["kind"] == "audio" and first["pending"] is True
    quiet(world, {"gpu_free_pct": 22, "gpu_util_pct": 86})
    assert handed(world) == []                                       # quiet, but the card is busy
    why = world["client"].get(f"/chat/media/{first['job_id']}/state").get_json()["why"]
    assert why == "waiting for the GPU to free up (22% free)"
    quiet(world, {"gpu_free_pct": 75, "gpu_util_pct": 3})
    assert [j["id"] for j in handed(world)] == [first["job_id"]]     # the oldest; the picture waits its turn on the same machine
    world["client"].put(f"/home/media/{first['job_id']}?title=shanty", data=MP3, headers={**world["H"], "Content-Type": "audio/mpeg"})
    world["client"].post(f"/home/jobs/{first['job_id']}", json={"result": {"title": "shanty", "seconds": 45}}, headers=world["H"])
    quiet(world, {"gpu_free_pct": 75, "gpu_util_pct": 3})
    assert [j["id"] for j in handed(world)] == [second["job_id"]]


def test_a_job_outlives_the_portal_and_the_captain_gets_a_dm(world, monkeypatch):
    item = ask(world, "make_image", {"prompt": "a red barn"})["media"][0]
    app2 = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), pictures=world["pats"])   # a redeploy: same bucket
    c2 = app2.test_client()
    t2 = _pair(c2)
    H2 = {"Authorization": f"Bearer {t2}"}
    c2.post("/home/report", json={"status": {}, "capabilities": {"make_image": {"ready": True}}}, headers=H2)     # puts it back in line
    world["clock"].t += pm.QUIET_S + 1
    jobs = [j for j in c2.get("/home/jobs", headers=H2).get_json()["jobs"] if j["kind"] in pm.KINDS]
    assert [j["id"] for j in jobs] == [item["job_id"]] and jobs[0]["prompt"] == "a red barn"   # the same id: its card keeps working
    told = []
    monkeypatch.setattr(app2.extensions["notifier"], "alert", lambda text: told.append(text) or True)
    put = c2.put(f"/home/media/{item['job_id']}?title=barn", data=PNG, headers={**H2, "Content-Type": "image/png"})
    assert put.status_code == 200 and told == ["Your picture is ready in Skippy's chat: barn."]
    login(c2, ADMIN)
    assert c2.get(f"/chat/media/{item['job_id']}/state").get_json()["state"] == "done"
    got = c2.get(f"/chat/media/{item['job_id']}")
    assert got.status_code == 200 and got.data == PNG
    login(c2, FRIEND)
    assert c2.get(f"/chat/media/{item['job_id']}").status_code == 404 and c2.get(f"/chat/media/{item['job_id']}/state").get_json() == {"state": "unknown"}


def test_a_guests_picture_waits_the_same_and_gets_no_dm(world, monkeypatch):
    login(world["client"], FRIEND)
    item = ask(world, "make_image", {"prompt": "a cat"})["media"][0]
    told = []
    monkeypatch.setattr(world["app"].extensions["notifier"], "alert", lambda text: told.append(text) or True)
    quiet(world)
    handed(world)
    world["client"].put(f"/home/media/{item['job_id']}", data=PNG, headers={**world["H"], "Content-Type": "image/png"})
    assert told == [] and world["client"].get(f"/chat/media/{item['job_id']}/state").get_json()["state"] == "done"


def test_activity_resets_the_quiet_clock():
    clock = Clock()
    p = pm.Patient(clock=clock)
    p.quiet_for("core")
    clock.t += pm.QUIET_S + 10
    assert p.ready("core")[0]
    p.touch("core", "pc")
    assert not p.ready("core")[0] and not p.ready("pc")[0]


def test_the_chat_keeps_a_place_for_pictures_and_songs_and_its_card_waits():
    from pathlib import Path
    page = (Path(portal.__file__).parent / "templates" / "_chat.html").read_text(encoding="utf-8")
    assert "function waitCard" in page and "[picture:" not in page.split("pictureNote")[0][-10:]
    assert '"song" : m.kind === "image" ? "picture" : "result"' in page and "In line: " in page and "Composing…" in page and "chat.media_state" in page
    assert "Drawing (quality, up to an hour)…" in page and 'd.tier === "hq"' in page


def test_skippy_core_counts_free_gigabytes_not_a_share_because_its_memory_is_shared_with_the_llms():
    clock = Clock()
    p = pm.Patient(clock=clock)
    p.quiet_for("core")
    clock.t += pm.QUIET_S + 1
    assert p.ready("core", {"free_pct": 28, "util_pct": 3, "free_gib": 30.0}) == (False, "waiting for memory to free up (30 GB free, 35 needed)")
    assert p.ready("core", {"free_pct": 38, "util_pct": 3, "free_gib": 46.0}) == (True, "")           # 38% free is plenty there
    assert p.ready("core", {"free_pct": 55, "util_pct": 70, "free_gib": 70.0}) == (False, "waiting for the GPU to settle (70% busy)")


def test_a_quality_picture_goes_to_skippy_core_and_waits_for_the_core_studios_own_figures(world):
    caps = {"make_image": {"ready": True, "box": "pc"},
            "make_image_hq": {"ready": True, "box": "core", "gpu_free_pct": 25, "gpu_util_pct": 2, "gpu_free_gib": 30.0}}
    send = lambda: world["client"].post("/home/report", json={"status": {}, "capabilities": caps}, headers=world["H"])   # noqa: E731
    send()
    item = ask(world, "make_image", {"prompt": "a nebula", "quality": True}, "a quality image of a nebula")["media"][0]
    assert item["tier"] == "hq"
    world["clock"].t += pm.QUIET_S + 1
    send()
    assert handed(world) == []
    state = world["client"].get(f"/chat/media/{item['job_id']}/state").get_json()
    assert state["why"] == "waiting for memory to free up (30 GB free, 35 needed)" and state["tier"] == "hq" and state["box"] == "core"
    caps["make_image_hq"]["gpu_free_gib"] = 46.0                  # the box with its three LLMs resident
    reply = send().get_json()                                       # (the report's own reply is how the reporter gets its jobs)
    (job,) = [j for j in reply["jobs"] if j["kind"] in pm.KINDS]
    assert job["kind"] == "make_image_hq" and job["box"] == "core"
    assert world["client"].get(f"/chat/media/{item['job_id']}/state").get_json() == {"state": "working", "tier": "hq"}


def test_a_picture_is_quick_unless_quality_is_asked_for_and_each_tier_says_when_it_is_off(world):
    caps = {"make_image": {"ready": True}}
    world["client"].post("/home/report", json={"status": {}, "capabilities": caps}, headers=world["H"])
    with world["app"].test_request_context():
        tools = world["app"].extensions["chat_tools_for"]({"email": ADMIN})
        with pytest.raises(chat_tools.ToolError, match="Quality pictures"):
            tools._make_image({"prompt": "an owl", "quality": True})              # skippy-core isn't advertising: say so
    (item,) = ask(world, "make_image", {"prompt": "an infographic of the planets"})["media"]
    assert "tier" not in item and world["pats"].pending(item["job_id"])["kind"] == "make_image"
    schema = next(t for t in tools.schemas() if t["name"] == "make_image")
    assert "quality" in schema["input_schema"]["properties"] and "up to an hour" in schema["description"]


# ── a picture the AGENT asked for (a SKIPPY_PICTURE line: the bridge sends {"t": "picture", "id", "prompt"}) ──
def test_an_agent_picture_becomes_a_patient_job_with_the_bridges_id_once_and_its_card_finds_it(world):
    pic = {"t": "picture", "id": "0123456789abcdef", "prompt": "a grey tabby cat asleep on a windowsill"}
    push = lambda after: world["client"].post("/home/workshop/abcd1234", headers=world["H"], json={    # noqa: E731
        "who": ADMIN, "conv": CONV, "start": after, "events": [pic], "state": "running"})
    assert push(0).status_code == 200
    push(0)                                                          # pushed again (a retry): still one job
    assert [e["t"] for e in world["app"].extensions["workshop"].read(ADMIN, "abcd1234")["events"]] == ["picture"]
    quiet(world)
    (job,) = handed(world)
    assert job["id"] == pic["id"] and job["kind"] == "make_image" and job["prompt"] == pic["prompt"] and "_id" not in job
    assert world["pats"].pending(pic["id"])["job"].get("_id") is None
    login(world["client"], ADMIN)
    assert world["client"].get(f"/chat/media/{pic['id']}/state").get_json()["state"] == "working"
    world["client"].put(f"/home/media/{pic['id']}?title=cat", data=PNG, headers={**world["H"], "Content-Type": "image/png"})
    assert world["client"].get(f"/chat/media/{pic['id']}").data == PNG


def test_an_agent_quality_picture_is_drawn_on_skippy_core(world):
    world["client"].post("/home/report", json={"status": {}, "capabilities": {
        "make_image": {"ready": True}, "make_image_hq": {"ready": True, "box": "core", "gpu_free_gib": 50.0, "gpu_util_pct": 1}}},
        headers=world["H"])
    pic = {"t": "picture", "id": "fedcba9876543210", "prompt": "a lighthouse in a storm", "tier": "hq"}
    world["client"].post("/home/workshop/abcd1234", headers=world["H"], json={
        "who": ADMIN, "conv": CONV, "start": 0, "events": [pic], "state": "running"})
    assert world["pats"].pending(pic["id"])["kind"] == "make_image_hq" and world["pats"].pending(pic["id"])["box"] == "core"
    assert world["app"].extensions["workshop"].read(ADMIN, "abcd1234")["events"][0]["tier"] == "hq"


@pytest.mark.parametrize("bad", [{"id": "nothex!!nothex!!"}, {"id": "abc"}, {"prompt": "  "}, {"prompt": 7}])
def test_a_malformed_picture_event_is_dropped(bad):
    import workshop as workshop_mod
    assert workshop_mod._clean({"t": "picture", "id": "0123456789abcdef", "prompt": "a cat", **bad}) is None


def test_the_agents_transcript_shows_its_pictures_with_the_chats_waiting_card():
    from pathlib import Path
    folder = Path(portal.__file__).parent / "templates"
    assert "window.SkippyWaitCard = waitCard" in (folder / "_chat.html").read_text(encoding="utf-8")
    page = (folder / "_workshop.html").read_text(encoding="utf-8")
    assert 'p.t === "picture"' in page and page.count('e.t === "picture"') == 2 and "SkippyWaitCard(" in page


def test_the_queue_says_where_each_job_is_to_whoever_holds_the_home_token(world):
    item = ask(world, "make_image", {"prompt": "a dog"})["media"][0]
    c = world["client"]
    assert c.get("/home/patient").status_code == 401
    (row,) = c.get("/home/patient", headers=world["H"]).get_json()["jobs"]
    assert row["id"] == item["job_id"] and row["kind"] == "make_image" and "quiet for 5 minutes" in row["where"]
    quiet(world)
    handed(world)
    assert c.get("/home/patient", headers=world["H"]).get_json()["jobs"][0]["where"] == "handed out"


def test_a_picture_right_after_another_doesnt_wait_for_the_studio_to_unload(world):
    """The studio holds its model ~10 GB for 10 idle minutes after a picture: that memory is the next picture's, not "in use"."""
    item = ask(world, "make_image", {"prompt": "a second cat"})["media"][0]
    caps = {"make_image": {"ready": True, "box": "pc", "loaded": True}}
    world["client"].post("/home/report", json={"status": {}, "capabilities": caps}, headers=world["H"])
    quiet(world, {"gpu_free_pct": 8, "gpu_util_pct": 3})
    assert [j["id"] for j in handed(world)] == [item["job_id"]]


def test_an_unloaded_studio_still_needs_the_card_mostly_free(world):
    ask(world, "make_image", {"prompt": "a cat"})
    caps = {"make_image": {"ready": True, "box": "pc", "loaded": False}}
    world["client"].post("/home/report", json={"status": {}, "capabilities": caps}, headers=world["H"])
    quiet(world, {"gpu_free_pct": 8, "gpu_util_pct": 3})
    assert handed(world) == []
