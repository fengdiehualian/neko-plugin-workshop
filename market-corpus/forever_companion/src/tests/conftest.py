"""独立仓库的测试基建：桩掉宿主 SDK，把插件根目录加载为包 ``forever_companion``。

独立仓库里没有 ``plugin.sdk``（宿主 SDK），直接 ``import forever_companion`` 会
在 import 链上失败。这里在 pytest 收集任何测试之前：

1. 往 ``sys.modules`` 注入最小可用的 ``plugin.sdk.plugin`` 桩
   （装饰器原样返回函数/类，``tr()`` 返回默认文案）；
2. 用 importlib 把插件根目录的 ``__init__.py`` 注册为包 ``forever_companion``，
   使 ``from .cycle import ...`` 相对导入正常工作。

测试侧通过 ``make_plugin`` / ``tm`` fixture 拿到挂好假 store/config/总线的
插件实例与插件模块，不依赖宿主仓库即可跑状态机测试。
注意：测试文件不要 ``from conftest import ...`` —— conftest 由 pytest 自动加载，
直接 import 会触发二次导入链。
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 注意：不能把插件根目录插进 sys.path —— 根目录自带 __init__.py，
# pytest 会把它当包并试图导入该 __init__.py（触发无父包的相对导入错误）。
# 纯模块 cycle.py 的测试导入由 test_cycle.py 自行管理。


def _pre_register_parent_packages() -> None:
    """挂载模式防护：预注册外层父包的轻量桩，阻止 pytest 真去导入它们。

    官方市场 CI 会把仓库整体挂载到宿主 ``neko/plugin/plugins/<id>/`` 包树内跑
    测试；pytest 9 importlib 模式收集测试模块时会沿 ``__init__.py`` 链把外层
    父包一并导入（宿主的 ``plugin/__init__.py`` 带 pydantic 等重型导入，测试用
    轻量 venv 里没有），导致 58 个收集错误。这里沿目录向上找 ``__init__.py``，
    为链上每个包名在 sys.modules 预注册最小包对象（已存在真实模块则不覆盖）；
    pytest 解析父包时发现已在 sys.modules 就跳过文件导入，链条到此为止。
    独立仓库模式下根目录不在任何包链里，本函数自然无操作。
    """
    chain: list[tuple[str, Path]] = []
    current = ROOT
    while True:
        init_file = current / "__init__.py"
        if not init_file.is_file():
            break
        chain.append((current.name, current))
        parent = current.parent
        if parent == current:
            break
        current = parent
    if len(chain) < 2:  # 只有根目录自己（或更少）→ 不在包链里，无需处理
        return
    chain.reverse()  # 顶层包在前："plugin" → "plugins" → "forever_companion"
    dotted_parts: list[str] = []
    for name, directory in chain:
        dotted_parts.append(name)
        dotted = ".".join(dotted_parts)
        if dotted in sys.modules:
            continue  # 真实模块已加载（宿主运行时）：绝不覆盖
        stub = types.ModuleType(dotted)
        stub.__file__ = str(directory / "__init__.py")
        stub.__path__ = [str(directory)]  # 带 __path__ 才是"包"，子模块解析靠它
        sys.modules[dotted] = stub


_pre_register_parent_packages()


def pytest_collect_directory(path, parent):
    """强制所有目录按 Dir 收集，不建 Package 节点。

    插件根目录的 ``__init__.py`` 是包本体而非测试包标记；若 pytest 把根目录
    建成 Package，会在 setup 时导入它（无父包相对导入报错）。一律用 Dir 即可绕开。
    """
    from _pytest.nodes import Dir

    return Dir.from_parent(parent, path=path)


# ---------------------------------------------------------------------------
# SDK 桩
# ---------------------------------------------------------------------------


class Ok:
    def __init__(self, value=None):
        self.value = value


class Err:
    def __init__(self, error=None):
        self.error = error


class SdkError(Exception):
    pass


class _UiNamespace:
    @staticmethod
    def context(**kwargs):
        def deco(fn):
            return fn

        return deco

    @staticmethod
    def action(**kwargs):
        def deco(fn):
            return fn

        return deco


def _identity_decorator_factory(**kwargs):
    def deco(fn):
        return fn

    return deco


def _identity_decorator(target):
    return target


def _make_sdk_module() -> types.ModuleType:
    plugin_pkg = types.ModuleType("plugin")
    sdk_pkg = types.ModuleType("plugin.sdk")
    mod = types.ModuleType("plugin.sdk.plugin")

    mod.Ok = Ok
    mod.Err = Err
    mod.SdkError = SdkError
    mod.Result = object
    mod.ui = _UiNamespace
    # default 是 keyword-only：与生产 SDK 签名 tr(key, *, default="", **params)
    # 严格同构。曾用位置参数宽松桦，导致 build_intro_payload 直传两参调 tr
    # 的 bug 在 361 项全绿下漏网、用户实机点开介绍卡才炸（1.2.7 教训）
    mod.tr = lambda key, *, default=None, **_: default if default is not None else key

    class NekoPluginBase:
        def __init__(self, ctx):
            self.ctx = ctx
            self._dyn_entries: dict = {}

        @property
        def plugin_id(self) -> str:
            return str(getattr(self.ctx, "plugin_id", "plugin"))

        def register_dynamic_entry(self, entry_id, handler, **kwargs):
            self._dyn_entries[entry_id] = handler
            return True

        def unregister_dynamic_entry(self, entry_id):
            return self._dyn_entries.pop(entry_id, None) is not None

    mod.NekoPluginBase = NekoPluginBase

    mod.neko_plugin = _identity_decorator
    mod.lifecycle = _identity_decorator_factory
    mod.timer_interval = _identity_decorator_factory
    mod.llm_tool = _identity_decorator_factory
    mod.plugin_entry = _identity_decorator_factory
    mod.quick_action = _identity_decorator_factory

    plugin_pkg.sdk = sdk_pkg
    sdk_pkg.plugin = mod

    async def _no_activity(_source, **_kwargs):
        # 默认桩：活动信号不可用（插件侧会自动跳过活动感知行）
        return types.SimpleNamespace(
            privacy_state="unavailable",
            system_idle_seconds=None,
            foreground_category=None,
        )

    mod.get_os_activity_snapshot = _no_activity

    sys.modules["plugin"] = plugin_pkg
    sys.modules["plugin.sdk"] = sdk_pkg
    sys.modules["plugin.sdk.plugin"] = mod
    return mod


if "plugin.sdk.plugin" not in sys.modules:
    _make_sdk_module()


# ---------------------------------------------------------------------------
# 把插件根目录加载为包 ``forever_companion``
# ---------------------------------------------------------------------------


def _load_package() -> types.ModuleType:
    if "forever_companion" in sys.modules:
        return sys.modules["forever_companion"]
    spec = importlib.util.spec_from_file_location(
        "forever_companion",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    # 显式补 __path__/__package__：spec_from_file_location 的包模块在部分
    # importlib 路径下缺这两个属性会导致"attempted relative import with no
    # known parent package"（子包 core/services/mixins 的相对导入需要它们）
    module.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    module.__package__ = "forever_companion"  # type: ignore[attr-defined]
    sys.modules["forever_companion"] = module
    # pytest 的 importlib 模式会把插件根当 Package 节点，收集/ setup 时以
    # 模块名 "__init__" 导入 ROOT/__init__.py（目录名含点、无父包 spec，
    # 顶层相对导入会崩）。预注册同名模块指到已加载的包对象，import_path
    # 命中 sys.modules 缓存直接返回，不再二次执行 __init__.py。
    sys.modules.setdefault("__init__", module)
    spec.loader.exec_module(module)
    return module


forever_companion = _load_package()


# ---------------------------------------------------------------------------
# 假宿主上下文与插件工厂
# ---------------------------------------------------------------------------


class FakeLogger:
    def _emit(self, *args, **kwargs):
        pass

    info = warning = error = debug = _emit


class FakeI18n:
    def t(self, key, default=None, **_):
        return default if default is not None else key


class FakeStore:
    def __init__(self, initial=None):
        self.data = dict(initial or {})

    async def get(self, key):
        return Ok(self.data.get(key))

    async def set(self, key, value):
        self.data[key] = value
        return Ok(None)

    async def delete(self, key):
        # 与宿主 PluginStore.delete 对齐：返回 Ok(bool) 表示 key 是否原本存在
        return Ok(self.data.pop(key, None) is not None)


class FakeConfig:
    def __init__(self, section=None):
        self.section = dict(section or {})

    async def dump(self, timeout=5.0):
        return dict(self.section)


class FakePlugins:
    async def call_entry_json(self, *args, **kwargs):
        return Err("no host")

    async def call_entry(self, *args, **kwargs):
        return Err("no host")


class FakeCtx:
    def __init__(self):
        self.plugin_id = "forever_companion"
        self.bus = None
        # 宿主"最近触发角色"粘滞缓存的桩：_resolve_current_lanlan 的第二级回落
        self._current_lanlan = ""


async def _default_http_stub(method, path, body=None, headers=None):
    """默认宿主 HTTP 桩：一律返回 None（模拟宿主不可达，走 best-effort 分支）。

    既防测试误触真机 127.0.0.1:48911，也避免连接超时拖慢测试；
    需要模拟宿主行为（主动搭话开关/current_catgirl/角色名单）的测试自行替换 p._proactive_http。
    """
    return None


def make_plugin(tide_section=None, store_initial=None, mood_section=None, http=None, current_lanlan=""):
    """构建一个挂好假依赖的 ForeverCompanionPlugin 实例。

    http：宿主 HTTP 桩（签名同 _proactive_http）；current_lanlan：写入 ctx._current_lanlan。
    """
    section = {"tide": dict(tide_section or {})}
    if mood_section is not None:
        section["mood"] = dict(mood_section)
    ctx = FakeCtx()
    ctx._current_lanlan = current_lanlan
    p = forever_companion.ForeverCompanionPlugin(ctx)
    p.logger = FakeLogger()
    p.i18n = FakeI18n()
    p.store = FakeStore(store_initial)
    p.config = FakeConfig(section)
    p.plugins = FakePlugins()
    p._pushed = []
    # push_message 桩记录全部 kwargs（含 0.5.0 起的 target_lanlan 定向）
    p.push_message = lambda **kwargs: p._pushed.append(kwargs) or {"submitted": True}
    p._proactive_http = http if http is not None else _default_http_stub
    return p


DEFAULT_TIDE = {
    "enabled": True,
    "anchor_date": "2026-08-01",
    "cycle_length": 28,
    "period_length": 5,
    "ovulation_day": 14,
    "ovulation_window": 3,
    "timezone": "Asia/Shanghai",
    "phases": {
        "menstrual": {
            "prompt": "容易疲倦。",
            "mood_note": "这个阶段情绪更敏感。",
        },
        "follicular": {"prompt": "状态回血。"},
        "ovulatory": {"prompt": "电量满格。"},
        "luteal": {"prompt": "一切如常。"},
    },
}


@pytest.fixture
def tm():
    """插件模块本体（forever_companion）。"""
    return forever_companion


@pytest.fixture
def default_tide():
    return dict(DEFAULT_TIDE)


@pytest.fixture
def plugin_factory(default_tide):
    """工厂：构建挂好假依赖的插件实例，可选叠加 tide/mood 配置。"""

    def _make(tide_extra=None, mood_extra=None):
        tide = dict(default_tide)
        tide.update(tide_extra or {})
        p = make_plugin(tide_section=tide, mood_section=mood_extra)
        asyncio.run(_ready(p))
        return p

    return _make


@pytest.fixture
def plugin_factory_full(default_tide):
    """完整工厂（多角色测试用）：可指定宿主 HTTP 桩 / ctx._current_lanlan / store 初始数据。"""

    def _make(tide_extra=None, mood_extra=None, http=None, current_lanlan="", store_initial=None):
        tide = dict(default_tide)
        tide.update(tide_extra or {})
        p = make_plugin(
            tide_section=tide,
            mood_section=mood_extra,
            store_initial=store_initial,
            http=http,
            current_lanlan=current_lanlan,
        )
        asyncio.run(_ready(p))
        return p

    return _make


@pytest.fixture
def boot_factory(default_tide):
    """裸实例工厂：不预跑 _load_state/_refresh_config，由测试自己驱动 startup。

    专给"启动时序本身"用（如 store 通电时机如何决定载入可信度）：可注入自定义
    store/config 复刻宿主行为。与 plugin_factory* 的唯一区别就是不调 _ready。
    """

    def _make(tide_extra=None, store=None, config=None, current_lanlan="", http=None):
        tide = dict(default_tide)
        tide.update(tide_extra or {})
        p = make_plugin(tide_section=tide, current_lanlan=current_lanlan, http=http)
        if store is not None:
            p.store = store
        if config is not None:
            p.config = config
        return p

    return _make


async def _ready(p):
    await p._load_state()
    await p._refresh_config()
