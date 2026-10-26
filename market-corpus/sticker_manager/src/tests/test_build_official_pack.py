import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sticker_official_builder", ROOT / "tools" / "build_official_pack.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def _template(tmp_path, *, digest=None, file="a.gif", version=1):
    source = tmp_path / "source"
    source.mkdir()
    buffer = io.BytesIO()
    frames = [Image.new("RGBA", (24, 24), color) for color in ("red", "blue")]
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=[50, 120], loop=0)
    original = buffer.getvalue()
    old = b"GIF89a" + b"compressed-frames"
    (source / "a.gif").write_bytes(original)
    template = tmp_path / "template.zip"
    manifest = {
        "version": 3,
        "app": "sticker_manager",
        "pack_version": version,
        "groups": [{"name": "g", "desc": "group caption"}],
        "stickers": [{
            "file": file, "sha256": digest or builder.digest(old), "desc": "caption",
            "caption": "meaning", "tags": ["tag"], "group": "g", "visible_text": "",
        }],
    }
    with zipfile.ZipFile(template, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("stickers/" + file, old)
    return source, template, original, old


def test_preserves_motion_and_metadata_with_alias(tmp_path):
    source, template, original, old = _template(tmp_path)
    target = tmp_path / "official.zip"
    report = builder.build_official_pack(source, template, target)
    assert report["entries"] == 1 and report["version"] == 2
    assert report["delivery_bytes"] <= builder.BUDGET
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert archive.read("stickers/a.gif") == original
        encoded = archive.read("delivery/a.webp")
    source_frames, source_durations, source_loop = builder.frames_of(original)
    output_frames, output_durations, output_loop = builder.frames_of(encoded)
    assert output_durations == source_durations and output_loop == source_loop
    assert [frame.tobytes() for frame in source_frames] == [frame.tobytes() for frame in output_frames]
    entry = manifest["stickers"][0]
    assert entry["sha256"] == builder.digest(original)
    assert entry["delivery_sha256"] == builder.digest(encoded)
    assert entry["source_sha256"] == builder.digest(original)
    assert entry["legacy_sha256"] == [builder.digest(old)]
    assert entry["desc"] == "caption" and entry["caption"] == "meaning"
    assert entry["tags"] == ["tag"] and entry["group"] == "g"
    assert manifest["groups"] == [{"name": "g", "desc": "group caption"}]


def test_rebuild_preserves_history_without_aliasing_current_image(tmp_path):
    source, template, _, old = _template(tmp_path)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    builder.build_official_pack(source, template, first)
    builder.build_official_pack(source, first, second)
    with zipfile.ZipFile(second) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["pack_version"] == 3
    assert manifest["stickers"][0]["legacy_sha256"] == [builder.digest(old)]


def test_invalid_template_does_not_replace_existing_output(tmp_path):
    source, template, _, _ = _template(tmp_path, digest="0" * 64)
    target = tmp_path / "official.zip"
    target.write_bytes(b"existing")
    with pytest.raises(ValueError, match="digest mismatch"):
        builder.build_official_pack(source, template, target)
    assert target.read_bytes() == b"existing"


@pytest.mark.parametrize("file", ["../a.gif", "nested/a.gif", "C:a.gif", ".hidden.gif"])
def test_unsafe_source_names_are_rejected(tmp_path, file):
    source, template, _, _ = _template(tmp_path, file=file)
    with pytest.raises(ValueError):
        builder.build_official_pack(source, template, tmp_path / "output.zip")


def test_version_must_increase(tmp_path):
    source, template, _, _ = _template(tmp_path)
    with pytest.raises(ValueError, match="version must increase"):
        builder.build_official_pack(source, template, tmp_path / "output.zip", version=1)
