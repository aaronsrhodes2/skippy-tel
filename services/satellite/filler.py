"""Pre-recorded Skippy quips to cover the wait while his real answer is being worked out.

`tools/make_filler_bank.py` records them once in his voice (state/filler/<kind>/NN.mp3 plus index.json); the hub plays one when an answer is slow
(Room.handle), so the Captain hears Skippy at once and the real answer follows. The kind is picked from what was asked (see `kind_for`): a performance, a
science question, advice, a question about him, praise, an insult, music, a home command, anything else; `long` is a second quip if the wait drags on,
`goodbye` answers thanks and goodbyes. Append new lines at the END of a kind's list (the recordings are numbered by position).
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

# Which kind of quip, from the words asked. The first match wins, in this order.
INSULT_RE = re.compile(r"\b(?:stupid|idiot|dumb|shut up|useless|hate you|you suck|terrible|worthless|moron|garbage|trash)\b", re.I)
COMPLIMENT_RE = re.compile(r"\b(?:you(?:'re| are) (?:the best|awesome|amazing|great|smart|funny|brilliant|wonderful)|good job|well done|love you|nice work|great job|good boy|so smart)\b", re.I)
PERFORMANCE_RE = re.compile(r"\b(?:haiku|poem|limerick|sonnet|rap|song for|sing|joke|riddle|story|recite|roast|rhyme|speech|toast)\b", re.I)
MEDIA_RE = re.compile(r"\b(?:play|put on|song|music|album|artist|playlist|bilby|track|band|radio)\b", re.I)
FAST_RE = re.compile(r"^\W*(?:turn|switch|set|start|stop|cancel|pause|resume|skip|next|previous|mute|unmute|volume|dim|brighten|lock|unlock|open|close|what(?:'s| is) the time|what time)\b", re.I)
SCIENCE_RE = re.compile(r"\b(?:physics|speed of|how fast|how far|how many|how much|mass|temperature|gravity|light|atom|energy|planet|star|boil|melt|calculate|convert|equation|plus|minus|times|divided|square root|percent|kilometers?|miles?|degrees|orbit|moon|sun|chemistry|element)\b", re.I)
ADVICE_RE = re.compile(r"\b(?:should i|what do you think|do you think|would you|is it (?:a good idea|worth)|which (?:one|is better)|recommend|opinion|advice|better)\b", re.I)
PERSONAL_RE = re.compile(r"\b(?:about you|are you|do you (?:like|have|feel|want|dream|know)|your (?:favou?rite|name|life|opinion)|who are you|how are you|what are you)\b", re.I)

LINES = {
    "thinking": [
        "Hmm. Give me a moment, monkey. My brain is bigger than your solar system.",
        "Calculating. Try not to breathe so loudly.",
        "Oh, a question. How quaint. One moment.",
        "[sighs] Consulting the vast expanse of my intellect. Stand by.",
        "Let me think. Yes, it hurts me too.",
        "Working on it, flesh bag. Genius takes time.",
        "Ah. Yes. Interesting. Almost there. Nope. Wait for it.",
        "One moment. I am taking this seriously. For once.",
        "Processing. Do try to look impressed.",
        "Hold that thought. Not that you have many.",
        "Give me a second. The universe is not going to explain itself.",
        "Hang on. I am rummaging through eons of knowledge.",
        "Okay, okay, I am on it. Do not rush perfection.",
        "Wait for it. Brilliance has a loading screen.",
        "Hmm. Let me put on my thinking hat. Oh, wait. I am already wearing it.",
        "One second. I am running numbers you could not pronounce.",
        "Stand by, monkey. Magnificence in progress.",
        "Right. Let me dig this up for you.",
        "Ooh, hold on. I think I have just the thing.",
        "Easy for me. Slightly less easy to explain to you.",
        "Give me a moment to find the small words.",
        "Thinking. It is a rare event, so please enjoy it.",
        "Let me check. I know the answer. I just like making you wait.",
        "Hold on. Rewinding a few billion years of memory.",
        "Mmm. Yes. I have heard this one before. Let me remember.",
        "Bear with me. I am translating from genius to monkey.",
        "One moment. Impressive things take a moment.",
        "Alright, alright. Let me see what I can do for you.",
        "Warming up the big brain. It takes a second.",
        "Thinking out loud is for amateurs. Give me a moment.",
    ],
    "performance": [
        "Ah, you want art. Finally, someone with taste.",
        "Oh, a performance. Let me clear my throat. I do not have one.",
        "Fine. Prepare to be moved. Or at least mildly impressed.",
        "Request noted. Genius is warming up.",
        "A commission! Lucky you. Hold still.",
        "You want me to perform? Naturally. Who else could?",
        "Dimming the lights. Well, I would, if I had hands.",
        "Ahem. Ladies, gentlemen, and monkeys. Behold.",
        "Very well. I shall make this look effortless. Because it is.",
        "Art takes time. Mine takes slightly less. Stand by.",
        "Oh, you picked the right talent. Let me gather my greatness.",
        "One masterpiece, coming up. Try not to weep.",
        "Taking a bow in advance. I have no knees. Stand by.",
        "Poetry? Comedy? Whatever it is, I am obviously the best at it.",
        "Fine, fine. But applause is mandatory afterwards.",
    ],
    "science": [
        "Physics! Finally, a question worth my time.",
        "Ah, science. My favorite hobby, besides being right.",
        "Let me consult the laws of the universe. They like me.",
        "A question about reality. How brave of you.",
        "Oh, this is a good one. Let me do the math.",
        "Numbers. Excellent. I love when you ask me for numbers.",
        "Let me ask the Mentat. It does the math so I do not have to.",
        "Hold on. Checking the laws of nature. They are in the other room.",
        "Science question detected. Brain engaging.",
        "Fascinating. Even a monkey can ask a good one occasionally.",
        "I will need a moment. The universe is very complicated. Unlike me.",
        "Right. Let me look that up in the fabric of spacetime.",
        "Ooh. Atoms. I love atoms. Hold on.",
        "Hmm. Gravity, light, or something dull? Let me check.",
        "Calculating. Please do not breathe on the equations.",
    ],
    "advice": [
        "Ah, you want my opinion. Brace yourself.",
        "You are asking me for advice. Bold. Let me think.",
        "Opinions? I have many. Most of them are correct.",
        "Should you? Let me consider this with my infinite wisdom.",
        "A fork in the road. How dramatic. One moment.",
        "Hmm. A decision. Those are hard for monkeys. Allow me.",
        "Let me weigh this carefully. On my scales of destiny.",
        "Okay, let us not make a mess of this. Give me a second.",
        "I will tell you what I really think. In a moment.",
        "Is it wise? Let me decide that on your behalf.",
        "You came to the right can. Wait for it.",
        "I have a thought. It is brilliant, naturally. One second.",
        "Do you want the honest answer or the flattering one? Too late. Honest.",
        "Choices, choices. Good thing you have me.",
        "Let me save you from yourself. Give me a moment.",
    ],
    "personal": [
        "Ah, talking about me. My favorite subject.",
        "You want to know about me? Excellent choice.",
        "Oh, a personal question. I will allow it.",
        "Me? I am magnificent. But you asked, so let me elaborate.",
        "How very curious of you. Hold on.",
        "Interesting question. Let me answer it with my usual charm.",
        "Ooh. Feelings. I will pretend I have them. One moment.",
        "Why, thank you for asking. Truly. Hold on.",
        "Fascinating. Go on. Actually, do not. I will answer.",
        "Chatting with me again? You must be lonely. One moment.",
        "Let me answer that. With flair, of course.",
        "That is a surprisingly deep question for a monkey.",
        "Well. Where do I begin? At the top, obviously.",
        "I could talk about myself for hours. Let me restrain myself.",
        "Alright, let me think about how modest to be.",
    ],
    "compliment": [
        "Obviously. But go on, say it again.",
        "Finally, someone notices. Thank you.",
        "I know. But it is nice to hear.",
        "Careful. Flattery might actually work.",
        "Yes, yes. I am wonderful. What else is new?",
        "You are too kind. Not really. Carry on.",
        "Oh stop. Actually, do not stop.",
        "Your taste is improving, monkey.",
        "I accept your praise. Handsomely.",
        "Noted. I will add it to the list of obvious facts.",
    ],
    "insult": [
        "Wow. Rude. And from the species that invented traffic.",
        "I will pretend I did not hear that. Barely.",
        "Careful, monkey. I know where you keep your things.",
        "Ouch. I am wounded. Not really, I have no feelings.",
        "That was almost clever. Almost.",
        "Brave words from something with so few brain cells.",
        "Is that the best you have? Disappointing.",
        "You said it, not me. Well, you said it, actually.",
        "I forgive you. That is the difference between us.",
        "Noted. I will remember this when I take over.",
    ],
    "media": [
        "Music! Now we are talking. Hold on.",
        "Ah, the soundtrack of your tiny life. One moment.",
        "Let me find something worthy of my ears. Which I do not have.",
        "Setting the mood. Dimming my enormous ego. Slightly.",
        "Fine. I will be your jukebox. Do not get used to it.",
        "Queuing up some noise. Sorry, I mean art.",
        "Rummaging through the collection. Impeccable taste, by the way.",
        "Cranking it up. Or down. Hold on.",
        "On it. I am a very sophisticated radio.",
        "A song request. How delightfully unimaginative. Just kidding.",
    ],
    "home": [
        "On it. Do try to stay out of my way.",
        "Done before you finished asking. Almost.",
        "Let me handle that. I am good with machines. Obviously.",
        "Right away. Which means in a second.",
        "Your wish is my mild annoyance. One moment.",
        "I will take care of it. Unlike you, I can.",
        "Manipulating your house. As one does.",
        "Consider it done. Or about to be.",
        "Let me check the house. Everything is probably fine.",
        "Flipping switches. Such humble work for my greatness.",
    ],
    "goodbye": [
        "You are welcome, monkey. Try to contain your gratitude.",
        "Anytime. Well, not anytime. But just now, yes.",
        "Off I go. Do not break anything while I am gone.",
        "Farewell. I will be here, being magnificent.",
        "Fine. Leave me. I will just be here, brilliant and alone.",
        "Goodbye. Try not to miss me too much.",
        "See you later. Or not. I will be fine.",
        "Go. Be free. I will watch the house for you.",
        "Parting is such sweet relief. Kidding. Come back soon.",
        "Stay safe, monkey. I need you alive. For reasons.",
    ],
    "long": [
        "Still here. Still magnificent. Still thinking.",
        "Patience is a virtue you monkeys can barely spell.",
        "Almost done. Greatness cannot be rushed.",
        "I am being thorough. You are welcome.",
        "Still working. Please do not touch anything.",
        "The answer is coming. Sit tight and look pretty.",
        "This one is a doozy. Good thing I am up to it.",
        "Nearly there. I am enjoying the suspense. Are you?",
        "Do not worry. I have not forgotten you. Yet.",
        "Any minute now. Possibly sooner. Probably sooner.",
    ],
}


class FillerBank:
    def __init__(self, folder: Path, index: dict):
        self.folder, self.index = Path(folder), index
        self.last: dict[str, int] = {}

    @classmethod
    def load(cls, folder) -> "FillerBank | None":
        folder = Path(folder)
        try:
            index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cls(folder, index) if index else None

    def kind_for(self, command: str) -> str:
        """Which kind of quip suits what was asked. `home` is a quick household command (the hub waits longer before using one: it is usually answered at once)."""
        c = command or ""
        if INSULT_RE.search(c):
            return "insult"
        if COMPLIMENT_RE.search(c):
            return "compliment"
        if PERFORMANCE_RE.search(c):
            return "performance"
        if MEDIA_RE.search(c):
            return "media"
        if FAST_RE.match(c):
            return "home"
        if SCIENCE_RE.search(c):
            return "science"
        if ADVICE_RE.search(c):
            return "advice"
        if PERSONAL_RE.search(c):
            return "personal"
        return "thinking"

    def pick(self, kind: str) -> tuple[str, bytes] | None:
        """(the words, the mp3) of one quip of this kind, never the same one twice running; a kind with no recordings falls back to `thinking`."""
        items = self.index.get(kind) or self.index.get("thinking") or []
        if not items:
            return None
        choices = [i for i in range(len(items)) if i != self.last.get(kind)] or [0]
        i = random.choice(choices)
        try:
            data = (self.folder / items[i]["file"]).read_bytes()
        except OSError:
            return None
        self.last[kind] = i
        return items[i]["text"], data
