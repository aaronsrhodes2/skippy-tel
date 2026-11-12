"""The cameras on SkippyCore (compose.cams.yml, cams/frigate_config.py, install/cams.sh): Frigate joins the stack only when core.env says
CORE_CAMS=1, it is pinned and its API is on 127.0.0.1 only, its config carries the cameras' addresses but never their password, a bad
camera name or address is refused before it reaches a URL, and the install script parses."""
import json
import subprocess
import shutil
from pathlib import Path

import pytest

from core_testlib import load

dep = load("deployer/deployer.py", "core_deployer")
fc = load("cams/frigate_config.py", "core_frigate_config")
CORE = Path(dep.__file__).resolve().parents[1]
CAMS = {"cameras": {"front_door": {"host": "192.168.1.60", "doorbell": True}, "driveway": {"host": "driveway-cam.lan"}}}


def test_frigate_joins_the_stack_only_when_core_env_says_so(tmp_path, monkeypatch):
    for k in dep.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "core.env"
    f.write_text("CORE_GPU=1\nCORE_TESLA=1\n")
    assert "core/compose.cams.yml" not in dep.Deployer(dep.load_settings(str(f)), log=lambda m: None).compose_files()
    f.write_text("CORE_GPU=1\nCORE_TESLA=1\nCORE_CAMS=1\n")
    d = dep.Deployer(dep.load_settings(str(f)), log=lambda m: None)
    assert d.compose_files()[-2:] == ["-f", "core/compose.cams.yml"]
    assert d.env("")["CORE_CAMS"] == "1"                                        # the smoke test checks the cameras only then


def test_the_compose_file_pins_frigate_and_keeps_its_api_on_localhost():
    text = (CORE / "compose.cams.yml").read_text(encoding="utf-8")
    assert "ghcr.io/blakeblackshear/frigate:0.18.2@sha256:" in text
    assert '"127.0.0.1:7355:5000"' in text and text.count("ports:") == 1         # 8971 (login), 8554 and 8555 stay unpublished
    assert "LIBVA_DRIVER_NAME=radeonsi" in text
    assert "env_file: ${CAMS_ENV:-/etc/skippy-core/cams.env}" in text                # the password alone, never the whole vault


def test_the_config_has_every_camera_and_never_the_password():
    out = fc.render(CAMS)
    assert "{FRIGATE_REOLINK_PASSWORD}" in out and "password=hunter2" not in out
    for name, host in (("front_door", "192.168.1.60"), ("driveway", "driveway-cam.lan")):
        assert f"http://{host}/flv?port=1935&app=bcs&stream=channel0_main.bcs&user=admin" in out
        assert f"rtsp://127.0.0.1:8554/{name}_sub" in out and f"  {name}:\n    ffmpeg:" in out
    assert "type: openvino" in out and "device: CPU" in out and "preset-vaapi" in out
    assert "mqtt:\n  enabled: false" in out and "track: [person, car]" in out
    assert out.count("      enabled: true") == 2                                # detection on for each camera (Frigate's default is off)
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(out)
    assert set(parsed["cameras"]) == {"front_door", "driveway"} and set(parsed["go2rtc"]["streams"]) == {
        "front_door", "front_door_sub", "driveway", "driveway_sub"}


@pytest.mark.parametrize("bad", [{"cameras": {}}, {"cameras": {"Front Door": {"host": "1.2.3.4"}}},
                                 {"cameras": {"door": {"host": "1.2.3.4/x?&user=evil"}}}, {"cameras": {"door": {}}}, []])
def test_a_bad_camera_is_refused_before_it_reaches_a_url(bad):
    with pytest.raises(ValueError):
        fc.render(bad)


def test_the_renderer_reads_cams_json(tmp_path, capsys):
    p = tmp_path / "cams.json"
    p.write_text(json.dumps(CAMS))
    assert fc.main([str(p)]) == 0 and "front_door_sub" in capsys.readouterr().out
    assert fc.main([str(tmp_path / "missing.json")]) == 1


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_the_install_script_parses():
    assert subprocess.run(["bash", "-n", str(CORE / "install" / "cams.sh")], capture_output=True).returncode == 0
