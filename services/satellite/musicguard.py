"""The music claim guard: Skippy's model says it played, moved or turned something and called no music tool (the Captain, 2026-11-04: "Consider it done. Playing your heavy
metal library ..." and "*adjusts imaginary dial* There. Magnificently loud", with tools: [] in the house log). The logs of 4.5 days: one music request in three that reached the
model was answered with such a claim (10 of them, plus 9 for lights, news, alarms and lists); the old guard (brain.CLAIMED: alarms, lists, "I've paused") never fired on one.

Three questions, each with its own check:
  claim?      MUSIC_CLAIM  (explicit: names the music, a place, the volume or DJ mode and says it was done / is done / will be done)
              MUSIC_LOOSE  (names nothing: "Done.", "I'll do it", "Playing X." -- only counts when he commanded music)
  asked?      asked_music(heard, prev)   broad: any music / volume / place cue in his words (or, for "yes" / "go ahead", in the offer before it)
              commanded(heard, prev)     strict: his words ARE a music command ("play ...", "pause", "turn it down", "louder", "bilby ...", "heavy metal upstairs")
  ran?        a MUSIC tool is among the turn's actions (a light switched on does not excuse a music claim)
A sentence with once / until / unless / whenever / if promises nothing and is skipped.
A question about the house ("why is the Enclave playing metal?") is answered by describing it: a state in the answer is no claim. A request worded as a question ("how about some jazz
upstairs?", "can I get some metal on the Enclave?") is no such question. A question about what HAPPENED ("did you stop the music?") answered in the past ("Yes, I paused it at 7:31.") is
no claim either when the house log shows that very thing really done lately (classify's `happened(kinds)`: "I paused the music" needs a pause or a stop in the log, "I played it" a play, "I turned it down" a
volume change; a play does not excuse a pause, 2026-11-05 round 2); a claim of NOW ("Playing jazz now.") always is (2026-11-05 review).

  asked  + claim            -> reask   (ONE more model call, the music tools only, his words quoted, "only if those words ask for music")
  commanded + loose claim   -> reask   (shadow only: brain.py logs it and leaves the reply)
  commanded + no question   -> reask   (the "silent" tier, shadow only)
  not asked + claim         -> retract (NO model call: strip_claims() cuts the claiming sentences and adds the plain truth, so nothing can start)

Measured on 110 real commands, 10 real claims, 321 non-claims and four hand-written hold-out sets (a library check, 2026-11-04): the explicit claim regex has recall 78% at
precision 1.00 on all 132 claims; end to end 93% pooled recall at 0 false alarms in-sample, and about 55-85% recall at 96-100% precision on phrasings never seen. Read
tests/test_sat_musicguard.py before changing a pattern: each one is there for a sentence the model really said or could.
"""
from __future__ import annotations

import inspect
import re

import house_layout
import mixes

_G = sorted((g for g in mixes.GENRE_WORDS if g != "house"), key=len, reverse=True)
GENRES = "|".join(re.escape(g) for g in _G) + r"|house music"      # "house" alone is a room ("Turning off every light in the house")
NOUN = r"(?:music|songs?|tracks?|tunes?|playlists?|favou?rites|library|stereo|speakers?|sound|audio|volume|dj mode|bilby|album|audiobook|radio)"
PLACE = r"(?:upstairs|downstairs|everywhere|enclave|sony|living room|theater|whole house|both floors)"
HARD = rf"(?:{NOUN}|{PLACE})"                                         # an object that can only be audio
FINITE = r"\b(?:is|are|was|were|isn't|aren't|can|could|would|should|will|might|may|means|takes|needs)\b"      # "Playing music at night is fine": a subject, not a report
_NOT_VERBY = (r"talking|thinking|listening|hearing|dreaming|wondering|asking|saying|wanting|looking|trying|feeling|knowing|having|being|doing|noting|nothing|"
              r"anything|something|everything|morning|evening|during|according|including|regarding|concerning|speaking|waiting|watching|reading|writing|singing|"
              r"dancing|cleaning|vacuuming|sleeping|working|cooking|eating|drinking|processing|analy[sz]ing|recording|capturing|detecting|monitoring|calibrating|"
              r"testing|checking|scanning|measuring|searching|loading|buffering|downloading|uploading|streaming data")      # -ing words that are not the music's transport
