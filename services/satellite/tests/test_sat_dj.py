"""Bilby's DJ mode (dj.py): keys, tempos, phrase points, the gapless chain, the order, and a rendered crossing that lands on the beat."""
import shutil

import numpy as np
import pytest

import dj

R = dj.RATE
PEAK = 0.006                      # a 12 ms burst peaks 6 ms after its beat: where onsets() puts it


# ── keys and tempos ──
def test_camelot_from_traktor_keys():
    assert dj.camelot(0) == (8, False)            # C major = 8B
    assert dj.camelot(21) == (8, True)            # A minor = 8A
    assert dj.camelot(12) == (5, True)            # C minor = 5A (Traktor's "10m")
    assert dj.camelot(7) == (9, False)            # G major = 9B


def test_key_distance():
    assert dj.key_distance((8, True), (8, True)) == 0
    assert dj.key_distance((8, True), (9, True)) == 1
    assert dj.key_distance((8, True), (8, False)) == 1
    assert dj.key_distance((12, True), (1, True)) == 1      # round the wheel
    assert dj.key_distance((8, True), (11, True)) == 3
    assert dj.key_distance(None, (8, True)) is None


def test_tempo_match_half_and_double():
    r, mult = dj.tempo_match(124, 128)
    assert mult == 1 and r == pytest.approx(124 / 128)
    assert dj.tempo_match(128, 64)[1] == 2.0
    assert dj.tempo_match(120, 140) is None


def track(bpm=124.0, first=0.4, dur=240.0, key=8, minor=True, path="x"):
    return dj.Track(path, bpm, first, dur, key, minor)


# ── the plan ──
def test_plan_cuts_on_a_key_clash_or_far_tempos_or_unknown():
    assert dj.plan(track(), track(key=11)).kind == "cut"
    assert "tempo" in dj.plan(track(bpm=124), track(bpm=100)).reason
    assert dj.plan(None, track()).kind == "cut"


def test_plan_mixes_out_on_a_32_beat_phrase_that_leaves_room():
    a, b = track(bpm=120, first=0.5, dur=200), track(bpm=123)
    p = dj.plan(a, b)
    assert p.kind == "mix" and p.phrase == 32
    beats = (p.a_out - a.first_beat) / a.period
    assert beats == pytest.approx(round(beats)) and round(beats) % 32 == 0
    assert p.a_out + dj.OVERLAP * a.period <= a.duration
    assert p.tempo == pytest.approx(120 / 123)
    assert len(p.ramp) == dj.RAMP_STEPS and p.ramp[-1] < 1 and p.ramp[0] > p.tempo


def test_plan_stays_ahead_of_a_fade_out_and_of_where_the_track_is():
    a, b = track(bpm=120, first=0.5, dur=200), track(bpm=120)
    late = dj.plan(a, b)
    early = dj.plan(a, b, a_loud_end=150)
    assert early.a_out < late.a_out and early.a_out + dj.OVERLAP * a.period / 2 <= 150
    assert dj.plan(a, b, not_before=199).kind == "cut"
    assert dj.plan(a, b).ramp == []                          # same tempo: nothing to glide back from


def test_parse_nml(tmp_path):
    nml = tmp_path / "c.nml"
    nml.write_text('''<?xml version="1.0" encoding="UTF-8"?><NML VERSION="19"><COLLECTION ENTRIES="2">
<ENTRY TITLE="Zero" ARTIST="Delerium"><LOCATION DIR="/:Users/:a/:corrected_music/:Delerium/:Mythologie (2016)/:" FILE="Zero.m4a" VOLUME="Mac"/>
<INFO KEY="10m" PLAYTIME="291" PLAYTIME_FLOAT="290.46" RANKING="204"/><TEMPO BPM="81.4997" BPM_QUALITY="100"/><MUSICAL_KEY VALUE="12"/>
<CUE_V2 NAME="Beat" TYPE="0" START="20.6"/><CUE_V2 NAME="AutoGrid" TYPE="4" START="31.5"/></ENTRY>
<ENTRY TITLE="Elsewhere"><LOCATION DIR="/:Other/:" FILE="x.mp3"/><TEMPO BPM="120"/></ENTRY>
</COLLECTION></NML>''', encoding="utf-8")
    got = dj.parse_nml(str(nml))
    assert list(got) == ["Delerium/Mythologie (2016)/Zero.m4a"]
    t = got["Delerium/Mythologie (2016)/Zero.m4a"]
    assert t.bpm == pytest.approx(81.4997) and t.first_beat == pytest.approx(0.0315) and (t.key, t.minor) == (5, True)
    assert t.stars == 4 and t.duration == pytest.approx(290.46)


