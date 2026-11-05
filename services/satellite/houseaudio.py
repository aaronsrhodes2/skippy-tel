"""What the house is playing, said once: for the house chat's prompt, for a bare "stop", and for the routines (the Captain, 2026-11-04: "Bilby plays all the audio,
even Audible books ... a single audio stream ... the house chat needs to know how to talk to Bilby for audio").

Bilby is the house's ONE player: his music, Spotify and his audiobooks, the same stream everywhere unless he narrows it, with Skippy's voice ducking it. The model
reads `Audio.status()` on every turn, so it never has to guess what plays, where, or that a stop means all of it; the plain "stop" commands read `Audio.playing()`.
Everything here is read-only and never raises: a radio that does not answer is a sentence, not an error.
"""
from __future__ import annotations

import time

KIND_WORDS = {"enclave": "the Enclave upstairs", "hdmi": "the Sony downstairs", "bt": "the Bluetooth stereo"}
_STARTED: dict = {}                                                # who started the music that is queued now: {"by": "...", "at": wall clock}; this hub process only


def note_start(by: str, clock=time.time) -> None:
    """The music was started by `by` ("his own request", "the homecoming routine"): so the chat can say why the Enclave is playing (2026-11-04: it played for fifty minutes "on its own")."""
    _STARTED.update(by=str(by), at=float(clock()))


def started_line() -> str:
    if not _STARTED:
        return ""
    when = time.strftime("%I:%M %p", time.localtime(_STARTED["at"])).lstrip("0")
    return f"This music was started by {_STARTED['by']} at {when}."


def kind_words(kind: str) -> str:
    if kind.startswith("pod:"):
        return f"the box in the {kind[4:]}"
    return KIND_WORDS.get(kind, kind)


def _list(words: list[str]) -> str:
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]


def station_line(state: dict | None) -> str:
    """One honest sentence for the house radio's /state ({} or None: it is not answering)."""
    st = state or {}
    if not st:
        return "Bilby, the house radio, is not answering right now: you cannot play or steer music until it does."
    title, artist = str(st.get("title") or "").strip(), str(st.get("artist") or "").strip()
    what = title + (f" by {artist}" if artist and title else "")
    level = st.get("level")
    volume = f" Volume is {round(float(level) * 10)} of 10." if isinstance(level, (int, float)) else ""
    queue = int(st.get("queue_len") or 0)
    open_ = [k for k in (st.get("open") or st.get("speakers") or [])]
    where = _list([kind_words(k) for k in open_]) or "no speaker that is on"
    tail = f"{queue} tracks are queued{', DJ mode is on' if st.get('dj') else ''}."
    if st.get("playing"):
        line = f"Bilby is playing {what or 'music'} on {where}.{volume} {tail}"
    elif what:
        line = f"Bilby is paused on {what}.{volume} {tail} 'Resume' carries on."
    else:
        line = "Bilby is not playing anything."
    closed = [k for k in (st.get("closed") or [])]
    if closed:
        line += f" Switched off right now: {_list([kind_words(k) for k in closed])}."
    return line


def book_line(where: dict | None) -> str:
    """BookPlayer.where(): '' when no book is loaded."""
    if not where:
        return ""
    title, author = where.get("title") or "a book", where.get("author") or ""
    name = f"'{title}'" + (f" by {author}" if author else "")
    if where.get("playing"):
        return f"An audiobook is playing through Bilby too: {name}, chapter {where.get('chapter')} of {where.get('chapters')}."
    return f"His audiobook {name} is loaded but paused (chapter {where.get('chapter')})."


ROUTINE_WORDS = {"arrived": "the car arrived home", "started": "the {routine} routine started", "done": "the {routine} routine finished",
                 "stopped": "he stopped the {routine} routine", "snoozed": "he put the {routine} routine off", "skipped": "a part of the {routine} routine was skipped"}


def recent_line(records: list[dict], limit: int = 8) -> str:
    """'Recently: 19:30 the car arrived home; 19:31 he said "stop the music" (music); ...' from the house log's records (housemem.HouseLog.recent): what he said, the
    routines, the car; so the chat can say why the music is on without guessing. '' when there is nothing."""
    out: list[str] = []
    for r in records:
        kind, at = r.get("kind"), str(r.get("time") or "")
        if kind == "routine":
            what = ROUTINE_WORDS.get(str(r.get("outcome")), "the {routine} routine: {outcome}").format(routine=r.get("routine") or "", outcome=r.get("outcome") or "")
        elif kind == "command" and r.get("words"):
            tools = [t for t in (r.get("tools") or []) if isinstance(t, str)]
            what = f'he said "{str(r["words"])[:70]}"' + (f" ({', '.join(tools)})" if tools else "")
        else:
            continue
        out.append(f"{at} {what}".strip())
    return ("Recently: " + "; ".join(out[-limit:]) + ".") if out else ""


class Audio:
    """The hub's view of the house's audio. `radio` is a RadioClient (state(), playing); `room` returns the Room whose `book` is the BookPlayer (or None);
    `routines` the daily.Daily (run, busy); `hlog` the housemem.HouseLog (recent). All optional, all read defensively."""

    def __init__(self, radio, room=None, routines=None, hlog=None):
        self.radio, self.room, self.routines, self.hlog = radio, room, routines, hlog

    def _book(self):
        try:
            r = self.room() if self.room is not None else None
            return getattr(r, "book", None)
        except Exception:                                          # noqa: BLE001
            return None

    def playing(self) -> bool:
        """Anything the house plays right now: the station, or a book."""
        try:
            if self.radio is not None and bool(getattr(self.radio, "playing", False)):
                return True
        except Exception:                                          # noqa: BLE001
            pass
        book = self._book()
        return bool(book is not None and getattr(book, "playing", False))

    def routine(self) -> str:
        try:
            if self.routines is not None and getattr(self.routines, "busy", False):
                return str(getattr(self.routines.run, "name", "") or "")
        except Exception:                                          # noqa: BLE001
            pass
        return ""

    def status(self) -> str:
        """The lines for the end of the system prompt: the station, a book, a routine that may start the library."""
        try:
            state = self.radio.state() if self.radio is not None and hasattr(self.radio, "state") else None
        except Exception:                                          # noqa: BLE001
            state = None
        lines = [station_line(state)]
        if state and int(state.get("queue_len") or 0):
            lines.append(started_line())
        book = self._book()
        try:
            lines.append(book_line(book.where() if book is not None else None))
        except Exception:                                          # noqa: BLE001
            pass
        name = self.routine()
        if name:
            lines.append(f"The {name} routine is running right now; its music step starts the library unless he has already said stop.")
        try:
            lines.append(recent_line(self.hlog.recent()) if self.hlog is not None else "")
        except Exception:                                          # noqa: BLE001
            pass
        return " ".join(x for x in lines if x)