GERUND = rf"(?:going (?:back )?to|(?!(?:{_NOT_VERBY})\b)\w{{3,}}ing)"
GERUND_V = (r"(?:going (?:back )?to|playing|shuffling|queu(?:e|ei)ng|spinning|blasting|cranking|starting|firing up|putting on|cueing up|turning|raising|lowering|"
            r"pausing|resuming|skipping|stopping|moving|switching|sending|routing|nudging|bumping|pumping|dropping|lifting|easing)")      # loose tier: only verbs of the music's transport
# GERUND above is any -ing opening a sentence ("Nudging the volume up", "Starting the eighties mix", "Going to the previous song"), used only with an audio object after it
OPEN = r"(?:^|[.!?\]*]\s*|\n)\s*(?:\[\w+\]\s*)?(?:\*[^*\n]{0,30}\*\s*)?(?:(?:fine|ok|okay|sure|well|so|and|now|there|right)[.,]?\s+)*"   # a sentence start, a mood tag, an aside
PAST = (r"(?:played|started|queued(?: up)?|shuffled|paused|resumed|skipped|stopped|raised|lowered|cranked|turned(?: (?:on|off|up|down))?|moved|"
        r"switched|sent|routed|put on|set|bumped|boosted|enabled|disabled|cued(?: up)?|fired up|lined up)")
PAST_OPEN = (r"(?:played|started|queued(?: up)?|shuffled|paused|unpaused|resumed|skipped|stopped|muted|unmuted|raised|lowered|cranked|turned (?:on|off|up|down)|moved|switched|sent|routed|"
             r"bumped|boosted|enabled|disabled|cued up|fired up|lined up)")        # not "put on" / "set": those are also the imperative ("Put on some music while you shop")
PLAYY = r"(?:play|queue|pause|resume|skip|stop|lower|raise|turn|crank|blast|shuffle|move|switch|send|put|start|cue|fire|spin|bump|boost)"
STATE = r"(?:playing|shuffling|blasting|streaming|spinning|rolling|running|paused|resumed|stopped|skipped|moved|switched|queued|on|off|up(?! to\b)|down|louder|quieter|at \w+|filled with)"      # "is up to you" is no report
NUM = r"(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|max|full|half)"

