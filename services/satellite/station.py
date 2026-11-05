"""The house radio's commands (radio.py runs them; the hubs, the portal's Connect and the car only ask). One station, one queue, one tune
for every speaker that is on (the Captain, 2026-11-01: "There is one tap for music everywhere, and it's playing the same tune for everyone,
like a radio station. The hubs can change it, or add to it, but they are not the players").

`run(house, name, args, room, favorites)` is the old hub music tools' logic, moved here whole, with the pods' own players gone: `house` is
the radio's stereo.StereoMusic (Bilby, the DJ, Spotify), `room` only says where "here" is. Every answer is a sentence Skippy says.

  play_music   his grammar (mode + filters, mixes.resolve), favorites, everything, an artist / album / song; queue=true adds after what plays;
               speaker = here (the asking room's place) | stereo (where it plays now) | everywhere | upstairs | downstairs | a room
  music        pause / resume / next / previous / what / favorite / tired / louder / quieter / volume
  speakers     what=music: which speakers play the station (the voice is the hub's own business)
  dj_mode      on / off
  spotify      play / add / like
  audiobook    play / pause / resume / next_chapter / previous_chapter / back / forward / where / sleep / speed / list: a book is an item of the queue
"""
from __future__ import annotations

import asyncio
import random
import re

GRAMMAR_KEYS = ("mode", "genre", "playlist", "artist", "album", "song", "new")
STEREO_LEVELS = 10
POD = "pod:"                                      # a pod's own speaker as one of the station's speakers: "pod:<hub room>"


def grammar_of(args) -> dict:
    """The parts of play_music's arguments that are his grammar's mode and filters (mixes.resolve), not the old free `what`."""
    return {k: v for k, v in (args or {}).items() if k in GRAMMAR_KEYS and v}


def mix_words(args) -> str:
    import mixes                                                # noqa: PLC0415
    d = mixes.describe(grammar_of(args))
    return f" of {d}" if d else ""


def place_words(env: dict, kinds) -> str:
    """The station's speakers in his words: the router outputs by their place, a pod by its room."""
    import house_layout                                         # noqa: PLC0415
    import speaker_out                                          # noqa: PLC0415
    kinds = set(kinds)
    outs = {k for k in kinds if not k.startswith(POD)}
    words = [speaker_out.place_words(env, outs)] if outs else []
    for k in sorted(kinds - outs):
        try:
            room = house_layout.default().room_of_pod(k[len(POD):]) or k[len(POD):]
        except Exception:                                       # noqa: BLE001
            room = k[len(POD):]
        if not any(room in w for w in words):
            words.append(f"the {room}")
    return " and ".join(w for w in words if w)


def where_said(house) -> str:
    """'downstairs and upstairs' for where the station really plays now (only speakers that are on): never a place the sound didn't go."""
    open_, _ = house.where()
    words = place_words(house.env or {}, open_)
    return f"{'everywhere' if open_ and len(words.split(' and ')) > 1 else words}" if words else "on the house stereo"


def not_there(house) -> str:
    """' Upstairs isn't on, so not there.' for a place asked for whose speaker is off (the Enclave switched off)."""
    _, closed = house.where()
    words = place_words(house.env or {}, closed)
    return f" {words[:1].upper()}{words[1:]} isn't on, so not there." if words else ""


def place(house, kinds) -> None:
    """A place asked for ("upstairs", "everywhere", "here") as the speakers' switches: exactly those on, the other stereos off (HouseRadio.place). A plain
    StereoMusic (the old hub, a test) just moves."""
    (getattr(house, "place", None) or house.move)(kinds)