def test_setting_vault_then_voice(tmp_path):
    assert dj.enabled({"BILBY_DJ": "off"}, tmp_path) is False
    assert dj.enabled({}, tmp_path) is True
    dj.set_enabled(False, tmp_path)
    assert dj.enabled({"BILBY_DJ": "on"}, tmp_path) is False


# ── the chain ──
class Src:
    def __init__(self, n, value):
        self.pcm, self.pos, self.closed = np.full((n, 2), value, dtype="float32"), 0, False

    def read(self, frames):
        c = self.pcm[self.pos:self.pos + frames]
        self.pos += len(c)
        return c

    @property
    def done(self):
        return self.pos >= len(self.pcm)

    def close(self):
        self.closed = True


def test_chain_is_gapless_and_cuts_a_piece_exactly():
    ch = dj.ChainSource()
    a = ch.append(Src(1000, 1.0))
    started = []
    ch.append(Src(300, 2.0), on_start=lambda: started.append(True))
    assert ch.cut_at(a, 700, margin=100)
    out = np.concatenate([ch.read(256) for _ in range(4)])
    assert (out[:700, 0] == 1).all() and (out[700:1000, 0] == 2).all() and started == [True]
    assert len(out) == 1000 and not ch.done                    # more may come: silence, not the end
    ch.finish()
    assert ch.done


def test_chain_refuses_a_cut_already_passed():
    ch = dj.ChainSource()
    a = ch.append(Src(1000, 1.0))
    ch.read(600)
    assert not ch.cut_at(a, 650, margin=100)


def test_walk_keeps_tempo_and_key_together():
    lib = {f"t{i}": track(bpm=120 + (i % 2) * 40, key=8 if i % 2 == 0 else 2, path=f"t{i}") for i in range(40)}
    order = dj.walk(list(lib), lib.get, rng=__import__("random").Random(3))
    assert sorted(order) == sorted(lib)
    pairs = list(zip(order, order[1:]))
    good = sum(1 for x, y in pairs if dj.tempo_match(lib[x].bpm, lib[y].bpm))
    assert good >= len(pairs) - 2                              # only the jump between the two groups doesn't mix


# ── a real crossing, rendered by ffmpeg (rubberband) and checked by ear, so to speak ──
def _has_scipy():
    try:
        import scipy.signal  # noqa: F401
        return True
    except ImportError:
        return False


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or not _has_scipy(), reason="needs ffmpeg (with rubberband) and scipy")


def clicks(path, bpm, first, dur, freq, bass=60):
    """A click track: a short tone burst on every beat, and a thump at `bass` Hz under it (the bass the EQ swap trades)."""
    from scipy.io import wavfile
    x = np.zeros(int(R * dur), dtype="float32")
    n = int(0.012 * R)
    t = np.arange(n) / R
    burst = (0.5 * np.sin(2 * np.pi * freq * t) * np.hanning(n)).astype("float32")
    m = int(0.08 * R)
    thump = (0.4 * np.sin(2 * np.pi * bass * np.arange(m) / R) * np.hanning(m)).astype("float32")
    b = first
    while b < dur - 0.1:
        i = int(round(b * R))
        x[i:i + n] += burst
        x[i:i + m] += thump
        b += 60 / bpm
    wavfile.write(path, R, np.stack([x, x], 1))


def onsets(x, freq):
    """Times of the bursts at `freq` in x (band-limited envelope, one per beat)."""
    mono = x.mean(axis=1)
    spec = np.fft.rfft(mono)
    f = np.fft.rfftfreq(len(mono), 1 / R)
    spec[(f < freq * 0.8) | (f > freq * 1.25)] = 0
    env = np.abs(np.fft.irfft(spec, len(mono)))
    th = env.max() * 0.3
    above = np.flatnonzero(env > th)
    out, last = [], -10 ** 9
    for i in above:                                                # the first sample over the line, then 0.15 s deaf
        if i - last > int(0.15 * R):
            out.append((i + int(np.argmax(env[i:i + int(0.03 * R)]))) / R)
            last = i
    return np.array(out)


