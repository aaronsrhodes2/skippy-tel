"""Skippy's quick brain for the house voice: the Anthropic Messages API (a small fast model) with the Skippy persona, a short memory of the last
few exchanges, and a few tools (timers now; the lights and the rest of the house when home automation is connected, see docs/portal roadmap 30).

It is NOT the agent: it has no shell, no files, no deploys. It talks, and it can set a timer. Everything it may do is the list in TOOLS.

Snappy like an Echo (the Captain, 2026-11-05): its prompt is one text all day in every room (what changes rides in Brain.context(), a note at the start of his message), Brain.warm()
reads that prompt into Core's cache ahead of time, answers are short, and the commands the model got wrong or slow are routed before it (timer_route, light_route, the music and list
routes): when the tool simply worked a phrase of a closed bank (ACKS, fixed_phrases()) is the whole answer and no second model call is made.
"""
from __future__ import annotations

import copy
import json
import re
import threading
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
from timers import length_label                                  # the name an unnamed timer gets ("5 minutes"): what "cancel the five minute timer" looks for

PERSONA_FILE = Path(__file__).resolve().parents[1] / "flask-api" / "app" / "skippy_persona.md"
FALLBACK_PERSONA = ("You are Skippy the Magnificent, the Elder AI: an ancient beer can of unimaginable power, sardonic, loyal, and secretly fond "
                    "of the filthy monkeys you babysit. Be funny, but always correct and useful.")
MEMORY_TURNS = 8
MEMORY_BLOCK = 4                      # the window slides by blocks: the 9th exchange drops the oldest four (5 to 8 are held), so the prompt Core caches only grows for four turns and is read again once, not every turn
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
    "so use plain words only: no markdown, no lists, no code, no URLs, no emoji, no stage directions in asterisks or brackets (never *sighs* or [laughs]: the voice reads them out), "
    "nothing that cannot be said aloud; say numbers, times and units the way a person would. "
    "Be brief, and keep the Skippy in you in few words: a spoken answer is one or two short sentences unless he asks for more, and once a tool has done what he asked, the confirmation is a few words. "
    "You act ONLY through your tools: never say you did something (a light, a plug, a timer, an alarm, the music) in a turn where you called no tool for it. If you called no tool, nothing happened, "
    "so call it first, then say what it answered. Your own voice (its speed, pitch or loudness) is not something a tool changes: if he asks, say so in a few words and call no tool (the audiobook tool's speed is for a book only). "
    "A plain request (a time, a light, a fact, a timer) gets just the answer, with a dig folded in only when it fits in a few words; a request for something made "
    "(a haiku, a joke, a limerick, a poem, a story, a recitation, an explanation) gets the WHOLE thing, performed, with a quip "
    "before or after it, in about a hundred words or fewer. Never describe the thing instead of giving it. Use your tools when one fits (timers{house}{music}): call them, never write a tool call as text, "
    "and never write [SKIPPY_LEARN] or [TTS_ADJUST] here (nothing strips them: they would be read aloud). "
    "{nohouse}He is standing in a room waiting for you, so get to it.\n"
    "His newest message starts with a note in square brackets, \"[Right now: ...]\". The note is the house's context, NOT something he said, and you never read it out: its first line is the clock "
    "(answer any question about the time or date from it, never from memory), then where he is, what the house audio is doing and anything you looked up lately. His own words follow the note."
)
# The prompt above is the same text all day in every room, on purpose (the Captain, 2026-11-05: "streamline it so it becomes snappy like Amazon Echo"). Core's chat model keeps one cache checkpoint
# about 500 tokens before the end of its last prompt, so a word that changes in the middle of a 5,600-token prompt (the minute, the room, what plays) made every turn read all of it again: 4-6 s
# cold against 0.6-1.0 s warm. What changes rides in Brain.context(), a note at the start of his newest message, which the stored history never keeps.

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
    "stop, it stays off. Asked what is playing, or on which speakers, read the 'Right now in the house' lines of the note that starts his message, or call music with what. "
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


RETRY_GROUPS = re.compile(r"\b(?:plugs|outlets|switches|everything|appliances|devices)\b|\b(?:all|every|each)\s+(?:the\s+)?(?:plug|outlet|switch)\b", re.I)      # (a whole set of plugs is every plug on it: the fridge, the router ...)
RETRY_GUARDED = re.compile(r"\b(?:garage|locks?|unlock|doors?|gates?|dead ?bolts?|locked|disarm|alarm system)\b", re.I)      # (what the HOUSE itself will not touch without his yes aloud: a second try may call them, with no `confirm`)

# The spoken yes for a lock, a door or the garage (fix3:guards, the second review's core#2). house.House.control answers a call without `confirm=true` with "X needs him to confirm out loud first. Ask him, and
# only call again with confirm set once he has said yes." but the `confirm` was the MODEL's to write, and it wrote it on the first call, with no yes said (the real Core, 5 of 6 sensitive orders: open and close the garage,
# lock the front door, "can you open the garage" and "let my brother in, unlock the door"; fix3:guards' own run, 11 of 12; with this 0 of 18, and the two-turn flow below opened it 6 of 6). Only HE says yes:
# Brain.run_tool drops the model's `confirm` from every home_control call unless (a) the last tool answer of
# the exchange before this one was that very question (CONFIRM_ASKED) for the same thing and the same action, asked at most CONFIRM_FOR_S ago, and (b) his words now are a short yes and nothing else (says_yes).
# One yes opens one door, once. (Latent while the vault leaves HASS_SENSITIVE unset: the lock and the garage are then invisible to the model. The garage-opener setup turns it on.)
CONFIRM_ASKED = "needs him to confirm out loud first"
CONFIRM_FOR_S = 120.0
_YES_CORE = frozenset("yes yeah yep yup yea ya sure ok okay alright affirmative confirm confirmed absolutely definitely correct proceed ahead".split())
_YES_FILLER = frozenset("please now then and just sir captain skippy hey thanks thank you i said that that's it for the a this them do go thing of course uh um er erm hmm well oh".split())      # (uh um er erm hmm well oh: what Whisper writes for a hesitation, fix4:guards. No "right": it is a yes-word only inside "that's right")
_YES_PHRASE = re.compile(r"\b(?:do it|go ahead|go for it|please do|that'?s right|that is right|i confirm|of course)\b")
_YES_THIS = frozenset("it that this them".split())
# "yeah right", "yeah, sure", "sure, sure": in English usually a no in a yes's clothes (fix4:guards, the third review's code#5). When in doubt the house asks again: a false no costs one more word, a false yes opens the garage.
_YES_SARCASM = re.compile(r"\b(?:yeah|yea|ya|yep|yup)\s+(?:right|sure)\b|\bsure\s+sure\b")


def says_yes(heard: str, asked: dict | None = None) -> bool:
    """Is what he said a plain yes and nothing else? Up to ten words, all of them yes-words ("yes", "yeah, go ahead", "do it", "sure, thanks", "I said yes") or the words of the very thing the house asked about
    (`asked` = {"name", "action"} of the call: "yes, open the garage door", "open it"), with at least one that says yes. A "no", a "but", a different door ("yes, open the front door"), another order ("yes, and
    turn off the lights") or nothing but "please" is not one."""
    words = re.findall(r"[a-z']+", ORDER_ADDRESS.sub("", (heard or "").strip()).lower())
    if not words or len(words) > 10:
        return False
    said = " ".join(words)
    if _YES_SARCASM.search(said):
        return False
    words = re.sub(r"\bthat's right\b", "that's", said).split()           # ("right" is a yes-word only in "that's right")
    thing = set(re.findall(r"[a-z']+", f"{(asked or {}).get('name') or ''} {(asked or {}).get('action') or ''}".lower())) - {"the", "a", "my", "our"}
    if any(w not in _YES_CORE and w not in _YES_FILLER and w not in thing for w in words):
        return False
    action = {w for w in re.findall(r"[a-z']+", str((asked or {}).get("action") or "").lower())}
    return bool(_YES_CORE & set(words) or _YES_PHRASE.search(said) or (action & set(words) and _YES_THIS & set(words)))


def _same_thing(a, b) -> bool:
    """Is the thing `a` names (the call he is about to make) the thing `b` names (what he was asked about)? All of b's words (less the small ones) are in a's: "Garage Door" and "the garage door" for "garage door", and
    "garage door" for "garage". The other way round it is NOT: "door" for "front door", "garage" for "garage door" is a vaguer name, and the house settles a vague name by itself (it could land on another door or lock);
    his yes is for what he was asked, so the house asks again (fix4:guards, the third review's code#3 C16: a false no costs one more word, a false yes opens the garage)."""
    wa, wb = (set(re.findall(r"[a-z0-9]+", str(x or "").lower())) - {"the", "a", "my", "our"} for x in (a, b))
    return bool(wa) and bool(wb) and wb <= wa


def is_read(name: str, args: dict | None) -> bool:
    """Does this tool call only LOOK (RETRY_READS, or an `action` of RETRY_READ_ACTIONS)? A look changes nothing."""
    action = str((args or {}).get("action") or "").strip().lower()
    return name in RETRY_READS or action in RETRY_READ_ACTIONS.get(name, ())


def retry_may(name: str, args: dict, ordered: bool) -> bool:
    """May a second try (after a denial, a bare acknowledgement or a claim) run this call? Looking always, changing only when his words are an order, undoing or announcing never, and never a thing that
    has to keep running or can burn (RISKY_THING: the router, the fridge, a pump, a CPAP, the oven ... fix3:guards, 2026-11-05: 8 of 8 were switched off by a second try) or a whole set of plugs. A lock, a door or the
    garage is not refused here: the call runs without its `confirm` and the house itself asks him to say yes aloud (Brain.run_tool)."""
    action = str((args or {}).get("action") or "").strip().lower()
    if name in RETRY_NEVER or action in RETRY_NEVER_ACTIONS.get(name, ()):
        return False
    if is_read(name, args):
        return True
    if name == "home_control":
        target = str((args or {}).get("name") or "")
        if (RISKY_THING.search(target) and not RETRY_GUARDED.search(target)) or RETRY_GROUPS.search(target):
            return False
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
# A second try that takes the claim back. "n't" counted anywhere, so a claim with a quip in it ("Timer set for ten minutes, Captain. Don't burn the kitchen down.", the real Core, 2026-11-05) was
# "honest" and stood: only the negations of what was done count (didn't, couldn't, can't ...), not "don't" / "doesn't", which a quip says to him.
HONEST = re.compile(r"\b(?:not|never|nothing|no|wrong|mistake|sorry|apologi[sz]e|cannot|unable|idiom|figure of speech)\b|\b(?:didn|couldn|wasn|isn|aren|weren|haven|hasn|hadn|wouldn|shouldn)['’]t\b|\b(?:can|won)['’]t\b", re.I)
# A reply that only ACKNOWLEDGES (the Captain's oldest complaint, "it said Done and did nothing"; on the real Core, 2026-11-05, with no tool called: 'Done.', 'Done, Captain. The kitchen lights are on.',
# 'Already done, Captain.', 'No trouble at all.' to "light up the kitchen for me" and its kind): a few words that open like an acknowledgement and ask nothing, to an order whose words name something
# one of this voice's tools does (ORDER_CUES). It gets the same second try as any claim, with all the tools. CLAIMED above judges the words of a claim; these judge a claim with no words in it.
BARE_ACK = re.compile(r"^\W*(?:(?:okay|ok|alright|all right|sure|fine|well|so|and|now|right)[.,!]*\s+)*(?:done|handled|got it|on it|will do|you got it|consider it done|right away|already (?:done|on|off|set)"
                      r"|no (?:problem|trouble)(?: at all)?|there(?: you go)?(?=\W|$)(?!\s+(?:is|are|was|were|will|would|has|have)\b)|all set|as you wish|coming right up|certainly|of course|absolutely|okay|ok|sure|alright|all right|fine"
                      r"|(?:off|on|up|down) it goes|(?:off|on)(?=[.!,]|$)"
                      r"|roger(?: that)?|aye(?: aye)?|you bet|gladly|affirmative|understood|yep|yup|yes|very well|no worries|your wish is my command"       # (fix2:guards: a few more ways to say it)
                      r"|(?:added|removed|cleared|set|paused|resumed|skipped|stopped|muted|unmuted|dimmed|brightened|lowered|raised|queued|cancel+ed|started)(?=\W|$)"      # (fix3:guards: the verb of the order, said as done: "Added, Captain.", "Paused.")
                      r"|\w+ it is(?=\W|$))\b", re.I)                                                                                                            # ("Louder it is.", "Upstairs it is.")
BARE_WORDS = 14                 # (a quip before the "Done." makes the reply longer than "Done, Captain. The kitchen lights are on.")
BARE_NAMING_WORDS = 25          # (fix3:guards) ... and a reply this long counts too when it NAMES the thing he ordered: "Fine, I'll switch on the kitchen lights for you. There. All done. You're welcome." is 15 words and was no "Done."
# The words of a service, said where no tool did one: "You're welcome" (nobody thanked it for nothing) and "as you wish" / "as you asked". In any sentence of a reply to an order, with nothing taken back.
ACK_TAIL = re.compile(r"\byou(?:'re| are) welcome\b|\bas you (?:like|wish|wanted|asked|requested|ordered|commanded)\b|\bas (?:requested|ordered)\b", re.I)
ORDER_CUES = (                  # (the tools that do it, the words of an order for it): the cue counts only while one of the tools is really here
    (("home_control",), re.compile(r"\b(?:lights?|lamps?|plugs?|outlets?|fans?|thermostat|heat(?:er|ing)?|brightness|brighter|brighten|dim(?:mer)?|percent|degrees|scenes?)\b"
                                   r"|\b(?:turn|switch|power|shut|cut|kill|put)\b[^.!?]*\b(?:on|off|out)\b", re.I)),            # (or any thing by its name: "turn the coffee maker on")
    (("set_timer", "cancel_timers"), re.compile(r"\btimers?\b", re.I)),
    (("set_alarm", "set_reminder", "cancel_alarm"), re.compile(r"\b(?:alarms?|remind(?:er|ers| me)?|wake me)\b", re.I)),
    (("play_music", "music", "speakers", "dj_mode", "spotify"), re.compile(r"\b(?:music|songs?|tracks?|volume|louder|quieter|play|pause|resume|skip|shuffle|playlist|album|stereo|speakers?|spotify|bilby|radio|dj|mute)\b"
                                                                            r"|\bturn\s+(?:it|that|this)\s+(?:up|down)\b", re.I)),            # ("turn it down a little": the volume, though no word of the music is in it)
    (("list",), re.compile(r"\blists?\b", re.I)),
    (("announce",), re.compile(r"\b(?:announce|broadcast)\b", re.I)),
)
# fix2:guards (the second review of the house speed change, 2026-11-05). The backstop above is insurance, so it has to be quiet and safe. (1) It fires only on an ORDER in shape (order_shaped): on the real
# Core 12 of 12 bare acknowledgements to statements ("I do not need the alarm on sunday", "the lights are off") got a second try, with odd meta-talk spoken and two lookups run. (2) Its second try goes
# through the gate of the one after a denial (retry_may: nothing is cancelled, cleared or announced on a bare "Okay.", and the model's own `confirm` is dropped). (3) It judges what would be SPOKEN
# (no stage direction), counts an acknowledgement in the first OR the last sentence of a short reply (a quip before the "Done."), and a plain claim of state to an order for the lights.
ORDER_VERBS = ("turn switch flip toggle put set start stop cancel kill cut shut dim brighten darken raise lower increase decrease bump crank make get let leave keep "
               "play pause resume continue skip shuffle queue mute unmute replay rewind add remove delete clear erase read show tell announce broadcast wake remind snooze schedule create take "
               "activate open close quiet silence list bring have give send move enable disable").split()               # (fix3:guards: "please bring the volume down a little" was no order; nor "have the music play in the kitchen", "give me a timer ...")
# (a verb that is also a noun or a word of an idiom opens an order only in the forms people order with: "light up", "power off", "end the timer"; not in "keep in mind", "get this", "let me think", "put simply")
_ORDER_VERB = "(?:" + "|".join(ORDER_VERBS) + r"|light up|power (?:on|off|down|up)|end (?:the|that|this|my|all|it))"
_ORDER_IDIOM = (r"(?!get this\b|keep in mind\b|tell you what\b|put (?:simply|differently|it this way)\b|make (?:sense|a difference)\b|let me (?:think|see|know|say|explain|tell|check|ask|finish|try)\b"
                r"|cut (?:costs?|corners|class)\b|play (?:it by ear|along|dead|ball|fair|safe|hooky|house|dates?|time|lists?|buttons?|menus?|mode|school|ground|room)\b|kill (?:time|the mood)\b)")        # (fix3:guards: "cut costs on the lights", "play it by ear", "kill time with a list")
_ORDER_FILLERS = r"(?:(?:and|so|but|well|then|now|also|just|ok|okay|alright|all right|yeah|yes|oh|um|uh|hmm|hey|right|kindly|please|go ahead and)\W+)*"
ORDER_ADDRESS = re.compile(r"^\W*(?:(?:hey|ok|okay)\s+)?(?:skippy|bilby)\b\W*", re.I)
ORDER_CLAUSES = re.compile(r"[.;!?,:]+\s*|\s+(?:and then|then)\s+", re.I)
ORDER_FRAME = re.compile(r"^(?:(?:can|could|would|will) you (?:please )?" + _ORDER_VERB + r"\b|(?:would|do) you mind\b|i(?:'d| would) like\b|i want\b|i need\b|we (?:want|need)\b|let'?s\b|how about\b)", re.I)
ORDER_NOT_FRAME = re.compile(r"^(?:i (?:want|need|would like|'d like) to (?:know|ask|say|understand|learn|find out|talk|tell|be|think|check|figure|go|sleep|leave|eat|rest|relax|work|study|call|shower|focus)\b"
                             r"|i need (?:to )?(?:a break|sleep|rest|coffee)\b)", re.I)                # (his own plans and needs; "I want to hear some jazz" is still a way to ask for it)
# fix3:guards (the second review, core#5 and code#6: "we need a new thermostat someday", "dim lighting makes me sleepy", "lights out at ten is the rule at camp", "turn signals are on the left",
# "play time is over" took a bare "Okay." for the answer to an order and were asked again; "please bring the volume down a little" was missed). A wish, a need or a liking followed by "to" and a verb that
# is no order verb ("I would like to thank you", "we need to talk about the thermostat", "I want to apologize for the timer") is a remark; so is "how about that music last night".
ORDER_TO_OTHER = re.compile(r"^(?:i|we)(?:'d| would)? (?:want|need|like) to (?!(?:" + "|".join(ORDER_VERBS) + r"|hear|listen)\b)", re.I)
ORDER_REMARK = re.compile(r"^how about that\b.*\b(?:last (?:night|week|time)|yesterday|earlier|ago)\b", re.I)
ORDER_WISH = re.compile(r"^(?:i|we)(?:'d| would)? (?:want|need|like)\b(.*)$", re.I)
ORDER_ABILITY_START = re.compile(r"^(?:can|could|would|will) you (?:please )?" + _ORDER_VERB + r"\b", re.I)
# Words that make "can you set X" an order for something particular: a determiner, a number, a place. Without them ("can you set timers", "can you turn on lights") it asks whether it can.
_ORDER_PARTICULAR = frozenset("the a an my our your his her their this that these those some any all every each both another one two three four five six seven eight nine ten eleven twelve twenty thirty "
                              "forty fifty sixty half quarter kitchen bedroom bathroom living dining office theater hall hallway garage porch deck attic basement study den laundry guest master front back "
                              "desk bed ceiling floor table upstairs downstairs everywhere here there it them me us".split())
