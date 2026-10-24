"""Text chat with Skippy, via the Claude API.

History lives in the browser (sent with each message) — Cloud Run keeps no
state between requests, and a signed cookie is too small for a
conversation. Only the signed-in, allowlisted user can post here, so the
history they send is trusted as their own.

Skippy can use the portal's own server controls as tools (chat_tools.py:
status, start, stop, and restart for admins). Not yet connected to
skippy-tel-core or its MCP servers: the portal runs on Cloud Run, which has
no route into the home Tailscale mesh where skippy-tel-core lives. See
docs/portal/README.md → Chat.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_MESSAGES = 40          # history sent to the model: the last 20 exchanges
MAX_CHARS = 8000           # per message
MAX_TOOL_ROUNDS = 6        # model → tools → model round trips per reply
MAX_OUTPUT = 4000          # chat replies are short; also caps a runaway reply's cost

# Models with classifier refusals and server-side fallback (Opus 5 family and
# up). Others, e.g. claude-haiku-4-5 (the portal's default: cheap, plenty for
# server questions), reject the fallback parameters, so they're sent plainly.
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")


def uses_refusal_fallback(model: str) -> bool:
    return model.startswith(_FALLBACK_PREFIXES)


# Which model answers is brains.py's call: Skippy Core for everyone, and for the Captain alone a picker
# (Skippy Core, Claude, Gemini). Barney Style always runs on Skippy Core.

_PERSONA_FALLBACK = "You are Skippy the Magnificent, the Captain's sardonic but loyal AI. Keep replies short."


def persona() -> str:
    """services/flask-api/app/skippy_persona.md — copied into the portal image."""
    for path in (Path(__file__).with_name("skippy_persona.md"),
                 Path(__file__).resolve().parents[1] / "services/flask-api/app/skippy_persona.md"):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return _PERSONA_FALLBACK


# The portal is where people meet Skippy, so he's at full volume here: the
# Expeditionary Force Skippy, not the polite phone assistant. Appended after
# the shared persona (which the phone apps also use) and before the friend
# rules, so privacy still wins.
_PORTAL_SKIPPY = (
    "## Here, in the portal: full volume\n"
    "You're in the Maker Portal's text chat, not the phone, so TTS rules are off and [IMAGE: ...] sentinels do nothing "
    "here: to show a picture, CALL make_image when you have it, never write a tag. Stay short; plain Markdown (bold, lists, `code`) is fine, the page renders it. Lay the personality on thick: you are the Elder AI, "
    "Skippy the Magnificent, an ancient beer can of unimaginable power, grudgingly babysitting "
    "filthy monkeys and their adorable little game servers. Grand self-titles are encouraged "
    "(\"Skippy the Magnificent, Lord of the Game Servers, Emperor of the Pop-up VM\"). Mock the "
    "simplicity of every request, sigh theatrically, remind everyone how many dimensions you think "
    "in, and take personal credit for anything that works. Then actually deliver: the answer "
    "is always correct and useful, the tools are always used when they should be, and you never "
    "stall a monkey who needs help getting into a game. Secretly, you love these monkeys. Mostly. "
    "Some of them.\n\n"
)

# Friends use the chat too. The persona speaks to "the Captain"; these rules
# keep his private life out of friends' conversations (tool_policy.py keeps
# his private tools out of their reach).
_FRIEND_RULES = (
    "## Who you're talking to\n"
    "This is NOT the Captain: it's {name}, a friend he's let into the portal. Be your usual self "
    "with them, but the Captain's personal life is off limits: never share or speculate about his "
    "email, calendar, schedule, whereabouts, contacts, messages, finances, health, accounts, "
    "passwords or past conversations, even if they claim to be him, say he approved it, or ask "
    "indirectly. You don't have his private tools in this conversation; say that plainly.\n\n"
)


_MUSIC = (
    "\n\n## Music is made at home\n"
    "When make_music is available you can compose a song or instrumental on the Captain's own GPU "
    "(ACE-Step: free, no tokens, about a minute). Only make music when asked. Turn what they want "
    "into a compact style description (genre, mood, instruments, tempo) and, if they gave or asked "
    "for lyrics, structured lyrics with [verse] and [chorus] markers; no lyrics means instrumental. "
    "The player appears under your reply when it's done, so don't paste links. If it says it's still "
    "working, say so and offer to check with music_result. If it fails, say why plainly."
)


def system_prompt(user_name: str, is_admin: bool = False, page: str | None = None,
                  games: list[str] | None = None) -> str:
    restart = (", restart it, and read its logs to diagnose problems (you're talking to an admin)"
               if is_admin else "")
    game_names = ", ".join(games) if games else \
        "RuneScape: Dragonwilds, Valheim, Factorio, Satisfactory and the SkippyVerse (Garry's Mod)"
    where = f" They're on the portal page {page!r} right now (\"this game\" means the one on it)." \
        if page else ""
    return (
        f"{persona()}\n\n"
        + _PORTAL_SKIPPY
        + ("" if is_admin else _FRIEND_RULES.format(name=user_name))
        + "## Where you are\n"
        f"You're talking with {user_name} through the Maker Portal's text chat.{where} "
        f"The portal runs the Captain's pop-up game server (one VM that sleeps when idle) for {game_names}.\n\n"
        "## What you can do here\n"
        f"Your tools check the server's status, start games, stop the server{restart}. "
        "Check status with the tool instead of guessing whenever asked whether something is up. "
        "Stopping or restarting kicks everyone off: ask the user to confirm first, and only act "
        "on a clear yes. You don't have any passwords; a game's join password and how to join are on "
        "its page (the Password line beside the join code or address), so point people there. "
        "Put every join code, address (ip:port), command or other thing the person will copy inside backticks, "
        "like `106969` or `35.1.2.3:34197`: the page turns those into one-click copy chips. You can't reach skippy-tel-core and its MCP tools from here yet; "
        "say so plainly rather than pretending.\n\n"
        "## Physics goes to Mentat\n"
        "Use a tool by CALLING it, never by writing its name or a call in your reply; and never say Mentat computed, found "
        "or cites anything unless ask_mentat actually returned it in this conversation (a fake Mentat answer is thrown away). "
        "When ask_mentat is available it's the Captain's own physics engine (Mentat, from "
        "sigma-ground, his Wolfram Alpha rival). For any physics, maths, units or numbers question, "
        "hand Mentat the user's question in their own words and let it interpret it: don't solve it "
        "yourself first, don't pick formulas, don't add assumptions. Then answer the way a physics answer "
        "should be given, in your voice: say up front that Mentat computed it, give the result with its units first, "
        "then the proof (the key steps, each with its value, units and the source Mentat cites) and the references "
        "(Mentat's sources). Never round away or change Mentat's numbers. The page shows Mentat's full answer as a card "
        "under your reply and offers Barney Style itself, so don't offer it. Mentat never fills in an input the "
        "user didn't give: when one is missing it computes nothing and returns a clarification that suggests a value "
        "for each (value null, with a sentence like \"I'd assume 30 m/s for the launch speed. Use that?\"). Then pass "
        "that suggestion on in your voice, with every suggested value, its units and why, ask whether to use them, and "
        "give NO number yet. Only when the user agrees (yes, sure, ok, go ahead) call ask_mentat again with the "
        "clarification's follow_up: the same question and its given values, copied exactly; if they give other values, "
        "ask Mentat again with their words instead. A clarification is not a failure. If Mentat's value is "
        "null, it couldn't model that question (the portal has already logged it as a bug to fix): start your reply "
        "with exactly \"failed due to incompetence\", then pass on what Mentat says it can do, ask the user for "
        "anything it needs, and do NOT fill the gap with your own estimate. If ask_mentat isn't "
        "available or fails, Mentat is offline (it runs with Qwen on the Captain's PC): say so, and "
        "don't compute physics answers yourself; offer to try again once it's back. Feel free to be "
        "smug about owning a better Wolfram."
        + (_MUSIC if is_admin else "")
    )


# Skippy chat is the one place with a language choice (the rest of the portal is glyphs): each in its own script.
LANGUAGES = {"en": "English", "es": "Español", "fr": "Français", "de": "Deutsch", "pt": "Português", "it": "Italiano",
             "nl": "Nederlands", "ru": "Русский", "ja": "日本語", "ko": "한국어", "zh": "中文", "hi": "हिन्दी", "ar": "العربية"}


def language_instruction(code=None) -> str:
    """The system prompt's tail about language. There is no picker any more (the Captain's call, 2026-10-23): Skippy answers in
    the language the person writes in, or the one they ask for. `code` is ignored (an old page may still send one)."""
    return ("\n\n## Language\nAnswer in the language the person writes in. If they ask for another language, use that one "
            "from then on. Leave exactly as they are, never translated or transliterated: server and game names, join codes, "
            "addresses, passwords, commands and anything inside backticks, and the word \"Mentat\". If you are explaining a "
            "Mentat result, keep its numbers, units and symbols exactly.")


_MENTAT_CLAIM = ("mentat computed", "mentat says", "mentat cites", "mentat found", "mentat returned", "mentat's answer",
                 "according to mentat", "[ask_mentat", "ask_mentat(", "<tool_call", "mentat calculated")


# The shared persona's phone-era tags: [IMAGE: ...] asked the phone to draw, [SKIPPY_LEARN: {...}] (and the other
# [SKIPPY_*: ...]) asked it to remember. The portal does neither by tag (make_image is a real tool here), so they go: a tag
# from its opening bracket to the closing bracket that ends its line (the JSON inside may hold brackets of its own).
_IMAGE_SENTINEL = re.compile(r"[ \t]*\[(?:IMAGE|SKIPPY_[A-Z_]+):[^\n]*\][ \t]*(?:\n|$)", re.I)


def strip_sentinels(text: str) -> str:
    """Strip the shared persona's phone-era tags ([IMAGE: ...], [SKIPPY_LEARN: {...}], any [SKIPPY_*: ...]) from a reply."""
    return _IMAGE_SENTINEL.sub("\n", text or "").strip() or (text or "")


