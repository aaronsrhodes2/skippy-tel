"""Skippy's quick brain for the house voice: the Anthropic Messages API (a small fast model) with the Skippy persona, a short memory of the last
few exchanges, and a few tools (timers now; the lights and the rest of the house when home automation is connected, see docs/portal roadmap 30).

It is NOT the agent: it has no shell, no files, no deploys. It talks, and it can set a timer. Everything it may do is the list in TOOLS.
"""
from __future__ import annotations

import copy
import re
import time
from pathlib import Path

import house_layout
import musicguard
import alarms as alarms_mod
import lists as lists_mod
import house as hass_mod                                         # the house: any backend (HomeKit, Home Assistant ...)
import news as news_mod
import weather as weather_mod
import car as car_mod                                            # car_status: his Tesla, read only (the reader on skippy-core)
import physics as physics_mod                                    # ask_mentat: the only source of a physical number (PLATINUM 21)

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

# What the house chat knows about the house's audio and about itself (the Captain, 2026-11-04: "the house chat seems to have no idea about the house it's in or the AI it has access to"
# ... "needs to know how to talk to Bilby for audio"). Bilby is the ONE player: his music, Spotify and his audiobooks as one stream; the Enclave and the Sony each play it or not.
BILBY = (
    "\n\n## Bilby: the house's audio\n"
    "Bilby is the ONE player for everything the house plays: his music library (about twenty thousand tracks), Spotify and his audiobooks. They are items of one queue and come out as a single stream, "
    "the same on every speaker that is switched on (he is the only listener), so there is never a second stream to start for a room. Each speaker has its own switch that stays as he set it: the Enclave "
    "upstairs and the Sony downstairs, and the box in a room, which is a microphone, a face and a last-resort speaker (it plays Bilby only when its floor's stereo is on but gone). With no speaker "
    "listening the stream waits where it was and carries on by itself when one comes back. Your own voice ducks all of the stream, on every speaker, while you speak. You talk to Bilby with tools, "
    "and only a tool changes anything:\n"
    "- play_music starts something: what / genre / artist / album / song / playlist / mode (discovery, favorites) / shuffle. Leave speaker out unless he named a place.\n"
    "- audiobook plays his book as that same stream (play, resume, next_chapter, back, forward, where, sleep, speed, list); it replaces what plays and keeps its place.\n"
    "- music steers whatever plays, a book too: pause (that is also 'stop', 'enough', 'be quiet', 'turn it off'), resume, next (a chapter, in a book), previous, louder, quieter, volume (1 to 10), what, favorite, tired.\n"
    "- speakers: device + on turns ONE speaker on or off ('turn the Sony off', 'play it on the Sony too', 'switch the Enclave on'); place sets them as a group (upstairs = the Enclave on and the Sony off, "
    "downstairs the other way round, everywhere = all on).\n"
    "- dj_mode turns the beat-matched mixing on or off; spotify plays his Spotify.\n"
    "'Stop', 'pause', 'enough' or 'turn it off' with nothing else named mean the house audio, a book included: call music with pause. A routine (morning, homecoming) may start the library; if he says "
    "stop, it stays off. Asked what is playing, or on which speakers, read the 'Right now' lines at the end of this prompt or call music with what. "
    "Never say you played, stopped, moved, switched or changed anything unless a tool just did it.\n"
)
ABILITIES = (
    "\n\n## What you are, and what you can reach from here\n"
    "You are Skippy, running on skippy-pc in his house; your thinking is done by Skippy Core, the local model on skippy-core. From this voice you act ONLY through these tools: {names}. "
    "In his words, you can: {can}. Out of your reach from here: {gone}his files and code. {chat}Asked for something none of your tools does, say plainly that this voice cannot do it yet and where it can be done. "
    "Never claim you did what no tool did, and never claim you cannot do what a tool above does.\n"
)
# The tools in his words, for "what can you do?" and "can you ...?" (the Captain, 2026-11-05: the house chat "still gets confused about what it has access to"): each phrase
# is said only while one of its tools is really here, so the list is never longer than the truth
CAPS = (
    ("the house: lights, plugs, the thermostat, covers, speakers and scenes", ("home_control", "home_state", "home_devices")),
    ("Bilby: his music library, Spotify and the DJ mixing as one stream, with a switch for each speaker (the Enclave, the Sony)", ("play_music", "music", "speakers", "dj_mode", "spotify")),
    ("his audiobooks, on the same stream", ("audiobook",)),
    ("alarms and reminders", ("set_alarm", "set_reminder", "cancel_alarm", "list_alarms")),
    ("timers", ("set_timer", "cancel_timers", "list_timers")),
    ("his lists", ("list",)),
    ("announcements to other rooms", ("announce",)),
    ("the news", ("news",)),
    ("a web search for anything else", ("web_search",)),
    ("the weather", ("weather",)),
    ("his car, read only", ("car_status",)),
    ("physics, through Mentat", ("ask_mentat",)),
)
# A reply that says it cannot, with no tool called (2026-11-04: "I do not have access to your car's systems" with the tool right there, and "You are wrong, you DO have access.
# Missed tool call."; 2026-11-01: "I cannot see the thermostat", "I cannot set alarms"). It gets one more go with the real tool list; the first reply stands unless a tool runs.
# The review of 2026-11-05: the model types ’ for ' now and then (6 of 101 apostrophes in the logs), says "this voice cannot do that yet" (the very words ABILITIES asks it to use), "there is
# no tool for that", "I can't help with that"; while "I cannot believe it", "I can't help but laugh" and "I can not only set timers but ..." are figures of speech. denies() reads plain quotes.
DENIAL = re.compile(r"\bI (?:cannot|can't|can not(?! only))\b(?! believe| help (?:but|myself|it\b|[a-z]+ing\b)| stress| wait\b| say (?:i'?m|i am|that)\b| imagine| be bothered| waste| blame| complain| express| thank)"
                    r"|\bI(?:'m| am) (?:not able|unable) to\b|\bI (?:do not|don't) have (?:access|the ability|a way|any way|a tool|the tools)\b|\bI lack (?:access|the ability|a way)\b"
                    r"|\bI have no (?:access|way|ability|tools?|control)\b|\b(?:not|isn't|aren't) (?:connected|wired|hooked up)\b|\bno such tool\b|\bno tools? for\b"
                    r"|\bthis voice (?:cannot|can't|can not)\b|\boutside what I can do\b|\bI (?:could not|couldn't) (?:do (?:that|it|this)|access|reach|control|see)\b", re.I)
_PLAIN_QUOTES = {0x2019: "'", 0x2018: "'", 0x02BC: "'"}


def denies(text: str) -> bool:
    """Does this reply say it cannot (DENIAL)? Whatever apostrophe the model typed."""
    return bool(DENIAL.search((text or "").translate(_PLAIN_QUOTES)))


DENIED = ("[The house: you said you cannot do that, but you called no tool, and you DO have these: {names}. Read his words again. If one of those tools does what he asked, call it "
          "now and say what it answered. If none can, say plainly which part no tool covers. Never say you cannot do what one of them does.{asked}]")
