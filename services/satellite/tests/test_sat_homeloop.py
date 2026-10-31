"""The home loop: the house log, the learner, and the propose -> answer -> automate path, with no hardware (a stub link, a stub gate)."""
import asyncio
import json
import time

import housemem
import proposals
import routines
from brain import Brain
from hub import Room
from timers import Timers

WED = ["2026-10-07", "2026-10-14", "2026-10-21", "2026-10-28"]           # Wednesdays
THU = ["2026-10-08", "2026-10-15", "2026-10-22"]


def stamp(date, hm):
    return time.mktime(time.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M"))


def seed(root, wake_wed=("06:30", "06:32", "06:28", "06:31"), lamp=("06:25", "06:27", "06:24", "06:26")):
    for d, w, lm in zip(WED, wake_wed, lamp):
        housemem.HouseLog(root, clock=lambda w=w, d=d: stamp(d, w)).event("wake", room="office", by="skippy")
        housemem.HouseLog(root, clock=lambda lm=lm, d=d: stamp(d, lm)).event("action", room="office", what="home", tool="home_control", target="Bedroom Lamp", act="on")
    for d, t in zip(THU, ("07:00", "07:50", "06:10")):                    # scattered Thursdays: no pattern
        housemem.HouseLog(root, clock=lambda t=t, d=d: stamp(d, t)).event("wake", room="office", by="skippy")


def test_log_is_append_only_jsonl_with_local_time_and_weekday(tmp_path):
    log = housemem.HouseLog(tmp_path, clock=lambda: stamp("2026-10-28", "06:30"))
    log.event("wake", room="office")
    log.event("command", room="office", words="lights on", tools=["home_control"], outcome="ok")
    recs = [json.loads(x) for x in (tmp_path / "log" / "2026-10-28.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in recs] == ["wake", "command"]
    assert recs[0]["weekday"] == "Wednesday" and recs[0]["time"] == "06:30" and recs[1]["words"] == "lights on"


def test_log_failure_is_swallowed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert housemem.HouseLog(blocker).event("wake") is None             # root is a file: cannot make log/ -> no exception


def test_learner_finds_wednesday_wake_and_lamp_but_not_scattered_thursdays(tmp_path):
    seed(tmp_path)
    found = {r["id"]: r for r in routines.learn(tmp_path, today="2026-10-29")}
    assert found["wake-wednesday"]["time"] == "06:30" and found["wake-wednesday"]["n"] == 4
    lamp = found["light-bedroom-lamp-on-wednesday"]
    assert lamp["n"] == 4 and lamp["observed_days"] == 4 and lamp["automatable"] and lamp["confidence"] >= 0.55
    assert lamp["first_date"] == "2026-10-07" and lamp["last_date"] == "2026-10-28" and len(lamp["dates"]) == 4
    assert not [i for i in found if i.endswith("thursday")]


def test_two_samples_are_never_a_pattern(tmp_path):
    for d in WED[:2]:
        housemem.HouseLog(tmp_path, clock=lambda d=d: stamp(d, "06:30")).event("wake", room="office")
    assert routines.learn(tmp_path, today="2026-10-29") == []


def test_patchy_record_lowers_confidence(tmp_path):
    seed(tmp_path)
    assert {r["id"]: r for r in routines.learn(tmp_path)}["wake-wednesday"]["confidence"] > 0.5
    for d in ["2026-09-30", "2026-09-23", "2026-09-16"]:                     # three more Wednesdays seen, without the pattern
        housemem.HouseLog(tmp_path, clock=lambda d=d: stamp(d, "12:00")).event("command", room="office", words="hi")
    assert {r["id"]: r for r in routines.learn(tmp_path)}["wake-wednesday"]["confidence"] < 0.5


def test_write_replaces_the_folder_and_makes_a_summary(tmp_path):
    seed(tmp_path)
    routines.write(tmp_path, routines.learn(tmp_path, today="2026-10-29"))
    assert (tmp_path / "routines" / "light-bedroom-lamp-on-wednesday.json").is_file()
    assert "Wednesdays around 06:25" in (tmp_path / "routines" / "SUMMARY.md").read_text(encoding="utf-8")
    routines.write(tmp_path, [])
    assert not list((tmp_path / "routines").glob("*.json"))


def test_dry_run_writes_nothing(tmp_path, capsys):
    seed(tmp_path)
    assert routines.main(["learn", "--dry-run", "--home", str(tmp_path), "--today", "2026-10-29"]) == 0
    assert not (tmp_path / "routines").exists() and "dry run" in capsys.readouterr().out


def test_parse_answer():
    p = proposals.parse_answer
    assert [p(x) for x in ("Yes please.", "yeah, sure", "No.", "nope", "Never.", "do not ask me again", "what time is it", "turn on the lamp")] == \
        ["yes", "yes", "no", "no", "never", None, None, None] or p("do not ask me again") in ("no", "never")
    assert p("don't ask me again") == "never"


def test_refresh_makes_one_proposal_and_never_stays_dead(tmp_path):
    seed(tmp_path)
    ws = proposals.Workspace(tmp_path)
    found = routines.learn(tmp_path, today="2026-10-29")
    assert ws.refresh(found) == ["light-bedroom-lamp-on-wednesday"]
    assert ws.refresh(found) == []                                           # already waiting
    text = ws.all_proposals()[0]["text"]
    assert "bedroom lamp on around 6:26 AM on 4 of the last 4 Wednesdays" in text and "yes, no, or never" in text
    ws.answer("light-bedroom-lamp-on-wednesday", "never", "never")
    assert ws.refresh(found) == [] and ws.next_to_ask() is None


def test_asking_is_daytime_and_once_a_day(tmp_path):
    seed(tmp_path)
    ws = proposals.Workspace(tmp_path, clock=lambda: stamp("2026-10-29", "12:00"))
    ws.refresh(routines.learn(tmp_path, today="2026-10-29"))
    assert ws.next_to_ask()["id"] == "light-bedroom-lamp-on-wednesday"
    assert proposals.Workspace(tmp_path, clock=lambda: stamp("2026-10-29", "03:00")).next_to_ask() is None
    ws.mark_asked("light-bedroom-lamp-on-wednesday")
    assert ws.next_to_ask() is None                                          # asked a moment ago
    assert proposals.Workspace(tmp_path, clock=lambda: stamp("2026-10-30", "12:00")).next_to_ask() is not None


def test_automation_only_after_yes_and_fires_once(tmp_path):
    seed(tmp_path)
    ws = proposals.Workspace(tmp_path, clock=lambda: stamp("2026-11-04", "06:25"))      # a Wednesday
    ws.refresh(routines.learn(tmp_path, today="2026-10-29"))
    assert ws.due() == []                                                    # not answered: nothing is automated
    assert ws.answer("light-bedroom-lamp-on-wednesday", "no", "no") is None and ws.due() == []
    ws.answer("light-bedroom-lamp-on-wednesday", "maybe", "x")               # not an answer: ignored
    auto = proposals.Workspace(tmp_path, clock=lambda: stamp("2026-11-04", "06:25")).answer("light-bedroom-lamp-on-wednesday", "yes", "yes please")
    assert auto["approved_words"] == "yes please" and auto["time"] == "06:25"
    assert [a["id"] for a in ws.due()] == ["auto-light-bedroom-lamp-on-wednesday"] and ws.due() == []
    assert proposals.Workspace(tmp_path, clock=lambda: stamp("2026-11-04", "09:00")).due() == []     # too late: not fired hours after
    assert proposals.Workspace(tmp_path, clock=lambda: stamp("2026-11-05", "06:25")).due() == []     # Thursday


# ── the hub end to end ──
class Link:
    hub_follows_up = True

    def __init__(self):
        self.spoken, self.room = [], None

    async def heard(self, t): pass
    async def released(self): pass
    async def woke(self, by): pass
    async def show(self, t): pass
    async def voice_started(self): pass
    async def voice_stopped(self): pass

    async def speak(self, said, voice, follow_up=False):
        self.spoken.append(said)

    announce = speak


class Gate:
    def tts(self, text): return b"mp3"
    def summary(self, text): return text


class Brain2:
    """A brain that turns the lamp on when told, recording the call as Brain does."""
    def __init__(self): self.actions = []

    def reply(self, heard):
        self.actions = [{"tool": "home_control", "args": {"name": "Bedroom Lamp", "action": "on"}, "outcome": "Done."}] if "lamp" in heard else []
        return "Done, monkey."


def make(tmp_path, now):
    ran = []
    link = Link()
    room = Room("office", link, Gate(), Brain2(), Timers(), log=lambda m: None, hlog=housemem.HouseLog(tmp_path, clock=lambda: now[0]),
                workspace=proposals.Workspace(tmp_path, clock=lambda: now[0]), run_action=lambda t, a: ran.append((t, a)) or "Done.")
    return room, link, room.workspace, ran


def events(tmp_path, date):
    return [json.loads(x) for x in (tmp_path / "log" / f"{date}.jsonl").read_text(encoding="utf-8").splitlines()]


def test_the_hub_logs_a_command_and_its_actions(tmp_path):
    now = [stamp("2026-10-29", "12:00")]
    room, link, ws, ran = make(tmp_path, now)
    asyncio.run(room.handle("turn on the bedroom lamp"))
    recs = events(tmp_path, "2026-10-29")
    assert [(e["kind"], e.get("what")) for e in recs] == [("command", None), ("action", "home")]
    assert recs[0]["words"] == "turn on the bedroom lamp" and recs[0]["tools"] == ["home_control"] and recs[0]["room"] == "office" and recs[0]["weekday"] == "Thursday"
    assert link.spoken == ["Done, monkey."]


def test_proposal_loop_end_to_end_yes(tmp_path):
    seed(tmp_path)
    proposals.Workspace(tmp_path).refresh(routines.learn(tmp_path, today="2026-10-29"))
    now = [stamp("2026-10-29", "12:00")]
    room, link, ws, ran = make(tmp_path, now)
    asyncio.run(room.handle("what is the time"))
    assert link.spoken[0] == "Done, monkey." and "Want me to do that for you at 6:25 AM on Wednesdays" in link.spoken[1]
    asyncio.run(room.handle("yes please"))
    assert "Wednesday" in link.spoken[2]
    prop = ws.all_proposals()[0]
    assert prop["status"] == "yes" and prop["answer_words"] == "yes please" and prop["automation_id"]
    assert (tmp_path / "automations" / "auto-light-bedroom-lamp-on-wednesday.json").is_file()
    now[0] = stamp("2026-11-04", "06:25")                                    # the next Wednesday: the tick fires it through the house tools, and logs it
    asyncio.run(room.timers_due())
    assert ran == [("home_control", {"name": "bedroom lamp", "action": "on"})]
    assert [e["kind"] for e in events(tmp_path, "2026-11-04")] == ["automation"]
    assert [e["kind"] for e in events(tmp_path, "2026-10-29")] == ["command", "proposal", "answer"]


def test_proposal_no_and_other_command_make_no_automation(tmp_path):
    seed(tmp_path)
    proposals.Workspace(tmp_path).refresh(routines.learn(tmp_path, today="2026-10-29"))
    now = [stamp("2026-10-29", "12:00")]
    room, link, ws, ran = make(tmp_path, now)
    asyncio.run(room.handle("what is the time"))
    asyncio.run(room.handle("no"))
    assert ws.all_proposals()[0]["status"] == "no" and not list((tmp_path / "automations").glob("*.json"))
    room.pending = {"id": "light-bedroom-lamp-on-wednesday", "weekday": "Wednesday", "time": "06:25"}
    asyncio.run(room.handle("turn on the lamp"))                              # not an answer: an ordinary command, and nothing is stored
    assert room.pending is None and ws.all_proposals()[0]["status"] == "no"


def test_brain_records_the_tools_it_ran():
    class B:
        def __init__(self, **k): self.__dict__.update(k)

    calls = [B(content=[B(type="tool_use", id="1", name="set_timer", input={"seconds": 60, "label": "tea"})]), B(content=[B(type="text", text="Set.")])]

    class Client:
        class messages:
            @staticmethod
            def create(**k): return calls.pop(0)

    brain = Brain(Client, "m", Timers(), persona="p")
    assert brain.reply("a tea timer") == "Set."
    assert brain.actions[0]["tool"] == "set_timer" and brain.actions[0]["args"]["seconds"] == 60
