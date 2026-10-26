from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter, merge_character_evidence
from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack, git_blob_hash
from plugin.plugins.hsr_companion.tests.test_experience_fixes import catalog


def make_pack(tmp_path, data=None):
    if data is None:
        stream = BytesIO()
        Image.new("RGB", (64, 64), "red").save(stream, format="PNG")
        data = stream.getvalue()
    manifest = {
        "revision": "1" * 40,
        "source_name": "test source",
        "source_url": "https://github.com/OneDragon-Anything/StarRailOneDragon",
        "license_id": "GPL-3.0",
        "license_url": "https://raw.githubusercontent.com/OneDragon-Anything/StarRailOneDragon/"
        + "1" * 40
        + "/LICENSE",
        "templates": [
            {
                "entity_id": "character.silverwolf",
                "name": "银狼",
                "path": "assets/template/character_avatar/silverwolf/raw.png",
                "git_blob_sha1": git_blob_hash(data),
                "bytes": len(data),
            }
        ],
    }
    return AvatarTemplatePack(manifest, root=tmp_path), data


def download_for(data):
    return lambda url: b"GNU GENERAL PUBLIC LICENSE\nVersion 3" if url.endswith("/LICENSE") else data


def test_templates_are_external_and_hash_verified(tmp_path):
    pack, data = make_pack(tmp_path)
    assert not pack.status()["installed"]
    assert pack.install(downloader=download_for(data))["installed"]
    assert pack.root.is_relative_to(tmp_path)
    assert (pack.root / "LICENSE.txt").is_file()
    assert (pack.root / "SOURCE.json").is_file()
    pack._path(pack.manifest["templates"][0]).write_bytes(b"corrupt")
    assert not AvatarTemplatePack(pack.manifest, root=tmp_path).status()["installed"]


def test_failed_template_download_is_not_reported_ready(tmp_path):
    pack, _ = make_pack(tmp_path)
    status = pack.install(downloader=download_for(b"wrong bytes"))
    assert status["state"] == "error"
    assert status["installed"] is False
    assert "文字识别仍可使用" in status["message"]


def test_template_paths_cannot_escape_or_fetch_arbitrary_urls(tmp_path):
    pack, data = make_pack(tmp_path)
    pack.manifest["templates"][0]["path"] = "../../outside.png"
    assert pack.install(downloader=download_for(data))["state"] == "error"


def test_no_avatar_pack_degrades_to_ocr(tmp_path):
    pack, _ = make_pack(tmp_path)
    roles, status = pack.match(Image.new("RGB", (1280, 800)), page_type="unknown")
    assert roles == []
    assert status["reason"] == "portrait_pack_missing"


def test_conflicting_hud_evidence_is_not_promoted(tmp_path):
    db = catalog(tmp_path)
    silver = db.resolve_character("银狼")
    sparkle = db.resolve_character("花火")
    region = {"left": 0.86, "right": 0.95, "top": 0.3, "bottom": 0.4}
    label = {"entity_id": sparkle["id"], "name": sparkle["name"], "identity_scope": "hud_label", "region": region}
    portrait = {"entity_id": silver["id"], "name": silver["name"], "identity_scope": "hud_portrait", "region": region}
    roles, conflicts = merge_character_evidence([label], [portrait], db)
    assert roles == []
    assert conflicts == 1
    roles, _ = merge_character_evidence([], [{**portrait, "entity_id": "invented"}], db)
    assert roles == []


def test_portrait_path_can_work_when_ocr_is_unavailable(tmp_path, monkeypatch):
    from types import SimpleNamespace

    db = catalog(tmp_path)
    silver = db.resolve_character("银狼")
    role = {
        "entity_id": silver["id"],
        "name": silver["name"],
        "identity_scope": "hud_portrait",
        "region": {"left": 0.8, "right": 0.9, "top": 0.2, "bottom": 0.3},
    }
    adapter = LocalVisionAdapter(
        db, avatar_pack=SimpleNamespace(match=lambda image, **kwargs: ([role], {"available": True}))
    )
    monkeypatch.setattr(adapter, "_get_backend", lambda: (_ for _ in ()).throw(ImportError("OCR unavailable")))
    image = BytesIO()
    Image.new("RGB", (1280, 800)).save(image, format="PNG")
    result = adapter.analyze(image.getvalue())
    assert result["ok"] is True
    assert result["characters"][0]["name"] == "银狼"
    assert result["scene"]["primary_state"] == "unknown"
    assert "OCR unavailable" in result["ocr"]["error"]


def test_opencv_geometry_rejects_flat_or_unrelated_hud(tmp_path):
    cv2 = pytest.importorskip("cv2", reason="Requires the isolated optional vision test runtime")
    import numpy as np

    rng = np.random.default_rng(1234)
    texture = rng.integers(0, 256, (120, 100, 3), dtype=np.uint8)
    texture = cv2.GaussianBlur(texture, (3, 3), 0.8)
    payload = BytesIO()
    Image.fromarray(texture).save(payload, format="PNG")
    pack, data = make_pack(tmp_path, payload.getvalue())
    assert pack.install(downloader=download_for(data))["installed"]
    image = Image.new("RGB", (1280, 800), "gray")
    image.paste(Image.fromarray(texture), (1140, 200))
    roles, status = pack.match(image, page_type="unknown")
    assert status["available"], status
    assert [item["name"] for item in roles] == ["银狼"]
    roles, _ = pack.match(Image.new("RGB", (1280, 800), "gray"), page_type="unknown")
    assert roles == []
    unrelated = Image.new("RGB", (1280, 800), "gray")
    unrelated.paste(Image.fromarray(rng.integers(0, 256, texture.shape, dtype=np.uint8)), (1140, 200))
    assert pack.match(unrelated, page_type="unknown")[0] == []
    assert pack.match(image, page_type="story_dialogue")[0] == []