ASKED_ONLY = " He may be only asking what you can do or how things stand: then use a tool that only looks, or answer in words, and change nothing."
NOT_DONE = "Not done: that is not for a second try (a question, or something that undoes or announces), so nothing was changed. Say plainly that you did not do it; he can ask again."
# The second try is never for what can hurt: a "no" about a lock, a door or the garage (a real run, 2026-11-05: "Can you open the garage door?" was answered "it requires confirmation", then
# the asked-again model called home_control open with confirm=true on a question) stands, and whatever the second try calls runs WITHOUT its `confirm`: only he says yes, aloud.
RISKY = re.compile(r"\b(?:garage|locks?|unlock|doors?|gates?|dead ?bolts?|locked|disarm|sentry|security|alarm system|oven|stove|burners?|fireplace|grill|space heater|iron(?! (?:maiden|man))|dryer)\b", re.I)
# What the second try may DO (the review of 2026-11-05: "are you able to cancel alarms", "Clear the house chat" (a real utterance of 2026-10-31) and "tell my wife I love her" were answered "I cannot ...",
# the nudge offered EVERY tool and the model's pick ran: both alarms gone, the weekday wake-up too, the shopping list empty, an announcement in every room). It may look at anything; it may change
# something only when his words are an order and not a question; it never undoes or broadcasts on its own: cancelling with nothing named cancels everything, `list` clear / remove empties a list,
# `announce` speaks in the other rooms. retry_tools() is the menu it is shown, retry_may() the gate every call it makes passes (a real one and one written as text).
RETRY_NEVER = {"cancel_alarm", "cancel_timers", "announce"}
RETRY_NEVER_ACTIONS = {"list": ("remove", "clear")}
RETRY_READS = {"list_timers", "list_alarms", "home_state", "home_devices", "car_status", "weather", "news", "web_search", "ask_mentat"}
RETRY_READ_ACTIONS = {"music": ("what",), "audiobook": ("where", "list"), "list": ("read", "lists")}
RETRY_PATIENCE_S = 10.0                 # the second try is optional and the room is deaf while it waits: it never waits out the 120 s (RETRY_FOR_S) a first call may
# A question, not an order: "are you able to cancel alarms", "do you know how to clear the list", "what timers do I have", "is the heat on", "you can't set alarms, can you". Its second try may only look.
NOT_AN_ORDER = re.compile(r"^\W*(?:(?:hey |ok |okay )?skippy\W+)?(?:(?:so|but|and|well|um|uh|hmm|ok|okay)\W+)*"
                          r"(?:(?:does|did|has|had|am|is|are|was|were|isn't|aren't|wasn't|weren't|doesn't|didn't|hasn't|haven't|hadn't)\s"
                          r"|(?:do|have)\s+(?:you(?! mind\b| think you could\b)|i|we|they|these|those|the|my|any|anyone|anything|someone|there)\b"      # ("do it", "have it ready", "do you mind turning it off" are orders)
                          r"|(?:what|how)\b(?! about)|(?:which|who|whom|whose|where|when|why)\b"                                             # ("how about some jazz" is an order)
                          r"|you (?:can|could|cannot|can't|don't|do not|have|are|should|must)\b|i (?:wish|bet|think|heard|thought|guess|suppose|wonder)\b|(?:if|whether)\b)", re.I)
# "what can you do for me": a reply that says what it cannot do is the right answer, not a denial to ask again. Only a question about abilities in general is one: the first version took
# any "what do you / what are you ..." for it, so "what do you know about my car", "what do you see in the car" and "what are you seeing outside" got no second try after "I do not have
# access to your car's systems" (the review of 2026-11-05, round 2: the car incident of 2026-11-04 again, for the phrasings no pre-route claims).
ASKS_ABILITY = re.compile(r"\bwhat (?:else |all |exactly )?(?:can|could|can't|cannot) you (?:not |actually |really |even |possibly )?(?:do|help|control|handle)\b"
                          r"|\bwhat (?:else )?(?:are|do) you (?:actually |really )?(?:able|capable|good (?:at|for)|do|know how to (?:do|control|handle))\b"
                          r"|\bwhat (?:are|is) your (?:abilities|capabilities|tools|limits)\b|\byour (?:abilities|capabilities|tools)\b|\bwhat you (?:can|could) do\b", re.I)


def retry_may(name: str, args: dict, ordered: bool) -> bool:
    """May the second try after a denial run this call? Looking always, changing only when his words are an order, undoing or announcing never."""
    action = str((args or {}).get("action") or "").strip().lower()
    if name in RETRY_NEVER or action in RETRY_NEVER_ACTIONS.get(name, ()):
        return False
    if name in RETRY_READS or action in RETRY_READ_ACTIONS.get(name, ()):
        return True
    return ordered


def retry_tools(tools: list[dict], ordered: bool) -> list[dict]:
    """The second try's menu: `tools` without what retry_may never allows, each `action` list cut to the actions it does allow (copies: the shared schemas stay as they are)."""
    out = []
    for t in tools:
        enum = (((t.get("input_schema") or {}).get("properties") or {}).get("action") or {}).get("enum")
        if not enum:
            if retry_may(t["name"], {}, ordered):
                out.append(t)
            continue
        keep = [a for a in enum if retry_may(t["name"], {"action": a}, ordered)]
        if keep and keep != list(enum):
            t = copy.deepcopy(t)
            t["input_schema"]["properties"]["action"]["enum"] = keep
        if keep:
            out.append(t)
    return out


# What is out of reach, said only while it is true (the Captain, 2026-11-04: "What is my car's charge level right now?" was answered "I do not have access to your car's systems";
# "You are wrong, you DO have access. Missed tool call."). The car and Mentat are tools now (car.py, physics.py), so each is named as out of reach only while its tool is missing;
# a tool added later shows in the list above by itself. tool -> (how it is said as out of reach, how it is said as what the portal chat has)
ELSEWHERE = {"car_status": ("his car, ", "the car"), "ask_mentat": ("physics calculations (Mentat), ", "Mentat")}

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
_CLAUSES = re.compile(r";|\b(?:but|however|though|although|yet|while)\b", re.I)         # a sentence's clauses: 'I cannot reach the speakers, but alarm set for seven.' (Brain._claims)
UNCLAIM = "That did not get done, whatever I just claimed. Say it again and I'll actually do it this time."
ASK_AGAIN = ("[The house: you said that, but you called no tool, so NOTHING was done. If he asked for it, call the right tool now, then say what it answered; "
             "if he did not, say plainly what you meant.]")
# The same for his music (the Captain, 2026-11-04: "Consider it done. Playing your heavy metal library ..." and "*adjusts imaginary dial* There. Magnificently loud", both with
# tools: [] in the house log: the music never left the theater's speakers). One music request in three that reached the model was answered with such a claim; musicguard.py
# says when a reply is one. He asked and a claim came: ONE more call, with only the music tools and his words quoted; he did not ask: no model call at all, the claim is cut
# out and the plain truth said, so nothing can start. The second words stand only when they take the claim back or ask; anything else is another claim and gets the fixed line.
MUSIC_TOOL_NAMES = musicguard.MUSIC_TOOLS
MUSIC_NAMES = {"play_music", "music", "speakers", "dj_mode", "spotify"}          # what a re-ask offers: the tools that play, never the audiobook or the lights
REASK_MUSIC = ('[The house: you said that, but you called no music tool, so NOTHING was played, moved or changed. He said: "{heard}". If those words ask for music, call the right '
               "tool now, using only what he said (play_music: what / genre / artist / album / song / mode / shuffle, and speaker ONLY if he named a place; speakers: move what "
               "plays; music: volume, pause, resume, next; dj_mode), then say in one sentence what it answered. If they do not ask for music, call nothing and say plainly that "
               "you did not play anything.]")
UNCLAIM_MUSIC = "I said I had done it, but I did not: nothing was played, moved or changed. Say it again with the place and what to play and I will do it."
HONEST = re.compile(r"\b(?:not|never|nothing|no|wrong|mistake|sorry|apologi[sz]e|cannot|unable|idiom|figure of speech)\b|n't", re.I)      # a second try that takes the claim back
# The persona is shared with flask-api, whose chat strips [SKIPPY_LEARN: {...}] / [TTS_ADJUST: {...}]; here they would be read aloud. Worse
# (2026-11-01): one such line in the memory and Core wrote every tool call after it as text, "[list action="read" list="shopping"]".
ANNOTATION = re.compile(r"\[(?:SKIPPY_LEARN|TTS_ADJUST):\s*\{.*?\}\s*\]", re.S)
# Also "[play_music: what=everything, speaker=everywhere]" (a colon after the name, commas between: 2026-11-01, said "Done" and played nothing)
TEXT_CALL = re.compile(r"\[(\w+):?((?:[\s,]+\w+=(?:\"[^\"]*\"|'[^']*'|[^\s,\]]+))*)\s*\]")
TEXT_ARG = re.compile(r"(\w+)=(?:\"([^\"]*)\"|'([^']*)'|([^\s,\]]+))")
# A call written as words where no tool is on offer (a routed answer, 2026-11-04: Core, handed a Mentat answer that missed, wrote "[ask_mentat: What is the half-life of carbon-14?]"
# as its whole reply): it is never spoken, and the plain apology stands in.
PHANTOM = re.compile(r"\[(?:ask_mentat|car_status)\b[^\]]*\]", re.I)


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


