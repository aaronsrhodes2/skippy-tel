"""flash_screens.py: the PC's monitors and the room boxes' screens for a Skippy-vision moment (portal/vision.py), each best effort."""
from __future__ import annotations

import base64
import subprocess

import flash_screens as fs

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


def b64(raw):
    return base64.b64encode(raw).decode()


def test_every_monitor_and_every_box_comes_back_under_the_names_the_portal_takes():
    got = fs.capture(monitors_fn=lambda: [(0, 0, 2560, 1440), (2560, 0, 4480, 1080)], grab_fn=lambda rect: PNG + bytes([rect[2] % 256]),
                     rooms=[("theater", "192.168.0.84"), ("living-room", "192.168.0.90")], pod_fn=lambda host: PNG + host.encode())
    assert [f["name"] for f in got["files"]] == ["pc-screen-1.png", "pc-screen-2.png", "pod-theater.png", "pod-living-room.png"]
    assert got["errors"] == [] and base64.b64decode(got["files"][2]["b64"]) == PNG + b"192.168.0.84"


def test_a_monitor_that_cannot_be_read_and_a_box_that_is_off_do_not_take_the_rest_with_them():
    def grab(rect):
        if rect[0]:
            raise OSError("screen grab failed")
        return PNG

    def pod(host):
        if host == "10.0.0.2":
            raise RuntimeError("timed out")
        return PNG
    got = fs.capture(monitors_fn=lambda: [(0, 0, 10, 10), (10, 0, 20, 10)], grab_fn=grab,
                     rooms=[("office", "10.0.0.2"), ("theater", "10.0.0.3")], pod_fn=pod)
    assert [f["name"] for f in got["files"]] == ["pc-screen-1.png", "pod-theater.png"]
    assert [e.split(":")[0] for e in got["errors"]] == ["pc-screen-2", "pod-office"] and "timed out" in got["errors"][1]


def test_a_machine_with_no_way_to_list_monitors_still_answers():
    def boom():
        raise OSError("no display")
    got = fs.capture(monitors_fn=boom, rooms=[])
    assert got["files"] == [] and got["errors"][0].startswith("monitors: OSError")


def test_the_rooms_are_read_from_satellite_rooms_and_anything_odd_is_skipped():
    assert fs.rooms_from("theater=esphome://192.168.0.84, den=192.168.1.41:6053 ,Living-Room=esphome://skippy-living.local") == [
        ("theater", "192.168.0.84"), ("den", "192.168.1.41"), ("living-room", "skippy-living.local")]
    assert fs.rooms_from("../x=esphome://h, a b=h, ok=h; rm -rf, =h, noequals") == []
    assert fs.rooms_from("") == [] and len(fs.rooms_from(",".join(f"r{n}=10.0.0.{n}" for n in range(20)))) == 8


def test_the_rooms_come_from_the_environment_then_the_vault(tmp_path):
    vault = tmp_path / ".env"
    vault.write_text("A=1\nSATELLITE_ROOMS=theater=esphome://192.168.0.84\nB=2\n", encoding="utf-8")
    assert fs.configured_rooms(env={}, vault=vault) == [("theater", "192.168.0.84")]
    assert fs.configured_rooms(env={"SATELLITE_ROOMS": "den=1.2.3.4"}, vault=vault) == [("den", "1.2.3.4")]
    assert fs.configured_rooms(env={}, vault=tmp_path / "missing") == []


def test_a_box_screen_is_asked_for_through_the_satellites_own_python(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        (tmp_path / "unused").write_text("")
        out = cmd[3]                                                      # shot.py <host> <out.png>
        open(out, "wb").write(PNG)
        return subprocess.CompletedProcess(cmd, 0, b"wrote", b"")
    got = fs.pod_png("192.168.0.84", run=run, python=tmp_path / "py.exe", shot=tmp_path / "shot.py")
    assert got == PNG and calls[0][0][:3] == [str(tmp_path / "py.exe"), str(tmp_path / "shot.py"), "192.168.0.84"]
    assert calls[0][1]["timeout"] == fs.POD_TIMEOUT_S                      # a box that never answers can't hold the flash up


def test_a_box_that_fails_says_why_in_one_line(tmp_path):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, b"", b"Traceback...\nTimeoutError: the box did not answer")
    try:
        fs.pod_png("10.0.0.9", run=run, python=tmp_path / "p", shot=tmp_path / "s")
    except RuntimeError as exc:
        assert "did not answer" in str(exc)
    else:
        raise AssertionError("a failed shot.py must raise")


# ── the bridge's door (a picture of the desktop: the home token or nothing) ──
def test_the_door_needs_the_home_token_and_answers_with_what_capture_found(tmp_path, monkeypatch):
    from bridge_testkit import make_door, make_shop
    door, server = make_door(make_shop(tmp_path, monkeypatch), monkeypatch)
    monkeypatch.setattr(fs, "capture", lambda: {"files": [{"name": "pc-screen-1.png", "b64": b64(PNG)}], "errors": ["pod-office: timed out"]})
    try:
        assert door("POST", "/flash/screens", {}, token=None)[0] == 401
        assert door("POST", "/flash/screens", {}, token="not-the-token")[0] == 401
        status, body = door("POST", "/flash/screens", {})
        assert status == 200 and body["files"][0]["name"] == "pc-screen-1.png" and body["errors"] == ["pod-office: timed out"]
        assert door("GET", "/flash/screens")[0] in (404, 405)                           # only a POST takes a picture
    finally:
        server.shutdown()
