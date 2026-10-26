import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sticker_split_builder", ROOT / "tools/split_official_pack.py")
splitter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(splitter)


def test_split_preserves_exact_archive_bytes_and_checksums(tmp_path):
    original = b"archive-fixture\x00\xff" * 9
    source = tmp_path / "source.zip"
    source.write_bytes(original)
    manifest_path = splitter.split_pack(source, tmp_path / "parts", chunk_bytes=31)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["size"] == len(original)
    assert manifest["sha256"] == hashlib.sha256(original).hexdigest()
    chunks = [(manifest_path.parent / row["name"]).read_bytes() for row in manifest["parts"]]
    assert b"".join(chunks) == original
    for row, chunk in zip(manifest["parts"], chunks):
        assert len(chunk) == row["size"] <= 31
        assert hashlib.sha256(chunk).hexdigest() == row["sha256"]
    assert source.read_bytes() == original


def test_bundled_chunks_fit_github_and_match_reviewed_v3_archive():
    folder = ROOT / "official"
    manifest = json.loads((folder / "official_pack.zip.parts.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    total = 0
    for row in manifest["parts"]:
        chunk = (folder / row["name"]).read_bytes()
        assert 0 < len(chunk) <= splitter.CHUNK_BYTES < 100 * 1024 * 1024
        assert len(chunk) == row["size"]
        assert hashlib.sha256(chunk).hexdigest() == row["sha256"]
        digest.update(chunk)
        total += len(chunk)
    assert total == manifest["size"] == 119192210
    assert digest.hexdigest() == manifest["sha256"] == "38916cd97f21b2f6f3169117a762c0311d532f45028567b39eefa1523f64e568"