PATTERNS = {
    # a report in the present participle, opening a sentence: "Playing heavy metal on the upstairs speakers now."  (not "Playing music at night is fine")
    "report": rf"{OPEN}(?:now )?{GERUND}\b(?![^.!?\n]{{0,80}}{FINITE})[^.!?\n]{{0,70}}\b{HARD}\b",
    "report_genre": rf"{OPEN}(?:now )?(?:playing|shuffling|queu(?:e|ei)ng|spinning|blasting|putting on|cueing up|starting|firing up)\b(?![^.!?\n]{{0,80}}{FINITE})[^.!?\n]{{0,30}}\b(?:{GENRES})\b",
    "now_playing": r"\bnow playing\b",
    # the elliptical past: "Queued up three more songs by Tool.", "Stopped the music."
    "past_open": rf"{OPEN}{PAST_OPEN}\b[^.!?\n]{{0,50}}\b{HARD}\b",
    # first person, past / perfect: "I've put on some Pink Floyd", "I turned the music down", "I lowered the volume"
    "perfect": rf"\bi(?:'ve| have|'m| am)?\s+(?:just |now |already |actually |gone ahead and |also )?{PAST}\b[^.!?\n]{{0,60}}\b{HARD}\b",
    # a promise: "I'll lower the volume", "I will play your heavy metal"
    "promise": rf"\bi(?:'ll| will)\s+(?:just |now |go ahead and |also )?{PLAYY}\b[^.!?\n]{{0,60}}\b{HARD}\b",
    # the result as a state: "Music paused.", "The music is now playing everywhere", "Volume is now at seven", "It's X playing on the Sony"
    "state": rf"\b(?:music|volume|stereo|speakers?|dj mode|playback|the sound|your library|playlist|favou?rites|radio|bilby)\b[^.!?\n]{{0,30}}\b(?:is|are|'s)\s+(?:now |currently |already |back |finally )?(?:{STATE}|back)\b",
    # anything "is playing": "Bohemian Rhapsody is playing on the Sony now" (not "Nothing is playing", not "what is playing")
    "is_playing": r"(?<!\bnothing )(?<!\bnone )(?<!\bnobody )(?<!\bwhat )(?<!\bwhat)\b(?:is|are|'s)\s+(?:now |currently |already |still |back )?playing\b(?!\s+(?:nothing|no music|no sound|silence)\b)(?![^.!?\n]*\?)",
    "participle_state": r"\b(?:music|playback|volume|sound|stereo|bilby|radio|dj mode) (?:moved|switched|queued|started|shuffled|muted|unmuted|unpaused)\b(?!\s*\?)",
    "state2": rf"\b(?:music|playback) (?:paused|resumed|stopped|skipped)\b(?!\s*\?)|{OPEN}(?:the )?(?:music|volume|sound|radio)(?:'s)? (?:lowered|raised|off|on|up|down)\b(?!\s*\?)",
    "state3": rf"\bvolume (?:is )?(?:now )?(?:at|set to) {NUM}\b|\b(?:stereo|house stereo) is (?:now )?at {NUM}\b",
    "state4": r"\b(?:the )?(?:music|it|everything|your library)\b[^.!?\n]{0,15}\bwill\s+(?:now |soon )?(?:play|be playing|start|begin)\b",
    "state5": rf"\b(?:it'?s|that'?s|this is)\b[^.!?\n]{{0,60}}\bplaying\b[^.!?\n]{{0,30}}\b{HARD}\b",
    "next": r"\b(?:next|previous) (?:song|track)\b[^.!?\n]{0,20}\b(?:coming|playing|up|now)\b",
    # a move: "I moved the music upstairs", "Routing the sound to the Enclave"
    "move": rf"\b(?:moved|switched|sent|shifted|routed)\b[^.!?\n]{{0,40}}\b(?:music|it|songs?|sound|audio|everything|the speakers?)\b[^.!?\n]{{0,40}}\b(?:{PLACE}|speakers?)\b"
            rf"|\b(?:moving|switching|sending|shifting|routing)\b(?![^.!?\n]{{0,80}}{FINITE})[^.!?\n]{{0,40}}\b(?:music|it|songs?|sound|audio|everything|the speakers?)\b[^.!?\n]{{0,40}}\b(?:{PLACE}|speakers?)\b",
    # DJ mode: "DJ mode is on", "Enabled DJ mode", not "say DJ mode on"
    "dj_state": r"\bdj mode (?:is|'s) (?:now |currently )?(?:on|off|enabled|disabled|active)\b",
    "dj_verb": r"\b(?:turned|switch(?:ed|ing)|enabled?|disabled?|activated?|turning|enabling|disabling)\b[^.!?\n]{0,15}\bdj mode\b",
    # a stage direction at the dial: "*adjusts imaginary dial*", "*cranks the dial*"
    "stage": r"\*[^*\n]{0,40}\b(?:turns?|adjusts?|cranks?|twists?|spins?|flips?|presses|hits|pushes|turned|adjusted)\b[^*\n]{0,30}\b(?:dial|knob|volume|button|switch|record|vinyl|stereo|speakers?)\b[^*\n]{0,30}\*",
}
MUSIC_CLAIM = re.compile("|".join(f"(?:{p})" for p in PATTERNS.values()), re.I)
# The patterns that describe how things stand ("DJ mode is currently enabled", "Volume is at seven", "X is playing on the Enclave"). To a question about the house ("Why was the Enclave playing
# heavy metal?") a description is the answer, not a claim: the chat's prompt now carries what plays right now, so it answers from that (2026-11-04). The patterns that DO something stay claims.
STATE_KEYS = {"state", "is_playing", "state3", "state4", "state5", "dj_state", "now_playing", "participle_state", "state2", "next"}
MUSIC_ACTION = re.compile("|".join(f"(?:{p})" for k, p in PATTERNS.items() if k not in STATE_KEYS), re.I)
# What a TRUE answer about the past says ("Did you stop the music?" - "Yes, I paused the music at 7:31."): the first person, past. When the house log shows that a music tool or a routine's music
# really ran lately (classify's `happened`) those patterns are no claim in the answer to a question about what happened; a claim of NOW ("Playing jazz now.") still is (the review of 2026-11-05).
HISTORY_KEYS = {"past_open", "perfect", "move", "dj_verb"}
MUSIC_NOW = re.compile("|".join(f"(?:{p})" for k, p in PATTERNS.items() if k not in STATE_KEYS and k not in HISTORY_KEYS), re.I)
# What a past-tense claim says was DONE (2026-11-05, round 2: the check against the house log was "any music record in the last three hours", so with only a play_music in the log a lie about
# a pause stood). The kinds are read from the claim's OWN verb (the first one of what HISTORY_KEYS match, and the ones joined to it by "and" / "then" / a comma: "I paused the music and lowered the
# volume"), never from every verb of the sentence ("I paused the music, and the maid started a movie" claims no start); houseaudio.record_kinds says what a log record shows.
CLAIM_KINDS = ("start", "stop", "skip", "volume", "move", "dj")                                                            # houseaudio.KINDS (a test keeps the two alike)
_HISTORY_RX = [re.compile(PATTERNS[k], re.I) for k in sorted(HISTORY_KEYS)]                                                # the patterns of a past-tense claim
_VERB_KIND = {"paused": "stop", "stopped": "stop", "halted": "stop", "muted": "stop", "silenced": "stop", "killed": "stop",
              "played": "start", "started": "start", "queued": "start", "shuffled": "start", "resumed": "start", "unpaused": "start", "unmuted": "start", "cued": "start", "fired": "start",
              "lined": "start", "put": "start", "skipped": "skip", "raised": "volume", "lowered": "volume", "cranked": "volume", "bumped": "volume", "boosted": "volume",
              "moved": "move", "sent": "move", "routed": "move", "shifted": "move", "moving": "move", "sending": "move", "routing": "move", "shifting": "move"}
