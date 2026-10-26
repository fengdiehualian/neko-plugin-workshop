from __future__ import annotations

import json
import zipfile

import pytest
from conftest import GIF_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.catalog import content_sha256
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.services.official_delivery import OfficialDelivery

from .test_media import animated_webp


def make_pack(path, original, variant, *, corrupt=False):
    manifest = {"stickers": [{"sha256": content_sha256(original), "delivery_file": "send.webp",
                              "delivery_sha256": content_sha256(variant) if not corrupt else "0" * 64}]}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("delivery/send.webp", variant)
    return path


def test_sender_uses_variant_but_never_replaces_collected_original(tmp_path, run_async):
    original = GIF_BYTES + b"x" * (300 * 1024)
    variant = animated_webp()
    pack = make_pack(tmp_path / "official.zip", original, variant)
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path / "library"))
    plugin._official_pack_path = lambda: pack
    lib = plugin._library
    assert lib.load().ok
    sticker, error = lib.add(data=original, desc="original", tags=[])
    assert not error
    result = run_async(plugin._sender.send(sticker, lanlan="K", source="tool",
                                           settings=StickerManagerSettings(enabled=True)))
    assert result.ok and host.push.calls[0]["parts"][0]["data"] == variant
    assert host.push.calls[0]["parts"][0]["mime"] == "image/webp"
    assert lib.image_path(sticker).read_bytes() == original
    assert lib.get(sticker.id).sha256 == content_sha256(original)
    assert not host.images.calls


def test_unknown_image_is_not_matched_by_filename(tmp_path):
    resolver = OfficialDelivery(make_pack(tmp_path / "pack.zip", GIF_BYTES, animated_webp()))
    assert resolver.resolve(GIF_BYTES + b"other") is None


def test_variant_integrity_failure_is_not_silently_flattened(tmp_path):
    resolver = OfficialDelivery(make_pack(tmp_path / "pack.zip", GIF_BYTES, animated_webp(), corrupt=True))
    with pytest.raises(ValueError, match="digest"):
        resolver.resolve(GIF_BYTES)


def test_missing_optional_pack_preserves_nonofficial_image_path(tmp_path):
    assert OfficialDelivery(tmp_path / "absent.zip").resolve(GIF_BYTES) is None
