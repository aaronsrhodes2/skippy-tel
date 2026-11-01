"""His music grammar (the Captain, 2026-11-01): wake + function (music / Bilby) + mode + filters + location, parsed straight to play_music's
arguments (brain.music_grammar, no model in between) and queued by mixes.resolve: the car's discovery mix, genre families, his playlists
(Traktor's and Spotify's, Bilby's copy preferred), each place, and a filter that matches nothing played as the closest, never a question."""
from __future__ import annotations

import asyncio
import random
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain  # noqa: E402
import hub  # noqa: E402
import mixes  # noqa: E402

S1 = "spotify:track:" + "1" * 22
S2 = "spotify:track:" + "2" * 22
S3 = "spotify:track:" + "3" * 22


def test_his_four_phrases_parse_to_the_exact_arguments():
    assert brain.music_grammar("skippy, bilby discovery everywhere") == {"mode": "discovery", "shuffle": True, "speaker": "everywhere"}
    assert brain.music_grammar("skippy, play favorite music upstairs") == {"mode": "favorites", "shuffle": True, "speaker": "upstairs"}
    assert brain.music_grammar("skippy, tell bilby to shuffle new darkwave songs") == {"new": True, "genre": "darkwave", "shuffle": True}
    assert brain.music_grammar("hey skippy, play goth songs downstairs on discovery mode") == {
        "mode": "discovery", "genre": "goth", "shuffle": True, "speaker": "downstairs"}


def test_whisper_spellings_of_bilby_and_the_old_plain_forms_still_work():
    assert brain.music_grammar("Bill be discovery everywhere.") == {"mode": "discovery", "shuffle": True, "speaker": "everywhere"}
    assert brain.music_grammar("Tell Bill B to play music everywhere.") == {"what": "everything", "shuffle": True, "speaker": "everywhere"}
    assert brain.bilby_route("bilby") == {"what": "everything", "shuffle": True}


def test_the_other_parts_of_the_grammar():
    g = brain.music_grammar
    assert g("play my library") == {"mode": "favorites", "shuffle": True}
    assert g("play new wave music") == {"genre": "new wave", "shuffle": True}             # "new wave" is a genre, not "new"
    assert g("play some music by the cure in the theater") == {"artist": "cure", "speaker": "downstairs"}
    assert g("play my gallows playlist upstairs") == {"playlist": "gallows", "speaker": "upstairs"}
    assert g("shuffle the playlist the box") == {"playlist": "box", "shuffle": True}
    assert g("play the album disintegration") == {"album": "disintegration"}
    assert g("play bauhaus on discovery") == {"mode": "discovery", "artist": "bauhaus", "shuffle": True}
    for not_this in ("pause the music", "what music is this", "play Pink Floyd upstairs", "play spotify upstairs", "turn the music down",
                     "pay the bill by friday", "play the news"):
        assert g(not_this) is None, not_this


def test_each_place():
    for said, place in (("everywhere", "everywhere"), ("in the whole house", "everywhere"), ("on both floors", "everywhere"),
                        ("upstairs", "upstairs"), ("in the living room", "upstairs"), ("on the enclave", "upstairs"),
                        ("downstairs", "downstairs"), ("in the theater", "downstairs"), ("on the sony", "downstairs")):
        assert brain.music_grammar(f"play goth music {said}")["speaker"] == place, said
    assert "speaker" not in brain.music_grammar("play goth music")                      # no place: where the room plays, as before


def test_discovery_mixes_a_favorite_in_about_one_in_four_like_the_car():
    all_ = [f"lib/{i}.mp3" for i in range(300)]
    favs = [f"fav/{i}.mp3" for i in range(100)]
    q = mixes.build("discovery", {"all": all_, "favorites": favs, "suggested": [S1, S2]}, random.Random(1), 200)
    assert len(q) == 200 and len(set(q)) == 200
    assert all(q[i].startswith("fav/") for i in range(3, 200, 4))                     # drive-pure.js: (n + 1) % FAV_EVERY == 0
    assert sum(x.startswith("fav/") for x in q) == 50
    q = mixes.build("favorites", {"all": all_, "favorites": favs[:5], "liked": [S1, S2]}, random.Random(1))
    assert sorted(q) == sorted(favs[:5] + [S1, S2])                                  # favorites: his favorites and Spotify liked, nothing else


