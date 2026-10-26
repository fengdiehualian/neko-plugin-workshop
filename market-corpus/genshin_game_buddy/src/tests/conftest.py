"""genshin_game_buddy 测试共享夹具。

三条硬约束（对应 FIELD_COVERAGE_CHECKLIST 的 E 节）：
  1. **数据根必须重定向到 tmp**：``NEKO_STORAGE_SELECTED_ROOT`` 指向 tmp_path，
     插件就绝不会碰用户真实目录（KB E-14 禁止自拼数据根）。
  2. **push_message / logger / store 全是记录桩**：便于断言"有没有谎报成功"。
  3. **bus 是绊线**：任何对 ``ctx.bus.X`` 的读取都会抛错并记账（E-22 隐私面）。
"""

from __future__ import annotations

import re
import shutil
import tomllib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]

#: 与 plugin/sdk/shared/core/context.py 的 _SDK_CONTEXT_METHOD_NAMES 对齐。
#: 少一个方法，ensure_sdk_context 就会把 FakeCtx 包进 SdkContext，
#: 于是 push_message 的捕捉点变成 SDK 内部，断言就失去意义。
_SDK_METHOD_NAMES = (
    "get_own_config",
    "get_own_base_config",
    "get_own_profiles_state",
    "get_own_profile_config",
    "get_own_effective_config",
    "update_own_config",
    "replace_own_config",
    "upsert_own_profile_config",
    "delete_own_profile_config",
    "set_own_active_profile",
    "query_plugins",
    "trigger_plugin_event",
    "get_system_config",
    "query_memory",
    "run_update",
    "export_push",
    "finish",
    "push_message",
    "create_card",
    "get_card",
    "create_view",
    "get_view",
    "update_status",
)

#: 本文件与 tests/static 的红线扫描共用的"插件自有源码"清单。
PLUGIN_MODULES = ("__init__.py", "game_registry.py", "term_store.py", "session.py", "context_pack.py")


def read_plugin_source(name: str) -> str:
    return (PLUGIN_DIR / name).read_text(encoding="utf-8")


def plugin_sources() -> dict[str, str]:
    return {name: read_plugin_source(name) for name in PLUGIN_MODULES}


# =============================================================================
# 桩
# =============================================================================