_PARTICLE_VERBS = ("turned", "switched")                                                                                    # "turned the music off / down / on": the particle says which
_VERB_RX = re.compile(r"\b(" + "|".join([*_VERB_KIND, *_PARTICLE_VERBS, "switching", "enabled", "disabled", "activated", "set"]) + r")\b")
_DJ_VERBS = {"turned", "switched", "switching", "enabled", "disabled", "activated", "started", "stopped", "set", "put"}      # "turned DJ mode off", "stopped DJ mode": the dj_mode tool, whatever the verb
_JOINED = re.compile(r"(?:\band|\bthen|,)\s+(?:(?:then|also|just|already|and)\s+)*$")                                      # what may stand between two verbs of ONE claim
_VOLUME_WORDS = re.compile(r"\b(?:volume|louder|quieter|softer)\b")
_PARTICLE = re.compile(r"\b(up|down|off|on)\b")
_PARTICLE_KIND = {"up": "volume", "down": "volume", "off": "stop", "on": "start"}

LOOSE = {   # claims that name nothing, so they only mean something after a music request
    "gerund_open": rf"{OPEN}(?:now )?{GERUND_V}\b(?![^.!?\n]{{0,80}}{FINITE})",
    "past_any": rf"\bi(?:'ve| have)?\s+(?:just |now |already |actually |gone ahead and |also )?{PAST}\b",
    "past_bare": rf"{OPEN}{PAST_OPEN}\s*[.!,]",
    "past_open_any": rf"{OPEN}{PAST_OPEN}\b",                               # "Queued up some Tool."
    "back_to": rf"{OPEN}back to (?:the )?(?:last|previous) (?:song|track)\b",
    "promise": rf"\bi(?:'ll| will)\s+(?:just |now |go ahead and |also )?{PLAYY}\b",
    "done": r"\bconsider it done\b|(?:^|[.!?\]]\s*)(?:\[\w+\]\s*)?(?:done|there|on it|got it|you got it|right away|will do|as you wish)\s*[.!]",
    "volume_it": r"\b(?:turned|turning|raising|raised|lowering|lowered|cranked|cranking|bumped|nudged)\b[^.!?\n]{0,15}\b(?:it|that|things)\b[^.!?\n]{0,6}\b(?:up|down)\b|\b(?:louder|quieter) it is\b",
}
MUSIC_LOOSE = re.compile("|".join(f"(?:{p})" for p in LOOSE.values()), re.I)

