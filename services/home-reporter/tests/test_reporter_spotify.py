"""The spotify job (reporter.spotify_job): the Drive page's Spotify rows reach the Captain's Spotify at home, four operations only, and the
service's own words come back when the car's Spotify isn't there."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import reporter


def serve(calls):
    class H(BaseHTTPRequestHandler):
        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            calls.append((self.command, self.path, json.loads(self.rfile.read(n) or b"null") if n else None))
            code, body = (404, {"error": "the car's Spotify isn't among his devices (open Spotify in the car)"}) if self.path == "/car/play" \
                and calls[-1][2]["uri"].endswith("x" * 22) else (200, {"liked": [{"uri": "spotify:track:" + "1" * 22}], "suggested": []})
            raw = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        do_GET = do_POST = _do

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def test_the_four_operations_and_nothing_else():
    calls = []
    srv, url = serve(calls)
    try:
        assert reporter.spotify_job({"op": "mix"}, url)["result"]["liked"]
        assert reporter.spotify_job({"op": "car_play", "uri": "spotify:track:" + "1" * 22}, url)["result"]
        assert "open Spotify in the car" in reporter.spotify_job({"op": "car_play", "uri": "spotify:track:" + "x" * 22}, url)["error"]
        assert reporter.spotify_job({"op": "car_pause"}, url)["result"]
        assert "unknown" in reporter.spotify_job({"op": "token"}, url)["error"]            # never the sign-in or anything else
        assert [c[:2] for c in calls] == [("GET", "/mix"), ("POST", "/car/play"), ("POST", "/car/play"), ("POST", "/car/pause")]
        assert reporter.run_job({"kind": "spotify", "op": "car"}) == {"error": "Spotify isn't set up on skippy-pc"} or reporter.SPOTIFY
    finally:
        srv.shutdown()
    assert "isn't set up" in reporter.spotify_job({"op": "mix"}, "")["error"]