def resolve_device(house, word: str) -> list[str]:
    """The speakers a word names, as the switches' ids: "the Sony" -> ["hdmi"], "the Enclave" -> ["enclave"], "upstairs" -> the stereos there, "the living room
    box" -> ["pod:living"]. [] when it names none."""
    import speaker_out                                          # noqa: PLC0415
    w = " ".join(re.findall(r"[a-z0-9]+", (word or "").lower()))
    outs = getattr(house, "outputs", None)
    pods = [i for i in (outs.ids() if outs is not None else []) if i.startswith(POD)]
    if re.search(r"\b(?:box|pod|boxes|pods)\b", w):
        named = [i for i in pods if i[len(POD):].lower() in w.split()]
        return named or (pods if len(pods) == 1 else [])
    return list(speaker_out.place_kinds(house.env or {}, w) or [])


def _truthy(v, default: bool = True) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "off", "no", "0", "")
    return default if v is None else bool(v)


async def device_switch(house, args: dict) -> str:
    """`speakers` with a device: one speaker's switch ("turn the Sony off", "play on the Sony too"). Off means off: nothing covers for it."""
    import outputs as outputs_mod                               # noqa: PLC0415
    ids = resolve_device(house, str(args.get("device") or ""))
    switch = getattr(house, "set_switch", None)
    if not ids or switch is None:
        return "I don't know that speaker: the Sony, the Enclave, or a room's box."
    on = _truthy(args.get("on"))
    for i in ids:
        switch(i, on)
    names = " and ".join(f"the {outputs_mod.name_of(i)}" for i in ids)
    if not house.speakers:
        tail = " No speaker is on now" + (", so the music waits until one is." if house.playing else ".")
    else:
        tail = f" The music plays {where_said(house)} now." if house.playing else f" It will play {where_said(house)}."
    return f"Done: {names} {'on' if on else 'off'}." + tail + not_there(house)


def here_kinds(env: dict, room: str) -> list[str] | None:
    """The asking room's speakers: its place's outputs (house_layout.json: the living room is the Enclave), else that room's own pod."""
    import house_layout                                         # noqa: PLC0415
    import speaker_out                                          # noqa: PLC0415
    if not room:
        return None
    try:
        place = house_layout.default().room_of_pod(room) or room
    except Exception:                                           # noqa: BLE001
        place = room
    return speaker_out.place_kinds(env, place) or [POD + room]


def speaker_kinds(env: dict, speaker: str, room: str = "") -> list[str] | None:
    """play_music's `speaker` as the station's speakers: None for "stereo" (where it plays now) and for a word that is no place."""
    import speaker_out                                          # noqa: PLC0415
    s = (speaker or "here").strip().lower()
    if s == "stereo":
        return None
    if s == "here":
        return here_kinds(env, room)
    return speaker_out.place_kinds(env, s)


_STEREO_WORDS = ("stereo", "the stereo")                        # where it plays now, not a place
_PREPS = ("on", "in", "at", "to", "through", "over", "via", "using", "from")
_SURE_PLACES = ("upstairs", "downstairs", "everywhere", "enclave", "the enclave", "whole house", "the whole house", "all rooms")


def clean_what(env: dict, what: str) -> tuple[str, str]:
    """(`what` without the places and the times in it, the speaker word those places gave or ""): the model puts "on the Enclave" or "today" into a free `what`
    ("heavy metal on enclave": the Captain, 2026-11-04, a search for that found nothing and he was told the library was empty of metal). A place is a place with a
    speaker (house_layout.json's outputs) that follows a preposition ("on the enclave") or is plainly one ("upstairs"): never the "Living" of "Living Colour" or the
    "Theatre" of "Theatre of Tragedy". "on the stereo" is where it plays now (speaker "stereo": stay), never the layout's alias "the stereo", which is the Sony."""
    import house_layout                                         # noqa: PLC0415
    lay, have, place, stereo = house_layout.default(), house_layout.words(what.lower()), "", False
    for _ in range(3):
        rooms, i, n, have = lay.find(" ".join(have))
        if rooms is None:
            break
        phrase = " ".join(have[i:i + n])
        before = have[i - 1] if i else ""
        prepped = before in _PREPS or (before == "the" and i > 1 and have[i - 2] in _PREPS)
        if not ((prepped and (lay.outputs(phrase) or phrase in _STEREO_WORDS)) or phrase in _SURE_PLACES):
            break
        if phrase in _STEREO_WORDS:
            stereo = True
        else:
            place = place or phrase
        j = i - 1 if i and before == "the" and not phrase.startswith("the ") else i      # the preposition and the article that led into it go too
        j = j - 1 if j and have[j - 1] in _PREPS else j
        del have[j:i + n]
    import mixes                                                # noqa: PLC0415
    rest = " ".join(mixes.TAIL_WORDS.sub(" ", " ".join(have)).split())
    while True:                                                 # what the place leaves dangling: "heavy metal on", "in the"
        n2 = re.sub(r"^(?:on|in|to|through|at|the|for)\s+|\s+(?:on|in|to|through|at|the|for)$", "", rest)
        if n2 == rest:
            return rest, place or ("stereo" if stereo else "")
        rest = n2


