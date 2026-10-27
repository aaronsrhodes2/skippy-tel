"""The reporter's loopback door for the private Skippy's quick pop-up tools (local_api.py): the same call a portal job makes, results straight back,
the slow Core ones refused (they wait for the box through the portal), and nothing that is not a registered tool is ever called."""
import base64
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import local_api as la  # noqa: E402

REG = {"remove_background": {"name": "remove_background", "box": "pc", "description": "cut out", "inputs": {"type": "object"}, "media_in": ["image/png"],
                             "media_required": True, "outputs": ["image/png"], "est_seconds": 30, "vram_gb": 3.4, "tier": "quick"},
       "search_memory": {"name": "search_memory", "box": "pc", "description": "search", "inputs": {"type": "object"}, "outputs": ["application/json"], "est_seconds": 20},
       "make_video": {"name": "make_video", "box": "core", "description": "video", "outputs": ["video/mp4"], "est_seconds": 1900}}


def test_the_list_is_only_the_quick_pc_tools():
    names = [e["name"] for e in la.listing(REG)]
    assert names == ["remove_background", "search_memory"] and "make_video" not in names
    assert set(la.listing(REG)[0]) == {"name", "description", "inputs", "media_in", "media_required", "outputs", "est_seconds", "vram_gb", "mem_gb", "box", "tier"}


def test_a_call_returns_the_bytes_under_their_type_and_passes_the_file_and_arguments_on():
    seen = {}

    def call(entry, body):
        seen.update(entry=entry["name"], body=body)
        return b"\x89PNG-bytes", "image/png"
    b64 = base64.b64encode(b"in").decode()
    status, out = la.run("remove_background", {"args": {"output": "png"}, "input": {"type": "image/png", "name": "a.png", "b64": b64}}, REG, call)
    assert status == 200 and out["type"] == "image/png" and base64.b64decode(out["b64"]) == b"\x89PNG-bytes" and out["bytes"] == 10
    assert seen == {"entry": "remove_background", "body": {"args": {"output": "png"}, "input": {"type": "image/png", "name": "a.png", "b64": b64}}}


def test_refusals_say_why_and_an_error_from_the_service_is_a_502():
    never = lambda entry, body: (_ for _ in ()).throw(AssertionError("must not run"))                  # noqa: E731
    assert la.run("remove_background", {"args": {}}, REG, never) == (400, {"error": "remove_background needs a file (input)"})
    status, out = la.run("make_video", {"args": {}}, REG, never)
    assert status == 409 and "SKIPPY_POPUP" in out["error"]                                              # the slow ones wait for the box, through the portal
    assert la.run("rm -rf", {}, REG, never)[0] == 404 and la.run("nope_tool", {}, REG, never)[0] == 404
    assert la.run("search_memory", {"args": {}}, REG, lambda e, b: {"error": "the model isn't loaded"}) == (502, {"error": "the model isn't loaded"})
    status, out = la.run("search_memory", {"args": "junk", "input": "junk"}, REG, lambda e, b: (b"{}", "application/json"))
    assert status == 200 and base64.b64decode(out["b64"]) == b"{}"                                       # junk arguments become none, never a crash


def test_the_real_server_answers_over_http_and_refuses_what_it_should():
    server = la.serve(0, lambda: REG, lambda entry, body: (b'{"hits": 2}', "application/json"))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert [e["name"] for e in json.loads(urllib.request.urlopen(base + "/popups").read())] == ["remove_background", "search_memory"]
        req = urllib.request.Request(base + "/popup/search_memory", data=json.dumps({"args": {"query": "x"}}).encode(), method="POST")
        got = json.loads(urllib.request.urlopen(req).read())
        assert got["type"] == "application/json" and base64.b64decode(got["b64"]) == b'{"hits": 2}'
        for path, data, code in (("/popup/make_video", b"{}", 409), ("/popup/ghost", b"{}", 404), ("/popup/search_memory", b"not json", 400), ("/elsewhere", b"{}", 404)):
            try:
                urllib.request.urlopen(urllib.request.Request(base + path, data=data, method="POST"))
                raise AssertionError(path)
            except urllib.error.HTTPError as e:
                assert e.code == code, (path, e.code)
    finally:
        server.shutdown()
