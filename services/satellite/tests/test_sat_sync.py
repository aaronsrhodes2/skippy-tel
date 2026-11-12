"""Lining Skippy's voice up between the pod and the PC's outputs by ear (sync.py): the PC's microphone hears every copy, the hub corrects."""
import numpy as np

import router as rt
import sync


def voice(seconds=2.0, seed=1):
    """Something voice-like: noise bursts with gaps, 48 kHz stereo."""
    rng = np.random.default_rng(seed)
    n = int(rt.RATE * seconds)
    x = rng.standard_normal(n).astype("float32") * 0.3
    env = (np.sin(np.linspace(0, 25, n)) > 0).astype("float32")
    return np.stack([x * env, x * env], axis=1)


class FakeSinks:
    def __init__(self, delay):
        self.sinks = {"hdmi": type("S", (), {"delay_s": delay})()}


def room_hearing(clip, copies, t_start=100.0, seconds=8.0, noise=0.02, seed=2):
    """A MicRing that heard `clip` at each (clock, loudness) in `copies`, over room noise."""
    rng = np.random.default_rng(seed)
    mono = clip.mean(axis=1)
    room = rng.standard_normal(int(rt.RATE * seconds)).astype("float32") * noise
    for at, gain in copies:
        i = int((at - t_start) * rt.RATE)
        room[i:i + len(mono)] += mono[: len(room) - i] * gain
    ring = sync.MicRing("CMTECK", log=lambda *a: None)
    step = int(rt.RATE * 0.05)
    for j in range(0, len(room) - step + 1, step):
        ring.add(t_start + j / rt.RATE, room[j:j + step])
    return ring


def calibrator(router, ring, tmp_path, extra=0.1):
    logs = []
    c = sync.Calibrator(router, ["hdmi"], ring, log=logs.append, path=tmp_path / "audio-sync.json", clock=lambda: 1e9)
    c.extra["hdmi"] = extra
    return c, logs


def test_the_projector_behind_the_pod_gets_less_delay(tmp_path):
    clip = voice()
    push, queued, lat = 101.0, 0.5, 0.1
    hdmi_at = push + queued + lat + 0.1                        # where the router expected it (extra 0.1)
    pod_at = hdmi_at - 0.35                                    # the Captain: the pod ~0.35 s ahead
    router = FakeSinks(0.5)
    c, logs = calibrator(router, room_hearing(clip, [(pod_at, 1.0), (hdmi_at, 0.4)]), tmp_path)
    got = c.measure(clip, {"hdmi": (push, queued, lat)})
    assert abs(got["outputs"]["hdmi"]["offset"] - 0.35) < 0.003
    assert abs(router.sinks["hdmi"].delay_s - (0.5 - sync.GAIN * 0.35)) < 0.003
    assert "behind the pod" in logs[-1]
    assert sync.saved_delays(tmp_path / "audio-sync.json")["hdmi"] == got["outputs"]["hdmi"]["delay"]


def test_the_quieter_far_copy_is_still_told_apart_from_the_pod_by_when_it_was_due(tmp_path):
    """The pod is loudest at the mic or not: the PC output is the copy that lands where the router said it would."""
    clip = voice(seed=5)
    push = 101.0
    hdmi_at = push + 0.5 + 0.1 + 0.1
    router = FakeSinks(0.5)
    c, _ = calibrator(router, room_hearing(clip, [(hdmi_at - 0.4, 0.3), (hdmi_at, 1.0)]), tmp_path)
    got = c.measure(clip, {"hdmi": (push, 0.5, 0.1)})
    assert abs(got["outputs"]["hdmi"]["offset"] - 0.4) < 0.003


def test_it_settles_under_50_ms_within_a_few_replies(tmp_path):
    """The pod lands at a fixed point; each correction moves where the next clip's projector copy lands."""
    router = FakeSinks(0.5)
    proj = 0.17                                                # the projector's own latency, unknown to the hub
    offsets = []
    for n in range(5):
        clip = voice(seed=10 + n)
        push = 101.0
        pod_at = push + 0.35
        hdmi_at = push + router.sinks["hdmi"].delay_s + 0.1 + proj
        c, _ = calibrator(router, room_hearing(clip, [(pod_at, 1.0), (hdmi_at, 0.5)], seed=20 + n), tmp_path, extra=0.1 if n == 0 else c.extra["hdmi"])
        offsets.append(c.measure(clip, {"hdmi": (push, router.sinks["hdmi"].delay_s, 0.1)})["outputs"]["hdmi"]["offset"])
    assert abs(offsets[0]) > 0.3
    assert abs(offsets[3]) < 0.05 and abs(offsets[4]) < 0.05


