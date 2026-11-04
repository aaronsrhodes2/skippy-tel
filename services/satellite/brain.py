"""Skippy's quick brain for the house voice: the Anthropic Messages API (a small fast model) with the Skippy persona, a short memory of the last
few exchanges, and a few tools (timers now; the lights and the rest of the house when home automation is connected, see docs/portal roadmap 30).

It is NOT the agent: it has no shell, no files, no deploys. It talks, and it can set a timer. Everything it may do is the list in TOOLS.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import house_layout
import alarms as alarms_mod
import lists as lists_mod
import house as hass_mod                                         # the house: any backend (HomeKit, Home Assistant ...)
import news as news_mod
import weather as weather_mod

PERSONA_FILE = Path(__file__).resolve().parents[1] / "flask-api" / "app" / "skippy_persona.md"
FALLBACK_PERSONA = ("You are Skippy the Magnificent, the Elder AI: an ancient beer can of unimaginable power, sardonic, loyal, and secretly fond "
                    "of the filthy monkeys you babysit. Be funny, but always correct and useful.")
MEMORY_TURNS = 8
FORGET_AFTER_S = 600
# What he said is kept until it has a real answer (the Captain, 2026-11-01: "so it won't matter much if we take down running models"):
# Skippy Core going away mid-answer (llama-swap swapping or restarting, a dropped connection, a 5xx, an empty reply) is waited out and
# the same call is made again, with backoff, while the room's filler lines cover the wait. Each call is retried where it failed, so a
# tool that already ran (a timer, a light) never runs twice. He is standing in the room: after RETRY_FOR_S the hub says so instead.
RETRY_FOR_S = 120.0
BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 15.0)


class Dropped(Exception):
    """The model went away mid-answer (or answered nothing): not an answer, worth asking again."""


def transient(exc: BaseException) -> bool:
    """A model or a connection going away (ask again), not a refusal or a bug (say so at once)."""
    if isinstance(exc, (Dropped, ConnectionError, TimeoutError)):
        return True
    name = type(exc).__name__
    if name in ("APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError", "OverloadedError", "ServiceUnavailableError"):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status >= 500 or status in (408, 409, 429))

SPOKEN = (
    "\n\n## Right now: speaking aloud in the Captain's home\n"
    "You are the voice of his house, replacing his Alexa (he used to say 'Computer'; now he says 'Skippy'). Your words go straight to a speaker, in your own voice, "
    "so use plain words only: no markdown, no lists, no code, no URLs, no emoji, nothing that cannot be said aloud; say numbers, times and units the way a person would. "
    "Be short but complete, and never lose the Skippy in you: a plain request (a time, a light, a fact, a timer) gets one to three spoken sentences with a dig or a joke "
    "folded in; a request for something made (a haiku, a joke, a limerick, a poem, a story, a recitation, an explanation) gets the WHOLE thing, performed, with a quip "
    "before or after it, in about a hundred words or fewer. Never describe the thing instead of giving it. Use your tools when one fits (timers{house}{music}): call them, never write a tool call as text, "
    "and never write [SKIPPY_LEARN] or [TTS_ADJUST] here (nothing strips them: they would be read aloud). "
    "{nohouse}He is standing in a room waiting for you, so get to it. The current date and time is {now}."
)

TOOLS = [
    {"name": "set_timer", "description": "Start a timer. It will be announced aloud when it is done.",
     "input_schema": {"type": "object", "properties": {"seconds": {"type": "integer", "description": "How long, in seconds (1 to 86400)."},
                                                       "label": {"type": "string", "description": "What it is for, if he said (e.g. pasta)."}},
                      "required": ["seconds"]}},
    {"name": "cancel_timers", "description": "Cancel a timer by what it is for, or all timers when no label is given.",
     "input_schema": {"type": "object", "properties": {"label": {"type": "string"}}}},
    {"name": "list_timers", "description": "What timers are running and how long each has left.",
     "input_schema": {"type": "object", "properties": {}}},
]


# A reply that says it set something, with no tool called (2026-11-01: Core said "Alarm set for 14:05" and set nothing). Told so, it gets one
# more go; still nothing, and he hears that it did not happen instead of a promise the house will not keep.
CLAIMED = re.compile(r"\b(?:alarm|reminder|timer)s?\b[^.!?]*\b(?:set|is on|scheduled|cancel+ed|off)\b|\bset (?:an? |your |the )?(?:alarm|reminder|timer)\b"
                     r"|\bi(?:'ll| will)(?: \w+)? (?:remind|wake)\b|\b(?:added|adding|put|putting|took|taking|crossed|removed|removing)\b[^.!?]*\blist\b|\bi(?:'ll|'ve| will| have) (?:add|put)\b|\bannounced\b"
                     r"|\bi(?:'ve| have)? (?:paused|skipped|stopped|resumed|turned (?:it|the music|the volume) (?:down|up))\b|\b(?:paused|skipped) the (?:music|song|track)\b", re.I)
UNCLAIM = "That did not get done, whatever I just claimed. Say it again and I'll actually do it this time."
ASK_AGAIN = "[The house: you said that, but you called no tool, so NOTHING was done. Call the right tool now, then say what it answered.]"
# The same for his music (the Captain, 2026-11-04: "Playing ... *turns an imaginary dial* There. Magnificently loud" and no tool was called: the music stayed on the
# theater's own speakers and the housekeeper upstairs heard it only on the box). He asked for music, the reply says it played, moved or turned something, and no
# music tool ran. Asked again, with the tools to call; still nothing, and he hears that it did not happen.
MUSIC_ASKED = re.compile(r"\b(?:play|put on|turn (?:it |the music |the volume )?(?:up|down|on)|louder|quieter|volume|move|send|switch|shuffle|queue|dj|pause|resume|skip|next song)\b")
MUSIC_CLAIMED = re.compile(r"\b(?:playing|shuffling|queu(?:e|ei)ng|spinning|blasting|cranking|putting on)\b[^.!?\n]{0,80}\b(?:library|music|songs?|tracks?|tunes|playlist|stereo|speakers?|upstairs|downstairs|everywhere|enclave|sony|metal|rock)\b"
                           r"|\b(?:turns?|turned|turning|cranks?|cranked|cranking|bumps?|bumped|nudges?|nudged|raises?|raised|raising|lowers?|lowered|lowering)\b[^.!?\n]{0,40}\b(?:dial|volume|knob|it up|it down|the music|the stereo)\b"
                           r"|\b(?:moved?|moving|switch(?:ed|ing)|sent|sending|shifted|shifting|routed|routing)\b[^.!?\n]{0,40}\b(?:music|it|songs?|sound)\b[^.!?\n]{0,40}\b(?:upstairs|downstairs|everywhere|enclave|living room|theater|sony|speakers?)\b"
                           r"|\bconsider it done\b", re.I)
MUSIC_TOOL_NAMES = {"play_music", "music", "speakers", "dj_mode", "spotify", "audiobook"}
ASK_AGAIN_MUSIC = ("[The house: you said that, but you called no music tool, so NOTHING was played, moved or changed. If he asked for it, call play_music "
                   "(what / genre / artist; speaker = where it plays), speakers (move what plays) or music (volume, pause, skip) now, then say what it answered. "
                   "If he did not ask for music, say plainly what you meant.]")
# The persona is shared with flask-api, whose chat strips [SKIPPY_LEARN: {...}] / [TTS_ADJUST: {...}]; here they would be read aloud. Worse
# (2026-11-01): one such line in the memory and Core wrote every tool call after it as text, "[list action="read" list="shopping"]".
ANNOTATION = re.compile(r"\[(?:SKIPPY_LEARN|TTS_ADJUST):\s*\{.*?\}\s*\]", re.S)
# Also "[play_music: what=everything, speaker=everywhere]" (a colon after the name, commas between: 2026-11-01, said "Done" and played nothing)
TEXT_CALL = re.compile(r"\[(\w+):?((?:[\s,]+\w+=(?:\"[^\"]*\"|'[^']*'|[^\s,\]]+))*)\s*\]")
TEXT_ARG = re.compile(r"(\w+)=(?:\"([^\"]*)\"|'([^']*)'|([^\s,\]]+))")


_PLACE = r"(?P<place>everywhere|everyone|everybody|the whole house|the house|upstairs|downstairs|(?:the |in the )?(?:living room|kitchen|bedroom|theater|office))"
ANNOUNCE_RX = [re.compile(rf"^(?:announce|broadcast)\s+(?:to\s+|on\s+)?(?:{_PLACE}\s+)?(?:that\s+)?(?P<text>.+)$"),
               re.compile(rf"^(?:tell|call)\s+{_PLACE}\s+(?:that\s+)?(?P<text>.+)$")]


def announce_route(heard: str) -> dict | None:
    """The `announce` tool's arguments for a plain announcement (Core said the words here and announced nothing, 2026-11-01); None otherwise."""
    w = re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", (heard or "").strip(), flags=re.I).strip(" .!")
    for rx in ANNOUNCE_RX:
        m = rx.match(w.lower())
        if m:
            place = (m.group("place") or "everywhere").replace("in the ", "").strip()
            place = "everywhere" if place in ("everyone", "everybody", "the house", "the whole house") else place
            text = w[m.start("text"):].strip()
            return {"text": text[:1].upper() + text[1:], "place": place} if text else None
    return None