# Bilby is his music (the Captain, 2026-11-01: "Bilby == music"). Whisper doesn't know the word: "Bill be everywhere", "Tell Bill B to play music".
BILBY_HEARD = re.compile(r"\b(?:bill\s*-?\s*(?:b|be|bee|bie|bey)|bil(?:by|bo|bee|bie|bey|be))\b(?!\s+(?:paid|due|payment|pay|sent|sure))", re.I)   # never "bill by"
_SPEAKER = {"everywhere": "everywhere", "the whole house": "everywhere", "the house": "everywhere", "both": "everywhere", "both floors": "everywhere",
            "all over the house": "everywhere", "throughout the house": "everywhere", "every room": "everywhere", "all rooms": "everywhere",
            "upstairs": "upstairs", "the living room": "upstairs", "living room": "upstairs", "the enclave": "upstairs", "enclave": "upstairs",
            "up stairs": "upstairs", "upper floor": "upstairs",
            "downstairs": "downstairs", "the theater": "downstairs", "theater": "downstairs", "the sony": "downstairs", "sony": "downstairs",
            "down stairs": "downstairs", "lower floor": "downstairs"}
# Only words that are not also band names (Basement Jaxx, Theatre of Hate, Garage Inc.): the house layout's wide list runs only after a failed name search, behind a preposition
# (station.clean_what). One place phrase, with the word before it and the speaker noun after it ("on the upstairs stereo"). A request can name two ("upstairs on the enclave"): the
# first-only version left "on enclave" in the search words (the Captain, 2026-11-04: the housekeeper got a random shuffle and "the library is empty of metal"). `updown`:
# "up in the living room" is a place for a play verb ("turn it up in ..." is not one).
_ALT = "|".join(sorted({re.escape(k.removeprefix("the ")) for k in _SPEAKER}, key=len, reverse=True))
_NEAR = r"(?:on|in|to|through|at|over|via) "
_WHERE = {ud: re.compile(r"(?:^|\s)(?:" + ("(?:up |down )?" if ud else "") + _NEAR + r")?(?:the )?(?P<place>" + _ALT + r")(?:\s+(?:stereo|speakers?|sound ?system))?\b")
          for ud in (False, True)}
_STEREO = re.compile(r"(?:^|\s)(?:on|through|over|using|via) (?:the |my )?(?:stereo|speakers?|sound ?system)\b")      # "on the stereo": where it plays now
_NAMED_BY = re.compile(r"\b(?:by|called|named|the song|the album)$")                                              # "music by Basement Jaxx": a name, not a place
# Lead-ins ("make sure you play ...", "I want to hear ...") and tails ("... today", "... for the maid while she cleans"): words a request carries that are never part of a name.
# Tails come off the END only and never right after song / album / playlist / by ("play the song tomorrow never knows", "songs by Tonight Alive", "today's top hits"): the first
# version cut them out anywhere and broke those names (found by the library check, 2026-11-04).
_LEADIN = re.compile(r"^(?:(?:please|just|also|then|and|so|ok|okay|now|hey)\s+|(?:make sure|be sure|see that|ensure|remember to|don'?t forget to)(?: that)?(?: you| to)?\s+"
                     r"|(?:i(?:'d| would|'ll| will)? (?:like|want|need)|i wanna) you to\s+|go ahead and\s+|(?:can|could|would|will) you(?: please)?\s+|why don'?t you\s+)")
_WANT = re.compile(r"^(?:i want to|i wanna|i'd like to|i would like to|let me|let'?s) (?:hear|listen to)\s+|^(?:i(?:'d| would) like|i want|i need|i wanna)\s+(?!to\b|you\b)")
_TAIL = re.compile(r"\s+(?:please|thanks|thank you|again|(?:right |just )?now|today|tonight|for now|for a while|every ?day|all (?:day|night|morning|afternoon|evening)(?: long)?"
                   r"|this (?:morning|afternoon|evening)|for (?:me|us|them|him|her|everyone|everybody|(?:the |my |our )?(?:maids?|housekeepers?|cleaners?|cleaning (?:crew|lady|people)|guests?|party|kids?|dogs?|baby|company))"
                   r"|(?:while|because|since|so that) (?:she|he|they|we|i|you|it|the (?:maid|cleaner|guests|kids))\b.*|(?:please )?skippy)$")
_NAME_WORD = re.compile(r"\b(?:songs?|album|playlist|by)\b")
_NAME_INTRO = re.compile(r"\b(?:songs?|album|playlist|by|called|named)$")
# "play metal tomorrow", "at 8", "when she gets here" are not "play now": the model (or an alarm) hears them.
_DEFER = re.compile(r"\b(?:tomorrow|later|at \d|at (?:noon|midnight)|in (?:an?|\d+|a few) (?:hours?|minutes?|mins?)|when|once|after|before|until|next (?:week|month)|this weekend"
                    r"|(?:on )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b")
_LEAD = re.compile(r"^(?:(?:tell|ask|have|get) bilby (?:to )?|bilby,? )")
_VERB = re.compile(r"^(?:play|shuffle|put on|start|turn on|throw on|give me|let'?s hear)\b")
_NOT_PLAY = re.compile(r"\b(?:spotify|audiobooks?|books?|podcasts?|radio|news|pause|stop|skip|next|previous|louder|quieter|volume|down|up|what|who|which|videos?|movies?|films?|episodes?)\b")
_MODES = [("discovery", re.compile(r"\b(?:(?:on|in) )?(?:the )?discover(?:y)?(?: mode)?\b")),
          ("favorites", re.compile(r"\b(?:(?:on|from|in) )?(?:my |his )?(?:favou?rites?(?: (?:music|songs|tracks|list))?|(?<!whole )(?<!entire )library)(?: mode)?\b"))]
_FILLER = re.compile(r"\b(?:play|shuffle|put on|start|turn on|throw on|give me|let'?s hear|bilby|music|songs?|tracks?|tunes|some|my|his|the|me|a few|"
                     r"of|from|mode|please|now|for me|to|all|any|stuff|everything|anything|a bit of|a little|and)\b")
_ONLY_A_STOP = {"off", "out", "mute", "muted", "quiet", "silence", "silent", "enough", "quit", "sleep"}      # what is left of "bilby, music off" once the filler words are gone
# What is left of "bilby, turn off DJ mode" / "tell Bilby to turn off shuffle" / "bilby, kill the dj": a way of switching something OFF, which is for the model (the dj_mode tool), never a song to look for.
# Searched for, nothing matched and the closest, the whole library, started on shuffle (round 2 of the 2026-11-05 review: the stop guards had sent these here).
_OFF_FORM = re.compile(r"^(?:turn|switch|shut|power|cut|kill|end|halt|quit|mute|stop|pause|silence|disable|cease|no more|enough|i want|i need)\b|\b(?:off|out)$")

def fix_bilby(heard: str) -> str:
    return BILBY_HEARD.sub("Bilby", heard or "")


