"""The house's side of talking to the Factorio foreman by voice: while the game's door answers, what is heard goes to the game; while it
does not, nothing changes. A stand-in door answers here (the real one is AI-factorio's copilot/voice_door.py); no game, no network."""
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import factorio_door
from factorio_door import NO_ANSWER, STILL_WORKING, Door, GameBrain, whose, wrap


class Brain:
    def __init__(self):
        self.heard, self.timing, self.warmed = [], {"total_s": 1.0}, 0

    def reply(self, heard):
        self.heard.append(heard)
        return "house: " + heard

    def warm(self):
        self.warmed += 1
        return 0.5


class FakeDoor:
    """The game's door, in-process: scripted answers to /said."""

    def __init__(self, up=True, answers=None, hear_fails=False, said_fails=False):
        self.up, self.answers, self.hear_fails, self.said_fails = up, list(answers if answers is not None else [["Iron first, then copper."]]), hear_fails, said_fails
        self.calls, self.said_n = [], 7

    def __call__(self, method, url, body=None, timeout=5.0):
        self.calls.append((method, url.split("47862")[-1], body))
        if not self.up:
            raise urllib.error.URLError("refused")
        if url.endswith("/health"):
            return {"ok": True, "service": "skippy-factorio", "heard": 0, "said": self.said_n}
        if method == "POST":
            if self.hear_fails:
                raise urllib.error.URLError("reset")
            return {"ok": True, "id": 1, "after": self.said_n}
        if self.said_fails:
            raise urllib.error.URLError("reset")
        lines = self.answers.pop(0) if self.answers else []
        out = {"lines": [{"id": self.said_n + i + 1, "text": t, "voice": "skippy"} for i, t in enumerate(lines)], "next": self.said_n + len(lines)}
        self.said_n += len(lines)
        return out


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def game_brain(fake, clock=None, wait_s=60.0, who="captain"):
    brain, logged = Brain(), []
    door = Door(http=fake, clock=clock or Clock(), wait_s=wait_s)
    return GameBrain(brain, door, log=logged.append, who=who), brain, logged


def test_whose_words_they_are():
    assert whose("build more furnaces") == ("game", "build more furnaces")
    assert whose("take over my character") == ("game", "take over my character")
    assert whose("turn the lights off") == ("house", "turn the lights off")
    assert whose("set a timer for ten minutes") == ("house", "set a timer for ten minutes")
    assert whose("play some music") == ("house", "play some music")
    assert whose("House, what is the weather") == ("house", "what is the weather")
    assert whose("house: build a list") == ("house", "build a list")
    assert whose("Foreman, the lights at the smelters are out") == ("game", "the lights at the smelters are out")     # the prefix wins over a house word
    assert whose("the game, what is wrong") == ("game", "what is wrong")
    assert whose("foreman") == ("game", "foreman")                                  # the prefix alone is not swallowed


def test_while_a_game_is_on_what_is_heard_goes_to_the_foreman_and_his_answer_is_spoken():
    fake = FakeDoor()
    gb, brain, logged = game_brain(fake)
    assert gb.reply("what should we do next?") == "Iron first, then copper."
    assert brain.heard == []                                                         # the house brain was not asked
    assert fake.calls[1] == ("POST", "/hear", {"text": "what should we do next?", "who": "captain"})
    assert fake.calls[2][1].startswith("/said?after=7&wait=")
    assert logged == ["(to the Factorio foreman: what should we do next?)"]


def test_house_things_stay_with_the_house_while_a_game_is_on():
    fake = FakeDoor()
    gb, brain, _ = game_brain(fake)
    assert gb.reply("turn the lights off") == "house: turn the lights off"
    assert gb.reply("house, how is the factory") == "house: how is the factory"      # said to the house, without the word
    assert fake.calls == []                                                          # the door was not even looked at
    assert gb.reply("foreman, turn the lights on at the smelters") == "Iron first, then copper."


