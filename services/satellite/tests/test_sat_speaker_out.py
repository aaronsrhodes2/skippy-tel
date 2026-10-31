"""His voice also out of the PC's own outputs (HDMI, Bluetooth stereo): the settings, and that a missing device is skipped quietly."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speaker_out as S  # noqa: E402


def test_the_settings_name_the_outputs_per_room_and_default_to_the_box_alone():
    pod, outs = S.parse_outputs({}, "office")
    assert pod is True and outs == []
    pod, outs = S.parse_outputs({"SATELLITE_OUT": "office=pod+hdmi+bt;kitchen=hdmi", "SATELLITE_OUT_BT": "Enclave"}, "office")
    assert pod is True and [o.kind for o in outs] == ["hdmi", "bt"] and outs[1].part == "Enclave" and outs[1].delay_s == 0.4
    pod, outs = S.parse_outputs({"SATELLITE_OUT": "office=pod+hdmi+bt;kitchen=hdmi"}, "kitchen")
    assert pod is False and [o.kind for o in outs] == ["hdmi"]


def test_an_output_that_is_not_there_is_skipped_and_logged():
    said = []
    out = S.PcOut("bt", "No Such Speaker 12345", log=said.append)
    assert asyncio.run(out.play(b"not really an mp3")) is False and "no sound device" in said[0]