def _words(heard: str) -> str:
    """The request's words, lower case: the wake word, the lead-ins ('make sure you', 'can you', 'I want to hear' = play) and the punctuation off."""
    w = re.sub(r"[^\w'&\- ]+", " ", re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", fix_bilby(heard).lower()).strip(" ?!.,")).strip()
    while (n := _WANT.sub("play ", _LEADIN.sub("", w, count=1).strip())) != w:
        w = n
    return w


def _peel(w: str, updown: bool = False) -> tuple[str, list[str], bool]:
    """(w without its places, 'on the stereo' and its tails; the speakers named, in order said; whether 'on the stereo' was said)."""
    said: list[str] = []

    def take(m):
        place = m.group("place")
        if place == "house" and "the" not in m.group(0).split():
            return m.group(0)                                           # 'play house music': a genre, not the whole house
        if _NAMED_BY.search(m.string[:m.start()]):
            return m.group(0)
        said.append(_SPEAKER.get(place) or _SPEAKER["the " + place])
        return " "
    w = re.sub(r"\s+(?:and|or|plus|also)$", "", _WHERE[updown].sub(take, w).strip())        # 'upstairs and downstairs' leaves an 'and'
    on_stereo = bool(_STEREO.search(w))
    w = " ".join(_STEREO.sub(" ", w).split())
    while (m := _TAIL.search(w)) and not _NAME_INTRO.search(w[:m.start()]) and not _NAME_WORD.search(w[m.start():]):
        w = w[:m.start()]                                               # tails come off the END, never out of a name ('the album today')
    return w, said, on_stereo


def _speaker(said: list[str]) -> str:
    """The speaker for the places said: the same one twice is that one; two different ones ("upstairs and downstairs") are everywhere."""
    return said[0] if len(set(said)) == 1 else "everywhere"


def _deferred(w: str) -> bool:
    """'play metal tomorrow', 'at 8', 'when she gets here' are not 'play now'. Names after song / album / by are left alone."""
    return "playlist" not in w and bool(_DEFER.search(re.split(r"\b(?:songs?|album|by|called|named)\b", w, maxsplit=1)[0]))


_SPLIT = re.compile(r"(?:(?<=[a-z]{3}[.])|(?<=[!?;]))\s+(?=[A-Z])")           # a sentence ends here, but not after 'Mr.' or 'U.S.'
_ASKS = re.compile(r"^(?:move|send|switch|take|bring|shift|route|put|have|get|play|go|change)\b")


def _asks(p: str) -> bool:
    w = _words(p)
    return bool(_LEAD.match(w + " ") or _VERB.match(w) or re.match(r"^(?:music|bilby)\b", w) or _ASKS.match(w))


def _clause(heard: str) -> str:
    """The sentence that is the request. The others explain it ("Play it on the upstairs stereo. The maid is coming over to clean and they like heavy metal") unless they are a
    lone place or tail ("Play metal. Upstairs."): then the whole is kept."""
    parts = [p for p in _SPLIT.split((heard or "").strip()) if p.strip()]
    ask = [i for i, p in enumerate(parts) if _asks(p)]
    if len(parts) < 2 or not ask:
        return heard
    return " ".join([parts[ask[0]]] + [p for i, p in enumerate(parts) if i != ask[0] and not _asks(p) and _peel(_words(p))[0] == ""])


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
    Places: everywhere (both), upstairs / living room / Enclave, downstairs / theater / Sony. Every place said, the lead-ins and the tails (when and for whom) are
    peeled off before what is left can become a name (2026-11-04); a request for later ("tomorrow", "at 8") is not play-now: None."""
    import mixes                                                  # noqa: PLC0415
    w = _words(_clause(heard))
    led = bool(_LEAD.match(w + " ")) or w == "bilby"
    w = _LEAD.sub("", w + " ").strip() if led else w
    if not (led or _VERB.match(w) or re.match(r"^(?:music|bilby)\b", w)) or _deferred(w):
        return None
    w, said, on_stereo = _peel(w, updown=True)
    if _NOT_PLAY.search(w):                                               # after the peel: 'up in the living room' is a place, not 'turn it up'
        return None
    out: dict = {"speaker": _speaker(said)} if said else ({"speaker": "stereo"} if on_stereo else {})        # "on the stereo": where it plays now
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
    genre_only = any(re.fullmatch(re.escape(g), bare) for g in mixes.GENRE_WORDS)                 # "play heavy metal upstairs": a genre needs no "music" after it
    if not (led or mode_said or genre_only or re.search(r"\b(?:bilby|music|songs?|tracks?|tunes|playlist|album|library|collection)\b", w)):
        return None                                                       # "play Pink Floyd upstairs": the model's call, as before
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
    if rest in _ONLY_A_STOP and not any(k in out for k in ("playlist", "artist", "album", "song")):
        return None                                           # "bilby, music off": what is left is a stop, not a song called "off" (the library was searched for it and shuffled when nothing matched)
    genre = next((g for g in mixes.GENRE_WORDS if re.fullmatch(rf"{re.escape(g)}(?: music| songs)?", rest)), None)
    if not genre and _OFF_FORM.search(rest) and not _VERB.match(w) and not any(k in out for k in ("playlist", "artist", "album", "song")):
        return None                                           # "bilby, turn off dj mode": the model's (a genre such as "power metal" is still a genre, and a play verb still plays)
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


# "it" is what already plays: "play it on the upstairs stereo" moves the music (`play`: an idle station starts the library there), it is no search for a song called "it".
_MOVE = re.compile(r"^(?:(?P<start>play|put|start)\s+(?:it|this|that|these|those)(?:\s+(?:back|over))?"
                   r"|(?:(?P<put>put)|move|send|switch|take|bring|shift|route|have|get)\s+(?:it|this|that|these|those|the music|the songs?|the tunes)(?:\s+(?:back|over))?"
                   r"|(?:switch|go|change|move)(?:\s+(?:back|over))?(?:\s+to)?)$")


def speakers_route(heard: str) -> dict | None:
    """The speakers tool's arguments for "play it on the upstairs stereo", "move it to the Enclave", "send it everywhere", "switch to downstairs"; None otherwise.
    The Captain, 2026-11-04: the model answered "Playing ... *turns an imaginary dial*" and called nothing, so the music stayed where it was and the
    housekeeper upstairs heard nothing. `play`: the verb was play or put, so an idle station starts the library there instead of only choosing speakers."""
    w0 = _words(_clause(heard))
    w, said, _ = _peel(w0, updown=True)
    m = _MOVE.match(w)
    if not said or not m or _deferred(w0) or _NOT_PLAY.search(w0):
        return None
    return {"what": "music", "place": _speaker(said), **({"play": True} if m.group("start") or m.group("put") else {})}


# One speaker's switch (the Captain, 2026-11-05: "a single audio stream going out and the devices decide if they are outputting that stream or not"): "turn the Sony off", "play it on the
# Sony too", "switch the Enclave on". Additive or subtractive, never a move: a place ("upstairs") is the group shortcut above. The Sony is also a TV, so "turn off the Sony" is a speaker
# only while the house is playing or when sound is named; the Enclave and a room's box are always speakers.
_DEV = (r"(?P<dev>(?:the |my |our )?(?:sony|enclave|(?:(?:living room|living|office|kitchen|bedroom|theater|theatre|downstairs|upstairs) )?(?:box|pod)))"
        r"(?P<snd>\s+(?:speakers?|sound|audio|stereo))?")
_DEV_OFF = [re.compile(p) for p in (
    rf"^(?:turn|switch|shut|power|cut|kill)\s+(?:off|out)\s+{_DEV}$",
    rf"^(?:turn|switch|shut|power|cut|kill)\s+{_DEV}\s+(?:off|out)$",
    rf"^mute\s+{_DEV}$",
    rf"^(?:stop|quit)\s+playing\s+(?:on|in|through|over|via)\s+{_DEV}$",
    rf"^(?:no|stop)\s+(?:more\s+)?(?:music|sound|audio)\s+(?:on|in|through|from)\s+{_DEV}$",
    rf"^(?:take|remove|drop)\s+{_DEV}\s+(?:off|out)(?:\s+of\s+(?:it|the music|the mix))?$",
    rf"^(?:don'?t|do not|never)\s+play\s+(?:it\s+|the music\s+)?(?:on|in|through)\s+{_DEV}$",
)]
_DEV_ON = [re.compile(p) for p in (
    rf"^(?:turn|switch|power)\s+on\s+{_DEV}$",
    rf"^(?:turn|switch|power)\s+{_DEV}\s+on$",
    rf"^(?:play|put|have)\s+(?:it|this|that|the music|the songs?)\s+(?:on|in|through|over|via)\s+{_DEV}\s+(?:too|as well|also)$",
    rf"^(?:also|and)\s+(?:play\s+)?(?:on|in|through)\s+{_DEV}$",
    rf"^(?:add|bring in|include)\s+{_DEV}(?:\s+(?:too|as well|also))?$",
)]


def device_route(heard: str, playing: bool | None = None) -> dict | None:
    """The speakers tool's arguments for one speaker's switch, or None. {"what": "music", "device": "the sony", "on": False}."""
    w = _words(_clause(heard))
    w = re.sub(r"\s+(?:please|now|for me)$", "", w).strip()
    for rxs, on in ((_DEV_OFF, False), (_DEV_ON, True)):
        for rx in rxs:
            m = rx.match(w)
            if m:
                dev = m.group("dev").strip()
                if re.search(r"\bsony\b", dev) and not (playing or m.group("snd") or re.search(r"\b(?:music|speakers?|sound|audio|stereo)\b", w)):
                    return None                                         # the Sony is a TV too: "turn off the Sony" with nothing playing is not about the music
                return {"what": "music", "device": dev, "on": on}
    return None


# The music tool's plain commands: his own reading first ("never play this again" needs its "again"), then the same words without a place or a "bit".
_MORE = [("louder", re.compile(r"^(?:make (?:it|the music) louder|turn up (?:it|the music|that|the volume|the stereo)|(?:a )?(?:bit |little )?louder|(?:it'?s )?too (?:quiet|soft))$")),
         ("quieter", re.compile(r"^(?:make (?:it|the music) (?:quieter|softer)|turn down (?:it|the music|that|the volume|the stereo)|(?:a )?(?:bit |little )?(?:quieter|softer))$")),
         ("pause", re.compile(r"^(?:turn (?:the music|the stereo) off|turn off (?:the music|the stereo))$")),
         ("next", re.compile(r"^play the next (?:song|track)$")),
         ("what", re.compile(r"^what (?:music|song|track) is (?:this|that|playing)$"))]


def _first(w: str, rxs) -> dict | None:
    for action, rx in rxs:
        if m := rx.match(w):
            if action == "volume":
                lv = m.group("level")
                return {"action": "volume", "level": int(lv) if lv.isdigit() else WORD_NUMBERS[lv]}
            return {"action": action}
    return None


# Stopping the house audio in every way he says it (the Captain, 2026-11-04: "stop Bilby" reached the model, which called nothing, and the metal played on for fifty minutes).
# Bilby plays everything, so a stop with an audio word, or with Bilby named, is always a stop; a bare "stop" / "enough" / "shut up" is one only while the house is playing
# (a person can say "stop" to anything). Never a conditional or a timed one ("stop the music when I leave", "in twenty minutes"): those are the model's.
_STOP_NOT = re.compile(r"\b(?:when|if|after|until|unless|before|once|while|because|don'?t|do not|never|not|can'?t|won'?t|without|tomorrow|tonight|later|minutes?|hours?|seconds?)\b"
                       r"|\b(?:in|at|for) (?:an? |\d)")
# The review of 2026-11-05 of the first version: (1) `all`, `all of` and `all the` were three ways to read the same words, so a transcript that repeated "all the" took exponential time (22 repeats:
# 1.5 s, 24: 8 s, and a regex does not let go of the GIL): `all(?: of)?` reads any run of them one way. (2) `everything` / `all of it` were audio words, so "turn off everything" paused Bilby and left the
# lights on: only stop / pause / silence ... everything is a stop (below). (3) `down` was a way of turning off, so "turn that music down" paused it: only shut / power down is (turning down is quieter).
_STOP_AUDIO = (r"(?:(?:the|that|this|those|these|my|his|some|any|every|all(?: of)?)\s+)*"
               r"(?:music|audio|sounds?|noise|stereo|speakers?|radio|shuffle|tunes?|songs?|tracks?|playlist|playback|spotify|dj(?: mode)?|mix|beats?"
               r"|audiobook|book|story|podcast|(?:heavy )?metal|rock|punk|goth|industrial|techno|jazz|classical|ambient|darkwave)(?:\s+music)?")
# The review of 2026-11-05, round 2: "DJ mode off" and "shuffle off" were the modes' switches (the dj_mode tool), but "turn off dj mode", "turn dj mode off" and "stop the dj" still PAUSED the music:
# the verb forms read dj and shuffle as audio words. DJ mode has its own tool, so `dj` is never a stop in any form (_NO_DJ). Shuffle is a mode's switch in the forms that turn something OFF
# ("turn off shuffle", "shuffle off", "no more shuffle": _NO_MODE), but "stop the shuffle" stops what plays, as test_sat_stop_bilby pins.
_NO_DJ = r"(?!.*\bdj\b)"
_NO_MODE = r"(?!.*\b(?:dj|shuffle)\b)"
_STOP_RX = [re.compile(p) for p in (
    rf"^{_NO_DJ}(?:stop|pause|halt|end|kill|cut|silence|mute|quiet|quit|cease)\s+(?:playing\s+)?{_STOP_AUDIO}$",
    r"^(?:stop|pause|halt|silence|mute|quiet|quit|cease)\s+(?:everything|all of it|it all)$",
    rf"^{_NO_MODE}(?:turn|switch|shut|power|cut|kill)\s+(?:off|out)\s+{_STOP_AUDIO}$",
    rf"^{_NO_MODE}(?:shut|power)\s+down\s+{_STOP_AUDIO}$",
    rf"^{_NO_MODE}(?:turn|switch|shut|power|cut|kill)\s+{_STOP_AUDIO}\s+(?:off|out)$",
    rf"^{_NO_MODE}(?:shut|power)\s+{_STOP_AUDIO}\s+down$",
    r"^(?:stop|quit|cease)\s+playing$",
    rf"^{_NO_MODE}(?:no more|enough(?:\s+(?:of|with))?)\s+{_STOP_AUDIO}$",
    rf"^{_NO_DJ}make\s+{_STOP_AUDIO}\s+stop$",
    rf"^{_NO_DJ}i\s+(?:want|need|would like)\s+{_STOP_AUDIO}\s+to\s+(?:stop|be\s+(?:quiet|stopped))$",
    rf"^{_NO_MODE}i\s+(?:want|need|would like)\s+{_STOP_AUDIO}\s+to\s+be\s+off$",
    # No verb: "music off", "Bilby, music off", "stereo off" (it used to start the library, searching it for a song called "off"). "DJ mode off" and "shuffle off" are the modes' switches, not a stop.
    rf"^{_NO_MODE}{_STOP_AUDIO}\s+off$",
    rf"^{_NO_MODE}no\s+{_STOP_AUDIO}$",
    rf"^{_NO_MODE}(?:that'?s|that is|it'?s|it is)\s+enough\s+{_STOP_AUDIO}$",
    rf"^{_NO_MODE}(?:i'?m|i am)\s+(?:done|finished|through)\s+with\s+{_STOP_AUDIO}$",
)]
_QUIETER = re.compile(rf"^(?:turn\s+{_STOP_AUDIO}\s+down|turn\s+down\s+{_STOP_AUDIO})$")            # "turn that music down", "turn the sound down", "turn down the radio": the volume, never a stop
_STOP_BARE = re.compile(r"^(?:stop|pause|halt|enough|enough already|that'?s enough|shut up|be quiet|quiet|silence|mute|mute it|mute that|stop it|stop that|stop this|stop all that"
                        r"|cut it|cut it out|kill it|turn it off|turn that off|turn this off|turn off|switch off|shut off|power off|shut down|shut it off|shut that off|shut it down"
                        r"|make it stop|make that stop|knock it off|no more|off)$")


def stop_route(heard: str, playing: bool | None = None) -> dict | None:
    """{"action": "pause"} when he is stopping what the house plays (music or a book), else None. `playing`: the house audio is on (None: not known, so the bare forms stay the model's)."""
    s = re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", (heard or "").lower()).strip(" ?!.")
    s = re.sub(r"[,;]+", " ", s)
    s = re.sub(r"^(?:(?:please|just|hey|ok|okay|so|and|well|um|uh|now)\s+)+", "", s.strip())
    s = re.sub(r"^(?:(?:can|could|would|will) you(?: please)?|i (?:want|need|would like) you to|go ahead and|you can)\s+", "", s)
    s = " ".join(s.split())
    for _ in range(2):
        s = re.sub(r"\s+(?:please|now|right now|for me|thanks|thank you|already|immediately|at once|everywhere)$", "", s)
    if not s or _STOP_NOT.search(s):
        return None
    named = bool(re.search(r"\bbilby\b", s))
    if named:                                                         # "stop Bilby", "Bilby, stop", "tell Bilby to stop playing": Bilby is the audio word
        s = " ".join(re.sub(r"\b(?:(?:tell|ask|have|get|make|let)\s+)?bilby(?:\s+to)?\b", " ", s).split())
    if any(rx.match(s) for rx in _STOP_RX):
        return {"action": "pause"}
    if (named or playing) and _STOP_BARE.match(s):
        return {"action": "pause"}
    return None


def music_route(heard: str, playing: bool | None = None) -> dict | None:
    w = re.sub(r"\s+(?:please|now)$", "", re.sub(r"^(?:please|can you|could you)\s+", "", re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", (heard or "").lower()).strip(" ?!.,")))
    if got := _first(w, MUSIC_RX):
        return got
    w = re.sub(r"\s+(?:a (?:bit|little|lot)|some|more)$", "", _peel(_words(heard))[0])
    # the last: with the places peeled off, as the commands above. What is left of "turn off the living room" is a bare "turn off" that has lost its object: only an audio word or a named Bilby
    # decides there, never "the house is playing" (the review of 2026-11-05: with the station playing, "turn off the theater" / "turn it off downstairs" paused Bilby and the lights stayed on)
    return _first(w, [*MUSIC_RX, *_MORE, ("quieter", _QUIETER)]) or stop_route(heard, playing) or stop_route(w, None)


# The persona file is shared with flask-api's phone chat, and its example answers teach the denial: "I don't have live weather data right now. Check a weather app" and "I cannot set timers
# directly from here, but Android's clock app is right there" are what the house voice then said, nearly word for word (the review of 2026-11-05; the capability list further down the
# prompt cannot outvote a worked example). The house voice leaves out every example whose answer says "I cannot / I don't ..."; flask-api keeps the file as it is.
_EXAMPLE = re.compile(r"\n+Q: [^\n]*\nA: [^\n]*(?:\n[ \t]+[^\n]*)*")
_SAYS_NO = re.compile(r"\bI (?:cannot|can't|can not|do not|don't)\b", re.I)


def house_persona(text: str) -> str:
    return _EXAMPLE.sub(lambda m: "" if _SAYS_NO.search(m.group(0)) else m.group(0), text)


def load_persona() -> str:
    try:
        return house_persona(PERSONA_FILE.read_text(encoding="utf-8"))
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
                 car=None, physics=None, sleep=time.sleep):
        self.client, self.model, self.timers, self.clock = client, model, timers, clock
        self.sleep = sleep                                            # between tries when the model went away (tests pass a no-op)
        self.retries = 0                                              # how many tries the last reply needed past the first (the house log)
        self.hass = hass                                              # a hass.Hass when Home Assistant is connected, else None
        self.extra_tools, self.run_extra = extra if extra else ([], None)   # more tools from where the brain lives (hub.py: the room's music)
        self.persona = persona if persona is not None else load_persona()
        self.now = now or (lambda: time.strftime("%A, %B %d, %Y, %H:%M"))   # the date too: with only the weekday, Core said "November 22, 2025" (2026-10-31)
        self.history: list[dict] = []
        self.actions: list[dict] = []                                 # the tools called for the last reply (the house log reads them): {tool, args, outcome}
        self.guard: tuple = (None, None)                              # what the music claim guard did for the last reply: (reask|retract|None, claim|loose|silent|None)
        self._last = 0.0
        self.pre_route = None                                         # heard -> tool args for plain commands the model is unreliable at (hub.py: the audiobook); None = the model decides
        self.audio = None                                             # houseaudio.Audio (hub.py): what Bilby plays now, said in the prompt and read by a bare "stop"
        self.news = news                                              # news.News: the `news` tool, "about what?", and the headlines it found last
        self.weather = weather                                        # weather.Weather: the `weather` tool (NWS, NOAA tides and buoys, Open-Meteo)
        self.car = car                                                # car.Car: the `car_status` tool (the Captain's Tesla, read only)
        self.physics = physics                                        # physics.Physics: the `ask_mentat` tool (Mentat does the physics, PLATINUM 21)
        self.offered: dict | None = None                              # what he brought up himself (a conversation starter) and its sources: {"note", "at"}
        self.timing: dict = {"route": None, "model_s": [], "tools_s": 0.0, "total_s": 0.0}     # where the last reply's time went (reply() fills it, the hub logs it)

    def _lap(self, key: str, seconds: float) -> None:
        """Add to the last reply's timing. Never raises: a number for the log is not worth a reply."""
        try:
            if key == "model_s":
                self.timing["model_s"].append(seconds)                # one entry per model call, in order (a call made again after the model went away counts once, waits included)
            elif key == "tools_s":
                self.timing["tools_s"] += seconds
            else:
                self.timing[key] = seconds
        except Exception:                                             # noqa: BLE001
            pass

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
        bilby = BILBY if any(t["name"] == "play_music" for t in self.extra_tools) else ""
        return (self.persona + SPOKEN.format(now=self.now(), house=house, nohouse=nohouse, music=music) + layout + bilby + self.abilities() + books + news
                + (weather_mod.PROMPT if self.weather is not None else "") + (car_mod.PROMPT if self.car is not None else "")
                + (physics_mod.PROMPT if self.physics is not None else "") + offered + self.live())

    def abilities(self) -> str:
        """What this voice can do, from the tools it really has: so it neither denies a tool nor claims one it lacks (2026-11-04). Out of reach: only what has no tool."""
        names = {t["name"] for t in self.tools()}
        gone = [said for tool, said in ELSEWHERE.items() if tool not in names]
        chat = (f"The Skippy chat on makerportal.live has {' and '.join(on for tool, (_, on) in ELSEWHERE.items() if tool not in names)}, and the private Skippy there writes code. "
                if gone else "The private Skippy on makerportal.live writes code. ")
        can = "; ".join(phrase for phrase, tools in CAPS if names & set(tools)) or "set timers"
        return ABILITIES.format(names=", ".join(sorted(names)), can=can, gone="".join(out for out, _ in gone), chat=chat)

    def live(self) -> str:
        """The house audio right now (houseaudio.Audio.status), last in the prompt: what is playing, where, a book, a routine about to start the library."""
        if self.audio is None:
            return ""
        try:
            return "\n\n## Right now in the house\n" + self.audio.status()
        except Exception:                                             # noqa: BLE001 — the prompt must never fail because the radio is away
            return ""

    def _house_playing(self) -> bool | None:
        """Is anything playing in the house (the station or a book)? None when this brain cannot tell, so a bare "stop" is left to the model."""
        try:
            return bool(self.audio.playing()) if self.audio is not None else None
        except Exception:                                             # noqa: BLE001
            return None

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
        began = time.monotonic()
        try:
            out = self._run_tool(name, args)
        finally:
            self._lap("tools_s", time.monotonic() - began)
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
        if name == "car_status" and self.car is not None:
            return self.car.run(args)
        if name == "ask_mentat" and self.physics is not None:
            room = getattr(self, "room", "")
            return self.physics.run(args, room=room, trace=f"house-{room or 'room'}-{int(self.clock())}")
        if self.hass is not None and name in {t["name"] for t in hass_mod.TOOLS}:
            return hass_mod.run_tool(self.hass, name, args)
        if self.run_extra is not None and name in {t["name"] for t in self.extra_tools}:
            return self.run_extra(name, args)
        return "I have no such tool."

    def tools(self) -> list[dict]:
        return (TOOLS + (hass_mod.TOOLS if self.hass is not None else []) + list(self.extra_tools) + (self.news.tools() if self.news is not None else [])
                + (self.weather.tools() if self.weather is not None else []) + (self.car.tools() if self.car is not None else [])
                + (self.physics.tools() if self.physics is not None else []))

    def _create(self, need_words: bool = False, patience: float | None = None, **kw):
        """One model call, made again with backoff while the model is away (transient()), up to RETRY_FOR_S (or `patience`: an optional call, with the room deaf while it waits, passes a short
        one). `need_words`: a reply with neither words nor a tool call is the model dropping out too. Raises the last failure when the window runs out. Timed (self.timing)."""
        began = time.monotonic()
        try:
            return self._create_waiting(need_words, RETRY_FOR_S if patience is None else patience, **kw)
        finally:
            self._lap("model_s", time.monotonic() - began)

    def _create_waiting(self, need_words: bool, window: float, **kw):
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
                if not transient(e) or time.monotonic() - started + pause > window:
                    raise
                self.retries += 1
                print(f"[brain] the model went away ({type(e).__name__}): asking again in {pause:.0f} s", flush=True)
                self.sleep(pause)

    def reply(self, heard: str) -> str:
        """What Skippy says to what was heard. A model that went away is asked again (_create); raises what is left when it stays away
        (the caller decides what to do aloud). Afterwards `self.timing` says where the time went: {"route": the pre-route's tool or None, "model_s": [seconds of each model call, in order],
        "tools_s": the seconds in tools in all, "total_s": the seconds in this reply}; zeroed at the start of every reply, never raises."""
        began = time.monotonic()
        self.timing = {"route": None, "model_s": [], "tools_s": 0.0, "total_s": 0.0}
        try:
            return self._reply(heard)
        finally:
            self._lap("total_s", time.monotonic() - began)

    def _note(self, key: str, value) -> None:
        try:
            self.timing[key] = value
        except Exception:                                             # noqa: BLE001 — timing is never worth a reply
            pass

    def _reply(self, heard: str) -> str:
        now = self.clock()
        self.actions = []
        self.guard = (None, None)
        self.retries = 0
        if now - self._last > FORGET_AFTER_S:
            self.history = []                                         # a new conversation after ten quiet minutes
        self._last = now
        heard = fix_bilby(heard)                                      # Whisper writes "Bill be" / "Bill B" for Bilby (2026-11-01)
        messages = self.history[-2 * MEMORY_TURNS:] + [{"role": "user", "content": heard}]
        text = ""
        if self.news is not None and news_mod.is_bare_request(heard):  # "what's the news?": about what, before any model runs
            self.history = (self.history + [{"role": "user", "content": heard}, {"role": "assistant", "content": news_mod.ASK_WHAT}])[-2 * MEMORY_TURNS:]
            self._note("route", "news")
            return news_mod.ASK_WHAT
        tool, routed = "audiobook", (self.pre_route(heard) if self.pre_route is not None else None)
        if not routed and self.news is not None:
            tool, routed = "news", self.news.pre_route(heard, self.history)   # "politics", in answer to "news about what?"
        if not routed and self.car is not None:                      # before the thermostat and the weather: "how warm is it in the car" is the car
            tool, routed = "car_status", car_mod.pre_route(heard)    # left to itself, Core said "I do not have access to your car's systems" and called nothing (2026-11-04)
        if not routed and self.physics is not None:                  # before the weather: "the temperature of the sun" is Mentat's
            tool, routed = "ask_mentat", physics_mod.pre_route(heard)   # a physical number comes from Mentat, never from Core's memory (PLATINUM 21)
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
            tool, routed = "speakers", device_route(heard, self._house_playing()) or speakers_route(heard)   # "turn the Sony off" is one switch; "play it on the upstairs stereo": the music moves, it is not searched for (2026-11-04)
        if not routed and any(t["name"] == "music" for t in self.extra_tools):
            tool, routed = "music", music_route(heard, self._house_playing())   # "make it louder", "play the music again" (resume): before the grammar, which would search for "again"; "stop Bilby", "enough" (2026-11-04)
        if not routed and any(t["name"] == "play_music" for t in self.extra_tools):
            tool, routed = "play_music", bilby_route(heard)          # "bilby everywhere": the hub didn't know Bilby was his music (2026-11-01)
        if routed:                                                    # the tool runs first; the model only words the answer, with no tools to forget to call
            self._note("route", tool)
            result = self.run_tool(tool, routed)
            label = {"audiobook": "Already done by the house for this request"}.get(tool, f"The {tool} tool's answer for this request")
            messages[-1] = {"role": "user", "content": f"{heard}\n\n[{label}: {result}]"}
            try:
                resp = self._create(need_words=True, model=self.model, max_tokens=200 if tool == "audiobook" else 600, system=self.system(), messages=messages)
                text = " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            except Exception:                                         # noqa: BLE001 — it happened (and stayed away): say it plainly
                text = ""
            text = PHANTOM.sub("", text).strip()
            text = text or {"audiobook": result.split(") ", 1)[-1], "news": "I found the headlines, and then my brain wandered off. Ask me again.",
                            "car_status": "I looked at the car, and then my brain wandered off. Ask me again.",
                            "ask_mentat": "Mentat answered, and then my brain wandered off. Ask me again."}.get(
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
        prev = next((m["content"] for m in reversed(self.history) if m["role"] == "assistant" and isinstance(m["content"], str)), "")
        action, why = musicguard.classify(heard, prev, text, [a["tool"] for a in self.actions], happened=self._music_happened)
        self.guard = (action, why)                                    # the house log keeps it: the next count of claims needs no log archaeology
        quiet = bool(text) and not self.actions                       # words and no tool: the only replies the guards below look at
        claimed = quiet and why != "history" and self._claims(text)      # ("history": musicguard saw a true answer about the past, checked against the house log)
        figure = quiet and self.physics is not None and physics_mod.unbacked(text) and physics_mod.askable(heard)
        # A denial is judged BEFORE the logging-only tier below and before CLAIMED (the review of 2026-11-05): musicguard files "start a pasta timer", "put on the kitchen light", "crank the heat",
        # "pause the timer" under the music commands, so its shadow tier won and 26 of 123 logged commands never got the second try; and "I cannot set an alarm from here" matched CLAIMED
        # ("set an alarm") and was asked "you said that, but NOTHING was done". A reply that also claims something in another clause, or gives a figure, takes those branches as before.
        denied = quiet and not claimed and not figure and denies(text) and not RISKY.search(heard) and not ASKS_ABILITY.search(heard)
        if action == "retract":
            print(f"[brain] claimed music he did not ask for: {text[:80]!r}: cut, no tool", flush=True)
            text = musicguard.strip_claims(text, musicguard.describing(heard))
        elif action == "reask" and why == "claim":
            print(f"[brain] claimed music with no music tool: {text[:80]!r}: asked again", flush=True)
            text = self._second_try(text, messages, REASK_MUSIC.format(heard=heard.replace('"', "'")), self.music_tools(), UNCLAIM_MUSIC)
        elif denied:
            print(f"[brain] said it cannot, and called no tool: {text[:80]!r}: asked again with the tools", flush=True)
            self.guard = ("reask", "denial")
            text = self._second_try_denied(text, messages, heard)
        elif action:
            print(f"[brain] (shadow, {why}) a music command and no music tool: {text[:80]!r}", flush=True)       # the loose and silent tiers: logged for a week, the reply stands
        elif claimed:
            print(f"[brain] claimed without a tool: {text[:80]!r}: asked again", flush=True)
            text = self._second_try(text, messages, ASK_AGAIN, self.tools(), UNCLAIM, tools_ran=lambda: bool(self.actions))
        elif figure:
            print(f"[brain] a physical figure with no Mentat call: {text[:80]!r}: Mentat asked now", flush=True)   # Core answered physics from memory, once as "Mentat" (2026-11-04)
            self.guard = ("force", "physics")
            text = self._ask_mentat_now(heard) or text                # a Mentat that cannot be reached is said, never a figure from memory
        text = text or "Hm. I had nothing to say, which is unlike me."
        self.history += [{"role": "user", "content": heard}, {"role": "assistant", "content": text}]
        self.history = self.history[-2 * MEMORY_TURNS:]
        return text

    def _ran_music(self) -> bool:
        return any(a["tool"] in MUSIC_TOOL_NAMES for a in self.actions)

    def music_tools(self) -> list[dict]:
        return [t for t in self.tools() if t["name"] in MUSIC_NAMES]

    def _music_happened(self, kinds=None) -> bool:
        """Does the house log show a music tool or a routine's music lately? musicguard asks only when a history answer ('Did you stop the music?' - 'Yes, at 7:31') claims one, and says
        which `kinds` of thing it claims ({"stop"} for 'I paused it'): the log must show those, not just any music. An audio that cannot be asked about a kind proves nothing for it."""
        try:
            if self.audio is None:
                return False
            return bool(self.audio.music_lately(kinds) if kinds else self.audio.music_lately())
        except Exception:                                             # noqa: BLE001
            return False

    @staticmethod
    def _claims(text: str) -> bool:
        """Does the reply say it set, added or cancelled something (CLAIMED)? A sentence that also says it CANNOT is no claim unless another clause of it claims: 'I cannot set an alarm
        from here, Captain.' names the very thing it refuses, 'I cannot reach the speakers, but alarm set for seven.' does not."""
        for s in musicguard.sentences(text):
            if CLAIMED.search(s) and (not denies(s) or any(CLAIMED.search(c) and not denies(c) for c in _CLAUSES.split(s))):
                return True
        return False

    def _second_try_denied(self, text: str, messages: list, heard: str = "") -> str:
        """One more go after "I cannot ..." with no tool called, told the tools it really has. A tool that runs is its answer; if none does, the first reply stands (it may be true).
        The menu is the safe part of the tool list (retry_tools): it looks at anything, changes something only when his words are an order, and never undoes or announces. The call is
        optional and the room is deaf while it waits, so it waits RETRY_PATIENCE_S at most, and nothing that goes wrong in it ever costs him the first reply."""
        ordered = not NOT_AN_ORDER.search(heard or "")
        tools = retry_tools(self.tools(), ordered)
        if not tools:
            return text
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": DENIED.format(names=", ".join(sorted(t["name"] for t in tools)), asked="" if ordered else ASKED_ONLY)}]
        new, last = "", ""
        try:
            for _ in range(3):
                resp = self._create(need_words=True, patience=RETRY_PATIENCE_S, model=self.model, max_tokens=600, system=self.system(), messages=messages, tools=tools)
                blocks = list(resp.content)
                new = " ".join(b.text for b in blocks if getattr(b, "type", "") == "text").strip()
                calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
                if not calls:
                    break
                new, results = "", []                                 # (words that came with a tool call are not the answer)
                for c in calls:
                    args = {k: v for k, v in (c.input if isinstance(c.input, dict) else {}).items() if k != "confirm"}      # (never with the model's own `confirm`: that is his yes, aloud)
                    if retry_may(c.name, args, ordered):
                        last = self.run_tool(c.name, args)
                        results.append(last)
                    else:
                        results.append(NOT_DONE)
                messages.append({"role": "assistant", "content": [self._dump(b) for b in blocks]})
                messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": r} for c, r in zip(calls, results)]})
        except Exception as e:                                        # noqa: BLE001 — the model stayed away (Dropped), answered with a failure, a tool broke: the first reply stands
            print(f"[brain] the second try failed ({type(e).__name__}): {'what it did stands' if self.actions else 'the first reply stands'}", flush=True)
        new = ANNOTATION.sub("", new).strip()
        if new and not self.actions:                                  # a call written as words ("[list_timers]"): through the same gate as a real one
            new = self._text_calls(new, messages, allow=lambda n, a: retry_may(n, a, ordered), patience=RETRY_PATIENCE_S)
        return (new or last or text) if self.actions else text

    def _ask_mentat_now(self, heard: str) -> str:
        """Mentat asked by the house, with his words as they were said, and Core only wording the answer (the routed flow: no tools to forget to call or to write as text)."""
        result = self.run_tool("ask_mentat", {"question": heard})
        messages = self.history[-2 * MEMORY_TURNS:] + [{"role": "user", "content": f"{heard}\n\n[The ask_mentat tool's answer for this request: {result}]"}]
        try:
            resp = self._create(need_words=True, model=self.model, max_tokens=600, system=self.system(), messages=messages)
            words = " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        except Exception:                                             # noqa: BLE001 — it stayed away: the plain apology below
            words = ""
        return ANNOTATION.sub("", TEXT_CALL.sub("", PHANTOM.sub("", words))).strip() or "Mentat answered, and then my brain wandered off. Ask me again."

    def _second_try(self, text: str, messages: list, nudge: str, tools: list[dict], fallback: str, tools_ran=None) -> str:
        """One more go after a claim with no tool: `nudge` says so, `tools` are what it may call. A tool that runs is its answer; otherwise its words stand only when they take
        the claim back or ask (musicguard.music_claimed: anything else is another claim), else `fallback`."""
        ran = tools_ran or self._ran_music
        messages += [{"role": "assistant", "content": text}, {"role": "user", "content": nudge}]
        new = ""
        for _ in range(3):
            try:
                resp = self._create(need_words=True, model=self.model, max_tokens=600, system=self.system(), messages=messages, **({"tools": tools} if tools else {}))
            except Dropped:
                break
            blocks = list(resp.content)
            new = " ".join(b.text for b in blocks if getattr(b, "type", "") == "text").strip()
            calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
            if not calls:
                break
            messages.append({"role": "assistant", "content": [self._dump(b) for b in blocks]})
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": self.run_tool(c.name, c.input)} for c in calls]})
        if ran():
            return new or text
        honest = bool(new) and not musicguard.music_claimed(new) and (bool(HONEST.search(new)) or "?" in new)
        return new if honest else fallback

    def _text_calls(self, text: str, messages: list, allow=None, patience: float | None = None) -> str:
        """A tool call written as words ("[list action="read" list="shopping"]") is run as the call it meant, and the model words the answer. `allow`, when given, is the gate of the
        second try after a denial (retry_may): a call it refuses is not run, and no call runs with the `confirm` the model wrote (that is his yes, aloud)."""
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
            if allow is not None:
                args.pop("confirm", None)
                if not allow(m.group(1), args):
                    results.append(NOT_DONE)
                    continue
            results.append(self.run_tool(m.group(1), args))
        print(f"[brain] a tool call written as text, run: {[m.group(0)[:60] for m in found]}", flush=True)
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "[The house ran the tool call you wrote as text: " + " / ".join(results)
                                                 + ". Now say the answer aloud, in plain words. Never write a tool call as text: call the tool.]"}]
        try:
            resp = self._create(need_words=True, patience=patience, model=self.model, max_tokens=600, system=self.system(), messages=messages)
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
