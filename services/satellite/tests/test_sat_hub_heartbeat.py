"""The hub's heartbeat (hub.heartbeat): each room box connected or not, each house backend answering or not, for the Status map."""
import asyncio
import json

import pytest

import hub
from house import HomeError, House


class Up(House):
    def states(self, fresh=False):
        return [{"entity_id": "light.a"}, {"entity_id": "light.b"}]


class Nest(House):
    def states(self, fresh=False):
        raise HomeError("the Nest isn't connected to Skippy yet")


class Both:
    parts = [Up(), Nest()]


class FakeHub:
    rooms_spec = [("office", "esphome", "1.2.3.4", 6053), ("theater", "esphome", "1.2.3.5", 6053)]
    rooms = {"theater": object()}


def test_the_heartbeat_writes_rooms_and_backends_whole(tmp_path):
    path = tmp_path / "hub-status.json"

    async def stop(_):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(hub.heartbeat(FakeHub(), path, Both(), clock=lambda: 1000.0, sleep=stop))
    got = json.loads(path.read_text(encoding="utf-8"))
    assert got["at"] == 1000 and got["rooms"] == {"office": False, "theater": True}
    assert got["house"]["up"] == {"ok": True, "things": 2}
    assert got["house"]["nest"] == {"ok": False, "why": "the Nest isn't connected to Skippy yet"}
    assert not (tmp_path / "hub-status.tmp").exists()