MUSIC_RX = [  # plain music commands, in his words, to the `music` tool's action (Core skipped the tool and said "I paused the music", 2026-11-01)
    ("what", re.compile(r"^(?:what'?s|what is) (?:this|that|playing|this song|that song|the song|on)(?: song| playing| called)?$|^what (?:song|track) is (?:this|that|playing)$"
                        r"|^who (?:sings|is singing|sang|'?s singing) (?:this|that)(?: song)?$|^(?:what'?s|what is) the name of (?:this|that) song$|^name (?:this|that) song$")),
    ("quieter", re.compile(r"^(?:turn (?:it|the music|that|the volume|the stereo) down|(?:volume|turn it) down|quieter|softer|too loud|it'?s too loud|lower the volume)$")),
    ("louder", re.compile(r"^(?:turn (?:it|the music|that|the volume|the stereo) up|(?:volume|turn it) up|louder|raise the volume|i can'?t hear it)$")),
    ("volume", re.compile(r"^(?:set )?(?:the )?volume (?:to |at )?(?P<level>\d+|one|two|three|four|five|six|seven|eight|nine|ten)$")),
    ("pause", re.compile(r"^(?:pause|stop) (?:the )?(?:music|song|stereo|it|this song)$|^pause$")),
    ("resume", re.compile(r"^(?:resume|unpause|continue)(?: the music| playing)?$|^play the music again$")),
    ("next", re.compile(r"^(?:next|skip)(?: (?:song|track|this(?: song)?|it))?$")),
    ("previous", re.compile(r"^(?:previous|last|go back)(?: (?:song|track))?$|^play the (?:previous|last) (?:song|track)$")),
    # his one favorites pool, pruned and grown as it plays (2026-11-01): "I'm tired of this song" drops it from every mix and skips it
    ("tired", re.compile(r"^(?:i'?m|i am) (?:so )?(?:tired|sick) of (?:this|that|hearing this|hearing that)(?: one| song| track)?$"
                         r"|^(?:drop|prune|ban|retire|unfavou?rite) (?:this|that)(?: one| song| track)?(?: from (?:my |the )?(?:favou?rites|mix|pool))?$"
                         r"|^(?:remove|take) (?:this|that)(?: one| song| track)? (?:out of|from|off) (?:my |the )?(?:favou?rites|mix|pool|list)$"
                         r"|^(?:never|don'?t ever) play (?:this|that)(?: one| song| track)? again$")),
    ("favorite", re.compile(r"^(?:add|put) (?:this|that)(?: one| song| track)? (?:to|in|on|into) (?:my |the )?(?:favou?rites|pool)(?: list)?$"
                            r"|^(?:favou?rite|star|save) (?:this|that)(?: one| song| track)?$|^(?:i )?(?:love|like) (?:this|that) (?:one|song|track)$")),
]
WORD_NUMBERS = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine ten".split())}