async def grammar_queue(args: dict, house, favorites) -> tuple[list[str], str, bool]:
    """mixes.resolve with the radio's sources: the gate's catalog, his favorites, Spotify (liked, suggestions, playlists: the station's client)
    and Traktor's playlists (the DJ's collection.nml, cached in state/dj)."""
    import dj                                                   # noqa: PLC0415
    import mixes                                                # noqa: PLC0415
    catalog = await house.media.catalog()
    env = getattr(house, "env", None) or {}
    spot = getattr(house.player, "spotify", None)
    if spot is not None and getattr(house.player, "spotify_down", lambda: False)():
        spot = None

    def work():
        favs, tired, played = favorites.pool() if favorites is not None else ([], set(), set())   # his pool, what he is tired of, what he heard
        lists = mixes.traktor_playlists(env.get("BILBY_TRAKTOR_NML", dj.DEFAULT_NML), dj.STATE) if args.get("playlist") else {}
        return mixes.resolve(args, catalog, favs if args.get("mode") else [], spot, lists, tired=tired, played=played)
    return await asyncio.to_thread(work)


async def find(args: dict, house, favorites) -> tuple[list[str], str, bool, bool, str]:
    """(tracks, a note to say first, mixed, premixed, what to say when there are none) for a play_music request."""
    import music as music_mod                                   # noqa: PLC0415
    what = str((args or {}).get("what") or ("" if grammar_of(args) else "everything"))
    mixed = bool((args or {}).get("shuffle"))
    plain = " ".join(re.findall(r"[a-z]+", what.lower()))
    note, premixed = "", False
    if grammar_of(args):                                        # his grammar: mode + filters (mixes.py); Spotify rows he owns play from Bilby
        found, note, ordered = await grammar_queue(args, house, favorites)
        mixed, premixed = not ordered, not ordered
        return found, note, mixed, premixed, "Nothing in his library to play right now (the music gate is not answering)."
    if re.fullmatch(r"(?:my |his )?(?:favou?rites?|favou?rite songs?|liked songs)", plain):   # his one pool: favorites + liked, pruned
        if mixed:                                               # "shuffle my favorites": the pool's mix (favorites + liked)
            found, note, _ordered = await grammar_queue({**(args or {}), "mode": "favorites", "shuffle": True}, house, favorites)
            premixed = True
        else:                                                   # "play my favorites": the list in its order, newest first
            favs, tired, _played = await asyncio.to_thread(favorites.pool) if favorites is not None else ([], set(), set())
            found = [f for f in favs if f not in tired]
        return found, note, mixed, premixed, "His favorites list is empty so far."
    if re.fullmatch(r"(?:everything|all|all (?:of )?(?:my |his )?music|(?:my |his |the )?(?:whole |entire )?(?:library|collection))", plain):
        found, mixed = list(await house.media.catalog()), True
        if favorites is not None:                               # never a song he is tired of
            tired = (await asyncio.to_thread(favorites.pool))[1]
            found = [t for t in found if t not in tired]
        return found, note, mixed, premixed, f"Nothing in his library matches {what!r}."
    import mixes                                                # noqa: PLC0415
    clean, place = clean_what(getattr(house, "env", None) or {}, what)                # a place and a time said inside the words ("heavy metal on enclave")
    if place and clean and isinstance(args, dict) and not args.get("speaker"):
        args["speaker"] = place                                 # it plays there (play_music reads the speaker after this)
    genre = mixes.genre_phrase(clean)
    if genre:                                                   # "heavy metal", "some loud metal": a genre before it is a title search (3 songs by title, 1,941 by tag)
        found, note, _ordered = await grammar_queue({**(args or {}), "genre": genre, "shuffle": True}, house, favorites)
        return found, note, True, True, f"Nothing in his library matches {genre!r}."
    found = music_mod.search(await house.media.catalog(), what)
    if not found and what.strip():                              # not a name: maybe without the place and the time, maybe a genre ("polka"); else the closest is the shuffle, said in one line
        if place and not clean and isinstance(args, dict) and not args.get("speaker"):
            args["speaker"] = place                             # the words were only a place ("upstairs"), and no song or band has that name: it plays there
        if clean and clean != what.strip().lower():
            found = music_mod.search(await house.media.catalog(), clean)
        if not found:
            found, note, _ordered = await grammar_queue({**(args or {}), "genre": clean or what, "shuffle": True}, house, favorites)
            mixed = premixed = True
        what = clean or what
    return found, note, mixed, premixed, f"Nothing in his library matches {what!r}."


