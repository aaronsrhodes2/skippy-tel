"""Skippy's face and his animated 3D self: the assets are served, and the pages wear them."""
from test_app import env  # noqa: F401  (the app fixture)


def test_his_assets_are_public_and_the_pages_reference_them(env):
    client, _, _ = env
    for url, magic in (("/static/skippy/skippy-head.png", b"\x89PNG"), ("/static/skippy/skippy-full.png", b"\x89PNG"), ("/static/skippy/skippy.glb", b"glTF")):
        resp = client.get(url)
        assert resp.status_code == 200 and resp.data[:len(magic)] == magic, url
    assert len(client.get("/static/skippy/skippy.glb").data) < 4_000_000                  # a phone must be able to fetch him
    js = client.get("/static/skippy-3d.js").data.decode()
    assert "skippy-speaking" in js and "three@0.160.0" not in js                          # the pinned CDN lives in the template, not the module
