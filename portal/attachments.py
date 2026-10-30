"""Files that come with a chat message (the clip key, paste or drop on the page): pictures, text and code, PDFs.

Nothing from the page is trusted: each file is a base64 data URL with a name, checked against its declared type (a picture
must BE a jpeg/png/webp by its own first bytes, a PDF must start %PDF, text must decode as UTF-8) and against the size
limits. Then it is shaped for the brain that will read it:

  anthropic_blocks()  Claude: image blocks, a PDF as a document block, text files as text blocks.
  text_for()          a text-only brain (Skippy Core's chat slot): text and PDF text inline; each picture becomes the
                      vision slot's description of it (`describe(picture) -> str`), so a guest's brain can see too.
  openai_parts()      Gemini (OpenAI-compatible): image_url parts and text.

Files live only for the message they came with (the page keeps a note of the name in the history, never the bytes).
"""
from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass

MAX_FILES = 4
MAX_B64_EACH = 1_400_000         # about 1 MB of file (the page shrinks pictures before sending)
MAX_B64_TOTAL = 3_600_000
MAX_TEXT_CHARS = 60_000          # what of one text file or PDF goes to a model (about 15k tokens)
MAX_PDF_PAGES = 40

IMAGE_MAGIC = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n", "image/webp": b"RIFF"}
# Sounds, for the pop-ups that take one (popups.py: stem separation, transcription). No brain hears them: each is a line naming it.
AUDIO_TYPES = {"audio/wav": "audio/wav", "audio/x-wav": "audio/wav", "audio/wave": "audio/wav", "audio/mpeg": "audio/mpeg",
               "audio/mp3": "audio/mpeg", "audio/flac": "audio/flac", "audio/x-flac": "audio/flac", "audio/ogg": "audio/ogg"}
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "text/html", "text/css", "text/x-python", "application/json",
              "application/javascript", "text/javascript", "application/xml", "text/xml", "application/x-yaml", "text/yaml"}
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".yml", ".yaml", ".xml", ".sh",
                 ".kt", ".java", ".c", ".h", ".cpp", ".rs", ".go", ".sql", ".toml", ".ini", ".log", ".tsx", ".jsx")
_DATA_URL = re.compile(r"data:([a-z0-9.+/-]+)(?:;[a-z0-9=-]+)*;base64,([A-Za-z0-9+/]+={0,2})", re.I)
_NAME = re.compile(r"[^\w .()+-]")


def _is_audio(mime: str, data: bytes) -> bool:
    return {"audio/wav": data[:4] == b"RIFF" and data[8:12] == b"WAVE", "audio/flac": data[:4] == b"fLaC", "audio/ogg": data[:4] == b"OggS",
            "audio/mpeg": data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)}[mime]


@dataclass(frozen=True)
class File:
    name: str
    kind: str            # "image" | "pdf" | "text" | "audio" | "home" (a big file kept by skippy-pc's popup-gate: `ref`, no bytes here)
    mime: str
    data: bytes
    ref: str = ""
    size: int = 0

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode()

    def text(self) -> str:
        """The words in a text file or a PDF ('' for a picture), cut to MAX_TEXT_CHARS."""
        if self.kind == "text":
            return self.data.decode("utf-8")[:MAX_TEXT_CHARS]
        if self.kind == "pdf":
            from pypdf import PdfReader
            try:
                reader = PdfReader(io.BytesIO(self.data))
                pages = [(p.extract_text() or "") for p in reader.pages[:MAX_PDF_PAGES]]
            except Exception:  # noqa: BLE001 — a PDF pypdf can't read is said so, not a crash
                return "(this PDF couldn't be read as text)"
            more = f"\n(only the first {MAX_PDF_PAGES} pages)" if len(reader.pages) > MAX_PDF_PAGES else ""
            return ("\n\n".join(pages).strip() or "(no text in this PDF: it may be scanned images)")[:MAX_TEXT_CHARS] + more
        return ""