async def play_music(house, args: dict, room: str, favorites) -> str:
    import dj                                                   # noqa: PLC0415
    import music as music_mod                                   # noqa: PLC0415
    found, note, mixed, premixed, none = await find(args, house, favorites)
    if not found:
        return none
    if mixed and not premixed:
        found = random.sample(found, len(found))[:music_mod.MAX_QUEUE]
    said = (note + " ") if note else ""
    if (args or {}).get("queue") and house.playing:             # one queue: Bilby's tracks after what plays now, Spotify's included
        await house.box.append(found)
        return f"Queued {len(found)} track{'s' if len(found) != 1 else ''} after this on the house radio."
    said_where = str((args or {}).get("speaker") or "").strip()
    kinds = speaker_kinds(house.env or {}, said_where, room) if said_where else None
    if kinds:
        place(house, kinds)                                     # "everywhere", "upstairs", "the office": the speakers' switches from now on. No place said: the switches stay
    n = await house.play_tracks(found, mixed, premixed=premixed)
    title, artist = house.box.title()
    return (f"{said}Playing {n} track{'s' if n != 1 else ''}{mix_words(args)} {where_said(house)}"
            + (" in DJ mode" if dj.enabled(house.env) else "")
            + f", starting with {title}" + (f" by {artist}." if artist else ".") + not_there(house) + not_listening(house))


def kind_of(box) -> str:
    """What the station's current item is: song, spotify or book (a plain box without the notion is a song)."""
    kind = getattr(box, "kind", None)
    return kind() if callable(kind) else "song"


def now_playing(house) -> str:
    """The `music` tool's "what": what the station is playing now (a song, a Spotify track or an audiobook)."""
    if house.playing and house.box.track():
        title, artist = house.box.title()
        if kind_of(house.box) == "book":
            w = house.player.books.where() if getattr(house.player, "books", None) is not None else None
            part = f", chapter {w['chapter']} of {w['chapters']}" if w else ""
            return f"Playing on the house radio: the audiobook {title}" + (f" by {artist}" if artist else "") + f"{part}."
        return f"Playing on the house radio: {title}" + (f" by {artist}." if artist else ".")
    return "Nothing is playing."


