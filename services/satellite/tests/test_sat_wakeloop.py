"""The wake loop's test tool (wakeloop.py): what it reads from the hub's log, and the media server's door for its clips."""
import asyncio
import socket

import pytest

import media
import wakeloop

aiohttp = pytest.importorskip("aiohttp")


def test_a_name_a_pause_and_a_command_heard_and_answered_passes():
    lines = ["08:25:00 [office] woke", "08:25:08 [office] heard: What time is it?", "08:25:22 [office] said: It is 8:25.",
             "08:25:10 [kitchen] (no audio from the box for 8 s while listening: back to rest)"]
    v = wakeloop.verdict(lines, "office", "What time is it?")
    assert v["ok"] and v["heard"] == "What time is it?" and not v["watchdog"]


def test_the_watchdog_or_the_wrong_words_fail_it():
    deaf = ["07:47:14 [office] woke", "07:47:23 [office] (no audio from the box for 8 s while listening: back to rest)"]
    assert not wakeloop.verdict(deaf, "office", "What time is it?")["ok"]
    assert not wakeloop.verdict(deaf, "office", "")["ok"]
    wrong = ["08:25:00 [office] woke", "08:25:08 [office] heard: Lay down, come on."]
    assert not wakeloop.verdict(wrong, "office", "What time is it?")["ok"]
    assert wakeloop.verdict(["08:27:50 [office] woke"], "office", "")["ok"]          # the name alone: woke, and kept listening


def test_the_clips_door_opens_only_with_the_pass_and_only_to_its_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "WAKELOOP_DIR", tmp_path)
    (tmp_path / "skippy.wav").write_bytes(b"RIFFclip")
    (tmp_path / "notes.txt").write_text("no")

    async def go():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        m = media.MediaServer(port=port, host="127.0.0.1", home_token="tok")
        await m.start()
        try:
            base = f"http://127.0.0.1:{port}/w/"
            good = media.wakeloop_pass("tok")
            async with aiohttp.ClientSession() as http:
                async with http.get(base + good + "/skippy.wav") as r:
                    assert r.status == 200 and await r.read() == b"RIFFclip" and r.headers["Content-Type"] == "audio/wav"
                for bad in ("guess/skippy.wav", good + "/notes.txt", good + "/missing.wav", good + "/..%2Fskippy.wav"):
                    async with http.get(base + bad) as r:
                        assert r.status == 404, bad
        finally:
            await m.stop()
    asyncio.run(go())
    assert media.wakeloop_pass("") == "" and media.wakeloop_pass("tok") != media.wakeloop_pass("other")