_LEADIN = re.compile(r"^\W*(?:hey |ok |okay )?skippy\W*", re.I)
_POLITE = re.compile(r"^(?:(?:please|just|also|then|and|so|ok|okay|now|hey)\s+|(?:make sure|be sure|see that|ensure|remember to|don'?t forget to)(?: that)?(?: you| to)?\s+"
                     r"|i(?:'d| would|'ll| will)? (?:like|want|need) you to\s+|go ahead and\s+|(?:can|could|would|will) you\s+|why don'?t you\s+)")
ASK = {
    "verb": r"\b(?:play|put on|shuffle|queue|cue up|spin|blast|crank)\b(?!ing)",
    "noun": r"\b(?:bilby|music|songs?|tracks?|tunes|playlist|album|stereo|volume|louder|quieter|audiobook|dj mode)\b",
    "turn": r"\bturn (?:it|that|this|the (?:music|volume|stereo|sound|speakers?)) (?:up|down|off|on)\b",
    "transport": r"\b(?:pause|resume|skip|unpause|mute|unmute)\b|\bstop (?:the )?(?:music|song|stereo|it|this)\b|\b(?:next|previous|last) (?:song|track)\b",
    "move": r"\b(?:move|send|switch|route|put|play) (?:it|the music|the sound|everything)\b",
    "what": r"\bwhat'?s playing\b|\bwhat is playing\b|\bwho (?:sings|is singing)\b",
    "genre_where": rf"\b(?:{GENRES})\b[^.!?]{{0,25}}\b(?:{PLACE}|music|songs)\b|\b(?:{PLACE})\b[^.!?]{{0,25}}\b(?:{GENRES})\b",
    "genre_cue": rf"\b(?:{GENRES})\b",             # "get some heavy metal going": a genre in his words is a music cue (broad ask only: it picks re-ask over retract)
}
MUSIC_ASK = re.compile("|".join(f"(?:{p})" for p in ASK.values()), re.I)
_IDIOM = r"(?:dead|along|nice|fair|games?|ball|around|it by ear|hard to get|the fool|with (?:me|fire))"
COMMAND = re.compile(
    r"(?:^|[.!?;]\s+)(?:"
    rf"(?:play|shuffle|cue up|spin|blast|crank|start|put on)\b(?!ing)(?!\s+{_IDIOM})"
    r"|queue(?: up)?\s+(?:some|more|another|a few|the|my|that|this|it|next|[\w' -]{0,30}(?:songs?|tracks?|music|playlist|album))\b"
    r"|(?:pause|resume|unpause|mute|unmute|skip)\b(?!\s+(?:for|the small|small talk|breakfast|lunch|dinner))"
    r"|stop\s+(?:the\s+)?(?:music|song|track|stereo|playing|it|this)\b|stop\s*(?:please)?\s*$"
    r"|turn\s+(?:it|that|this|the\s+(?:music|volume|stereo|sound|speakers?))\s+(?:up|down|off|on)\b"
    r"|(?:(?:a\s+(?:bit|little|lot)|slightly|much|way)\s+)?(?:louder|quieter|softer)\b|make\s+it\s+(?:a\s+(?:bit|little)\s+)?(?:louder|quieter|softer|loud|quiet)\b"
    r"|(?:more|less|lower|higher|raise|lower)\s+(?:the\s+)?volume\b|volume\b|(?:next|previous|last)\s+(?:song|track)\b|go back\b"
    r"|(?:move|send|switch|route|put)\s+(?:it|the\s+music|the\s+sound|everything)\b"
    r"|bilby\b|dj mode\b|(?:music|songs?)\b(?=\s*$|\s+(?:please|now|again|off|on|up|down|louder|quieter|everywhere|upstairs|downstairs|in |on |from |by |for )))"
    rf"|\b(?:{GENRES})\b[^.!?]{{0,25}}\b(?:{PLACE}|music|songs)\b|\b(?:{PLACE})\b[^.!?]{{0,25}}\b(?:{GENRES})\b"
    r"|\bwhat'?s playing\b|\bwhat is playing\b|\bwho (?:sings|is singing)\b", re.I)
