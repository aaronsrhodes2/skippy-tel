"""Browser Games: the games you play in a browser tab, nothing to install (the `/play` app).

Game Servers (`/games`) are the dedicated servers on the games VM; these are pages. A game with
no `endpoint` or `url` yet is shown as "coming soon": a card, no link to nowhere.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserGame:
    slug: str
    title: str
    blurb: str
    icon: str
    source: str                   # the code
    endpoint: str | None = None   # a portal page
    url: str | None = None        # or a page hosted elsewhere
    glyph: str = "ship"           # templates/_glyphs.html: what its card shows


GAMES = (
    BrowserGame("weapon-vs-armor", "Weapon vs. Armor",
                "Pick a weapon, stack the armor, fire, and watch it go through, layer by layer. No hit points and "
                "no tuning knobs: sigma-ground's own solver works it out from the materials themselves.",
                "🛡️", "https://github.com/aaronsrhodes2/sigma-ground/tree/master/games/weapon_vs_armor",
                endpoint="experiments.weapon_vs_armor", glyph="shield"),
    BrowserGame("star-control-zero", "Star Control Zero: The Precursors",
                "A fan-made prequel to Star Control II, 250,000 years earlier: fly a Precursor scout through a "
                "frontier cluster, meet the young species, and fight Them-rift incursions in 1v1 melee.",
                "🚀", "https://github.com/aaronsrhodes2/star-control-precursors"),
    BrowserGame("orion-trail", "Orion Trail",
                "Earth is dead. Space wants you dead too. An Oregon Trail in space: ration air, water and food, patch "
                "the hull, and choose your way through 158 things that go wrong between Luna and a new world.",
                "🪐", "https://github.com/aaronsrhodes2/orion-trail", endpoint="orion_trail", glyph="orbit"),
    BrowserGame("doom-loop", "Doom Loop",
                "A time-loop strategy game in a browser tab: steer a swarm of self-replicating probes through thirty "
                "years of Sol, make the yearly calls at the moot, and learn how to stop The Others. Work in progress; "
                "this card plays the newest build.",
                "♾️", "https://github.com/aaronsrhodes2/doom-loop", glyph="loop"),
    BrowserGame("shifting-prophesy", "Shifting Prophesy",
                "Purple Skippy's shake-down cruise: the home AI builds a whole top-down adventure with a procedurally generated "
                "world, one prompt at a time, with Claude Haiku on the same prompts as the reference. Play both builds and see "
                "every turn scored.",
                "🔮", "https://github.com/aaronsrhodes2/shifting-prophesy-referee", endpoint="prophesy.board", glyph="rune"),
)
