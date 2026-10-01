"""An in-process fake Jellyfin 10.11: just the handful of endpoints the video-gate uses, behaving like the real one where it
matters for the gate (each of these was measured on the Captain's server, 2026-09-30):

  * ids are 32 hex without dashes; an unknown id is a 400, not a 404; a subtitle index it hasn't got is a 500;
  * /Videos/<id>/stream honours Range (206, Content-Range, 416 past the end) and has no ETag;
  * master.m3u8 echoes every parameter into `main.m3u8?...` AND lists an #EXT-X-IMAGE-STREAM-INF whose URI carries
    `ApiKey=<the key>`; the media playlist repeats the parameters on every segment plus runtimeTicks/actualSegmentLengthTicks;
  * a segment request without those two numbers, or with StartTimeTicks, is a 400;
  * DELETE /Videos/ActiveEncodings needs deviceId and playSessionId and answers 204.

Everything that arrives is logged (`requests`), so a test can assert what the gate asked for, with which credentials, and that
nothing else was ever asked.
"""
from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

KEY = "FAKEKEY" + "a1b2c3d4" * 3 + "e5f6"           # a recognisable secret: tests grep every response for it
USER_ID = "16d8deb46bd940a2b872c236900d9028"
SERVER_ID = "e2cdc0a89b1c4cf2acb5474fe177bc49"
OTHER_ID = "ffffffffffffffffffffffffffffffff"


PATTERN = bytes(range(251))


def fid(n: int) -> str:
    return f"{n:032x}"


