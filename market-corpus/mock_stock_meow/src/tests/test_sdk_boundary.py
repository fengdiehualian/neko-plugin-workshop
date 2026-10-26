"""宿主 SDK 导入边界守卫。

**背景（真实启动事故）**：

插件 0.7.0 在真实 N.E.K.O 宿主里启动即崩：

    Plugin 'mock_stock_meow' lifecycle event 'startup' failed:
    Process crashed: No module named 'plugin.sdk.shared.core.cards'

根因是插件直接 import 了宿主**内部实现** ``plugin.sdk.shared.core.cards``。
在开发机上（源码树可 import）看不出来，一打包进宿主就炸。

宿主 SDK 的根命名空间写得很清楚（``plugin/sdk/__init__.py``）：

    The `shared` subpackage is an internal implementation detail and
    should NOT be imported directly by plugin developers.

插件**只准**从公开门面导入：

| 允许 | 用途 |
|:--|:--|
| ``plugin.sdk.plugin`` | 插件开发主门面（``Ok`` / ``Err`` / ``SdkError`` / 装饰器 …） |
| ``plugin.sdk`` | SDK 根命名空间（``EVENT_META_ATTR`` 等常量） |
| ``plugin.sdk.adapter`` | 适配器开发门面 |

**而且门面导出的符号集随宿主版本而变** —— 源码树里有、运行时未必有：

* 0.7.0 直引 ``plugin.sdk.shared.core.cards`` → 启动即崩（``No module named``）
* 0.7.1 改引 ``plugin.sdk.plugin`` 的 ``CardSubmissionError`` → 启动即崩
  （``cannot import name``，Steam 版运行时的门面没有它）

所以本文件按**运行时实测**的白名单守门（``RUNTIME_FACADE_NAMES``），
并对已知不存在的符号单列黑名单（``KNOWN_ABSENT_IN_RUNTIME``）。
"""

from __future__ import annotations

import ast
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]

#: 插件**唯一**允许的宿主 SDK 导入前缀。
ALLOWED_SDK_PREFIXES = (
    "plugin.sdk.plugin",
    "plugin.sdk.adapter",
    "plugin.sdk",
)

#: 运行时门面的**真实** ``__all__``（从 Steam 版宿主 ``projectneko_server.exe``
#: 里提取）+ 官方自带插件实际 import 的模块属性。
#: 只认在这里的名字 —— 源码树里有、运行时未必有。
RUNTIME_FACADE_NAMES = frozenset(
    {
        # 结果契约
        "Ok", "Err", "Result", "unwrap", "unwrap_or", "SdkError", "TransportError",
        # 基类与元数据
        "NekoPluginBase", "PluginMeta", "NEKO_PLUGIN_META_ATTR", "NEKO_PLUGIN_TAG",
        # 装饰器
        "EntryKind", "neko_plugin", "on_event", "plugin_entry", "lifecycle",
        "message", "timer_interval", "custom_event", "hook", "before_entry",
        "after_entry", "around_entry", "replace_entry", "quick_action",
        "llm_tool", "LlmToolMeta",
        # 活动 / i18n / 配置 / 运行时
        "OsActivitySnapshot", "get_os_activity_snapshot", "PluginI18n", "tr",
        "PluginConfig", "Plugins", "PluginRouter", "SystemInfo", "PluginStore",
        "PluginSettings", "SettingsField", "PushMessageResult",
        "get_plugin_logger", "Plugin",
        # 模块属性（官方插件实际 import 过）
        "ui",
    }
)

#: **已知在宿主运行时里不存在**的符号（0.7.1 踩过的坑）。
#:
#: **0.7.4 实测补充**：Steam 版宿主（应用 0.9.0）的插件 SDK 门面里**压根
#: 没有卡片 API** —— 在冻结的 ``projectneko_server.exe`` 里扫过：
#: ``create_card`` / ``ChatCard`` / ``PluginView`` / ``html_card`` 全部
#: **0 次命中**（``card_fields`` / ``get_card`` 的命中都来自 QQ 人设卡、话题卡
#: 等**别的子系统**，不是插件 SDK）。
#:
#: 所以卡片必须**按能力探测**地用（``getattr(ctx, "create_card", None)``），
#: 不能硬调 ``self.ctx.create_card(...)`` ——那会直接 ``AttributeError``。
KNOWN_ABSENT_IN_RUNTIME = frozenset(
    {
        "CardSubmissionError",  # → cannot import name
        "ChatCard",             # → 同上（shared/core/cards.py 是新加的）
        "PluginView",           # → 同上
        "create_card",          # → 自由函数，运行时没有
    }
)

#: 宿主内部实现（插件一律不许碰）。
FORBIDDEN_HOST_ROOTS = {
    "plugin",  # 逐条判定：只允许上面三个公开门面
    "utils",  # 源码树根的工具包（plugin_card_fields 等）
    "main_logic",
    "app",
    "services",
    "core",
    "brain",
    "_types",
}


