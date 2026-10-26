from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _package(name: str) -> ModuleType:
    module = sys.modules.setdefault(name, ModuleType(name))
    module.__path__ = []
    return module


def _decorator(*args: Any, **_kwargs: Any) -> Any:
    if len(args) == 1 and callable(args[0]):
        return args[0]
    return lambda target: target


def install_plugin_runtime_stubs() -> None:
    """Supply import-only host edges for plugin-private unit tests.

    These stubs intentionally do not model the SDK or RapidOCR contracts. Tests
    using them may exercise plugin-owned methods, while host integration remains
    responsible for validating decorators, base classes, Result, and OCR wiring.
    """

    plugin = _package("plugin")
    plugins = _package("plugin.plugins")
    shared_plugins = _package("plugin.plugins._shared")
    rapidocr = _package("plugin.plugins._shared.rapidocr")
    rapidocr_support = sys.modules.setdefault(
        "plugin.plugins._shared.rapidocr.rapidocr_support",
        ModuleType("plugin.plugins._shared.rapidocr.rapidocr_support"),
    )

    rapidocr_support.DEFAULT_RAPIDOCR_ENGINE_TYPE = "onnxruntime"
    rapidocr_support.DEFAULT_RAPIDOCR_LANG_TYPE = "ch"
    rapidocr_support.DEFAULT_RAPIDOCR_MODEL_TYPE = "mobile"
    rapidocr_support.DEFAULT_RAPIDOCR_OCR_VERSION = "PP-OCRv4"
    rapidocr_support.inspect_rapidocr_installation = lambda **_kwargs: {}
    rapidocr_support.load_rapidocr_runtime = lambda **_kwargs: (None, {})
    rapidocr_support.resolve_rapidocr_model_cache_dir = (
        lambda *_args, **_kwargs: Path(".rapidocr-test-cache")
    )

    plugin.plugins = plugins
    plugins._shared = shared_plugins
    shared_plugins.rapidocr = rapidocr
    rapidocr.rapidocr_support = rapidocr_support

    sdk_plugin = sys.modules["plugin.sdk.plugin"]

    class NekoPluginBase:
        def __init__(self, ctx: Any = None) -> None:
            self.ctx = ctx

            class _ConfigProxy:
                async def dump(_self, timeout: float = 5.0) -> dict[str, object]:
                    del timeout
                    return dict(ctx._config)

            self.config = _ConfigProxy()
            self.i18n = type(
                "_I18nProxy",
                (),
                {"t": lambda _self, _key, *, default="", **_kwargs: default},
            )()
            self.list_actions: list[dict[str, object]] = []

            class _PluginsProxy:
                async def call_entry(
                    _self,
                    entry_ref: str,
                    *,
                    params: dict[str, object],
                    timeout: float,
                ) -> Any:
                    result = await ctx.trigger_plugin_event(
                        entry_ref=entry_ref,
                        params=params,
                        timeout=timeout,
                    )
                    if isinstance(result, (sdk_plugin.Ok, sdk_plugin.Err)):
                        return result
                    return sdk_plugin.Ok(result)

            self.plugins = _PluginsProxy()

        def enable_file_logging(self, **_kwargs: Any) -> Any:
            return self.ctx.logger

        def data_path(self, name: str) -> Path:
            root = Path(self.ctx.config_path).parent / ".test-data"
            root.mkdir(parents=True, exist_ok=True)
            return root / name

        def register_static_ui(self, path: str) -> None:
            self.static_ui_path = path

        def get_static_ui_config(self) -> dict[str, str] | None:
            path = getattr(self, "static_ui_path", "")
            return {"path": path} if path else None

        def set_list_actions(self, actions: list[dict[str, object]]) -> None:
            self.list_actions = actions

        def __getattr__(self, name: str) -> Any:
            return getattr(self.ctx, name)

    sdk_plugin.NekoPluginBase = NekoPluginBase
    sdk_plugin.lifecycle = _decorator
    sdk_plugin.neko_plugin = _decorator
    sdk_plugin.plugin_entry = _decorator
    sdk_plugin.timer_interval = _decorator
    sdk_plugin.tr = lambda _key, *, default="", **_kwargs: default

    ok_type = sdk_plugin.Ok
    err_type = sdk_plugin.Err
    ok_type.is_ok = lambda _self: True
    ok_type.is_err = lambda _self: False
    err_type.is_ok = lambda _self: False
    err_type.is_err = lambda _self: True

__all__ = ["install_plugin_runtime_stubs"]
