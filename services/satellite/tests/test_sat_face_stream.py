"""The streamed face: the wire format and its validation (the same checks the box's skippy_stream does), loss tolerance, the renderer, and the link's wiring."""
import random
import struct
import sys
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import face_render as fr  # noqa: E402
import face_stream as fs  # noqa: E402


def frame(seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 65536, (fs.H, fs.W), dtype=np.uint16)


def apply(datagrams, fb):
    res = {"tiles": 0, "bad": 0}
    for d in datagrams:
        r = fs.decode_datagram(d, fb)
        res["tiles"] += r["tiles"]
        res["bad"] += r["bad"]
    return res


def test_keyframe_roundtrip_and_datagram_size():
    enc, fb = fs.Encoder(), np.zeros((fs.H, fs.W), ">u2")
    f = frame(1)
    dg = enc.encode(f)
    assert all(len(d) <= fs.MAX_DATAGRAM for d in dg)
    assert dg[-1][2] & 1 and not any(d[2] & 1 for d in dg[:-1])
    seqs = [struct.unpack_from('<H', d, 5)[0] for d in dg]
    assert seqs == list(range(seqs[0], seqs[0] + len(dg)))              # every datagram has the next sequence number: the box counts gaps          # only the last datagram of a frame says so
    r = apply(dg, fb)
    assert r["bad"] == 0 and r["tiles"] == fs.ROWS * fs.COLS
    assert np.array_equal(fb.astype("u2"), f)


def test_only_changed_tiles_are_sent_and_flat_tiles_are_tiny():
    enc, fb = fs.Encoder(), np.zeros((fs.H, fs.W), ">u2")
    flat = np.zeros((fs.H, fs.W), np.uint16)
    apply(enc.encode(flat), fb)
    flat2 = flat.copy()
    flat2[100:110, 200:230] = 0xF800                                   # a small red patch: touches tiles around (12..13, 6..14)
    dg = enc.encode(flat2)
    r = apply(dg, fb)
    assert np.array_equal(fb.astype("u2"), flat2)
    assert r["tiles"] <= 4 + fs.REFRESH_TILES
    assert sum(len(d) for d in dg) < 1500


def test_incompressible_tiles_go_raw_flat_ones_are_solid_and_edges_are_runs():
    enc = fs.Encoder()
    dg = enc.encode(frame(2))
    first = struct.unpack_from("<HHBBH", dg[0], 7)
    assert first[4] & 0x8000 and first[4] & 0x7FFF == fs.TW * fs.TH * 2
    img = np.zeros((fs.H, fs.W), np.uint16)
    img[:, 16:32] = 0xF81F                                                   # a flat colour: solid
    img[32:48, 0:16] = np.where(np.arange(16)[None, :] < 7, 0x07E0, 0x001F)  # a hard vertical edge: a few runs
    enc2, fb = fs.Encoder(), np.zeros((fs.H, fs.W), ">u2")
    out = enc2.encode(img)
    kinds = set()
    for d in out:
        p = 7
        while p + 8 <= len(d):
            _, _, _, _, n = struct.unpack_from("<HHBBH", d, p)
            kinds.add("raw" if n & 0x8000 else ("solid" if n & 0x4000 else "runs"))
            p += 8 + (n & 0x7FFF if n & 0x8000 else (n & 0x3FFF if n & 0x4000 else n & 0x1FFF))
    assert kinds == {"solid", "runs"}
    assert apply(out, fb)["bad"] == 0 and np.array_equal(fb.astype("u2"), img)
    assert sum(len(d) for d in out) < 30000                                  # 900 tiles, nearly all solid


