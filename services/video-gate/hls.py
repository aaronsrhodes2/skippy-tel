"""hls.py: transcoded video for the files a browser can't play as they are, from Jellyfin's HLS, made safe to hand out.

What Jellyfin 10.11 really sends (measured on the Captain's server, not assumed):
  * master.m3u8 lists `main.m3u8?<every parameter we sent>` and also an `#EXT-X-IMAGE-STREAM-INF ... URI="Trickplay/...?ApiKey=<THE
    REAL KEY>"` line: an unfiltered pass-through would hand the admin key to the browser;
  * main.m3u8 is a whole-film VOD playlist (800 segments for a two-hour film) whose every segment URL repeats all the
    parameters plus runtimeTicks/actualSegmentLengthTicks, which Jellyfin needs to find the segment again;
  * `StartTimeTicks` does NOT shift the playlist and makes every segment request fail with 400, so a start offset is
    handed to the player as `#EXT-X-START` instead (hls.js and Safari both honour it);
  * `DELETE /Videos/ActiveEncodings?deviceId&playSessionId` stops a transcode (204).

So the gate never forwards a playlist. It PARSES one and WRITES a new one out of validated pieces only: the tags it knows,
a segment path in an allowed shape, and `?s=<session>`; the two tick numbers (and Jellyfin's parameters) stay on the server,
in the session's table. Anything the playlist mentions that isn't in that table can't be fetched through the gate.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from urllib.parse import parse_qsl

from jellyfin import DEVICE_ID, SID_RE, shape_of

log = logging.getLogger("video-gate")

DEFAULT_HEIGHT = 1080
MAX_SEGMENTS = 20000
EXTRA_KEYS = ("runtimeTicks", "actualSegmentLengthTicks")     # the only query values Jellyfin's segment URLs get besides ours
CONTENT_TYPES = {"m3u8": "application/vnd.apple.mpegurl", "ts": "video/mp2t", "mp4": "video/mp4", "m4s": "video/mp4",
                 "vtt": "text/vtt; charset=utf-8"}
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_SAFE_ATTR = {"BANDWIDTH": r"[0-9]{1,10}", "AVERAGE-BANDWIDTH": r"[0-9]{1,10}", "RESOLUTION": r"[0-9]{2,5}x[0-9]{2,5}",
              "FRAME-RATE": r"[0-9]{1,3}(?:\.[0-9]{1,3})?", "VIDEO-RANGE": r"SDR|PQ|HLG"}
_CODECS = re.compile(r'"[A-Za-z0-9.,]{1,80}"')
_DIRTY = re.compile(r"api[_-]?key|token|https?:|//|\.\./|[\r\x00]", re.I)


class PlaylistError(Exception):
    """Jellyfin's playlist isn't something we can rewrite safely: nothing of it is passed on."""


class Busy(Exception):
    """Too many transcodes at once."""


def transcode_params(item_id: str, sid: str, audio: int | None, height: int) -> dict[str, str]:
    """Jellyfin's parameters for one transcode: H.264/AAC stereo in MPEG-TS (what hls.js and Safari both play; Jellyfin
    copies the video stream when it already fits and only re-encodes what it must). Fixed apart from the validated bits."""
    p = {"MediaSourceId": item_id, "DeviceId": DEVICE_ID, "PlaySessionId": sid, "VideoCodec": "h264", "AudioCodec": "aac",
         "TranscodingMaxAudioChannels": "2", "MaxAudioChannels": "2", "SegmentContainer": "ts", "MinSegments": "1",
         "BreakOnNonKeyFrames": "true", "MaxHeight": str(height)}
    if audio is not None:
        p["AudioStreamIndex"] = str(audio)
    return p


class Session:
    """One viewer's transcode: its Jellyfin parameters and the table of files its playlists have advertised."""

    __slots__ = ("sid", "item", "owner", "params", "start", "table", "last", "stopped")

    def __init__(self, sid, item, owner, params, start, now):
        self.sid, self.item, self.owner, self.params, self.start = sid, item, owner, params, start
        self.table: dict[str, tuple[str, dict]] = {}     # path → (kind, {runtimeTicks.., actualSegmentLengthTicks..})
        self.last, self.stopped = now, False

    def upstream_params(self, extras: dict) -> dict:
        return {**self.params, **extras}


