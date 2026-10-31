"""The car as a guard (core/car-fleet/fleet.py): Sentry's state read from the car's stream in whatever shape the car sends it, an event only
for AWARE and PANIC (never for the broker's retained copy), the alarm answered by flashing the lights and locking (never the horn), at most
once a minute; his tokens refreshed before they run out and rewritten root-only; the streaming config carries our CA and only Sentry."""
import json
import sys

from core_testlib import CORE, load

sys.path.insert(0, str(CORE / "car-fleet"))
fleet = load("car-fleet/fleet.py", "core_car_fleet")


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_the_sentry_value_is_read_in_every_shape_the_car_sends():
    assert fleet.sentry_state(b'"SentryModeStateAware"') == "aware"
    assert fleet.sentry_state(b'"Panic"') == "panic"
    assert fleet.sentry_state(b'{"sentryModeStateValue": "SentryModeStateArmed"}') == "armed"
    assert fleet.sentry_state(b"SentryModeStateQuiet") == "quiet"                 # not JSON at all
    assert fleet.sentry_state(b'"SentryModeStateUnknown"') == "" and fleet.sentry_state(b"42") == ""


def test_only_aware_and_panic_are_events_and_a_retained_copy_is_old_news():
    clock, answers = Clock(), []
    s = fleet.Sentry(react=lambda: answers.append(1) or "lights flashed, doors locked", clock=clock, background=False)
    assert s.observe("panic", retained=True) is None and s.state == "panic" and not answers   # a reconnect, not a new alarm
    assert s.observe("armed") is None and s.events == fleet.deque(maxlen=fleet.EVENTS)
    aware = s.observe("aware")
    assert aware["state"] == "aware" and "came close" in aware["said"] and not answers
    assert s.observe("aware") is None                                                          # the same state again: nothing new
    clock.t += 5
    panic = s.observe("panic")
    assert answers == [1] and "touched" in panic["said"] and panic["said"].endswith("lights flashed, doors locked")
    assert [e["state"] for e in s.view()["events"]] == ["aware", "panic"]


def test_the_alarm_is_answered_at_most_once_a_minute():
    clock, answers = Clock(), []
    s = fleet.Sentry(react=lambda: answers.append(1) or "done", clock=clock, background=False)
    for step in (0, 10, 10, 50):                    # panic, armed, panic again 20 s after the first, then 70 s after
        clock.t += step
        s.observe("panic")
        s.observe("armed")
    assert answers == [1, 1] and len(s.events) == 4


def test_a_failed_answer_is_said_and_the_event_stands():
    def boom():
        raise RuntimeError("car asleep")
    s = fleet.Sentry(react=boom, clock=Clock(), background=False)
    assert "the answer failed: car asleep" in s.observe("panic")["said"]


def test_the_guard_flashes_and_locks_and_never_honks():
    calls = []
    f = fleet.Fleet(tokens=type("T", (), {"get": lambda self: "tok"})(), vin="5YJ3TEST000000000",
                    call=lambda m, p, b, t: calls.append(p) or (200, {"response": {"result": True}}))
    assert f.guard() == "lights flashed, doors locked"
    assert [p.rsplit("/", 1)[1] for p in calls] == ["flash_lights", "door_lock"]
    assert "honk" not in (CORE / "car-fleet" / "fleet.py").read_text(encoding="utf-8").replace("no honk", "").replace("no horn", "")


def test_a_sleeping_car_is_woken_once_for_arming():
    answers = [(408, {"error": "vehicle unavailable"}), (200, {}), (408, {}), (200, {"response": {"result": True}})]
    paths = []
    f = fleet.Fleet(tokens=type("T", (), {"get": lambda self: "tok"})(), vin="5YJ3TEST000000000",
                    call=lambda m, p, b, t: paths.append(p) or answers.pop(0), sleep=lambda s: None)
    assert f.command("set_sentry_mode", {"on": True}, wake=True) == ""
    assert sum(p.endswith("/wake_up") for p in paths) == 1


def test_a_refusal_is_said_in_the_cars_words():
    f = fleet.Fleet(tokens=type("T", (), {"get": lambda self: "tok"})(), vin="5YJ3TEST000000000",
                    call=lambda m, p, b, t: (200, {"response": {"result": False, "reason": "vehicle_not_paired"}}))
    assert f.command("flash_lights") == "vehicle_not_paired"


def test_tokens_are_refreshed_before_they_run_out_and_kept_root_only(tmp_path):
    path = tmp_path / "tokens.json"
    clock = Clock()
    path.write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "client_id": "cid", "expires_at": clock.t + 3600}))
    sent = []
    t = fleet.Tokens(path, post=lambda url, form: sent.append(form) or (200, {"access_token": "new", "refresh_token": "r2", "expires_in": 28800}),
                     clock=clock)
    assert t.get() == "old" and not sent
    clock.t += 3200                                                   # under 10 minutes left
    assert t.get() == "new" and sent[0] == {"grant_type": "refresh_token", "client_id": "cid", "refresh_token": "r1"}
    kept = json.loads(path.read_text())
    assert kept["refresh_token"] == "r2" and kept["client_id"] == "cid" and kept["expires_at"] == clock.t + 28800
    if sys.platform != "win32":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_a_refused_refresh_says_sign_in_again(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "client_id": "cid", "expires_at": 0}))
    t = fleet.Tokens(path, post=lambda url, form: (401, {"error": "login_required"}))
    try:
        t.get()
        raise AssertionError("expected a refusal")
    except RuntimeError as exc:
        assert "sign in again" in str(exc)
    assert "no tokens" in _problem(fleet.Tokens(tmp_path / "none.json"))


def _problem(tokens):
    try:
        tokens.get()
    except RuntimeError as exc:
        return str(exc)
    return ""


def test_the_streaming_config_carries_our_ca_and_only_sentry(tmp_path, monkeypatch):
    (tmp_path / "ca.pem").write_text("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(fleet, "TELEMETRY_CA", tmp_path / "ca.pem")
    monkeypatch.setattr(fleet, "TELEMETRY_HOST", "skippy-core.example.ts.net")
    cfg = fleet.Fleet(tokens=None, vin="V").telemetry_config(now=0)
    assert cfg["hostname"] == "skippy-core.example.ts.net" and cfg["port"] == 10000 and "BEGIN CERTIFICATE" in cfg["ca"]
    assert list(cfg["fields"]) == ["SentryMode"] and cfg["exp"] == fleet.CONFIG_DAYS * 86400


def test_the_container_keeps_its_doors_local_and_the_server_pinned():
    text = (CORE / "compose.tesla.yml").read_text(encoding="utf-8")
    tele = text.split("  fleet-telemetry:", 1)[1].split("\n\n", 1)[0]
    assert '"127.0.0.1:7443:7443"' in tele and "cap_drop" in tele and "/etc/skippy-core/tesla-fleet/telemetry:/certs:ro" in tele
    guard = text.split("  car-fleet:", 1)[1]
    assert '"127.0.0.1:7353:7353"' in guard and "required: false" in guard
    conf = json.loads((CORE / "car-fleet" / "telemetry.json").read_text(encoding="utf-8"))
    assert conf["port"] == 7443 and conf["records"]["V"] == ["mqtt"] and conf["mqtt"]["broker"] == "mosquitto:1883"
    assert "ARG VEHICLE_COMMAND=v0.4.1" in (CORE / "car-fleet" / "Dockerfile").read_text(encoding="utf-8")