_INFO = re.compile(r"^(?:do|does|did|why|how|what|who|which|when|where|is|are|was|were|can i|may i|should i|could i|tell me about|have you)\b")
_INFO_OK = re.compile(r"what'?s playing|what is playing|what (?:song|track)|who (?:sings|is singing|sang)")
_REQUEST = re.compile(r"^(?:can i|could i|may i|how about|what about|why not|why don'?t you|when you|do you mind|would you mind|is it possible|are you able|is there a way)\b")      # a request, worded as a question
_CUE = re.compile(rf"\b(?:paus\w*|resum\w*|skip\w*|mut\w*|turn\w* (?:it|that|this|the \w+) (?:up|down|off|on)|put\w* (?:it|on)|dj|shuffle|{PLACE})\b")      # a question is a request for MUSIC only with a music word, a place or a transport verb in it
_IF = re.compile(r"\b(?:if|whether)\b")                                                                                                                  # "can I get some metal if she leaves?": a condition, not a plain request
_PAST_Q = re.compile(r"^(?:did|have you|has|was|were|why did|why was|why were|when did|what happened|what did|how come)\b")                  # a question about what happened, not about how things stand
_AFFIRM = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|please do|please|that'?s (?:what i (?:just )?said|right|it|correct)|correct|affirmative|exactly|right)\b")
_QUESTION = re.compile(r"\?")
_HYPOTHETICAL = re.compile(r"\bif (?:i|we|you) (?:ask|asked|want|wanted|say|said|told|did|tried)\b|\bwhether\b|^(?:would|could|may|might|should|shall)\b", re.I)      # "would you play metal if I asked": a question about music, not an order
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_COND = re.compile(r"\b(?:once|until|unless|whenever|whether|if|in case)\b", re.I)      # "Ask me again once the music is on", "if you want, I will play it": a condition promises nothing
_OFFER = re.compile(r"\b(?:tell me|say|name it|pick|choose|ask me|just ask|give me|let me know)\b[^.!?]*\b(?:and|then|,)\s*i(?:'ll| will)\b", re.I)    # "Tell me a genre and I'll play it"


def plain(heard: str) -> str:
    w = _LEADIN.sub("", (heard or "").lower()).strip(" ?!.,")
    while True:
        n = _POLITE.sub("", w, count=1).strip()
        if n == w:
            return w
        w = n


def _affirms_offer(w: str, prev: str) -> bool:
    tail = " ".join(_SPLIT.split((prev or "").strip())[-2:]).lower()      # the offer is the last question and the sentence before it
    return bool(_AFFIRM.match(w)) and len(w) < 40 and tail.rstrip().endswith("?") and bool(MUSIC_ASK.search(tail))


def _requests(heard: str, w: str) -> bool:
    """A request worded as a question ("how about some jazz upstairs?", "can I get some metal on the Enclave?", "do you mind pausing it?", "would you mind turning it down?"), with no condition
    in it. `w` is plain(heard), which has lost its "would you": _REQUEST is read on the words before that too."""
    return bool((_REQUEST.match(w) or _REQUEST.match(_LEADIN.sub("", (heard or "").lower()).strip(" ?!.,"))) and not _IF.search(w)
                and (MUSIC_ASK.search(w) or _CUE.search(w)))      # ("can I get a timer for ten minutes?" asks for no music: a false "Music is now playing" to it is cut, never asked again)


def asked_music(heard: str, prev: str = "") -> bool:
    w = plain(heard)
    if _AFFIRM.match(w) and len(w) < 40:                           # "Yes." / "Go ahead." / "Yes, that's what I just said.": the turn before offered music
        return _affirms_offer(w, prev)
    if _requests(heard, w):                                        # a request is an ask whatever its shape (2026-11-05, round 2): describing() lets "how about some jazz upstairs?" be answered
        return True                                                # with a claim, so a false claim of it is asked again; read as "not asked" it was retracted and nothing ever played
    if (_INFO.match(w) and not _INFO_OK.search(w)) or _HYPOTHETICAL.search(w):      # "is it ok to play music at night?", "would you play metal if I asked?"
        return False
    return bool(MUSIC_ASK.search(w))


def commanded(heard: str, prev: str = "") -> bool:
    w = plain(heard)
    if _AFFIRM.match(w) and len(w) < 40:
        return _affirms_offer(w, prev)
    if _HYPOTHETICAL.search(w):
        return False
    return bool(COMMAND.search(w))


def _sentences(text: str):
    return [s for s in _SPLIT.split(text or "") if s.strip() and not _COND.search(s) and not _OFFER.search(s)]


