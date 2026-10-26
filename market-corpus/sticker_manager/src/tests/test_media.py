from conftest import FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.core.media import preserves_animation


def webp_chunk(kind, payload):
    chunk = kind + len(payload).to_bytes(4, "little") + payload
    return chunk + (b"\0" if len(payload) % 2 else b"")


def animated_webp(size=64):
    payload = webp_chunk(b"VP8X", b"\x02" + b"\0" * 9)
    payload += webp_chunk(b"ANIM", b"\0" * 6)
    payload += webp_chunk(b"ANMF", b"\0" * size)
    return b"RIFF" + (4 + len(payload)).to_bytes(4, "little") + b"WEBP" + payload


def test_detect_animation_flags_and_chunks():
    assert preserves_animation(animated_webp(), "image/webp")
    assert preserves_animation(b"GIF89a", "image/gif")
    payload = webp_chunk(b"VP8X", b"\0" * 10)
    static = b"RIFF" + (4 + len(payload)).to_bytes(4, "little") + b"WEBP" + payload
    assert not preserves_animation(static, "image/webp")
    assert not preserves_animation(b"broken", "image/webp")
    assert not preserves_animation(animated_webp()[:24], "image/webp")


def test_large_animated_webp_refuses_upload(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._library.load()
    data = animated_webp(300 * 1024)
    sticker, error = plugin._library.add(data=data, desc="animation", tags=[])
    assert not error
    result = run_async(plugin._sender.send(
        sticker, lanlan="K", source="tool", settings=StickerManagerSettings(enabled=True),
    ))
    assert result.code == "sticker_too_large"
    assert not host.images.calls and not host.push.calls


def test_small_animated_webp_keeps_exact_bytes(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._library.load()
    data = animated_webp()
    sticker, error = plugin._library.add(data=data, desc="animation", tags=[])
    assert not error
    result = run_async(plugin._sender.send(
        sticker, lanlan="K", source="tool", settings=StickerManagerSettings(enabled=True),
    ))
    assert result.ok
    assert host.push.calls[0]["parts"] == [{"type": "image", "data": data, "mime": "image/webp"}]
    assert not host.images.calls


def test_explicit_flatten_setting_still_supported(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._library.load()
    sticker, error = plugin._library.add(data=animated_webp(300 * 1024), desc="animation", tags=[])
    assert not error
    result = run_async(plugin._sender.send(
        sticker, lanlan="K", source="panel",
        settings=StickerManagerSettings(enabled=True, send=SendSettings(animated_via_upload=True)),
    ))
    assert result.ok and len(host.images.calls) == 1