@needs_ffmpeg
def test_the_crossing_lands_on_the_beat(tmp_path):
    a_path, b_path = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
    clicks(a_path, 124.0, 0.37, 75, 1000, bass=100)
    clicks(b_path, 128.0, 0.61, 60, 4000, bass=45)
    # Traktor's grid for A is 25 ms off: the audio must pull it back onto the beat
    a, b = track(124.0, 0.37 + 0.025, 75, path="a"), track(128.0, 0.61, 60, path="b")
    p = dj.plan(a, b)
    assert p.kind == "mix"
    clip, a_stop, b_resume = dj.render(p, a_path, b_path)
    assert len(clip) / R == pytest.approx(p.seconds + dj.SEAM_S, abs=0.01)
    span = dj.OVERLAP * a.period
    # A's beats in the crossing are where the click track has them (the nudge found the true grid)
    ta = onsets(clip[: int(span * R)], 1000)
    true_a = 0.37 + PEAK + np.arange(200) * a.period - a_stop
    assert len(ta) >= dj.OVERLAP // 2 + 2
    assert np.abs(ta[:, None] - true_a[None, :]).min(axis=1).max() < 0.004
    # every beat of B while they play together falls on one of A's: beat-matched
    tb = onsets(clip[: int(span * R)], 4000)
    assert len(tb) >= dj.OVERLAP // 2 - 1
    assert np.abs(tb[:, None] - true_a[None, :]).min(axis=1).max() < 0.008
    # after the crossing B glides back to its own tempo: its beats spread out to 60/128
    tb_all = onsets(clip, 4000)
    gaps = np.diff(tb_all)
    assert gaps[0] == pytest.approx(60 / 124, abs=0.006)
    assert gaps[-1] == pytest.approx(60 / 128 / p.ramp[-1], abs=0.006)
    # the EQ swap: B's bass (45 Hz) is out for the first half, A's (100 Hz) is out for the last quarter
    def band(x, lo, hi):
        spec = np.fft.rfft(x.mean(axis=1))
        f = np.fft.rfftfreq(len(x), 1 / R)
        return float(np.abs(spec[(f >= lo) & (f <= hi)]).sum())
    first, last = clip[: int(span * R * 0.45)], clip[int(span * R * 0.75): int(span * R)]
    assert band(first, 35, 55) < 0.1 * band(first, 90, 110)
    assert band(last, 90, 110) < 0.1 * band(last, 35, 55)
    # B's own piece picks up exactly where the clip ends: its beats continue on B's grid
    true_b = 0.61 + PEAK + np.arange(200) * (60 / 128)
    nxt = true_b[true_b > b_resume + 0.03][0]                 # (b_resume itself falls inside a burst: the piece starts on its beat)
    tail = dj.decode(b_path, b_resume, 3.0)
    got = onsets(tail, 4000) + b_resume
    first_after = got[got > b_resume + 0.03][0]
    assert first_after == pytest.approx(nxt, abs=0.004)


@needs_ffmpeg
def test_chain_of_body_clip_body_has_no_gap(tmp_path):
    a_path, b_path = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
    clicks(a_path, 120.0, 0.5, 70, 1000)
    clicks(b_path, 121.0, 0.5, 60, 4000)
    a, b = track(120.0, 0.5, 70, path="a"), track(121.0, 0.5, 60, path="b")
    p = dj.plan(a, b)
    clip, a_stop, b_resume = dj.render(p, a_path, b_path)
    start = a_stop - 4.0
    full_a = dj.decode(a_path, start, 6.0)
    ch = dj.ChainSource()
    body = ch.append(dj._Clip(full_a))
    ch.append(dj._Clip(clip))
    ch.append(dj._Clip(dj.decode(b_path, b_resume, 4.0)))
    assert ch.cut_at(body, int(round((a_stop - start) * R)), margin=0)
    ch.finish()
    out = []
    while not ch.done:
        out.append(ch.read(2400))
    out = np.concatenate(out)
    assert len(out) == int(round(4.0 * R)) + len(clip) + int(round(4.0 * R))
    # the beats run on with no hiccup across both joins: every gap is one beat of A, one of B, or a glide step in between
    tb = onsets(out[int(4.0 * R):], 4000)
    gaps = np.diff(tb)
    assert gaps.min() > 60 / 121 * 0.97 and gaps.max() < 60 / 120 * 1.03


