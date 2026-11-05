"""A stand-in for one gate, for edge/selftest.sh only: it answers what the real gates answer (JSON that says who it is and what reached it) and
has a few paths that exercise the proxy: a big deterministic body, a slow trickle, a range, a body cut short. Nothing here is imported by the
portal or the real gates; the selftest runs it in a throwaway container as `python selftest_upstream.py <gate name>`."""
import hashlib
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

NAME = sys.argv[1] if len(sys.argv) > 1 else "gate"
BLOCK = 65536
RANGE_BODY = b"0123456789" * 10


def big_block(i: int) -> bytes:
    """Block i of the deterministic stream: the same bytes every time, cheap to make, impossible to guess by luck."""
    return (hashlib.sha256(f"{NAME}:{i}".encode()).digest() * (BLOCK // 32))


def big_sha256(megabytes: int) -> str:
    h = hashlib.sha256()
    for i in range(megabytes * 1048576 // BLOCK):
        h.update(big_block(i))
    return h.hexdigest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "gunicorn/22.0"            # the real gates say this: the edge must not pass it on
    sys_version = ""

    def log_message(self, *args):
        pass

    def read_body(self):
        want = int(self.headers.get("Content-Length") or 0)
        h, got = hashlib.sha256(), 0
        while got < want:
            chunk = self.rfile.read(min(BLOCK, want - got))
            if not chunk:
                break
            h.update(chunk)
            got += len(chunk)
        return got, h.hexdigest()

    def send_json(self, code, obj, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")           # the gates answer CORS themselves: the edge must keep it
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_any(self):
        got, digest = self.read_body()
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if "/big" in url.path:                              # N megabytes of the deterministic stream, then the same stream's hash at /sha
            mb = int((query.get("mb") or ["8"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(mb * 1048576))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            for i in range(mb * 1048576 // BLOCK):
                self.wfile.write(big_block(i))
            return
        if "/sha" in url.path:
            return self.send_json(200, {"sha256": big_sha256(int((query.get("mb") or ["8"])[0]))})
        if "/trickle" in url.path:                          # a line every half second, flushed: a proxy that buffers shows nothing until the end
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            for n in range(4):
                line = f"line {n}\n".encode()
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                self.wfile.flush()
                time.sleep(0.5)
            self.wfile.write(b"0\r\n\r\n")
            return
        if "/range" in url.path:
            span = self.headers.get("Range", "")
            if span.startswith("bytes="):
                first, _, last = span[6:].partition("-")
                a, b = int(first), int(last or len(RANGE_BODY) - 1)
                part = RANGE_BODY[a:b + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{a + len(part) - 1}/{len(RANGE_BODY)}")
            else:
                part = RANGE_BODY
                self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(part)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(part)
            return
        if "/abort" in url.path:                            # promises a megabyte, sends 100 bytes, hangs up: the proxy logs an error that carries the request
            self.send_response(200)
            self.send_header("Content-Length", "1048576")
            self.end_headers()
            self.wfile.write(b"x" * 100)
            self.wfile.flush()
            self.close_connection = True
            return
        self.send_json(200, {"service": NAME, "method": self.command, "path": self.path, "got": got, "sha256": digest,
                             "host": self.headers.get("Host"), "xfp": self.headers.get("X-Forwarded-Proto"), "via": self.headers.get("Via")},
                       extra={"Via": "1.1 gunicorn"})            # (the Server header is already there: server_version above)

    do_GET = do_HEAD = do_PUT = do_POST = do_OPTIONS = do_DELETE = handle_any


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