def test_lost_datagram_heals_through_the_rolling_refresh():
    enc, fb = fs.Encoder(), np.zeros((fs.H, fs.W), ">u2")
    f = np.zeros((fs.H, fs.W), np.uint16)
    apply(enc.encode(f), fb)
    f2 = f.copy()
    f2[:, :] = frame(3)[:, :] >> 8                                      # every tile changes
    dg = enc.encode(f2)
    assert len(dg) > 3
    apply(dg[:1] + dg[2:], fb)                                           # the second datagram never arrives
    assert not np.array_equal(fb.astype("u2"), f2)
    for _ in range(fs.ROWS * fs.COLS // fs.REFRESH_TILES + 1):           # the box keeps showing it; the sender keeps refreshing slices
        apply(enc.encode(f2), fb)
    assert np.array_equal(fb.astype("u2"), f2)


def rec(x, y, w, h, n, data):
    return struct.pack("<HHBBH", x, y, w, h, n) + data


HEAD = b"K\x02\x01\x01\x00\x00\x00"


@pytest.mark.parametrize("name,buf", [
    ("empty", b""),
    ("short", b"K\x02\x01\x00\x00\x00"),
    ("bad magic", b"X\x02\x01\x00\x00\x00\x00" + rec(0, 0, 4, 4, 0x8000 | 32, bytes(32))),
    ("bad version", b"K\x09\x01\x00\x00\x00\x00" + rec(0, 0, 4, 4, 0x8000 | 32, bytes(32))),
    ("zero width", HEAD + rec(0, 0, 0, 4, 0x8000, b"")),
    ("zero height", HEAD + rec(0, 0, 4, 0, 0x8000, b"")),
    ("huge tile", HEAD + rec(0, 0, 200, 200, 0x8000 | 100, bytes(100))),
    ("off the right", HEAD + rec(470, 0, 16, 16, 0x8000 | 512, bytes(512))),
    ("off the bottom", HEAD + rec(0, 475, 16, 16, 0x8000 | 512, bytes(512))),
    ("x way out", HEAD + rec(65535, 65535, 16, 16, 0x8000 | 512, bytes(512))),
    ("length past the end", HEAD + rec(0, 0, 16, 16, 1000, bytes(10))),
    ("raw length wrong", HEAD + rec(0, 0, 16, 16, 0x8000 | 100, bytes(100))),
    ("garbage deflate", HEAD + rec(0, 0, 16, 16, 40, bytes(range(40)))),
    ("solid with the wrong length", HEAD + rec(0, 0, 16, 16, 0x4000 | 5, bytes(5))),
    ("runs that do not fill the tile", HEAD + rec(0, 0, 16, 16, 0x2000 | 3, bytes([5, 0, 0]))),
    ("runs with a zero count", HEAD + rec(0, 0, 16, 16, 0x2000 | 6, bytes([0, 0, 0, 255, 0, 0]))),
    ("runs not a multiple of three", HEAD + rec(0, 0, 16, 16, 0x2000 | 4, bytes(4))),
    ("deflate too short", HEAD + rec(0, 0, 16, 16, len(zlib.compress(bytes(100))[2:-4]), zlib.compress(bytes(100))[2:-4])),
    ("deflate too long", HEAD + rec(0, 0, 16, 16, len(zlib.compress(bytes(2000))[2:-4]), zlib.compress(bytes(2000))[2:-4])),
    ("truncated header of a record", HEAD + b"\x00\x00\x00"),
])
def test_bad_datagrams_change_nothing_and_never_raise(name, buf):
    fb = np.full((fs.H, fs.W), 0x1234, ">u2")
    before = fb.copy()
    r = fs.decode_datagram(buf, fb)
    assert r["tiles"] == 0
    assert np.array_equal(fb, before)


def test_random_garbage_never_raises():
    rng = random.Random(7)
    fb = np.zeros((fs.H, fs.W), ">u2")
    for _ in range(300):
        n = rng.randrange(0, 300)
        buf = bytes(rng.randrange(256) for _ in range(n))
        if rng.random() < 0.7 and n > 5:
            buf = b"K\x02" + buf[2:]                                      # a valid header on garbage records
        fs.decode_datagram(buf, fb)


# ── the renderer ──
def run(face, ms, start=0, step=50):
    t = start
    while t < start + ms:
        face.tick(t)
        t += step
    return t


def test_renderer_draws_a_full_frame_in_every_state():
    random.seed(3)
    for st in ("idle", "listening", "recording", "thinking", "speaking", "music", "muted", "offline", "sick"):
        f = fr.Face()
        f.set_state(st)
        f.talking = st == "speaking"
        f.mouth = "nBCDBCE" * 5
        run(f, 600)
        img = f.render()
        assert img.shape == (480, 480, 3) and img.dtype == np.uint8 and img.any(), st


def test_offline_is_still_eyes_shut_blue_and_nothing_moves():
    random.seed(4)
    f = fr.Face()
    f.set_state("offline")
    run(f, 9000)                                                           # (the eased values settle: a float is truncated to a pixel, so wait out the tail)
    a = f.render()
    run(f, 2000, start=9000)
    b = f.render()
    face_area = (slice(60, 420), slice(60, 420))                           # (the lamp breathes at the edge; inside it nothing moves)
    assert np.array_equal(a[face_area], b[face_area])                      # the face, the can and the hat do not move
    assert f.iris[0].hidden and f.white[0].hidden                           # eyes shut: no white, no iris
    r, g, bl = (int(v) for v in f.render()[300:340, 160:240].reshape(-1, 3).mean(0))
    assert bl > r                                                          # the can is tinted blue


def test_thinking_tints_the_can_orange_and_spawns_equations():
    random.seed(5)
    f = fr.Face()
    f.set_state("thinking")
    run(f, 4500)
    r, g, b, qa = f.rc
    assert r > 200 and qa >= 60
    assert any(s["on"] for s in f.sl) or f.eq[0].text


def test_talking_follows_the_mouth_track_and_rocks_the_can():
    random.seed(6)
    f = fr.Face()
    f.set_state("speaking")
    f.talking, f.talk_t0, f.mouth, f.emote = True, 0, "nDDDDDDDDDD", "n|" + "E" * 20
    t = run(f, 400)
    assert f.mu > 5 and f.ml > 5                                           # mouth open for the D shapes
    f.mouth = "nXXXXXXXXXX" * 3
    f.talk_t0 = t
    run(f, 800, start=t)
    assert f.mu < 2
    assert f.ramp > 8                                                      # strong emotion: the can rocks hard


def test_session_makes_ears_and_cyan():
    f = fr.Face()
    f.set_state("idle")
    f.set_session(True)
    assert f.face_rgb == 0x22d3ee
    run(f, 1500)
    assert f.ear > 0.9 and f.ear_fl.opa == 255


def test_conversation_lines_flow_up_and_are_deduplicated():
    f = fr.Face()
    f.add_convo(0, "hello there")
    f.add_convo(1, "Oh, a greeting.")
    f.add_convo(1, "Oh, a greeting.")
    assert len(f.convo) == 2
    assert f.convo_target < 474 and f.convo_h == 23 * 2
    run(f, 3000)
    assert abs(f.convo_y - f.convo_target) < 2
    assert f.render()[int(f.convo_target) + 20:int(f.convo_target) + 40, 20:200].max() > 100


def test_render_is_fast_enough_for_30_fps():
    f = fr.Face()
    f.set_state("speaking")
    f.talking, f.mouth = True, "nBCDBCE" * 30
    t0 = time.perf_counter()
    for i in range(40):
        f.tick(i * 50)
        f.render()
    assert (time.perf_counter() - t0) / 40 < 0.033


# ── the streamer ──
def test_streamer_sends_frames_a_box_can_show_and_a_heartbeat():
    got = []
    s = fs.FaceStreamer("127.0.0.1", name="test", send=got.append)
    s.face.set_state("idle")
    s.start()
    time.sleep(1.2)
    s.stop()
    s.join(2)
    assert len(got) > 15
    fb = np.zeros((fs.H, fs.W), ">u2")
    r = apply(got, fb)
    assert r["bad"] == 0 and r["tiles"] > fs.ROWS * fs.COLS
    assert fb.any()
    assert all(len(d) <= fs.MAX_DATAGRAM for d in got)


# ── the link ──
class TextSensorState(SimpleNamespace):
    pass


class BinarySensorState(SimpleNamespace):
    pass


class FakeClient:
    def __init__(self):
        self.texts = []

    def text_command(self, key, state):
        self.texts.append((key, state))


def make_link():
    from esphome_link import EsphomeLink
    link = EsphomeLink(FakeClient(), media=None, box_host="127.0.0.1", log=lambda *_: None)
    link.keys.update({"mouth": 1, "emote": 2, "session": 3, "face": 4, "stream_live": 5, "subtitle": 6})
    link.room = SimpleNamespace(name="office")
    link.streamer = fs.FaceStreamer("127.0.0.1", name="office", send=lambda d: None)
    return link


def test_link_feeds_the_streamer_what_the_box_and_hub_say():
    link = make_link()
    f = link.streamer.face
    link.set_text("mouth", "nBCD")
    link.set_text("emote", "q|nnQQ")
    link.set_text("session", "1")
    assert (f.mouth, f.emote, f.in_session) == ("nBCD", "q|nnQQ", True)
    assert link.client.texts == [(1, "nBCD"), (2, "q|nnQQ"), (3, "1")]            # the box still gets them
    link.on_state(TextSensorState(key=4, state="speaking,1"))
    assert f.state_now == "speaking" and f.talking and f.talk_t0 > 0
    link.on_state(TextSensorState(key=4, state="idle,0"))
    assert f.state_now == "idle" and not f.talking and f.mouth == ""
    link.on_state(BinarySensorState(key=5, state=True))
    assert link.streamer.live and link.streamer.enc.keyframes == 2                 # the box took the screen: the whole picture goes at once


def test_link_without_the_face_sensor_does_not_stream():
    from esphome_link import EsphomeLink
    link = EsphomeLink(FakeClient(), media=None, box_host="127.0.0.1", log=lambda *_: None)
    link.start_stream(True)
    assert link.streamer is None
    link.set_text("mouth", "n")                                                     # and everything still works without one


# ── the pod's two keys: microphone off = earplugs, speaker off = a floating microphone, both = off duty ──
def _settle(f, n=40, t0=100000):
    for i in range(n):
        f.tick(t0 + i * 50)
    return f.render()


def test_keys_change_the_picture_and_back():
    from face_render import Face
    f = Face(); f.set_state("idle")
    base = _settle(f)
    assert f.mic_cap.opa == 255                                 # listening, speaker on: the floating microphone
    f.set_keys(True, False); plugs = _settle(f, t0=200000)
    assert (plugs != base).any() and f.plug[0].opa == 255 and f.plug[1].opa == 255 and f.mic_cap.opa == 0
    f.set_keys(False, True); quiet = _settle(f, t0=300000)
    assert f.mic_cap.opa == 0 and f.plug[0].opa == 0 and (quiet != base).any()
    f.set_keys(True, True); off = _settle(f, t0=400000)
    assert f.face_rgb == 0xc026d3 and f.mic_cap.opa == 0 and f.plug[0].opa == 255
    f.set_keys(False, False); back = _settle(f, t0=500000)
    assert f.face_rgb == 0x6a28c9 and f.plug[0].opa == 0 and f.mic_cap.opa == 255 and (off != back).any()


def test_off_duty_never_blue_or_red_and_offline_wins():
    from face_render import Face
    f = Face(); f.set_state("idle"); f.set_keys(True, True)
    assert f.face_rgb not in (0x475569, 0xef4444) and f.face_rgb == 0xc026d3
    f.set_state("offline")
    assert f.face_rgb == 0x475569
    _settle(f)
    assert f.mic_cap.opa == 0                                  # (no floating microphone on a dead face)


def test_face_sensor_fields_reach_the_renderer():
    from face_stream import FaceStreamer
    s = FaceStreamer("127.0.0.1", send=lambda d: None)
    s.set_box_state("idle", False, True, True)
    assert s.face.mic_off and s.face.spk_off and s.face.face_rgb == 0xc026d3
    s.set_box_state("speaking", True, True, False)
    assert s.face.mic_off and not s.face.spk_off


# ── the stream rides out Wi-Fi loss bursts: half rate while the box's loss counter grows, the whole face again afterwards ──
def test_loss_burst_slows_the_stream_then_resends_everything():
    from face_stream import FaceStreamer, LOSS_HOLD_S
    s = FaceStreamer("127.0.0.1", send=lambda d: None)
    s.note_loss(100, now=10.0)                                   # the first report is only a baseline
    assert not s.slow_now(10.1)
    s.note_loss(100, now=11.0)
    assert not s.slow_now(11.1)                                  # no growth: no loss
    s.note_loss(130, now=12.0)                                   # 30 datagrams never arrived
    assert s.slow_now(12.1) and s.slow_now(12.0 + LOSS_HOLD_S - 0.1)
    s.note_loss(160, now=13.0)                                   # still losing: the hold is renewed
    assert s.slow_now(13.0 + LOSS_HOLD_S - 0.1)
    s.enc.keyframes = 0
    assert not s.slow_now(13.0 + LOSS_HOLD_S + 0.1)              # the burst is over...
    assert s.enc.keyframes == 2 and s.recoveries == 1            # ...so the whole face is sent again
    assert not s.slow_now(20.0) and s.recoveries == 1


def test_loss_counter_wraps_and_a_reboot_does_not_look_like_loss():
    from face_stream import FaceStreamer
    s = FaceStreamer("127.0.0.1", send=lambda d: None)
    s.note_loss(4294967290, now=1.0); s.note_loss(4294967295, now=2.0)
    assert s.slow_now(2.1)
    s2 = FaceStreamer("127.0.0.1", send=lambda d: None)
    s2.note_loss(5000, now=1.0); s2.note_loss(3, now=2.0)        # the box rebooted: the counter went back to zero (a drop, not a loss)
    assert not s2.slow_now(2.1)


def test_flat_can_is_flat_and_keeps_the_silhouette():
    import numpy as np
    from PIL import Image
    from face_render import BALL_DIR
    old = BALL_DIR.parent / "ball"
    for i in range(5):
        a = np.asarray(Image.open(BALL_DIR / f"0{i}.png").convert("RGBA"))
        o = np.asarray(Image.open(old / f"0{i}.png").convert("RGBA"))
        assert a.shape == o.shape
        assert ((a[..., 3] > 128) == (o[..., 3] > 128)).mean() > 0.999          # the same shape
        assert len(np.unique(a[a[..., 3] > 128][:, :3], axis=0)) <= 8           # a handful of flat tones, no texture


def test_flat_can_sends_much_less_than_the_brushed_one():
    import numpy as np
    from face_render import Face, to565
    from face_stream import Encoder
    f = Face(); f.set_state("idle"); e = Encoder(); e.encode(to565(f.render())); sizes = []
    for i in range(120):
        f.tick(100000 + i * 50)
        sizes.append(sum(len(d) for d in e.encode(to565(f.render()))))
    assert np.mean(sizes) < 22 * 1024                           # (a gliding flat can: about 18 KB a frame; the brushed one went 366 KB/s stepped and a stream of changes when it glides)


# ── the face drawn in a child process of its own (the hub's work cannot make its frames late) ──
def test_remote_streamer_draws_in_a_child_and_ends_with_its_input():
    import socket, time
    from face_stream import RemoteFaceStreamer
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 0)); rx.settimeout(0.5)
    port = rx.getsockname()[1]
    r = RemoteFaceStreamer("127.0.0.1", name="test", port=port)
    r.start()
    try:
        got, t0 = [], time.time()
        while time.time() - t0 < 20 and len(got) < 5:                 # (the child imports numpy, OpenCV and Pillow first)
            try:
                got.append(rx.recvfrom(2000)[0])
            except socket.timeout:
                pass
        assert len(got) >= 5 and all(d[:2] == b"K\x02" for d in got)
        r.set_box_state("thinking", False, True, False); r.face.mouth = "n|BBB"; r.face.emote = "a|AAA"; r.face.set_session(True)
        r.face.add_convo(0, "what time is it?"); r.set_live(True); r.note_loss(0)
        time.sleep(0.5)
        assert r.alive
    finally:
        r.stop()
    assert not r.alive                                                 # the child leaves when its input ends (a killed hub takes it along)
    rx.close()