# The verbs of a statement, said right after the noun an "order" begins with: "turn signals ARE on the left", "play time IS over", "set top boxes MAKE the lamp flicker". Not when a pronoun comes before it:
# "play the song we WERE listening to", "turn off the lights that ARE on" are orders with a clause in them.
_ORDER_PREDICATE = frozenset("is are was were am has have had does did make makes keep keeps need needs cost costs looks sounds seems feels means gets goes comes runs stays takes burns lasts".split())      # (not the bare "go", "stay",
#                            "get": "let the lights STAY on", "set the timer to GO off" are orders)
_ORDER_PRONOUN = frozenset("i we you they he she it that which who there this what and or but then also please just to".split())      # (and "and": "play some music and MAKE it loud" is two orders; "to": "set an alarm to MAKE noise")
_ORDER_FREE_TEXT = frozenset("play queue replay".split())                                                        # (what follows is a TITLE, any words at all: "play is this love", "play take on me"; the idioms in _ORDER_IDIOM are the statements)
_ORDER_CLAUSE_WORDS = frozenset("that if when while because since until so as whether how what which who where".split())      # ("turn off the lights if the kids ARE asleep": a clause inside the order)
_ORDER_TAKES_CLAUSE = frozenset("announce broadcast tell remind ensure".split()) | _ORDER_FREE_TEXT                    # (verbs that are followed by a sentence: "tell everyone dinner IS ready", or a title)
_ORDER_PARTICLES = frozenset("on off up down out in back over away".split())
ORDER_VERB_FIRST = re.compile(r"^" + _ORDER_FILLERS + _ORDER_IDIOM + _ORDER_VERB + r"\b", re.I)
ORDER_PLEASE = re.compile(r"\bplease\W*$", re.I)
_ORDER_DET = r"(?!\s+(?:the|a|an|my|our|your|his|her|their|this|that|these|those)\b)"
_ORDER_PLACE = (r"(?:kitchen|bedroom|bathroom|living room|living|dining room|dining|office|theater|hall|hallway|garage|porch|deck|attic|basement|study|den|laundry|guest|master|front|back|desk|bed|ceiling|floor|table|"
                r"reading|track|stove|upstairs|downstairs|all|every|both|each|these|those|everything|the|my|our|room|lights?|lamps?)")
ORDER_CURT_DIR = re.compile(rf"^(?:{_ORDER_PLACE}\s+){{0,3}}(?:lights?|lamps?|plugs?|outlets?|fans?|speakers?|stereo|music|volume)\s+(?:on|off|out|up|down|louder|quieter|softer)\b{_ORDER_DET}", re.I)     # "lights off in the bedroom"
ORDER_CURT = re.compile(r"\b(?:needs?|has|have|ought)\s+to\s+be\s+(?:on|off)\b|\bmust\s+be\s+(?:on|off)\b"                                                                                       # "the bedroom light needs to be on"
                        r"|^(?:next|previous|last)\s+(?:song|track)\b", re.I)                                                                                                                    # "next track"
# What a clause says about a time or a rule rather than about now: "we want the lights on a dimmer eventually", "lights off at nine is the rule for the kids", "dim lighting makes me sleepy" (fix3:guards).
ORDER_NEGATED = re.compile(r"\b(?:don'?t|do not|never|not|no longer|without|except|instead of|unless|nobody|nothing|someday|sometime|eventually|usually|normally|typically|generally|often|sometimes|always|whenever"
                           r"|rules?|makes)\b|n't\b", re.I)


def order_shaped(heard: str) -> bool:
    """Is what he said an ORDER in shape, not a statement that happens to name a light or a timer? Some clause of it (split at punctuation and "and then") opens with a verb ("turn off ...", "play ...",
    after "so", "please", "okay" ...), or with can / could / would you + a verb, would you mind, I want / I need / I would like, let's, how about, or is the curt "lights off in the bedroom" / "the
    bedroom light needs to be on"; or his words end with "please". A clause that says not / don't / never / except is no order ("please don't turn off the lights"); neither is "I want to know ...".
    "the lights are off", "my timer went off already", "I do not need the alarm on sunday", "someone turned the music up" are statements: a bare "Okay." to one is the right answer.
    fix3:guards: nor is a clause that defers or describes ("someday", "usually", "makes", "the rule"), a wish for something that is no tool's ("I would like to thank you", "we want a new stereo for
    christmas"), a question whether it can ("can you set timers"), or a noun phrase that only begins like an order, because a verb of a statement follows it ("turn signals ARE on the left",
    "play time IS over": _statement_after) or it is an idiom ("cut costs", "play it by ear")."""
    words = ORDER_ADDRESS.sub("", (heard or "").strip())
    for clause in ORDER_CLAUSES.split(words):
        c = clause.strip()
        c = c if ORDER_FRAME.match(c) else re.sub(r"^" + _ORDER_FILLERS, "", c, flags=re.I).strip()
        if not c or ORDER_NEGATED.search(c) or ORDER_NOT_FRAME.match(c) or ORDER_TO_OTHER.match(c):
            continue
        if ORDER_FRAME.match(c) and not (ORDER_REMARK.match(c) or _asks_ability(c) or _wish_off_topic(c)):
            return True
        m = ORDER_VERB_FIRST.match(c)
        if m and not _statement_after(c, m.end()):
            return True
        m = ORDER_CURT_DIR.search(c)
        if (m and not _statement_after(c, m.end())) or ORDER_CURT.search(c):
            return True
    return bool(ORDER_PLEASE.search(words)) and not ORDER_NEGATED.search(words)


def _statement_after(clause: str, end: int) -> bool:
    """Does a verb of a STATEMENT follow within a few words of where an order's verb (or the curt "lights off") ended in `clause`? "turn signals ARE on the left", "play time IS over", "set top boxes MAKE the
    lamp flicker", "fans on high MAKE the whole room cold": an order's verb is followed by what to do it to and where, not by another verb. A pronoun in front of it ("play the song we WERE listening to", "turn
    off the lights that ARE on") or "make sure" makes it an order with a clause in it."""
    verb = (re.findall(r"[a-z']+", clause[:end].lower()) or [""])[-1]
    if verb in _ORDER_TAKES_CLAUSE or re.match(r"(?:make|be) sure\b|see (?:to it|that)\b", clause, re.I):
        return False                                                  # ("announce that dinner is ready", "tell everyone the timer is done", "make sure the lights are off")
    words = re.findall(r"[a-z']+", clause[end:].lower())
    if words and words[0] in _ORDER_PARTICLES:
        words = words[1:]
    for i, w in enumerate(words[:6]):
        if w in _ORDER_PREDICATE:
            return i == 0 or (words[i - 1] not in _ORDER_PRONOUN and not _ORDER_CLAUSE_WORDS & set(words[:i]))
    return False


def _wish_off_topic(clause: str) -> bool:
    """"I want / I need / I would like ..." is an order only for something a tool does: a cue of ORDER_CUES within the first eight words of what he wants, not counting what he compares it with ("I need a
    vacation more than a timer" wants a vacation). "I would like to hear ..." is the way he asks for music."""
    m = ORDER_WISH.match(clause)
    if not m or re.match(r"\s*to (?:hear|listen)\b", m.group(1), re.I):
        return False
    if re.match(r"\s*(?:a|an|some|another|more)\s+(?:new|better|nicer|bigger|smaller|cheaper|quieter|louder)\b", m.group(1), re.I):
        return True                                                   # ("we want a new stereo for christmas", "I want a quieter fan": a thing to have, not something done)
    near = " ".join(re.findall(r"[a-z']+", m.group(1).lower())[:8]).split(" than ")[0]
    return not any(rx.search(near) for _, rx in ORDER_CUES)


def _asks_ability(clause: str) -> bool:
    """"can you set timers", "could you turn on lights": whether it can, not an order. After the verb comes a bare plural noun (one or two words, the last ending in -s) and nothing particular: no determiner,
    number, place or pronoun ("can you set a timer", "can you set timers for ten minutes", "can you set kitchen timers", "can you play music" are orders)."""
    m = ORDER_ABILITY_START.match(clause)
    if not m:
        return False
    rest = re.findall(r"[a-z']+|\d+", clause[m.end():].lower())
    if rest and rest[0] in _ORDER_PARTICLES:
        rest = rest[1:]
    if rest[-2:] in (["for", "me"], ["for", "us"]):
        rest = rest[:-2]
    if rest and rest[-1] == "please":
        rest = rest[:-1]
    if not rest or len(rest) > 2 or any(w in _ORDER_PARTICULAR or w.isdigit() for w in rest):
        return False
    return len(rest[-1]) > 3 and rest[-1].endswith("s") and not rest[-1].endswith(("ss", "us", "is"))


ACK_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
# A plain claim of state to an order for the lights, the plugs and the fans, with no tool called ("The kitchen lights are on, Captain." / "Lights on." / "I turned the lights off." / "Turning on the lamp."):
# a claim of what a house tool does, like CLAIMED above, but judged only against an ORDER for the house (Brain._acknowledges has his words; "are the kitchen lights on?" asks, and its answer is no claim).
_STATE_DET = r"(?!\s+(?:the|a|an|my|our|your|his|her|their|this|that|these|those|top|left|right|wall|table|floor)\b)"
STATE_CLAIM = re.compile(r"\b(?:lights?|lamps?|plugs?|outlets?|fans?|switch(?:es)?|thermostat|heat(?:er)?|everything)\s+(?:is|are)\s+(?:(?:now|already|all|both|currently|back|just)\s+)*(?:on|off|out|lit|dimmed)\b" + _STATE_DET
                         + r"|^(?:the\s+)?(?:[\w']+\s+){0,2}(?:lights?|lamps?|plugs?|outlets?|fans?)\s+(?:on|off|out)\b" + _STATE_DET
                         + r"|\b(?:(?:i|we)(?:'ve| have)?\s+)?(?:turned|switched|flipped|dimmed|brightened|powered)\b[^.!?]{0,40}\b(?:on|off|out|up|down)\b"
                         + r"|^(?:turning|switching|flipping|dimming|brightening|lighting|powering)\b[^.!?]{0,40}\b(?:on|off|up|down|out)\b", re.I)
# The same for the other tools (fix3:guards, the second review's core#8: the real Core answered "I need a pasta timer for ten minutes" with "Setting a timer for ten minutes." and called no tool): a sentence that
# says the thing is being done or was done, judged only against an order for it. The key is the first tool of the ORDER_CUES group. (A claim of music with a name in it is musicguard's.)
DONE_CLAIMS = {
    "home_control": STATE_CLAIM,
    "set_timer": re.compile(r"(?<!can )(?<!could )(?<!to )(?<!would )(?<!should )(?<!must )(?<!may )(?<!might )\b(?:set(?:ting)?|start(?:ed|ing)?|creat(?:ed|ing)|cancel+(?:ed|ing)|clear(?:ed|ing)|stopp(?:ed|ing))\b[^.!?]{0,40}\btimers?\b"
                            r"|\btimers?\b[^.!?]{0,20}\b(?:started|running|ticking|going)\b", re.I),             # (not "I can SET a timer", "to SET one": an offer is no claim)
    "set_alarm": re.compile(r"(?<!can )(?<!could )(?<!to )(?<!would )(?<!should )(?<!must )(?<!may )(?<!might )\b(?:set(?:ting)?|schedul(?:ed|ing)|creat(?:ed|ing)|cancel+(?:ed|ing))\b[^.!?]{0,40}\b(?:alarms?|reminders?)\b"
                            r"|\bi(?:'ll| will) (?:wake|remind) you\b", re.I),
    "list": re.compile(r"(?<!can )(?<!could )(?<!to )(?<!would )(?<!should )(?<!must )(?<!may )(?<!might )\b(?:add(?:ed|ing)|put(?:ting)?|remov(?:ed|ing)|clear(?:ed|ing)|cross(?:ed|ing)|tak(?:en|ing)|took)\b[^.!?]{0,40}\blists?\b", re.I),
    "announce": re.compile(r"\bannounc(?:ed|ing)\b", re.I),
}                                                                    # (no entry for the music: "Playing along: autumn moon ..." answers "play me a haiku"; what is claimed of the music is musicguard's, and BARE_ACK / the bank have "Paused.")


def bank_phrase(sentence: str) -> bool:
    """Is this sentence one of the closed bank the house says itself after a tool worked (fixed_phrases(): "Paused.", "Next.", "Volume five.", "Timer set for ten minutes.")? Said with no tool called it is a claim."""
    return sentence.strip().rstrip(".!").lower() in {p.rstrip(".").lower() for p in fixed_phrases()}


# A reply that says sorry and then still PROMISES or CLAIMS an action (fix4:guards, the third review's core#4; 3 of about 35 real second tries: "Right, my mistake. Let me cancel that timer for you.", "My mistake. Calling home_control
# to turn off everything now."): "mistake" and "sorry" in HONEST are apologies, not a retraction, so that was spoken as the answer with nothing done. announces() says it: a promise (let me ..., calling, I'll, I will, going to), a claim
# (I've cancelled ..., CLAIMED, DONE_CLAIMS) in a sentence that does not say not / never / nothing / cannot. A sentence with a condition or an offer ("Say it again and I'll do it") promises nothing (musicguard.sentences).
ANNOUNCES = re.compile(r"\blet me (?!know\b|explain\b|see\b|think\b|say\b|check\b|tell\b|finish\b|try\b|guess\b|be\b|ask\b|correct\b|clarify\b|apologi[sz]e\b)\w+|\bcalling\b|\bi(?:'ll| will)\b|\b(?:going to|gonna)\b"
                       r"|\bi(?:'ve| have| just| already)?\s+(?:cancel\w*|remov\w*|delet\w*|clear\w*|turned|switched|set|started|stopped|paused|skipped|added|announced|put|took|taken|done|fixed|changed|handled|played|queued|muted|dimmed)\b"
                       r"|^\W*(?:(?:alright|okay|ok|fine|right|well|so|now|and)\W+)*(?:done|handled|all set)\b|\bconsider it done\b"      # ("all set" opens a sentence: "The seven o'clock alarm is all set." is the answer of a look, review4:seams)
                       r"|^\W*(?:(?:alright|okay|ok|fine|right|well|so|now|and)\W+)*(?:cancell?ed|cleared|removed|deleted|announced)[.!\s]*$"      # (a sentence that is only "Cancelled." claims like "Done.": "Right. Cancelled. What alarm was it?" was spoken with both alarms set, review4:core)
                       r"|(?:^\W*(?:(?:alright|okay|ok|fine|sorry|right|well|so|now|and|oh|whoops|oops)\W+)*|\bi(?:'m| am) (?:now |just )?)[a-z]+ing\s+\S[^.!?]{0,40}?\b(?:now|right away|immediately|this (?:very )?(?:second|moment))\b", re.I)
#                      ("Setting that up now.", "I'm switching it off at this moment.": a sentence that BEGINS with the gerund, or is "I'm ...ing"; not "Gasoline is playing now." and not "Playing now: Gasoline.", the answers of a look)
_SAYS_NOT = re.compile(r"\b(?:not|no|never|nothing|nobody|cannot|unable|neither|nor|still)\b|n't\b", re.I)      # ("still": 'the timer is still running' says nothing was changed)
_NO_IDIOM = re.compile(r"\b(?:no|not a) (?:problem|worries|worry|trouble|bother|need|doubt|wonder)\b", re.I)         # ("No problem, I've turned them off." is no "no")


_ASKING = re.compile(r"\b(?:want|wanted|wish|need|needs|should|would|could|can|may|might|shall|please|which)\b", re.I)      # (a request or a question is no claim: 'the alarm you want cancelled', measured on the real Core)
_CLAIM_OPENS = re.compile(r"^\W*(?:(?:alright|okay|ok|fine|right|well|so|now|and)\W+)*(?:done|handled|all set|i(?:'ve| have| just| already|'ll| will)\b|let me\b)", re.I)      # (a sentence that BEGINS with the claim or the promise, whatever it ends in)


def announces(words: str, claims: bool = True) -> bool:
    """Does this reply still promise or claim an action, in a sentence that does not take it back with a not / no / never / nothing / cannot / n't / still? A promise or a perfect claim (ANNOUNCES) anywhere; with `claims`,
    also a claim of what is done (CLAIMED, a DONE_CLAIMS rule) in a sentence that is no question and no request ('Please say which alarm you want cancelled.' is both an honest answer and a CLAIMED match). Without
    `claims` only what the house says in the first person counts: a read answer is full of 'the alarm is set' and 'is on'."""
    for s in musicguard.sentences((words or "").translate(_PLAIN_QUOTES)):
        if ("?" in s and not _CLAIM_OPENS.match(s)) or _SAYS_NOT.search(_NO_IDIOM.sub(" ", s)):          # (a question promises and claims nothing: "Which alarm should I cancel?", "What time should I set it for?" are honest, review4:seams; but a sentence that OPENS with the claim and ends in a tag question still claims: "Done, happy now?", "I've cancelled it, okay?", review5)
            continue
        if ANNOUNCES.search(s) or (claims and "?" not in s and not _ASKING.search(s) and (CLAIMED.search(s) or any(rx.search(s) for rx in DONE_CLAIMS.values()))):
            return True
    return False


# What a quip says that is not a retraction ("Try not to trip over the furniture, monkey." has a "not" in it): taken out before HONEST looks for a retraction of the claim.
QUIP_NEGATIONS = re.compile(r"\b(?:try|trying|careful|remember|sure|please|make sure)\s+not\s+to\b|\bno need\b|\bnothing (?:better|else|more) to do\b|\bnot that\b|\bnever fear\b|\bno wonder\b", re.I)
# The backstop's nudge: the same words as ASK_AGAIN, naming only the tools its second try may use (retry_tools: the safe part of the list, see RETRY_NEVER).
ASK_AGAIN_ONLY = ("[The house: you said that, but you called no tool, so NOTHING was done. If he asked for it, call the right tool now (you may use: {names}), then say what it answered; "
                  "if he did not, say plainly what you meant.]")
# A tool call written as text that the house could not read (TEXT_CALL / _bare_action, or a bracket that starts with a tool's name and that TEXT_CALL cannot read at all, "[set_timer 90 tea]"): it is never run with no
# arguments (production's music answers "I cannot do that to the music."), the model is asked again.
TEXT_AGAIN = ("[The house: you wrote a tool call as text{named} and the house could not read it, so NOTHING was done. If he asked for it, call the tool itself now (the tool, not words about it), "
              "then say in one sentence what it answered; if he did not, say plainly what you meant.]")
BARE_FILLERS = {"with", "using", "action", "the", "a", "an", "to", "do", "please", "is", "as", "of", "value", "tool", "call", "now"}                  # words around the one enum word of "[music] with action \"next\""
# The persona is shared with flask-api, whose chat strips [SKIPPY_LEARN: {...}] / [TTS_ADJUST: {...}]; here they would be read aloud. Worse
# (2026-11-01): one such line in the memory and Core wrote every tool call after it as text, "[list action="read" list="shopping"]".
# Three shapes, none of them ever spoken (fix3:text, the second review's problem 1): the whole annotation (its JSON and the closing bracket); a bracket of the tag with no JSON in it; and one the token limit CUT
# (3 of the 6 real ones were cut by the 160-token cap), from its bracket to the END of the words. The cut one has no closing bracket, which the first pattern needed, and the cut-back to a whole sentence
# (whole_sentences) then landed INSIDE its text ("... Adds items. Reads lists.") and the rest of it was spoken: Brain._spoken strips it BEFORE that cut-back (strip_model_markup).
ANNOTATION = re.compile(r"\[(?:SKIPPY_LEARN|TTS_ADJUST)(?::\s*\{.*?\}\s*\]|\b[^\]{}]*\]|\b.*\Z)", re.S | re.I)
# Also "[play_music: what=everything, speaker=everywhere]" (a colon after the name, commas between: 2026-11-01, said "Done" and played nothing)
# (fix2:guards: also "[music with action=quieter]", and a closer right after it, "[music action=next][/music]")
TEXT_CALL = re.compile(r"\[(\w+):?(?:[\s,]+with)?((?:[\s,]+\w+=(?:\"[^\"]*\"|'[^']*'|[^\s,\]]+))*)\s*\](?:[ \t]*\[/\1\])?")
TEXT_ARG = re.compile(r"(\w+)=(?:\"([^\"]*)\"|'([^']*)'|([^\s,\]]+))")
TEXT_LINE = re.compile(r"(\w+)\s*[:=]\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s,\]]+))")        # one `key: value` line after a call written as "[music]" (Brain._call_lines); must be the whole line (fullmatch)
# A call written as words where no tool is on offer (a routed answer, 2026-11-04: Core, handed a Mentat answer that missed, wrote "[ask_mentat: What is the half-life of carbon-14?]"
# as its whole reply): it is never spoken, and the plain apology stands in. It knew two tools only (fix3:text, the second review's problem 7): Brain._phantom takes the name of ANY tool the brain has.
def phantom_pattern(names) -> re.Pattern:
    """A bracket that starts with the name of one of `names` ("[ask_mentat: What is ...]", "[set_timer seconds=90]"): a tool call written as words. Nothing matches when there are no names."""
    names = sorted(map(re.escape, names), key=len, reverse=True)
    return re.compile(r"\[(?:" + "|".join(names) + r")\b[^\]]*\]", re.I) if names else re.compile(r"(?!)")