CATALOG = {
    "Bauhaus/In the Flat Field/Dark Entries.mp3": ("Dark Entries", "Bauhaus", "In the Flat Field", "Gothic Rock", 100),
    "Christian Death/Only Theatre/Romeo's Distress.mp3": ("Romeo's Distress", "Christian Death", "Only Theatre", "Deathrock", 200),
    "Clan of Xymox/Medusa/Louise.mp3": ("Louise", "Clan of Xymox", "Medusa", "Darkwave", 900),
    "Clan of Xymox/Medusa/Michelle.mp3": ("Michelle", "Clan of Xymox", "Medusa", "", 950),            # untagged: takes Xymox's genre
    "Lebanon Hanover/Tomb/Gallowdance.mp3": ("Gallowdance", "Lebanon Hanover", "Tomb", "Coldwave", 990),
    "Pink Floyd/DSOTM/Money.mp3": ("Money", "Pink Floyd", "DSOTM", "Classic Rock", 300),
    "Front 242/Tyranny/Rhythm of Time.mp3": ("Rhythm of Time", "Front 242", "Tyranny", "Techno-Industrial", 400),
}


def test_genre_families():
    assert mixes.genre_match("Gothic Rock", "goth") and mixes.genre_match("Deathrock", "goth") and mixes.genre_match("Post-Punk", "goth")
    assert not mixes.genre_match("Rock", "goth") and not mixes.genre_match("Classic Rock", "goth")
    assert mixes.genre_match("Coldwave", "darkwave") and mixes.genre_match("Dark Wave", "darkwave") and not mixes.genre_match("Gothic Rock", "darkwave")
    assert mixes.genre_match("Techno-Industrial", "industrial") and mixes.genre_match("Darkwave \\ EBM \\ Industrial", "ebm")
    q, note, _ = mixes.resolve({"genre": "goth", "shuffle": True}, CATALOG, rnd=random.Random(2))
    assert set(q) == {p for p in CATALOG if not p.startswith(("Pink", "Front"))} and note == ""
    q, _, _ = mixes.resolve({"genre": "darkwave", "new": True, "shuffle": True}, CATALOG, rnd=random.Random(2))
    assert "Clan of Xymox/Medusa/Michelle.mp3" in q and "Bauhaus/In the Flat Field/Dark Entries.mp3" not in q


def test_new_is_the_most_recently_added(monkeypatch):
    monkeypatch.setattr(mixes, "NEW_COUNT", 2)
    q, _, _ = mixes.resolve({"new": True, "shuffle": True}, CATALOG, rnd=random.Random(3))
    assert set(q) == {"Lebanon Hanover/Tomb/Gallowdance.mp3", "Clan of Xymox/Medusa/Michelle.mp3"}


def test_a_filter_that_matches_nothing_says_so_and_plays_the_closest():
    q, note, _ = mixes.resolve({"genre": "polka", "shuffle": True}, CATALOG, rnd=random.Random(4))
    assert set(q) == set(CATALOG) and note == "Nothing polka in his library, so here is the closest."
    q, note, _ = mixes.resolve({"genre": "goth", "artist": "Nobody At All", "shuffle": True}, CATALOG, rnd=random.Random(4))
    assert q and all(mixes.genre_match(mixes.genre_of(p, CATALOG, mixes.artist_genres(CATALOG)), "goth") for p in q)
    assert "Nobody At All" in note
    q, note, _ = mixes.resolve({"mode": "favorites"}, CATALOG, favorites=[], rnd=random.Random(4))
    assert set(q) == set(CATALOG) and "favorites" in note


class FakeSpotify:
    def __init__(self):
        self.lists = {"night drive": {"playlist": {"name": "Night Drive"},
                                      "tracks": [{"uri": S1, "title": "Dark Entries (2009 Remaster)", "artist": "Bauhaus"},
                                                 {"uri": S2, "title": "Never Owned", "artist": "Somebody"}]}}

    def mix(self):
        return {"liked": [{"uri": S1, "title": "Dark Entries", "artist": "Bauhaus"}, {"uri": S3, "title": "Liked", "artist": "Clan of Xymox"}],
                "suggested": [{"uri": S2, "title": "Never Owned", "artist": "Somebody"}]}

    def playlist(self, name):
        if name.lower() not in self.lists:
            raise RuntimeError("no such playlist")
        return self.lists[name.lower()]