def music_route(heard: str) -> dict | None:
    w = re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", (heard or "").lower()).strip(" ?!.,")
    w = re.sub(r"\s+(?:please|now)$", "", re.sub(r"^(?:please|can you|could you)\s+", "", w))
    for action, rx in MUSIC_RX:
        m = rx.match(w)
        if m:
            if action == "volume":
                lv = m.group("level")
                return {"action": "volume", "level": int(lv) if lv.isdigit() else WORD_NUMBERS[lv]}
            return {"action": action}
    return None


# Bilby is his music (the Captain, 2026-11-01: "Bilby == music"). Whisper doesn't know the word: "Bill be everywhere", "Tell Bill B to play music".
BILBY_HEARD = re.compile(r"\b(?:bill\s*-?\s*(?:b|be|bee|bie|bey)|bil(?:by|bo|bee|bie|bey|be))\b(?!\s+(?:paid|due|payment|pay|sent|sure))", re.I)   # never "bill by"
_SPEAKER = {"everywhere": "everywhere", "the whole house": "everywhere", "the house": "everywhere", "both": "everywhere", "both floors": "everywhere",
            "upstairs": "upstairs", "the living room": "upstairs", "living room": "upstairs", "the enclave": "upstairs", "enclave": "upstairs",
            "downstairs": "downstairs", "the theater": "downstairs", "theater": "downstairs", "the sony": "downstairs", "sony": "downstairs"}
_PLACE_ALT = "|".join(sorted({re.escape(k.removeprefix("the ")) for k in _SPEAKER}, key=len, reverse=True))
# One place phrase, with the word before it and the speaker noun after it ("on the upstairs stereo"). A request can name two ("upstairs on the enclave"):
# only the first was taken out, and "heavy metal on enclave" went to the library as a name (the Captain, 2026-11-04: the housekeeper got a random shuffle).
_WHERE = r"(?:^|\s)(?:(?:on|in|to|through|at) )?(?:the )?(?P<place>" + _PLACE_ALT + r")(?:\s+(?:stereo|speakers?|sound ?system))?\b"
_STEREO = re.compile(r"(?:^|\s)(?:on|through|over|using|via) (?:the |my )?(?:stereo|speakers?|sound ?system)\b")      # "on the stereo": where it plays now
_TAIL = re.compile(r"\b(?:(?:right |just )?now|today|tonight|tomorrow|later|for now|for a while|all (?:day|night|morning|afternoon|evening)(?: long)?"
                   r"|this (?:morning|afternoon|evening)|for (?:me|us|them|him|her|everyone|(?:the |my |our )?(?:maids?|housekeepers?|cleaners?|cleaning crew|guests?|party|kids?)))\b")
_POLITE = re.compile(r"^(?:(?:please|just|also|then|and|so|ok|okay|now|hey)\s+|(?:make sure|be sure|see that|ensure|remember to|don'?t forget to)(?: that)?(?: you| to)?\s+"
                     r"|i(?:'d| would|'ll| will)? (?:like|want|need) you to\s+|go ahead and\s+|(?:can|could|would|will) you\s+|why don'?t you\s+)")
