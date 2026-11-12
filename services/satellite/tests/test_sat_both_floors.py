"""'Play on both upstairs and downstairs' is a request the hub answers itself (speakers: everywhere), never one the model may refuse (the Captain, 2026-11-11)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain  # noqa: E402

EVERYWHERE = {"what": "music", "place": "everywhere", "play": True}


def test_both_floors_phrasings_route_to_everywhere():
    for said in ("play on both upstairs and downstairs", "play it upstairs and downstairs too", "play it on both upstairs and downstairs",
                 "put the music on upstairs and downstairs"):
        assert brain.speakers_route(said) == EVERYWHERE, said


def test_one_place_and_one_speaker_are_unchanged():
    assert brain.speakers_route("play upstairs") == {"what": "music", "place": "upstairs", "play": True}
    assert brain.speakers_route("move it upstairs") == {"what": "music", "place": "upstairs"}
    assert brain.device_route("play it on the Sony too", True) == {"what": "music", "device": "the sony", "on": True}
