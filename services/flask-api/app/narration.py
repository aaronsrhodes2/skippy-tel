"""
narration.py — in-character feed narration strings for SkippyChat.

Each tier/service has a pool of Skippy-voice one-liners.  `get_narration()`
picks one at random.  The caller can pass `now_playing_title` to cross-link
the Nagatha lines with whatever Bilby is currently spinning — the kind of
casual inter-system awareness that makes the whole thing feel alive.

Tone guide:
  - Skippy (local/claude tiers)   : sarcastic, long-suffering, secretly proud
  - Nagatha                       : warm, curious, gently amused
  - DropShip                      : Skippy doing grunt-work with quiet dignity
  - Image gen                     : Skippy playing artist — grudgingly enjoying it
  - Trust the awesomeness         : reverent invocation of tremendous power
  - Translate                     : showing off, casually
"""
import random


_NARRATIONS: dict[str, list[str]] = {
    "local": [
        "Skippy sighs and handles this one himself.",
        "Skippy mutters something about doing all the work around here.",
        "Skippy is already three steps ahead, as usual.",
        "Skippy processes this in roughly three nanoseconds and tries not to look bored.",
        "Oh, *this* one. Skippy suppresses a sigh and gets on with it.",
    ],
    "nagatha": [
        "Nagatha is on it, humming quietly while she works.",
        "Nagatha searches the web, a small smile on her face.",
        "Nagatha is already typing. She finds this sort of thing delightful.",
        "Nagatha dives in, cheerful as ever.",
    ],
    "dropship": [
        "Skippy dredges through the DropShip Gallery with quiet dignity.",
        "Skippy rummages through the photo archive.",
        "Skippy retrieves the goods from orbital storage.",
        "Skippy mutters as he dredges through the DropShip Gallery…",
        "DropShip inbound. Skippy loads the manifest.",
    ],
    "image": [
        "Skippy fires up the rendering engine. Don't rush him.",
        "Skippy is painting. Digitally. With lasers, basically.",
        "Skippy considers the composition, then decides it will be magnificent.",
        "Skippy engages the SD pipeline. An artist at work.",
    ],
    "translate": [
        "Skippy translates. He speaks forty-seven languages fluently.",
        "Skippy switches linguistic gears without breaking a sweat.",
        "Language barrier? Please. Skippy handles this in his sleep.",
    ],
    "trust_awesomeness": [
        "Skippy invokes the awesomeness. You're welcome.",
        "The awesomeness has been trusted. Proceed with reverence.",
    ],
    "solid_gold": [
        "Skippy bridges the solid gold channel. Fancy.",
        "Solid gold mode engaged. Read-only and magnificent.",
    ],
    "ups": [
        "Skippy pings the UPS. It beeps back reassuringly.",
        "Skippy checks the power situation with practiced ease.",
    ],
    "bilby": [
        "Bilby nods slowly with a big smile as he rotates the tunes.",
        "Bilby checks the decks, satisfied.",
        "Skippy relays the Bilby data. The music continues.",
    ],
    "vault": [
        "The vault stirs. Skippy retrieves the requested item.",
        "Skippy opens the vault quietly, as one does.",
    ],
}

# Fallback pool for unknown tiers
_DEFAULT_POOL = _NARRATIONS["local"]


def get_narration(tier_id: str, now_playing_title: str | None = None) -> str:
    """
    Return a random in-character narration line for the given tier.

    If `tier_id` is "nagatha" and `now_playing_title` is supplied, the line
    will reference the currently playing song — a small cross-system callback
    that makes the assistant feel aware of its surroundings.
    """
    pool = _NARRATIONS.get(tier_id, _DEFAULT_POOL)
    line = random.choice(pool)

    # Cross-link Nagatha's narration with the current Bilby track when available.
    if tier_id == "nagatha" and now_playing_title:
        line = (
            f"Nagatha is searching the web, "
            f"humming along to \"{now_playing_title}\" while she works."
        )

    return line