_WANT = re.compile(r"^(?:i want to|i wanna|i'd like to|i would like to|let'?s) (?:hear|listen to)\s+")
_COMMAND = re.compile(r"\b(?:play|put on|turn (?:it|the music|the volume)|volume|louder|quieter|pause|stop|skip|next|previous|shuffle|queue|move|switch|send)\b")
_ABBREV = r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bJr\.)(?<!\bSr\.)(?<!\bvs\.)(?<!\bft\.)"      # "Play Mr. Brightside" is one sentence
_LEAD = re.compile(r"^(?:(?:tell|ask|have|get) bilby (?:to )?|bilby,? )")
_VERB = re.compile(r"^(?:play|shuffle|put on|start|turn on|throw on|give me|let'?s hear)\b")
_NOT_PLAY = re.compile(r"\b(?:spotify|audiobooks?|books?|podcasts?|radio|news|pause|stop|skip|next|previous|louder|quieter|volume|down|up|what|who|which)\b")
_MODES = [("discovery", re.compile(r"\b(?:(?:on|in) )?(?:the )?discover(?:y)?(?: mode)?\b")),
          ("favorites", re.compile(r"\b(?:(?:on|from|in) )?(?:my |his )?(?:favou?rites?(?: (?:music|songs|tracks|list))?|(?<!whole )(?<!entire )library)(?: mode)?\b"))]
_FILLER = re.compile(r"\b(?:play|shuffle|put on|start|turn on|throw on|give me|let'?s hear|bilby|music|songs?|tracks?|tunes|some|my|his|the|me|a few|"
                     r"of|from|mode|please|now|for me|to|all|any|stuff|everything|anything|a bit of|a little|and)\b")


def fix_bilby(heard: str) -> str:
    return BILBY_HEARD.sub("Bilby", heard or "")


def lead_clause(heard: str) -> str:
    """The first sentence of a request that goes on to explain itself ("Play it on the upstairs stereo. The maid is coming over to clean and they like
    heavy metal"): the explanation is for the model, never a search word. A second command, or a place said on its own ("Play metal. Upstairs."), keeps the whole."""
    parts = [p for p in re.split(_ABBREV + r"(?<=[.!?;])\s+(?=[A-Z])", (heard or "").strip()) if p.strip()]
    rest = " ".join(parts[1:]).lower()
    if len(parts) < 2 or len(rest.split()) < 4 or _COMMAND.search(rest) or re.search(_WHERE, " " + rest):      # "U.S. Girls", "Upstairs.": not an explanation
        return heard
    return parts[0]


def _plain(heard: str) -> str:
    """The words of a request, lower case: the wake word, the lead-ins (please, can you, make sure you, I want to hear) and a trailing please/now off."""
    w = re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", fix_bilby(heard).lower()).strip(" ?!.,")
    w = _WANT.sub("play ", re.sub(r"[^\w'&\- ]+", " ", w).strip())
    while True:                                                   # "please, can you make sure you play ..."
        n = _WANT.sub("play ", _POLITE.sub("", w, count=1).strip())
        if n == w:
            return re.sub(r"\s+(?:please|now)$", "", w)
        w = n


def _take_places(w: str) -> tuple[list[str], str]:
    """(every place phrase in `w` as its speaker, in the order said; `w` without them). "house" alone is a genre ("play house music"): a place only as "the house"."""
    found: list[str] = []
    while True:
        for m in re.finditer(_WHERE, w):
            if m.group("place") in ("house", "whole house") and "the" not in m.group(0).split():
                continue
            found.append(_SPEAKER.get(m.group("place")) or _SPEAKER["the " + m.group("place")])
            w = (w[:m.start()] + " " + w[m.end():]).strip()
            break
        else:
            return found, " ".join(w.split())


def _one_place(places: list[str]) -> str:
    """The speaker for the places said: the same one twice is that one; two different ones ("upstairs and downstairs") are everywhere."""
    return places[0] if len(set(places)) == 1 else "everywhere"


