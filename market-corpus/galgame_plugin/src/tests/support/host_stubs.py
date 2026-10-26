from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class SdkError(RuntimeError):
    """Minimal stand-in for plugin-owned tests, not an SDK contract fixture."""

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.details = details


class Ok(Generic[T]):
    def __init__(self, value: T) -> None:
        self.value = value


class Err(Generic[T]):
    def __init__(self, error: T) -> None:
        self.error = error


def _package(name: str) -> ModuleType:
    module = sys.modules.setdefault(name, ModuleType(name))
    module.__path__ = []
    return module


def install_host_import_stubs() -> None:
    """Install only the imports needed to collect plugin-private LLM tests.

    Assertions about the real SDK Result types and N.E.K.O LLM utilities remain
    host contracts. These stand-ins let independent tests replace those edges
    while exercising the plugin's own parsing, caching, and fallback logic.
    """

    plugin = _package("plugin")
    sdk = _package("plugin.sdk")
    shared = _package("plugin.sdk.shared")

    sdk_plugin = sys.modules.setdefault("plugin.sdk.plugin", ModuleType("plugin.sdk.plugin"))
    sdk_plugin.SdkError = SdkError
    sdk_plugin.Ok = Ok
    sdk_plugin.Err = Err

    class _ConfigProxy:
        def __init__(self, ctx: Any) -> None:
            self._ctx = ctx

        async def dump(self, timeout: float = 5.0) -> dict[str, object]:
            payload = await self._ctx.get_own_effective_config(timeout=timeout)
            config = payload.get("config", {})
            return dict(config) if isinstance(config, dict) else {}

    class _I18nProxy:
        def t(self, _key: str, *, default: str = "", **_kwargs: object) -> str:
            return default

    class NekoPluginBase:
        def __init__(self, ctx: Any = None) -> None:
            self.ctx = ctx
            self.plugin_id = str(getattr(ctx, "plugin_id", "galgame_plugin"))
            self.config = _ConfigProxy(ctx)
            self.i18n = _I18nProxy()
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
                    if isinstance(result, (Ok, Err)):
                        return result
                    return Ok(result)

            self.plugins = _PluginsProxy()

        def enable_file_logging(self, **_kwargs: Any) -> Any:
            return self.ctx.logger

        def data_path(self, name: str) -> Path:
            return Path(self.ctx.config_path).parent / name

        def register_static_ui(self, path: str) -> None:
            self.static_ui_path = path

        def get_static_ui_config(self) -> dict[str, str] | None:
            path = getattr(self, "static_ui_path", "")
            return {"path": path} if path else None

        def set_list_actions(self, actions: list[dict[str, object]]) -> None:
            self.list_actions = actions

        async def run_update(self, **kwargs: object) -> Any:
            return await self.ctx.run_update(**kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.ctx, name)

    def _decorator(*args: Any, **_kwargs: Any) -> Any:
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return lambda target: target

    sdk_plugin.NekoPluginBase = NekoPluginBase
    sdk_plugin.lifecycle = _decorator
    sdk_plugin.neko_plugin = _decorator
    sdk_plugin.plugin_entry = _decorator
    sdk_plugin.timer_interval = _decorator
    sdk_plugin.tr = lambda _key, *, default="", **_kwargs: default
    Ok.is_ok = lambda _self: True
    Ok.is_err = lambda _self: False
    Err.is_ok = lambda _self: False
    Err.is_err = lambda _self: True

    sdk_models = sys.modules.setdefault("plugin.sdk.shared.models", ModuleType("plugin.sdk.shared.models"))
    sdk_models.Ok = Ok
    sdk_models.Err = Err

    plugin.sdk = sdk
    sdk.plugin = sdk_plugin
    sdk.shared = shared
    shared.models = sdk_models

    plugins = _package("plugin.plugins")
    plugin.plugins = plugins
    shared_plugins = _package("plugin.plugins._shared")
    plugins._shared = shared_plugins
    rapidocr = _package("plugin.plugins._shared.rapidocr")
    shared_plugins.rapidocr = rapidocr
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
    rapidocr_support.resolve_rapidocr_model_cache_dir = lambda *_args, **_kwargs: Path("rapidocr-models")
    rapidocr.rapidocr_support = rapidocr_support

    server = _package("plugin.server")
    routes = _package("plugin.server.routes")
    plugin.server = server
    server.routes = routes
    install_task_store = sys.modules.setdefault(
        "plugin.server.routes._install_task_store",
        ModuleType("plugin.server.routes._install_task_store"),
    )
    install_task_store.update_install_task_state = lambda *_args, **_kwargs: None
    routes._install_task_store = install_task_store

    utils = _package("utils")
    config_manager = sys.modules.setdefault("utils.config_manager", ModuleType("utils.config_manager"))

    class _MissingConfigManager:
        def get_model_api_config(self, _role: str) -> dict[str, object]:
            return {}

    config_manager.get_config_manager = lambda: _MissingConfigManager()

    file_utils = sys.modules.setdefault("utils.file_utils", ModuleType("utils.file_utils"))
    file_utils.robust_json_loads = json.loads

    llm_client = sys.modules.setdefault("utils.llm_client", ModuleType("utils.llm_client"))

    async def _missing_llm(**_kwargs: Any) -> Any:
        raise RuntimeError("host LLM client is unavailable in standalone tests")

    llm_client.create_chat_llm_async = _missing_llm
    llm_client.create_chat_llm = lambda **_kwargs: None
    llm_client.ChatOpenAI = Any

    token_tracker = sys.modules.setdefault("utils.token_tracker", ModuleType("utils.token_tracker"))
    token_tracker.set_call_type = lambda _call_type: None

    utils.config_manager = config_manager
    utils.file_utils = file_utils
    utils.llm_client = llm_client
    utils.token_tracker = token_tracker
