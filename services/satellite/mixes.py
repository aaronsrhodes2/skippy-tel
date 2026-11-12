"""The Captain's music grammar's queues: wake + function (music / Bilby) + mode + filters + location, as one list of tracks for play_music.

Every item is a Bilby catalog path or a Spotify URI ("spotify:track:..."), the same mixed queue the house stereo already plays (stereo.py, dj.py
hand a Spotify row over to Spotify). His playlists are one thing wherever they live: a Traktor playlist (his DJ sets in collection.nml), or a
Spotify playlist; and every Spotify row he also owns plays from Bilby (prefer_bilby: same artist and title), so Spotify serves the list and
Bilby plays it whenever it can.

Modes (build(), the car's Bilby Shuffle, portal/static/drive-pure.js buildQueue, step for step, so the car and the house mix alike; the
Captain's words of 2026-11-01):
  favorites   his ONE favorites pool: his favorites and his Spotify liked songs, minus the ones he is tired of ("favorite music", "my library";
              the car's "shuffle library"). He grows it as it plays ("favorite this", a Bilby or a Spotify song) and prunes it ("I'm tired
              of this song": portal/favorites.py tire)
  discovery   what he hasn't heard: his collection's unplayed songs (never a favorite, a liked, a played or a tired one: portal/favorites.py
              played, every song heard past 30 s on any player), and one of Spotify's new songs (his liked artists' other songs) in about
              every SUGGEST_EVERY, kept inside his normal genres: the genre tags of his pool
  ""          the plain library

"new" (filters["new"]): what was ADDED to Bilby most recently (the catalog's date added, the file's mtime Syncthing keeps): the NEW_COUNT newest of
what else matches. In discovery it also keeps Spotify's suggestions, which are new to him by construction. It never means Spotify's new releases.

Genres: the catalog's genre tag (the gate reads it from the file; the music-collection pipeline consolidates them, stage8i), split on , / \\ ;
and matched by family (GENRES: "goth" is gothic rock, deathrock, batcave, post-punk ...). A track with no genre takes its artist's most common one
(artist_genres), so nothing needs a second nightly to keep it fresh: the gate's own rescan is the refresh. A Spotify row has the genre of its
artist in Bilby (Spotify's Development Mode gives no genres worth a call per row); an artist Bilby doesn't know is left out of a genre filter.
"""
from __future__ import annotations

import json
import os
import random
import re
import unicodedata
from collections import Counter
from pathlib import Path

SUGGEST_EVERY = 4                     # discovery: one of Spotify's new songs in about every four (drive-pure.js SUGGEST_EVERY)
QUEUE_MAX = 400                       # drive-pure.js QUEUE_MAX
NEW_COUNT = 300

# family word -> the genre tags it covers (lower case, spaces and hyphens folded by _g). A word not here matches tags that contain it.
GENRES: dict[str, set[str]] = {
    "goth": {"gothic rock", "gothic", "goth", "goth rock", "deathrock", "death rock", "batcave", "post-punk", "post punk", "darkwave", "dark wave",
             "ethereal wave", "ethereal", "coldwave", "cold wave"},
    "darkwave": {"darkwave", "dark wave", "coldwave", "cold wave", "ethereal wave", "ethereal", "minimal wave", "minimal synth", "neue deutsche todeskunst"},
    "deathrock": {"deathrock", "death rock", "batcave"},
    "post-punk": {"post-punk", "post punk", "coldwave", "cold wave"},
    "industrial": {"industrial", "ebm", "electro-industrial", "techno-industrial", "aggrotech", "industrial rock", "industrial metal", "electro"},
    "ebm": {"ebm", "electro-industrial", "aggrotech", "futurepop", "electro"},
    "synthpop": {"synthpop", "synth-pop", "synth pop", "synth rock", "futurepop", "electropop", "dance-pop"},
    "new wave": {"new wave", "new romantic", "synthpop", "synth-pop", "post-punk"},
    "electronic": {"electronic", "electronica", "idm", "dance", "techno", "house", "electro-techno", "acid house", "big beat", "alternative dance",
                   "leftfield", "progressive house", "dub", "trip-hop", "downtempo", "ambient techno"},
    "ambient": {"ambient", "illbient", "ambient techno", "new age", "dark ambient"},
    "metal": {"metal", "heavy metal", "melodic death metal", "groove metal", "funk metal", "death metal", "black metal", "thrash metal", "industrial metal"},
    "punk": {"punk", "punk rock", "psychobilly", "pop punk", "post-hardcore", "hardcore", "cowpunk", "alternative & punk"},
    "rock": {"rock", "classic rock", "hard rock", "alternative rock", "indie rock", "soft rock", "acoustic rock", "arena rock", "gothic rock"},
    "alternative": {"alternative rock", "alternative", "alternative & punk", "alternative pop", "dance-rock", "indie rock"},
    "80s": {"new wave", "synthpop", "post-punk", "new romantic"},
}
ALIASES = {"gothic": "goth", "gothic rock": "goth", "goth rock": "goth", "dark wave": "darkwave", "coldwave": "darkwave", "cold wave": "darkwave",
           "death rock": "deathrock", "post punk": "post-punk", "synth pop": "synthpop", "synth-pop": "synthpop", "electronica": "electronic",
           "eighties": "80s", "techno": "electronic", "heavy metal": "metal", "punk rock": "punk", "industrial music": "industrial"}