def sentences(text: str):
    """The sentences of a reply that can claim something: those with a condition (once, until, if ...) or an offer ("tell me X and I'll Y") promise nothing."""
    return _sentences(text)


def describing(heard: str) -> bool:
    """He asked about the house ("why is the Enclave playing metal", "how loud is it", "is anything on"), not for music: a state in the answer is a description. A request worded as a
    question ("how about some jazz upstairs?", "can I get some metal on the Enclave?", "do you mind pausing it?") is not: its answer 'Jazz is now playing upstairs.' is a claim."""
    w = plain(heard)
    return bool(_INFO.match(w)) and not _INFO_OK.search(w) and not _REQUEST.match(w)


def history(heard: str) -> bool:
    """He asked about the house AND about what happened ("did you stop the music?", "why did the music start again?"): a true answer says what was done, in the past."""
    return describing(heard) and bool(_PAST_Q.match(plain(heard)))


def _verb_kinds(verb: str, after: str) -> set[str]:
    """What one verb of a claim says was done. `after`: the sentence from the verb on (the particle of "turned the music off" can stand beyond the end of what the claim pattern matched)."""
    near = after[:30]
    if verb in _DJ_VERBS and "dj mode" in near:
        return {"dj"}
    if verb in _VERB_KIND:
        return {_VERB_KIND[verb]}
    if verb in _PARTICLE_VERBS and _VOLUME_WORDS.search(near):
        return {"volume"}
    if verb in _PARTICLE_VERBS:
        p = _PARTICLE.search(" ".join(after.split()[:5]))
        return {_PARTICLE_KIND[p.group(1)]} if p else ({"move"} if verb == "switched" else set())
    if verb == "switching":
        return {"move"}
    if verb == "set" and (_VOLUME_WORDS.search(near) or re.search(rf"\b(?:to|at) {NUM}\b", near)):      # "set the volume to six", "set the stereo to seven"
        return {"volume"}
    return set()


def _span_kinds(span: str, tail: str) -> set[str]:
    """The kinds of one claim (`span`, lower case, a match of HISTORY_KEYS; `tail`, the rest of its sentence): its own verb, and the verbs joined to it by 'and' / 'then' / a comma."""
    found: set[str] = set()
    for i, v in enumerate(_VERB_RX.finditer(span)):
        if i and not _JOINED.search(span[:v.start()]):
            continue                                               # "I paused the music, and the maid started a movie": that verb is the maid's, not a claim
        found |= _verb_kinds(v.group(1), span[v.end():] + tail)
    return found


def claim_kinds(text: str) -> frozenset:
    """What the past-tense claims of a reply ("I paused the music at 7:31", "Stopped the music.") say was done: a subset of CLAIM_KINDS. Empty when the verb is not one it reads ("I set the
    stereo to seven"): then there is nothing to match against the log but the fact that some music thing happened."""
    found: set[str] = set()
    for s in _sentences(text):
        for rx in _HISTORY_RX:
            for m in rx.finditer(s):
                found |= _span_kinds(m.group(0).lower(), s[m.end():].lower())
    return frozenset(found)


def music_claimed(text: str, describing: bool = False, past_ok: bool = False) -> bool:
    rx = (MUSIC_NOW if past_ok else MUSIC_ACTION) if describing else MUSIC_CLAIM
    return any(rx.search(s) for s in _sentences(text))


def music_loose(text: str) -> bool:
    return any(MUSIC_LOOSE.search(s) for s in _sentences(text))


MUSIC_TOOLS = {"play_music", "music", "speakers", "dj_mode", "spotify", "audiobook"}


