"""house_mcp.py: the house's three tools over MCP's stdio JSON-RPC, for the private Skippy's runs."""
import house_mcp
from house import House


class Lamp(House):
    def __init__(self):
        self.calls = []

    def states(self, fresh=False):
        return [{"entity_id": "light.desk", "state": "off", "attributes": {"friendly_name": "desk lamp"}},
                {"entity_id": "lock.front", "state": "locked", "attributes": {"friendly_name": "front door"}}]

    def _service(self, domain, service, data):
        self.calls.append((domain, service, data["entity_id"]))


def call(name, args, house):
    return house_mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": args}},
                            lambda: house)["result"]["content"][0]["text"]


def test_it_lists_the_house_tools_and_answers_initialize():
    names = {t["name"] for t in house_mcp.handle({"id": 1, "method": "tools/list"}, lambda: None)["result"]["tools"]}
    assert {"home_devices", "home_state", "home_control"} <= names
    assert {"play_music", "speakers", "music"} <= names                  # the pods' music is reachable from the private Skippy (2026-11-11)
    assert house_mcp.handle({"id": 2, "method": "initialize"}, lambda: None)["result"]["serverInfo"]["name"] == "skippy-house"
    assert house_mcp.handle({"method": "notifications/initialized"}, lambda: None) is None


def test_a_light_is_switched_and_a_lock_never_is_even_with_confirm():
    lamp = Lamp()
    assert call("home_control", {"name": "desk lamp", "action": "on"}, lamp).startswith("Done")
    lamp.sensitive = True                                    # even where locks are visible, text never confirms one
    assert "confirm" in call("home_control", {"name": "front door", "action": "unlock", "confirm": True}, lamp)
    assert lamp.calls == [("light", "turn_on", "light.desk")]


def test_no_house_set_up_is_a_sentence():
    assert "Nothing in the house is set up" in call("home_devices", {}, None)


def test_music_tools_go_to_the_radio_not_the_house():
    sent = []

    def radio(name, args):
        sent.append((name, args))
        return "Playing calm music on upstairs and downstairs."
    msg = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "play_music", "arguments": {"mood": "calm", "speaker": "both floors"}}}
    out = house_mcp.handle(msg, lambda: None, radio)["result"]["content"][0]["text"]   # no house set up: music still plays
    assert out.startswith("Playing calm") and sent == [("play_music", {"mood": "calm", "speaker": "both floors"})]


def test_a_radio_that_is_down_is_a_sentence():
    assert "not answering" in house_mcp.radio_tool("music", {"action": "what"}, url="http://127.0.0.1:9", timeout=2)