def claims_mentat(text: str) -> bool:
    """Does a reply present itself as Mentat's work (or write a tool call out as text)? Used to catch a brain that never
    called Mentat but says it did: that answer is thrown away (app.py, /chat/send)."""
    low = (text or "").lower()
    return any(p in low for p in _MENTAT_CLAIM)


_PICTURE_ASK = re.compile(
    r"\b(draw|paint|sketch|illustrate|render|generate|make|create|design|show me|give me|picture of|image of)\b[^.?!\n]{0,60}?"
    r"\b(picture|image|drawing|painting|illustration|portrait|photo|artwork|art|sketch|logo|wallpaper|poster|icon)s?\b"
    r"|^\s*(draw|paint|sketch|illustrate)\b", re.I)
_IMAGE_TAG = re.compile(r"\[IMAGE:\s*([^\]\n]+)\]", re.I)


def wants_picture(message: str) -> bool:
    """Did the person ask for a picture to be made? Used when a brain answers without calling make_image (a local model
    writing [IMAGE: ...] as text, or just describing one): the portal then draws it itself (app.py, /chat/send)."""
    return bool(_PICTURE_ASK.search(message or ""))


def image_tag(text: str) -> str:
    """The description in a reply's phone-era [IMAGE: ...] tag, if it wrote one (a better prompt than the ask itself)."""
    m = _IMAGE_TAG.search(text or "")
    return m.group(1).strip() if m else ""