def test_only_one_copy_heard_changes_nothing(tmp_path):
    clip = voice()
    router = FakeSinks(0.5)
    c, logs = calibrator(router, room_hearing(clip, [(101.7, 1.0)]), tmp_path)
    assert c.measure(clip, {"hdmi": (101.0, 0.5, 0.1)}) is None
    assert router.sinks["hdmi"].delay_s == 0.5 and "not the pod" in logs[-1]


def test_a_wild_offset_is_not_trusted(tmp_path):
    clip = voice()
    router = FakeSinks(0.5)
    hdmi_at = 101.7
    c, logs = calibrator(router, room_hearing(clip, [(hdmi_at - 1.9, 1.0), (hdmi_at, 0.5)], t_start=99.0, seconds=9.0), tmp_path)
    c.measure(clip, {"hdmi": (101.0, 0.5, 0.1)})
    assert router.sinks["hdmi"].delay_s == 0.5 and "came 1.9 s late" in logs[-1] and "unchanged" in logs[-1]


def test_the_microphone_not_covering_the_clip_is_no_measurement(tmp_path):
    clip = voice()
    router = FakeSinks(0.5)
    c, logs = calibrator(router, room_hearing(clip, [], t_start=10.0, seconds=2.0), tmp_path)
    assert c.measure(clip, {"hdmi": (101.0, 0.5, 0.1)}) is None and "did not cover" in logs[-1]


def test_only_outputs_sharing_a_room_with_its_pod_are_lined_up():
    assert sync.pod_rooms_kinds({"SATELLITE_OUT": "office=pod+hdmi;den=bt"}) == {"hdmi"}
    assert sync.for_router(type("R", (), {"sinks": {"hdmi": 1}})(), {"SATELLITE_OUT": "office=pod+hdmi", "SATELLITE_SYNC_MIC": "0"}) is None


def test_each_voice_clip_starts_exactly_its_delay_behind_however_the_queue_drifted():
    """The device's clock against the mixer's let the queue wander from empty to delay + SLACK_S; a voice clip now gets exactly its delay."""
    from test_sat_router import World, tone
    w = World(present=("HDMI",))
    r = rt.Router([("hdmi", "HDMI", 0.3)], find=w.find, stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None, autostart=False)
    r.mix_once()
    for _ in range(12):                                        # the mixer ran ahead of the device: 0.6 s of silence queued
        r.mix_once()
    sink = r.sinks["hdmi"]
    assert sink.buffered / rt.RATE > 0.3 and sink.idle
    p = r.play(rt.ClipSource(tone(0.2)), targets={"hdmi"})
    r.mix_once()
    assert abs(p.marks["hdmi"][1] - 0.3) < 1e-6
    out = w.streams[len("HDMI")].pull(int(rt.RATE * 0.3) + rt.BLOCK)
    assert out[: int(rt.RATE * 0.3)].max() == 0 and out[int(rt.RATE * 0.3):].max() > 0


def test_a_clip_still_playing_is_never_cut_to_realign():
    from test_sat_router import World, tone
    w = World(present=("HDMI",))
    r = rt.Router([("hdmi", "HDMI", 0.1)], find=w.find, stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None, autostart=False)
    r.mix_once()
    r.play(rt.ClipSource(tone(0.5)), targets={"hdmi"})
    r.mix_once()
    r.mix_once()
    sink = r.sinks["hdmi"]
    before = sink.buffered
    assert not sink.idle and sink.realign() == before / rt.RATE and sink.buffered == before