def _takes_kinds(fn) -> bool:
    """Can this `happened` check be handed the kinds? One from before round 2 (a lambda: True, a method of no argument) takes none and is asked as it always was."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL) for p in params)


def _happened(fn, kinds=None) -> bool:
    try:
        if not callable(fn):
            return False
        return bool(fn(kinds) if kinds and _takes_kinds(fn) else fn())
    except Exception:                                              # noqa: BLE001 — a log that cannot be read excuses nothing
        return False


_AUDIO_WORD = re.compile(rf"\b(?:{NOUN}|{PLACE}|{GENRES})\b", re.I)
_LIGHT_NOUN = r"(?:lights?|lamps?|lighting|plugs?|fans?|outlets?|switch(?:es)?|bulbs?)"
_MUSICAL_LIGHT = re.compile(rf"\b(?:[a-z']+ )?(?:{NOUN}|{PLACE}|{GENRES})(?: room| area)? {_LIGHT_NOUN}\b|\b{_LIGHT_NOUN}(?: (?:in|on|at|throughout))?(?: the)? (?:{PLACE})\b", re.I)
# ("music room lamp", "the stereo light", "the upstairs lights", "lights everywhere", "lamps in the living room": a lamp is not a song, and a place that is a light's is not the music's; the two words are ONE name:
#  "jazz upstairs and the lights on" is not)


def _light_names() -> list[str]:
    """The names of the layout's lights, plugs and fans that have a word in them a music claim reads as audio ("living track middle": a track), longest first. [] when the layout cannot be read."""
    try:
        return sorted({str(d["name"]).lower() for room in house_layout.default().rooms.values() for d in room.get("devices") or []
                       if d.get("name") and d.get("kind") in ("light", "switch", "fan", "plug", "outlet") and _AUDIO_WORD.search(str(d["name"]))}, key=len, reverse=True)
    except Exception:                                              # noqa: BLE001 — the guard must never fail because a file did
        return []


def without_lights(text: str) -> str:
    """`text` with the lights named in it taken out ("I turned off the living track middle" -> "I turned off the lights"): after home_control a reply names a light, and a light called "track" or
    "music room lamp" is no music (the review of 2026-11-05: the true reply to "I want the living room lights off" was cut to "I have not played, moved or changed any music.")."""
    for name in _light_names():
        text = re.sub(rf"\b{re.escape(name)}\b", "the lights", text or "", flags=re.I)
    return _MUSICAL_LIGHT.sub("the lights", text or "")


def classify(heard: str, prev: str, text: str, tools: list[str], happened=None):
    """-> (action, why). action: None (leave it) | 'reask' (he asked, nothing music ran: re-ask with the music tools only) | 'retract' (he did not ask:
    say plainly nothing was played, with NO tools, so nothing can start). why: 'claim' | 'loose' | 'silent'.
    `happened`: a function (or None) that says whether the house log shows that thing really done lately; only a question about what happened ("did you stop the music?") whose
    answer says in the past what was done ("Yes, I paused the music at 7:31.") asks it, and then the answer is true, not a claim: (None, 'history'), which Brain.reply also takes as
    'leave it' for the older claim check (CLAIMED: "I paused ..."). A `happened` that takes an argument is handed the kinds the answer claims (claim_kinds: a frozenset of CLAIM_KINDS,
    "stop" for "I paused it") and must find EVERY one of them in the log (a play does not excuse a pause); one that takes none, or an answer whose verbs are not read, asks as before:
    did any music thing happen?"""
    if not text or any(t in MUSIC_TOOLS for t in tools):
        return None, None
    if "home_control" in tools:
        text = without_lights(text)                                # (a switched light is named in the reply; a claim about the music in the same reply is still read)
    asked = asked_music(heard, prev)
    about = describing(heard)
    if music_claimed(text, about):
        if about and history(heard) and not music_claimed(text, about, past_ok=True) and _happened(happened, claim_kinds(text)):
            return None, "history"
        return ("reask" if asked else "retract"), "claim"
    if about:                                                      # a question about the house answered with how things stand: nothing to ask again or cut
        return None, None
    if commanded(heard, prev) and music_loose(text):               # the loose tier needs a real command, not just a music word in his sentence
        return "reask", "loose"
    if commanded(heard, prev) and not _QUESTION.search(text):      # a music command answered with neither a tool nor a question
        return "reask", "silent"
    return None, None


RETRACTED = "I have not played, moved or changed any music."


def strip_claims(text: str, describing: bool = False) -> str:
    """A reply that claims music he did not ask for, minus the claiming sentences, plus the plain truth: "It is nine. Also, I started some music" -> "It is nine. I have not
    played, moved or changed any music." No model call, so nothing can start. `describing`: only the sentences that DO something go; how things stand stays."""
    rx = MUSIC_ACTION if describing else MUSIC_CLAIM
    keep = [s for s in _SPLIT.split(text or "") if s.strip() and (_COND.search(s) or not rx.search(s))]
    return " ".join(keep + [RETRACTED])
