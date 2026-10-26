from __future__ import annotations

import threading
from importlib import import_module
from types import SimpleNamespace

import pytest
from tests.support.plugin_runtime_stubs import install_plugin_runtime_stubs

install_plugin_runtime_stubs()

plugin_core_module = import_module("market_plugins.galgame_plugin.plugin_core")
service_module = import_module("market_plugins.galgame_plugin.service")
GalgamePlugin = plugin_core_module.GalgamePlugin
build_config = service_module.build_config

pytestmark = pytest.mark.plugin_unit


class _ConfigService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def persist_rapidocr_lang(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def _rapidocr_entry_plugin() -> tuple[GalgamePlugin, _ConfigService]:
    plugin = GalgamePlugin.__new__(GalgamePlugin)
    plugin._cfg = build_config({})
    plugin._state = SimpleNamespace(next_poll_at_monotonic=1.0)
    plugin._state_lock = threading.Lock()
    plugin._state_dirty = False
    plugin._cached_snapshot = {"cached": True}
    plugin._ocr_reader_manager = None
    service = _ConfigService()
    plugin._config_service = service
    plugin._refresh_dependency_status = lambda: None
    plugin._start_background_bridge_poll = lambda: None
    plugin.logger = SimpleNamespace(
        debug=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
    )
    return plugin, service


@pytest.mark.asyncio
async def test_set_rapidocr_lang_accepts_ocr_version_and_persists() -> None:
    plugin, service = _rapidocr_entry_plugin()
    updates: list[str] = []
    plugin._ocr_reader_manager = SimpleNamespace(
        update_config=lambda config: updates.append(config.rapidocr_ocr_version)
    )

    result = await plugin.galgame_set_rapidocr_lang(ocr_version="v5")

    assert result.is_ok()
    assert result.value["ocr_version"] == "PP-OCRv5"
    assert plugin._cfg.rapidocr_ocr_version == "PP-OCRv5"
    assert updates == ["PP-OCRv5"]
    assert service.calls[0]["ocr_version"] == "PP-OCRv5"


@pytest.mark.asyncio
async def test_set_rapidocr_lang_preserves_version_on_lang_only_change() -> None:
    plugin, service = _rapidocr_entry_plugin()
    plugin._cfg.rapidocr_ocr_version = "PP-OCRv5"
    plugin._ocr_reader_manager = SimpleNamespace(update_config=lambda _config: None)

    result = await plugin.galgame_set_rapidocr_lang(lang_type="korean")

    assert result.is_ok()
    assert result.value["lang_type"] == "korean"
    assert result.value["ocr_version"] == "PP-OCRv5"
    assert service.calls[0]["ocr_version"] is None
    assert service.calls[0]["auto_detect_lang"] is False


@pytest.mark.asyncio
async def test_set_rapidocr_lang_rejects_invalid_ocr_version() -> None:
    plugin, service = _rapidocr_entry_plugin()

    result = await plugin.galgame_set_rapidocr_lang(ocr_version="PP-OCRv6")

    assert result.is_err()
    assert plugin._cfg.rapidocr_ocr_version == "PP-OCRv4"
    assert service.calls == []


@pytest.mark.asyncio
async def test_set_rapidocr_lang_is_noop_when_values_are_unchanged() -> None:
    plugin, service = _rapidocr_entry_plugin()
    updates: list[str] = []
    plugin._ocr_reader_manager = SimpleNamespace(
        update_config=lambda config: updates.append(config.rapidocr_ocr_version)
    )

    version_result = await plugin.galgame_set_rapidocr_lang(
        ocr_version="PP-OCRv4"
    )
    auto_result = await plugin.galgame_set_rapidocr_lang(auto_detect_lang=True)

    assert version_result.is_ok() and version_result.value["skipped"] is True
    assert auto_result.is_ok() and auto_result.value["already_applied"] is True
    assert updates == []
    assert service.calls == []


@pytest.mark.asyncio
async def test_set_rapidocr_lang_rolls_back_when_reader_update_fails() -> None:
    plugin, service = _rapidocr_entry_plugin()

    def _raise_update(_config: object) -> None:
        raise RuntimeError("boom")

    plugin._ocr_reader_manager = SimpleNamespace(update_config=_raise_update)

    result = await plugin.galgame_set_rapidocr_lang(ocr_version="PP-OCRv5")

    assert result.is_err()
    assert plugin._cfg.rapidocr_ocr_version == "PP-OCRv4"
    assert service.calls == []


def test_auto_detect_lang_preserves_user_ocr_version() -> None:
    plugin, service = _rapidocr_entry_plugin()
    plugin._cfg.rapidocr_ocr_version = "PP-OCRv5"

    plugin._on_rapidocr_auto_lang_changed("ch")

    assert plugin._cfg.rapidocr_lang_type == "ch"
    assert plugin._cfg.rapidocr_ocr_version == "PP-OCRv5"
    assert service.calls == [
        {
            "lang_type": "ch",
            "auto_detect_lang": True,
            "auto_detect_last_lang": "ch",
        }
    ]