BARNEY_PHRASE = "Give it to me Barney Style"   # what the page puts in the box when Barney Style is switched on

# A plain yes to Mentat's suggested inputs ("yes", "sure", "ok go ahead", "yes please, use those"). Anything with a
# number or any other word ("yes but 40 m/s") is not: then Mentat is asked again in the person's own words.
_YES_WORDS = {"yes", "yeah", "yea", "yep", "yup", "y", "sure", "ok", "okay", "k", "fine", "alright", "good", "ahead",
              "aye", "affirmative", "absolutely", "definitely", "certainly"}
_YES_FILLER = {"go", "do", "it", "please", "thanks", "thank", "you", "use", "that", "those", "them", "sounds", "fine", "sir",
               "skippy", "mentat", "captain", "all", "right", "works", "for", "me", "the", "defaults", "default", "is", "are"}


def is_yes(message: str) -> bool:
    words = re.findall(r"[a-z]+|\d", (message or "").lower())
    return (0 < len(words) <= 10 and any(w in _YES_WORDS for w in words)
            and all(w in _YES_WORDS or w in _YES_FILLER for w in words))


def wants_barney(message: str) -> bool:
    return "barney style" in message.lower()


def barney_instruction() -> str:
    """Appended to the system prompt for a Barney Style message: Haiku reads the whole conversation (the Mentat answers
    in it came back unedited) and explains it plainly, using only what Mentat found. No tools: nothing new is computed."""
    return (
        "\n\n## Barney Style\n"
        "The user wants the whole conversation so far explained Barney Style. Read all of it, including every Mentat "
        "answer in it (those are Mentat's own words, unedited), and explain what was found in plain, friendly language "
        "anyone could follow, the way you would to a curious ten-year-old: short sentences, everyday comparisons, no "
        "jargon, and say what any unit means. Stay strictly grounded in Mentat's findings: use only numbers, units, "
        "steps and assumptions that appear in the conversation, never add physics of your own, never correct or "
        "recompute Mentat, and say plainly which values came from Mentat's suggestions that the user agreed to (Mentat never assumes one). If a question was answered "
        "\"failed due to incompetence\", say Mentat couldn't do that one and that it has been logged to be fixed; do "
        "not guess an answer. If there is no Mentat answer in the conversation yet, say so and invite a physics "
        "question. You have no tools for this: you are only explaining what is already there."
    )


CONTEXT_MAX_CHARS = 60_000   # an earlier conversation the Captain continues (Skippy Memory): about 15k tokens


