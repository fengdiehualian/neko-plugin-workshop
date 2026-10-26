"""测试夹具（沿用 our_life 仓的测试基建，按本插件的通道裁剪）。

三件事：

1. **桩掉宿主 SDK**（`plugin.sdk.plugin`）：插件代码只从公共门面导入，桩要足够完整，
   才能把仓根包真的加载起来，让入口、装饰器元数据、角色归属这些行为在没有 N.E.K.O
   宿主的机器上也能被测。真宿主可用时（在仓库内跑）绝不覆盖真包，只覆盖门面。
2. **把仓根加载为包 `sticker_manager`**：仓库目录名 `n.e.k.o_plugin_sticker_manager`
   不是合法 Python 标识符，且插件根自带 `__init__.py`（它是包本体，不是测试包标记）。
3. **挡住 pytest 的 Package 节点**：importlib 模式下 pytest 会在 setup 时以模块名
   `"__init__"` 导入 ROOT/__init__.py——顶层相对导入没有父包会崩。两道防线：
   预注册 `sys.modules["__init__"]`；`pytest_collect_directory` 一律返回 Dir。

另提供假宿主：`FakeConfig` / `FakeHostContext`（含 pushed 记录与 images 桩）/
`build_plugin`。表情库是文件存储，测试用 `tmp_path` 注入 `data_root`。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# importlib 模式下测试文件里的 `from conftest import ...` 需要这个名字可解析：
# pytest 可能以唯一化模块名导入本文件，这里把常规名也注册上（幂等）。
sys.modules.setdefault("conftest", sys.modules[__name__])

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "sticker_manager"


# ---------------------------------------------------------------------------
# 挂载态防护：CI 会把仓库挂到宿主 plugin/plugins/<id>/ 包树里跑测试
# ---------------------------------------------------------------------------


def _pre_register_parent_packages() -> None:
    """沿目录向上为每个含 `__init__.py` 的父包预注册轻量桩。"""
    chain: list[tuple[str, Path]] = []
    current = ROOT
    while True:
        if not (current / "__init__.py").is_file():
            break
        chain.append((current.name, current))
        parent = current.parent
        if parent == current:
            break
        current = parent
    if len(chain) < 2:
        return
    chain.reverse()
    dotted_parts: list[str] = []
    for name, directory in chain:
        dotted_parts.append(name)
        dotted = ".".join(dotted_parts)
        if dotted in sys.modules:
            continue
        stub = types.ModuleType(dotted)
        stub.__file__ = str(directory / "__init__.py")
        stub.__path__ = [str(directory)]  # type: ignore[attr-defined]
        sys.modules[dotted] = stub


# ---------------------------------------------------------------------------
# SDK 桩
# ---------------------------------------------------------------------------


def _record_meta(func: Any, dargs: tuple[Any, ...], dkwargs: dict[str, Any]) -> Any:
    meta = getattr(func, "__neko_stub_meta__", None)
    if isinstance(meta, list):
        meta.append({"args": dargs, "kwargs": dkwargs})
    else:
        setattr(func, "__neko_stub_meta__", [{"args": dargs, "kwargs": dkwargs}])
    if dkwargs.get("id"):
        setattr(func, "__neko_stub_id__", dkwargs["id"])
    return func


def _passthrough_decorator(*dargs: Any, **dkwargs: Any):
    def decorate(func: Any) -> Any:
        return _record_meta(func, dargs, dkwargs)

    return decorate


def _identity_decorator(target: Any) -> Any:
    return target


def _build_facade() -> types.ModuleType:
    facade = types.ModuleType("plugin.sdk.plugin")

    @dataclass(frozen=True)
    class Ok:
        value: Any = None

        def is_ok(self) -> bool:
            return True

    @dataclass(frozen=True)
    class Err:
        error: Any = None

        def is_ok(self) -> bool:
            return False

    class SdkError(RuntimeError):
        def __init__(self, message: str, *, code: str | None = None, details: Any = None):
            super().__init__(message)
            self.code = code
            self.details = details

    def unwrap_or(result: Any, default: Any = None) -> Any:
        return result.value if isinstance(result, Ok) else default

    def tr(key: str, *, default: str = "", **params: Any) -> dict[str, Any]:
        return {"$i18n": key, "default": default, "params": params}

    class NekoPluginBase:
        """最小宿主基类：只提供入口代码真正用到的属性。"""

        def __init__(self, ctx: Any):
            self.ctx = ctx
            self.plugin_id = str(getattr(ctx, "plugin_id", PACKAGE_NAME))
            self.plugin_dir = ROOT
            self.config_dir = ROOT
            self.logger = getattr(ctx, "logger", logging.getLogger("sticker_manager.stub"))
            self.config = getattr(ctx, "config", None)
            self.bus = getattr(ctx, "bus", None)

        def data_path(self, *parts: str) -> Path:
            base = Path(getattr(self.ctx, "data_root", ROOT / ".stub-data"))
            return base.joinpath(*parts) if parts else base

        def push_message(self, **kwargs: Any) -> dict[str, Any]:
            pushed = getattr(self.ctx, "pushed", None)
            if isinstance(pushed, list):
                pushed.append(dict(kwargs))
            return {"submitted": True}

    ui = types.SimpleNamespace(context=_passthrough_decorator, action=_passthrough_decorator)

    for name in (
        "plugin_entry",
        "lifecycle",
        "timer_interval",
        "message",
        "on_event",
        "custom_event",
        "hook",
        "llm_tool",
        "quick_action",
    ):
        setattr(facade, name, _passthrough_decorator)

    facade.Ok = Ok  # type: ignore[attr-defined]
    facade.Err = Err  # type: ignore[attr-defined]
    facade.SdkError = SdkError  # type: ignore[attr-defined]
    facade.unwrap_or = unwrap_or  # type: ignore[attr-defined]
    facade.tr = tr  # type: ignore[attr-defined]
    facade.NekoPluginBase = NekoPluginBase  # type: ignore[attr-defined]
    facade.neko_plugin = _identity_decorator  # type: ignore[attr-defined]
    facade.ui = ui  # type: ignore[attr-defined]
    return facade


def ensure_sdk_stub() -> bool:
    """把 `plugin.sdk.plugin` 换成桩门面（理由见 our_life 同名函数：独立仓与
    挂载态必须行为一致，真基类会自建真通道，一碰就 TransportError）。"""
    facade = _build_facade()
    existing_plugin = sys.modules.get("plugin")
    existing_sdk = sys.modules.get("plugin.sdk")
    if isinstance(existing_plugin, types.ModuleType):
        plugin_pkg = existing_plugin
    else:
        plugin_pkg = types.ModuleType("plugin")
        plugin_pkg.__path__ = []  # type: ignore[attr-defined]
    if isinstance(existing_sdk, types.ModuleType):
        sdk_pkg = existing_sdk
    else:
        sdk_pkg = types.ModuleType("plugin.sdk")
        # Keep the real shared SDK importable for transport integration tests.
        sdk_pkg.__path__ = [  # type: ignore[attr-defined]
            str(Path(directory) / "sdk") for directory in getattr(plugin_pkg, "__path__", [])
            if (Path(directory) / "sdk").is_dir()
        ]
    plugin_pkg.sdk = sdk_pkg  # type: ignore[attr-defined]
    sdk_pkg.plugin = facade  # type: ignore[attr-defined]
    sys.modules["plugin"] = plugin_pkg
    sys.modules["plugin.sdk"] = sdk_pkg
    sys.modules["plugin.sdk.plugin"] = facade
    return True


# ---------------------------------------------------------------------------
# 仓根 → 包
# ---------------------------------------------------------------------------


def register_plugin_package() -> types.ModuleType:
    existing = sys.modules.get(PACKAGE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    module.__package__ = PACKAGE_NAME  # type: ignore[attr-defined]
    sys.modules[PACKAGE_NAME] = module
    sys.modules.setdefault("__init__", module)
    spec.loader.exec_module(module)
    return module


def pytest_collect_directory(path: Any, parent: Any) -> Any:
    from _pytest.main import Dir

    return Dir.from_parent(parent, path=path)


# ---------------------------------------------------------------------------
# 假宿主
# ---------------------------------------------------------------------------


@dataclass
class FakeConfig:
    data: dict[str, Any] = field(default_factory=dict)
    dump_error: Exception | None = None
    set_error: Exception | None = None
    writes: list[tuple[str, Any]] = field(default_factory=list)

    async def dump(self) -> dict[str, Any]:
        if self.dump_error is not None:
            raise self.dump_error
        return self.data

    async def set(self, path: str, value: Any) -> None:
        if self.set_error is not None:
            raise self.set_error
        self.writes.append((path, value))
        # 与真宿主 PluginConfig.set 同形：点分路径**逐层嵌套**（`_set_by_path`），
        # 不是把整串当平键塞进第一段——`sticker_manager.enabled` 只有一层所以看不出来，
        # 但 `sticker_manager.send.eagerness` 这类两层路径必须嵌，否则 from_config 读不到。
        parts = path.split(".")
        section, keys = parts[0], parts[1:]
        target = self.data.setdefault(section, {})
        if not isinstance(target, dict):
            target = self.data[section] = {}
        for key in keys[:-1]:
            nxt = target.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                target[key] = nxt
            target = nxt
        if keys:
            target[keys[-1]] = value
        else:
            self.data[section] = value


class FakeImages:
    """`ctx.images.upload` 的桩：记录调用并回一个 URL part。"""

    def __init__(self, *, fail: bool = False):
        self.calls: list[bytes] = []
        self.fail = fail

    async def upload(self, data: bytes, *, mime: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
        self.calls.append(bytes(data))
        if self.fail:
            raise RuntimeError("stub upload failure")
        return {"type": "image", "url": f"https://stub.local/{len(self.calls)}.jpg", "mime": "image/jpeg"}


class FakePush:
    """记录 ctx.push_message 的调用；reject_next 可模拟本地超限拦截。

    **参数名白名单按 SDK 逐字核过**（宿主 `SdkContext.push_message`，用
    `inspect.signature` 拿的真参数表）。为什么不是宽 `**kwargs` 直接收下：
    v0.16.1 的教训——`bus.memory` 那条宽桩把"传错参数名"遮了三轮，真机每次抛
    TypeError 都被 except 吞成"总线没信号"，测试却全绿（桩宽容=盲区）。
    `push_message` 同一个病：传错键在真机是 TypeError，注入静默不发生。
    """

    # 宿主 SdkContext.push_message 的全部入参（除 self）；缺一或多一都算桩被用错。
    SDK_PARAMS = frozenset(
        {
            "source",
            "message_type",
            "description",
            "priority",
            "content",
            "binary_data",
            "binary_url",
            "metadata",
            "unsafe",
            "fast_mode",
            "target_lanlan",
            "visibility",
            "ai_behavior",
            "parts",
            "coalesce_key",
            "mime",
            "delivery",
            "reply",
        }
    )

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.reject_reason: str | None = None

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        unknown = set(kwargs) - self.SDK_PARAMS
        if unknown:
            raise TypeError(f"push_message() got unexpected keyword argument(s): {', '.join(sorted(unknown))}")
        self.calls.append(dict(kwargs))
        if self.reject_reason is not None:
            reason = self.reject_reason
            self.reject_reason = None
            return {"ok": False, "submitted": False, "reason": reason}
        return {"submitted": True}


class FakeConversationsNamespace:
    """`bus.conversations` 的桩：**参数签名与 SDK 逐字对齐**。

    宿主 `SdkConversationsBus.get(*, conversation_id, max_count, since_ts, timeout)`
    不吃 `bucket_id`。做成严格签名是有原因的：v0.14.1~v0.16.0 期间的宽 `**kwargs` 桩
    把"传错参数名"这件事遮了整整三轮——真机上每次读都抛 TypeError，被 except 吞成
    "总线里没有目标"，测试却全绿。桩宽容=盲区。
    """

    def __init__(self, records: list[dict[str, Any]] | None = None, *, error: bool = False):
        self.records = list(records or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        *,
        conversation_id: str | None = None,
        max_count: int = 50,
        since_ts: float | None = None,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "max_count": max_count,
                "since_ts": since_ts,
                "timeout": timeout,
            }
        )
        if self.error:
            raise RuntimeError("bus unavailable")
        return list(self.records)


class FakeMemoryNamespace:
    """`bus.memory` 的桩：同样严格对齐 `SdkMemoryBus.get(*, bucket_id, limit, timeout)`。

    注意 `bucket_id` 是**必填**的——漏传在真机上是 TypeError，不是默认值。
    """

    def __init__(self, records: list[dict[str, Any]] | None = None, *, error: bool = False):
        self.records = list(records or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def get(
        self, *, bucket_id: str, limit: int = 20, timeout: float = 5.0
    ) -> list[dict[str, Any]]:
        self.calls.append({"bucket_id": bucket_id, "limit": limit, "timeout": timeout})
        if self.error:
            raise RuntimeError("bus unavailable")
        return list(self.records)[-limit:]


class FakeBus:
    """宿主总线桩：两个命名空间分开喂。

    `memory` 必须存在且默认空列表——真实宿主永远有这个桶。默认给空而不是缺省，
    是为了让 v0.16.0 的轮次驱动走主路径（"桶里没有用户消息"≠"总线读不到"）；
    要演"总线断了/形状变了"的降级路径，显式传 `memory_error=True`。
    """

    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        *,
        memory_records: list[dict[str, Any]] | None = None,
        error: bool = False,
        memory_error: bool = False,
    ):
        self.conversations = FakeConversationsNamespace(records, error=error)
        self.memory = FakeMemoryNamespace(
            memory_records if memory_records is not None else [], error=memory_error
        )


def conversation_record(conversation_id: str, timestamp: float, lanlan: str, turn_type: str = "user") -> dict[str, Any]:
    """构造一条总线轮次记录（形状与宿主 `bus.conversations` 一致）。"""
    return {
        "conversation_id": conversation_id,
        "timestamp": timestamp,
        "metadata": {"lanlan_name": lanlan, "turn_type": turn_type},
    }


def user_message_record(
    timestamp: float, content: str = "在吗", lanlan: str = "", *, is_voice: bool = False
) -> dict[str, Any]:
    """构造一条 `bus.memory` 的用户轮记录（宿主 turn.py 写入的原始形状）。

    时间戳字段是 `_ts` 不是 `timestamp`，角色归属字段是 `lanlan` 不是 `lanlan_name`
    ——两处键名差异是 services/turns.py 与 services/lanlan.py 各自要吃下的现实。
    """
    record: dict[str, Any] = {
        "type": "user_message",
        "content": content,
        "_ts": timestamp,
        "is_voice": is_voice,
    }
    if lanlan:
        record["lanlan"] = lanlan
    return record


@dataclass
class FakeHostContext:
    plugin_id: str = PACKAGE_NAME
    logger: Any = field(default_factory=lambda: logging.getLogger("sticker_manager.test"))
    config: Any = field(default_factory=FakeConfig)
    data_root: Any = None
    pushed: list[dict[str, Any]] = field(default_factory=list)
    bus: Any = None

    def __post_init__(self):
        if self.data_root is None:
            self.data_root = ROOT / ".stub-data"
        self.push = FakePush()
        self.images = FakeImages()
        if self.bus is None:
            self.bus = FakeBus([])

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        return self.push.push_message(**kwargs)


def build_plugin(
    host: FakeHostContext | None = None,
) -> tuple[Any, FakeHostContext]:
    """实例化插件主类并显式接管宿主通道（独立仓 / 挂载态行为一致）。"""
    package = register_plugin_package()
    host = host or FakeHostContext()
    plugin = package.StickerManagerPlugin(host)
    plugin.logger = host.logger
    plugin.config = host.config
    plugin.bus = host.bus
    # Legacy transport/gate tests are synchronous; tail-order tests inject
    # isolated temporary logs and a health stub, never the running Steam host.
    plugin._sender._turn_end = None
    return plugin, host


# ---------------------------------------------------------------------------
# 图片样本
# ---------------------------------------------------------------------------

# 最小合法文件头（内容不必可渲染；本插件只嗅探文件头）
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF_BYTES = b"GIF89a" + b"\x00" * 64
WEBP_BYTES = b"RIFF" + (70).to_bytes(4, "little") + b"WEBP" + b"\x00" * 62
NOT_AN_IMAGE = b"this is definitely not an image"

_STUB_SIZES = {"tiny": 64, "oversize": 300 * 1024}


def sample_image(kind: str = "png", size: int = 64) -> bytes:
    """按格式与大致尺寸生成样本字节。"""
    bases = {"png": PNG_BYTES, "jpg": JPEG_BYTES, "gif": GIF_BYTES, "webp": WEBP_BYTES}
    head = bases[kind]
    if len(head) >= size:
        return head
    return head + b"\x00" * (size - len(head))


_pre_register_parent_packages()
ensure_sdk_stub()
register_plugin_package()


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return tmp_path / "library"


@pytest.fixture
def make_plugin(data_root: Path) -> Any:
    def _make(*, config: Any = None, data_root_override: Path | None = None) -> tuple[Any, FakeHostContext]:
        host = FakeHostContext(
            config=config if config is not None else FakeConfig(
                data={"sticker_manager": {"enabled": True}}
            ),
            data_root=data_root_override or data_root,
        )
        return build_plugin(host)

    return _make


@pytest.fixture
def plugin(make_plugin: Any) -> Any:
    instance, _host = make_plugin()
    return instance


@pytest.fixture
def run_async() -> Any:
    def _run(coro: Any) -> Any:
        return asyncio.run(coro)

    return _run


@pytest.fixture(autouse=True)
def no_host_http() -> Any:
    """全局关掉注入目标解析的宿主 HTTP 级（v0.15.0 实机踩的隔离坑）。

    宿主与测试可以同机并存：不关掉时，"没有目标"的用例会真问到运行中的宿主、
    拿到角色名而**误判通过**。要验这一级的用例自己把它打开并打桩 `_fetch_blocking`
    （见 tests/test_lanlan.py 的 `enable_http`）。
    """
    module = sys.modules.get("sticker_manager.services.lanlan")
    if module is None:
        yield
        return
    previous = module.HTTP_ENABLED
    module.HTTP_ENABLED = False
    try:
        yield
    finally:
        module.HTTP_ENABLED = previous