def not_listening(house) -> str:
    """' But no speaker is on, so it waits ...' when the station was told to play and nobody would hear it (radio.py's listeners)."""
    note = getattr(house, "listening_note", None)
    return note() if note is not None else ""


async def audiobook(house, args: dict, room: str = "") -> str:
    """The `audiobook` tool: his books on the station. audiobooks.py is the engine (place, chapters, speed, sleep timer); the book is the queue's item
    "book:<id>", so it starts, pauses, resumes and skips like any other item, plays on the speakers that are on, and a hub never owns it."""
    import audiobooks                                           # noqa: PLC0415
    engine = getattr(house.player, "books", None)
    if engine is None:
        return "There are no audiobooks set up on the house radio."
    args = dict(args or {})
    if str(args.get("action") or "") == "pause" and not audiobooks.is_item(house.box.track()):
        await house.box.pause()                                 # the station's one pause, whatever it plays
        return "Done: paused the house radio."
    said = await audiobooks.run_tool(engine, args, transport=house.box.book_transport())
    return said + (not_listening(house) if str(args.get("action") or "") in ("play", "resume") else "")


def stereo_volume(house, action: str, level=None) -> str:
    """The station's own loudness, in tenths of full (the router's gain on Bilby and Spotify; ducking under Skippy is separate). Full is
    where the Sony's and the Enclave's knobs were set, so "louder" past full is theirs to do."""
    if not hasattr(house, "set_level"):
        return "The house stereo's volume is on its own knob."
    now = max(1, min(STEREO_LEVELS, round(getattr(house, "level", 1.0) * STEREO_LEVELS)))
    if action == "volume":
        try:
            want = int(level)
        except (TypeError, ValueError):
            return f"The house stereo is at {now} of {STEREO_LEVELS}."
    else:
        want = now + (2 if action == "louder" else -2)
    want = max(1, min(STEREO_LEVELS, want))
    if action == "louder" and now >= STEREO_LEVELS:
        return f"Not louder: the house stereo is already at full ({STEREO_LEVELS} of {STEREO_LEVELS}); past that is the speaker's own knob."
    house.set_level(want / STEREO_LEVELS)
    return f"Done: the house stereo is at {want} of {STEREO_LEVELS}."


async def music(house, args: dict, room: str, favorites) -> str:
    import music as music_mod                                   # noqa: PLC0415
    action = str((args or {}).get("action") or "")
    box = house.box
    step = {"pause": box.pause, "resume": box.resume, "next": box.next, "previous": box.prev}.get(action)
    if step is not None:
        await step()
        return f"Done: {action} on the house radio." + (not_listening(house) if action == "resume" else "")
    if action == "tired":                                       # "I'm tired of this song": out of his pool and every mix, and on to the next one
        if favorites is None:
            return "His favorites list is not set up here."
        said = await asyncio.to_thread(favorites.tire, room, music_mod.device_id("stereo"))
        if said.startswith("Dropped"):
            await box.next()
        return said
    if action == "favorite":                                    # "Skippy, add this song to my favorites" (portal/favorites.py)
        if favorites is None:
            return "His favorites list is not set up here."
        return await asyncio.to_thread(favorites.mark, room, music_mod.device_id("stereo"))
    if action == "what":
        return now_playing(house)
    if action in ("louder", "quieter", "volume"):
        return stereo_volume(house, action, (args or {}).get("level"))
    return "I cannot do that to the music."