# Words that are a genre when he says them as a filter ("play goth songs"); the brain's parser reads these (brain.music_grammar).
GENRE_WORDS = sorted(set(GENRES) | set(ALIASES) | {"folk", "pop", "classical", "soundtrack", "comedy", "hip-hop", "hip hop", "jazz", "blues",
                                                  "country", "techno", "trance", "house", "trip-hop", "indie", "indie rock", "classic rock",
                                                  "hard rock", "alternative rock", "ethereal", "minimal wave", "aggrotech", "futurepop",
                                                  "psychobilly", "shoegaze", "dream pop"}, key=len, reverse=True)

# Words a request carries that are never part of a name: when and for whom ("play metal today", "for the maid"). station.clean_what drops them from a free `what` (brain.py peels its own tails off the END of a request, never out of a name).
TAIL_WORDS = re.compile(r"\b(?:(?:right |just )?now|today|tonight|tomorrow|later|for now|for a while|all (?:day|night|morning|afternoon|evening)(?: long)?"
                        r"|this (?:morning|afternoon|evening)|please|for (?:me|us|them|him|her|everyone|(?:the |my |our )?(?:maids?|housekeepers?|cleaners?|cleaning crew|guests?|party|kids?)))\b")
_GENRE_FILLER = r"(?:(?:some|any|more|good|great|loud|the|my|his|real|proper) )*"


# What he says about a feeling -> the mood tags that fit (music-collection/stage8l_mood_tags.py writes them into Traktor's collection; dj.mood_index reads them).
MOOD_WORDS: dict[str, set[str]] = {
    "relaxing": {"calm", "dreamy"}, "relaxed": {"calm", "dreamy"}, "chill": {"calm", "dreamy"}, "mellow": {"calm", "dreamy"},
    "calm": {"calm", "dreamy"}, "calming": {"calm", "dreamy"}, "peaceful": {"calm", "dreamy"}, "soothing": {"calm", "dreamy"}, "sleepy": {"calm", "dreamy"},
    "quiet": {"calm"}, "soft": {"calm", "dreamy"}, "gentle": {"calm", "dreamy"}, "dreamy": {"dreamy"},
    "sad": {"melancholy"}, "melancholy": {"melancholy"}, "melancholic": {"melancholy"}, "gloomy": {"melancholy", "dark"}, "wistful": {"melancholy", "tender"},
    "dark": {"dark"}, "moody": {"dark", "melancholy"}, "brooding": {"dark", "melancholy"},
    "happy": {"uplifting"}, "upbeat": {"uplifting", "party"}, "uplifting": {"uplifting"}, "cheerful": {"uplifting"}, "feel good": {"uplifting"}, "positive": {"uplifting"},
    "energetic": {"energetic"}, "high energy": {"energetic"}, "pumped": {"energetic"}, "workout": {"energetic"}, "hype": {"energetic", "party"},
    "angry": {"angry"}, "aggressive": {"angry", "energetic"}, "intense": {"angry", "energetic"},
    "romantic": {"tender"}, "tender": {"tender"}, "sweet": {"tender"}, "love": {"tender"},
    "party": {"party"}, "dance": {"party", "energetic"}, "danceable": {"party", "energetic"},
}
_MOOD_ALT = "|".join(sorted((re.escape(w) for w in MOOD_WORDS), key=len, reverse=True))