def _plugin_sources() -> list[Path]:
    """插件目录下全部 Python 源文件（排除缓存）。"""
    return sorted(
        path
        for path in _PLUGIN_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and ".git" not in path.parts
    )


def _absolute_imports(path: Path) -> list[tuple[str, int]]:
    """收集文件里所有**绝对** import 的完整模块名与行号（相对 import 不计）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import（from . import x）—— 插件内部自用，合法
                continue
            if node.module:
                found.append((node.module, node.lineno))
    return found


def _is_allowed(module: str) -> bool:
    if not module.startswith("plugin."):
        return True  # 非宿主 SDK 的绝对 import（标准库 / pytest 等），不归本测试管
    return any(module == prefix or module.startswith(prefix + ".") for prefix in ALLOWED_SDK_PREFIXES)


def test_no_private_sdk_imports_anywhere() -> None:
    """**全项目**扫描：不得 import 宿主内部实现（``plugin.sdk.shared.core`` 等）。"""
    offenders: list[str] = []
    for path in _plugin_sources():
        relative = path.relative_to(_PLUGIN_ROOT).as_posix()
        for module, lineno in _absolute_imports(path):
            root = module.split(".")[0]
            if root not in FORBIDDEN_HOST_ROOTS:
                continue
            if not _is_allowed(module):
                offenders.append(f"{relative}:{lineno} -> {module}")
    assert not offenders, (
        "插件不得导入宿主内部实现（只能走公开门面 plugin.sdk.plugin / plugin.sdk）："
        f"{offenders}"
    )


def test_shared_subpackage_is_never_imported() -> None:
    """``plugin.sdk.shared`` 一个字都不能出现（它在宿主里是内部实现）。"""
    offenders: list[str] = []
    for path in _plugin_sources():
        relative = path.relative_to(_PLUGIN_ROOT).as_posix()
        for module, lineno in _absolute_imports(path):
            if module == "plugin.sdk.shared" or module.startswith("plugin.sdk.shared."):
                offenders.append(f"{relative}:{lineno} -> {module}")
    assert not offenders, f"不得导入 plugin.sdk.shared.*：{offenders}"


def test_cards_are_created_through_the_public_context_api() -> None:
    """创建卡片**必须**走宿主 ctx 上的方法，绝不碰 ``shared`` 内部实现。

    历史事故：曾直接 import ``plugin.sdk.shared.core.cards.create_card``，
    导致打包后在真实宿主里启动即崩。

    **0.7.4 补充**：Steam 版宿主（0.9.0）的 ctx 上**没有** ``create_card``
    这个方法（实测：冻结二进制里 0 次命中），所以必须**按能力探测**地用
    ``getattr(ctx, "create_card", None)``，硬调 ``self.ctx.create_card(...)``
    会在真机上直接 ``AttributeError``。
    """
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # ① 不能再有裸 ``create_card`` 的自由函数导入
    bare_imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.endswith(".cards")
        and any(alias.name == "create_card" for alias in node.names)
    ]
    assert not bare_imports, f"不得导入 create_card 自由函数：{bare_imports}"

    # ② 必须通过 ctx 上的方法拿卡片 —— 两种合法写法：
    #    a) ``self.ctx.create_card(...)``（新宿主有这个方法时）
    #    b) ``getattr(self.ctx, "create_card", None)``（**能力探测**，0.9.0 走这条）
    direct_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_card"
    ]
    for call in direct_calls:
        assert isinstance(call.func.value, ast.Attribute), (
            f"create_card 必须是 ctx 上的方法调用（行 {call.lineno}）"
        )
        assert call.func.value.attr == "ctx", (
            f"create_card 应当挂在 ctx 上（行 {call.lineno}）"
        )

    probed = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "create_card"
    ]
    assert direct_calls or probed, (
        "插件应当通过 ctx.create_card(...) 或 getattr(ctx, 'create_card', None) 创建卡片"
    )


def test_card_creation_is_capability_detected() -> None:
    """卡片必须**按能力探测**地用，不能硬调 ``self.ctx.create_card(...)``。

    真机实测（Steam 版宿主 0.9.0）：``SdkContext`` 上**没有** ``create_card``
    ——硬调会直接 ``AttributeError``，把每次推送都变成一条告警。
    """
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    hard_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_card"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "ctx"
    ]
    assert not hard_calls, (
        "不得硬调 self.ctx.create_card(...)：宿主 0.9.0 的 ctx 没有这个方法，"
        f"必须用 getattr(self.ctx, 'create_card', None) 探测（行 {hard_calls}）"
    )

    probed = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "create_card"
    ]
    assert probed, "卡片投递必须用 getattr(ctx, 'create_card', None) 做能力探测"


def test_card_submission_error_is_not_imported() -> None:
    """**不得** import ``CardSubmissionError``（运行时门面里没有它）。

    0.7.1 引的 ``CardSubmissionError`` 在 Steam 版宿主里**不存在**：

        cannot import name 'CardSubmissionError' from 'plugin.sdk.plugin'

    → 插件启动即崩。任何"卡片异常类"都必须用 ``except Exception`` 兜底。
    """
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)

    assert "CardSubmissionError" not in imported, (
        "CardSubmissionError 在宿主运行时里不存在，不得 import"
    )

    # 也不许在代码里 except 它
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            names = (
                {node.type.id}
                if isinstance(node.type, ast.Name)
                else {n.id for n in ast.walk(node.type) if isinstance(n, ast.Name)}
            )
            assert "CardSubmissionError" not in names, (
                "不得针对 CardSubmissionError 写 except —— 用 except Exception 兜底"
            )


def test_only_runtime_verified_sdk_names_are_imported() -> None:
    """只 import **经运行时验证存在**的门面符号。

    依据：从 Steam 版宿主（``projectneko_server.exe``，Nuitka 编译）的运行时
    门面里提取出的真实 ``__all__``，加上官方自带插件实际 import 的模块属性。

    白名单只认"**在运行时被验证存在**"的名字 —— 源码树里有、运行时未必有
    （0.7.1 的 ``CardSubmissionError`` / ``create_card`` / ``ChatCard`` /
    ``PluginView`` 就是活生生的例子）。
    """
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    allowed = RUNTIME_FACADE_NAMES
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "plugin.sdk.plugin":
            continue
        for alias in node.names:
            if alias.name not in allowed:
                offenders.append(f"{alias.name}（行 {node.lineno}）")
    assert not offenders, (
        f"这些符号未在运行时验证存在，不得从 plugin.sdk.plugin 导入：{offenders}"
    )


def test_no_known_absent_sdk_symbol_is_used() -> None:
    """**已知在运行时不存在**的符号，一个都不许 import。

    只查**导入名**：``self.ctx.create_card(...)`` 是宿主 ``ctx`` 上的方法
    （运行时**有**这个方法），不是 import，不在黑名单范围内。
    """
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])

    offenders = sorted(imported & KNOWN_ABSENT_IN_RUNTIME)
    assert not offenders, f"这些符号在宿主运行时里不存在：{offenders}"


def test_plugin_imports_without_the_private_shared_subpackage() -> None:
    """在**没有** ``plugin.sdk.shared`` 的环境里，插件必须照样能 import。

    这是 0.7.0 启动事故的**复现脚本**。

    真实宿主只提供**公开门面**（``plugin.sdk.plugin`` 等），``plugin.sdk.shared``
    是宿主自己的内部实现，不会暴露给插件进程。0.7.0 的插件代码直引
    ``plugin.sdk.shared.core.cards`` → 打包进宿主后启动即崩：

        Process crashed: No module named 'plugin.sdk.shared.core.cards'

    本测试把 ``plugin.sdk.shared.*`` 从 ``sys.modules`` 里摘掉并挂上拦截器，
    再重新 import 插件 —— 模拟"宿主只给公开门面"的受限环境。
    """
    import importlib
    import sys

    # ① 公开门面先加载进来（它内部会用到 shared，但那是宿主自己的事）。
    #    用 importlib 而不是 ``import plugin.sdk.plugin``：后者会绑一个用不到的
    #    名字（ruff F401），而 CI 的 ruff 带 --ignore-noqa，noqa 救不了。
    importlib.import_module("plugin.sdk.plugin")

    private_names = [name for name in sys.modules if name.startswith("plugin.sdk.shared")]
    saved_private = {name: sys.modules[name] for name in private_names}
    for name in private_names:
        del sys.modules[name]

    plugin_names = [
        name for name in sys.modules if name.startswith("plugins.mock_stock_meow")
    ]
    saved_plugin = {name: sys.modules[name] for name in plugin_names}
    for name in plugin_names:
        del sys.modules[name]

    class _BlockPrivateShared:
        """拦截一切 ``plugin.sdk.shared*`` 的导入。"""

        def find_spec(self, fullname, path=None, target=None):
            if fullname.startswith("plugin.sdk.shared"):
                raise ModuleNotFoundError(f"No module named {fullname!r}")
            return None

    blocker = _BlockPrivateShared()
    sys.meta_path.insert(0, blocker)
    try:
        module = importlib.import_module("plugins.mock_stock_meow")
        assert module.PLUGIN_VERSION
        assert module.MockStockMeowPlugin is not None
    except ModuleNotFoundError as error:  # pragma: no cover - 回归时才会走到
        raise AssertionError(
            f"插件在只有公开门面的宿主环境里 import 失败：{error}"
        ) from error
    finally:
        sys.meta_path.remove(blocker)
        # 还原现场，不污染其它测试
        for name in list(sys.modules):
            if name.startswith("plugins.mock_stock_meow"):
                del sys.modules[name]
        sys.modules.update(saved_plugin)
        sys.modules.update(saved_private)
