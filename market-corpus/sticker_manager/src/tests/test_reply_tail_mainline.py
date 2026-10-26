"""Reply-tail integration with current candidate and media delivery contracts."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import GIF_BYTES, PNG_BYTES
from sticker_manager.core.catalog import content_sha256
from sticker_manager.services.bundled_pack import open_pack

from .test_host_reply_tail import CONTEXT, send, setup
from .test_media import animated_webp
from .test_official_delivery import make_pack


@pytest.mark.parametrize("selector", [{"group": "mixed"}, {"query": "开心"}])
def test_candidates_require_exact_id_before_registering_tail(tmp_path, run_async, selector):
    plugin, host, client, sticker = setup(tmp_path)
    assert not plugin._library.update(sticker.id, desc="开心", group="mixed")[1]
    candidates = run_async(plugin.tool_sticker_send(**selector, _ctx={"host_reply": CONTEXT}))
    assert candidates["candidate_ids"] == [sticker.id] and not candidates["sent"]
    assert not client.calls and not host.push.calls and not plugin._library.read_usage()

    confirmed = run_async(plugin.tool_sticker_send(
        sticker_id=candidates["candidate_ids"][0], _ctx={"host_reply": CONTEXT},
    ))
    assert confirmed["queued"] == sticker.id and not confirmed["sent"]
    assert [call[0] for call in client.calls] == ["register"]
    assert not host.push.calls and plugin._library.get(sticker.id).use_count == 0


def test_empty_selection_keeps_private_continuation_without_registering(tmp_path, run_async):
    plugin, host, client, _ = setup(tmp_path)
    result = run_async(plugin.tool_sticker_send(
        query="不存在的动作情境", _ctx={"host_reply": CONTEXT},
    ))
    assert not result.get("candidate_ids") and not result.get("sent")
    assert "仅供内部判断" in result["note"]
    assert not client.calls and not host.push.calls


@pytest.mark.parametrize("caption", ["", "extra caption"])
@pytest.mark.parametrize("edit,code", [
    ("disable", "sticker_disabled"),
    ("delete", "sticker_file_missing"),
    ("delete_file", "sticker_file_missing"),
    ("switch_zone", "sticker_zone_changed"),
    ("move_zone", "sticker_zone_changed"),
])
def test_tail_upload_revalidates_before_caption_or_registration(
    tmp_path, run_async, edit, code, caption,
):
    plugin, host, client, _ = setup(tmp_path)
    library = plugin._library
    sticker, error = library.add(data=PNG_BYTES + b"x" * (1024 * 1024), desc="large still", tags=[])
    assert not error

    async def upload(*args, **kwargs):
        if edit == "disable":
            assert not library.update(sticker.id, disabled=True)[1]
        elif edit == "delete":
            assert not library.remove(sticker.id)
        elif edit == "delete_file":
            library.image_path(sticker).unlink()
        else:
            zone, error = library.create_zone("other")
            assert not error
            if edit == "switch_zone":
                assert library.activate_zone(zone) == (True, "")
            else:
                assert library.create_group("other group", zone=zone) == (True, "")
                assert not library.update(sticker.id, group="other group")[1]
        return {"type": "image", "url": "https://stub.local/image.jpg"}

    host.images.upload = upload
    result = run_async(send(plugin, sticker, text=caption))
    assert not result.ok and result.code == code and not result.text_submitted
    assert not client.calls and not host.push.calls
    assert not plugin._sender._host_pending and not plugin._sender._inflight
    assert not plugin._sender._last_sent and not library.read_usage()


def test_tail_registration_uses_metadata_updated_during_upload(tmp_path, run_async):
    plugin, host, client, _ = setup(tmp_path)
    sticker, error = plugin._library.add(data=PNG_BYTES + b"x" * (1024 * 1024), desc="large still", tags=[])
    assert not error

    async def upload(*args, **kwargs):
        assert not plugin._library.update(sticker.id, desc="updated")[1]
        return {"type": "image", "url": "https://stub.local/image.jpg"}

    host.images.upload = upload
    result = run_async(send(plugin, sticker))
    assert result.ok and result.queued and result.desc == "updated"
    assert plugin._sender._host_pending["K"].sticker.desc == "updated"
    assert len(client.calls) == 1 and not host.push.calls


@pytest.mark.parametrize("tail_enabled", [True, False])
@pytest.mark.parametrize("size_kib", [300, 1100])
def test_official_original_uses_tail_budget_with_variant_for_ordinary_delivery(
    tmp_path, run_async, tail_enabled, size_kib,
):
    plugin, host, client, _ = setup(tmp_path)
    plugin.ctx.reply_tail = client
    plugin._sender._plugin = plugin
    plugin._settings = replace(
        plugin._settings, send=replace(plugin._settings.send, host_reply_tail=tail_enabled),
    )
    original = GIF_BYTES + b"x" * (size_kib * 1024)
    variant = animated_webp()
    pack = make_pack(tmp_path / "official.zip", original, variant)
    plugin._official_pack_path = lambda: pack
    sticker, error = plugin._library.add(data=original, desc="original", tags=[])
    assert not error

    result = run_async(send(plugin, sticker))
    assert result.ok and result.queued is tail_enabled
    use_original = tail_enabled and size_kib <= 1024
    expected = [{
        "type": "image", "data": original if use_original else variant,
        "mime": "image/gif" if use_original else "image/webp",
    }]
    if tail_enabled:
        assert client.calls[0][3] == expected and client.calls[0][4] == "blind"
        assert not host.push.calls and plugin._library.get(sticker.id).use_count == 0
        client.state = {"accepted": True, "status": "submitted"}
        assert run_async(plugin._sender.drain(settings=plugin._settings))[0][1].ok
    else:
        assert not client.calls and host.push.calls[0]["parts"] == expected
        assert host.push.calls[0]["ai_behavior"] == "blind"
    assert not host.images.calls
    assert plugin._library.image_path(sticker).read_bytes() == original
    assert plugin._library.get(sticker.id).sha256 == content_sha256(original)
    assert plugin._library.get(sticker.id).use_count == 1


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_tail_original_budget_has_a_bounded_edge(tmp_path, run_async, extra_bytes):
    plugin, host, client, _ = setup(tmp_path, gif=True)
    data = GIF_BYTES + b"x" * (1024 * 1024 - len(GIF_BYTES) + extra_bytes)
    sticker, error = plugin._library.add(data=data, desc="budget edge", tags=[])
    assert not error
    result = run_async(send(plugin, sticker))
    if extra_bytes:
        assert not result.ok and result.code == "sticker_too_large"
        assert not client.calls
    else:
        assert result.ok and result.queued
        assert client.calls[0][3] == [{"type": "image", "data": data, "mime": "image/gif"}]
    assert not host.images.calls and not host.push.calls


@pytest.mark.parametrize("mode", ["old_sdk", "no_context", "agent", "panel"])
def test_larger_original_budget_is_only_for_supported_tail(tmp_path, run_async, mode):
    plugin, host, client, _ = setup(tmp_path)
    data = GIF_BYTES + b"x" * (300 * 1024)
    sticker, error = plugin._library.add(data=data, desc="large animation", tags=[])
    assert not error
    if mode == "old_sdk":
        plugin._sender._plugin.ctx.reply_tail = None
    result = run_async(send(
        plugin, sticker, context=None if mode == "no_context" else CONTEXT,
        source=mode if mode in {"agent", "panel"} else "tool",
    ))
    assert not result.ok and result.code == "sticker_too_large"
    assert not client.calls and not host.push.calls and not host.images.calls


def test_all_bundled_originals_reach_tail_without_reencoding(tmp_path, run_async):
    plugin, host, client, _ = setup(tmp_path, cooldown_sec=0)
    pack = Path(__file__).resolve().parents[1] / "official/official_pack.zip"
    assert plugin._library.seed_official(pack)["imported"] == 190
    assert plugin._library.activate_zone(plugin._library.official_zone()) == (True, "")
    with open_pack(pack) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        originals = {
            row["sha256"]: archive.read("stickers/" + row["file"]) for row in manifest["stickers"]
        }
    for sticker in plugin._library.all():
        original = originals.get(sticker.sha256)
        if original is None:
            continue
        result = run_async(send(plugin, sticker))
        assert result.ok and result.queued
        assert client.calls[-1][3] == [{"type": "image", "data": original, "mime": "image/gif"}]
        client.state = {"accepted": True, "status": "submitted"}
        assert run_async(plugin._sender.drain(settings=plugin._settings))[0][1].ok
    assert len([call for call in client.calls if call[0] == "register"]) == 190
    assert not host.push.calls and not host.images.calls
