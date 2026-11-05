"""What the house is playing, said once: for the house chat's prompt, for a bare "stop", and for the routines (the Captain, 2026-11-04: "Bilby plays all the audio,
even Audible books ... a single audio stream ... the house chat needs to know how to talk to Bilby for audio").

Bilby is the house's ONE player: his music, Spotify and his audiobooks are items of one queue and come out as one stream; each speaker has a switch and plays it
or not; with no speaker listening the stream is held; Skippy's voice ducks all of it. The model reads `Audio.status()` on every turn, so it never has to guess what
plays, on which speakers (switched off, gone, or silent), or that a stop means all of it; the plain "stop" commands read `Audio.playing()`.
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


def book_words(book: dict) -> str:
    title, author = book.get("title") or "a book", book.get("author") or ""
    return f"the audiobook '{title}'" + (f" by {author}" if author else "") + (f", chapter {book.get('chapter')} of {book.get('chapters')}" if book.get("chapters") else "")


def speakers_line(st: dict) -> str:
    """Every speaker of the one stream and what it is doing, so the chat never says a speaker plays when it is switched off or gone (a radio that does not send
    its switches yet: only the ones switched off)."""
    outs = [o for o in (st.get("outputs") or []) if isinstance(o, dict) and o.get("id")]
    if not outs:
        closed = [k for k in (st.get("closed") or [])]
        return f"Switched off right now: {_list([kind_words(k) for k in closed])}." if closed else ""
    said = []
    for o in outs:
        name = kind_words(o["id"])
        if o["id"].startswith("pod:"):
            if o.get("on"):
                state = "playing" if o.get("open") else "on but not tuned in"
            else:
                state = "switched off" if o.get("mode") == "off" else "silent (it plays only if the stereo on its floor drops out)"
        else:
            state = ("playing it" if o.get("open") else "on but not connected right now") if o.get("on") else "switched off"
        said.append(f"{name} is {state}")
    return "Speakers: " + _list(said) + "."


def station_line(state: dict | None) -> str:
    """One honest sentence for the house radio's /state ({} or None: it is not answering): a song, a Spotify track or an audiobook, and where."""
    st = state or {}
    if not st:
        return "Bilby, the house radio, is not answering right now: you cannot play or steer music until it does."
    kind = st.get("kind") or "song"
    book = st.get("book") if kind == "book" and isinstance(st.get("book"), dict) else None
    title, artist = str(st.get("title") or "").strip(), str(st.get("artist") or "").strip()
    what = book_words(book) if book else title + (f" by {artist}" if artist and title else "")
    level = st.get("level")
    volume = f" Volume is {round(float(level) * 10)} of 10." if isinstance(level, (int, float)) else ""
    queue = int(st.get("queue_len") or 0)
    open_ = [k for k in (st.get("open") or st.get("speakers") or [])]
    where = _list([kind_words(k) for k in open_]) or "no speaker that is on"
    tail = "" if kind == "book" else f"{queue} tracks are queued{', DJ mode is on' if st.get('dj') else ''}."
    held = st.get("held") if isinstance(st.get("held"), dict) else None
    if st.get("playing"):
        line = f"Bilby is playing {what or 'music'} on {where}.{volume}" + (f" {tail}" if tail else "")
    elif held and what and held.get("kind") == "car":
        line = f"Bilby has let go of {what}: he is listening to it in the car. 'Play my book' carries on from the car's place."
    elif held and what:
        line = (f"Bilby is holding {what}: {held.get('why') or 'no speaker is on'}, so there is nobody to play it to. It carries on by itself when a speaker is turned on "
                f"(or comes back).{volume}" + (f" {tail}" if tail else ""))
    elif what:
        line = f"Bilby is paused on {what}.{volume}" + (f" {tail}" if tail else "") + (" 'Resume' carries on." if kind != "book" else " 'Resume' carries on from the same place.")
    else:
        line = "Bilby is not playing anything."
    spoken = speakers_line(st)
    if spoken:
        line += f" {spoken}"
    return line


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
    """The hub's view of the house's audio. `radio` is a RadioClient (state(), playing): a book is an item of the radio's one stream, so it is all in there;
    `routines` the daily.Daily (run, busy); `hlog` the housemem.HouseLog (recent). All optional, all read defensively."""

    def __init__(self, radio, routines=None, hlog=None):
        self.radio, self.routines, self.hlog = radio, routines, hlog

    def playing(self) -> bool:
        """Anything the house plays right now: a song, Spotify or a book."""
        try:
            return bool(self.radio is not None and getattr(self.radio, "playing", False))
        except Exception:                                          # noqa: BLE001
            return False

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
        name = self.routine()
        if name:
            lines.append(f"The {name} routine is running right now; its music step starts the library unless he has already said stop.")
        try:
            lines.append(recent_line(self.hlog.recent()) if self.hlog is not None else "")
        except Exception:                                          # noqa: BLE001
            pass
        return " ".join(x for x in lines if x)