@needs_ffmpeg
def test_the_player_mixes_one_track_into_the_next_and_moves_the_queue_on(tmp_path):
    import asyncio
    import threading

    import music

    root = tmp_path / "lib"
    root.mkdir()
    clicks(str(root / "a.wav"), 124.0, 0.4, 50, 1000)
    clicks(str(root / "b.wav"), 126.0, 0.4, 50, 4000)
    known = {"a.wav": track(124.0, 0.4, 50, path="a.wav"), "b.wav": track(126.0, 0.4, 50, path="b.wav")}

    class Lib(dj.Library):
        def traktor(self):
            return known

    class Play:
        def __init__(self, src):
            self.source, self.finished, self.gain = src, threading.Event(), 1.0

    class Router:
        sinks = {"hdmi": 1}

        def __init__(self):
            self.plays = []

        def available(self, k): return True

        def play(self, source, layer, targets, gain):
            self.plays.append(Play(source))
            return self.plays[-1]

        def stop(self, p):
            p.source.close()
            p.finished.set()

    class Media:
        async def catalog(self): return {"a.wav": ("A", "x", ""), "b.wav": ("B", "y", "")}
        def track_url(self, path, start, towards=""): return "unused"

    lib = Lib(str(root), nml="", state=tmp_path / "state", log=lambda m: None)
    logs = []
    r = Router()
    player = dj.DjPlayer(r, ["hdmi"], lib=lib, mixer=dj.Mixer(lib, {}, log=logs.append, state=tmp_path / "state"), log=logs.append)
    box = music.BoxMusic(player, Media(), log=logs.append, name="stereo")
    player.music = box

    async def go():
        await box.play_queue(["a.wav", "b.wav"], 0, 30.0)          # start A 30 s in: its mix-out point is still ahead
        chain = r.plays[0].source
        for _ in range(400):                                       # the crossing is rendered and queued behind A
            await asyncio.sleep(0.05)
            if len(chain.pieces) == 3:
                break
        assert len(chain.pieces) == 3 and chain.pieces[0].limit is not None
        heard = []
        while not chain.done and len(heard) < 2000:
            got = chain.read(dj.RATE)
            if not len(got):
                await asyncio.sleep(0.05)
            heard.append(got)
            await asyncio.sleep(0)
            if chain.pieces and chain.pieces[0].tag == "b.wav":
                break
        await asyncio.sleep(0.1)
        assert box.idx == 1 and box.track() == "b.wav" and box.playing
        assert any("mixed into B" in str(m) for m in logs)
        await player.stop()
    asyncio.run(go())


def test_walk_keeps_spotify_items_it_cannot_place():
    lib = {f"t{i}": track(path=f"t{i}") for i in range(6)}
    tracks = list(lib) + ["spotify:track:abc"]
    assert sorted(dj.walk(tracks, lib.get)) == sorted(tracks)


def test_spotify_taking_over_stops_the_chain_and_its_feeder():
    import asyncio
    import threading

    class Play:
        def __init__(self, src):
            self.source, self.finished, self.gain = src, threading.Event(), 1.0

    class Router:
        sinks = {"hdmi": 1}

        def __init__(self):
            self.plays, self.stopped = [], []

        def available(self, k): return True

        def play(self, source, layer, targets, gain):
            self.plays.append(Play(source))
            return self.plays[-1]

        def stop(self, p):
            self.stopped.append(p)
            p.source.close()
            p.finished.set()

    r = Router()
    spot = type("S", (), {"play": lambda self, u, ms: None, "pause": lambda self: None})()
    player = dj.DjPlayer(r, ["hdmi"], lib=dj.Library("nowhere", nml="", log=lambda m: None), log=lambda m: None, spotify=spot)
    player._ensure_stream = lambda: None                       # no spotify container here
    chain = dj.ChainSource()
    p = r.play(chain, "media", {"hdmi"}, 1.0)
    player.play_obj, player.chain = p, chain
    gen = player.gen
    player._adopt({"uri": "spotify:track:x", "title": "T", "artist": "A"})
    assert p in r.stopped and player.chain is None and player.gen != gen

    async def go():
        player.play_obj, player.chain = r.play(dj.ChainSource(), "media", {"hdmi"}, 1.0), None
        g = player.gen
        await player.play_spotify("spotify:track:y")
        assert player.gen != g and r.stopped[-1] is not p
    asyncio.run(go())


