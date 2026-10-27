"""The portal's modules: every control on the launcher, and the Graveyard where the Captain retires the ones nobody uses.

Each launcher control is a module: an id, a glyph (templates/_glyphs.html), the page it opens and a name for a screen reader
(the launcher itself shows no words). Who may use a module is decided elsewhere (grants in auth.py); this decides only
whether the module is on the launcher at all. An admin retires a module in the Admin app's Graveyard and revives it there;
a retired module's control leaves the launcher, and its page still answers at its own address (nothing is deleted).

The retired list is stored in the games VM's instance metadata (`portal-modules`, a JSON list), next to the friends list
(portal/friends.py), with the same rules: no database, no deploy to change it, Terraform ignores the key
(infra/gcp-games/main.tf), a failed read shows nothing retired (every control stays) and a failed write is reported, never
half done. Admin cannot be retired: the Graveyard lives in it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

KEY = "portal-modules"
CACHE_SECONDS = 10


class Unavailable(RuntimeError):
    """The VM's metadata couldn't be read or written, so the retired list was left alone."""


@dataclass(frozen=True)
class Module:
    id: str                 # also the tile's telemetry id (tile:<id>) and its colour (base.html: [data-app])
    glyph: str              # templates/_glyphs.html
    endpoint: str           # url_for(...) of the page it opens
    label: str              # for a screen reader and the Graveyard; never printed on the launcher
    retirable: bool = True
    pane: bool = False      # a pane of the Captain's /me dashboard, not a launcher tile: the Graveyard retires it, /me hides it


MODULES = (
    Module("skippy", "can", "chat.index", "Skippy"),
    Module("games", "console", "dashboard.index", "Game servers"),
    Module("play", "ship", "play_index", "Browser games"),
    Module("music", "wave", "music.player_page", "Bilby's Audio Service"),
    Module("video", "lens", "video.video_page", "Nagatha's Video Service"),
    Module("experiments", "atom", "experiments.index", "Experiments"),
    Module("tabletop", "d20", "tabletop.page", "Tabletop"),
    Module("dashboard", "gauge", "me.index", "Dashboard"),
    Module("admin", "gear", "admin.index", "Admin", retirable=False),
    # /me panes (id = "me-" + the pane's id in templates/me.html). MCP and Home are what /me is for; the rest wait here to be retired when
    # their telemetry (pane:<id> clicks, in the Home pane's "Least-used UI") says nobody opens them.
    Module("me-server", "server", "me.index", "Dashboard: Server", pane=True),
    Module("me-core", "core", "me.index", "Dashboard: Core", pane=True),
    Module("me-activity-pane", "eye", "me.index", "Dashboard: Activity", pane=True),
    Module("me-watcher", "gauge", "me.index", "Dashboard: Watcher", pane=True),
    Module("me-people", "user", "me.index", "Dashboard: People", pane=True),
    Module("me-weather", "globe", "me.index", "Dashboard: Weather and traffic", pane=True),
    Module("me-links", "globe", "me.index", "Dashboard: Links", pane=True),
    # Skippy's chat keys (id = "key-" + the key's name; their taps are chat:<name>, in "Least-used UI"). Send, the mode, the voice
    # and Your chats are the chat itself and are never listed; these can be retired when nobody presses them, and revived here.
    Module("key-apps", "apps", "chat.index", "Chat key: Apps", pane=True),
    Module("key-new", "new", "chat.index", "Chat key: New chat", pane=True),
    Module("key-attach", "clip", "chat.index", "Chat key: Attach", pane=True),
    Module("key-tools", "tools", "chat.index", "Chat key: Tools", pane=True),
    Module("key-copy", "sheet", "chat.index", "Chat key: Copy chat (agent)", pane=True),
    Module("key-notify", "chats", "chat.index", "Chat key: Discord me when done (agent)", pane=True),
)
CHAT_KEYS = tuple(m.id[4:] for m in MODULES if m.id.startswith("key-"))
BY_ID = {m.id: m for m in MODULES}


class Modules:
    def __init__(self, vm):
        self.vm = vm
        self._cache: tuple[float, frozenset] | None = None

    def retired(self) -> frozenset:
        """Ids that are retired right now (cached a few seconds: every launcher load asks)."""
        if self._cache and time.monotonic() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        try:
            ids = self._parse(self.vm.get_metadata(KEY))
        except Exception:   # noqa: BLE001 — VM API down or the list unreadable: nothing is retired, every control stays
            return self._cache[1] if self._cache else frozenset()
        self._cache = (time.monotonic(), ids)
        return ids

    @staticmethod
    def _parse(raw) -> frozenset:
        data = json.loads(raw or "[]")
        if not isinstance(data, list):
            raise ValueError("not a list")
        return frozenset(i for i in data if isinstance(i, str) and i in BY_ID and BY_ID[i].retirable)

    def _fresh(self) -> frozenset:
        """What is stored right now, for a read-modify-write. A failed read raises Unavailable: it must never look like
        "nothing is retired", or the save that follows would write back only the one module changed."""
        try:
            raw = self.vm.get_metadata(KEY)
        except Exception as exc:   # noqa: BLE001
            raise Unavailable("couldn't read the retired modules") from exc
        try:
            return self._parse(raw)
        except (ValueError, TypeError):
            raise Unavailable("the stored list of retired modules is unreadable") from None   # never overwrite what we can't read

    def _save(self, ids: frozenset) -> None:
        try:
            self.vm.set_metadata(KEY, json.dumps(sorted(ids)))
        except Exception as exc:   # noqa: BLE001
            raise Unavailable("couldn't save the retired modules") from exc
        self._cache = None

    def retire(self, module_id: str) -> None:
        if module_id not in BY_ID or not BY_ID[module_id].retirable:
            raise KeyError(module_id)
        self._save(self._fresh() | {module_id})

    def revive(self, module_id: str) -> None:
        if module_id not in BY_ID:
            raise KeyError(module_id)
        self._save(self._fresh() - {module_id})
