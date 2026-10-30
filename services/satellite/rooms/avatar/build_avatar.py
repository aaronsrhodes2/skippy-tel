"""The room box's animated Skippy: the chat's 3D model rendered to frames, and the firmware's include for them.

    python services/satellite/rooms/avatar/build_avatar.py serve      serve render.html on http://localhost:47313 and save every frame it sends
                                                                     (open the printed URL in a browser; it says "done" in its title when finished)
    python services/satellite/rooms/avatar/build_avatar.py preview    the same, three talking frames at twice the size into frames/preview (to judge a change)
    python services/satellite/rooms/avatar/build_avatar.py yaml       write skippy-avatar.yaml (the frames as images, one animimg per loop)

The frames are committed, so building the firmware needs no browser. Re-render only when the model or the motion changes.
"""
from __future__ import annotations

import base64
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]                                   # SkippyTel: the page loads the model from portal/static
FRAMES = HERE / "frames"
PORT = 47313
W, H = 180, 220
# Each loop: how long it lasts, how many frames, and how talkative (level) or thoughtful (tilt) he is. Frames are 79 KB each in flash.
LOOPS = [
    {"name": "idle", "seconds": 5.333, "frames": 32, "level": 0, "tilt": 0},
    {"name": "talk", "seconds": 1.2, "frames": 12, "level": 1, "tilt": 0},
]


class Handler(SimpleHTTPRequestHandler):
    names: set = set()
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):                          # quiet: one line per frame is plenty
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        parts = self.path.strip("/").split("/")
        if parts[0] == "frame" and len(parts) == 3 and parts[1] in self.names and parts[2].isdigit():
            out = FRAMES / parts[1] / f"{int(parts[2]):02d}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(base64.b64decode(body.split(b",", 1)[1]))
            print("frame", parts[1], parts[2], flush=True)
        elif parts[0] == "done":
            print("done", len(json.loads(body)), "frames", flush=True)
            self.server.done = True
        self.send_response(204)
        self.end_headers()


def serve(preview: bool = False) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.done = False
    loops = [{**LOOPS[1], "name": "preview", "frames": 3}] if preview else LOOPS     # preview: three talking frames at twice the size
    Handler.names = {L["name"] for L in loops}
    q = f"w={W * (2 if preview else 1)}&h={H * (2 if preview else 1)}&loops={quote(json.dumps(loops, separators=(',', ':')))}"
    print(f"open http://localhost:{PORT}/services/satellite/rooms/avatar/render.html?{q}", flush=True)
    while not srv.done:
        srv.handle_request()
    if not preview:
        write_yaml()


def write_yaml() -> None:
    """skippy-avatar.yaml: a package with every frame as an image (merged into skippy-room.yaml's image list); widget-<loop>.yaml: one animimg
    each, included as a widget on the home page. Paths are relative to skippy-room.yaml, as ESPHome reads them."""
    lines = ["# Made by avatar/build_avatar.py: Skippy's frames (the chat's 3D model, rendered). Don't edit by hand.", "image:"]
    for L in LOOPS:
        for i in range(L["frames"]):
            lines += [f"  - file: avatar/frames/{L['name']}/{i:02d}.png", f"    id: skippy_{L['name']}_{i:02d}", "    type: RGB565"]
    (HERE / "skippy-avatar.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for L in LOOPS:
        out = [f"# Made by avatar/build_avatar.py. Skippy {L['name']}: {L['frames']} frames, {L['seconds']} s, looping.",
               "animimg:", f"  id: avatar_{L['name']}", "  align: CENTER", "  y: -30", f"  duration: {round(L['seconds'] * 1000)}ms",
               "  repeat_count: forever", "  auto_start: true"]
        if L["name"] != "idle":
            out.append("  hidden: true")
        out += ["  src:"] + [f"    - skippy_{L['name']}_{i:02d}" for i in range(L["frames"])]
        (HERE / f"widget-{L['name']}.yaml").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("wrote skippy-avatar.yaml and the widget files", flush=True)


if __name__ == "__main__":
    {"serve": serve, "preview": lambda: serve(preview=True), "yaml": write_yaml}[sys.argv[1] if len(sys.argv) > 1 else "serve"]()