def clean(raw) -> list[File]:
    """[{name, data: data URL}] from the page → checked Files, or ValueError saying what is wrong."""
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_FILES:
        raise ValueError(f"At most {MAX_FILES} files to a message.")
    out, total = [], 0
    for item in raw:
        if isinstance(item, dict) and "ref" in item:          # a big file the page sent home first (popup_in.py): its id, never its bytes
            out.append(_home_file(item))
            continue
        if not isinstance(item, dict) or not isinstance(item.get("data"), str):
            raise ValueError("A file didn't arrive whole.")
        name = _NAME.sub("_", str(item.get("name") or "file"))[:80] or "file"
        m = _DATA_URL.fullmatch(item["data"])
        if not m:
            raise ValueError(f"{name}: not a file this chat can read.")
        mime, b64 = m.group(1).lower(), m.group(2)
        total += len(b64)
        if len(b64) > MAX_B64_EACH or total > MAX_B64_TOTAL:
            raise ValueError("Those files are too big: try fewer or smaller ones (about 1 MB each).")
        try:
            data = base64.b64decode(b64, validate=True)
        except ValueError:
            raise ValueError(f"{name} was damaged on the way.") from None
        if mime in IMAGE_MAGIC:
            if not data.startswith(IMAGE_MAGIC[mime]):
                raise ValueError(f"{name} isn't the picture it says it is.")
            out.append(File(name, "image", mime, data))
        elif mime in AUDIO_TYPES:
            if not _is_audio(AUDIO_TYPES[mime], data):
                raise ValueError(f"{name} isn't the sound it says it is.")
            out.append(File(name, "audio", AUDIO_TYPES[mime], data))
        elif mime == "application/pdf" or name.lower().endswith(".pdf"):
            if not data.startswith(b"%PDF"):
                raise ValueError(f"{name} isn't a PDF.")
            out.append(File(name, "pdf", "application/pdf", data))
        elif mime in TEXT_TYPES or mime.startswith("text/") or name.lower().endswith(TEXT_SUFFIXES):
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError(f"{name} isn't plain text.") from None
            out.append(File(name, "text", "text/plain", data))
        else:
            raise ValueError(f"{name}: pictures, sounds, PDFs and text or code files only.")
    return out


HOME_TYPES = {"audio/wav", "audio/mpeg", "audio/flac", "audio/ogg", "image/png", "image/jpeg", "image/webp"}   # popup-gate's own


def _home_file(item: dict) -> File:
    name = _NAME.sub("_", str(item.get("name") or "file"))[:80] or "file"
    ref, mime, size = item.get("ref"), str(item.get("type") or "").lower(), item.get("bytes")
    if (not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{32}", ref) or mime not in HOME_TYPES
            or isinstance(size, bool) or not isinstance(size, int) or size <= 0):
        raise ValueError(f"{name} didn't arrive whole.")
    return File(name, "home", mime, b"", ref=ref, size=size)


def note(files: list[File]) -> str:
    """What the history keeps of a message's files: their names, never their bytes."""
    return "\n[attached: " + ", ".join(f.name for f in files) + "]" if files else ""


def _wrap(f: File, body: str) -> str:
    return f"<file name={f.name!r}>\n{body}\n</file>"


def _heard(f: File) -> str:
    if f.kind == "home":
        return _wrap(f, f"A {'picture' if f.mime.startswith('image/') else 'sound'} file ({f.mime}, {f.size / 1048576:.1f} MB), kept at "
                        "home for a tool. You can't see or hear it; a tool that takes an attachment can use it.")
    return _wrap(f, f"A sound file ({f.mime}, {len(f.data) // 1024} KB). You can't hear it; a tool that takes an attachment can use it.")


def anthropic_blocks(files: list[File], text: str) -> list[dict]:
    blocks = []
    for f in files:
        if f.kind == "image":
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": f.mime, "data": f.b64}})
        elif f.kind == "pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": f.b64},
                           "title": f.name})
        elif f.kind in ("audio", "home"):
            blocks.append({"type": "text", "text": _heard(f)})
        else:
            blocks.append({"type": "text", "text": _wrap(f, f.text())})
    return blocks + [{"type": "text", "text": text}]


def text_for(files: list[File], text: str, describe) -> str:
    parts = []
    for f in files:
        if f.kind == "image":
            parts.append(_wrap(f, "A picture. What the vision model sees in it:\n" + describe(f)))
        elif f.kind in ("audio", "home"):
            parts.append(_heard(f))
        else:
            parts.append(_wrap(f, f.text()))
    return "\n\n".join(parts + [text])


def openai_parts(files: list[File], text: str) -> list[dict]:
    parts = []
    for f in files:
        if f.kind == "image":
            parts.append({"type": "image_url", "image_url": {"url": f"data:{f.mime};base64,{f.b64}"}})
        elif f.kind in ("audio", "home"):
            parts.append({"type": "text", "text": _heard(f)})
        else:
            parts.append({"type": "text", "text": _wrap(f, f.text())})
    return parts + [{"type": "text", "text": text}]


DESCRIBE_PROMPT = ("Describe this picture precisely for someone who can't see it and needs every detail: what it is, the "
                   "layout from top to bottom, all text in it word for word, colours, counts, and anything unusual. No opinions.")