class FakeLogger:
    """loguru 风格的记录桩：保留 (level, 渲染后文本)。"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str, message: object, *args: object) -> None:
        text = str(message)
        if args:
            try:
                text = text.format(*args)
            except Exception:  # 消息里带字面量花括号时不至于炸掉测试
                text = " ".join([text, *(str(arg) for arg in args)])
        self.records.append((level, text))

    def debug(self, message: object, *args: object, **_kwargs: object) -> None:
        self._record("debug", message, *args)

    def info(self, message: object, *args: object, **_kwargs: object) -> None:
        self._record("info", message, *args)

    def warning(self, message: object, *args: object, **_kwargs: object) -> None:
        self._record("warning", message, *args)

    def error(self, message: object, *args: object, **_kwargs: object) -> None:
        self._record("error", message, *args)

    def exception(self, message: object, *args: object, **_kwargs: object) -> None:
        self._record("error", message, *args)

    @property
    def text(self) -> str:
        return "\n".join(text for _level, text in self.records)

    def lines(self, level: str | None = None) -> list[str]:
        return [text for lvl, text in self.records if level is None or lvl == level]


class _StoreResult:
    """SDK ``Result`` 的最小替身（``is_ok()`` + ``.value``）。"""

    def __init__(self, ok: bool, value: Any = None) -> None:
        self._ok = ok
        self._value = value

    def is_ok(self) -> bool:
        return self._ok

    @property
    def value(self) -> Any:
        return self._value


class FakeStore:
    """KV 桩。``fail_write`` 用来构造"写失败但没抛异常"的降级路径。"""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.data: dict[str, Any] = {}
        self.fail_write = False
        self.write_calls = 0

    async def get(self, key: str, default: Any = None) -> _StoreResult:
        return _StoreResult(True, self.data.get(key, default))

    async def set(self, key: str, value: Any) -> _StoreResult:
        self.write_calls += 1
        if self.fail_write:
            return _StoreResult(False)
        self.data[key] = value
        return _StoreResult(True)

    async def delete(self, key: str) -> _StoreResult:
        self.data.pop(key, None)
        return _StoreResult(True)


class BusTripwire:
    """``ctx.bus`` 的绊线：任何属性读取都会抛错并记账。"""

    def __init__(self) -> None:
        self.uses = 0

    def __getattr__(self, name: str) -> Any:
        self.uses += 1
        raise AssertionError(f"plugin must not read ctx.bus.{name} (privacy boundary)")


class FakeCtx:
    """满足 ``_is_sdk_context_compatible`` 的最小宿主上下文。"""

    def __init__(self, *, config: dict[str, Any], logger: FakeLogger | None = None) -> None:
        self.plugin_id = "genshin_game_buddy"
        self.config_path = str(PLUGIN_DIR / "plugin.toml")
        self.metadata = {
            "config_path": self.config_path,
            "i18n": {"default_locale": "zh-CN", "locales_dir": "i18n"},
        }
        self.logger = logger or FakeLogger()
        self.images = None
        self.models = None
        self._effective_config = config
        self.bus = BusTripwire()
        self.pushed: list[dict[str, Any]] = []
        self.receipt: Any = {"submitted": True}

    def __getattr__(self, name: str) -> Any:
        if name in _SDK_METHOD_NAMES:
            if name == "push_message":
                def _push(**kwargs: Any) -> Any:
                    self.pushed.append(kwargs)
                    return self.receipt

                return _push

            async def _stub(*_args: Any, **_kwargs: Any) -> Any:
                return {}

            return _stub
        raise AttributeError(name)


class FakeConfig:
    """``self.config.dump()`` 的替身。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.dumps = 0

    async def dump(self, *, timeout: float = 5.0) -> dict[str, Any]:
        del timeout
        self.dumps += 1
        return self.payload


# =============================================================================
# 配置构造
# =============================================================================

GENSHIN_ENTRY: dict[str, Any] = {
    "display_name": "原神",
    "aliases": ["原神", "Genshin", "Genshin Impact", "genshin"],
    "terms_dir": "terms/genshin",
    "enabled": True,
}

WUWA_ENTRY: dict[str, Any] = {
    "display_name": "鸣潮",
    "aliases": ["鸣潮", "Wuthering Waves", "WuWa", "wuthering"],
    "terms_dir": "terms/wuthering_waves",
    "enabled": True,
}

DEFAULT_OPTIONS: dict[str, Any] = {
    "default_game": "",
    "auto_restore_last_game": True,
    "context_inject_max_terms": 5,
    "max_context_chars": 800,
    "lookup_max_matches": 3,
    "lookup_max_chars_per_term": 200,
    "reinject_every_n_messages": 0,
}


def make_config(
    *,
    games: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    store_enabled: bool = True,
) -> dict[str, Any]:
    """构造一份与宿主下发形状一致的生效配置。"""
    merged_options = dict(DEFAULT_OPTIONS)
    merged_options.update(options or {})
    if games is None:
        games = {"genshin": dict(GENSHIN_ENTRY), "wuthering_waves": dict(WUWA_ENTRY)}
    return {
        "plugin": {"store": {"enabled": store_enabled}},
        "genshin_game_buddy": merged_options,
        "games": games,
    }


#: 测试暂存根目录。刻意放在插件目录内：
#: ``pytest`` 的 ``tmp_path`` 与 ``tempfile.mkdtemp()`` 在沙箱/托管环境下建出来的
#: 目录**不可写**（建一级成功、往里写就 PermissionError，实测于 Windows 沙箱），
#: 而插件源码目录在测试时一定可写（``__pycache__`` / ``.pytest_cache`` 本来就在这里）。
#: 该目录已进 .gitignore、用点前缀，打包规则会跳过。
SCRATCH_ROOT = PLUGIN_DIR / ".pytest-tmp"


