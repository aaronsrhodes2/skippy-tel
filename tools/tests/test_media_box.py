"""tools/media_box.py: the pure parts (what to install, which splits, the pins). The adb and download parts are exercised against the real box by hand."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_box as mb  # noqa: E402


def test_the_real_manifest_loads_and_every_app_is_pinned():
    m = mb.load_manifest()
    assert m["name"] and m["host"].count(".") == 3
    assert {a["package"] for a in m["apps"]} >= {"com.limelight", "com.plexapp.android"}
    assert all(len(a["sha256"]) == 64 for a in m["apps"])


def test_a_manifest_without_a_sha_pin_is_refused(tmp_path):
    bad = {"name": "x", "host": "1.2.3.4", "remove": [], "apps": [{"package": "a", "kind": "apk", "sha256": "abc"}]}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(bad))
    try:
        mb.load_manifest(p)
    except ValueError as e:
        assert "SHA-256" in str(e)
    else:
        raise AssertionError("an unpinned app was accepted")


def test_mdns_finds_only_this_boxs_connect_service():
    out = ("List of discovered mdns services\n"
           "adb-ZX24\t_adb-tls-pairing._tcp\t192.168.0.69:34791\n"
           "adb-ZX24\t_adb-tls-connect._tcp\t192.168.0.69:36453\n"
           "adb-OTHER\t_adb-tls-connect._tcp\t192.168.0.50:40000\n")
    assert mb.parse_mdns(out, "192.168.0.69") == "192.168.0.69:36453"
    assert mb.parse_mdns(out, "192.168.0.7") is None


def test_plan_installs_what_is_missing_or_not_the_pinned_file_and_removes_only_what_is_there():
    m = {"apps": [{"package": "a", "sha256": "1" * 64}, {"package": "b", "sha256": "2" * 64}], "remove": ["junk", "gone"]}
    todo = mb.plan(m, {"a", "b", "junk"}, {"a": "1" * 64, "b": "0" * 64})
    assert [a["package"] for a in todo["install"]] == ["b"]            # b is on the box but not the pinned file
    assert todo["remove"] == ["junk"]                                  # "gone" is not there
    assert mb.plan(m, {"a", "b"}, {"a": "1" * 64, "b": "2" * 64}) == {"install": [], "remove": []}


def test_splits_are_base_first_then_only_the_wanted_ones():
    names = ["base.apk", "split_config.en.apk", "split_config.x86.apk", "split_config.arm64_v8a.apk"]
    assert mb.pick_splits(names, ["arm64_v8a", "en", "hdpi"]) == ["base.apk", "split_config.arm64_v8a.apk", "split_config.en.apk"]


def test_a_cached_file_counts_only_when_its_hash_is_the_pin(tmp_path):
    data = b"hello"
    sha = hashlib.sha256(data).hexdigest()
    app = {"package": "a", "kind": "apk", "sha256": sha}
    assert mb.cached(app, tmp_path) is None
    (tmp_path / f"{sha}.apk").write_bytes(b"tampered")
    assert mb.cached(app, tmp_path) is None
    (tmp_path / f"{sha}.apk").write_bytes(data)
    assert mb.cached(app, tmp_path) == tmp_path / f"{sha}.apk"