class Sessions:
    """Who is transcoding what. At most `cap` at a time (more is a 429); one per pass: starting a new film with the same pass
    stops the old transcode; a session nobody has asked anything of for `idle_s` is stopped in Jellyfin (its ffmpeg is
    expensive) but kept, so a paused film resumes: the next segment request wakes Jellyfin's transcoder again."""

    def __init__(self, stopper, clock=time.time, cap: int = 2, idle_s: float = 120.0, ttl_s: float = 6 * 3600.0,
                 max_sessions: int = 256):
        self._stop_upstream, self.clock = stopper, clock
        self.cap, self.idle_s, self.ttl_s, self.max_sessions = cap, idle_s, ttl_s, max_sessions
        self._by_sid: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _active(self, now: float, skip=None) -> int:
        return sum(1 for s in self._by_sid.values() if s is not skip and now - s.last <= self.idle_s)

    def active_count(self) -> int:
        with self._lock:
            return self._active(self.clock())

    def create(self, owner: str, item: str, audio, height: int, start: float) -> Session:
        now = self.clock()
        gone: list[Session] = []
        with self._lock:
            mine = [s for s in self._by_sid.values() if s.owner == owner]
            others_active = sum(1 for s in self._by_sid.values() if s.owner != owner and now - s.last <= self.idle_s)
            if others_active >= self.cap:
                raise Busy()                  # refused BEFORE the owner's old transcode is touched
            for s in mine:
                gone.append(self._by_sid.pop(s.sid))
            sid = secrets.token_hex(16)
            sess = Session(sid, item, owner, transcode_params(item, sid, audio, height), start, now)
            self._by_sid[sid] = sess
            while len(self._by_sid) > self.max_sessions:
                oldest = min(self._by_sid.values(), key=lambda s: s.last)
                gone.append(self._by_sid.pop(oldest.sid))
        for s in gone:
            self._stop(s)
        return sess

    def touch(self, sid: str, item: str) -> Session | None:
        """The session for an HLS request, marked as in use. None: unknown (or for another film). Busy: it had gone idle and
        the slots it would need again are taken."""
        now = self.clock()
        with self._lock:
            s = self._by_sid.get(sid)
            if s is None or s.item != item:
                return None
            if now - s.last > self.idle_s and self._active(now, skip=s) >= self.cap:
                raise Busy()
            s.last, s.stopped = now, False
            return s

    def discard(self, sess: Session) -> None:
        with self._lock:
            self._by_sid.pop(sess.sid, None)
        self._stop(sess)

    def stop_for(self, owner: str, item: str) -> int:
        """The player says it has stopped: end that pass's transcode of that film now."""
        with self._lock:
            gone = [self._by_sid.pop(s.sid) for s in list(self._by_sid.values()) if s.owner == owner and s.item == item]
        for s in gone:
            self._stop(s)
        return len(gone)

    def reap(self) -> None:
        """Stop what nobody is watching: idle sessions in Jellyfin, long-dead ones from the table."""
        now = self.clock()
        to_stop = []
        with self._lock:
            for s in list(self._by_sid.values()):
                if now - s.last > self.ttl_s:
                    del self._by_sid[s.sid]
                    if not s.stopped:
                        to_stop.append(s)
                elif now - s.last > self.idle_s and not s.stopped:
                    s.stopped = True
                    to_stop.append(s)
        for s in to_stop:
            self._stop(s)

    def _stop(self, sess: Session) -> None:
        try:
            self._stop_upstream(sess.sid)
        except Exception as e:  # noqa: BLE001 — Jellyfin being away must not break the request that triggered this
            log.warning("video-gate: could not stop a transcode (%s)", type(e).__name__)

    def run_forever(self, every: float = 15.0) -> None:
        while True:
            time.sleep(every)
            try:
                self.reap()
            except Exception as e:  # noqa: BLE001
                log.error("video-gate: reaper crashed: %s", type(e).__name__)


# ---- playlist rewriting -------------------------------------------------------------------------------------------
def _lines(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines or lines[0] != "#EXTM3U":
        raise PlaylistError("not a playlist")
    return lines


def _uri(line: str, kinds: tuple[str, ...]) -> tuple[str, dict]:
    """(path, extras) of one URI Jellyfin wrote. The path must be one of OUR allowed shapes (so no host, no '..', no
    scheme); only the two tick numbers survive from its query: everything else, an api key included, is dropped."""
    path, _, query = line.partition("?")
    if shape_of(path) not in kinds:
        raise PlaylistError("unexpected file in playlist")
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=64)
    except ValueError:
        raise PlaylistError("too many parameters") from None
    extras = {k: v for k, v in pairs if k in EXTRA_KEYS and re.fullmatch(r"[0-9]{1,19}", v)}
    return path, extras


def _attrs(value: str) -> dict[str, str]:
    return {k: v for k, v in _ATTR.findall(value)}


def _start_tag(sess: Session) -> list[str]:
    return [f"#EXT-X-START:TIME-OFFSET={sess.start:.3f},PRECISE=YES"] if sess.start > 0 else []


def _finish(out: list[str], forbidden) -> str:
    """Last line of defence: the text we are about to hand out is checked for anything that looks like a key, a URL or a
    path escape, and for the literal secrets we know. It should never fire: every line was built from validated parts."""
    text = "\n".join(out) + "\n"
    if _DIRTY.search(text) or any(f and f in text for f in forbidden):
        raise PlaylistError("playlist failed the final check")
    return text


