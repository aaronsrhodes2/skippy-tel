"""The Captain's one-time Spotify sign-in, on skippy-pc (services/spotify/README.md). Standard library only; Claude runs it, he only clicks.

    python services/spotify/signin.py            opens http://127.0.0.1:43874/ in his browser and waits (30 minutes at most)

Two steps on one page, both on Spotify's own pages, so his password never passes through here:
  1. the Web API (his playlists, search, adding songs): the authorization code flow with PKCE for his own Spotify app. If the vault has no
     SPOTIFY_CLIENT_ID yet, the page says how to make the app (developer.spotify.com, Development Mode: Premium needed, which he has) and takes its
     Client ID (not a secret). The redirect URI to add there is REDIRECT below. The refresh token goes into the vault .env (SPOTIFY_REFRESH_TOKEN,
     with SPOTIFY_CLIENT_ID) and straight to the spotify container, so nothing restarts.
  2. the speaker (go-librespot): the page shows the container's pairing code and the spotify.com/pair link, and says when it has logged in.
"""
from __future__ import annotations

import base64
import hashlib
import html
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

PORT = 43874
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SERVICE = os.environ.get("SPOTIFY_SERVICE_URL", "http://127.0.0.1:43873")
VAULT_ENV = Path(os.environ.get("SKIPPY_VAULT_DIR", r"G:\My Drive\vault")) / ".env"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from service import SCOPES  # noqa: E402


def read_env(path: Path = VAULT_ENV) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def set_env(values: dict, path: Path = VAULT_ENV) -> None:
    """Replace (or add) KEY=value lines in the vault .env, leaving every other line as it was."""
    text = path.read_bytes().decode("utf-8") if path.exists() else ""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    for key, value in values.items():
        new = f"{key}={value}"
        for i, line in enumerate(lines):
            if re.match(rf"\s*{re.escape(key)}\s*=", line):
                lines[i] = new
                break
        else:
            lines.append(new)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes((nl.join(lines) + nl).encode("utf-8"))           # its own line ends, on any system
    os.replace(tmp, path)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, challenge: str, state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
                                                    "code_challenge_method": "S256", "code_challenge": challenge, "state": state})


def exchange(client_id: str, code: str, verifier: str) -> dict:
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": client_id,
                                   "code_verifier": verifier}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:                    # noqa: S310 — Spotify's token endpoint
        return json.loads(r.read())