# ── Spotify blended into a Bilby shuffle on the stereo ──
def test_blend_puts_two_spotify_after_every_three_bilby():
    import stereo
    assert stereo.blend(list("abcdefg"), ["s1", "s2", "s3", "s4"]) == ["a", "b", "c", "s1", "s2", "d", "e", "f", "s3", "s4", "g"]
    assert stereo.blend(list("abcdef"), ["s1"]) == ["a", "b", "c", "s1", "d", "e", "f"]   # Spotify ran out: Bilby alone
    assert stereo.blend_rule({}) == (3, 2) and stereo.blend_rule({"BILBY_BLEND": "off"}) is None
    assert stereo.blend_rule({"BILBY_BLEND": "4:1"}) == (4, 1)


def test_a_spotify_item_that_wont_start_is_skipped_and_spotify_rests():
    import asyncio
    import threading

    import music

    class Play:
        def __init__(self, src):
            self.source, self.finished, self.gain = src, threading.Event(), 1.0

    class Router:
        sinks = {"hdmi": 1}

        def available(self, k): return True

        def play(self, source, layer, targets, gain): return Play(source)

        def stop(self, p):
            p.source.close()
            p.finished.set()

    asked = []
    spot = type("S", (), {"play": lambda self, u, ms, *rest: asked.append(u), "pause": lambda self: None})()
    clock = [1000.0]
    logs = []
    player = dj.DjPlayer(Router(), ["hdmi"], lib=dj.Library("nowhere", nml="", log=lambda m: None), log=logs.append, spotify=spot,
                         clock=lambda: clock[0], popen=lambda *a, **k: type("P", (), {"stdout": type("O", (), {"read": lambda s, n: b""})(),
                                                                                      "kill": lambda s: None})())
    player._ensure_stream = lambda: None

    class Media:
        async def catalog(self): return {}
        def track_url(self, path, start, towards=""): return "http://x/" + path

    box = music.BoxMusic(player, Media(), log=logs.append, name="stereo", clock=lambda: clock[0])
    player.music = box

    async def go(monkey_start):
        dj.SPOT_START_S = monkey_start
        await box.play_queue(["spotify:track:AAAAAAAAAAAAAAAAAAAAAA", "spotify:track:BBBBBBBBBBBBBBBBBBBBBB", "a.mp3"])
        await asyncio.sleep(0.15)                                   # the watchdog fires: Spotify never started
        assert player.spotify_down(), (logs, asked, player.spot_uri, player.spot_seen, box.idx)
        for _ in range(20):
            await asyncio.sleep(0.02)
            if box.idx == 2:
                break
        assert box.track() == "a.mp3" and asked == ["spotify:track:AAAAAAAAAAAAAAAAAAAAAA"]   # the second Spotify item was skipped without asking
        await player.stop()

    async def guarded():
        try:
            await go(0.05)
        finally:
            await player.stop()                                     # releases the router play's watcher thread, pass or fail

    old = dj.SPOT_START_S
    try:
        asyncio.run(guarded())
    finally:
        dj.SPOT_START_S = old


