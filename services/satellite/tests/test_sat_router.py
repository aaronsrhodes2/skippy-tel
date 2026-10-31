"""The house's audio router (router.py): one decode per sound, every PC output fed from one mixer, voice over media, outputs that come and go."""
import numpy as np

import book_out
import router as rt
import speaker_out


class FakeStream:
    def __init__(self, dev, cb):
        self.dev, self.cb, self.closed = dev, cb, False

    def pull(self, frames):
        out = np.zeros((frames, 2), dtype="float32")
        self.cb(out, frames, None, None)
        return out

    def stop(self):
        pass

    def close(self):
        self.closed = True


class World:
    def __init__(self, present=("HDMI", "Enclave")):
        self.present, self.streams, self.reinits = set(present), {}, 0

    def find(self, part):
        return (len(part), part) if part in self.present else None

    def factory(self, dev, cb):
        s = FakeStream(dev, cb)
        self.streams[dev] = s
        return s

    def reinit(self):
        self.reinits += 1


def make(world, delays=(0.0, 0.0)):
    r = rt.Router([("hdmi", "HDMI", delays[0]), ("bt", "Enclave", delays[1])], find=world.find, stream_factory=world.factory,
                  reinit=world.reinit, log=lambda *a: None, autostart=False)
    r.mix_once()                                    # the first block opens the outputs
    return r


def tone(seconds, level=0.5):
    return np.full((int(rt.RATE * seconds), 2), level, dtype="float32")


def test_one_clip_reaches_every_output_from_one_decode():
    w = World()
    r = make(w)
    src = rt.ClipSource(tone(0.1))
    r.play(src, targets={"hdmi", "bt"})
    for _ in range(3):
        r.mix_once()
    for s in w.streams.values():
        got = s.pull(rt.BLOCK * 4)
        assert got.max() == np.float32(0.5)           # the clip, once per output
    assert src.done


def test_an_outputs_delay_is_silence_in_front():
    w = World()
    r = make(w, delays=(0.0, 0.2))
    r.play(rt.ClipSource(tone(0.05)), targets={"hdmi", "bt"})
    r.mix_once()
    hdmi, bt = w.streams[len("HDMI")], w.streams[len("Enclave")]
    assert hdmi.pull(rt.BLOCK * 2).max() > 0           # the HDMI is not delayed
    assert bt.pull(int(rt.RATE * 0.2)).max() == 0      # the stereo waits its 0.2 s first


def test_voice_ducks_a_book_instead_of_stopping_it():
    w = World()
    r = make(w)
    book = r.play(rt.ClipSource(tone(10, 0.8)), layer="media", targets={"hdmi"})
    r.mix_once()
    r.play(rt.ClipSource(tone(2, 0.0)), layer="voice", targets={"hdmi"})
    for _ in range(12):
        r.mix_once()
    assert r.layer_gain["media"] == rt.DUCK and book.id in r.plays    # quieter, still playing
    r.stop(next(p for p in r.plays.values() if p.layer == "voice"))
    for _ in range(12):
        r.mix_once()
    assert r.layer_gain["media"] == 1.0


def test_a_stereo_turned_on_later_is_found_while_nothing_plays():
    w = World(present=("HDMI",))
    r = make(w)
    assert r.available("hdmi") and not r.available("bt")
    w.present.add("Enclave")
    r._last_scan = -1e9
    r.mix_once()
    assert r.available("bt") and w.reinits >= 1


def test_a_missing_output_never_resets_audio_that_is_playing():
    w = World(present=("HDMI",))
    r = make(w)
    before = w.reinits
    r.play(rt.ClipSource(tone(5)), targets={"hdmi"})
    r._last_scan = -1e9
    r.mix_once()
    assert w.reinits == before


def test_the_hub_adapters_put_voice_and_books_on_the_router(monkeypatch):
    w = World()
    monkeypatch.setattr(rt, "_ROUTER", make(w))
    env = {"SATELLITE_OUT": "office=pod+hdmi+bt"}
    pod, outs = speaker_out.parse_outputs(env, "office")
    assert pod and len(outs) == 1 and isinstance(outs[0], speaker_out.RoutedOut) and outs[0].kinds == {"hdmi", "bt"}
    books = book_out.build_outputs(env, "office")
    assert len(books) == 1 and isinstance(books[0], book_out.RoutedBookOut)
    assert speaker_out.parse_outputs({**env, "SATELLITE_ROUTER": "0"}, "office")[1][0].__class__ is speaker_out.PcOut


