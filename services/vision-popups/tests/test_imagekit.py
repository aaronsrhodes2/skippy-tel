"""imagekit: what comes in, what goes back, and the geometry the tools share. No GPU, no model."""
import base64
import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import imagekit as ik  # noqa: E402


def png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_decode_takes_bytes_base64_and_data_urls():
    raw = png(Image.new("RGB", (8, 6), (10, 20, 30)))
    for data in (raw, base64.b64encode(raw).decode(), "data:image/png;base64," + base64.b64encode(raw).decode()):
        rgb, alpha = ik.decode(data)
        assert rgb.size == (8, 6) and rgb.mode == "RGB" and alpha is None


def test_decode_keeps_real_transparency_only():
    see_through = Image.new("RGBA", (4, 4), (255, 0, 0, 0))
    _, alpha = ik.decode(png(see_through))
    assert alpha is not None and alpha.getextrema() == (0, 0)
    _, alpha = ik.decode(png(Image.new("RGBA", (4, 4), (255, 0, 0, 255))))
    assert alpha is None


@pytest.mark.parametrize("data, why", [(b"", "needed"), (b"not a picture", "isn't a picture"), ("%%%", "picture")])
def test_decode_refuses_junk_as_bad_input(data, why):
    with pytest.raises(ik.BadInput, match=why):
        ik.decode(data)


def test_decode_refuses_more_than_the_limit():
    with pytest.raises(ik.BadInput, match="too big"):
        ik.decode(b"\0" * (ik.MAX_INPUT_BYTES + 1))


def test_input_limit_takes_everything_the_chat_and_the_agent_send():
    """A picture one hop accepts must not be refused here (portal/tests/test_limit_chains.py's rule): the chat's ~1 MB
    attachments and the agent's 12 MB screenshots."""
    assert ik.MAX_INPUT_BYTES >= 12 * 1024 * 1024


def test_results_fit_what_the_reporter_hands_the_portal():
    assert ik.MAX_OUTPUT_BYTES <= 25_000_000          # reporter.CLIP_MAX


def test_encode_is_png_and_falls_back_when_too_big(monkeypatch):
    noise = Image.fromarray(np.random.default_rng(0).integers(0, 255, (300, 300, 3), dtype=np.uint8))
    out = ik.encode(noise, "x")
    assert out["mime"] == "image/png" and out["bytes"].startswith(b"\x89PNG")
    monkeypatch.setattr(ik, "MAX_OUTPUT_BYTES", 150_000)
    out = ik.encode(noise, "x")
    assert out["mime"] in ("image/webp", "image/jpeg") and len(out["bytes"]) <= 150_000
    j = ik.as_json(out)
    assert base64.b64decode(j["b64"]) == out["bytes"] and j["name"] == "x"


def test_on_black_is_pure_black_where_the_subject_isnt():
    rgb = Image.new("RGB", (4, 1), (200, 100, 50))
    alpha = Image.fromarray(np.array([[0, 255, 128, 0]], dtype=np.uint8), "L")
    a = np.asarray(ik.on_black(rgb, alpha))
    assert (a[0, 0] == 0).all() and (a[0, 3] == 0).all()        # #000000: see-through on the Viture glasses
    assert tuple(a[0, 1]) == (200, 100, 50)


def test_lift_keeps_a_dark_subject_visible_on_the_glasses():
    rgb = Image.new("RGB", (2, 1), (0, 0, 0))
    alpha = Image.fromarray(np.array([[255, 0]], dtype=np.uint8), "L")
    a = np.asarray(ik.on_black(rgb, alpha, lift=32))
    assert a[0, 0].min() == 32 and (a[0, 1] == 0).all()


def test_cutout_carries_the_matte():
    rgb = Image.new("RGB", (2, 2), (1, 2, 3))
    alpha = Image.new("L", (2, 2), 77)
    out = ik.cutout(rgb, alpha)
    assert out.mode == "RGBA" and out.getpixel((0, 0)) == (1, 2, 3, 77)


def test_normals_face_the_viewer_on_a_flat_map_and_tilt_on_a_ramp():
    flat = np.asarray(ik.normals_from_depth(np.ones((16, 16))))
    assert (abs(flat.astype(int) - (128, 128, 255)) <= 1).all()
    ramp = np.asarray(ik.normals_from_depth(np.tile(np.arange(32, dtype=float), (16, 1))))
    assert ramp[8, 16, 0] < 124     # rising to the right: the normal leans left


def test_depth_maps_cover_the_full_range():
    d = np.linspace(2.0, 5.0, 100).reshape(10, 10)
    g16, g8 = ik.gray16(d), ik.gray8(d)
    assert np.asarray(g16).min() == 0 and np.asarray(g16).max() == 65535
    assert g8.mode == "L" and np.asarray(g8).max() == 255
    assert np.asarray(ik.gray8(np.zeros((3, 3)))).max() == 0


def test_dedupe_keeps_the_best_of_overlapping_masks_and_drops_specks():
    a = np.zeros((10, 10), bool); a[:5, :5] = True
    b = a.copy(); b[5, 0] = True                          # nearly the same as a
    c = np.zeros((10, 10), bool); c[6:, 6:] = True
    speck = np.zeros((10, 10), bool); speck[9, 0] = True
    kept = ik.dedupe_masks([a, b, c, speck], [0.9, 0.95, 0.85, 0.99], 0.7, min_area=2)
    assert kept == [1, 2]


def test_mask_bbox():
    m = np.zeros((10, 10), bool); m[2:4, 3:7] = True
    assert ik.mask_bbox(m) == [3, 2, 7, 4]
    assert ik.mask_bbox(np.zeros((3, 3), bool)) is None


@pytest.mark.parametrize("said, prompt", [
    ("find the mugs", "mugs."),
    ("Find the mugs and the keyboard", "mugs. keyboard."),
    ("cat, a remote; dog", "cat. remote. dog."),
    ("where are the red cups", "red cups."),
])
def test_detection_prompt(said, prompt):
    assert ik.detection_prompt(said) == prompt


def test_detection_prompt_needs_words():
    with pytest.raises(ik.BadInput):
        ik.detection_prompt("find the")


def test_fit_side_only_shrinks():
    img = Image.new("RGB", (4000, 1000))
    assert ik.fit_side(img, 2000).size == (2000, 500)
    assert ik.fit_side(img, 5000).size == (4000, 1000)


def test_overlay_and_boxes_draw_without_changing_size():
    rgb = Image.new("RGB", (64, 48))
    m = np.zeros((48, 64), bool); m[10:20, 10:20] = True
    assert ik.overlay_masks(rgb, [m]).size == (64, 48)
    out = ik.draw_boxes(rgb, [{"label": "mug", "score": 0.9, "box": [5, 20, 30, 40]}])
    assert out.size == (64, 48) and out.getpixel((5, 30)) == ik.PALETTE[0]


def test_paddle_tables_become_html():
    import tools
    otsl = "<fcel>Item<fcel>项目<nl><fcel>A & B<ecel><nl><fcel>Total<lcel><nl>"
    out = tools.otsl_to_html(otsl)
    assert out.splitlines() == ["<table>", "<tr><td>Item</td><td>项目</td></tr>", "<tr><td>A &amp; B</td><td></td></tr>",
                                '<tr><td colspan="2">Total</td></tr>', "</table>"]
    assert tools.otsl_to_html("plain words") == "plain words"