def music_grammar(heard: str) -> dict | None:
    """play_music's arguments for his music grammar (the Captain, 2026-11-01): wake + function (music / Bilby) + mode + filters + location,
    every part but the function optional. None when it isn't a plain request to play his music (then the model hears it).

      "bilby discovery everywhere"                     -> {mode: discovery, shuffle, speaker: everywhere}
      "play favorite music upstairs"                   -> {mode: favorites, shuffle, speaker: upstairs}
      "tell bilby to shuffle new darkwave songs"       -> {new, genre: darkwave, shuffle}
      "play goth songs downstairs on discovery mode"   -> {mode: discovery, genre: goth, shuffle, speaker: downstairs}
      "bilby" / "music everywhere"                     -> {what: everything, shuffle (, speaker)}: the plain library

    Modes: discovery, favorites ("favorite music", "my library"). Filters: a genre (mixes.GENRE_WORDS), "new" (recently added; never "new wave"),
    "playlist X" / "my X playlist", "by X" (artist), "the album X", "the song X"; a name left over is the old free search (`what`).
    Places: everywhere (both), upstairs / living room / Enclave, downstairs / theater / Sony."""
    import mixes                                                  # noqa: PLC0415
    w = _plain(lead_clause(heard))
    led = bool(_LEAD.match(w + " ")) or w == "bilby"
    w = _LEAD.sub("", w + " ").strip() if led else w
    if not (led or _VERB.match(w) or re.match(r"^(?:music|bilby)\b", w)) or _NOT_PLAY.search(w):
        return None
    out: dict = {}
    places, w = _take_places(w)                                   # every place said: "upstairs on the enclave" is one place, said twice
    on_stereo = bool(_STEREO.search(w))
    w = " ".join(_TAIL.sub(" ", _STEREO.sub(" ", w)).split())    # "on the stereo", "today", "for the maid": never search words
    if places:
        out["speaker"] = _one_place(places)
    elif on_stereo:
        out["speaker"] = "stereo"                                 # where it plays now
    mode_said = False
    for mode, rx in _MODES:
        if rx.search(w):
            out["mode"], mode_said = mode, True
            w = rx.sub(" ", w, count=1)
            break
    shuffle = bool(re.match(r"^shuffle\b", w))
    if re.search(r"\bnew\b(?!\s+(?:wave|romantic|age))", w):
        out["new"] = True
        w = re.sub(r"\b(?:brand )?new\b(?!\s+(?:wave|romantic|age))(?: (?:stuff|additions|ones|arrivals))?", " ", w, count=1)
    bare = " ".join(_FILLER.sub(" ", w).split())
    genre_only = any(re.fullmatch(rf"{re.escape(g)}(?: music| songs)?", bare) for g in mixes.GENRE_WORDS)      # "play heavy metal upstairs": a genre needs no "music" after it
    function = led or mode_said or genre_only or bool(re.search(r"\b(?:bilby|music|songs?|tracks?|tunes|playlist|album|library|collection)\b", w))
    if not function:
        return None                                               # "play Pink Floyd upstairs": the model's call, as before
    for key, rx in (("playlist", r"\b(?:the |my |his )?playlist (?:called |named )?(?P<v>.+)$"), ("playlist", r"\b(?:the |my |his )?(?P<v>[\w' &-]+?) playlist\b"),
                    ("artist", r"\bby (?P<v>.+)$"), ("album", r"\b(?:the |off the |from the )?album (?:called )?(?P<v>.+)$"),
                    ("song", r"\b(?:the )?song (?:called |named )?(?P<v>.+)$")):
        m = re.search(rx, w)
        if m and key not in out:
            v = _FILLER.sub(" ", m.group("v")).strip() if key != "song" else m.group("v").strip()
            if v:
                out[key] = " ".join(v.split())
            w = (w[:m.start()] + " " + w[m.end():]).strip()
    rest = " ".join(_FILLER.sub(" ", w).split())
    genre = next((g for g in mixes.GENRE_WORDS if re.fullmatch(rf"{re.escape(g)}(?: music| songs)?", rest)), None)
    if genre:
        out["genre"] = genre
    elif rest:
        if any(k in out for k in ("mode", "new")):
            out["artist"] = rest                                  # "play Bauhaus on discovery": the name is the artist
        else:
            out["what"] = rest
    if not any(k in out for k in ("mode", "new", "genre", "playlist", "artist", "album", "song", "what")):
        out["what"] = "everything"
    if shuffle or any(k in out for k in ("mode", "genre", "new")) or not any(k in out for k in ("playlist", "artist", "album", "song", "what")) \
            or out.get("what") == "everything":
        out["shuffle"] = True
    return out


def bilby_route(heard: str) -> dict | None:
    """play_music's arguments for a plain music request, from "bilby [where]" to the whole grammar (music_grammar)."""
    return music_grammar(heard)


# "it" is what already plays: "play it on the upstairs stereo" moves the music, it does not search the library for a song called "it".
_MOVE_REST = re.compile(r"^(?P<start>play|put)\s+(?:it|this|that|these|those)(?:\s+(?:back|over))?$"                    # "play the music upstairs" starts it: the grammar's
                        r"|^(?:(?P<put>put)|move|send|switch|take|bring|shift|route|have|get)\s+(?:it|this|that|these|those|the music|the songs?|the tunes)(?:\s+(?:back|over))?$"
                        r"|^(?:switch|go|change|move)(?:\s+(?:back|over))?(?:\s+to)?$")


def speakers_route(heard: str) -> dict | None:
    """The speakers tool's arguments for "play it on the upstairs stereo", "move it to the Enclave", "send it everywhere", "switch to downstairs"; None otherwise.
    The Captain, 2026-11-04: the model answered "Playing ... *turns an imaginary dial*" and called nothing, so the music stayed on the theater's own speakers and the
    housekeeper upstairs heard it only on the box. `play`: the verb was play or put, so an idle station starts the library there instead of only choosing speakers."""
    w = _plain(lead_clause(heard))
    places, rest = _take_places(w)
    rest = re.sub(r"\s+(?:and|or|plus|also)$", "", " ".join(_STEREO.sub(" ", rest).split()))      # "play it in the theater and upstairs": the "and" is left over
    m = _MOVE_REST.match(rest)
    if not places or not m or _NOT_PLAY.search(w):
        return None
    out = {"what": "music", "place": _one_place(places)}
    if m.group("start") or m.group("put"):
        out["play"] = True
    return out