def mood_phrase(text: str) -> str | None:
    """The feeling `text` is, when it is nothing but one ("something relaxing", "some chill music for Khan", "sad songs"); None otherwise."""
    t = " ".join(re.findall(r"[a-z0-9&'-]+", (text or "").lower()))
    m = re.fullmatch(rf"{_GENRE_FILLER}(?:something |anything |stuff |music )?(?:that is |thats |that's )?({_MOOD_ALT})(?: music| songs| tracks| stuff| vibes?| tunes)?(?: for [a-z ]+)?", t)
    return m.group(1) if m else None


def genre_phrase(text: str) -> str | None:
    """The genre `text` is, when it is nothing but one ("heavy metal", "some loud metal music", "goth songs"); None otherwise. A bare genre is a genre before it is a title
    search: "heavy metal" found 3 songs by title (Pantera's "Heavy Metal Rules") against 1,941 tagged metal, "metal" 191 against 1,941 (2026-11-04)."""
    t = " ".join(re.findall(r"[a-z0-9&'-]+", (text or "").lower()))
    return next((g for g in GENRE_WORDS if re.fullmatch(rf"{_GENRE_FILLER}{re.escape(g)}(?: music| songs| tracks| stuff)?", t)), None)


def _g(s: str) -> str:
    return re.sub(r"[\s_-]+", " ", unicodedata.normalize("NFKC", s or "").lower()).strip()


def _n(s: str) -> str:
    """An artist or title, folded for matching across Bilby and Spotify: case, accents, punctuation, a leading 'the', '(remastered)' tails."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\s*[\(\[][^)\]]*(?:remaster|version|edit|mix|mono|stereo|live|deluxe)[^)\]]*[\)\]]", "", s)
    s = re.sub(r"\s+-\s+.*(?:remaster|version|edit|live).*$", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"^the ", "", s)


def feat_off(title: str) -> str:
    """'Song (feat. Someone)' / 'Song ft. Someone' / 'Song - Single Version' -> 'Song': the guest credit isn't part of the song's name."""
    t = re.sub(r"\s*[\(\[](?:feat\.?|ft\.?|featuring|with) [^)\]]*[\)\]]", "", title or "", flags=re.I)
    return re.sub(r"\s+(?:feat\.?|ft\.?|featuring) .*$", "", t, flags=re.I)


def tags_of(genre: str) -> list[str]:
    return [_g(t) for t in re.split(r"[,/\\;|]+", genre or "") if _g(t)]


def family(word: str) -> set[str]:
    w = _g(word)
    w = _g(ALIASES.get(w, w))
    return {_g(t) for t in GENRES.get(w, {w})}


def genre_match(genre: str, want: str) -> bool:
    """Does this track's genre tag fit the genre he said? By family first ('goth' covers 'Deathrock'), else the word inside a tag
    ('industrial' in 'Techno-Industrial')."""
    tags, fam = tags_of(genre), family(want)
    if any(t in fam for t in tags):
        return True
    w = _g(ALIASES.get(_g(want), want))
    return w not in GENRES and any(re.search(rf"\b{re.escape(w)}\b", t) for t in tags)


# ── the catalog ──
def row(info) -> tuple[str, str, str, str, int]:
    """(title, artist, album, genre, added) from media.catalog()'s tuple (older ones carry only the first three)."""
    t = (list(info) + ["", "", "", "", 0])[:5]
    return str(t[0] or ""), str(t[1] or ""), str(t[2] or ""), str(t[3] or ""), int(t[4] or 0)


def artist_genres(catalog: dict) -> dict[str, str]:
    """Each artist's most common genre tag in Bilby: the genre of his untagged tracks and of a Spotify row by that artist."""
    count: dict[str, Counter] = {}
    for info in catalog.values():
        _t, artist, _al, genre, _m = row(info)
        if genre:
            count.setdefault(_n(artist), Counter())[genre] += 1
    return {a: c.most_common(1)[0][0] for a, c in count.items()}


def genre_of(path: str, catalog: dict, by_artist: dict) -> str:
    _t, artist, _al, genre, _m = row(catalog.get(path) or ())
    return genre or by_artist.get(_n(artist), "")


