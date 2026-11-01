"""What the house stereo tells Bilby's page over Connect (music.BoxMusic.state): names for the queue's Spotify items and the DJ mode, and the
page's `mix:on` / `mix:off` command (portal/static/bilby-app.js, the stereo pane)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import music  # noqa: E402

URI = "spotify:track:" + "a" * 22


class Pod:
    async def play(self, url):
        pass

    async def stop(self):
        pass


class Media:
    async def catalog(self):
        return {"x/One.flac": ("One", "A", "")}

    def track_url(self, track, offset, towards):
        return "http://hub/" + track


def box():
    return music.BoxMusic(Pod(), Media(), log=lambda *_: None, name="stereo")


def test_state_names_the_spotify_items_of_the_queue_once_with_the_queue():
    b = box()
    b.add_spotify([{"uri": URI, "title": "Around the World", "artist": "Daft Punk"}])
    b.queue, b._queue_sent = ["x/One.flac", URI], False
    first = b.state()
    assert first["queue"] == ["x/One.flac", URI]
    assert first["titles"] == {URI: ["Around the World", "Daft Punk"]}
    assert "titles" not in b.state() and "queue" not in b.state()         # like the queue, only when it changed


def test_no_mix_key_unless_the_stereo_sets_one_and_the_page_can_switch_it():
    b = box()
    assert "mix" not in b.state()
    on = {"v": False}
    b.mix = {"get": lambda: {"on": on["v"], "xfade": 6.0}, "set": lambda v: on.update(v=v)}
    assert b.state()["mix"] == {"on": False, "xfade": 6.0}
    asyncio.run(b.command("mix:on", []))
    assert b.state()["mix"]["on"] is True
    asyncio.run(b.command("mix:off", []))
    assert on["v"] is False


def test_a_pod_without_a_mix_ignores_the_command():
    asyncio.run(box().command("mix:on", []))                                # nothing to switch, nothing raised