def test_a_429_from_spotify_plays_bilbys_next_song_at_once_and_spotify_rests():
    # 2026-11-01 07:21: "Spotify said 429: Too many requests" at the hand-over, and 15 s of nothing before the watchdog gave up
    import asyncio
    import threading

    import music

    class Play:
        def __init__(self, src):
            self.source, self.finished, self.gain = src, threading.Event(), 1.0

    class Router:
        sinks = {"hdmi": 1}

        def available(self, k): return True

        def play(self, source, layer, targets, gain): return Play(source)

        def stop(self, p):
            p.source.close()
            p.finished.set()

    asked = []

    def refuse(self, u, ms, *rest):
        asked.append(u)
        raise RuntimeError("Spotify said 429: 'Too many requests'")
    spot = type("S", (), {"play": refuse, "pause": lambda self: None})()
    clock = [1000.0]
    logs = []
    player = dj.DjPlayer(Router(), ["hdmi"], lib=dj.Library("nowhere", nml="", log=lambda m: None), log=logs.append, spotify=spot,
                         clock=lambda: clock[0], popen=lambda *a, **k: type("P", (), {"stdout": type("O", (), {"read": lambda s, n: b""})(),
                                                                                      "kill": lambda s: None})())
    player._ensure_stream = lambda: None

    class Media:
        async def catalog(self): return {}
        def track_url(self, path, start, towards=""): return "http://x/" + path

    box = music.BoxMusic(player, Media(), log=logs.append, name="stereo", clock=lambda: clock[0])
    player.music = box

    async def go():
        await box.play_queue(["spotify:track:AAAAAAAAAAAAAAAAAAAAAA", "spotify:track:BBBBBBBBBBBBBBBBBBBBBB", "a.mp3"])
        for _ in range(10):                                         # no SPOT_START_S wait: the refusal itself moves on
            await asyncio.sleep(0.01)
            if box.idx == 2:
                break
        assert box.track() == "a.mp3" and player.spotify_down() and asked == ["spotify:track:AAAAAAAAAAAAAAAAAAAAAA"]
        assert any("refused" in str(m) for m in logs)

    async def guarded():
        try:
            await go()
        finally:
            await player.stop()
    asyncio.run(guarded())


@needs_ffmpeg
def test_dj_mode_hands_over_to_spotify_on_a_phrase(tmp_path):
    """The next item is Spotify's: no beat-matching (its sound is live), but the hand-over waits for the playing track's phrase point."""
    import asyncio
    import threading

    import music

    root = tmp_path / "lib"
    root.mkdir()
    clicks(str(root / "a.wav"), 120.0, 0.5, 60, 1000)
    known = {"a.wav": track(120.0, 0.5, 60, path="a.wav")}

    class Lib(dj.Library):
        def traktor(self):
            return known

    class Play:
        def __init__(self, src):
            self.source, self.finished, self.gain = src, threading.Event(), 1.0

    class Router:
        sinks = {"hdmi": 1}

        def __init__(self):
            self.plays = []

        def available(self, k): return True

        def play(self, source, layer, targets, gain):
            self.plays.append(Play(source))
            return self.plays[-1]

        def stop(self, p):
            p.source.close()
            p.finished.set()

    lib = Lib(str(root), nml="", state=tmp_path / "state", log=lambda m: None)
    r = Router()
    spot = type("S", (), {"play": lambda self, *a: None, "pause": lambda self: None})()
    player = dj.DjPlayer(r, ["hdmi"], lib=lib, mixer=dj.Mixer(lib, {}, log=lambda m: None, state=tmp_path / "state"), log=lambda m: None,
                         spotify=spot)
    handed, pos = [], {"read": 0}

    async def handoff(fade_s):
        handed.append((pos["read"], fade_s))
    player.handoff_to_spotify = handoff

    class Media:
        async def catalog(self): return {}
        def track_url(self, path, start, towards=""): return "unused"

    box = music.BoxMusic(player, Media(), log=lambda m: None, name="stereo")
    player.music = box

    async def go():
        await box.play_queue(["a.wav", "spotify:track:" + "C" * 22], 0, 25.0)
        chain = r.plays[0].source
        for _ in range(4000):
            if handed:
                break
            got = chain.read(dj.RATE // 20)
            pos["read"] += len(got)
            await asyncio.sleep(0.005 if len(got) else 0.05)
        assert handed, "no hand-over"
        at = 25.0 + handed[0][0] / dj.RATE
        beats = (at - 0.5) / 0.5
        assert abs(beats - round(beats / 8) * 8) < 0.25          # it waited for a phrase boundary (to within the read step)
        assert handed[0][1] == pytest.approx(16 * 0.5)
        assert at < 60 - 8                                         # ahead of the end, with the fade still to come

    async def guarded():
        try:
            await asyncio.wait_for(go(), 60)
        finally:
            await player.stop()
    asyncio.run(guarded())
