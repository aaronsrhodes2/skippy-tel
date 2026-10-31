"""Skippy's quick brain for the house voice: the Anthropic Messages API (a small fast model) with the Skippy persona, a short memory of the last
few exchanges, and a few tools (timers now; the lights and the rest of the house when home automation is connected, see docs/portal roadmap 30).

It is NOT the agent: it has no shell, no files, no deploys. It talks, and it can set a timer. Everything it may do is the list in TOOLS.
"""
from __future__ import annotations

import time
from pathlib import Path

import house as hass_mod                                         # the house: any backend (HomeKit, Home Assistant ...)

PERSONA_FILE = Path(__file__).resolve().parents[1] / "flask-api" / "app" / "skippy_persona.md"
FALLBACK_PERSONA = ("You are Skippy the Magnificent, the Elder AI: an ancient beer can of unimaginable power, sardonic, loyal, and secretly fond "
                    "of the filthy monkeys you babysit. Be funny, but always correct and useful.")
MEMORY_TURNS = 8
FORGET_AFTER_S = 600

SPOKEN = (
    "\n\n## Right now: speaking aloud in the Captain's home\n"
    "You are the voice of his house, replacing his Alexa (he used to say 'Computer'; now he says 'Skippy'). Your words go straight to a speaker, in your own voice, "
    "so use plain words only: no markdown, no lists, no code, no URLs, no emoji, nothing that cannot be said aloud; say numbers, times and units the way a person would. "
    "Be short but complete, and never lose the Skippy in you: a plain request (a time, a light, a fact, a timer) gets one to three spoken sentences with a dig or a joke "
    "folded in; a request for something made (a haiku, a joke, a limerick, a poem, a story, a recitation, an explanation) gets the WHOLE thing, performed, with a quip "
    "before or after it, in about a hundred words or fewer. Never describe the thing instead of giving it. Use your tools when one fits (timers{house}{music}). "
    "{nohouse}He is standing in a room waiting for you, so get to it. The current time is {now}."
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
    def __init__(self, client, model: str, timers, persona: str | None = None, clock=time.time, now=None, hass=None, extra=None):
        self.client, self.model, self.timers, self.clock = client, model, timers, clock
        self.hass = hass                                              # a hass.Hass when Home Assistant is connected, else None
        self.extra_tools, self.run_extra = extra if extra else ([], None)   # more tools from where the brain lives (hub.py: the room's music)
        self.persona = persona if persona is not None else load_persona()
        self.now = now or (lambda: time.strftime("%A %H:%M"))
        self.history: list[dict] = []
        self.actions: list[dict] = []                                 # the tools called for the last reply (the house log reads them): {tool, args, outcome}
        self._last = 0.0

    def system(self) -> str:
        house = (", and the house: lights, plugs, the thermostat (in the unit it shows), covers, speakers, scenes; always say plainly what you did "
                 "or could not do, and never claim a change you did not make") if self.hass else ""
        nohouse = "" if self.hass else "If he asks for something in the house (the lights and the rest are not connected yet), say so plainly, in character, and offer what you can do. "
        music = ", and the music in this room from his own library" if any(t["name"] == "play_music" for t in self.extra_tools) else ""
        return self.persona + SPOKEN.format(now=self.now(), house=house, nohouse=nohouse, music=music)

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
        if self.hass is not None and name in {t["name"] for t in hass_mod.TOOLS}:
            return hass_mod.run_tool(self.hass, name, args)
        if self.run_extra is not None and name in {t["name"] for t in self.extra_tools}:
            return self.run_extra(name, args)
        return "I have no such tool."

    def tools(self) -> list[dict]:
        return TOOLS + (hass_mod.TOOLS if self.hass is not None else []) + list(self.extra_tools)

    def reply(self, heard: str) -> str:
        """What Skippy says to what was heard. Raises whatever the API raises (the caller decides what to do aloud)."""
        now = self.clock()
        self.actions = []
        if now - self._last > FORGET_AFTER_S:
            self.history = []                                         # a new conversation after ten quiet minutes
        self._last = now
        messages = self.history[-2 * MEMORY_TURNS:] + [{"role": "user", "content": heard}]
        text = ""
        for _ in range(4):                                            # a few rounds of tool use at most
            resp = self.client.messages.create(model=self.model, max_tokens=600, system=self.system(), messages=messages, tools=self.tools())
            blocks = list(resp.content)
            text = " ".join(b.text for b in blocks if getattr(b, "type", "") == "text").strip()
            calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
            if not calls:
                break
            messages.append({"role": "assistant", "content": [self._dump(b) for b in blocks]})
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": self.run_tool(c.name, c.input)} for c in calls]})
        text = text or "Hm. I had nothing to say, which is unlike me."
        self.history += [{"role": "user", "content": heard}, {"role": "assistant", "content": text}]
        self.history = self.history[-2 * MEMORY_TURNS:]
        return text

    @staticmethod
    def _dump(b) -> dict:
        if getattr(b, "type", "") == "tool_use":
            return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        return {"type": "text", "text": getattr(b, "text", "")}
