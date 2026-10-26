from __future__ import annotations

import json
from pathlib import Path

from conftest import FakeHostContext, build_plugin
from sticker_manager.core.catalog import content_sha256, detect_image_format
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.services.bundled_pack import open_pack
from sticker_manager.services.library import Library
from sticker_manager.services.official_assets import validate_official_assets

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "official" / "official_pack.zip"


def test_bundled_originals_and_delivery_are_verified():
    validate_official_assets(PACK)
    assert not PACK.exists(), "Tests must exercise the shipped split archive"
    with open_pack(PACK) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["pack_version"] == 3
        assert manifest["media_policy"] == "original_gif_with_full_motion_delivery"
        assert len(manifest["stickers"]) == 190
        assert len({row["group"] for row in manifest["stickers"]}) == 22
        for row in manifest["stickers"]:
            original = archive.read("stickers/" + row["file"])
            assert row["sha256"] == row["source_sha256"] == content_sha256(original)
            assert detect_image_format(original) == ("gif", "image/gif")
            assert row["legacy_sha256"]


def test_all_official_stickers_send_without_flattening_and_export_roundtrips(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path / "library"))
    plugin._official_pack_path = lambda: PACK
    lib = plugin._library
    assert lib.seed_official(PACK)["imported"] == 190
    before = {sticker.id: sticker.sha256 for sticker in lib.all()}
    settings = StickerManagerSettings(enabled=True, send=SendSettings(cooldown_sec=0, recent_dedup_count=0))
    for sticker in lib.all():
        result = run_async(plugin._sender.send(sticker, lanlan="K", source="tool", settings=settings))
        assert result.ok, (sticker.id, result)
        part = host.push.calls[-1]["parts"][0]
        assert len(part["data"]) <= settings.send.inline_max_bytes
        assert detect_image_format(part["data"])[1] == part["mime"]
        assert part["mime"] in {"image/gif", "image/webp"}
        assert lib.get(sticker.id).sha256 == before[sticker.id]
    assert len(host.push.calls) == 190 and not host.images.calls
    result, error = lib.export_pack()
    assert not error and result["exported"] == 190
    exported = Path(result["file"])
    destination = Library(tmp_path / "reimport")
    assert destination.load().ok
    assert destination.upload_start("originals.zip", size=exported.stat().st_size)[1] == ""
    assert destination.import_pack(exported) == {"imported": 190, "duplicates": 0, "rejected": 0, "failed": 0}
    assert {sticker.sha256 for sticker in destination.all()} == set(before.values())
