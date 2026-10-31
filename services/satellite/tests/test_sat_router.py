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
