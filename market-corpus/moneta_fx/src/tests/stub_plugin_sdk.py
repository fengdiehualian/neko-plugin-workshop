"""Minimal ``plugin.sdk.plugin`` stub so the engine can be tested outside N.E.K.O.

Only used by local smoke tests — never shipped inside the plugin package.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Callable


class _Result:
    def __init__(self, value: Any = None, error: Any = None) -> None:
        self.value = value
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Result(value={self.value!r}, error={self.error!r})"


def Ok(data: Any = None) -> _Result:
    return _Result(value=data)


def Err(error: Any) -> _Result:
    return _Result(error=error)


class SdkError(Exception):
    pass


class TransportError(SdkError):
    pass


def unwrap(result: _Result) -> Any:
    if result.error is not None:
        raise result.error
    return result.value


def unwrap_or(result: _Result, default: Any) -> Any:
    return default if result.error is not None else result.value


def _decorator_factory(attribute: str) -> Callable[..., Callable]:
    def factory(**kwargs: Any) -> Callable:
        def decorator(fn: Callable) -> Callable:
            setattr(fn, attribute, dict(kwargs))
            return fn

        return decorator

    return factory


def _passthrough(*args: Any, **kwargs: Any) -> Callable:
    """``@neko_plugin`` is applied bare (no parentheses)."""

    def decorator(fn: Callable) -> Callable:
        return fn

    if len(args) == 1 and not kwargs and callable(args[0]):
        return args[0]
    return decorator


neko_plugin = _passthrough
plugin_entry = _decorator_factory("__neko_entry__")
lifecycle = _decorator_factory("__neko_lifecycle__")
timer_interval = _decorator_factory("__neko_timer__")
message = _decorator_factory("__neko_message__")
on_event = _decorator_factory("__neko_event__")
custom_event = _decorator_factory("__neko_custom_event__")
hook = _decorator_factory("__neko_hook__")
before_entry = _decorator_factory("__neko_before__")
after_entry = _decorator_factory("__neko_after__")
around_entry = _decorator_factory("__neko_around__")
replace_entry = _decorator_factory("__neko_replace__")
quick_action = _decorator_factory("__neko_quick_action__")


class _UiNamespace:
    @staticmethod
    def context(**kwargs: Any) -> Callable:
        return _decorator_factory("__neko_ui_context__")(**kwargs)

    @staticmethod
    def action(**kwargs: Any) -> Callable:
        return _decorator_factory("__neko_ui_action__")(**kwargs)


ui = _UiNamespace()


def tr(key: str, default: str = "", **params: Any) -> str:
    return default or key


class _PluginNamespace:
    entry = staticmethod(plugin_entry)
    lifecycle = staticmethod(lifecycle)
    timer = staticmethod(timer_interval)
    message = staticmethod(message)
    hook = staticmethod(hook)


plugin = _PluginNamespace()


class NekoPluginBase:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.logger = getattr(ctx, "logger", None)
        self.plugin_id = "moneta_fx"
        self.plugin_dir = None
        self.storage_dir = None
        self.metadata = {}
        self.bus = None
        self.plugins = None
        self.system_info = None

    def report_status(self, status: dict) -> None:
        pass

    def push_message(self, **kwargs: Any) -> dict:
        return {"submitted": False, "reason": "stub"}

    def data_path(self, *parts: str):
        from pathlib import Path

        return Path(*parts)

    def cache_path(self, *parts: str):
        from pathlib import Path

        return Path(*parts)

    def register_dynamic_entry(self, *args: Any, **kwargs: Any) -> bool:
        return True


class PluginMeta:
    pass


class EntryKind:
    ACTION = "action"
    SERVICE = "service"
    HOOK = "hook"
    CUSTOM = "custom"


class LlmToolMeta:
    pass


class PluginI18n:
    def t(self, key: str, default: str = "", **params: Any) -> str:
        return default or key


class PluginSettings:
    pass


class SettingsField:
    pass


class PluginStore:
    def __init__(self) -> None:
        self._data: dict = {}

    async def get(self, key: str) -> _Result:
        return Ok(self._data.get(key))

    async def set(self, key: str, value: Any) -> _Result:
        self._data[key] = value
        return Ok(True)


class PluginConfig:
    async def dump(self) -> dict:
        return {}

    async def get_int(self, key: str, default: int = 0) -> int:
        return default


class PluginRouter:
    pass


class Plugins:
    pass


class SystemInfo:
    pass


def get_plugin_logger(name: str = "moneta_fx"):
    import logging

    return logging.getLogger(name)


def install() -> None:
    """Register the stub as ``plugin.sdk.plugin`` for local testing."""
    if "plugin" in sys.modules:
        return
    # 先把插件根目录放上 sys.path，这样本模块被 import 后，被测模块的
    # 平铺导入（from engine import ...）也能解析。
    here = Path(__file__).resolve()
    for path in (str(here.parents[1]), str(here.parent)):
        if path not in sys.path:
            sys.path.insert(0, path)

    root = types.ModuleType("plugin")
    sdk = types.ModuleType("plugin.sdk")
    sdk_plugin = types.ModuleType("plugin.sdk.plugin")

    exports = {
        "NekoPluginBase": NekoPluginBase,
        "PluginMeta": PluginMeta,
        "EntryKind": EntryKind,
        "neko_plugin": neko_plugin,
        "plugin_entry": plugin_entry,
        "lifecycle": lifecycle,
        "timer_interval": timer_interval,
        "message": message,
        "on_event": on_event,
        "custom_event": custom_event,
        "hook": hook,
        "before_entry": before_entry,
        "after_entry": after_entry,
        "around_entry": around_entry,
        "replace_entry": replace_entry,
        "quick_action": quick_action,
        "plugin": plugin,
        "ui": ui,
        "tr": tr,
        "PluginI18n": PluginI18n,
        "PluginSettings": PluginSettings,
        "SettingsField": SettingsField,
        "Ok": Ok,
        "Err": Err,
        "unwrap": unwrap,
        "unwrap_or": unwrap_or,
        "PluginStore": PluginStore,
        "PluginConfig": PluginConfig,
        "PluginRouter": PluginRouter,
        "Plugins": Plugins,
        "SystemInfo": SystemInfo,
        "SdkError": SdkError,
        "TransportError": TransportError,
        "get_plugin_logger": get_plugin_logger,
        "LlmToolMeta": LlmToolMeta,
    }
    for name, value in exports.items():
        setattr(sdk_plugin, name, value)
    root.sdk = sdk
    sdk.plugin = sdk_plugin
    sys.modules["plugin"] = root
    sys.modules["plugin.sdk"] = sdk
    sys.modules["plugin.sdk.plugin"] = sdk_plugin