def body_bytes(start: int, n: int) -> bytes:
    """What the fake 'file' holds at [start, start+n): a repeating 0..250 pattern, so a test can check a Range answer byte for byte."""
    off = start % 251
    return (PATTERN * (n // 251 + 2))[off:off + n]


def video_stream(codec="h264", profile="High", depth=8, pix="yuv420p", height=480, index=0, level=31):
    return {"Codec": codec, "Type": "Video", "Index": index, "Profile": profile, "BitDepth": depth, "PixelFormat": pix,
            "Height": height, "Width": height * 16 // 9, "Level": level, "IsDefault": True, "IsExternal": False,
            "IsTextSubtitleStream": False}


def audio_stream(codec="aac", index=1, lang="eng", channels=2, profile="LC"):
    return {"Codec": codec, "Type": "Audio", "Index": index, "Language": lang, "Channels": channels, "Profile": profile,
            "IsDefault": index == 1, "IsExternal": False, "IsTextSubtitleStream": False}


def sub_stream(index, codec="subrip", lang="eng", text=True, external=False):
    return {"Codec": codec, "Type": "Subtitle", "Index": index, "Language": lang, "IsExternal": external,
            "IsTextSubtitleStream": text, "IsDefault": False}


def movie(n, name, container="mov,mp4,m4a,3gp,3g2,mj2", streams=None, year=2000, runtime_s=7200, poster=True, backdrop=True,
          overview="A film.", genres=("Drama",), rating=7.456, official="PG-13", created="2023-09-11T09:29:04.0000000Z",
          position_s=0, played=False, kind="Movie", **extra):
    tags = {}
    if poster:
        tags["Primary"] = "p" + fid(n)[:12]
        tags["Thumb"] = "t" + fid(n)[:12]
        tags["Logo"] = "l" + fid(n)[:12]
    row = {
        "Name": name, "SortName": name.lower(), "ServerId": SERVER_ID, "Id": fid(n), "DateCreated": created,
        "Container": container, "OfficialRating": official, "Overview": overview, "Genres": list(genres),
        "CommunityRating": rating, "RunTimeTicks": runtime_s * 10_000_000 if runtime_s else None, "ProductionYear": year,
        "Type": kind, "IsFolder": False, "ImageTags": tags, "BackdropImageTags": ["b" + fid(n)[:12]] if backdrop else [],
        "MediaStreams": streams if streams is not None else [video_stream(), audio_stream()], "MediaType": "Video",
        "UserData": {"PlaybackPositionTicks": position_s * 10_000_000, "PlayCount": 1 if played else 0, "Played": played,
                     "IsFavorite": False, "ItemId": fid(n)},
    }
    row.update(extra)
    return row


def boxset(n, name, poster=True):
    return {"Name": name, "SortName": name.lower(), "Id": fid(n), "Type": "BoxSet", "IsFolder": True,
            "ImageTags": {"Primary": "p" + fid(n)[:12]} if poster else {}, "BackdropImageTags": [], "UserData": {"Played": False}}


def standard_library():
    """Ten films that exercise the `direct` rules, a series and an episode, and two collections."""
    items = [
        movie(1, "Alpha", year=1994, position_s=1200, genres=("Horror", "Thriller"), runtime_s=5400),            # direct
        movie(2, "Bravo", container="matroska,webm", year=1996, runtime_s=6000, streams=[
            video_stream(), audio_stream("ac3", 1, "eng", 6, ""), audio_stream("aac", 2, "jpn"),
            sub_stream(3), sub_stream(4, "hdmv_pgs_subtitle", "fra", text=False)]),                               # needs HLS
        movie(3, "Charlie", streams=[video_stream("hevc", "Main", 8, "yuv420p", 1080), audio_stream()]),
        movie(4, "Delta", streams=[video_stream(depth=10, pix="yuv420p10le", profile="High 10"), audio_stream()]),
        movie(5, "Echo", streams=[video_stream(), audio_stream("mp2", 1, "eng", 2, "")]),
        movie(6, "Foxtrot", container=None, streams=[], runtime_s=0, poster=False, backdrop=False),             # not probed yet
        movie(7, "Golf", container="mov", poster=False, backdrop=False, streams=[video_stream(), audio_stream("mp3", 1, "eng", 2, "")]),
        movie(8, "Hotel", streams=[video_stream()], played=True),                                              # silent film
        movie(9, "India", container="avi", streams=[video_stream("mpeg4", "Advanced Simple Profile", None, "yuv420p"), audio_stream("mp3")]),
        {**movie(10, "Juliet Show", kind="Series", streams=[], runtime_s=0), "Container": None},
        movie(11, "Juliet S01E02", kind="Episode", SeriesId=fid(10), ParentIndexNumber=1, IndexNumber=2, runtime_s=2700),
    ]
    boxes = [boxset(20, "The Alpha Collection"), boxset(21, "Empty Box", poster=False)]
    children = {fid(20): [fid(2), fid(1), OTHER_ID], fid(21): []}
    return items, boxes, children


class FakeJellyfin:
    def __init__(self, items=None, boxes=None, children=None, segments=5, fmp4=False):
        std = standard_library()
        self.items = items if items is not None else std[0]
        self.boxes = boxes if boxes is not None else std[1]
        self.children = children if children is not None else std[2]
        self.segments, self.fmp4 = segments, fmp4
        self.requests: list[dict] = []
        self.deleted: list[tuple[str, str]] = []
        self.userdata: list[dict] = []
        self.userdata_status = 0              # non-zero: answer every watch-state write with this status
        self.encodings: set[str] = set()
        self.video_size = 1_000_000
        self.video_chunk_delay = 0.0
        self.stream_extra_headers: dict[str, str] = {}
        self.stream_truncate: int | None = None   # send only this many bytes of a stream, then hang up
        self.stream_redirect = ""                 # answer a stream with a 302 to this URL
        self.stream_chunked = False               # send streams with Transfer-Encoding: chunked instead of a Content-Length
        self.playlist_hook = None             # fn(text, 'master'|'main') -> text: lets a test make Jellyfin send something hostile
        self.sent: dict[str, int] = {}              # bytes written per path (a disconnecting client leaves this short)
        self.aborted: dict[str, bool] = {}
        self.delay: dict[str, float] = {}           # path prefix → seconds to sit on a request (timeouts)
        self.vtt = "WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\nHello\r\n"
        self._lock = threading.Lock()
        self._conns: set = set()
        self.down = False
        self.httpd = None

    # ---- life cycle ---------------------------------------------------------------------------------------------
    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def setup(self):
                super().setup()
                fake._conns.add(self.connection)

            def finish(self):
                try:
                    super().finish()
                finally:
                    fake._conns.discard(self.connection)

            def do_GET(self):
                fake.handle(self, "GET")

            def do_HEAD(self):
                fake.handle(self, "HEAD")

            def do_POST(self):
                fake.handle(self, "POST")

            def do_DELETE(self):
                fake.handle(self, "DELETE")

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def verify_request(self, request, client_address):     # `down`: accept and hang up at once (a closed port takes ~2 s on Windows)
                return not fake.down

            def handle_error(self, request, client_address):      # a client hanging up mid-answer is the point of some tests
                if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                    super().handle_error(request, client_address)

        self.httpd = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def go_down(self):
        """Jellyfin stops answering: new connections are dropped at once and the keep-alive ones the gate's client holds are cut."""
        self.down = True
        for c in list(self._conns):
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass

    def come_back(self):
        self.down = False

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.go_down()

    # ---- helpers for tests --------------------------------------------------------------------------------------
    def calls(self, prefix: str = "", method: str | None = None) -> list[dict]:
        with self._lock:
            return [r for r in self.requests if r["path"].startswith(prefix) and (method is None or r["method"] == method)]

    def item(self, n: int) -> dict:
        return next(i for i in self.items if i["Id"] == fid(n))

    # ---- the server -----------------------------------------------------------------------------------------------
    def handle(self, h: BaseHTTPRequestHandler, method: str):
        parts = urlsplit(h.path)
        path, query = unquote(parts.path), parse_qsl(parts.query, keep_blank_values=True)
        q = dict(query)
        auth = h.headers.get("Authorization", "")
        rec = {"method": method, "path": path, "query": query, "q": q, "range": h.headers.get("Range"),
               "authed": f'Token="{KEY}"' in auth, "headers": {k.lower(): v for k, v in h.headers.items()}}
        with self._lock:
            self.requests.append(rec)
        for prefix, secs in self.delay.items():
            if path.startswith(prefix):
                time.sleep(secs)
        body = b""
        if method == "POST":
            body = h.rfile.read(int(h.headers.get("Content-Length") or 0))
        rec["body"] = body

        def send(code, data=b"", ctype="application/json; charset=utf-8", headers=None):
            h.send_response(code)
            h.send_header("Content-Type", ctype)
            h.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                h.send_header(k, v)
            h.end_headers()
            if method != "HEAD":
                h.wfile.write(data)

        if path == "/System/Info/Public":
            return send(200, json.dumps({"ServerName": "FAKE", "Version": "10.11.8"}).encode())
        if not rec["authed"]:
            return send(401, b"Unauthorized")
        m = re.fullmatch(r"/Items/([0-9a-f]{32})/Images/(\w+)", path)
        if path == "/Users":
            return send(200, json.dumps([{"Name": "viewer", "Id": OTHER_ID, "Policy": {"IsAdministrator": False}},
                                         {"Name": "aaron", "Id": USER_ID, "Policy": {"IsAdministrator": True}}]).encode())
        if path == "/Items":
            return self.items_list(send, q)
        if m:
            return self.image(send, m.group(1), m.group(2), q)
        m = re.fullmatch(r"/Videos/([0-9a-f]{32})/stream", path)
        if m:
            return self.stream(h, send, method, m.group(1), q)
        m = re.fullmatch(r"/Videos/([0-9a-f]{32})/(master|main)\.m3u8", path)
        if m:
            return self.playlist(send, m.group(1), m.group(2), parts.query, q)
        m = re.fullmatch(r"/Videos/([0-9a-f]{32})/(hls1/main/(-?\d+)\.(ts|mp4))", path)
        if m:
            return self.segment(send, m.group(1), m.group(3), m.group(4), q)
        if path == "/Videos/ActiveEncodings" and method == "DELETE":
            if "deviceId" not in q or "playSessionId" not in q:
                return send(400, b"Error processing request.", "text/plain")
            self.deleted.append((q["deviceId"], q["playSessionId"]))
            self.encodings.discard(q["playSessionId"])
            return send(204)
        m = re.fullmatch(r"/Videos/([0-9a-f]{32})/([0-9a-f]{32})/Subtitles/(\d+)/Stream\.vtt", path)
        if m:
            idx = int(m.group(3))
            have = [s["Index"] for i in self.items if i["Id"] == m.group(1) for s in i["MediaStreams"] if s["Type"] == "Subtitle"]
            if idx not in have:
                return send(500, b"Error processing request.", "text/plain")
            return send(200, self.vtt.encode(), "text/vtt")
        m = re.fullmatch(r"/UserItems/([0-9a-f]{32})/UserData", path)
        if m and method == "POST":
            if self.userdata_status:                         # a test makes Jellyfin refuse the write
                return send(self.userdata_status, b"")
            row = next((i for i in self.items if i["Id"] == m.group(1)), None)
            if row is None:
                return send(404, b"")
            fields = json.loads(body or b"{}")
            self.userdata.append({"item": m.group(1), "user": q.get("userId"), "body": fields})
            row["UserData"].update({k: v for k, v in fields.items() if k in ("PlaybackPositionTicks", "Played", "PlayCount")})
            return send(200, json.dumps({"ItemId": m.group(1)}).encode())
        with self._lock:
            rec["unexpected"] = True
        return send(404, b"")

    def items_list(self, send, q):
        if "userId" not in q:
            return send(400, b"Error processing request.", "text/plain")
        types = set(q.get("IncludeItemTypes", "").split(",")) - {""}
        if "ParentId" in q:
            ids = self.children.get(q["ParentId"], [])
            rows = [i for i in self.items if i["Id"] in ids]
        elif "BoxSet" in types:
            rows = list(self.boxes)
        else:
            rows = [i for i in self.items if not types or i["Type"] in types]
        total = len(rows)
        start, limit = int(q.get("StartIndex", 0)), int(q.get("Limit", 100000))
        send(200, json.dumps({"Items": rows[start:start + limit], "TotalRecordCount": total, "StartIndex": start}).encode())

    def image(self, send, item_id, kind, q):
        row = next((i for i in self.items + self.boxes if i["Id"] == item_id), None)
        if row is None:
            return send(400, b"Error processing request.", "text/plain")
        have = {"Primary": "Primary" in row["ImageTags"], "Thumb": "Thumb" in row["ImageTags"], "Logo": "Logo" in row["ImageTags"],
                "Backdrop": bool(row["BackdropImageTags"])}
        if not have.get(kind):
            return send(404, b"")
        width = q.get("maxWidth", "0")
        if kind == "Logo":
            return send(200, b"\x89PNG\r\n\x1a\n" + width.encode() * 10, "image/png")
        send(200, b"\xff\xd8\xff" + f"{item_id}:{kind}:{width}".encode() * 4, "image/jpeg")

    def stream(self, h, send, method, item_id, q):
        if item_id not in {i["Id"] for i in self.items}:
            return send(400, b"Error processing request.", "text/plain")
        if self.stream_redirect:                             # a Jellyfin that tries to send the gate somewhere else
            return send(302, b"", "text/plain", {"Location": self.stream_redirect})
        size = self.video_size
        rng, start, end = h.headers.get("Range"), 0, size - 1
        code, extra = 200, {"Accept-Ranges": "bytes", "Last-Modified": "Mon, 11 Sep 2023 09:43:37 GMT"}
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
            a, b = m.groups()
            if a == "" and b != "":
                start = max(0, size - int(b))
            elif a != "":
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            if start >= size:
                extra["Content-Range"] = f"bytes */{size}"
                return send(416, b"", "video/mp4", extra)
            code, extra["Content-Range"] = 206, f"bytes {start}-{end}/{size}"
        length = end - start + 1
        h.send_response(code)
        h.send_header("Content-Type", "video/mp4")
        if self.stream_chunked and method != "HEAD":         # no Content-Length: the end of the body is the terminating chunk
            h.send_header("Transfer-Encoding", "chunked")
        else:
            h.send_header("Content-Length", str(length))
        for k, v in {**extra, **self.stream_extra_headers}.items():
            h.send_header(k, v)
        h.end_headers()
        if method == "HEAD":
            return
        if self.stream_chunked:
            stop_at = end if self.stream_truncate is None else min(end, start + self.stream_truncate - 1)
            try:
                for pos in range(start, stop_at + 1, 65536):
                    data = body_bytes(pos, min(65536, stop_at - pos + 1))
                    h.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                if stop_at < end:                            # hung up with no terminating chunk
                    h.close_connection = True
                    h.connection.shutdown(socket.SHUT_RDWR)
                else:
                    h.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        key = f"stream:{item_id}"
        self.sent[key], pos = 0, start
        stop_at = end if self.stream_truncate is None else min(end, start + self.stream_truncate - 1)
        try:
            while pos <= stop_at:
                n = min(65536, stop_at - pos + 1)
                h.wfile.write(body_bytes(pos, n))
                h.wfile.flush()
                self.sent[key] += n
                pos += n
                if self.video_chunk_delay:
                    time.sleep(self.video_chunk_delay)
            if stop_at < end:                                # promised more than was sent, then hung up: Jellyfin dying mid-file
                h.close_connection = True
                h.connection.shutdown(socket.SHUT_RDWR)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.aborted[key] = True

    # ---- HLS ---------------------------------------------------------------------------------------------------
    def playlist(self, send, item_id, which, raw_query, q):
        if item_id not in {i["Id"] for i in self.items}:
            return send(400, b"Error processing request.", "text/plain")
        if q.get("MediaSourceId") != item_id or not q.get("PlaySessionId"):
            return send(400, b"Error processing request.", "text/plain")
        echo = urlencode(self_query(raw_query))
        ctype = "application/vnd.apple.mpegurl"
        if which == "master":
            text = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=256000,AVERAGE-BANDWIDTH=256000,VIDEO-RANGE=SDR,"
                    'CODECS="avc1.64001F,mp4a.40.2",RESOLUTION=960x720,FRAME-RATE=23.976\n'
                    f"main.m3u8?{echo}\n"
                    '#EXT-X-IMAGE-STREAM-INF:BANDWIDTH=7696,RESOLUTION=320x240,CODECS="jpeg",'
                    f'URI="Trickplay/320/tiles.m3u8?MediaSourceId={item_id}&ApiKey={KEY}"\n')
            return send(200, (self.playlist_hook(text, "master") if self.playlist_hook else text).encode(), ctype)
        self.encodings.add(q["PlaySessionId"])           # asking for the media playlist is what starts ffmpeg
        ext = "mp4" if self.fmp4 else "ts"
        lines = ["#EXTM3U", "#EXT-X-PLAYLIST-TYPE:VOD", f"#EXT-X-VERSION:{7 if self.fmp4 else 3}", "#EXT-X-TARGETDURATION:11",
                 "#EXT-X-MEDIA-SEQUENCE:0"]
        if self.fmp4:
            lines.append(f'#EXT-X-MAP:URI="hls1/main/-1.mp4?{echo}"')
        for n in range(self.segments):
            lines += ["#EXTINF:10.000000, nodesc",
                      f"hls1/main/{n}.{ext}?{echo}&runtimeTicks={n * 100_000_000}&actualSegmentLengthTicks=100000000"]
        lines.append("#EXT-X-ENDLIST")
        text = "\n".join(lines) + "\n"
        send(200, (self.playlist_hook(text, "main") if self.playlist_hook else text).encode(), ctype)

    def segment(self, send, item_id, n, ext, q):
        if item_id not in {i["Id"] for i in self.items}:
            return send(400, b"Error processing request.", "text/plain")
        if n != "-1" and ("runtimeTicks" not in q or "actualSegmentLengthTicks" not in q):
            return send(400, b"Error processing request.", "text/plain")
        if "StartTimeTicks" in q:                        # what the real 10.11 does with a start offset
            return send(400, b"Error processing request.", "text/plain")
        if not q.get("PlaySessionId"):
            return send(400, b"Error processing request.", "text/plain")
        self.encodings.add(q["PlaySessionId"])
        send(200, (b"G" if ext == "ts" else b"\x00\x00\x00\x18styp") + b"seg" + n.encode(), "video/mp2t" if ext == "ts" else "video/mp4")


def self_query(raw: str):
    return parse_qsl(raw, keep_blank_values=True)