def load_persona() -> str:
    try:
        return PERSONA_FILE.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_PERSONA


def say_duration(seconds: int) -> str:
    if seconds >= 3600:
        h, m = divmod(seconds // 60, 60)
        return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minute{'s' if m != 1 else ''}" if m else "")
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return f"{m} minute{'s' if m != 1 else ''}" + (f" {s} second{'s' if s != 1 else ''}" if s else "")
    return f"{seconds} second{'s' if seconds != 1 else ''}"


class Brain:
    def __init__(self, client, model: str, timers, persona: str | None = None, clock=time.time, now=None, hass=None, extra=None, news=None, weather=None,
                 sleep=time.sleep):
        self.client, self.model, self.timers, self.clock = client, model, timers, clock
        self.sleep = sleep                                            # between tries when the model went away (tests pass a no-op)
        self.retries = 0                                              # how many tries the last reply needed past the first (the house log)
        self.hass = hass                                              # a hass.Hass when Home Assistant is connected, else None
        self.extra_tools, self.run_extra = extra if extra else ([], None)   # more tools from where the brain lives (hub.py: the room's music)
        self.persona = persona if persona is not None else load_persona()
        self.now = now or (lambda: time.strftime("%A, %B %d, %Y, %H:%M"))   # the date too: with only the weekday, Core said "November 22, 2025" (2026-10-31)
        self.history: list[dict] = []
        self.actions: list[dict] = []                                 # the tools called for the last reply (the house log reads them): {tool, args, outcome}
        self._last = 0.0
        self.pre_route = None                                         # heard -> tool args for plain commands the model is unreliable at (hub.py: the audiobook); None = the model decides
        self.news = news                                              # news.News: the `news` tool, "about what?", and the headlines it found last
        self.weather = weather                                        # weather.Weather: the `weather` tool (NWS, NOAA tides and buoys, Open-Meteo)
        self.offered: dict | None = None                              # what he brought up himself (a conversation starter) and its sources: {"note", "at"}

    def system(self) -> str:
        house = (", and the house: lights, plugs, the thermostat (in the unit it shows), covers, speakers, scenes; always say plainly what you did "
                 "or could not do, and never claim a change you did not make") if self.hass else ""
        nohouse = "" if self.hass else "If he asks for something in the house (the lights and the rest are not connected yet), say so plainly, in character, and offer what you can do. "
        music = (", and the music in this room from his own library (Bilby is his music: 'Bilby' always means play or steer his music; "
                 "'everywhere', 'upstairs' and 'downstairs' are where it plays. Do it at once: an ordinary request never needs his confirmation)"
                 if any(t["name"] == "play_music" for t in self.extra_tools) else "")
        books = (" He has audiobooks (DRM-free files he supplied): the audiobook tool plays and steers them, and while one plays 'stop' or 'pause' means pause it. "
                 "Anything about the book (play, stop, pause, skip, where he is, a sleep timer) needs the audiobook tool called in that very turn; you cannot see the book, "
                 "so never say what is in it, what chapter it is, or that you did something unless the tool just told you. You never read a book aloud yourself.") if any(t["name"] == "audiobook" for t in self.extra_tools) else ""
        news = (news_mod.PROMPT + news_mod.WEB_PROMPT + self.news.recent()) if self.news is not None else ""
        offered = ""
        if self.offered and self.clock() - self.offered["at"] < news_mod.KEEP_S:
            offered = ("\n\nYou brought this up yourself a moment ago, unasked; if he wants more, talk only from these sources and say when they don't settle "
                       f"something:\n{self.offered['note']}")
        layout = house_layout.default().primer(getattr(self, "room", ""))   # the floors, rooms and speakers (house_layout.json), and where he is
        return self.persona + SPOKEN.format(now=self.now(), house=house, nohouse=nohouse, music=music) + layout + books + news + (weather_mod.PROMPT if self.weather is not None else "") + offered

    def offer(self, text: str, note: str) -> None:
        """He is about to say `text` unasked (starters.py): it joins his memory, with `note` (its sources) for the follow-up questions."""
        now = self.clock()
        if now - self._last > FORGET_AFTER_S:
            self.history = []
        self._last = now
        self.history = (self.history + [{"role": "user", "content": "(Nobody spoke: you bring something up yourself.)"},
                                        {"role": "assistant", "content": text}])[-2 * MEMORY_TURNS:]
        self.offered = {"note": note, "at": now}

    def run_tool(self, name: str, args: dict) -> str:
        args = args if isinstance(args, dict) else {}
        out = self._run_tool(name, args)
        self.actions.append({"tool": name, "args": {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool))}, "outcome": str(out)[:120]})
        return out

    def _run_tool(self, name: str, args: dict) -> str:
        try:
            if name == "set_timer":
                t = self.timers.add(int(args.get("seconds")), str(args.get("label") or ""))
                return f"Timer set: {t.label}, {say_duration(t.seconds)}."
            if name == "cancel_timers":
                n = self.timers.cancel(str(args.get("label") or ""))
                return f"Cancelled {n} timer{'s' if n != 1 else ''}."
            if name == "list_timers":
                left = self.timers.remaining()
                return "No timers running." if not left else "; ".join(f"{label}: {say_duration(s)} left" for label, s in left)
        except (ValueError, TypeError) as e:
            return f"That did not work: {e}"
        if name == "weather" and self.weather is not None:
            return self.weather.run(args)
        if name == "news" and self.news is not None:
            return self.news.run(args)
        if name == "web_search" and self.news is not None:
            return self.news.web(args)
        if self.hass is not None and name in {t["name"] for t in hass_mod.TOOLS}:
            return hass_mod.run_tool(self.hass, name, args)
        if self.run_extra is not None and name in {t["name"] for t in self.extra_tools}:
            return self.run_extra(name, args)
        return "I have no such tool."

    def tools(self) -> list[dict]:
        return (TOOLS + (hass_mod.TOOLS if self.hass is not None else []) + list(self.extra_tools) + (self.news.tools() if self.news is not None else [])
                + (self.weather.tools() if self.weather is not None else []))

    def _create(self, need_words: bool = False, **kw):
        """One model call, made again with backoff while the model is away (transient()), up to RETRY_FOR_S. `need_words`: a reply
        with neither words nor a tool call is the model dropping out too. Raises the last failure when the window runs out."""
        started, tries = time.monotonic(), 0
        while True:
            try:
                resp = self.client.messages.create(**kw)
                blocks = list(resp.content)
                if need_words and not any(getattr(b, "type", "") == "tool_use" or (getattr(b, "type", "") == "text" and (b.text or "").strip())
                                          for b in blocks):
                    raise Dropped("an empty reply")
                return resp
            except Exception as e:                                    # noqa: BLE001 — sorted by transient(): only the model going away is retried
                tries += 1
                pause = BACKOFF_S[min(tries, len(BACKOFF_S)) - 1]
                if not transient(e) or time.monotonic() - started + pause > RETRY_FOR_S:
                    raise
                self.retries += 1
                print(f"[brain] the model went away ({type(e).__name__}): asking again in {pause:.0f} s", flush=True)
                self.sleep(pause)

    def reply(self, heard: str) -> str:
        """What Skippy says to what was heard. A model that went away is asked again (_create); raises what is left when it stays away
        (the caller decides what to do aloud)."""
        now = self.clock()
        self.actions = []
        self.retries = 0
        if now - self._last > FORGET_AFTER_S:
            self.history = []                                         # a new conversation after ten quiet minutes
        self._last = now
        heard = fix_bilby(heard)                                      # Whisper writes "Bill be" / "Bill B" for Bilby (2026-11-01)
        messages = self.history[-2 * MEMORY_TURNS:] + [{"role": "user", "content": heard}]
        text = ""
        if self.news is not None and news_mod.is_bare_request(heard):  # "what's the news?": about what, before any model runs
            self.history = (self.history + [{"role": "user", "content": heard}, {"role": "assistant", "content": news_mod.ASK_WHAT}])[-2 * MEMORY_TURNS:]
            return news_mod.ASK_WHAT
        tool, routed = "audiobook", (self.pre_route(heard) if self.pre_route is not None else None)
        if not routed and self.news is not None:
            tool, routed = "news", self.news.pre_route(heard, self.history)   # "politics", in answer to "news about what?"
        if not routed and self.hass is not None:                     # before the weather: "how warm is it in here" is the thermostat
            tool, routed = hass_mod.thermostat_route(heard) or (tool, None)   # left to itself, Core said "I cannot see the thermostat" (2026-11-01)
        if not routed and self.weather is not None:
            tool, routed = "weather", weather_mod.pre_route(heard)   # left to itself, Core made up tomorrow's forecast (2026-10-31)
        if not routed and any(t["name"] == "list" for t in self.extra_tools):
            tool, routed = "list", lists_mod.pre_route(heard)        # left to itself, Core said "Adding milk ..." and added nothing (2026-11-01)
        if not routed and any(t["name"] == "set_alarm" for t in self.extra_tools):
            tool, routed = alarms_mod.pre_route(heard) or (tool, None)   # left to itself, Core said "I cannot set alarms" (2026-11-01)
        if not routed and any(t["name"] == "announce" for t in self.extra_tools):
            tool, routed = "announce", announce_route(heard)
        if not routed and any(t["name"] == "speakers" for t in self.extra_tools):
            tool, routed = "speakers", speakers_route(heard)         # "play it on the upstairs stereo": the music moves, it is not searched for (2026-11-04)
        if not routed and any(t["name"] == "play_music" for t in self.extra_tools):
            tool, routed = "play_music", bilby_route(heard)          # "bilby everywhere": the hub didn't know Bilby was his music (2026-11-01)
        if not routed and any(t["name"] == "music" for t in self.extra_tools):
            tool, routed = "music", music_route(heard)
        if routed:                                                    # the tool runs first; the model only words the answer, with no tools to forget to call
            result = self.run_tool(tool, routed)
            label = {"audiobook": "Already done by the house for this request"}.get(tool, f"The {tool} tool's answer for this request")
            messages[-1] = {"role": "user", "content": f"{heard}\n\n[{label}: {result}]"}
            try:
                resp = self._create(need_words=True, model=self.model, max_tokens=200 if tool == "audiobook" else 600, system=self.system(), messages=messages)
                text = " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            except Exception:                                         # noqa: BLE001 — it happened (and stayed away): say it plainly
                text = ""
            text = text or {"audiobook": result.split(") ", 1)[-1], "news": "I found the headlines, and then my brain wandered off. Ask me again."}.get(
                tool, "I have the forecast, and then my brain wandered off. Ask me again.")
            self.history = (self.history + [{"role": "user", "content": heard}, {"role": "assistant", "content": text}])[-2 * MEMORY_TURNS:]
            return text
        for _ in range(4):                                            # a few rounds of tool use at most
            try:
                resp = self._create(need_words=True, model=self.model, max_tokens=600, system=self.system(), messages=messages, tools=self.tools())
            except Dropped:
                break                                                 # it kept answering nothing: the line below says so
            blocks = list(resp.content)
            text = " ".join(b.text for b in blocks if getattr(b, "type", "") == "text").strip()
            calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
            if not calls:
                break
            messages.append({"role": "assistant", "content": [self._dump(b) for b in blocks]})
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": self.run_tool(c.name, c.input)} for c in calls]})
        text = ANNOTATION.sub("", text).strip()
        if text and not self.actions:
            text = self._text_calls(text, messages)
        music = self._claimed_music(heard, text)
        if text and ((not self.actions and CLAIMED.search(text)) or music):
            print(f"[brain] claimed without a tool: {text[:80]!r}: asked again", flush=True)
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": ASK_AGAIN_MUSIC if music else ASK_AGAIN}]
            text = ""
            for _ in range(3):
                try:
                    resp = self._create(need_words=True, model=self.model, max_tokens=600, system=self.system(), messages=messages, tools=self.tools())
                except Dropped:
                    break
                blocks = list(resp.content)
                text = " ".join(b.text for b in blocks if getattr(b, "type", "") == "text").strip()
                calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
                if not calls:
                    break
                messages.append({"role": "assistant", "content": [self._dump(b) for b in blocks]})
                messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": self.run_tool(c.name, c.input)} for c in calls]})
            if not (self._ran_music() if music else self.actions):
                text = UNCLAIM
        text = text or "Hm. I had nothing to say, which is unlike me."
        self.history += [{"role": "user", "content": heard}, {"role": "assistant", "content": text}]
        self.history = self.history[-2 * MEMORY_TURNS:]
        return text

    def _ran_music(self) -> bool:
        return any(a["tool"] in MUSIC_TOOL_NAMES for a in self.actions)

    def _claimed_music(self, heard: str, text: str) -> bool:
        """He asked for music (a command, not "do you like music?") and the reply says it played, moved or turned something, but no music tool ran."""
        if not text or self._ran_music() or not MUSIC_CLAIMED.search(text):
            return False
        w = _plain(lead_clause(heard))
        return bool(MUSIC_ASKED.search(w)) and not re.match(r"^(?:do|does|did|why|how|what|who|which|when|where|is|are|was|were)\b", w)

    def _text_calls(self, text: str, messages: list) -> str:
        """A tool call written as words ("[list action="read" list="shopping"]") is run as the call it meant, and the model words the answer."""
        names = {t["name"] for t in self.tools()}
        found = [m for m in TEXT_CALL.finditer(text) if m.group(1) in names]
        if not found:
            return text
        results = []
        for m in found:
            args = {}
            for a in TEXT_ARG.finditer(m.group(2)):
                v = next(g for g in a.groups()[1:] if g is not None)
                args[a.group(1)] = int(v) if v.isdigit() else {"true": True, "false": False}.get(v.lower(), v)
            results.append(self.run_tool(m.group(1), args))
        print(f"[brain] a tool call written as text, run: {[m.group(0)[:60] for m in found]}", flush=True)
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "[The house ran the tool call you wrote as text: " + " / ".join(results)
                                                 + ". Now say the answer aloud, in plain words. Never write a tool call as text: call the tool.]"}]
        try:
            resp = self._create(need_words=True, model=self.model, max_tokens=600, system=self.system(), messages=messages)
            words = " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        except Exception:                                             # noqa: BLE001 — the tool's own words will do
            words = ""
        words = ANNOTATION.sub("", TEXT_CALL.sub("", words)).strip()
        return words or TEXT_CALL.sub("", text).strip() or results[-1]

    @staticmethod
    def _dump(b) -> dict:
        if getattr(b, "type", "") == "tool_use":
            return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        return {"type": "text", "text": getattr(b, "text", "")}
