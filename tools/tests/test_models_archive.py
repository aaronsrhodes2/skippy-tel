"""The archive tool's promises: verify catches corruption/missing/extra files, the
manifest is incremental, an Ollama blob is checked against its own name, and merging
stores never overwrites or copies half-downloaded blobs."""
import hashlib
import json
from pathlib import Path

import models_archive as ma


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _archive(tmp_path):
    root = tmp_path / "models"
    _write(root / "finetunes" / "mine" / "adapter.safetensors", b"my precious lora")
    _write(root / "huggingface" / "Org__Repo" / "model.safetensors", b"weights" * 100)
    (root / "ma").mkdir(exist_ok=True)
    manifest = ma.build_manifest(root)
    (root / ma.MANIFEST).write_text(json.dumps(manifest))
    return root


def test_a_clean_archive_verifies(tmp_path):
    root = _archive(tmp_path)
    assert ma.verify(root) == [] and ma.verify(root, quick=True) == []


def test_verify_catches_corruption_missing_and_extra_files(tmp_path):
    root = _archive(tmp_path)
    (root / "finetunes" / "mine" / "adapter.safetensors").write_bytes(b"my precious LORA")   # same size, flipped bytes
    (root / "huggingface" / "Org__Repo" / "model.safetensors").unlink()
    _write(root / "notes.txt", b"new")
    problems = ma.verify(root)
    assert any(p.startswith("CORRUPT") and "adapter" in p for p in problems)
    assert any(p.startswith("MISSING") and "model.safetensors" in p for p in problems)
    assert any(p.startswith("EXTRA") and "notes.txt" in p for p in problems)
    assert not any("adapter" in p for p in ma.verify(root, quick=True))   # quick mode is sizes only, by design


def test_verify_says_so_when_there_is_no_manifest(tmp_path):
    assert "run `manifest` first" in ma.verify(tmp_path)[0]


def test_the_manifest_is_incremental(tmp_path, monkeypatch):
    root = _archive(tmp_path)
    previous = json.loads((root / ma.MANIFEST).read_text())
    _write(root / "finetunes" / "mine" / "new.bin", b"new file")
    reads = []
    real = ma.sha256_file
    monkeypatch.setattr(ma, "sha256_file", lambda p: reads.append(p.name) or real(p))
    manifest = ma.build_manifest(root, previous)
    assert reads == ["new.bin"]                                  # nothing else was re-read
    assert len(manifest["files"]) == 3


def test_partial_downloads_and_bookkeeping_are_not_archived(tmp_path):
    root = tmp_path / "models"
    _write(root / "ollama" / "models" / "blobs" / "sha256-abc-partial", b"half")
    _write(root / "huggingface" / "A__B" / ".cache" / "huggingface" / "download" / "x.metadata", b"rev")
    _write(root / "huggingface" / "A__B" / "w.bin", b"w")
    assert [rel for rel, _ in ma.files_under(root)] == ["huggingface/A__B/w.bin"]


def _ollama_store(root, name_parts, layers, present=True):
    store = root / "ollama" / "models"
    doc = {"config": {"digest": "sha256:" + _digest(b"cfg"), "size": 3},
           "layers": [{"digest": "sha256:" + _digest(d), "size": len(d)} for d in layers]}
    manifest = store / "manifests" / "registry.ollama.ai" / Path(*name_parts)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(doc))
    if present:
        for data in [b"cfg", *layers]:
            _write(store / "blobs" / f"sha256-{_digest(data)}", data)
    return store


def test_ollama_models_are_named_sized_and_checked_for_missing_blobs(tmp_path):
    store = _ollama_store(tmp_path, ["library", "qwen3", "8b"], [b"weights-a", b"template"])
    _ollama_store(tmp_path, ["hf.co", "someone", "repo", "Q4_K_M"], [b"gguf"])
    models = ma.ollama_models(store)
    assert set(models) == {"qwen3:8b", "hf.co/someone/repo:Q4_K_M"}
    assert models["qwen3:8b"] == {"size": 3 + len(b"weights-a") + len(b"template"), "missing": []}
    (store / "blobs" / f"sha256-{_digest(b'weights-a')}").unlink()
    assert ma.ollama_models(store)["qwen3:8b"]["missing"] == ["sha256:" + _digest(b"weights-a")]


def test_verify_flags_an_ollama_model_with_a_missing_blob(tmp_path):
    root = tmp_path / "models"
    store = _ollama_store(root, ["library", "qwen3", "8b"], [b"weights"])
    (root / ma.MANIFEST).write_text(json.dumps(ma.build_manifest(root)))
    (store / "blobs" / f"sha256-{_digest(b'weights')}").unlink()
    assert any("INCOMPLETE Ollama model qwen3:8b" in p for p in ma.verify(root))


def test_a_blob_whose_bytes_dont_match_its_own_name_is_corrupt_even_if_the_manifest_agrees(tmp_path):
    root = tmp_path / "models"
    _write(root / "ollama" / "models" / "blobs" / f"sha256-{_digest(b'the real bytes')}", b"rotted bytes!")
    (root / ma.MANIFEST).write_text(json.dumps(ma.build_manifest(root)))   # manifest recorded the rotten file
    assert any("bytes don't match the blob's own name" in p for p in ma.verify(root))


def test_merging_ollama_stores_dedupes_skips_partials_and_never_overwrites(tmp_path):
    a = _ollama_store(tmp_path / "a", ["library", "m", "1"], [b"shared", b"only-a"])
    b = _ollama_store(tmp_path / "b", ["library", "m", "1"], [b"shared", b"only-b"])          # same tag, different content
    _write(b / "blobs" / "sha256-deadbeef-partial", b"half a download")
    dst = tmp_path / "merged"
    assert "copied" in ma.copy_host_store(a, dst)
    report = ma.copy_host_store(b, dst)
    assert "CONFLICT" in report and "kept the first" in report
    blobs = {p.name for p in (dst / "blobs").iterdir()}
    assert f"sha256-{_digest(b'shared')}" in blobs and f"sha256-{_digest(b'only-b')}" in blobs
    assert "sha256-deadbeef-partial" not in blobs
    assert len(blobs) == 4                                       # config, shared, only-a, only-b: "shared" collapsed
    assert ma.copy_host_store(b, dst) == "copied 0 new blobs; CONFLICT (kept the first): ['1']"   # idempotent


def test_hf_revision_comes_from_the_download_notes(tmp_path):
    folder = tmp_path / "A__B"
    _write(folder / ".cache" / "huggingface" / "download" / "w.bin.metadata", b"a1b2c3d4\netag\n1234\n")
    assert ma.hf_revision(folder) == "a1b2c3d4"
    assert ma.hf_revision(tmp_path / "nope") is None