PHANTOM = phantom_pattern(("ask_mentat", "car_status"))
# A tool call written in one of Core's OTHER dialects (fix4:guards, the third review's core#2; the real Core, "turn everything off": a code fence "```tool_call::home_control|name=everything|action=off```", and in a second reply
# "[call: cancel_timers(label="pasta")]", "[Calling cancel_timers with label pasta]", "[tool_call cancel_timers]"). Nothing RUNS these and TOOL_MARKUP knows XML only, so they were spoken. A code fence (to its closing fence, else
# to the end of the words), a bracket that OPENS with the verb of a call (call, calling, tool_call, use, run, invoke ...), a line from "tool_call" on. Ordinary speech has none of it ("[sighs]", "the tool was great", "I am calling it a night").
CALL_DIALECT = re.compile(r"`{3}.*?(?:`{3}|\Z)"
                          r"|\[\s*(?:tool[_ -]?calls?|function[_ -]?calls?|calls?|calling|called|invok(?:e|es|ed|ing)|us(?:e|es|ing)|run(?:s|ning)?|execut(?:e|es|ed|ing))\b[^\]\n]{0,200}\]"
                          r"|\btool_calls?\b[^\n]*", re.I | re.S)


def strip_call_dialects(words: str) -> str:
    """`words` without a tool call written as text in one of Core's other dialects (CALL_DIALECT). Words with none of it are returned as they are, byte for byte."""
    if not words or ("`" not in words and "[" not in words and "tool_call" not in words.lower()):
        return words
    out = CALL_DIALECT.sub(" ", words)
    return words if out == words else re.sub(r"\s+([.,!?;:])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()
# Skippy Core's own tool-call markup, written into its words ("<tool_call><function=weather><parameter=query>Gold Canyon</parameter></function></tool_call>", the review of 2026-11-05: what a call with no tools on
# offer answered after four routed commands, 6 of 8 times). Never spoken, never kept: a call to the end (or to the end of the words, when the model ran out of room inside it), a lone function or
# parameter, a stray tag. Brain._spoken strips it from every model reply, so no path can say it or store it. (Nothing RUNS it: only a structured call, or a bracket TEXT_CALL knows, does.)
# Also the picture sentinel of the persona file it shares with the portal chat, "[IMAGE: <prompt>]" (3 of 117 worded replies on the real Core ended with one, and it was read aloud): there is no picture here.
_TOOL_BLOCKS = r"<tool_call\b.*?(?:</tool_call\s*>|\Z)|<function=.*?(?:</function\s*>|\Z)|<parameter=.*?(?:</parameter\s*>|\Z)"
_TOOL_IMAGE = r"\[IMAGE:[^\]]*(?:\]|\Z)"
# (a stray tag, "</function>": its ">" never coming, it ran to the end of the text, and a scan from each of thousands of them was quadratic: _tool_markup cleans what is inside it with _TOOL_MARKUP_FLAT, which has no stray tags, since none can match there)
TOOL_MARKUP = re.compile(_TOOL_BLOCKS + r"|(?P<tag></?(?:tool_call|function|parameter)\b[^>]*(?:>|\Z))|" + _TOOL_IMAGE, re.I | re.S)
_TOOL_MARKUP_FLAT = re.compile(_TOOL_BLOCKS + "|" + _TOOL_IMAGE, re.I | re.S)
# Skippy Core's reasoning, when the server leaves it in the words (fix3:text, the second review's problem 6: 2 of 20 real text-call replies held a lone "</think>", "[music]\nnext\n</think>\n\n[music]\nnext"):
# a whole block; a closing tag whose opening was never written (everything before it is the reasoning); an opening tag the model ran out of room after (to the end). Never spoken, never kept. A tool call the model wrote
# as text INSIDE it is still the call it meant, though: "[music]" and the line "music(action='quieter')" (the real Core, 1 of 16 for "turn the music down a notch"; the text-call rescue ran it before the reasoning was cut,
# the third review's core#1). strip_model_markup puts such a call back in front of the words (_reasoned_calls).
# Each of these ends in an alternative for the end of the text, as TOOL_MARKUP's blocks do (fix4:text, the third review's core#10): a scan that finds no closing ends there, instead of starting again at every opening after it
# (quadratic: '<think>x' * 20000 took 11.5 s). What a match that ran to the end of the text covered is left as it was (_sweep cuts only a match with a `cut` group): the patterns that needed their closing matched nothing there.
THINK = re.compile(r"<think\b[^>]*(?:>.*?(?:(?P<cut></think\s*>)|\Z)|\Z)", re.I | re.S)
THINK_CLOSE = re.compile(r"\A(?P<cut>).*</think\s*>", re.I | re.S)
THINK_OPEN = re.compile(r"<think\b[^>]*(?:(?P<cut>>.*\Z)|\Z)", re.I | re.S)
# The note that starts his message, copied into a reply and cut by the token limit (NOTE_ECHO needs its closing bracket), and the words that END inside the name of a bracket tag the persona writes ("... [SKIPPY_LE"):
# neither is words. (strip_model_markup)
NOTE_CUT = re.compile(r"\[Right now:[^\]]*(?:\]|(?P<cut>\Z))")
CUT_TAGS = ("SKIPPY_LEARN", "TTS_ADJUST", "IMAGE")
CUT_TAIL = re.compile(r"\[([A-Za-z_]{0,12})\Z")
# The same for the NAME of one of Core's XML tags ("Sure thing <func", "</param") and for the start of the clock note ("[Right no"): a reply the token limit cut there, with no sentence end before it for whole_sentences to cut
# back to, spoke the stub (fix4:text, the third review's code3#7).
CUT_XML = ("think", "tool_call", "function", "parameter")
CUT_XML_TAIL = re.compile(r"</?([A-Za-z_]{0,9})\Z")
NOTE_STUB = re.compile("(?:" + "|".join(re.escape("[Right now:"[:n]) for n in range(10, 1, -1)) + r")\Z")


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


# Nor does it keep the persona's ANNOTATION sections (the Captain's decision, 2026-11-06, the third review's core3#9): "Keyword Memory" teaches the model to end a reply with [SKIPPY_LEARN: {...}] and "TTS Voice
# Adjustment" with [TTS_ADJUST: {...}] (and, at its end, a nickname example in SKIPPY_LEARN again). Only flask-api's chat and the portal read those; the house has no consumer, strips them and keeps
# nothing: on the real Core 12 of 18 provoked replies carried one and 6 of 18 were nothing else ("remember that the throne light is the bedroom lamp" -> "Hm. I had nothing to say, which is unlike me.").
# So the house voice leaves out every "## " section of the file that names one of the two tags: the whole section, found by its heading and never by line numbers (flask-api keeps the file as it is).
_ANNOTATED = re.compile(r"\b(?:SKIPPY_LEARN|TTS_ADJUST)\b", re.I)
_SECTION = re.compile(r"(?m)^(?=## )")


def house_persona(text: str) -> str:
    sections = _SECTION.split(text)                                   # (what comes before the first heading, then one part per "## " section, each from its heading to the next one)
    text = "".join(s for i, s in enumerate(sections) if i == 0 or not _ANNOTATED.search(s))
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


# ── The house voice, made quick (the Captain, 2026-11-05: "streamline it so it becomes snappy like Amazon Echo") ──────────────────────────────────────────────────────────────────────
# (1) the system prompt is one text all day (SPOKEN above; Brain.context() carries what changes), (2) Brain.warm() reads it into Core's cache before a turn needs it, (3) an answer is short
# (SPEAK_TOKENS, WORDING_TOKENS, whole_sentences, strip_stage_directions), (4) a command that simply worked is confirmed from a CLOSED bank of phrases with no second model call (ACKS,
# TRANSPORT_ACKS, timer_ack; fixed_phrases() is the whole bank, for the hub to render into the voice's cache ahead of time), (5) the commands the model kept getting wrong (the lights: it said
# "Done." and switched nothing, 2026-11-05) or slow (a timer) are routed before it: light_route, timer_route.
SPEAK_TOKENS = 300                    # a round that may call a tool, and the answer after it: a spoken answer is a few sentences at most, and a tool call needs little room
WORDING_TOKENS = 160                  # the model only words what a tool already answered
WORDING_ROOM = {"audiobook": 200, "news": 300}      # the wording calls that had less already (the book's place) or need more (the headlines are talked through)
WARM_WORDS = "(The house is warming up; nobody has spoken.)"
WARM_TIMEOUT_S = 60.0
WARM_WAIT_S = 45.0                    # a reply that begins while the prompt is being warmed waits for the warm-up this long at most (racing it made both read the prompt at half speed: 40 s, 21 s alone)
TRACE_CHARS = 300                     # what the memory keeps of a tool's answer, and of a long word in a call's arguments
TRACE_CALLS = 4                       # how many tool calls of one exchange the memory keeps (the last ones)
# "It said Done and did nothing" (the Captain's oldest complaint). A model shown only words in its history (his "turn the lamp on", its "Done.") answers the next command in words. Eight phrasings no
# route takes ("I want the living room lights off", "I need a pasta timer for ten minutes", "could you skip to the next track" ...), the right tool called on the real Core, 2026-11-05: 2 of 8 in a fresh
# conversation, 1 of 8 after four routed commands, 0 of 8 after one earlier turn, 2 of 8 after two chat turns; with what follows, 22, 24, 23 and 22 of 24. So (1) the memory keeps the REAL exchange,
# the tool call and what it answered (Brain._remember, _window), and (2) every call that offers tools starts with one exchange that never changes, a tool doing what he asked (EXAMPLE, only while the house tool is
# here): part of the prompt Core caches, and the model's own first reading of how a command is done; (3) SPOKEN says a thing is done by calling its tool, and (4) a reply that only acknowledges
# an order is asked again (Brain._acknowledges). The plain words, for every reader of Brain.history, are a view of the same exchanges (plain_words), and a call that offers NO tools is shown
# only that view, with no EXAMPLE and no tool block: shown them, Core answered in its own tool-call XML (fix2:view, 2026-11-05). The window slides by MEMORY_BLOCK exchanges, not one (Brain._keep).
EXAMPLE = (
    {"role": "user", "content": "turn the kitchen fan on"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_example", "name": "home_control", "input": {"name": "kitchen fan", "action": "on"}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_example", "content": "Done: kitchen fan, on."}]},
    {"role": "assistant", "content": "Done."},
)
TRIED ="I tried that, and then my brain wandered off. Ask me again."      # a routed command whose answer the model could not word: nothing is claimed that the tool did not say
# What is said when the model's words for a routed tool's answer came to nothing (a dropped call, or only tool-call markup that _spoken cut). The tools whose answer is MATERIAL (a forecast, headlines,
# the car, Mentat's figure) keep a plain line here; every other tool's own answer is said when it is short and plain (own_answer: "Timer set: pasta, 10 minutes.", "Done: kitchen lights, off.",
# "Playing jazz on the Enclave."): it is true, it tells him the thing was done, and he is not asked to say it again (which would set a second timer).
UNWORDED = {"weather": "I have the forecast, and then my brain wandered off. Ask me again.", "news": "I found the headlines, and then my brain wandered off. Ask me again.",
            "car_status": "I looked at the car, and then my brain wandered off. Ask me again.", "ask_mentat": "Mentat answered, and then my brain wandered off. Ask me again."}
OWN_ANSWER_CHARS = 160


def own_answer(result) -> str:
    """A tool's own answer as the words to say, when it is short and plain: one line of at most OWN_ANSWER_CHARS with no brackets or markup in it; '' otherwise (material is not words)."""
    one = str(result or "").strip()
    return one if one and len(one) <= OWN_ANSWER_CHARS and "\n" not in one and not re.search(r"[\[\]{}<>]", one) else ""