@pytest.fixture
def scratch() -> Iterator[Path]:
    """一次用例独占的临时目录，结束时删除。

    用 ``uuid`` + ``mkdir`` 而不是 ``tempfile.mkdtemp``：后者的产物在上述环境里
    不可写，会让整批用例在 fixture 阶段就 PermissionError。
    """
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    path = SCRATCH_ROOT / f"run-{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def storage_root(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把运行数据根重定向到临时目录，并让插件的数据目录落在这里。"""
    root = scratch / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(root))
    return root


@pytest.fixture
def build_plugin(storage_root: Path, monkeypatch: pytest.MonkeyPatch):
    """构造一个真实插件实例，并把 ``self.config`` 换成桩。

    ``storage_root`` 必须在实例化**之前**生效——PluginStore 的目录在
    ``NekoPluginBase.__init__`` 里就解析完毕了。

    用例结束时会关掉每个实例的 SQLite 连接：不关的话 Windows 上 ``store.db``
    仍被句柄占着，scratch 目录删不掉，跑几轮就会在插件目录里堆几百个空壳。
    """
    del monkeypatch  # 只用它的副作用（setenv），保持 fixture 依赖关系显式
    created: list[Any] = []

    def _build(
        *,
        config: dict[str, Any] | None = None,
        logger: FakeLogger | None = None,
        receipt: Any = None,
    ):
        from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin

        payload = config if config is not None else make_config()
        ctx = FakeCtx(config=payload, logger=logger)
        if receipt is not None:
            ctx.receipt = receipt
        plugin = GenshinGameBuddyPlugin(ctx)
        plugin.config = FakeConfig(payload)
        created.append(plugin)
        return plugin

    yield _build

    for plugin in created:
        closer = getattr(getattr(plugin, "store", None), "_close_connection", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # pragma: no cover - 清理失败不该让用例报错
                pass


@pytest.fixture
def terms_root(scratch: Path) -> Path:
    """一棵可写的合成术语树（``<root>/terms/<game>/<dimension>.toml``）。"""
    root = scratch / "payload"
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_dimension(
    root: Path,
    game_id: str,
    dimension: str,
    body: str,
    *,
    terms_dir: str | None = None,
) -> Path:
    """把一段 TOML 正文写进 ``<root>/<terms_dir>/<dimension>.toml``。"""
    relative = terms_dir or f"terms/{game_id}"
    target = root.joinpath(*relative.split("/"))
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{dimension}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def write_terms_tree(root: Path, game_id: str, *, terms_dir: str | None = None, **dimensions: str) -> None:
    for dimension, body in dimensions.items():
        write_dimension(root, game_id, dimension, body, terms_dir=terms_dir)


def load_manifest() -> dict[str, Any]:
    with (PLUGIN_DIR / "plugin.toml").open("rb") as stream:
        return tomllib.load(stream)


def load_i18n(locale: str = "zh-CN") -> dict[str, str]:
    import json

    return json.loads((PLUGIN_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))


#: 采集插件自有源码里的 i18n 调用点。约定：只有 ``_t`` / ``tr`` 两个入口，
#: 且 ``self.i18n.t`` 全仓库只允许出现一次（在 ``_t`` 内，变量转发）。
I18N_CALL_PATTERN = re.compile(r"(?<![\w.])_t\(|(?<![\w.])tr\(")


def iter_i18n_call_sources() -> list[tuple[str, int, str]]:
    """返回 ``[(module, lineno, 源码行)]``，供 AST 扫描复用。"""
    out: list[tuple[str, int, str]] = []
    for name in PLUGIN_MODULES:
        for index, line in enumerate(read_plugin_source(name).splitlines(), start=1):
            if I18N_CALL_PATTERN.search(line):
                out.append((name, index, line.strip()))
    return out
