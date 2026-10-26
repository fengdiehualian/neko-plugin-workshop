"""P2 测试夹具。

三件事
------
1. 让 pytest 能直接 ``import engine``（插件根目录加入 ``sys.path``）——
   这是 P1 引擎层单测的既有约定。
2. 让 ``import plugins.mock_stock_meow`` 可用：把 ``plugins/`` 的父目录加入
   ``sys.path``，``plugins`` 成为一个 **PEP 420 命名空间包**。这与宿主加载
   插件的方式一致（``plugin/core/host.py`` 要求 ``parts[0] == "plugins"``），
   因此本插件内部一律使用**相对导入**（``from .engine...`` / ``from ..engine...``），
   在测试里也能原样复现。
3. 提供假 ctx + 已建表的插件实例，让入口测试跑在**真实的**宿主 SDK 存储之上
   （``self.db`` / ``self.state`` 由 ``NekoPluginBase.__init__`` 创建，
   插件不得自己 new）。数据根目录由 ``NEKO_STORAGE_SELECTED_ROOT`` 隔离到
   ``tmp_path``，绝不污染真实用户数据。
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Coroutine

import pytest

#: 插件自身目录（``.../plugins/mock_stock_meow``）。
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
#: ``plugins/`` 命名空间包的父目录（``.../plugin``）。
_PLUGINS_PARENT = _PLUGIN_ROOT.parents[1]

# 插件自身目录放最前：``engine`` / ``modes`` / ``storage`` 只此一家。
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

# ``plugins/`` 的父目录必须**追加**到末尾，不能插到最前 ——
# 市场 CI 里它就是宿主树的 ``plugin/``，而宿主树的 ``plugin/utils`` 是一个
# **正规包**（带 ``__init__.py``）。插到最前会遮蔽宿主根的 ``utils``，
# 而 ``utils/plugin_card_fields.py`` 正住在宿主根的 ``utils/`` 里 ⇒ 宿主 SDK
# （``plugin.sdk.plugin`` → ``plugin.sdk.shared.core.cards``）会在 import 时炸：
# ``No module named 'utils.plugin_card_fields'``。
# 追加到末尾就不遮蔽任何东西（``plugins`` 是 PEP 420 命名空间包，在路径末尾照样能解析）。
if str(_PLUGINS_PARENT) not in sys.path:
    sys.path.append(str(_PLUGINS_PARENT))

# 宿主 SDK（``plugin.sdk.plugin``）由 venv 的可编辑安装提供；源码树不在
# ``sys.path`` 上时给出明确报错，而不是让人猜。
def _import_host_sdk() -> Any:
    """真正 import 宿主 SDK（会连带把它的传递依赖也检查一遍）。"""
    import plugin.sdk.plugin as _host_sdk

    return _host_sdk


def _ensure_host_sdk() -> None:
    """确保宿主 SDK 可导入；失败时把**真正缺的那个模块**带出来。

    市场 CI 的 Release check 会在**插件目录**里跑 pytest（``cwd=插件目录``），
    此时 ``plugin`` 包不在 ``sys.path`` 上（只有 ``plugins`` 在），而 CI 的
    venv 里未必把它装成可编辑包 —— 于是 ``import plugin.sdk.plugin`` 会失败。

    所以：先常规 import；失败就**向上找**带 ``plugin/sdk/plugin`` 的宿主树根，
    把它**追加**到 ``sys.path`` 末尾再 import（追加在末尾，不遮蔽已安装的 SDK）。
    """
    try:
        _import_host_sdk()
        return
    except ModuleNotFoundError as error:
        first_error = error
    for parent in _PLUGIN_ROOT.parents:
        if (parent / "plugin" / "sdk" / "plugin").exists():
            if str(parent) not in sys.path:
                sys.path.append(str(parent))
            break
    # 半路失败的 import 会把 ``plugin`` 留在 sys.modules 里且 ``__path__`` 不完整，
    # 补完路径必须把残留清掉才能重新解析。
    for name in [n for n in sys.modules if n == "plugin" or n.startswith("plugin.")]:
        del sys.modules[name]
    try:
        _import_host_sdk()
    except ModuleNotFoundError as error:
        raise RuntimeError(
            f"宿主 SDK 不可用：缺少模块 {error.name!r}（首次报错：{first_error}）。"
            "请用 N.E.K.O-main/.venv 的 python 运行测试，"
            "或确认宿主树里存在 plugin/sdk/plugin。"
        ) from first_error


_ensure_host_sdk()


__all__ = ["FakeCtx", "code_fingerprint", "engine_mode_code_changes", "make_plugin", "run"]


def code_fingerprint(source: str) -> str:
    """Python 源码的「代码指纹」：剥掉 docstring 后取 AST dump 的 SHA256。

    **注释不参与**（``ast`` 本来就不保留注释），**docstring 也被剥掉**
    ⇒ 注释与文档字符串可以自由改（例如清理版权字眼），
    而任何**可执行代码**的改动 —— 哪怕只差一个空格外的常量 ——
    都会改变指纹 ⇒ 红线依然守得住。

    这是 P15.0 把守卫从「文件名 diff」升级到「代码指纹」的核心。
    """
    tree = ast.parse(source)
    for node in list(ast.walk(tree)):
        if not isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.dump(tree).encode("utf-8")).hexdigest()


def engine_mode_code_changes() -> list[str]:
    """引擎层 / 模式层里「**代码**变了」的文件列表（相对 ``HEAD``）。

    只看代码指纹，不看文件名 ⇒ 纯注释 / docstring 的改动**不算**违规。
    CI 里 checkout 后工作树 == HEAD ⇒ 恒为空。
    """
    changed: list[str] = []
    for layer in ("engine", "modes"):
        for path in sorted((_PLUGIN_ROOT / layer).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(_PLUGIN_ROOT).as_posix()
            result = subprocess.run(
                ["git", "show", f"HEAD:{rel}"],
                cwd=str(_PLUGIN_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                changed.append(f"{rel}（HEAD 里没有这个文件）")
                continue
            try:
                before = code_fingerprint(result.stdout)
                after = code_fingerprint(path.read_text(encoding="utf-8"))
            except SyntaxError as error:
                changed.append(f"{rel}（语法错误：{error}）")
                continue
            if before != after:
                changed.append(rel)
    return changed


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    """同步地跑一个协程（避免依赖 pytest-asyncio）。"""
    return asyncio.run(coro)


class FakeCtx:
    """最小可用的宿主 ctx。

    只实现插件真正用到的那几个成员：``plugin_id`` / ``logger`` /
    ``config_path`` / ``metadata`` / ``bus`` / ``_effective_config`` /
    ``get_own_config``。其余成员由 SDK 的 :class:`SdkContext` 兜底。
    """

    plugin_id = "mock_stock_meow"
    logger = None

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or Path("mock_stock_meow.toml")
        self.metadata: dict[str, Any] = {}
        self.bus: dict[str, Any] = {}
        self._effective_config: dict[str, Any] = {
            "plugin": {
                "store": {"enabled": True},
                "database": {"enabled": True, "name": "mock_stock_meow.db"},
            },
            "plugin_state": {"backend": "file"},
        }
        self.config_payload: dict[str, Any] = {}

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        return {"config": self.config_payload}

    async def update_own_config(
        self, updates: dict[str, Any], timeout: float = 10.0
    ) -> dict[str, Any]:
        self.config_payload.update(updates)
        return {"config": self.config_payload}

    async def get_system_config(self, timeout: float = 5.0) -> dict[str, Any]:
        return {"config": {}}


def make_plugin(ctx: FakeCtx) -> Any:
    """按宿主的加载方式实例化插件（``plugins.mock_stock_meow`` 包内导入）。"""
    from plugins.mock_stock_meow import MockStockMeowPlugin

    return MockStockMeowPlugin(ctx)


@pytest.fixture()
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCtx:
    """假 ctx + 隔离的数据根目录。"""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    return FakeCtx(config_path=tmp_path / "plugin.toml")


@pytest.fixture()
def plugin(ctx: FakeCtx) -> Any:
    """已执行 startup（建表完成）的插件实例。"""
    instance = make_plugin(ctx)
    result = run(instance.on_startup())
    assert result.is_ok()
    return instance