def prefer_bilby(rows: list[dict], catalog: dict) -> list[str]:
    """Spotify rows ({uri, title, artist}) as queue items: Bilby's own copy when he has the song (same artist and title), else the URI."""
    have: dict[tuple[str, str], str] = {}
    for path, info in catalog.items():
        title, artist, *_ = row(info)
        have.setdefault((_n(artist), _n(feat_off(title))), path)
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("uri"):
            continue
        first = (r.get("artist") or "").split(", ")[0]
        out.append(have.get((_n(first), _n(feat_off(r.get("title") or "")))) or r["uri"])
    return out


# ── his playlists ──
def traktor_playlists(nml: str, state: Path | None = None, marker: str = "corrected_music") -> dict[str, list[str]]:
    """{name: [catalog paths]} for every playlist in Traktor's collection (his DJ sets), cached beside the DJ's index by the NML's mtime."""
    try:
        mtime = os.path.getmtime(nml)
    except OSError:
        return {}
    idx = Path(state) / "traktor-playlists.json" if state else None
    if idx is not None:
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            if data.get("mtime") == mtime and data.get("nml") == nml:
                return data["playlists"]
        except (OSError, ValueError, KeyError):
            pass
    import xml.etree.ElementTree as ET                              # noqa: PLC0415
    out: dict[str, list[str]] = {}
    stack: list[str | None] = []
    for ev, e in ET.iterparse(nml, events=("start", "end")):
        if e.tag == "NODE":
            if ev == "start":
                stack.append(e.get("NAME") if e.get("TYPE") == "PLAYLIST" else None)
                if stack[-1]:
                    out.setdefault(stack[-1], [])
            else:
                stack.pop()
                e.clear()
        elif ev == "end" and e.tag == "PRIMARYKEY" and stack and stack[-1]:
            key = (e.get("KEY") or "").replace("/:", "/")
            cut = key.find(f"/{marker}/")
            if cut >= 0:
                out[stack[-1]].append(unicodedata.normalize("NFC", key[cut + len(marker) + 2:]))
        elif ev == "end" and e.tag == "ENTRY" and not stack:
            e.clear()                                               # the collection's own entries: not playlists, and 50 MB of them
    out = {k: v for k, v in out.items() if v and not k.startswith("_")}
    if idx is not None:
        try:
            idx.parent.mkdir(parents=True, exist_ok=True)
            tmp = idx.with_suffix(".tmp")
            tmp.write_text(json.dumps({"mtime": mtime, "nml": nml, "playlists": out}), encoding="utf-8")
            os.replace(tmp, idx)
        except OSError:
            pass
    return out


def pick_playlist(names, said: str) -> str | None:
    """The playlist he means by name: exact (folded), else every word he said in it, the newest-looking (longest digits) first."""
    want = _n(said)
    if not want:
        return None
    names = list(names)
    for n in names:
        if _n(n) == want:
            return n
    words = want.split()
    hits = [n for n in names if all(re.search(rf"\b{re.escape(w)}", _n(n)) for w in words)]
    return sorted(hits, key=lambda n: (-len(n), n))[0] if hits else None


# ── the mix ──
def build(mode: str, src: dict, rnd=None, max_n: int = QUEUE_MAX) -> list[str]:
    """drive-pure.js buildQueue, step for step. src: {all: [paths], favorites: [paths or URIs], liked: [items], suggested: [items],
    played: [items], tired: [items], genre: item -> its genre tag ('' unknown); keep_out / bounds: resolve()'s, taken before its filters}."""
    rnd = rnd or random.Random()
    tired = set(src.get("tired") or ())
    favs = [x for x in dict.fromkeys(src.get("favorites") or []) if x and x not in tired]
    liked = [x for x in (src.get("liked") or []) if x and x not in tired]
    seen, out = set(), []

    def add(it: str) -> None:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    if mode == "favorites":
        pool = favs + liked
        rnd.shuffle(pool)
        for it in pool:
            if len(out) >= max_n:
                break
            add(it)
        return out
    if mode != "discovery":
        rest = list(src.get("all") or [])
        rnd.shuffle(rest)
        for it in rest[:max_n * 2]:
            if len(out) >= max_n:
                break
            add(it)
        return out
    skip = set(src.get("keep_out") or (set(favs) | set(liked))) | set(src.get("played") or ()) | tired
    genre = src.get("genre") or (lambda _it: "")
    bounds = set(src["bounds"]) if src.get("bounds") is not None else {g for g in map(genre, favs + liked) if g}
    rest = [x for x in (src.get("all") or []) if x not in skip]
    sugg = [x for x in (src.get("suggested") or []) if x not in skip and (not bounds or not genre(x) or genre(x) in bounds)]
    rnd.shuffle(rest)
    rnd.shuffle(sugg)
    r = g = 0
    while len(out) < max_n and (r < len(rest) or g < len(sugg)):
        spot_turn = g < len(sugg) and (r >= len(rest) or (len(out) + 1) % SUGGEST_EVERY == 0)
        if spot_turn:
            add(sugg[g])
            g += 1
        else:
            add(rest[r])
            r += 1
    return out