def rewrite_master(text: str, sess: Session, forbidden=()) -> str:
    lines = _lines(text)
    out = ["#EXTM3U", *_start_tag(sess)]
    table: dict[str, tuple[str, dict]] = {}
    pending, variants = None, 0
    for ln in lines[1:]:
        if ln.startswith("#"):
            tag, _, value = ln.partition(":")
            if tag == "#EXT-X-VERSION" and re.fullmatch(r"[0-9]{1,2}", value):
                out.append(f"#EXT-X-VERSION:{value}")
            elif tag == "#EXT-X-STREAM-INF":
                if pending is not None:
                    raise PlaylistError("variant without a URI")
                attrs = _attrs(value)
                keep = [f"{k}={attrs[k]}" for k, rx in _SAFE_ATTR.items() if k in attrs and re.fullmatch(rx, attrs[k])]
                if not any(k.startswith("BANDWIDTH=") for k in keep):
                    keep.insert(0, "BANDWIDTH=2000000")
                if "CODECS" in attrs and _CODECS.fullmatch(attrs["CODECS"]):
                    keep.append(f"CODECS={attrs['CODECS']}")
                pending = "#EXT-X-STREAM-INF:" + ",".join(keep)
            # every other tag is dropped: #EXT-X-IMAGE-STREAM-INF (the key), #EXT-X-MEDIA, #EXT-X-I-FRAME-STREAM-INF, ...
            continue
        if pending is None:
            raise PlaylistError("URI without a variant")
        path, extras = _uri(ln, ("playlist",))
        table[path] = ("playlist", extras)
        out += [pending, f"{path}?s={sess.sid}"]
        pending, variants = None, variants + 1
    if pending is not None or not variants:
        raise PlaylistError("no usable variant")
    result = _finish(out, forbidden)
    sess.table.update(table)
    return result


def rewrite_media(text: str, sess: Session, forbidden=()) -> str:
    lines = _lines(text)
    out = ["#EXTM3U"]
    table: dict[str, tuple[str, dict]] = {}
    inf, byterange, started = None, None, False
    for ln in lines[1:]:
        if ln.startswith("#"):
            tag, _, value = ln.partition(":")
            if tag == "#EXT-X-VERSION" and re.fullmatch(r"[0-9]{1,2}", value):
                out.append(f"#EXT-X-VERSION:{value}")
                if not started:
                    out += _start_tag(sess)
                    started = True
            elif tag in ("#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE") and re.fullmatch(r"[0-9]{1,9}", value):
                out.append(f"{tag}:{value}")
            elif tag == "#EXT-X-PLAYLIST-TYPE" and value in ("VOD", "EVENT"):
                out.append(f"{tag}:{value}")
            elif tag in ("#EXT-X-ENDLIST", "#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-DISCONTINUITY"):
                out.append(tag)
            elif tag == "#EXTINF":
                m = re.fullmatch(r"([0-9]{1,6}(?:\.[0-9]{1,6})?)(?:,.*)?", value)
                if not m:
                    raise PlaylistError("bad EXTINF")
                inf = m.group(1)
            elif tag == "#EXT-X-BYTERANGE" and re.fullmatch(r"[0-9]{1,12}(?:@[0-9]{1,12})?", value):
                byterange = value
            elif tag == "#EXT-X-MAP":
                attrs = _attrs(value)
                uri = attrs.get("URI", "").strip('"')
                path, extras = _uri(uri, ("segment",))
                table[path] = ("segment", extras)
                rng = attrs.get("BYTERANGE", "").strip('"')
                out.append(f'#EXT-X-MAP:URI="{path}?s={sess.sid}"' + (f',BYTERANGE="{rng}"' if re.fullmatch(r"[0-9]{1,12}@[0-9]{1,12}", rng) else ""))
            elif tag == "#EXT-X-KEY" and _attrs(value).get("METHOD") != "NONE":
                raise PlaylistError("encrypted playlist")       # we'd hand out segments nobody can decode
            # everything else is dropped
            continue
        if inf is None:
            raise PlaylistError("segment without EXTINF")
        path, extras = _uri(ln, ("segment", "vtt"))
        if len(table) >= MAX_SEGMENTS:
            raise PlaylistError("too many segments")
        table[path] = ("segment" if shape_of(path) == "segment" else "vtt", extras)
        out.append(f"#EXTINF:{inf},")
        if byterange:
            out.append(f"#EXT-X-BYTERANGE:{byterange}")
        out.append(f"{path}?s={sess.sid}")
        inf = byterange = None
    if not table:
        raise PlaylistError("empty playlist")
    if not started:
        out[1:1] = _start_tag(sess)
    result = _finish(out, forbidden)
    sess.table.update(table)
    return result


# ---- request parameters (the HLS routes use these before anything goes upstream) ----------------------------------
def parse_height(raw) -> int | None:
    if raw is None:
        return DEFAULT_HEIGHT
    if not re.fullmatch(r"[0-9]{3,4}", raw):
        return None
    h = int(raw)
    return h if 144 <= h <= 2160 else None


def parse_start(raw, runtime: int) -> float | None:
    if raw is None or raw == "":
        return 0.0
    if not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,3})?", raw):
        return None
    v = float(raw)
    return v if v <= max(runtime, 0) else None


def valid_sid(sid) -> bool:
    return isinstance(sid, str) and bool(SID_RE.fullmatch(sid))
