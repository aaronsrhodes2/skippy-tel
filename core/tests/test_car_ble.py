"""The charging door (core/car-ble/ble.py): it turns start / stop / limit into tesla-control's commands and nothing else, wakes a sleeping
car once, says the car's own reason in plain words (already charging is not a failure), and its container keeps the key read-only and
its door on 127.0.0.1."""
import subprocess
import sys

import pytest

from core_testlib import CORE, load

sys.path.insert(0, str(CORE / "car-ble"))
ble = load("car-ble/ble.py", "core_car_ble")


class Runner:
    """Plays tesla-control: each call takes the next (returncode, output)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        code, out = self.answers.pop(0)
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")


def test_actions_become_only_the_three_charging_commands():
    assert ble.command("start") == ["charging-start"]
    assert ble.command("stop") == ["charging-stop"]
    assert ble.command("limit", "85") == ["charging-set-limit", "85"]
    for bad in (("unlock", None), ("limit", 49), ("limit", 101), ("limit", "lots"), ("", None)):
        with pytest.raises(ValueError):
            ble.command(*bad)


def test_a_command_is_signed_with_the_key_for_this_car(monkeypatch):
    monkeypatch.setattr(ble, "VIN", "5YJTEST")
    r = Runner((0, ""))
    assert ble.charge("start", runner=r) == {"ok": True, "said": "charging started"}
    assert r.calls == [["tesla-control", "-ble", "-vin", "5YJTEST", "-key-file", ble.KEY, "charging-start"]]


def test_a_sleeping_car_is_woken_once_and_asked_again():
    r = Runner((1, "Error: context deadline exceeded"), (0, ""), (0, ""))
    assert ble.charge("stop", runner=r)["ok"]
    assert [c[-1] for c in r.calls] == ["charging-stop", "wake", "charging-stop"]


def test_the_cars_reason_comes_back_in_plain_words():
    already = ble.charge("start", runner=Runner((1, "Error: car could not execute command: is_charging")))
    assert already == {"ok": True, "said": "nothing to do: it was already charging"}
    unplugged = ble.charge("start", runner=Runner((1, "Error: car could not execute command: disconnected")))
    assert unplugged == {"ok": False, "error": "the car isn't plugged in"}
    unpaired = Runner((1, "Error: vehicle rejected request: your public key has not been paired with the vehicle"))
    assert "key card" in ble.charge("stop", runner=unpaired)["error"]
    assert len(unpaired.calls) == 1                                           # a refusal is not sleep: no wake, no retry


def test_health_names_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ble, "READY", ble.Readiness("car-ble"))
    monkeypatch.setattr(ble, "HCI", tmp_path / "hci0")
    monkeypatch.setattr(ble, "KEY", str(tmp_path / "private.pem"))
    monkeypatch.setattr(ble, "VIN", "")
    got = ble.health()
    assert not got["ready"] and "Bluetooth radio" in got["detail"] and "no key" in got["detail"] and "TESLA_VIN" in got["detail"]
    (tmp_path / "hci0").mkdir()
    (tmp_path / "private.pem").write_text("k")
    monkeypatch.setattr(ble, "VIN", "5YJTEST")
    assert ble.health()["ready"]


def test_the_container_owns_the_radio_but_keeps_the_key_read_only_and_the_door_local():
    text = (CORE / "compose.tesla.yml").read_text(encoding="utf-8")
    block = text.split("  car-ble:", 1)[1]
    assert "network_mode: host" in block and "NET_ADMIN" in block and "NET_RAW" in block
    assert "/etc/skippy-core/tesla-ble/private.pem:/keys/private.pem:ro" in block
    assert "privileged" not in block
    assert ble.HOST == "127.0.0.1"                                            # host networking: the tailnet only through tailscale serve
    docker = (CORE / "car-ble" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG VEHICLE_COMMAND=v0.4.1" in docker                             # Tesla's tool, pinned