def test_his_playlists_are_one_thing_and_bilby_plays_what_he_owns():
    lists = {"Gallows 03-25-26": ["Lebanon Hanover/Tomb/Gallowdance.mp3", "Bauhaus/In the Flat Field/Dark Entries.mp3", "Gone/Not/There.mp3"]}
    q, note, ordered = mixes.resolve({"playlist": "gallows"}, CATALOG, playlists=lists)
    assert q == lists["Gallows 03-25-26"][:2] and ordered and note == "his playlist Gallows 03-25-26"   # Traktor's DJ set, in its order
    q, note, ordered = mixes.resolve({"playlist": "night drive"}, CATALOG, spotify=FakeSpotify(), playlists=lists)
    assert q == ["Bauhaus/In the Flat Field/Dark Entries.mp3", S2] and ordered                        # Spotify's list, Bilby's copy first
    assert note == "his playlist Night Drive, 1 of them from Bilby"
    q, _, _ = mixes.resolve({"mode": "favorites"}, CATALOG, favorites=["Pink Floyd/DSOTM/Money.mp3"], spotify=FakeSpotify(), rnd=random.Random(5))
    assert sorted(q) == sorted(["Pink Floyd/DSOTM/Money.mp3", "Bauhaus/In the Flat Field/Dark Entries.mp3", S3])
    q, _, _ = mixes.resolve({"mode": "favorites", "genre": "darkwave"}, CATALOG, favorites=["Pink Floyd/DSOTM/Money.mp3"],
                            spotify=FakeSpotify(), rnd=random.Random(5))
    assert q == [S3]                                                                                   # a Spotify row has its artist's genre in Bilby


def test_traktor_playlists_are_read_from_the_nml(tmp_path):
    nml = tmp_path / "collection.nml"
    nml.write_text('<?xml version="1.0"?><NML><COLLECTION><ENTRY TITLE="x"/></COLLECTION><PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES>'
                   '<NODE TYPE="PLAYLIST" NAME="Gallows 03-25-26"><PLAYLIST><ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Macintosh HD/:Users/:a/:corrected_music/:'
                   'Bauhaus/:In the Flat Field/:Dark Entries.mp3"/></ENTRY></PLAYLIST></NODE>'
                   '<NODE TYPE="PLAYLIST" NAME="_LOOPS"><PLAYLIST><ENTRY><PRIMARYKEY TYPE="TRACK" KEY="x/:corrected_music/:a.mp3"/></ENTRY></PLAYLIST></NODE>'
                   '</SUBNODES></NODE></PLAYLISTS></NML>', encoding="utf-8")
    got = mixes.traktor_playlists(str(nml), tmp_path / "state")
    assert got == {"Gallows 03-25-26": ["Bauhaus/In the Flat Field/Dark Entries.mp3"]}
    assert mixes.traktor_playlists(str(nml), tmp_path / "state") == got                                # from the cache
    assert mixes.pick_playlist(got, "gallows") == "Gallows 03-25-26" and mixes.pick_playlist(got, "polka") is None


# ── the hub's play_music with the grammar, on the house stereo ──
class Media:
    async def catalog(self):
        return CATALOG


class Box:
    def __init__(self):
        self.media, self.playing, self.queue = Media(), False, []

    async def play_queue(self, q):
        self.queue = list(q)

    async def pause(self):
        self.playing = False

    def title(self):
        return ("T", "A")


class Player:
    spotify = None


class House:
    def __init__(self):
        self.env, self.playing, self.box, self.player, self.moved, self.played = {}, False, Box(), Player(), None, None

    def move(self, kinds):
        self.moved = set(kinds)

    def where(self):
        return set(self.moved or ()), set()

    async def play_tracks(self, tracks, mixed=False, limit=500, premixed=False):
        self.played = (list(tracks), mixed, premixed)
        return len(tracks)

    async def stop(self):
        pass


class Room:
    def __init__(self):
        self.music, self.book, self.name, self.last_media = Box(), None, "theater", "music"


def test_the_hub_plays_the_grammar_on_the_place_named_without_asking():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    room, house = Room(), House()
    _, run = hub.music_tools(lambda: room, loop, favorites=None, stereo=lambda: house)
    said = run("play_music", brain.music_grammar("hey skippy, play goth songs downstairs on discovery mode"))
    tracks, mixed, premixed = house.played
    assert set(tracks) == {p for p in CATALOG if not p.startswith(("Pink", "Front"))} and mixed and premixed
    assert house.moved and room.last_media == "stereo"
    assert said.startswith("Playing 5 tracks of goth on discovery ") and "?" not in said
    said = run("play_music", {"genre": "polka", "speaker": "upstairs", "shuffle": True})
    assert said.startswith("Nothing polka in his library, so here is the closest. Playing 7 tracks")
    said = run("play_music", {"what": "polka", "speaker": "upstairs"})                     # a free word that is no name: the same, not "nothing matches"
    assert said.startswith("Nothing polka in his library, so here is the closest.")
    loop.call_soon_threadsafe(loop.stop)