def _field_match(text: str, said: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9']+", _n(said)) if w not in ("the", "a")]
    hay = _n(text)
    return bool(words) and all(re.search(rf"\b{re.escape(w)}", hay) for w in words)


def resolve(args: dict, catalog: dict, favorites: list[str] | None = None, spotify=None, playlists: dict | None = None,
            rnd=None, max_n: int = QUEUE_MAX, tired: set[str] | None = None, played=None, moods: dict | None = None) -> tuple[list[str], str, bool]:
    """(queue, a note to say, ordered) for play_music's grammar arguments: mode, genre, playlist, artist, album, song, new, shuffle.

    `spotify`: an object with mix() -> {liked, suggested} and playlist(name) -> {tracks, playlist} (spotify.Client), or None. `ordered`: the queue
    is in its own order already (a playlist, an album) and must not be shuffled again. A filter that matches nothing is said in the note and
    dropped, so the closest thing still plays; nothing is ever asked. `tired`: the songs he is tired of (paths or Spotify URIs, favorites.tire),
    left out of every mix: his one favorites pool is pruned as it plays. `played`: every song he has heard (favorites.played): discovery moves
    past them."""
    rnd = rnd or random.Random()
    tired = set(tired or ())
    if tired:
        catalog = {p: v for p, v in catalog.items() if p not in tired}
    mode = str(args.get("mode") or "").lower()
    mode = {"favorite": "favorites", "favourites": "favorites", "library": "favorites", "discover": "discovery"}.get(mode, mode)
    notes: list[str] = []
    by_artist = artist_genres(catalog)
    favs = [f for f in (favorites or []) if f and f not in tired]

    if args.get("playlist"):                                        # his playlist: Traktor's first, then Spotify's; Bilby plays what it has
        said = str(args["playlist"])
        name = pick_playlist((playlists or {}).keys(), said)
        if name:
            items = [p for p in playlists[name] if p in catalog]
            if items:
                q = items if not args.get("shuffle") else rnd.sample(items, len(items))
                return q[:max_n], f"his playlist {name}", True
        if spotify is not None:
            try:
                got = spotify.playlist(said)
                items = prefer_bilby(got.get("tracks") or [], catalog)
                if items:
                    q = items if not args.get("shuffle") else rnd.sample(items, len(items))
                    pname = (got.get("playlist") or {}).get("name") or said
                    mine = sum(1 for i in items if not i.startswith("spotify:"))
                    return q[:max_n], f"his playlist {pname}" + (f", {mine} of them from Bilby" if mine else ""), True
            except Exception:                                       # noqa: BLE001 — Spotify down or no such list: fall through and say so
                pass
        notes.append(f"I found no playlist called {said}")

    liked: list[str] = []
    suggested: list[str] = []
    spot_artist: dict[str, str] = {}                                # a Spotify row's first artist: its genre and its artist filter
    if mode in ("discovery", "favorites") and spotify is not None:
        try:
            m = spotify.mix() or {}
            fresh = lambda rs: [r for r in rs or [] if isinstance(r, dict) and r.get("uri") and r["uri"] not in tired]   # noqa: E731
            rows = fresh(m.get("liked")) + fresh(m.get("suggested"))
            spot_artist = {r["uri"]: (r.get("artist") or "").split(", ")[0] for r in rows}
            liked = prefer_bilby(fresh(m.get("liked")), catalog)
            if mode == "discovery":
                suggested = prefer_bilby(fresh(m.get("suggested")), catalog)
        except Exception:                                           # noqa: BLE001 — Bilby alone
            pass

    def genre(it: str) -> str:
        if it.startswith("spotify:"):
            return by_artist.get(_n(spot_artist.get(it, "")), "")
        return genre_of(it, catalog, by_artist)

    def fields(it: str) -> tuple[str, str, str]:
        if it.startswith("spotify:"):
            return "", spot_artist.get(it, ""), ""
        t, a, al, _g2, _m = row(catalog.get(it) or ())
        return t, a, al

    tests = []                                                      # (what he said, the test), applied in this order, each dropped if it empties all
    if args.get("genre"):
        g = str(args["genre"])
        tests.append((f"{g}", lambda it, g=g: genre_match(genre(it), g)))
    if args.get("mood"):
        mw = str(args["mood"]).lower()
        want = MOOD_WORDS.get(mw) or {mw}
        tests.append((f"{mw}", lambda it, want=want: bool(want & set((moods or {}).get(it, ())))))
    if args.get("artist"):
        a = str(args["artist"])
        tests.append((f"by {a}", lambda it, a=a: _field_match(fields(it)[1], a)))
    if args.get("album"):
        al = str(args["album"])
        tests.append((f"the album {al}", lambda it, al=al: _field_match(fields(it)[2], al)))
    if args.get("song"):
        s = str(args["song"])
        tests.append((f"the song {s}", lambda it, s=s: _field_match(fields(it)[0], s)))

    src = {"all": list(catalog) if mode != "favorites" else [], "favorites": favs, "liked": liked, "suggested": suggested}
    keep_out = set(favs) | set(liked)                               # discovery: never his pool, whatever the filters keep of it
    bounds = {g for g in map(genre, favs + liked) if g}            # his normal genres: his pool's
    for said, ok in tests:
        kept = {k: [i for i in v if ok(i)] for k, v in src.items()}
        if any(kept.values()):
            src = kept
        else:
            notes.append(f"nothing {said} in {'his favorites' if mode == 'favorites' else 'his library'}")
    if args.get("new"):
        added = {p: row(catalog.get(p) or ())[4] for p in src["all"]}
        src["all"] = sorted(src["all"], key=lambda p: -added.get(p, 0))[:NEW_COUNT]
        if mode == "favorites":
            src["favorites"] = src["favorites"][:NEW_COUNT // 4]   # newest first (favorites.py keeps them so)
    ordered = False
    if (args.get("album") or args.get("song") or args.get("artist")) and not args.get("shuffle") and mode == "" and not args.get("genre"):
        q = [p for p in catalog if p in set(src["all"])]          # an artist's or album's tracks in album order, like music.search
        q.sort(key=lambda p: (row(catalog[p])[2].lower(), p))
        queue, ordered = q[:max_n], True
    else:
        queue = build(mode, {**src, "keep_out": keep_out, "bounds": bounds, "genre": genre, "played": set(played or ())}, rnd, max_n)
    if not queue and (tests or args.get("new")):                    # every filter dropped and still nothing: the plain shuffle of the mode
        queue = build(mode, {"all": list(catalog), "favorites": favs, "liked": liked, "suggested": suggested}, rnd, max_n)
    if not queue and mode == "favorites":                           # no favorites and no Spotify: the library is the closest
        notes.append("his favorites came back empty")
        queue = build("", {"all": list(catalog)}, rnd, max_n)
    note = ("; ".join(notes) + ", so here is the closest.") if notes else ""
    return queue, note[:1].upper() + note[1:], ordered


def describe(args: dict) -> str:
    """'goth on discovery' for what is playing, in his words."""
    bits = []
    if args.get("new"):
        bits.append("newly added")
    if args.get("genre"):
        bits.append(str(args["genre"]))
    for k, pre in (("artist", "by "), ("album", "from "), ("song", ""), ("playlist", "the playlist ")):
        if args.get(k):
            bits.append(pre + str(args[k]))
    mode = str(args.get("mode") or "")
    if mode:
        bits.append({"discovery": "on discovery", "favorites": "from his favorites"}.get(mode, mode))
    return " ".join(bits)