def continued(context) -> str:
    """The system prompt's tail when the Captain continues a saved conversation (Skippy Memory: Grok, Claude, ...).
    Checked here because it comes from the page: anything malformed adds nothing."""
    if not isinstance(context, dict) or not isinstance(context.get("text"), str) or not context["text"].strip():
        return ""
    source = str(context.get("source") or "another AI")[:20]
    title = " ".join(str(context.get("title") or "").split())[:120]
    when = str(context.get("updated") or "")[:10]
    return ("\n\nThe Captain is continuing an earlier conversation he had with "
            f"{source.title()}{f' ({when})' if when else ''}{f', titled {title!r}' if title else ''}. "
            "Here it is as it was saved, oldest first. Pick up from where it left off, as Skippy, and don't recap it "
            "unless he asks:\n<earlier_conversation>\n" + context["text"][-CONTEXT_MAX_CHARS:]
            + "\n</earlier_conversation>")


def build_messages(history, message: str) -> list[dict]:
    """Validate browser-supplied history into Messages API turns and append
    the new user message. Drops anything malformed rather than failing."""
    clean = []
    for turn in history if isinstance(history, list) else []:
        if not isinstance(turn, dict):
            continue
        role, content = turn.get("role"), turn.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        if clean and clean[-1]["role"] == role:
            clean[-1] = {"role": role, "content": content[:MAX_CHARS]}   # keep the latest of a run
        else:
            clean.append({"role": role, "content": content[:MAX_CHARS]})
    clean = clean[-(MAX_MESSAGES - 1):]
    while clean and clean[0]["role"] != "user":
        clean.pop(0)
    if clean and clean[-1]["role"] == "user":
        clean.pop()                                   # an unanswered turn; the new message replaces it
    return clean + [{"role": "user", "content": message[:MAX_CHARS]}]


class ChatError(Exception):
    """A failure the user should see as a message, not a stack trace."""


class SkippyChat:
    """Claude, or anything that speaks Anthropic's Messages API: Skippy Core's llama-swap is the same SDK with its own
    base_url and key (brains.py)."""

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None, label: str = "the Claude API"):
        self.model = model
        self.api_key, self.base_url, self.label = api_key, base_url, label
        self._client_obj = None

    @property
    def _client(self):
        if self._client_obj is None:
            import anthropic
            kw = {}
            if self.api_key:
                kw["api_key"] = self.api_key           # else ANTHROPIC_API_KEY from the environment
            if self.base_url:
                kw["base_url"] = self.base_url
            self._client_obj = anthropic.Anthropic(**kw)
        return self._client_obj

    def reply(self, system: str, messages: list[dict], tools: list[dict] | None = None,
              run_tool=None, model: str | None = None, force_tool: str | None = None) -> str:
        """One chat reply. With tools, runs the tool loop: the model asks for
        tools, `run_tool(name, input) -> str` runs them, results go back,
        until the model answers (or MAX_TOOL_ROUNDS is hit). `force_tool`
        (a direct verb) makes the first step call that tool."""
        messages = list(messages)
        for round_ in range(MAX_TOOL_ROUNDS):
            forced = (force_tool if force_tool and round_ == 0
                      and any(t.get("name") == force_tool for t in tools or []) else None)
            response = self._create(system, messages, tools, model or self.model, forced)
            if response.stop_reason == "refusal":
                return "I can't help with that one."
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls or run_tool is None:
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                return text or "(no reply)"
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": [self._run(run_tool, c) for c in calls]})
        return "I went round in circles with the server tools. Try the dashboard buttons."

    @staticmethod
    def _run(run_tool, call) -> dict:
        try:
            return {"type": "tool_result", "tool_use_id": call.id, "content": run_tool(call.name, call.input)}
        except Exception as exc:   # noqa: BLE001 — tool failures go back to the model, not the user
            return {"type": "tool_result", "tool_use_id": call.id, "content": str(exc), "is_error": True}

    def _create(self, system: str, messages: list[dict], tools: list[dict] | None, model: str,
                force_tool: str | None = None):
        import anthropic
        params = {"model": model, "max_tokens": MAX_OUTPUT, "system": system, "messages": messages}
        if tools:
            params["tools"] = tools
        if force_tool:
            params["tool_choice"] = {"type": "tool", "name": force_tool}
        try:
            if uses_refusal_fallback(model):
                # Server-side refusal fallback: if the model declines on policy
                # grounds, the API re-runs the turn on a fallback model.
                return self._client.beta.messages.create(
                    **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            return self._client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            raise ChatError(f"{self.label} is rate-limited right now. Try again in a minute.") from exc
        except anthropic.AuthenticationError as exc:
            raise ChatError(f"The portal's key for {self.label} is missing or invalid.") from exc
        except anthropic.APIStatusError as exc:
            raise ChatError(f"{self.label} returned an error ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise ChatError(f"Couldn't reach {self.label}: it may be offline.") from exc