# ── music and nothing else: in rest mode he now and then dances ──
def _poses(f, t0, seconds):
    seen = set()
    for i in range(int(seconds * 20)):
        f.tick(t0 + i * 50)
        seen.add((int(f.bx_), int(f.by_)))
    return seen


def test_he_dances_to_music_in_rest_mode_only_now_and_then():
    from face_render import Face
    f = Face(); f.set_state("idle")
    still = _poses(f, 100000, 20)
    assert max(abs(x) for x, _ in still) <= 4                    # (the idle sway is small)
    f.music_heard = True
    big = _poses(f, 200000, 60)                                   # (a minute of music: at least one dance of 6-12 s)
    assert max(abs(x) for x, _ in big) >= 8 and min(y for _, y in big) <= -5
    assert f.next_dance > 0 and f.state_now == "idle"
    quiet = sum(1 for i in range(60 * 20) if (f.tick(300000 + i * 50) or True) and abs(f.bx_) <= 4)
    assert quiet > 20 * 15                                        # (it is not dancing all the time: only now and then)


def test_no_dance_when_a_conversation_is_open_or_earplugs_are_in():
    from face_render import Face
    f = Face(); f.set_state("idle"); f.music_heard = True; f.set_session(True)
    assert max(abs(x) for x, _ in _poses(f, 100000, 40)) <= 4
    g = Face(); g.set_state("idle"); g.music_heard = True; g.set_keys(True, False)
    assert max(abs(x) for x, _ in _poses(g, 100000, 40)) <= 4


def test_music_flag_reaches_the_face_through_the_link():
    from face_stream import FaceStreamer
    s = FaceStreamer("127.0.0.1", send=lambda d: None)
    s.set_music(True)
    assert s.face.music_heard


def test_the_can_glides_instead_of_teleporting():
    from face_render import Face
    for setup in ("idle", "dance", "talk"):
        f = Face(); f.set_state("idle")
        if setup == "dance":
            f.music_heard = True
        if setup == "talk":
            f.talking = True; f.talk_t0 = 0; f.mouth = "n|" + "DDCCBB" * 20
        xs, jump = [], 0
        for i in range(400):
            f.tick(200000 + i * 50)
            xs.append(f.ball_pos)
        jump = max(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(xs, xs[1:]))
        assert jump <= 9, (setup, jump)                        # (stepped, a swing jumped 20+ pixels in one frame)
