"""Test helpers: tiny real files, so mutagen and Pillow are exercised for real (the gate's tests aren't in CI, so a
dependency on both is fine here)."""
import io

FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413     # one MPEG-1 Layer III frame, 128 kbps, 44.1 kHz (417 bytes)


def make_mp3(path, cover: bytes | None = None, cover_mime: str = "image/jpeg", v2_version: int = 4, pictures=(), **frames):
    """An MP3 mutagen accepts (a few silent frames), with ID3 text frames (TIT2="x", TDRC="2016"...) and optionally a cover
    (a front cover), plus any number of other pictures as (data, picture type 0-20) pairs, which are stored BEFORE the cover."""
    from mutagen import id3
    path.write_bytes(FRAME * 8)
    tags = id3.ID3()
    for name, text in frames.items():
        tags.add(getattr(id3, name)(encoding=3, text=[text]))
    for n, (data, kind) in enumerate(pictures):                  # these go in first, so "the front cover wins" is a real test
        tags.add(id3.APIC(encoding=3, mime="image/jpeg", type=kind, desc=f"p{n}", data=data))   # the desc keeps each its own frame
    if cover is not None:
        tags.add(id3.APIC(encoding=3, mime=cover_mime, type=3, desc="front", data=cover))
    tags.save(path, v2_version=v2_version)
    return path


def image_bytes(size=(600, 400), fmt="JPEG", mode="RGB", color=(200, 30, 30), **save):
    from PIL import Image
    im = Image.new(mode, size, color)
    out = io.BytesIO()
    im.save(out, fmt, **save)
    return out.getvalue()