def test_the_routers_device_scan_opens_the_microphone_and_reopens_it_after_a_reread():
    """Re-reading PortAudio's device list closes every stream: the microphone opened beside it (14:41, 'Error querying device 15') died."""
    from test_sat_router import World
    w = World(present=("HDMI",))
    r = rt.Router([("hdmi", "HDMI", 0.0)], find=w.find, stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None, autostart=False)
    opened = []
    mic = sync.MicRing("CMTECK", log=lambda *a: None, open_stream=lambda part, cb: (opened.append(part) or object(), "Microphone (CMTECK)"))
    r.calibrator = sync.Calibrator(r, ["hdmi"], mic, log=lambda *a: None)
    r.mix_once()
    assert w.reinits == 1 and opened == ["CMTECK"] and mic.stream is not None
    r.scan()
    assert opened == ["CMTECK"]                                 # already open, nothing missing: left alone


def test_a_pod_copy_8_s_late_is_found_and_said_not_chased(tmp_path):
    """2026-10-31: the Captain heard the two copies start ~8 s apart; the +-2 s search never saw it."""
    clip = voice(seed=7)
    push = 101.0
    hdmi_at = push + 0.5 + 0.1 + 0.1
    router = FakeSinks(0.5)
    c, logs = calibrator(router, room_hearing(clip, [(hdmi_at, 0.6), (hdmi_at + 8.0, 1.0)], t_start=95.0, seconds=20.0), tmp_path)
    got = c.measure(clip, {"hdmi": (push, 0.5, 0.1)})
    assert abs(got["outputs"]["hdmi"]["offset"] + 8.0) < 0.01
    assert router.sinks["hdmi"].delay_s == 0.5 and "the pod's copy came 8.0 s late" in logs[-1]


def test_the_box_fetching_its_clip_is_logged_with_how_late():
    import asyncio
    import media as media_mod

    logs = []
    m = media_mod.MediaServer()
    m.log = logs.append
    url = m.put_clip(("mp3", b"ID3..."))
    clip = url.rsplit("/", 1)[1]
    req = type("Q", (), {"match_info": {"secret": m.secret, "clip": clip}})()
    asyncio.run(m._clip(req))
    asyncio.run(m._clip(req))                                  # a second fetch (a retry) is not logged again
    assert len(logs) == 1 and "fetched Skippy's clip" in logs[0]


def test_a_reflection_beside_the_projector_copy_is_not_taken_for_a_late_pod(tmp_path):
    clip = voice(seed=8)
    push = 101.0
    hdmi_at = push + 0.7
    router = FakeSinks(0.5)
    ring = room_hearing(clip, [(hdmi_at, 1.0), (hdmi_at + 0.012, 0.8), (hdmi_at + 5.0, 0.6)], t_start=95.0, seconds=20.0)
    c, logs = calibrator(router, ring, tmp_path)
    got = c.measure(clip, {"hdmi": (push, 0.5, 0.1)})
    assert abs(got["outputs"]["hdmi"]["offset"] + 5.0) < 0.01 and "the pod's copy came 5.0 s late" in logs[-1]


def test_saved_align_reads_each_outputs_wait(tmp_path):
    import json
    p = tmp_path / "audio-sync.json"
    p.write_text(json.dumps({"align": {"latency": {"hdmi": 0.150, "enclave": 0.297}}}))
    assert sync.saved_align(path=p) == {"hdmi": 0.147, "enclave": 0.0}
    assert sync.saved_align({"hdmi", "enclave", "pod"}, path=p) == {"hdmi": 0.147, "enclave": 0.0}
    assert sync.saved_align({"hdmi"}, path=p) == {}                       # alone in its room: waits for nothing
    assert sync.saved_align(path=tmp_path / "none.json") == {}


def test_pod_rooms_kinds_reads_preferences():
    assert sync.pod_rooms_kinds({"SATELLITE_OUT": "office=pod+enclave>hdmi"}) == {"enclave", "hdmi"}


def test_the_mic_measures_only_what_it_can_hear():
    """The USB mic is in the theater; the pod and the Enclave are upstairs (2026-11-01): an output it can't hear is never measured (it would
    be taken for off and the pod would speak instead)."""
    import sync as S
    R = type("R", (), {"sinks": {"enclave": 1, "hdmi": 1}})()
    env = {"SATELLITE_OUT": "living=pod+enclave", "SATELLITE_SYNC_HEARS": "hdmi"}
    assert S.for_router(R, env) is None
    env["SATELLITE_OUT"] = "living=pod+enclave;theater=pod+hdmi"
    assert S.pod_rooms_kinds(env) == {"enclave", "hdmi"}