def service(method: str, path: str, body: dict | None = None, token: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(SERVICE + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:                # noqa: S310 — the spotify container on localhost
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except OSError as e:
        return 0, {"error": f"the spotify container is not answering ({type(e).__name__}): docker compose up -d --build spotify"}


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Skippy · Spotify</title>
<style>body{{background:#000;color:#fff;font:16px/1.5 system-ui,sans-serif;max-width:40em;margin:2em auto;padding:0 16px}}h1{{color:#00FF88;font-size:1.3em}}
a{{color:#00CCFF}}code{{color:#FFAA00}}.ok{{color:#00FF88}}.wait{{color:#FFAA00}}.bad{{color:#FF4444}}input{{background:#000;color:#fff;border:1px solid #006644;
padding:.4em;width:100%;font:inherit}}button{{background:#000;color:#00FF88;border:1px solid #00FF88;padding:.4em 1em;font:inherit;margin-top:.5em}}
li{{margin:.4em 0}}</style>{refresh}<h1>Skippy · Spotify</h1>{body}"""


class State:
    def __init__(self, env_path: Path = VAULT_ENV):
        self.env_path = env_path
        self.client_id = read_env(env_path).get("SPOTIFY_CLIENT_ID", "")
        self.verifier, self.challenge = pkce()
        self.state = secrets.token_urlsafe(16)
        self.web_done = bool(read_env(env_path).get("SPOTIFY_REFRESH_TOKEN"))
        self.error = ""
        self.finished = threading.Event()


def page(st: State) -> str:
    if not st.client_id:
        body = f"""<p>Step 1 of 2: his own Spotify app (Development Mode, which needs Premium: he has it). Once only:</p><ol>
<li>Open <a href="https://developer.spotify.com/dashboard" target="_blank">developer.spotify.com/dashboard</a> and sign in. If the music organizer's
app is there, open it; otherwise <b>Create app</b> (any name, e.g. <i>Skippy</i>; tick <b>Web API</b>).</li>
<li>In its settings add the Redirect URI <code>{REDIRECT}</code> and save.</li>
<li>Copy its <b>Client ID</b> here (it is not a secret; no client secret is needed):</li></ol>
<form action="/start"><input name="client_id" placeholder="Client ID" autofocus required pattern="[0-9a-fA-F]{{32}}"><button>Sign in to Spotify</button></form>"""
        return PAGE.format(refresh="", body=body)
    if not st.web_done:
        err = f'<p class="bad">{html.escape(st.error)}</p>' if st.error else ""
        return PAGE.format(refresh="", body=f"""{err}<p>Step 1 of 2: <a href="/go">sign in to Spotify</a> and allow Skippy to read and change your playlists,
your library and playback.</p><p>(Client ID <code>{html.escape(st.client_id)}</code>. <a href="/forget">Use another app</a>.)</p>""")
    code, h = service("GET", "/health")
    player = (h.get("deps") or {}).get("player") or {}
    web = (h.get("deps") or {}).get("web_api") or {}
    lines = [f'<p class="{"ok" if web.get("ready") else "bad"}">1. Web API: {"signed in" if web.get("ready") else html.escape(web.get("detail") or h.get("error") or "not answering")}</p>']
    if player.get("ready"):
        lines.append('<p class="ok">2. The speaker is logged in: "Skippy" is in his Spotify devices.</p><p class="ok">All done. This page can be closed.</p>')
        st.finished.set()
    elif h.get("pair"):
        p = h["pair"]
        lines.append(f"""<p class="wait">Step 2 of 2: the speaker. Open <a href="{html.escape(p['url'])}" target="_blank">{html.escape(p['url'])}</a>
and enter the code <code style="font-size:1.4em">{html.escape(p['code'])}</code>.</p><p>This page checks every few seconds.</p>""")
    else:
        lines.append(f'<p class="wait">2. The speaker: {html.escape(player.get("detail") or h.get("error") or "waiting for the spotify container")} (checking again).</p>')
    return PAGE.format(refresh="" if player.get("ready") else '<meta http-equiv="refresh" content="4">', body="".join(lines))


def finish_web(st: State, code: str, exchange=exchange, service=service) -> None:
    got = exchange(st.client_id, code, st.verifier)
    refresh = got.get("refresh_token")
    if not refresh:
        raise RuntimeError(f"Spotify sent no refresh token ({got.get('error', 'unknown')})")
    set_env({"SPOTIFY_CLIENT_ID": st.client_id, "SPOTIFY_REFRESH_TOKEN": refresh}, st.env_path)
    token = read_env(st.env_path).get("SKIPPY_HOME_TOKEN", "")
    c, reply = service("POST", "/token", {"client_id": st.client_id, "refresh_token": refresh}, token)
    if c != 200:
        st.error = f"Saved in the vault, but the container did not take it ({c} {reply.get('error', '')}); `docker compose up -d spotify` loads it."
    st.web_done = True


def make_handler(st: State):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code: int, text: str, location: str = "") -> None:
            raw = text.encode()
            self.send_response(code)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if u.path == "/start" and re.fullmatch(r"[0-9a-fA-F]{32}", q.get("client_id", "")):
                st.client_id = q["client_id"].lower()
                set_env({"SPOTIFY_CLIENT_ID": st.client_id}, st.env_path)
                return self._send(302, "", authorize_url(st.client_id, st.challenge, st.state))
            if u.path == "/go" and st.client_id:
                return self._send(302, "", authorize_url(st.client_id, st.challenge, st.state))
            if u.path == "/forget":
                st.client_id, st.web_done = "", False
                return self._send(302, "", "/")
            if u.path == "/callback":
                if q.get("state") != st.state:
                    st.error = "That sign-in was for another page: try again."
                elif q.get("error"):
                    st.error = f"Spotify said: {q['error']}. (Is the Redirect URI {REDIRECT} in the app's settings?)"
                else:
                    try:
                        finish_web(st, q.get("code", ""))
                        st.error = st.error if st.web_done else ""
                    except Exception as e:                                # noqa: BLE001
                        st.error = f"The sign-in did not finish: {type(e).__name__}: {str(e)[:160]}"
                return self._send(302, "", "/")
            if u.path in ("/", ""):
                st.web_done = st.web_done and bool(read_env(st.env_path).get("SPOTIFY_REFRESH_TOKEN"))
                return self._send(200, page(st))
            return self._send(404, "not here")

        def log_message(self, *_a):
            pass
    return Handler


def main() -> int:
    force = "--again" in sys.argv                                         # sign in to the Web API again even though the vault has a token
    st = State()
    if force:
        st.web_done = False
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(st))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/"
    print(f"signin: {url} (waiting up to 30 minutes)", flush=True)
    webbrowser.open(url)
    done = st.finished.wait(1800)
    time.sleep(2)                                                          # the last page is shown before the server goes
    srv.shutdown()
    print("signin: done" if done else "signin: gave up waiting", flush=True)
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