def test_the_routed_voice_knows_when_the_pc_outputs_fall_silent(monkeypatch):
    import asyncio
    import time
    w = World()
    r = make(w, delays=(0.5, 0.0))
    out = speaker_out.RoutedOut(r, ["hdmi"], duck_others=False, decoder=lambda mp3, gain: tone(2.0))
    r.autostart = True
    before = time.monotonic()
    asyncio.run(asyncio.wait_for(out.play(b"mp3"), 10))
    r.close()
    assert out.talking_until >= before + 2.0 + 0.5 + speaker_out.ECHO_TAIL_S - 0.01   # the clip, the Sony's delay, the room's echo


def test_the_room_is_deaf_while_the_pc_outputs_still_say_his_words():
    """2026-10-31: the Sony plays his voice ~0.5 s after the pod; the room left BUSY when the pod finished and could hear his own last words."""
    import time
    import hub as hub_mod

    class Spotter:
        def __init__(self):
            self.fed = 0

        def feed(self, block):
            self.fed += 1
            return False

    class Out:
        talking_until = time.monotonic() + 30

    spot = Spotter()
    room = hub_mod.Room("office", type("Link", (), {})(), None, None, None, spotter=spot, log=lambda *a: None)
    room.pc_outs = [Out()]
    room.feed([0.3] * hub_mod.BLOCK)
    assert spot.fed == 0
    Out.talking_until = 0
    room.feed([0.3] * hub_mod.BLOCK)
    assert spot.fed == 1


def test_joined_mp3_pieces_become_one_stream_the_pod_plays_to_the_end_decoded_only_once():
    """2026-10-31: the voice gate joins one MP3 per sentence; the pod's decoder stopped at the second piece's header (silent mid-answer). The
    router's one pass decodes the reply once, encodes the pod's copy from that sound, and the PC outputs reuse the same sound."""
    import hub as hub_mod
    calls, decoded = [], []

    def run(args, input, capture_output, timeout):
        calls.append(args)
        return type("R", (), {"stdout": b"ONE-CLEAN-STREAM"})()

    def dec(mp3):
        decoded.append(mp3)
        return tone(0.1)
    assert hub_mod.one_stream(b"MP3Info...audio", run=run, decoder=dec) == b"MP3Info...audio" and not calls     # one piece: untouched
    pod = hub_mod.one_stream(b"Info..a..Info..b", run=run, decoder=dec)
    assert pod == b"ONE-CLEAN-STREAM" and "libmp3lame" in calls[0] and len(decoded) == 1
    assert speaker_out.decode_cached(pod).shape == tone(0.1).shape and len(decoded) == 1     # the Sony's copy: no second decode


def test_a_voice_clip_that_cannot_start_in_time_is_dropped_never_played_late():
    """2026-10-31: the first clip after a restart waited 18 s for the first device scan; the room then heard it as the Captain's answer."""
    w = World(present=())
    t = {"now": 100.0}
    r = rt.Router([("hdmi", "HDMI", 0.0)], find=w.find, stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None,
                  clock=lambda: t["now"], autostart=False)
    p = r.play(rt.ClipSource(tone(1.0)), targets={"hdmi"})
    r.mix_once()
    assert p.id in r.plays                                          # no output yet: it waits
    t["now"] += rt.STALE_VOICE_S + 0.5
    w.present.add("HDMI")
    r._last_scan = -1e9
    r.mix_once()
    assert p.finished.is_set() and p.id not in r.plays              # too late: dropped
    assert w.streams[len("HDMI")].pull(rt.BLOCK * 2).max() == 0     # nothing was played


def test_a_book_waits_for_its_output_instead_of_being_dropped():
    w = World(present=())
    t = {"now": 100.0}
    r = rt.Router([("hdmi", "HDMI", 0.0)], find=w.find, stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None,
                  clock=lambda: t["now"], autostart=False)
    p = r.play(rt.ClipSource(tone(1.0)), layer="media", targets={"hdmi"})
    t["now"] += 30
    r.mix_once()
    assert p.id in r.plays and not p.started
