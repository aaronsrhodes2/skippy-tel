"""His lists by voice, as the Echo kept them: "add milk to the shopping list", "what's on my to-do list", "take eggs off the list",
"clear the shopping list". One set for the whole house, on disk (state/lists.json), so every room and every restart sees the same lists."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

MAX_ITEMS = 200
ALIASES = {"shopping": "shopping", "grocery": "shopping", "groceries": "shopping", "shopping list": "shopping", "grocery list": "shopping",
           "todo": "to-do", "to do": "to-do", "to-do": "to-do", "todos": "to-do", "to-dos": "to-do", "tasks": "to-do", "chores": "to-do"}

TOOL = {"name": "list", "description": "His lists (shopping, to-do, or any other he names: 'the Costco list', 'packing list'). add: put items on it "
                                       "('add milk and eggs to the shopping list'); remove: take items off ('I got the milk', 'cross off eggs'); "
                                       "read: what is on it; clear: empty it ('clear the shopping list'); lists: which lists he has.",
        "input_schema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["add", "remove", "read", "clear", "lists"]},
            "list": {"type": "string", "description": "which list, in his words; shopping when he just says 'the list' about groceries"},
            "items": {"type": "array", "items": {"type": "string"}, "description": "add/remove: each item on its own, e.g. ['milk', 'eggs']"}},
            "required": ["action"]}}


def list_name(words: str) -> str:
    w = re.sub(r"\b(my|the|our|list)\b", " ", (words or "shopping").lower())
    w = " ".join(re.findall(r"[a-z0-9'-]+", w)) or "shopping"
    return ALIASES.get(w, w)


_LIST = r"(?:my |the |our )?(?P<list>[\w' -]{0,30}?)\s*list"
_ROUTES = [
    ("add", re.compile(rf"^(?:please |can you |could you )?(?:add|put|stick|throw)\s+(?P<items>.+?)\s+(?:to|on|onto|in)\s+{_LIST}\b")),
    ("remove", re.compile(rf"^(?:please |can you |could you )?(?:take|cross|scratch|strike)\s+(?P<items>.+?)\s+off\s+(?:of\s+)?{_LIST}\b")),
    ("remove", re.compile(rf"^(?:please |can you |could you )?(?:remove|delete)\s+(?P<items>.+?)\s+from\s+{_LIST}\b")),
    ("read", re.compile(rf"^(?:what'?s|what is|what are|read(?: me)?|tell me(?: what'?s)?)\s+(?:on|in)?\s*{_LIST}\b")),
    ("read", re.compile(rf"^(?:read|check)\s+(?:me\s+)?{_LIST}\b")),
    ("clear", re.compile(rf"^(?:clear|empty|wipe|erase)\s+(?:out\s+)?{_LIST}\b")),
]


def pre_route(heard: str) -> dict | None:
    """The `list` tool's arguments for a plain list request (Core skipped the tool and said "Adding milk ..." anyway, 2026-11-01); None otherwise."""
    w = re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", (heard or "").lower()).strip(" ?!.")
    for action, rx in _ROUTES:
        m = rx.match(w)
        if m:
            args = {"action": action, "list": list_name(m.group("list") or "shopping")}
            if "items" in rx.groupindex:
                args["items"] = [i.strip() for i in re.split(r",|\band\b", m.group("items")) if i.strip()]
            return args
    return None


def say_items(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


class Lists:
    def __init__(self, path: Path | str | None = None, log=print):
        self.path = Path(path) if path else None
        self.log = log
        self._lock = threading.Lock()              # every room's brain thread shares this one
        self.lists: dict[str, list[str]] = {}
        if self.path is not None and self.path.is_file():
            try:
                self.lists = {k: list(v) for k, v in json.loads(self.path.read_text(encoding="utf-8")).items()}
            except (OSError, ValueError, AttributeError) as e:
                self.log(f"(lists: {self.path} unreadable, starting empty: {e})")

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.lists, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            self.log(f"(lists: could not save: {e})")

    @staticmethod
    def _same(a: str, b: str) -> bool:
        a, b = (re.sub(r"^(?:the|some|a|an|more)\s+", "", x.lower().strip()) for x in (a, b))
        return a == b or a.rstrip("s") == b.rstrip("s") or (len(b) > 2 and b in a)

    def run(self, name: str, args: dict) -> str:
        args = args or {}
        action = str(args.get("action") or "read")
        which = list_name(str(args.get("list") or ""))
        raw = args.get("items") or []
        if isinstance(raw, str):                       # "milk, eggs and coffee" (a tool call the model wrote as text)
            raw = re.split(r",|\band\b", raw)
        items = [str(i).strip()[:80] for i in raw if str(i).strip()]
        with self._lock:
            have = self.lists.get(which, [])
            if action == "lists":
                names = [k for k, v in self.lists.items() if v]
                return ("His lists: " + "; ".join(f"{k} ({len(self.lists[k])})" for k in names) + ".") if names else "All his lists are empty."
            if action == "read":
                return f"The {which} list is empty." if not have else f"On the {which} list ({len(have)}): {say_items(have)}."
            if action == "clear":
                n = len(have)
                self.lists.pop(which, None)
                self._save()
                return f"Cleared the {which} list ({n} item{'s' if n != 1 else ''})."
            if not items:
                return f"Not done: which items, for the {which} list?"
            if action == "add":
                new = [i for i in items if not any(self._same(h, i) for h in have)]
                if len(have) + len(new) > MAX_ITEMS:
                    return f"Not done: the {which} list is full."
                self.lists[which] = have + new
                self._save()
                dup = [i for i in items if i not in new]
                return (f"Added {say_items(new)} to the {which} list." if new else "") + (f" {say_items(dup)} {'was' if len(dup) == 1 else 'were'} already on it." if dup else "")
            if action == "remove":
                gone = [h for h in have if any(self._same(h, i) for i in items)]
                missing = [i for i in items if not any(self._same(h, i) for h in have)]
                self.lists[which] = [h for h in have if h not in gone]
                self._save()
                return ((f"Took {say_items(gone)} off the {which} list." if gone else "")
                        + (f" {say_items(missing)} {'was' if len(missing) == 1 else 'were'} not on it." if missing else "")).strip()
        return "I cannot do that to a list."

    def tools(self):
        return [TOOL], self.run