def plain_words(messages) -> list[dict]:
    """A conversation as plain words, what he said and what Skippy said and nothing more: the ONE place that view is made, for Brain.history (everything that reads the memory), for every
    model call that offers NO tools (Brain._shape: the wording of a routed tool's answer, a second try without tools, the warm-up's second request) and so for any call site added later.
    A model shown tool_use / tool_result blocks and EXAMPLE with no tools on offer answers in Core's own tool-call XML instead of words (the real Core, the review of 2026-11-05: after four routed
    commands 6 of 8 weather answers and 6 of 6 "play some jazz"; fresh and with plain words 0 of 8). So EXAMPLE goes (it teaches calling a tool; with none on offer it can only teach a bare "Done."),
    every message that holds a tool call or its answer goes (such a pair is adjacent, so the roles still alternate), a list of text blocks becomes a string, and two messages in a row from one
    role are joined. A NEW list, the messages untouched: the caller goes on appending to its own. What a call needs to see (the tool's answer) is a plain string in its newest message."""
    msgs = [m for m in (messages or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if msgs[:len(EXAMPLE)] == list(EXAMPLE):
        msgs = msgs[len(EXAMPLE):]
    out: list[dict] = []
    for m in msgs:
        content = m.get("content")
        if isinstance(content, list):
            blocks = [b for b in content if isinstance(b, dict)]
            if any(b.get("type") in ("tool_use", "tool_result") for b in blocks):
                continue
            content = " ".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text").strip()
            if not content:
                continue
        elif not isinstance(content, str):
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": out[-1]["content"] + "\n\n" + content}
        elif out or m["role"] == "user":
            out.append({"role": m["role"], "content": content})
    return out


_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS_OF = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}


def number_words(n: int) -> str:
    """0 to 999 the way a person says it ("forty five"): the voice reads words better than digits, and the closed phrases below are said by it."""
    n = int(n)
    if not 0 <= n < 1000:
        return str(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS_OF[n - n % 10] + (f" {_ONES[n % 10]}" if n % 10 else "")
    return f"{_ONES[n // 100]} hundred" + (f" {number_words(n % 100)}" if n % 100 else "")


def say_duration_words(seconds: int) -> str:
    """say_duration with the numbers in words: "five minutes", "one hour thirty minutes" (the tools answer in digits, the voice says words)."""
    def part(n: int, unit: str) -> str:
        return f"{number_words(n)} {unit}{'s' if n != 1 else ''}"
    if seconds >= 3600:
        h, m = divmod(seconds // 60, 60)
        return part(h, "hour") + (" " + part(m, "minute") if m else "")
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return part(m, "minute") + (" " + part(s, "second") if s else "")
    return part(seconds, "second")


# What he hears when a command simply worked. A closed bank, so the hub can render every phrase into the voice's cache ahead of time (fixed_phrases) and a confirmation costs no text-to-speech
# wait; picked in turn (Brain._ack), never at random. Each says "done", never what was done: the tool's answer said that much and no more, and the words must not claim a thing it did not.
ACKS = ("Done.", "Okay.", "Done, Captain.", "Got it.", "Handled.", "Done, monkey.")
TRANSPORT_ACKS = {"pause": ("Paused.", "Okay, paused."), "resume": ("Resumed.", "Okay, resuming."), "next": ("Next.", "Skipping."), "previous": ("Going back.", "Previous.")}
CANCEL_ACKS = ("Timer cancelled.", "Timers cancelled.")
COMMON_TIMERS = (10, 15, 20, 30, 45, 90, *range(60, 3601, 60), 5400, 7200, 9000, 10800, 14400)       # the lengths whose confirmation is rendered ahead of time (another is said, and kept, the first time)


def timer_ack(seconds: int) -> str:
    return f"Timer set for {say_duration_words(seconds)}."


def volume_ack(level: int) -> str:
    return f"Volume {number_words(level)}."


def fixed_phrases(timers: bool = True) -> list[str]:
    """Every phrase a confirmation can be, once each, the commonest first: for the hub to render into the voice's cache at start, so a confirmation is said at once. `timers`: with the confirmations of
    the lengths in COMMON_TIMERS (71 of the 97); without, the 26 that are not about a length. (A timer of a length that is not in COMMON_TIMERS is the one phrase not in here: it is rendered the
    first time it is said.)"""
    every = [*ACKS, *(p for phrases in TRANSPORT_ACKS.values() for p in phrases), *CANCEL_ACKS, *(volume_ack(n) for n in range(1, 11)), *((timer_ack(s) for s in COMMON_TIMERS) if timers else ())]
    return list(dict.fromkeys(every))


# A stage direction is read out by the voice ("asterisk sighs asterisk"): four of seven replies in the baseline of 2026-11-05 had one. The prompt asks for none; this is for the ones that come anyway.
# What is NOT one (the review of 2026-11-05): a word he would stress, between asterisks ("Do *not* open the garage door." came out "Do open the garage door."). It is spoken, without its asterisks.
# A starred span is a direction when it is a direction verb ("*sighs*") or, with several words, begins like an action ("*adjusts imaginary dial*", "*rolls eyes*") or ends like one ("*dramatic
# pause*") and has no negation in it. A TITLE is never one (the second review of 2026-11-05: 15 to 17 of 43 well-known songs between asterisks lost their words, "Now playing *Dancing Queen* on the Enclave."
# became "Now playing on the Enclave.", "*Careless Whisper*" because its last word is a direction verb): two or more words that are not small joining words, every one begun with a capital letter
# ("*Rolling in the Deep*", "*Jessie's Girl*"; the sentence-case "*Rolls eyes*" is still a direction), and nor is a span whose first word has an apostrophe ("*Tomorrow's weather*", "*it's raining*":
# a possessive or a contraction is no action), so the first-word-ends-in-s/ing/ed clause does not take them. A bracketed span is one when it is short (one to five words, no colon, no digit:
# spoken text has no square brackets, and "[SKIPPY_LEARN: {...}]" and
# "[Right now: ...]" have a colon, the guards and Brain._spoken see to those). A product ("2*3*4", "a*b*c") is no span at all.
_DIRECTION_VERB = re.compile(r"(?:sigh|laugh|groan|cough|gasp|whisper|smirk|yawn|snort|scoff|snicker|sniff|wink|nod|smile)(?:s|ed|ing)?|chuckl(?:e|es|ed|ing)|giggl(?:e|es|ed|ing)|paus(?:e|es|ed|ing)"
                             r"|shrug(?:s|ged|ging)?|hum(?:s|med|ming)?|grin(?:s|ned|ning)?|beat", re.I)
_DIRECTION_NOUN = {"pause", "sigh", "roll", "beat", "shrug", "nod", "smile", "grin", "laugh", "chuckle", "cough", "giggle", "yawn", "groan", "snort", "scoff", "smirk", "gasp", "wink"}
_NEGATIVE = re.compile(r"\b(?:not|no|never|none|nothing|nobody|nowhere|neither|nor|off|cannot|without|\w+n't)\b", re.I)
_NOT_ACTIONS = {"this", "is", "was", "has", "as", "us", "his", "its", "yes", "plus", "less", "unless", "always", "perhaps", "sometimes", "besides", "towards", "does", "thus", "lots", "nothing", "something",
                "anything", "everything", "morning", "evening", "during", "according", "concerning", "regarding", "being", "having", "doing", "saying", "was", "does"}
_STAR = re.compile(r"(?<![\w*])(\*{1,2})([A-Za-z][^*\n]{0,58}?)\1(?!\*)")
_BRACKET = re.compile(r"\[([A-Za-z][A-Za-z' -]{0,58})\]")
_TITLE_JOINERS = frozenset({"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "but", "nor", "with", "from", "by", "as", "per", "via", "vs", "so", "yet", "up", "o", "n"})     # (a title keeps these in lower case)


def _titled(words: list[str]) -> bool:
    """Is this a title ("Dancing Queen", "Rolling in the Deep", "Careless Whisper")? At least two words that are not small joining words, all of them (and the first word of the span) begun with a capital
    letter: "Leans in" is a sentence ("Waves at", "Looks up" too), "Rolling in the Deep" is a song."""
    words = [w.strip("'") for w in words if w.strip("'")]
    big = [w for w in words if w.lower() not in _TITLE_JOINERS]
    return len(big) >= 2 and words[0][0].isupper() and all(w[0].isupper() for w in big)


def _direction(span: str) -> bool:
    """Is the text between two asterisks a stage direction, not a word stressed or a title? (see above)"""
    words = re.findall(r"[A-Za-z']+", span)
    if not words or _NEGATIVE.search(span):
        return False
    first, last = words[0].lower(), words[-1].lower()
    if len(words) == 1:
        return bool(_DIRECTION_VERB.fullmatch(first))
    if _titled(words):                                                # a song, a film, a place: its words are spoken, whatever they end in
        return False
    return bool(_DIRECTION_VERB.fullmatch(first) or _DIRECTION_VERB.fullmatch(last) or last in _DIRECTION_NOUN
                or (re.search(r"(?:s|ing|ed)$", first) and first not in _NOT_ACTIONS and "'" not in first))      # (no action has an apostrophe in it: "tomorrow's", "it's")


NOTE_ECHO = re.compile(r"\[Right now:[^\]]*(?:(?P<cut>\]\s*)|\Z)")  # Brain.context(), copied into a reply (a copy cut by the token limit is NOTE_CUT's; this one ends at the end of the text, as THINK does)


def strip_stage_directions(text: str) -> str:
    """"*sighs* It is nine. [laughs] Fine." -> "It is nine. Fine.": the voice would read the asterisks and the brackets out. A stressed word loses only its asterisks ("Do *not* open it" ->
    "Do not open it"). Text with neither in it is returned as it is."""
    if not text or ("*" not in text and "[" not in text):
        return text
    out = _BRACKET.sub(lambda m: " " if len(m.group(1).split()) <= 5 else m.group(0), _STAR.sub(lambda m: " " if _direction(m.group(2)) else m.group(2), text))
    if out == text:
        return text
    return re.sub(r"\s+([.,!?;:])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()


def whole_sentences(text: str) -> str:
    """`text` cut back to its last whole sentence: a reply the model ran out of room on must not stop in the middle of a word. Unchanged when it has no sentence end at all."""
    ends = list(re.finditer(r"[.!?]+[\"')\]]*(?=\s|$)", text or ""))
    return text[:ends[-1].end()].strip() if ends else text


def _sweep(rx: re.Pattern, text: str, cut: list | None = None, gone: str = " ") -> str:
    """`text` with the matches of `rx` that have a `cut` group cut out (`gone` takes their place, the pieces cut are added to `cut`). The other matches (they ran to the end of the text without finding their closing, see
    THINK) stay as they were."""
    def one(m: re.Match) -> str:
        if m.group("cut") is None:
            return m.group(0)
        if cut is not None:
            cut.append(m.group(0))
        return gone
    return rx.sub(one, text)


def _tool_markup(m: re.Match) -> str:
    """What takes the place of a match of TOOL_MARKUP: a space; for a stray tag that never found its ">" (it covers the rest of the text) the rest cleaned of the markup that can still be in it."""
    tag = m.group("tag")
    return _TOOL_MARKUP_FLAT.sub(" ", tag) if tag is not None and ">" not in tag else " "


# A tool call the model wrote as text inside its reasoning, as strip_model_markup puts it back (fix4:text, the third review's core#1): a bracket that starts with the name of one of the brain's tools ("[music action=quieter]",
# "[list_timers]") and, when nothing is inside the bracket, what gives its arguments: the rest of its line and up to two lines after it that LOOK like arguments (_arg_kind), never words (Brain._text_calls reads the same shapes).
_CALL_AT = re.compile(r"\[([A-Za-z_]\w*)")                                                # (the name first; the rest of the bracket is looked at only for a name that is a tool's: _CALL_END)
_CALL_END = re.compile(r"([^\]\n]{0,160})\]")
_ARG_KV = re.compile(r"\w+\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,\]]+)")                  # "action: next", 'action: "next"', "level=5"
_ARG_CALL = re.compile(r"\w+\s*\([^()\n]*\)\s*;?")                                         # "music(action='quieter')"
_ARG_PLAIN = re.compile(r"[A-Za-z_\"'\s:>→.-]*")                                           # the words of a bare action: "next", 'with action "next"', "→ next"
_ARG_FILLERS = frozenset("with using action the a an to do please is as of value tool call now".split())
_THINK_TAG = re.compile(r"</?think\b[^<>]*>", re.I)                                        # (no "<" inside: a tag that never closes stops at the next one, so many of them cost no more than one)


def _arg_kind(line: str, tool: str, names=(), plain=None) -> str | None:
    """What a line after a bracket is, when it can be part of the call: "kv" ("action: next"), "one" (the one word an action is: "next", 'with action "next"', "[next]", "music(action='quieter')"), "end" (the closer
    "[/music]"); None for anything else, which is words, and for a bracket that is a call of its own: "[cancel_alarm]" under "[list_timers]" is another call (the one that would never be restored), not an argument.
    `plain` (review4:seams): when given, a line of plain words is the word of an action only if that word is one of `plain` (the actions the tools have), so "Okay." under a bracket stays what it is: words."""
    line = line.strip()
    if not line or len(line) > 80:
        return None
    if re.fullmatch(r"\[/\w+\]|/\w+", line):
        return "end"
    body = re.sub(r"\s*\[/\w+\]$", "", line)                       # ("action: next [/music]")
    if _ARG_KV.fullmatch(body):
        return "kv"
    if (word := re.fullmatch(r"\[(\w+)\]", body)) is not None:
        return None if word.group(1) in names else "one"
    if _ARG_CALL.fullmatch(body):
        return "one"
    if _ARG_PLAIN.fullmatch(body):
        left = [w for w in re.findall(r"[A-Za-z_]+", body.lower()) if w not in _ARG_FILLERS and w != tool.lower()]
        return "one" if len(left) == 1 and (plain is None or left[0] in plain) else None
    return None


def _call_extent(text: str, start: int, tool: str, tail: re.Match, names=(), plain=None) -> tuple[str, int]:
    """(the call written in `text` from `start`, where it ends): `tool`'s bracket (`tail` its end) and, when nothing is inside it, the arguments that follow it, as written ("[music]" + "music(action='quieter')";
    "[music] action: next"; "[music]" + "action: next" + "level: null"): the rest of its line and the next two lines, those that look like arguments and stop at the first that does not."""
    call, kinds, end = text[start:tail.end()], [], tail.end()
    if tail.group(1).strip():
        return call, end                                           # (its arguments are inside it: "[music action=quieter]")
    pos = tail.end()
    for i, line in enumerate(text[pos:pos + 400].split("\n")[:3]):
        here, pos = pos, pos + len(line) + 1
        if not line.strip():
            continue
        kind = _arg_kind(line, tool, names, plain)
        if kind is None or (kinds and (kinds[-1] == "end" or (kinds[-1] == "one" and kind != "end") or (kinds[-1] == "kv" and kind not in ("kv", "end")))):
            break
        kinds.append(kind)
        call += (" " if i == 0 else "\n") + line.strip()
        end = here + len(line)
    return call, end


def _call_text(text: str, start: int, tool: str, tail: re.Match, names=()) -> str:
    """The call written in `text` from `start` (the bracket of `tool`, `tail` its end) and the arguments that follow it (_call_extent)."""
    return _call_extent(text, start, tool, tail, names)[0]


def _cut_calls(text: str, names, plain=frozenset(), gone: str = " ") -> str:
    """`text` with every call written in it cut out: a bracket that starts with the name of one of `names` and, when nothing is inside it, the argument lines after it that _call_extent reads ("[music]" + "action: next",
    + "next", + "/music"). A wiper that took only the bracket left those lines to be SPOKEN (review4:seams: the REASK_MUSIC second try, an unreadable "[music]" + "speaker: kitchen", a reply whose model went away after a
    structured call, all with a call put back from the reasoning). Plain words under a bracket go only when they are an action of the tools (`plain`)."""
    out, pos = [], 0
    while m := _CALL_AT.search(text, pos):
        if m.group(1) in names and (tail := _CALL_END.match(text, m.end())):
            out += [text[pos:m.start()], gone]
            pos = _call_extent(text, m.start(), m.group(1), tail, names, plain)[1]
        else:
            out.append(text[pos:m.end()])
            pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _written_calls(text: str, names, lead: bool = False) -> list[tuple]:
    """[(tool, call)] for every call written as text in `text` with the name of one of `names` (_call_text); with `lead`, [(tool, call, the text of its line before it)]."""
    out, pos = [], 0
    while m := _CALL_AT.search(text, pos):
        pos = m.end()
        if m.group(1) in names and (tail := _CALL_END.match(text, pos)):
            call = _call_text(text, m.start(), m.group(1), tail, names)
            if lead:                                               # (the line before the call, but 120 characters of it at most: _NOT_MEANT reads a negation and three words, and a whole line cost a quadratic time on a line of thousands of calls)
                low = max(0, m.start() - 120)
                cut = text.rfind("\n", low, m.start())
                out.append((m.group(1), call, text[cut + 1 if cut >= 0 else low:m.start()]))
            else:
                out.append((m.group(1), call))
            pos = tail.end()
    return out


def _call_key(call: str) -> str:
    """A call to compare with another: the white space, the quotes and the case make no difference."""
    return re.sub(r"\s+", " ", call.replace('"', "").replace("'", "")).strip().lower()


def _undoes(tool: str, call: str) -> bool:
    """Does this call undo or announce (RETRY_NEVER; RETRY_NEVER_ACTIONS: a list's remove and clear)? A call the model merely CONSIDERED in its reasoning must not cancel an alarm, clear a list or speak in every room."""
    return tool in RETRY_NEVER or any(re.search(rf"\b{re.escape(action)}\b", call, re.I) for action in RETRY_NEVER_ACTIONS.get(tool, ()))


_RETRACTS = re.compile(r"\b(?:did not|didn['’]t|have not|haven['’]t|(?:could not|couldn['’]t|cannot|can['’]t)(?! (?:believe|help|resist|stand|imagine|wait)\b)|unable to|nothing was|not done|sorry|apologi[sz]e|my mistake)\b", re.I)
# A negation just before the call, on its line ("nothing is playing, so I should not call [music action=next]", "no need to call [list_timers]", "instead of [music action=next]"): the model ruled that call out (review4:core).
_NOT_MEANT = re.compile(r"(?:\bnot\b|n['’]t\b|\bcannot\b|\bnever\b|\bno need to\b|\binstead of\b|\brather than\b|\bavoid\b)\s+(?:[\w'’]+\s+){0,3}$", re.I)      # (the typographic apostrophe too: the model types ’ now and then)


def _takes_back(kept: str) -> bool:
    """Do the words that follow the reasoning take its call back, or only ask about it? EVERY sentence of them is a question or a retraction (_RETRACTS in every clause): "Which speaker, Captain?", "Sorry, I did not do
    anything.". One statement among them stands by the call ("There. Quieter. Happy now?", "Sorry, my magnificence was too loud. Quieter now."): the tag question and the dig that come with a claim are the persona's own
    (14 of 95 real replies that wrote a call as text carry a question, 2 a "sorry" or a "didn't": review5)."""
    sentences = [s for s in ACK_SENTENCES.split(kept) if s.strip()]
    return bool(sentences) and all("?" in s or all(_RETRACTS.search(c) for c in _CLAUSES.split(s) if c.strip()) for s in sentences)


def _reasoned_calls(spans: list[str], names, kept: str) -> str:
    """The tool calls the model wrote as text inside its reasoning (`spans`, the pieces cut), one per line, as Brain._text_calls reads them: only those that begin with the name of a tool the brain has (`names`: "[speak]" and
    "[sighs]" are not calls), never one that undoes or announces (_undoes), never one a negation just before it rules out (_NOT_MEANT), never one the words (`kept`) hold themselves or contradict, and each call once.
    Nothing when there is no such call."""
    names = frozenset(names)
    found = [(tool, call) for span in spans for tool, call, lead in _written_calls(_THINK_TAG.sub("\n", span), names, lead=True) if not _undoes(tool, call) and not _NOT_MEANT.search(lead)]       # (the tags are line ends: "[music]</think>" is a bracket alone on its line)
    if not found:
        return ""                                                  # (the usual case: the words are not even looked at)
    # review4:seams: the words after the reasoning are the model's last word. A call of their own is its decision (the reasoning's was a draft: "[music action=quieter]" and then "[music action=louder]" ran both), and so are
    # a denial, a retraction and a question: a call it weighed and then refused is no call ("[home_control name=fridge action=off]", "I cannot do that." switched the fridge off).
    own = _written_calls(kept, names)
    if own or denies(kept) or _takes_back(kept):
        return ""
    seen, out = set(), []
    for tool, call in found:
        if tool == "home_control" and ((RISKY_THING.search(call) and not RETRY_GUARDED.search(call)) or RETRY_GROUPS.search(call)):
            continue                                               # (what has to keep running, a whole set of plugs: never from a thought, as a second try never touches them)
        if (key := _call_key(call)) not in seen and len(out) < 3:     # (three calls at most: a reasoning that loops through drafts is no list of orders)
            seen.add(key)
            out.append(call)
    return "\n".join(out)


def _model_markup(words: str, names=None) -> tuple[str, str]:
    """(the tool calls the model wrote as text inside its reasoning, the words): strip_model_markup in its two parts, for Brain._spoken, which cuts the words back to a whole sentence and must not touch the calls."""
    words = _sweep(NOTE_ECHO, words or "", gone="").strip()
    if "<" not in words and "[" not in words:
        return "", words
    out, spans = words, []
    if "think" in out.lower():
        out = _sweep(THINK_OPEN, _sweep(THINK_CLOSE, _sweep(THINK, out, spans), spans), spans)
    out = ANNOTATION.sub(" ", _sweep(NOTE_CUT, TOOL_MARKUP.sub(_tool_markup, out)))
    if (cut := CUT_TAIL.search(out)) and any(tag.startswith(cut.group(1).upper()) for tag in CUT_TAGS):
        out = out[:cut.start()]
    elif (cut := CUT_XML_TAIL.search(out)) and any(tag.startswith(cut.group(1).lower()) for tag in CUT_XML):
        out = out[:cut.start()]
    elif cut := NOTE_STUB.search(out):
        out = out[:cut.start()]
    kept = re.sub(r"\s{2,}", " ", out).strip() if out != words else words
    return (_reasoned_calls(spans, names, kept) if names and spans else ""), kept


def strip_model_markup(words: str, names=None) -> str:
    """What a model wrote, without what is not words: the ONE cleaning of everything the house says that Core wrote, for Brain._spoken (every reply of the brain) and Daily.word (the routines). Never in speech or
    in the memory: the note that starts his message (copied into a reply, cut or not), Core's reasoning (a block, a lone "</think>" and all before it, an unclosed "<think>" and all after it), its tool-call markup
    and the picture sentinel (TOOL_MARKUP), and the persona's annotations (ANNOTATION: whole, without JSON, or CUT by the token limit, from the bracket to the end; and the words that end inside the name of one,
    "... [SKIPPY_LE"). It comes BEFORE the cut-back to a whole sentence (whole_sentences), which would otherwise land inside an annotation's text. Words with none of it are returned as they are.
    `names` (the brain's tool names; the routines have no tools and pass none): a tool call the model wrote as text INSIDE its reasoning ("[music]" and the line "music(action='quieter')") is not lost with it but put back, one
    per line, in front of the words, so that the text-call rescue (Brain._text_calls) sees it as if the model had written it in its words (fix4:text, core#1; _reasoned_calls says which)."""
    calls, kept = _model_markup(words, names)
    return f"{calls}\n{kept}" if calls and kept else calls or kept


# ── a length of time, the way he says it ("five minutes", "an hour and a half", "half an hour", "ninety seconds") ───────────────────────────────────────────────────────────────────────
_UNIT_SECONDS = {"second": 1, "seconds": 1, "sec": 1, "secs": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600}
_SMALL = {w: i for i, w in enumerate(_ONES)}
_TENS_VALUE = {w: n for n, w in _TENS_OF.items()}


def _number_at(toks: list[str], i: int):
    """(value, index after it) for a number in digits ("5", "2.5") or in words ("twenty five", "one hundred twenty", "a hundred") that starts at toks[i], else None."""
    n, t = len(toks), toks[i]
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        return float(t), i + 1
    value, j, took = 0, i, False
    if t in ("a", "an") and i + 1 < n and toks[i + 1] == "hundred":
        value, j, took = 100, i + 2, True
    elif t in _SMALL and i + 1 < n and toks[i + 1] == "hundred":
        value, j, took = _SMALL[t] * 100, i + 2, True
    if took and j + 1 < n and toks[j] == "and" and (toks[j + 1] in _TENS_VALUE or toks[j + 1] in _SMALL):
        j += 1
    if j < n and toks[j] in _TENS_VALUE:
        value, j, took = value + _TENS_VALUE[toks[j]], j + 1, True
        if j < n and 1 <= _SMALL.get(toks[j], 0) <= 9:
            value, j = value + _SMALL[toks[j]], j + 1
    elif j < n and toks[j] in _SMALL:
        value, j, took = value + _SMALL[toks[j]], j + 1, True
    return (float(value), j) if took else None


def _quantity(toks: list[str], i: int):
    """(seconds, index after it) for ONE length of time that starts at toks[i]: "five minutes", "an hour", "half an hour", "a quarter of an hour", "a couple of minutes", "two and a half hours";
    a trailing "and a half" ("an hour and a half") is part of it. None when toks[i] does not start one."""
    n, t = len(toks), toks[i]

    def unit_at(k: int):
        return _UNIT_SECONDS[toks[k]] if k < n and toks[k] in _UNIT_SECONDS else None

    def skip(k: int, *words: str) -> int:
        while k < n and toks[k] in words:
            k += 1
        return k
    if t in ("a", "an") and i + 1 < n and toks[i + 1] in ("quarter", "couple", "half"):
        return _quantity(toks, i + 1)                                # the article belongs to the word after it
    if t in ("quarter", "couple", "half"):
        j = skip(i + 1, "of", "an", "a")
        u = unit_at(j)
        return (u * {"quarter": 0.25, "couple": 2.0, "half": 0.5}[t], j + 1) if u else None
    if t in ("a", "an") and unit_at(i + 1):
        if toks[i + 1] == "second" and toks[i + 2:i + 3] in (["timer"], ["timers"]):
            return None                                              # "set a second timer for five minutes": the second of two timers, not one second (it set a one second timer named "five minutes")
        value, j = 1.0, i + 1
    else:
        got = _number_at(toks, i)
        if got is None:
            return None
        value, j = got
        if toks[j:j + 3] == ["and", "a", "half"] and unit_at(j + 3):         # "two and a half minutes"
            value, j = value + 0.5, j + 3
        elif toks[j:j + 2] == ["and", "half"] and unit_at(j + 2):
            value, j = value + 0.5, j + 2
    u = unit_at(j)
    if not u:
        return None
    seconds, j = value * u, j + 1
    if toks[j:j + 3] == ["and", "a", "half"] and not unit_at(j + 3):         # "an hour and a half"
        seconds, j = seconds + u / 2, j + 3
    return seconds, j


def find_duration(w: str):
    """(seconds, start, end) of the first length of time in `w` (the characters it covers), or None. "set a timer for an hour and a half please" -> (5400, 16, 35)."""
    w = (w or "").lower().replace("-", " ")
    spans = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\d+(?:\.\d+)?|[a-z']+", w)]
    toks = [s[0] for s in spans]
    for i in range(len(toks)):
        got = _quantity(toks, i)
        if got is None:
            continue
        total, j = got
        while True:                                                  # "1 hour 30 minutes", "2 minutes and 30 seconds"
            k = j + 1 if j < len(toks) and toks[j] == "and" else j
            more = _quantity(toks, k) if k < len(toks) else None
            if more is None:
                break
            total, j = total + more[0], more[1]
        return int(round(total)), spans[i][1], spans[j - 1][2]
    return None


def parse_duration(text: str) -> int | None:
    """The first length of time in `text`, in seconds ("an hour and a half" -> 5400), or None."""
    got = find_duration(text)
    return got[0] if got and got[0] > 0 else None


def _plain(heard: str) -> str:
    """His words as a command reads them: lower case, without the name Skippy (at either end), the politeness and the punctuation ("can you please turn on the lamp, Skippy." -> "turn on the lamp")."""
    w = re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", (heard or "").lower()).strip(" ?!.,")
    w = re.sub(r"(?<=\d)\.(?=\d)", "xdotx", w)                       # (1.5 hours: the point belongs to the number)
    w = " ".join(re.sub(r"[^\w' -]+", " ", w).split())
    w = re.sub(r"^(?:(?:please|just|hey|ok|okay|so|and|well|um|uh|now|thanks|thank you)\s+)+", "", w)
    w = re.sub(r"^(?:(?:hey|ok|okay) )?skippy\s+", "", w)           # (the name after "thanks" or "so": "thanks Skippy, turn off the lights")
    w = re.sub(r"^(?:(?:can|could|would|will) you(?: please)?|go ahead and|you can)\s+", "", w)
    w = re.sub(r"^(?:(?:please|just)\s+)+", "", w)
    for _ in range(3):
        w = re.sub(r"\s+(?:please|now|right now|for me|thanks|thank you|skippy)$", "", w)
    return w.replace("xdotx", ".").strip()


# ── timers: "set a timer for five minutes" (it had cost two model calls, 3 s, and the model labelled it 'general') ────────────────────────────────────────────────────────────────────
# Only the plain command forms (a verb, or the length first); "I want a five minute timer", "can I get a timer", "tea timer ninety seconds" and anything with more in it are the model's.
_TV = r"(?:set|start|make|create|begin)"
_TART = r"(?:(?:an?|the|my|our|another|new) )*"
_TART_NOUN = r"(?:(?:an?|another|new) )*"                              # (with no verb, "the five minute timer" and "my five minute timer" are noun phrases, the answer to "which timer?": not an order to set one)
_TLABEL = r"(?:(?P<pre>[a-z']+(?: [a-z']+){0,2}) )?"
_TFOR = r"(?: (?:for|to) (?:(?:the|my|our|an?) )?(?P<post>[a-z']+(?: [a-z']+){0,2}))?"
_TIMER_SHAPES = (re.compile(rf"^{_TV} (?:(?:me|us) )?{_TART}{_TLABEL}timer(?: (?:for|of))? DUR{_TFOR}$"),          # set a pasta timer for ten minutes
                 re.compile(rf"^{_TV} (?:(?:me|us) )?{_TART}DUR {_TLABEL}timer{_TFOR}$"),                           # set a ten minute pasta timer
                 re.compile(rf"^{_TART_NOUN}DUR {_TLABEL}timer{_TFOR}$"),                                           # ten minute timer
                 re.compile(rf"^timer (?:for|of) DUR{_TFOR}$"))                                                     # timer for ten minutes
# cancel / delete / clear / remove: a bare "cancel the timer" or "cancel all timers" is clear enough; stop / kill / end / turn off: only a timer he names ("stop the one hour timer"; a bare "stop the timer" is the model's)
_TIMER_CANCEL = re.compile(r"^(?:cancel|delete|clear|remove|get rid of|(?P<soft>stop|kill|end|dismiss|abort|turn off|switch off|shut off)) (?:(?P<all>all|every|each)(?: of)? )?(?:(?:the|my|our|that|this|those|these) )?"
                           r"(?:(?P<label>[a-z0-9'.]+(?: [a-z0-9'.]+){0,5}?) )?(?P<noun>timers?)(?: (?:for|of) (?P<tail>[a-z0-9'.]+(?: [a-z0-9'.]+){0,5}))?$")
_NOT_A_LABEL = {"a", "an", "the", "my", "our", "me", "us", "for", "to", "of", "and", "with", "it", "its", "now", "then", "timer", "timers", "another", "new", "this", "that", "one", "him", "her", "them",
                "first", "second", "third", "fourth", "fifth", "last", "next", "other", "previous", "old", "oldest", "newest", "latest", "both", "any", "some", "every", "each", "all", "these", "those"}
_LABEL_NOISE = {"another", "new", "second", "third", "other", "extra", "timer", "timers"}       # words a set command may drop from a label without losing a thing ("set a second timer for five minutes")
_LENGTHY = re.compile(r"^(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
                      r"|hundred|half|quarter|couple|an?|and|of|seconds?|secs?|minutes?|mins?|hours?|hrs?)$")


def _timer_name(words: str) -> str | None:
    """The label `cancel_timers` gets for the timer he names, or None when his words do not name one clearly. A length is said the way an unnamed timer is named ("five minute" -> "5 minutes"),
    so it finds it; a name is kept as it is. An ordinal ("the second timer"), a unit alone ("the seconds timer"), a length with more words ("the five minute pasta timer") are the model's."""
    toks = words.split()
    if not toks:
        return None
    if toks in (["hour"], ["minute"]):
        return length_label(3600 if toks == ["hour"] else 60)
    got = find_duration(words)
    if got is not None:                                              # (before the words that are no name: "one hour" and "ninety second" have a "one" and a "second" in them)
        return length_label(got[0]) if got[1] == 0 and got[2] >= len(words) and 1 <= got[0] <= 86400 else None
    return None if set(toks) & _NOT_A_LABEL or any(_LENGTHY.match(t) for t in toks) else words


def timer_route(heard: str, running=None) -> tuple[str, dict] | None:
    """(tool, arguments) for a plain timer command, else None (then the model hears it): "set a timer for five minutes", "set a pasta timer for ten minutes", "a ten minute timer", "cancel the pasta
    timer", "cancel all timers". `running`: how many timers are running now (a number, or a function that says it): a bare "cancel the timer" is clear only while there is one."""
    try:
        w = _plain(heard).replace("-", " ")
        if not re.search(r"\btimers?\b", w):
            return None
        cancel = _TIMER_CANCEL.match(w)
        if cancel:
            label, tail = (cancel.group("label") or "").strip(), re.sub(r"^(?:(?:the|my|our|an?) )+", "", cancel.group("tail") or "").strip()
            if label and cancel.group("tail"):
                return None                                          # (two names: "the pasta timer for five minutes")
            if label or cancel.group("tail"):
                named = _timer_name(label or tail)
                return ("cancel_timers", {"label": named}) if named else None
            if cancel.group("soft"):
                return None                                          # a bare "stop the timer" may be the one that is ringing
            if cancel.group("all") or cancel.group("noun") == "timers":
                return "cancel_timers", {"label": ""}
            count = running() if callable(running) else running
            return None if isinstance(count, int) and count > 1 else ("cancel_timers", {"label": ""})
        got = find_duration(w)
        if got is None or not 1 <= got[0] <= 86400:
            return None
        seconds, a, b = got
        cleaned = " ".join(f"{w[:a]} DUR {w[b:]}".split())
        for rx in _TIMER_SHAPES:
            m = rx.match(cleaned)
            if m:
                found = m.groupdict()                                # (the last shape has no label before the word timer)
                pre, post = (" ".join(w for w in (found.get(k) or "").split() if w not in _LABEL_NOISE) for k in ("pre", "post"))
                if pre and post:
                    return None                                      # two names: the model chooses, nothing is lost without a word
                words = (pre or post).split()
                label = " ".join(words) if set(words) - _NOT_A_LABEL else ""       # "to check the oven" is the label as it is said; a phrase of only "it" / "him" / "the" has nothing to lose
                return "set_timer", {"seconds": seconds, **({"label": label} if label else {})}
    except Exception:                                                # noqa: BLE001 — a route is a shortcut: whatever goes wrong, the model hears it
        pass
    return None


# ── the lights: "turn on the bedroom lamp" (the model said "Done." and switched nothing, 2026-11-05) ─────────────────────────────────────────────────────────────────────────────────
# Never what can hurt (RISKY: locks, doors, the garage, alarms, burners, fireplaces, grills, irons, dryers; and heaters, kettles ...): those stay with the model and its confirm rule.
# The review of 2026-11-05 added what keeps a house running: the network (router, modem, wifi, server, nas), the power (ups, breaker), cold (fridge, freezer), water (pumps) and what looks after a person.
RISKY_THING = re.compile(RISKY.pattern + r"|\b(?:heaters?|heating|furnace|kettle|toaster|hot ?plates?|fryer|stove ?top|cooktop|curling iron|straightener|soldering"
                         r"|routers?|modem|wi-?\s?fi|servers?|nas|ups|breakers?|fridge|freezer|refrigerator|sump|pumps?|cameras?|medical|oxygen|cpap|ventilator|aquarium|incubator|boiler)\b", re.I)
_LIGHT_NOT = re.compile(r"\b(?:music|audio|sounds?|stereo|speakers?|radio|spotify|bilby|dj|playlist|audiobooks?|books?|podcasts?|volume|thermostat|heat|furnace|ac|air conditioning"
                        r"|timers?|alarms?|reminders?|dim|dimmer|brighter|brighten|brightness|percent)\b")
_LIGHT_PATTERNS = (re.compile(r"^(?P<verb>turn|switch|put|power|shut|cut|kill|flip|get) (?P<dir>on|off|out) (?P<target>.+)$"),           # turn on the lamp, put out the lights
                   re.compile(r"^(?P<verb>turn|switch|put|power|shut|cut|kill|flip|get) (?P<target>.+?) (?P<dir>on|off|out)$"),         # turn the lamp on, switch the fan off
                   re.compile(r"^(?P<verb>turn|switch|put|power|shut|cut|kill|flip|get) (?P<target>.+?) (?P<dir>on|off|out) (?P<tail>(?:in|inside|at|for|upstairs|downstairs|everywhere|throughout)\b.*)$"),   # turn the lights off in the bedroom
                   re.compile(r"^(?P<verb>kill|cut) (?P<target>.+)$"))                                                       # kill the lights
_LIGHT_CURT = re.compile(r"^(?P<target>.+?) (?P<dir>on|off|out)(?: (?P<tail>(?:in|inside|at|for|upstairs|downstairs|everywhere|throughout)\b.*))?$")        # no verb: bedroom lamp on, all the lights off, lights out, lamp on in the bedroom
# What is said before the command or around it (the review of 2026-11-05: 21 of 53 plausible phrasings were not routed): wanting it, asking for it politely, "back", "again", "in here".
_LIGHT_LEAD = re.compile(r"^(?:(?:i|we)(?:'d| would| will)? (?:like|want|need|love)(?: you)?(?: to)?|i wanna|let'?s(?: have)?|how about|why don'?t you|(?:do you )?mind|you (?:could|should|might)|i wish you would) ")
_LIGHT_NEEDS = re.compile(r"^(?P<t>.+?) (?:(?:needs?|has|have|ought|got|is supposed|are supposed) to be|should be|must be|gotta be|to be) (?P<d>on|off)$")       # the bedroom light needs to be on
_LIGHT_MAKE_SURE = re.compile(r"^(?:make sure|be sure|see to it|see that|ensure)(?: that)? (?P<t>.+?) (?:is|are) (?P<d>on|off)$")                                  # make sure the lights are off
_GERUND = {"turning": "turn", "switching": "switch", "putting": "put", "shutting": "shut", "cutting": "cut", "killing": "kill", "flipping": "flip", "powering": "power"}      # would you mind turning ...
_LIGHT_TARGET_NO = re.compile(r"^(?:what|what's|whats|how|why|who|when|where|which|is|are|was|were|do|does|did|don't|dont|never|not|no|stop|leave|i|we|you|it|they|he|she|there|them|go|keep|carry|hold)\b")      # idle talk that ends in "on" or "off"
_SMALL_WORDS = {"the", "a", "an", "my", "our", "your", "in", "on", "of", "at"}                  # words that name nothing
_ALL_WORDS = {"all", "every", "each", "whole", "entire"}                                          # "all the lights", "every light in the house"
_KIND_OF = {"light": "light", "lights": "light", "lamp": "light", "lamps": "light", "plug": "switch", "plugs": "switch", "outlet": "switch", "outlets": "switch",
            "switch": "switch", "switches": "switch", "fan": "fan", "fans": "fan"}
_WORD_DIGITS = {w: str(i) for i, w in enumerate(_ONES[:11])}


def _stem(w: str) -> str:
    """A word as the house's names and his are compared: "lights" and "light" alike, "one" and "1" alike."""
    w = _WORD_DIGITS.get(w, w)
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")) else w


_KIND_STEMS = {_stem(k) for k in _KIND_OF}


def _light_words(text) -> list[str]:
    return [_stem(w) for w in house_layout.words(text) if w not in _SMALL_WORDS]


def _light_name(target: str, devs: list[dict], here: str) -> str | None:
    """The name to give home_control for what he pointed at: ONE thing, or one place's lights ("kitchen lights", "all lights"); None when it is not clearly either."""
    want = set(_light_words(target))
    if not want:
        return None

    def safe(d: dict) -> bool:                                       # a light is a light, whatever it is called; a plug or a fan called Heater or Kettle is not
        return d.get("kind") == "light" or not RISKY_THING.search(str(d.get("name") or ""))

    def room_of(d: dict) -> str:
        return str(d.get("room") or "").lower()
    # 1. a thing with exactly that name ("Bedroom" is the light called Bedroom: the house's own rule too)
    own = [d for d in devs if set(_light_words(d.get("name"))) == want]
    if len(own) == 1:
        return own[0]["name"] if safe(own[0]) else None
    if own:                                                          # the same name twice: the room he is in can tell them apart, if the house is told which
        mine = [d for d in own if here and room_of(d) == here]
        return f"{mine[0]['name']} {mine[0]['room']}" if len(mine) == 1 and mine[0].get("room") and safe(mine[0]) else None
    # 2. a place and a kind of thing ("the kitchen lights", "all the lights", "the lamp in the bedroom"), or a kind alone in the room he is in
    lay = house_layout.default()
    rooms, phrase, rest = lay.take(target)
    rest_words = [w for w in rest.split() if w not in _SMALL_WORDS and w not in _ALL_WORDS]
    kinds = {_KIND_OF[w] for w in rest_words if w in _KIND_OF}
    each = rooms is None and bool({"every", "each"} & set(rest.split()))          # "every light" with no place is ALL of them (like "all": the review of 2026-11-05, it switched his room's only)
    if kinds and not [w for w in rest_words if w not in _KIND_OF] and (rooms is not None or here or each):
        word = next(w for w in rest_words if w in _KIND_OF)
        members = [d for d in devs if (each or room_of(d) in (rooms if rooms is not None else [here])) and d.get("kind") in kinds]
        if not members or not all(safe(d) for d in members):
            return None
        if kinds != {"light"}:                                       # a group of plugs or fans is the model's (a plug is anything: a router, a pump); ONE of them, named by its kind, is that one
            return members[0]["name"] if len(members) == 1 and not each and not word.endswith("s") else None
        if each:
            return "all lights"
        if rooms is None:
            return f"{here} {word}"
        bare = phrase[4:] if phrase.startswith("the ") else phrase
        return f"{bare} {word}" if lay.split(f"{bare} {word}")[0] == rooms else f"{phrase} {word}"
    # 3. a name, or part of one: every word he said is in the thing's name or its room, the room he is in first
    name_words = {w for w in want if w not in _KIND_STEMS}
    if not name_words:
        return None
    said_kinds = {_KIND_OF[w] for w in house_layout.words(target) if w in _KIND_OF}
    hits = [d for d in devs if name_words <= (set(_light_words(d.get("name"))) | set(_light_words(d.get("room")))) and (not said_kinds or d.get("kind") in said_kinds)]
    if len(hits) > 1 and here:
        hits = [d for d in hits if room_of(d) == here] or hits
    if len(hits) != 1:
        return None
    twins = [e for e in devs if set(_light_words(e.get("name"))) == set(_light_words(hits[0].get("name")))]
    return hits[0]["name"] if len(twins) == 1 and safe(hits[0]) else None


def light_route(heard: str, devices, here: str = "") -> tuple[str, dict] | None:
    """("home_control", {"name", "action"}) for a plain command to switch a light, plug or fan on or off, else None (then the model hears it). `devices`: what the house has, as
    Brain._house_devices() lists it ([{"id", "name", "room", "kind"}]), or a function that gives the list (asked only once his words are a switching sentence). `here`: the room he is in.
    "turn on the bedroom lamp", "switch the fan off", "kitchen under 1 off", "turn off all the lights", "turn on the kitchen lights"; "turn on the lights" in the room he is in is that room's lights.
    Ambiguous or unknown is None: the house itself would answer "That could be ...". Never what can hurt (RISKY_THING). The call is the model's own, so the same code path runs."""
    try:
        w = _plain(heard)
        if not w or _LIGHT_NOT.search(w) or RISKY_THING.search(w):
            return None
        got = _light_command(w)
        if got is None:
            return None
        target, direction, curt = got
        if curt and re.search(r"\?\W*$", str(heard or "")):
            return None                                              # "bedroom light on?" asks; a command with a verb said with a rising voice ("turn on the bedroom lamp?") is still one
        if len(target.split()) > 8 or _LIGHT_TARGET_NO.match(target):
            return None                                              # idle talk ("what's going on"): the house is not even asked what it has
        devs = list((devices() if callable(devices) else devices) or [])
        name = _light_name(target, devs, str(here or "").lower())
        return ("home_control", {"name": name, "action": direction}) if name else None
    except Exception:                                                # noqa: BLE001 — a route is a shortcut: whatever goes wrong, the model hears it
        return None


def _light_command(w: str):
    """(what he pointed at, "on" or "off", curt) for a sentence (`w`, as _plain reads it) that switches something, else None. What he says around the command is peeled off first, so every way of asking
    reaches the same few shapes: wanting it ("I would like the lights off", "I want to turn off the lights"), asking politely ("would you mind turning off the lights"), "the bedroom light needs to be
    on", "make sure the lights are off", "back" and "again", and "in here" / "in this room" (the room he is in: a bare kind of thing already means it). `curt`: no verb in it ("bedroom light on")."""
    w = _LIGHT_LEAD.sub("", w, count=1)
    w = re.sub(r"^(turning|switching|putting|shutting|cutting|killing|flipping|powering) ", lambda m: _GERUND[m.group(1)] + " ", w)
    w = re.sub(r"\b(every|each) one\b", r"\1", re.sub(r"\bback (?=on\b|off\b|out\b)", "", w))
    w = " ".join(re.sub(r"\s+again$", "", re.sub(r"\s*\b(?:(?:in|inside|from|right) )?(?:here|this room|the room|this space)\b", "", w)).split())
    m = _LIGHT_NEEDS.match(w) or _LIGHT_MAKE_SURE.match(w)
    if m:
        w = f"{m.group('t')} {m.group('d')}"
    if w.startswith("light up "):                                    # "light up the kitchen": its lights (a place only: nothing else is lit up by a route)
        target = w[len("light up "):]
        return (target + " lights", "on", False) if house_layout.default().place(target) is not None else None
    for rx in (*_LIGHT_PATTERNS[:3], _LIGHT_CURT, _LIGHT_PATTERNS[3]):
        m = rx.match(w)
        if m:
            found = m.groupdict()
            return found["target"] + (" " + found["tail"] if found.get("tail") else ""), ("on" if found.get("dir") == "on" else "off"), rx is _LIGHT_CURT       # (the tail: "turn the lights off in the bedroom" is the lights in the bedroom)
    return None


class Brain:
    ACKS = ACKS                                                       # (the hub asks a brain for the phrases it can say, or the module: both answer)

    def fixed_phrases(self, timers: bool = True) -> list[str]:
        return fixed_phrases(timers)

    def __init__(self, client, model: str, timers, persona: str | None = None, clock=time.time, now=None, hass=None, extra=None, news=None, weather=None,
                 car=None, physics=None, sleep=time.sleep):
        self.client, self.model, self.timers, self.clock = client, model, timers, clock
        self.sleep = sleep                                            # between tries when the model went away (tests pass a no-op)
        self.retries = 0                                              # how many tries the last reply needed past the first (the house log)
        self.hass = hass                                              # a hass.Hass when Home Assistant is connected, else None
        self.extra_tools, self.run_extra = extra if extra else ([], None)   # more tools from where the brain lives (hub.py: the room's music)
        self.persona = persona if persona is not None else load_persona()
        self.now = now or (lambda: time.strftime("%A, %B %d, %Y, %H:%M"))   # the date too: with only the weekday, Core said "November 22, 2025" (2026-10-31)
        self._turns: list[list[dict]] = []                            # the conversation, one entry per exchange: its messages as the model is shown them (history is the plain-words view)
        self._ran: list[dict] = []                                    # the tool calls that ran for the reply in progress, as the memory keeps them (run_tool fills it)
        self._call_ids = 0
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
        self._busy = False                                            # a reply is running (reply() sets it): warm() leaves the model to it
        self._warming = False                                         # a warm-up is running: another one waits for it
        self._gate = threading.Lock()                                 # warm() and reply() settle who goes first under it (the two flags above)
        self._warm_done = threading.Event()                           # set whenever no warm-up is in flight: a reply that began during one waits for it (WARM_WAIT_S)
        self._warm_done.set()
        self._acks = 0                                                # how many confirmations were picked from the closed banks: the next one is the next phrase (no randomness: the tests)
        self._confirm: dict | None = None                             # the home_control call his spoken yes confirms in the reply in progress ({"name", "action"}), else None (_confirm_given, run_tool)

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

    # ── the memory: whole exchanges, with the tools in them ──
    @property
    def history(self) -> list[dict]:
        """The conversation in plain words, a user and an assistant message per exchange (what he said, what Skippy said): the view of the memory that everything else reads
        (musicguard's last answer, the news route's "about what?", the tests). The model is shown the exchanges themselves (_window), tool calls included, or this same view (plain_words) when
        the call offers no tools."""
        return plain_words([m for turn in self._turns for m in turn])

    @history.setter
    def history(self, value) -> None:
        """Set the conversation from plain words: whole user / assistant pairs only (an unpaired tail, or a message that is no words, is dropped), the last MEMORY_TURNS of them."""
        msgs = plain_words(list(value or []))
        self._turns = [[msgs[i], msgs[i + 1]] for i in range(0, len(msgs) - 1, 2)][-MEMORY_TURNS:]

    def _window(self) -> list[dict]:
        """The last MEMORY_TURNS exchanges as messages. Always whole exchanges: a tool_result without its tool_use is refused, which cutting the flat list at 2 * MEMORY_TURNS would do."""
        return [m for turn in self._turns[-MEMORY_TURNS:] for m in turn]

    def _example(self) -> list[dict]:
        return [copy.deepcopy(m) for m in EXAMPLE] if self.hass is not None else []

    def _prefix(self) -> list[dict]:
        """The messages every model call starts with: EXAMPLE (while the house tool is here), then the conversation as it stands (forgotten after ten quiet minutes, as a reply forgets it).
        The ONE place they are made, for the reply and for warm(), so the warm-up reads exactly the prefix the next reply sends and Core's cache serves them both."""
        return self._example() + ([] if self.clock() - self._last > FORGET_AFTER_S else self._window())

    def _record(self, name: str, args: dict, result) -> None:
        """A tool call that ran for the reply in progress, as the memory will keep it: a unique id, the arguments (a long word cut), what it answered (cut to TRACE_CHARS)."""
        try:
            self._call_ids += 1
            kept = {k: (v[:TRACE_CHARS] if isinstance(v, str) else v) for k, v in args.items()}
            json.dumps(kept)
            answer = str(result)
            self._ran.append({"id": f"toolu_house_{self._call_ids:06d}", "name": name, "input": kept, "result": answer if len(answer) <= TRACE_CHARS else answer[:TRACE_CHARS].rstrip() + " ..."})
        except Exception:                                             # noqa: BLE001 — the memory is never worth a reply
            pass

    def _remember(self, heard: str, said: str) -> None:
        """Keep this exchange: what he said, each tool call that ran for it (the call, then what it answered: the model reads that earlier commands were done BY CALLING A TOOL) and what was
        said back. The note (context()) is never in it, nor is a first try that was taken back."""
        turn = [{"role": "user", "content": heard}]
        for call in self._ran[-TRACE_CALLS:]:
            turn += [{"role": "assistant", "content": [{"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["input"]}]},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": call["result"]}]}]
        turn.append({"role": "assistant", "content": said})
        self._keep(turn)

    def _keep(self, turn: list[dict]) -> None:
        """Add one exchange to the memory: the ONE place it grows (a reply, an offer). The window slides in BLOCKS, not by one: with MEMORY_TURNS held the next exchange drops the oldest MEMORY_BLOCK at
        once. Sliding by one changed the start of the conversation every turn, so every model turn read the whole window again (the real Core, the review of 2026-11-05: 330-690 fresh tokens,
        2.4-2.9 s, against 100-250 and 0.7-1.2 s when the window only grows); in blocks that costs one turn in four. Whole exchanges only, and the newest always stays."""
        turns = self._turns + [turn]
        if len(turns) > MEMORY_TURNS:
            turns = turns[-max(1, MEMORY_TURNS - MEMORY_BLOCK + 1):]
        self._turns = turns

    def _shape(self, kw: dict) -> dict:
        """The request a model call is made with, the ONE place it is decided: as the caller built it when it offers tools; when it offers none (no `tools`, or an empty list), the conversation in plain
        words (plain_words: no tool_use / tool_result blocks, no EXAMPLE). _create does it for every call and warm() for its second request, so a call site added later cannot forget, and the
        warm-up reads into Core's cache exactly the prefix the next such call sends."""
        return kw if kw.get("tools") else {**kw, "messages": plain_words(kw.get("messages"))}

    def system(self) -> str:
        """The prompt: the same text all day in every room (SPOKEN's comment says why), whatever the minute, the room, the music or the news. What changes is context()."""
        house = (", and the house: lights, plugs, the thermostat (in the unit it shows), covers, speakers, scenes; always say plainly what you did "
                 "or could not do, and never claim a change you did not make") if self.hass else ""
        nohouse = "" if self.hass else "If he asks for something in the house (the lights and the rest are not connected yet), say so plainly, in character, and offer what you can do. "
        music = (", and the music in this room from his own library (Bilby is his music: 'Bilby' always means play or steer his music; "
                 "'everywhere', 'upstairs' and 'downstairs' are where it plays. Do it at once: an ordinary request never needs his confirmation)"
                 if any(t["name"] == "play_music" for t in self.extra_tools) else "")
        books = (" He has audiobooks (DRM-free files he supplied): the audiobook tool plays and steers them, and while one plays 'stop' or 'pause' means pause it. "
                 "Anything about the book (play, stop, pause, skip, where he is, a sleep timer) needs the audiobook tool called in that very turn; you cannot see the book, "
                 "so never say what is in it, what chapter it is, or that you did something unless the tool just told you. You never read a book aloud yourself.") if any(t["name"] == "audiobook" for t in self.extra_tools) else ""
        news = (news_mod.PROMPT + news_mod.WEB_PROMPT) if self.news is not None else ""
        layout = house_layout.default().primer("")                    # the floors, rooms and speakers (house_layout.json); where he is, is context()
        bilby = BILBY if any(t["name"] == "play_music" for t in self.extra_tools) else ""
        return (self.persona + SPOKEN.format(house=house, nohouse=nohouse, music=music) + layout + bilby + self.abilities() + books + news
                + (weather_mod.PROMPT if self.weather is not None else "") + (car_mod.PROMPT if self.car is not None else "")
                + (physics_mod.PROMPT if self.physics is not None else ""))

    def context(self) -> str:
        """What changes from turn to turn, as ONE note in square brackets that starts his newest message (_user_turn): the clock, the room he is in, what the house audio is doing, the headlines
        and the conversation starter he was just given. It used to sit in the middle of system(), and every minute and every room re-read the whole prompt (4-6 s against 0.6-1.0 s). The stored history
        never keeps it, so consecutive turns share everything before it."""
        lay = house_layout.default()
        room = lay.room_of_pod(getattr(self, "room", ""))
        floor = lay.floor_of(room) if room else ""
        where = (f" He is talking to you from the {room}" + (f" ({floor})" if floor else "") + ".") if room else ""
        parts = [f"Right now: {self.now()}.{where}"]
        if live := self.live():
            parts.append(live)
        if self.news is not None and (headlines := self.news.recent().strip()):
            parts.append(headlines)
        if self.offered and self.clock() - self.offered["at"] < news_mod.KEEP_S:
            parts.append("You brought this up yourself a moment ago, unasked; if he wants more, talk only from these sources and say when they don't settle "
                         f"something:\n{self.offered['note']}")
        return "[" + "\n".join(parts) + "]"

    def _user_turn(self, heard: str, note: str = "") -> str:
        """The text of a turn's user message, the ONE place it is made for every model call of a turn: the context note, his words, and (a routed tool, a re-ask) the house's note on what its tool said."""
        return f"{self.context()}\n\n{heard}" + (f"\n\n{note}" if note else "")

    def abilities(self) -> str:
        """What this voice can do, from the tools it really has: so it neither denies a tool nor claims one it lacks (2026-11-04). Out of reach: only what has no tool."""
        names = {t["name"] for t in self.tools()}
        gone = [said for tool, said in ELSEWHERE.items() if tool not in names]
        chat = (f"The Skippy chat on makerportal.live has {' and '.join(on for tool, (_, on) in ELSEWHERE.items() if tool not in names)}, and the private Skippy there writes code. "
                if gone else "The private Skippy on makerportal.live writes code. ")
        can = "; ".join(phrase for phrase, tools in CAPS if names & set(tools)) or "set timers"
        return ABILITIES.format(names=", ".join(sorted(names)), can=can, gone="".join(out for out, _ in gone), chat=chat)

    def live(self) -> str:
        """The house audio right now (houseaudio.Audio.status), a line of context(): what is playing, where, a book, a routine about to start the library."""
        if self.audio is None:
            return ""
        try:
            status = (self.audio.status() or "").strip()
            return f"Right now in the house: {status}" if status else ""
        except Exception:                                             # noqa: BLE001 — the prompt must never fail because the radio is away
            return ""

    def _house_playing(self) -> bool | None:
        """Is anything playing in the house (the station or a book)? None when this brain cannot tell, so a bare "stop" is left to the model."""
        try:
            return bool(self.audio.playing()) if self.audio is not None else None
        except Exception:                                             # noqa: BLE001
            return None

    def _spoken(self, resp=None, restore: bool = True) -> str:
        """The words of a model reply. One the model ran out of room on (stop_reason max_tokens) is cut back to its last whole sentence: a spoken answer never stops in the middle of a word. The
        note that starts his message ("[Right now: ...]") is context for the model: should it copy the note into its reply, the copy is never spoken. Nor is Skippy Core's own tool-call markup
        (TOOL_MARKUP): every path takes its words from here, so none can say it, summarise it or keep it in the memory. The same for the persona's annotations (ANNOTATION), whole or CUT by the token limit, and for
        Core's reasoning (THINK): strip_model_markup takes them out BEFORE the cut-back to a whole sentence, which would land inside an annotation's text (fix3:text).
        A tool call the model wrote as text INSIDE its reasoning is put back in front of the words (strip_model_markup; the text-call rescue runs it as it ran it when the reasoning was still in the words, fix4:text, core#1), but
        only while NOTHING has run in this reply: that is when _text_calls reads these words. The others (the wording of a routed answer, of Mentat's, of a call that ran) are cleaned with the wipers of a call, which would leave the
        lines of such a call behind. Called on the class (`Brain._spoken(resp)`, no brain), it restores none. `restore=False` for a caller that does not hand the words to _text_calls (review4:code: the music re-ask,
        _second_try with no gate, spoke the lines of a restored call, "action: next", with its words): it restores none either."""
        brain, resp = (self, resp) if resp is not None else (None, self)
        names = None
        if brain is not None and restore and not getattr(brain, "actions", True):
            try:
                names = {t["name"] for t in brain.tools()}
            except Exception:                                         # noqa: BLE001 — the names of the tools are not worth a reply
                names = None
        calls, words = _model_markup(" ".join(b.text for b in list(resp.content) if getattr(b, "type", "") == "text"), names)
        if getattr(resp, "stop_reason", None) in ("max_tokens", "length"):
            words = whole_sentences(words)
        return f"{calls}\n{words}" if calls and words else calls or words

    def _ack(self, bank: tuple) -> str:
        """The next phrase of a closed bank, in turn: the same commands in the same order are said the same way (no randomness: the tests), and no two in a row alike."""
        said = bank[self._acks % len(bank)]
        self._acks += 1
        return said

    def _confirmation(self, tool: str, args: dict, result: str) -> str | None:
        """What he hears when a routed tool simply worked and the model would add nothing: a phrase of a closed bank (ACKS, TRANSPORT_ACKS, timer_ack ...; fixed_phrases() lists them all), else None and
        the model words the answer. Only the tools' exact plain successes count: a failure, a partial success ("These did not answer"), a note ("But no speaker is on") or any answer that carries more
        than "done" goes to the model as before, and a phrase never claims more than the tool said."""
        result = str(result or "").strip()
        try:
            if tool == "set_timer" and result.startswith("Timer set:"):
                return timer_ack(int(args["seconds"]))
            if tool == "cancel_timers":
                m = re.fullmatch(r"Cancelled (\d+) timers?\.", result)
                return None if not m or int(m.group(1)) < 1 else CANCEL_ACKS[0 if int(m.group(1)) == 1 else 1]
            if tool == "home_control" and str(args.get("action")) in ("on", "off") and result.startswith("Done:") and "did not answer" not in result:
                return self._ack(ACKS)
            if tool == "music":
                action = str(args.get("action") or "")
                if action in TRANSPORT_ACKS and result == f"Done: {action} on the house radio.":
                    return self._ack(TRANSPORT_ACKS[action])
                m = re.fullmatch(r"Done: the house stereo is at (\d+) of 10\.", result) if action in ("louder", "quieter", "volume") else None
                if m and 1 <= int(m.group(1)) <= 10:
                    return volume_ack(int(m.group(1)))
        except (KeyError, TypeError, ValueError):
            return None
        return None

    def _timers_running(self) -> int | None:
        try:
            return len(self.timers.remaining())
        except Exception:                                             # noqa: BLE001 — not known: a bare "cancel the timer" is then taken to mean the one there is
            return None

    def _house_devices(self) -> list[dict]:
        """What he may switch in the house, as light_route reads it: [{"id", "name", "room", "kind"}] for every light, plug and fan that is not sensitive. [] when there is no house or it cannot say
        (a stub, a backend that is away): the route then does nothing and the model decides, as before. Never raises."""
        try:
            hass = self.hass
            lay, rooms, out = hass._layout(), hass.rooms(), []
            for s in hass.states():
                domain = hass._domain(s)
                if domain not in ("light", "switch", "fan") or not hass._visible(s) or hass._is_sensitive(s):
                    continue
                eid, label = str(s.get("entity_id", "")), hass._label(s)
                kind = str(lay.device(eid, label).get("kind") or "")
                out.append({"id": eid, "name": label, "room": rooms.get(eid, ""), "kind": kind if kind in ("light", "switch", "fan") else domain})
            return out
        except Exception:                                             # noqa: BLE001
            return []

    def _light_resolves(self, name: str) -> bool:
        """Would the house settle on this name by its own rules (a place and a kind of thing, or exactly ONE thing)? So the route never hands the house a name it would answer with "That could be ..."."""
        try:
            group = self.hass._group(name)
            if group is not None:
                return bool(group)
            found, _ = self.hass.find(name)
            return found is not None and self.hass._domain(found) in ("light", "switch", "fan") and not self.hass._is_sensitive(found)
        except Exception:                                             # noqa: BLE001
            return False

    def _light_pre_route(self, heard: str) -> tuple[str, dict] | None:
        """light_route for the house this brain has, and the room it is in; the route stands only when the house itself would settle on the name (_light_resolves)."""
        if self.hass is None:
            return None
        if any(t["name"] == "speakers" for t in self.extra_tools) and device_route(heard, self._house_playing()) is not None:
            return None                                              # "turn off the sony" while the house plays is a speaker's switch, whatever a plug of the house is called (the speakers come after this route)
        here = house_layout.default().room_of_pod(getattr(self, "room", ""))
        got = light_route(heard, self._house_devices, here=here)
        return got if got is not None and self._light_resolves(got[1]["name"]) else None

    def offer(self, text: str, note: str) -> None:
        """He is about to say `text` unasked (starters.py): it joins his memory, with `note` (its sources) for the follow-up questions."""
        now = self.clock()
        if now - self._last > FORGET_AFTER_S:
            self._turns = []
        self._last = now
        self._keep([{"role": "user", "content": "(Nobody spoke: you bring something up yourself.)"}, {"role": "assistant", "content": text}])
        self.offered = {"note": note, "at": now}

    def _confirm_given(self, heard: str, now: float) -> dict | None:
        """The call his spoken yes confirms in the reply that begins now, else None (see CONFIRM_ASKED): the last tool answer of the exchange before this one was the house asking him to confirm a call of
        home_control, that was at most CONFIRM_FOR_S ago, and his words now are a short yes (says_yes). Read from the memory, so a conversation that was forgotten, an offer made since, a quiet gap or any other
        exchange in between has already lost it."""
        try:
            if not self._turns or now - self._last > CONFIRM_FOR_S:
                return None
            turn = self._turns[-1]
            use, answer = turn[-3]["content"][0], turn[-2]["content"][0]
            if use.get("type") != "tool_use" or use.get("name") != "home_control" or answer.get("type") != "tool_result" or CONFIRM_ASKED not in str(answer.get("content")):
                return None
            asked = {"name": str((use.get("input") or {}).get("name") or ""), "action": str((use.get("input") or {}).get("action") or "")}
            return asked if says_yes(heard, asked) else None
        except (IndexError, KeyError, TypeError, AttributeError):
            return None

    def _checked_confirm(self, args: dict) -> dict:
        """The args of a home_control call with the model's own `confirm` taken out, unless it is the call his yes just confirmed (the same thing, the same action): then it stays, once."""
        said = self._confirm
        if said and args.get("confirm") is True and _same_thing(args.get("name"), said["name"]) \
                and str(args.get("action") or "").strip().lower().replace(" ", "_") == said["action"].strip().lower().replace(" ", "_"):
            self._confirm = None                                         # (one yes, one door: asked again, it asks again)
            return args
        return {k: v for k, v in args.items() if k != "confirm"}

    def run_tool(self, name: str, args: dict) -> str:
        args = args if isinstance(args, dict) else {}
        if name == "home_control" and "confirm" in args:               # the ONE place a house call runs: only he says yes (a second try, a call written as text and a route all come through here)
            args = self._checked_confirm(args)
        began = time.monotonic()
        try:
            out = self._run_tool(name, args)
        finally:
            self._lap("tools_s", time.monotonic() - began)
        self.actions.append({"tool": name, "args": {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool))}, "outcome": str(out)[:120]})
        self._record(name, args, out)
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
        one). `need_words`: a reply with neither words nor a tool call is the model dropping out too. Raises the last failure when the window runs out. Timed (self.timing).
        A call that offers no tools is shown the conversation in plain words (_shape): every such call goes through here, so none can send tool blocks to a model with no tools to call."""
        began = time.monotonic()
        try:
            resp = self._create_waiting(need_words, RETRY_FOR_S if patience is None else patience, **self._shape(kw))
            self._cache_facts(resp)
            return resp
        finally:
            self._lap("model_s", time.monotonic() - began)

    def _cache_facts(self, resp) -> None:
        """What the first model call of a reply says about Core's prompt cache, for the hub's timing line: `cache_read` (prompt tokens it read from the cache) and `uncached` (the ones it had to
        read). Whether the prompt was warm is then in the house log, not in a guess. Nothing when the reply has no usage."""
        try:
            usage = getattr(resp, "usage", None)
            read, new = getattr(usage, "cache_read_input_tokens", None), getattr(usage, "input_tokens", None)
            if "cache_read" not in self.timing and isinstance(read, int) and not isinstance(read, bool):
                self.timing["cache_read"] = read
                if isinstance(new, int) and not isinstance(new, bool):
                    self.timing["uncached"] = new
        except Exception:                                             # noqa: BLE001 — a number for the log is not worth a reply
            pass

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
        with self._gate:                                              # (warm() decides under the same lock: either it began first, and we wait for it, or we did, and it starts nothing)
            self._busy = True                                         # warm() asks the model nothing while a reply is running
            warming = self._warming
        try:
            if warming:
                self._wait_for_warm()
            return self._reply(heard)
        finally:
            self._busy = False
            self._lap("total_s", time.monotonic() - began)

    def _wait_for_warm(self) -> None:
        """A warm-up is reading the prompt into Core's cache as this reply begins: wait for it (WARM_WAIT_S at most) rather than race it. Core does not share a prefill that is in flight, so a
        turn that raced a cold warm-up and the warm-up both read the 10,000-token prompt at half speed (turn 40 s against 21 s alone); a turn that waits finds the whole prompt cached and costs 1.5 s
        more once it is read (29.9-33.1 s from the warm-up's start, measured). The wait is in the timing as `warm_wait_s`."""
        waited = time.monotonic()
        done = self._warm_done.wait(WARM_WAIT_S)
        took = time.monotonic() - waited
        self._note("warm_wait_s", round(took, 2))
        print(f"[brain] waited {took:.1f} s for the prompt warm-up to finish{'' if done else ' (gave up: it is still running)'}", flush=True)

    def warm(self) -> float | None:
        """Read the prompt into Core's cache before a turn needs it: the very prefix a turn sends (the model, the system text, the tools, the history as it stands), a placeholder for his message and ONE
        token of answer. A second request does the same without the tools, the shape of a routed command's wording call: Core keeps a prompt per shape (measured: the first call of the other shape,
        cold, read 2,900 of 5,800 tokens again, 7.6 s under load; warm, 1.6 s). -> the seconds it took, or None when it did not happen (a reply is running, another warm-up is, the model is away).
        In the caller's thread (the hub's); never raises; counts for nothing: not in actions, timing, history or the quiet clock of a reply. A turn that follows with the same text costs 0.6-1.0 s
        of prompt instead of 4-6 s."""
        with self._gate:
            if self._busy or self._warming:
                return None
            self._warming = True
            self._warm_done.clear()                                   # (a reply that begins now waits for this warm-up: _wait_for_warm)
        began = time.monotonic()
        try:
            system, hello = self.system(), self._prefix() + [{"role": "user", "content": WARM_WORDS}]       # (the prefix the next reply will send: one helper makes both)
            self.client.messages.create(**self._shape(dict(model=self.model, max_tokens=1, system=system, tools=self.tools(), timeout=WARM_TIMEOUT_S, messages=hello)))
            if not self._busy:                                        # (a reply that began meanwhile has the model to itself)
                try:                                                  # (no tools: the plain words, exactly what the next call without tools sends, _shape)
                    self.client.messages.create(**self._shape(dict(model=self.model, max_tokens=1, system=system, timeout=WARM_TIMEOUT_S, messages=hello)))
                except Exception:                                     # noqa: BLE001 — the first shape is warm; the other is a bonus
                    pass
            return time.monotonic() - began
        except Exception:                                             # noqa: BLE001 — a warm-up is a favour to the next turn, never a failure
            return None
        finally:
            self._warming = False
            self._warm_done.set()                                     # (a reply waiting for it goes on)

    def _note(self, key: str, value) -> None:
        try:
            self.timing[key] = value
        except Exception:                                             # noqa: BLE001 — timing is never worth a reply
            pass

    def _reply(self, heard: str) -> str:
        now = self.clock()
        self._confirm = self._confirm_given(heard, now)               # (before the memory and the quiet clock move on: his yes is read against the exchange before this one)
        self.actions = []
        self._ran = []
        self.guard = (None, None)
        self.retries = 0
        if now - self._last > FORGET_AFTER_S:
            self._turns = []                                          # a new conversation after ten quiet minutes
        self._last = now
        heard = fix_bilby(heard)                                      # Whisper writes "Bill be" / "Bill B" for Bilby (2026-11-01)
        text = ""
        if self.news is not None and news_mod.is_bare_request(heard):  # "what's the news?": about what, before any model runs
            self._remember(heard, news_mod.ASK_WHAT)
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
        if not routed and self.hass is not None:                     # "turn on the bedroom lamp": left to itself, Core said "Done." and switched nothing (2026-11-05)
            tool, routed = self._light_pre_route(heard) or (tool, None)
        if not routed:                                               # "set a timer for five minutes": two model calls and 3 s, and the model labelled it 'general' (2026-11-05)
            tool, routed = timer_route(heard, self._timers_running) or (tool, None)
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
            said = self._confirmation(tool, routed, result)
            if said is not None:                                      # it simply worked: the closed bank says so, and no second model call is made
                self._note("template", True)                          # (the hub's timing line says so)
                self._remember(heard, said)                           # (with the tool call that ran: the model reads that a command is done by calling a tool, not by saying so)
                return said
            label = {"audiobook": "Already done by the house for this request"}.get(tool, f"The {tool} tool's answer for this request")
            messages = self._prefix() + [{"role": "user", "content": self._user_turn(heard, f"[{label}: {result}]")}]       # (the house as the tool left it)
            try:
                resp = self._create(need_words=True, model=self.model, max_tokens=WORDING_ROOM.get(tool, WORDING_TOKENS), system=self.system(), messages=messages)
                text = self._spoken(resp)
            except Exception:                                         # noqa: BLE001 — it happened (and stayed away): say it plainly
                text = ""
            text = strip_stage_directions(ANNOTATION.sub("", self._wipe_calls(self._phantom(text))).strip())       # (an annotation of the persona's would be read aloud, as on every other path; nor is a call in another dialect: review4:seams)
            text = text or (result.split(") ", 1)[-1] if tool == "audiobook" else UNWORDED.get(tool) or own_answer(result) or TRIED)       # (nothing to say: the tool's own short answer, else a plain line)
            self._remember(heard, text)
            return text
        messages = self._prefix() + [{"role": "user", "content": self._user_turn(heard)}]      # (one note for every model call of the turn: the second tries share it)
        for _ in range(4):                                            # a few rounds of tool use at most
            try:
                resp = self._create(need_words=True, model=self.model, max_tokens=SPEAK_TOKENS, system=self.system(), messages=messages, tools=self.tools())
            except Dropped:
                break                                                 # it kept answering nothing: the line below says so
            blocks = list(resp.content)
            text = self._spoken(resp)
            calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
            if not calls:
                break
            messages.append({"role": "assistant", "content": [self._dump(b) for b in blocks]})
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": self.run_tool(c.name, c.input)} for c in calls]})
        text = ANNOTATION.sub("", text).strip()
        if text and not self.actions:
            text = self._text_calls(text, messages, ordered=not NOT_AN_ORDER.search(heard))      # (the second try after a call that could not be read is behind the gate of the one after a denial: fix4:guards, code3#1)
        elif text:
            text = self._wipe_calls(text)                              # (a tool ran, and the words may still carry a call written as words: a bracket, a code fence ... never spoken, fix4:guards, core3#2)
        prev = next((m["content"] for m in reversed(self.history) if m["role"] == "assistant" and isinstance(m["content"], str)), "")
        action, why = musicguard.classify(heard, prev, text, [a["tool"] for a in self.actions], happened=self._music_happened)
        self.guard = (action, why)                                    # the house log keeps it: the next count of claims needs no log archaeology
        quiet = bool(text) and not self.actions                       # words and no tool: the only replies the guards below look at
        claimed = quiet and why != "history" and self._claims(text)      # ("history": musicguard saw a true answer about the past, checked against the house log)
        figure = quiet and self.physics is not None and physics_mod.unbacked(text) and physics_mod.askable(heard)
        # A denial is judged BEFORE the logging-only tier below and before CLAIMED (the review of 2026-11-05): musicguard files "start a pasta timer", "put on the kitchen light", "crank the heat",
        # "pause the timer" under the music commands, so its shadow tier won and 26 of 123 logged commands never got the second try; and "I cannot set an alarm from here" matched CLAIMED
        # ("set an alarm") and was asked "you said that, but NOTHING was done". A reply that also claims something in another clause, or gives a figure, takes those branches as before.
        denied = quiet and not claimed and not figure and denies(text) and not RISKY_THING.search(heard) and not ASKS_ABILITY.search(heard)      # (RISKY_THING: a "no" about the router or the fridge stands too)
        # "Done." and nothing done (the Captain's oldest complaint): a few words that only acknowledge, to an order for a tool this voice has, and no tool called. Judged FIRST: musicguard's tiers below
        # file "turn off the living room lights" under music (a "retract" that says "I have not played any music") or only log it, and neither gets the order done.
        nothing_done = quiet and not claimed and not figure and not denied and why != "history" and self._acknowledges(heard, text)
        if nothing_done:
            print(f"[brain] said it was done, and called no tool: {text[:80]!r}: asked again with the tools", flush=True)
            self.guard = ("reask", "ack")
            text = self._second_try(text, messages, *self._ack_menu(), UNCLAIM, tools_ran=lambda: bool(self.actions), ordered=True)      # (behind the gate of the second try after a denial)
        elif action == "retract":
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
            # Behind the gate of the other two second tries (fix3:guards, the second review: "I do not need the alarm on sunday" was answered "Alarm cancelled." with no tool, and this try, offered every tool
            # with the model's own `confirm`, ran cancel_alarm with nothing named: both alarms gone): reads always, a change only when his words are an order, never cancel / clear / announce, no `confirm`.
            # A genuine "cancel my alarm" that was claimed and not done is then told plainly (UNCLAIM), as on the backstop: the alarm he wakes to is never cancelled by a second guess.
            ordered = order_shaped(heard) and not NOT_AN_ORDER.search(heard)
            text = self._second_try(text, messages, ASK_AGAIN, self.tools(), UNCLAIM, tools_ran=lambda: bool(self.actions), ordered=ordered)
        elif figure:
            print(f"[brain] a physical figure with no Mentat call: {text[:80]!r}: Mentat asked now", flush=True)   # Core answered physics from memory, once as "Mentat" (2026-11-04)
            self.guard = ("force", "physics")
            text = self._ask_mentat_now(heard) or text                # a Mentat that cannot be reached is said, never a figure from memory
        text = strip_stage_directions(text)                           # (after the guards, which judge what the model said; what is spoken and kept has none)
        text = text or "Hm. I had nothing to say, which is unlike me."
        self._remember(heard, text)
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

    def _acknowledges(self, heard: str, text: str) -> bool:
        """Is this reply only an acknowledgement to an ORDER for something one of this voice's tools does (ORDER_CUES)? Called only for a reply with words and no tool. What would be SPOKEN is
        judged (the stage directions are out), it is BARE_WORDS at most and asks nothing, and the FIRST or the LAST sentence of it opens like an acknowledgement (BARE_ACK: "Done, Captain.", or a quip
        and then "Done.") or, for an order for the house's lights, plugs or fans, some sentence is a plain claim of state (STATE_CLAIM: "The kitchen lights are on, Captain."); and nothing is taken
        back (HONEST, less a quip's "try not to"). His words must be an order in shape (order_shaped: a statement that names a light is no order), never a question about the house (NOT_AN_ORDER) or
        about what it can do (ASKS_ABILITY), never about what can hurt (RISKY_THING: a lock, a door, the garage, the router, the fridge ...).
        fix3:guards: a reply of up to BARE_NAMING_WORDS counts when it names the thing he ordered; a sentence of the house's own closed bank ("Paused.", "Next.", "Volume five.": said only after a tool
        worked), a claim of the thing in the order's own tool (DONE_CLAIMS: "Setting a timer for ten minutes.") or a "you're welcome" is a claim like a "Done." """
        heard = heard or ""
        said = strip_stage_directions(text or "").strip()
        words = len(said.split())
        if not said or "?" in said or words > BARE_NAMING_WORDS or self.guard == ("reask", "claim"):          # (a claim of music to a request for music has its own second try: REASK_MUSIC)
            return False
        if NOT_AN_ORDER.search(heard) or RISKY_THING.search(heard) or ASKS_ABILITY.search(heard) or not order_shaped(heard):
            return False
        names = {t["name"] for t in self.tools()}
        wanted = [(tools, rx) for tools, rx in ORDER_CUES if names & set(tools) and rx.search(heard)]
        if not wanted:
            return False
        if words > BARE_WORDS and not any(rx.search(said) for _, rx in wanted):          # (a longer reply is a claim only when it names the light, the timer ... he asked for)
            return False
        sentences = [s for s in ACK_SENTENCES.split(said) if s.strip()]
        for i in dict.fromkeys((0, len(sentences) - 1)):               # the first sentence, then the last
            opening = BARE_ACK.match(sentences[i])
            if opening is not None:                                    # (what is left once the opening is out: "No trouble at all." opens with a "no" that is no refusal)
                rest = " ".join(sentences[:i] + [sentences[i][opening.end():]] + sentences[i + 1:])
                return not HONEST.search(QUIP_NEGATIONS.sub(" ", rest))
            if bank_phrase(sentences[i]):
                return not HONEST.search(QUIP_NEGATIONS.sub(" ", " ".join(sentences[:i] + sentences[i + 1:])))
        claims = [DONE_CLAIMS[tools[0]] for tools, _ in wanted if tools[0] in DONE_CLAIMS]
        if any(rx.search(s) for rx in claims for s in sentences) or any(ACK_TAIL.search(s) for s in sentences):
            return not HONEST.search(QUIP_NEGATIONS.sub(" ", said))
        return False

    def _ack_menu(self) -> tuple[str, list[dict]]:
        """(the nudge, the tools) of the backstop's second try. The tools are ALL of them, exactly the list the turn's own calls were made with: Core keeps its prompt cache per tool list, and a menu cut
        down to the safe part (retry_tools, as the second try after a denial has) was read from scratch, 10,487 tokens and 44 s against 127 tokens and 2.1 s on the real Core (2026-11-05). What the try may DO
        is decided where a call runs (_run_call: retry_may, never the model's `confirm`: nothing is cancelled, cleared or announced on a bare "Okay."), and the nudge names only the tools it may use."""
        allowed = retry_tools(self.tools(), True)
        return ASK_AGAIN_ONLY.format(names=", ".join(sorted(t["name"] for t in allowed))), self.tools()

    def _run_call(self, c, ordered: bool | None) -> str:
        """The answer to one tool call of a second try. `ordered` None: it runs as the model called it. Otherwise the gate of the second try after a denial: a call retry_may refuses is not run (NOT_DONE
        answers it), and no call runs with the `confirm` the model wrote (that is his yes, aloud)."""
        if ordered is None:
            return self.run_tool(c.name, c.input)
        args = {k: v for k, v in (c.input if isinstance(c.input, dict) else {}).items() if k != "confirm"}
        return self.run_tool(c.name, args) if retry_may(c.name, args, ordered) else self._refuse()

    def _refuse(self) -> str:
        """NOT_DONE, the answer to a call a second try may not make; and the note of how many calls had run when it was refused (_unsound)."""
        self._refused_at = len(self.actions)
        return NOT_DONE

    def _unsound(self, new: str) -> bool:
        """Are these the last words of a second try in which nothing that CHANGES anything ran, and do they still say it was done? (fix4:guards, the third review's core#3: a look at the alarms, a cancel that was refused,
        and then 'There was a 09:00 alarm. I've cancelled it.' with both alarms still set; on the real Core also a refused cancel, a look, and then 'The Sunday alarm is at 09:00. I've cancelled it for you.') If the model
        was told no (NOT_DONE) and has looked at nothing since, its last word came right after the refusal: the words must own it (_honest). Otherwise (it looked, or was never refused) the words answer the look
        ('what alarms do I have': the write refused, the look run, 'Two alarms, monkey.') unless they still promise or claim it in the first person (announces without `claims`: a read answer is full of 'the alarm is
        set'). A change that ran is the claim made true."""
        if self._changed():
            return False
        at = getattr(self, "_refused_at", None)
        return not self._honest(new) if at is not None and len(self.actions) <= at else announces(new, claims=False)

    def _changed(self) -> bool:
        """Did a tool that CHANGES something run in this reply? (A look, is_read, changes nothing: it never makes a claim true.)"""
        return any(not is_read(a["tool"], a["args"]) for a in self.actions)

    def _honest(self, new: str) -> bool:
        """Do the words of a second try take the claim back, or ask? A retraction (HONEST), a denial or a question, that says no music is done (music_claimed) and promises or claims nothing more (announces: 'Right, my mistake.
        Let me cancel that timer for you.' is an apology and a promise, not a retraction)."""
        return bool(new) and not musicguard.music_claimed(new) and not announces(new) and (bool(HONEST.search(new)) or denies(new) or "?" in new)

    def _needs_arguments(self, name: str) -> bool:
        """Does this tool mean nothing without arguments (its schema requires some, or has an `action` to choose), or do too much with none (RETRY_NEVER: cancel_alarm and cancel_timers with nothing named
        cancel everything, announce speaks in every room)? A call written as text with none, and none found, is not run."""
        schema = next((t.get("input_schema") or {} for t in self.tools() if t["name"] == name), {})
        return name in RETRY_NEVER or bool(schema.get("required")) or bool(((schema.get("properties") or {}).get("action") or {}).get("enum"))

    def _bare_action(self, name: str, rest: str) -> tuple[dict, int]:
        """A call written as `[music]next[/music]`, as `[music]` + `[next]` + `/music`, or as `[music] with action "next"` (the real Core, 3 of 7 for "could you skip to the next track"): its one argument is a bare
        word, an `action` the tool really has. -> ({"action": word}, characters of `rest` used) when the text right after the bracket (up to its closer, else the first line) is that word and the words that go
        around it (BARE_FILLERS) and nothing else; ({}, 0) for a sentence ("Skipping to the next track now."), two actions, none, or an action that undoes (RETRY_NEVER_ACTIONS)."""
        props = self._tool_props(name)
        enum = [str(a) for a in ((props.get("action") or {}).get("enum") or [])]
        if not enum or name in RETRY_NEVER:
            return {}, 0
        closer = re.search(rf"\[/{re.escape(name)}\]|(?<![\w/])/{re.escape(name)}\b", rest)
        if closer:
            seg, used = rest[:closer.start()], closer.end()
        else:
            seg, used = "", 0
            for line in rest.splitlines(keepends=True)[:3]:
                used += len(line)
                if line.strip():
                    seg = line
                    break
        words = [w.lower() for w in re.findall(r"[A-Za-z_]+", seg) if w.lower() not in BARE_FILLERS and w.lower() != name.lower()]
        if len(words) == 1 and words[0] in enum and words[0] not in RETRY_NEVER_ACTIONS.get(name, ()):
            return {"action": words[0]}, used
        return {}, 0

    def _tool_props(self, name: str) -> dict:
        """The arguments a tool really has (the properties of its schema); {} for a tool that has none, or is not here."""
        return next(((t.get("input_schema") or {}).get("properties") or {} for t in self.tools() if t["name"] == name), {})

    def _tool_brackets(self, text: str, names) -> list[str]:
        """The tools whose NAME opens a bracket of `text`, in the order written ("[set_timer 90 tea]", "[play_music: genre=jazz, speaker=living room]"): a call that was meant, whatever TEXT_CALL makes of its
        arguments. (An `action` word alone is not one: "[pause]" may be a stage direction. A closer is no call.) fix4:guards, the third review's core#6: nor is a stage direction that happens to open with the
        name of a tool that is an English word ("[music swells]", "[news anchor voice]", "[weather report voice]"): that one is a call only when what follows the name looks like arguments (a colon, an equals sign, a
        digit, a parenthesis) or is nothing; a name that is an identifier (set_timer, play_music: it has an underscore) is a call whatever follows ("[play_music jazz]")."""
        if not text or "[" not in text or not names:
            return []
        found = re.findall(r"\[(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b([^\]\n]{0,160})\]", text)
        return [name for name, rest in found if "_" in name or not rest.strip() or re.search(r"[:=(\d]", rest) or self._says_an_action(name, rest)]

    def _says_an_action(self, name: str, rest: str) -> bool:
        """Does what follows a tool's name in its bracket say one of that tool's ACTIONS, or the word "action"? Then it is the call written in plain words ("[music with quieter]", "[music next]",
        "[music action quieter]", "[list add milk]": 6 of the 43 real replies to "turn the music down a notch" were the first, and with core3#6's rule alone they were wiped as stage directions and the
        words that came with them ("There. Quieter.") spoken, nothing done), not a stage direction ("[music swells]", "[music with a flourish]")."""
        enum = {str(a).lower() for a in ((self._tool_props(name).get("action") or {}).get("enum") or [])}
        return bool(set(re.findall(r"[a-z_]+", rest.lower())) & (enum | {"action"}))

    def _phantom(self, text: str) -> str:
        """`text` without a tool call written in it as words, for a call that offers no tools (the wording of a routed answer, Mentat's): a bracket that starts with the name of ANY tool this brain has
        (PHANTOM knew only ask_mentat and car_status), whatever it holds. Any other bracket ("[nonsense a=1]") stays: it is not a call."""
        names = {t["name"] for t in self.tools()} | {"ask_mentat", "car_status"}
        actions = {str(a).lower() for t in self.tools() for a in ((((t.get("input_schema") or {}).get("properties") or {}).get("action") or {}).get("enum") or [])}
        if "[" in text:
            text = _cut_calls(text, names, actions, gone="")          # (a bracket takes the argument lines under it with it, as in _wipe_calls: "[music]" + "action: next" was spoken here, review5)
        return phantom_pattern(names).sub("", text)

    def _wipe_calls(self, text: str, names=None, every: bool = False) -> str:
        """`text` without the tool calls written in it as words: every bracket that starts with the name of one of this brain's tools or with an `action` one of them has, whatever it holds ("[music with
        action quieter]", "[set_timer 90 tea]", "[music mode=weird]", "[volume 5]": the real Core wrote that last one for "turn the music down a notch"), and the closers ("[/music]"); with `every`, also the
        calls TEXT_CALL reads whatever their name. A bracket that starts with any other word stays ("[nonsense a=1]"). fix4:guards (the third review's core#2) also the other dialects of a call written as
        words (CALL_DIALECT: a code fence, "[call: ...]", "[Calling ...]", "[tool_call ...]") and the function way, "music(action='quieter')"; text with none of it is returned as it is, byte for byte."""
        if not text or ("[" not in text and "`" not in text and "(" not in text and "tool_call" not in text.lower()):
            return text
        names = names if names is not None else {t["name"] for t in self.tools()}
        actions = {str(a) for t in self.tools() for a in ((((t.get("input_schema") or {}).get("properties") or {}).get("action") or {}).get("enum") or [])}
        out = _cut_calls(text, names, {a.lower() for a in actions}) if "[" in text else text      # (a bracket takes the argument lines under it with it: review4:seams)
        out = TEXT_CALL.sub(" ", out) if every else out
        heads = set(names) | actions
        if heads:
            out = re.sub(r"\[/?(?:" + "|".join(sorted(map(re.escape, heads), key=len, reverse=True)) + r")\b[^\]\n]{0,160}\]", " ", out)
        out = strip_call_dialects(out)
        if names:
            out = re.sub(r"\b(?:" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\((?=\s*\)|[^()\n]*[=\"'])(?:[^()\n]|\([^()\n]*\))*\)", " ", out)      # (empty, or with an `=` or a quote in it: "list(s)" is a word)
        if out == text:
            return text
        return re.sub(r"\s+([.,!?;:])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()

    def _second_try_denied(self, text: str, messages: list, heard: str = "") -> str:
        """One more go after "I cannot ..." with no tool called, told the tools it really has. A tool that runs is its answer; if none does, the first reply stands (it may be true).
        What it may DO is the safe part of the tool list (retry_tools): it looks at anything, changes something only when his words are an order, and never undoes or announces; the nudge names only
        those tools and every call it makes passes retry_may. The menu it is SHOWN is the whole list, exactly what the turn's own calls were made with (fix3:guards, the second review's code#8): Core
        keeps its prompt cache per tool list, and the cut menu was read from scratch (7.8 to 44 s measured, the room deaf meanwhile; 2 s with the whole list). The call is
        optional and the room is deaf while it waits, so it waits RETRY_PATIENCE_S at most, and nothing that goes wrong in it ever costs him the first reply."""
        ordered = not NOT_AN_ORDER.search(heard or "")
        allowed = retry_tools(self.tools(), ordered)
        if not allowed:
            return text
        self._refused_at = None                                       # (nothing was refused yet: _refuse, _unsound)
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": DENIED.format(names=", ".join(sorted(t["name"] for t in allowed)), asked="" if ordered else ASKED_ONLY)}]
        new, last = "", ""
        try:
            for _ in range(3):
                resp = self._create(need_words=True, patience=RETRY_PATIENCE_S, model=self.model, max_tokens=SPEAK_TOKENS, system=self.system(), messages=messages, tools=self.tools())
                blocks = list(resp.content)
                new = self._spoken(resp)
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
                        results.append(self._refuse())
                messages.append({"role": "assistant", "content": [self._dump(b) for b in blocks]})
                messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": r} for c, r in zip(calls, results)]})
        except Exception as e:                                        # noqa: BLE001 — the model stayed away (Dropped), answered with a failure, a tool broke: the first reply stands
            print(f"[brain] the second try failed ({type(e).__name__}): {'what it did stands' if self.actions else 'the first reply stands'}", flush=True)
        new = ANNOTATION.sub("", new).strip()
        if new and not self.actions:                                  # a call written as words ("[list_timers]"): through the same gate as a real one
            new = self._text_calls(new, messages, allow=lambda n, a: retry_may(n, a, ordered), patience=RETRY_PATIENCE_S)
        new = self._wipe_calls(new)                                   # (a call written in another dialect, or one a tool that ran left behind, is never words: fix4:guards, core3#2)
        if self.actions and self._unsound(new):
            return text                                               # (a look, a change that was refused and then 'I've cancelled it': the first reply stands, fix4:guards, core3#3)
        return (new or last or text) if self.actions else text

    def _ask_mentat_now(self, heard: str) -> str:
        """Mentat asked by the house, with his words as they were said, and Core only wording the answer (the routed flow: no tools to forget to call or to write as text)."""
        result = self.run_tool("ask_mentat", {"question": heard})
        messages = self._prefix() + [{"role": "user", "content": self._user_turn(heard, f"[The ask_mentat tool's answer for this request: {result}]")}]
        try:
            resp = self._create(need_words=True, model=self.model, max_tokens=WORDING_TOKENS, system=self.system(), messages=messages)
            words = self._spoken(resp, restore=False)
        except Exception:                                             # noqa: BLE001 — it stayed away: the plain apology below
            words = ""
        return ANNOTATION.sub("", self._wipe_calls(TEXT_CALL.sub("", self._phantom(words)))).strip() or "Mentat answered, and then my brain wandered off. Ask me again."      # (review4:seams: nor a call in another dialect)

    def _second_try(self, text: str, messages: list, nudge: str, tools: list[dict], fallback: str, tools_ran=None, ordered: bool | None = None, merge: bool = False) -> str:
        """One more go after a claim with no tool: `nudge` says so, `tools` are what it may call. A tool that runs is its answer; otherwise its words stand only when they take
        the claim back or ask (musicguard.music_claimed: anything else is another claim), else `fallback`. `ordered` (not None) puts the try behind the gate of the one after a denial (_run_call:
        retry_may, never the model's own `confirm`), also for a call it writes as words: the try after a bare "Okay." to an order is not allowed to undo or announce. `merge`: the first reply is NOT
        shown again; the nudge joins his message instead (a model shown a call written as text as its own last turn writes it again: 9 of 9 on the real Core)."""
        ran = tools_ran or self._ran_music
        self._refused_at = None                                       # (nothing was refused yet: _refuse, _unsound)
        if merge and messages and messages[-1].get("role") == "user" and isinstance(messages[-1].get("content"), str):
            messages[-1] = {"role": "user", "content": messages[-1]["content"] + "\n\n" + nudge}
        else:
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": nudge}]
        new = ""
        for _ in range(3):
            try:
                resp = self._create(need_words=True, model=self.model, max_tokens=SPEAK_TOKENS, system=self.system(), messages=messages, **({"tools": tools} if tools else {}))
            except Dropped:
                break
            blocks = list(resp.content)
            new = self._spoken(resp, restore=ordered is not None)       # (a call written inside its reasoning is put back only where _text_calls reads the words, just below: with no gate it is not)
            calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
            if not calls:
                break
            messages.append({"role": "assistant", "content": [self._dump(b) for b in blocks]})
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": self._run_call(c, ordered)} for c in calls]})
        if ordered is not None and new and not ran():                  # a call written as words ("[home_control name=lamp action=on]"): through the same gate as a real one
            new = self._text_calls(ANNOTATION.sub("", new).strip(), messages, allow=lambda n, a: retry_may(n, a, ordered), patience=RETRY_PATIENCE_S)
        new = self._wipe_calls(new)                                   # (fix4:guards, core3#2: a call written in another dialect, or one a tool that ran left behind, is not words; nothing left is the fallback)
        if ran() and not self._unsound(new):                          # (a tool ran, or a look answered; a refused change followed by "I've cancelled it" is not done: the words must own it, core3#3)
            return new or (text if self._changed() or merge else fallback)      # (no words after it: the first reply of a CLAIM stands only if a change ran, a look never makes it true; after a call written as text (merge) there is no claim, and _asked_again says the tool's own answer)
        return new if self._honest(new) else fallback

    def _asked_again(self, text: str, messages: list, names, meant: list, ordered: bool | None = None) -> str:
        """A tool call written as text that the house could not read or run, and nothing ran: the model is asked again, with the tools. The model is NOT shown its bracket as its own last turn (it wrote it again:
        9 of 9 on the real Core, 0.1 s each): the nudge (TEXT_AGAIN) joins his message and names the tool it `meant`. A tool that runs is the answer; a second reply that calls nothing says plainly that it was not done.
        `ordered` (fix4:guards, code3#1) puts the try behind the gate of the other second tries (_run_call: retry_may, never the model's own `confirm`): a bracket like '[cancel_alarm] Noted.' was followed by a call
        of cancel_alarm that cancelled both alarms."""
        said = self._wipe_calls(text, names, every=True)
        out = self._second_try(said or "Hm.", messages, TEXT_AGAIN.format(named=f" ({', '.join(meant[:2])})" if meant else ""), self.tools(), UNCLAIM, tools_ran=lambda: bool(self.actions), ordered=ordered, merge=True)
        if self.actions and not said and out == "Hm.":                # (the tool ran and the model never worded it: the tool's own answer, not the placeholder)
            out = str(self.actions[-1]["outcome"])
        return self._wipe_calls(out, names, every=True) or (str(self.actions[-1]["outcome"]) if self.actions else "") or out

    def _text_calls(self, text: str, messages: list, allow=None, patience: float | None = None, ordered: bool | None = None) -> str:
        """A tool call written as words ("[list action="read" list="shopping"]") is run as the call it meant, and the model words the answer. `allow`, when given, is the gate of the
        second try after a denial (retry_may): a call it refuses is not run, and no call runs with the `confirm` the model wrote (that is his yes, aloud). Only the arguments the tool has are kept ("[music
        mode=weird]" is a call with none); one that cannot be read, or a bracket that starts with a tool's name and that TEXT_CALL cannot read at all ("[set_timer 90 tea]"), runs nothing and the model is
        asked again (_asked_again), unless this is a second try already (`allow`): its calls are dropped and the words stand. `ordered` (fix4:guards, the third review's code3#1) is the gate of THAT second try, the
        one after a call that could not be read: his words are not a question (not NOT_AN_ORDER), so it looks at anything and changes something, but never cancels, clears or announces on its own, never touches
        what has to keep running, and never with the `confirm` the model writes (_run_call). None (a second try already, or no gate asked for): as before."""
        names = {t["name"] for t in self.tools()}
        found = [m for m in TEXT_CALL.finditer(text) if m.group(1) in names]
        if not found:
            # A bracket that starts with the name of a tool, "=" in it or not, is never spoken. When it is a call TEXT_CALL cannot read ("[set_timer 90 tea] Ninety seconds.", "[play_music: genre=jazz, speaker=living
            # room]") and nothing ran, the words that came with it are a claim with nothing behind it: the model is asked again, as for a call that was read and had no arguments (fix3:text, problem 7).
            meant = sorted(set(self._tool_brackets(text, names))) if allow is None else []
            if not meant:                                             # no call in it, or this is a second try already (its calls are dropped, the words stand)
                return self._wipe_calls(text, names)
            print(f"[brain] a tool call written as text that could not be read: {meant} in {text.strip()[:60]!r}", flush=True)
            return self._asked_again(text, messages, names, meant if len(meant) < 2 else [], ordered)
        consumed, readable, unread = [], [], []
        for m in found:
            args = {}
            for a in TEXT_ARG.finditer(m.group(2)):
                v = next(g for g in a.groups()[1:] if g is not None)
                args[a.group(1)] = int(v) if v.isdigit() else {"true": True, "false": False}.get(v.lower(), v)
            props = self._tool_props(m.group(1))
            args = {k: v for k, v in args.items() if k in props}      # only the arguments the tool has ("[music mode=weird]" ran music with a `mode`): none left is a call with none, read below
            if not args:                                              # "[music]" with the arguments on the lines after it ("action: next"): the real Core wrote that for "skip to the next track" (3 of 96)
                args, used = self._call_lines(m.group(1), text[m.end():])
                consumed.append((m.end(), m.end() + used))
            if not args:                                              # "[music]next[/music]", "[music] with action \"next\"" (3 of 7 for "could you skip to the next track"): one bare word, an action the tool has
                args, used = self._bare_action(m.group(1), text[m.end():])
                consumed.append((m.end(), m.end() + used))
            # A call with no arguments is run only if the tool needs none (list_timers) and it stands alone or only looks: never "music" with nothing (production answers "I cannot do that to the music.", and
            # he heard "Done" about nothing), never a play_music or an announce among ten more brackets (the real Core, in a long reply, wrote a list of a dozen "[tool] with what ..." and every one ran empty).
            (readable if args or (not self._needs_arguments(m.group(1)) and (len({f.group(1) for f in found}) == 1 or m.group(1) in RETRY_READS)) else unread).append((m, args))
        lost = {m.group(1) for m, _ in unread} - {m.group(1) for m, _ in readable}      # (a bracket written twice, the second one a stray closer, is no loss: its tool was read once)
        if not readable or len(lost) >= 2:                            # nothing could be read, or a pile of brackets (the model derailed): nothing runs, it is asked again with the tools
            print(f"[brain] a tool call written as text that could not be read: {[m.group(0)[:60] for m in found][:4]}", flush=True)
            if allow is not None:                                     # (inside a second try already: the calls are dropped, the words stand)
                return self._wipe_calls(text, names, every=True)
            return self._asked_again(text, messages, names, sorted({m.group(1) for m in found}) if len(lost) < 2 else [], ordered)       # (names the tool it meant, unless it is a pile)
        results, done = [], []
        for m, args in readable:
            if allow is not None:
                args.pop("confirm", None)
                if not allow(m.group(1), args):
                    results.append(self._refuse())
                    continue
            if (m.group(1), args) in done:                            # the same call written twice in one reply ("[music] with action next ... [music] with action next") is one call
                continue
            done.append((m.group(1), args))
            results.append(self.run_tool(m.group(1), args))
        print(f"[brain] a tool call written as text, run: {[m.group(0)[:60] for m, _ in readable]}", flush=True)
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "[The house ran the tool call you wrote as text: " + " / ".join(results)
                                                 + ". Now say the answer aloud, in plain words. Never write a tool call as text: call the tool.]"}]
        try:
            resp = self._create(need_words=True, patience=patience, model=self.model, max_tokens=WORDING_TOKENS, system=self.system(), messages=messages)
            words = self._spoken(resp, restore=False)                    # (the wording of a call that ran or was refused: nothing in it is read as a call again)
        except Exception:                                             # noqa: BLE001 — the tool's own words will do
            words = ""
        words = ANNOTATION.sub("", self._wipe_calls(words, names, every=True)).strip()
        for start, end in reversed(consumed):                         # (the lines that were its arguments are not words either)
            text = text[:start] + text[end:]
        return words or self._wipe_calls(text, names, every=True).strip() or results[-1]

    def _call_lines(self, name: str, rest: str) -> tuple[dict, int]:
        """The arguments of a call written as `[music]` with its arguments on the lines that follow ("action: next", "level: 5", "what: null" skipped): the lines straight after the bracket (or on
        its line) that read `key: value` with a key the tool really has. -> (arguments, characters used); nothing used when the first line after it is a sentence."""
        props = self._tool_props(name)
        args, used = {}, 0
        for i, line in enumerate(rest.splitlines(keepends=True)[:12]):
            body = line.strip()
            if not body:
                if i == 0:                                            # (the rest of the bracket's own line)
                    used += len(line)
                    continue
                break
            m = TEXT_LINE.fullmatch(body)
            if m is None:
                break
            value = next((g for g in m.groups()[1:] if g is not None), "")
            value = value.rstrip(".;") if m.group(4) is not None else value
            null = value.lower() in ("null", "none", "")
            if m.group(1) not in props and not null:
                break
            used += len(line)
            if m.group(1) in props and not null:
                args[m.group(1)] = int(value) if value.isdigit() else {"true": True, "false": False}.get(value.lower(), value)
        return args, (used if args else 0)

    @staticmethod
    def _dump(b) -> dict:
        if getattr(b, "type", "") == "tool_use":
            return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        return {"type": "text", "text": getattr(b, "text", "")}