def test_with_no_game_on_nothing_changes():
    fake = FakeDoor(up=False)
    clock = Clock()
    gb, brain, logged = game_brain(fake, clock)
    assert gb.reply("what should we do next?") == "house: what should we do next?"
    assert gb.reply("build more furnaces") == "house: build more furnaces"
    assert len(fake.calls) == 1 and logged == []                                     # one look, trusted for a few seconds
    clock.t += 6
    gb.reply("and again")
    assert len(fake.calls) == 2
    fake.up = True                                                                   # the game starts
    clock.t += 6
    assert gb.reply("what should we do next?") == "Iron first, then copper."


def test_several_lines_are_one_answer_and_an_empty_wait_is_waited_again():
    fake = FakeDoor(answers=[[], ["Placed four furnaces.", "Copper is next."]])
    gb, _, _ = game_brain(fake)
    assert gb.reply("build furnaces") == "Placed four furnaces. Copper is next."
    assert [c[1].split("&")[0] for c in fake.calls[2:]] == ["/said?after=7", "/said?after=7"]


def test_a_foreman_who_takes_too_long_is_said_to_be_working():
    fake = FakeDoor(answers=[])
    clock = Clock()
    real = fake.__call__

    def slow(method, url, body=None, timeout=5.0):
        if "/said" in url:
            clock.t += 20
        return real(method, url, body, timeout)
    gb = GameBrain(Brain(), Door(http=slow, clock=clock, wait_s=45.0))
    assert gb.reply("build a mall") == STILL_WORKING
    assert sum(1 for c in fake.calls if "/said" in c[1]) == 3


def test_a_door_that_shuts_mid_question_never_leaves_him_without_an_answer():
    gb, brain, _ = game_brain(FakeDoor(hear_fails=True))
    assert gb.reply("what next?") == "house: what next?"                             # could not be handed over: the house answers
    gb, brain, _ = game_brain(FakeDoor(said_fails=True))
    assert gb.reply("what next?") == NO_ANSWER and brain.heard == []                 # handed over, then silence: say so, do not ask twice


def test_everything_else_about_the_brain_is_the_brains_own():
    gb, brain, _ = game_brain(FakeDoor(up=False))
    gb.room = "theater"
    gb.pre_route = len
    assert brain.room == "theater" and brain.pre_route is len and gb.room == "theater"
    assert gb.timing == {"total_s": 1.0} and gb.warm() == 0.5 and brain.warmed == 1
    assert "_door" not in vars(brain)


def test_wrap_reads_the_settings():
    brain = Brain()
    assert wrap(brain, {"SATELLITE_FACTORIO_DOOR": "off"}) is brain
    gb = wrap(brain, {})
    assert isinstance(gb, GameBrain) and gb._door.url == "http://127.0.0.1:47862" and gb._door.wait_s == 60.0
    gb = wrap(brain, {"SATELLITE_FACTORIO_DOOR": "http://127.0.0.1:5000/", "SATELLITE_FACTORIO_WAIT": "90"})
    assert gb._door.url == "http://127.0.0.1:5000" and gb._door.wait_s == 90.0
    assert wrap(brain, {"SATELLITE_FACTORIO_WAIT": "soon"})._door.wait_s == 60.0


def test_against_a_real_door_over_http():
    said = {"n": 3}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self._send({"ok": True, "service": "skippy-factorio"})
            self._send({"lines": [{"id": 4, "text": "Four furnaces fit west of the ore.", "voice": "skippy"}], "next": 4})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
            said["heard"] = body
            self._send({"ok": True, "id": 1, "after": said["n"]})

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        gb = GameBrain(Brain(), Door("http://127.0.0.1:%d" % server.server_address[1]))
        assert gb.reply("where do the furnaces go?") == "Four furnaces fit west of the ore."
        assert said["heard"] == {"text": "where do the furnaces go?"}
    finally:
        server.shutdown()
        server.server_close()
    shut = Door("http://127.0.0.1:%d" % server.server_address[1])
    assert shut.is_open() is False and factorio_door.DOOR.endswith(":47862")
