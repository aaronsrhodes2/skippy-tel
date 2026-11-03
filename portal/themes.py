"""Each control's own theme (the Captain, 2026-11-02: "change each control's theme, like a single color on black, or some fancy one",
without asking an agent each time). The Captain picks it on the control itself: a black, invisible button in a deck tile's cut-off
corner, or a long press on a key (static/keys.js); it shows for everyone.

A theme is one of the palette's colours on black (CLAUDE.md's colour doctrine) or a named fancy one (bridge.css: psychedelic, stripes,
glow). Stored as live data like the retired list (portal/modules.py): the games VM's metadata `portal-themes`, a JSON map of a
control's telemetry id to its theme, so a change needs no build. A failed read shows every control in its own colours."""
from __future__ import annotations

import json
import re
import time

import modules as modules_mod

KEY = "portal-themes"
# in the picker's order (a spectrum, then white); the Captain, 2026-11-02: a brighter yellow for the first one, a blue with less green,
# and a purple beside the violet
COLOURS = {"red": "#FF4444", "orange": "#FF9900", "yellow": "#FFE600", "green": "#00FF88", "blue": "#2E8BFF", "violet": "#818CF8",
           "purple": "#B040FF", "pink": "#FF4FD8", "white": "#FFFFFF"}
FANCY = ("psychedelic", "stripes", "glow")
THEMES = tuple(COLOURS) + FANCY
CTL_ID = re.compile(r"[a-z0-9:_.\-]{1,60}")
MAX = 400


class Themes:
    def __init__(self, vm):
        self.vm = vm
        self._cache: tuple[float, dict] | None = None

    def all(self) -> dict:
        """{telemetry id: theme} (cached a few seconds: every page asks)."""
        if self._cache and time.monotonic() - self._cache[0] < modules_mod.CACHE_SECONDS:
            return self._cache[1]
        try:
            got = self._fresh()
        except modules_mod.Unavailable:
            got = {}
        self._cache = (time.monotonic(), got)
        return got

    def _fresh(self) -> dict:
        try:
            raw = self.vm.get_metadata(KEY)
        except Exception as exc:   # noqa: BLE001
            raise modules_mod.Unavailable("couldn't read the controls' themes") from exc
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            raise modules_mod.Unavailable("the controls' themes are unreadable") from None
        return {k: v for k, v in data.items() if CTL_ID.fullmatch(str(k)) and v in THEMES} if isinstance(data, dict) else {}

    def set(self, ctl_id: str, theme: str | None) -> dict:
        """Give a control a theme; None or "default" puts it back to its own. Returns the whole map."""
        if not CTL_ID.fullmatch(ctl_id or "") or (theme not in THEMES and theme not in (None, "", "default")):
            raise ValueError("unknown control or theme")
        got = self._fresh()
        if theme in THEMES:
            got[ctl_id] = theme
        else:
            got.pop(ctl_id, None)
        if len(got) > MAX:
            raise ValueError("too many themed controls")
        try:
            self.vm.set_metadata(KEY, json.dumps(got, separators=(",", ":"), sort_keys=True))
        except Exception as exc:   # noqa: BLE001
            raise modules_mod.Unavailable("couldn't save the controls' themes") from exc
        self._cache = None
        return got