async def spotify_go(house, args: dict) -> str:
    """The `spotify` tool: his Spotify on the station (spotify.py, stereo.py)."""
    import spotify as spotify_mod                               # noqa: PLC0415
    client = getattr(house.player, "spotify", None)
    if client is None:
        return "Spotify is not set up on the house radio."
    action = str(args.get("action") or "play")
    try:
        if action == "play":
            what = str(args.get("what") or "").strip()
            if not what:
                return "Say what to play on Spotify."
            tracks, how = await asyncio.to_thread(spotify_mod.queue_for, client, what)
            if not tracks:
                return f"Spotify found nothing for {what!r}."
            if args.get("shuffle"):
                tracks = random.sample(tracks, len(tracks))
            if args.get("queue") and house.playing:
                n = await house.play_spotify(tracks, queue=True)
                return f"Queued {n} Spotify track{'s' if n != 1 else ''} from {how} after this on the house radio."
            n = await house.play_spotify(tracks)
            title, artist = house.box.title()
            return (f"Playing {n} Spotify track{'s' if n != 1 else ''} from {how} on the house radio, starting with {title}" + (f" by {artist}." if artist else ".")
                    + not_listening(house))
        uri, label = await asyncio.to_thread(spotify_now, client, house)
        if not uri:
            return label
        if action == "add":
            playlist = str(args.get("playlist") or "").strip()
            if not playlist:
                return "Which of his Spotify playlists?"
            got = await asyncio.to_thread(client.add_to_playlist, playlist, [uri])
            return f"Added {label} to his Spotify playlist {got.get('name') or playlist}."
        if action == "like":
            await asyncio.to_thread(client.save, [uri])
            return f"Saved {label} to his Spotify liked songs."
        return "I cannot do that with Spotify."
    except spotify_mod.SpotifyError as e:
        return f"Spotify said no: {e}"


def spotify_now(client, house) -> tuple[str, str]:
    """(the Spotify URI of the song playing now, how to name it), or ("", why not). A Bilby song is looked up on Spotify by its title and artist."""
    import spotify as spotify_mod                               # noqa: PLC0415
    box = house.box
    if box.playing and kind_of(box) == "book":
        return "", "An audiobook is playing, so there is no song to add."
    if box.playing and box.track():
        title, artist = box.title()
        if spotify_mod.is_uri(box.track()):
            return box.track(), f"{title}" + (f" by {artist}" if artist else "")
        hits = client.search(f"track:{title} artist:{artist}" if artist else title, "track", 3)
        if not hits:
            return "", f"Spotify has no {title}" + (f" by {artist}." if artist else ".")
        h = hits[0]
        return h["uri"], f"{h.get('title')} by {h.get('artist')} (Spotify's copy)"
    return "", "Nothing is playing, so there is no song to add."


async def run(house, name: str, args: dict, room: str = "", favorites=None) -> str:
    """One command, from any hub, the portal or the car. Never raises: a failure is a sentence."""
    args = dict(args or {})
    if name == "dj_mode":                                       # house-wide, kept in state/dj.json (the vault's BILBY_DJ is the default)
        import dj                                               # noqa: PLC0415
        on = bool(args.get("on"))
        dj.set_enabled(on)
        return ("Done: DJ mode is on. His tracks on the house radio now mix into each other on the beat, from the next change of track."
                if on else "Done: DJ mode is off. On the house radio one track now ends and the next starts.")
    if name == "speakers":
        if args.get("device"):                                  # one speaker's switch: "turn the Sony off", "play on the Sony too"
            return await device_switch(house, args)
        kinds = speaker_kinds(house.env or {}, str(args.get("place") or ""), room)
        if not kinds:
            return "I don't know that place: everywhere, downstairs, upstairs, or a room."
        place(house, kinds)
        if args.get("play") and not house.playing:              # "play it upstairs" with nothing playing: the library, shuffled, there
            n = await house.shuffle()
            if n:
                return f"Nothing was playing, so the library is shuffled: {n} tracks {where_said(house)}." + not_there(house) + not_listening(house)
        return f"Done: the music plays {where_said(house)} from now on." + not_there(house)
    if name == "music":
        return await music(house, args, room, favorites)
    if name == "spotify":
        return await spotify_go(house, args)
    if name == "audiobook":
        return await audiobook(house, args, room)
    if name == "play_music":
        return await play_music(house, args, room, favorites)
    return f"The house radio has no {name!r}."
