"""猫娘实况相册 插件冒烟测试"""

import importlib


def test_plugin_module_importable():
    mod = importlib.import_module("plugin.plugins.neko_live_album")
    assert hasattr(mod, "NekoLiveAlbumPlugin")


def test_plugin_class_registered():
    from plugin.plugins.neko_live_album import NekoLiveAlbumPlugin

    assert NekoLiveAlbumPlugin is not None


def test_entry_methods_exist():
    from plugin.plugins.neko_live_album import NekoLiveAlbumPlugin

    for method in ("album_guide", "photo_react", "on_startup", "on_shutdown"):
        assert callable(getattr(NekoLiveAlbumPlugin, method, None)), method
