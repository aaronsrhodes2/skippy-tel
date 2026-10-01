"""jellyfin.py: the video-gate's ONLY way to Jellyfin, and the choke point for everything that goes upstream.

The gate holds an ADMIN-level Jellyfin API key and sits on the public internet behind passes, so nothing in here takes a
URL, a header or a query string from a client. Every method builds its upstream path from a fixed template plus pieces
that are re-validated HERE (32-hex ids, small integers, a short list of image kinds and segment-file shapes), whatever
the caller already checked: a bug in a route must not become an SSRF or a way to reach another Jellyfin endpoint.

The key travels only in the Authorization header (never in a URL, so it can't land in a log, a Referer or a playlist).
Redirects are never followed, proxies from the environment are ignored, cookies are dropped, every call has timeouts,
and errors are reduced to a JellyfinError(kind) with no upstream text: the routes turn that into a fixed message.
"""
from __future__ import annotations

import http.cookiejar
import logging
import re
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("video-gate")

ID_RE = re.compile(r"[0-9a-f]{32}")                       # a Jellyfin GUID without dashes
SID_RE = re.compile(r"[0-9a-f]{32}")                      # a PlaySessionId (the gate makes these itself)
# HLS file shapes the gate will ever fetch: directories and names are plain tokens, so no '..', no '%', no ':' or '//'
_DIRS = r"(?:[A-Za-z0-9_-]{1,40}/){0,3}"
PLAYLIST_RE = re.compile(_DIRS + r"[A-Za-z0-9_-]{1,40}\.m3u8")
SEGMENT_RE = re.compile(_DIRS + r"-?[0-9]{1,9}\.(?:ts|mp4|m4s)")      # '-1.mp4' is fMP4's init segment
VTT_RE = re.compile(_DIRS + r"[A-Za-z0-9_-]{1,40}\.vtt")
IMAGE_KINDS = {"poster": "Primary", "backdrop": "Backdrop", "logo": "Logo", "thumb": "Thumb"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
DEVICE_ID = "skippy-video-gate"
KEY_RE = re.compile(r"[A-Za-z0-9._~+/=-]{8,256}")         # nothing that could break out of a header value
MAX_TEXT = 8 * 1024 * 1024                                 # a playlist or image bigger than this is not what we asked for


class JellyfinError(Exception):
    """kind: down (unreachable, timeout, 5xx), missing (400/404: Jellyfin answers 400 for an unknown id), refused (our
    key was rejected), bad (an answer we can't use). Never carries upstream text."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def shape_of(path: str) -> str | None:
    """'playlist' | 'segment' | 'vtt' for a path that has an allowed file shape, else None."""
    if PLAYLIST_RE.fullmatch(path):
        return "playlist"
    if SEGMENT_RE.fullmatch(path):
        return "segment"
    if VTT_RE.fullmatch(path):
        return "vtt"
    return None


def _need_id(value: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError("not a Jellyfin id")
    return value


def _need_int(value, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError("integer out of range")
    return value


class Client:
    def __init__(self, base_url: str, api_key: str, connect_timeout: float = 3.05, read_timeout: float = 30.0):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query:
            raise ValueError("JELLYFIN_URL must be a plain http(s) address")
        if not KEY_RE.fullmatch(api_key or ""):
            raise ValueError("JELLYFIN_API_KEY missing or malformed")
        self.base = f"{parts.scheme}://{parts.netloc}"
        self._headers = {
            "Authorization": ('MediaBrowser Client="Skippy Video Gate", Device="skippy-pc", '
                              f'DeviceId="{DEVICE_ID}", Version="1.0", Token="{api_key}"'),
            "Accept-Encoding": "identity",     # streams are forwarded byte for byte: never let Jellyfin gzip them
        }
        self.connect_timeout, self.read_timeout = connect_timeout, read_timeout
        self._http = requests.Session()
        self._http.trust_env = False                                         # no proxy/.netrc from the environment
        self._http.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))   # accept no cookies
        # One retry, and only for a connection that could not be made (a pooled connection that Jellyfin restarted away): a
        # request that was sent is never repeated, so a POST can't be applied twice.
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=64,
                              max_retries=Retry(total=1, connect=1, read=0, status=0, redirect=0, other=0))
        self._http.mount("http://", adapter)
        self._http.mount("https://", adapter)

    def __repr__(self) -> str:                  # a stray log of the client must not print the key
        return f"<jellyfin.Client {self.base}>"

    # ---- plumbing ----------------------------------------------------------------------------------------------
    def _send(self, method: str, path: str, params=None, headers=None, stream=False, read_timeout=None, auth=True, body=None):
        assert path.startswith("/") and "?" not in path and "#" not in path    # callers build `path` from templates only
        hdrs = dict(self._headers) if auth else {}
        hdrs.update(headers or {})
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        try:
            r = self._http.request(method, self.base + path, params=params, headers=hdrs, stream=stream, json=body,
                                   timeout=(self.connect_timeout, read_timeout or self.read_timeout), allow_redirects=False)
        except requests.RequestException:
            raise JellyfinError("down") from None        # `from None`: nothing of the request rides along in a traceback
        return r

    @staticmethod
    def _fail(r) -> JellyfinError:
        status = r.status_code
        r.close()
        if status in (400, 404):
            return JellyfinError("missing")
        if status in (401, 403):
            return JellyfinError("refused")
        return JellyfinError("down" if status >= 500 else "bad")

    def _json(self, path: str, params=None):
        r = self._send("GET", path, params=params)
        try:
            if r.status_code != 200:
                raise self._fail(r)
            try:
                return r.json()
            except ValueError:
                raise JellyfinError("bad") from None
        finally:
            r.close()

    @staticmethod
    def _read_capped(r, cap: int = MAX_TEXT) -> bytes:
        got = bytearray()
        try:
            for chunk in r.iter_content(65536):
                got += chunk
                if len(got) > cap:
                    raise JellyfinError("bad")
        except requests.RequestException:
            raise JellyfinError("down") from None
        finally:
            r.close()
        return bytes(got)

    # ---- library ----------------------------------------------------------------------------------------------
    def ping(self) -> bool:
        """Is Jellyfin answering? (/System/Info/Public needs no key: this is only a liveness probe)."""
        try:
            r = self._send("GET", "/System/Info/Public", auth=False, read_timeout=2.0)
        except JellyfinError:
            return False
        try:
            return r.status_code == 200
        finally:
            r.close()

    def users(self) -> list[dict]:
        data = self._json("/Users")
        return data if isinstance(data, list) else []

    def items(self, user_id: str, page: int = 500, cap: int = 50000):
        """Every Movie/Series/Episode with the fields the catalog needs, paged so a big library never needs one huge answer."""
        _need_id(user_id)
        start = 0
        while start < cap:
            data = self._json("/Items", params={
                "userId": user_id, "Recursive": "true", "IncludeItemTypes": "Movie,Series,Episode",
                "Fields": "Overview,Genres,MediaStreams,DateCreated,ProductionYear,OfficialRating,CommunityRating,"
                          "RunTimeTicks,SortName",
                "SortBy": "SortName", "SortOrder": "Ascending", "StartIndex": str(start), "Limit": str(page)})
            rows = data.get("Items") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise JellyfinError("bad")
            yield from rows
            start += len(rows)
            total = data.get("TotalRecordCount")
            if not rows or (isinstance(total, int) and start >= total):
                return

    def collections(self, user_id: str) -> list[dict]:
        _need_id(user_id)
        data = self._json("/Items", params={"userId": user_id, "Recursive": "true", "IncludeItemTypes": "BoxSet",
                                            "Fields": "SortName"})
        rows = data.get("Items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise JellyfinError("bad")
        return rows

    def children(self, user_id: str, parent_id: str) -> list[dict]:
        _need_id(user_id), _need_id(parent_id)
        data = self._json("/Items", params={"userId": user_id, "ParentId": parent_id})
        rows = data.get("Items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise JellyfinError("bad")
        return rows

    # ---- media -----------------------------------------------------------------------------------------------
    def image(self, item_id: str, kind: str, width: int, quality: int = 80) -> tuple[bytes, str]:
        """(bytes, mime) of one of an item's images, scaled by Jellyfin. JellyfinError('missing') when it has none."""
        _need_id(item_id)
        if kind not in IMAGE_KINDS.values():
            raise ValueError("unknown image kind")
        _need_int(width, 16, 4096), _need_int(quality, 10, 100)
        r = self._send("GET", f"/Items/{item_id}/Images/{kind}", params={"maxWidth": str(width), "quality": str(quality)},
                       stream=True)
        if r.status_code != 200:
            raise self._fail(r)
        mime = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime not in IMAGE_TYPES or r.headers.get("Content-Encoding", "identity") != "identity":
            r.close()
            raise JellyfinError("bad")
        return self._read_capped(r), mime

    def stream(self, item_id: str, range_header: str | None = None, head: bool = False):
        """The original file, still open (the caller relays it and must close it). Raw bytes: static=true means Jellyfin
        does no remux or transcode, and an unrelated mediaSourceId is never sent: the item's own id is its default source."""
        _need_id(item_id)
        hdrs = {"Range": range_header} if range_header else {}
        r = self._send("HEAD" if head else "GET", f"/Videos/{item_id}/stream", stream=True, headers=hdrs,
                       params={"static": "true", "mediaSourceId": item_id})
        if r.status_code not in (200, 206, 416):
            raise self._fail(r)
        if r.headers.get("Content-Encoding", "identity") != "identity":
            r.close()
            raise JellyfinError("bad")
        return r

    def hls_text(self, item_id: str, name: str, params: dict[str, str]) -> str:
        """A playlist (master.m3u8, main.m3u8, ...) as text; `params` is the gate's own transcode profile."""
        r = self._hls("GET", item_id, name, params, stream=True, read_timeout=90.0)
        if r.status_code != 200:
            raise self._fail(r)
        return self._read_capped(r).decode("utf-8", "replace")

    def hls_stream(self, item_id: str, name: str, params: dict[str, str]):
        """A segment, still open (the caller relays it and must close it)."""
        r = self._hls("GET", item_id, name, params, stream=True, read_timeout=90.0)
        if r.status_code != 200:
            raise self._fail(r)
        if r.headers.get("Content-Encoding", "identity") != "identity":
            r.close()
            raise JellyfinError("bad")
        return r

    def _hls(self, method, item_id, name, params, stream, read_timeout):
        _need_id(item_id)
        if name != "master.m3u8" and shape_of(name) is None:
            raise ValueError("not an allowed HLS file")
        for k, v in params.items():
            if not re.fullmatch(r"[A-Za-z]{1,40}", k) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", v):
                raise ValueError("HLS parameter outside the safe alphabet")
        return self._send(method, f"/Videos/{item_id}/{name}", params=params, stream=stream, read_timeout=read_timeout)

    def stop_encoding(self, play_session_id: str) -> bool:
        """Tell Jellyfin to stop (and clean up) the transcode of one gate session."""
        if not SID_RE.fullmatch(play_session_id or ""):
            raise ValueError("bad session id")
        r = self._send("DELETE", "/Videos/ActiveEncodings", params={"deviceId": DEVICE_ID, "playSessionId": play_session_id})
        ok = r.status_code in (200, 204)
        r.close()
        return ok

    def subtitle(self, item_id: str, index: int) -> bytes:
        """One subtitle stream as WebVTT (Jellyfin converts text subtitles); at most MAX_TEXT bytes."""
        _need_id(item_id)
        _need_int(index, 0, 999)
        r = self._send("GET", f"/Videos/{item_id}/{item_id}/Subtitles/{index}/Stream.vtt", stream=True)
        if r.status_code != 200:
            raise self._fail(r)
        return self._read_capped(r, cap=2 * 1024 * 1024)

    def set_user_data(self, user_id: str, item_id: str, fields: dict) -> None:
        """Write the watch state of one item for one user (what Jellyfin's own apps read for Continue Watching). Only
        the four fields the gate computes can be sent, whatever the caller hands in."""
        _need_id(user_id), _need_id(item_id)
        body = {}
        if "PlaybackPositionTicks" in fields:
            body["PlaybackPositionTicks"] = _need_int(fields["PlaybackPositionTicks"], 0, 10 ** 15)
        if "Played" in fields:
            if not isinstance(fields["Played"], bool):
                raise ValueError("Played must be a bool")
            body["Played"] = fields["Played"]
        if "PlayCount" in fields:
            body["PlayCount"] = _need_int(fields["PlayCount"], 0, 10000)
        if "LastPlayedDate" in fields:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{7}Z", str(fields["LastPlayedDate"])):
                raise ValueError("bad date")
            body["LastPlayedDate"] = fields["LastPlayedDate"]
        r = self._send("POST", f"/UserItems/{item_id}/UserData", params={"userId": user_id}, body=body)
        if r.status_code not in (200, 204):
            raise self._fail(r)
        r.close()
