"""P5 · Hosted TSX 静态 lint（自带，不碰源码树）。

为什么自带
----------
官方校验器是 ``cd frontend/plugin-manager && npm run check-hosted-tsx -- plugin/plugins/<id>``，
但它 ``import ts from 'typescript'``，**需要 node_modules**。当前仓库里没有
``node_modules``，而 ``npm install`` 会往**源码树**里写东西 —— 违反"不碰源码树"
的红线。

所以这里按 P5 契约 §4.1 的规则**自己实现**一套静态扫描，规则与官方一致
（逐条对应 ``.agent/skills/neko-plugin/references/hosted-ui-authoring.md``）。
P6 若允许在源码树装依赖，必须**补跑官方校验器**作二次确认。

守的线：

1. 有 ``export default function`` 入口；
2. 只从 ``@neko/plugin-ui`` 导入（**禁** npm 包与外部裸模块）；
3. **禁**动态 ``import()``；
4. **禁** class 组件 / Context / portals / Suspense / dangerouslySetInnerHTML；
5. 相对依赖 **≤32 文件**、**≤512 KiB**，且必须留在插件根目录内；
6. 链接器限制：**禁** ``export *`` / 导出列表 / enum / generator / 抽象类 /
   解构导出 / 多声明符导出；
7. 面板**不引用**任何局中秘密字段（heat / seed / reversal / accuracy）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_UI_DIR = _PLUGIN_ROOT / "ui"
_PANEL = _UI_DIR / "panel.tsx"

#: 官方限制：相对依赖 32 个文件 / 512 KiB。
MAX_RELATIVE_FILES = 32
MAX_RELATIVE_BYTES = 512 * 1024

#: 允许的裸模块（只有这一个）。
ALLOWED_BARE_MODULES = {"@neko/plugin-ui"}

#: 禁用的构造（逐条对应 P5 契约 §4.1）。
BANNED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bimport\s*\(", "动态 import()：Hosted TSX 禁用"),
    (r"\bdangerouslySetInnerHTML\b", "dangerouslySetInnerHTML：禁用"),
    (r"\bcreateContext\b", "React Context：运行时没有"),
    (r"\buseContext\b", "React Context：运行时没有"),
    (r"\bcreatePortal\b", "portals：运行时没有"),
    (r"\bReactDOM\b", "ReactDOM：运行时没有"),
    (r"\bSuspense\b", "Suspense：运行时没有"),
    (r"\bfrom\s+['\"]react(?:-dom)?['\"]", "React：运行时是轻量 React-like，不是 React"),
    (r"\bclass\s+\w+\s+extends\b", "class 组件：禁用"),
    (r"\bexport\s+\*", "export *：链接器不支持"),
    (r"^\s*export\s*\{", "导出列表：链接器不支持"),
    (r"\benum\s+\w+", "enum：链接器不支持"),
    (r"\bfunction\s*\*", "generator：链接器不支持"),
    (r"\babstract\s+class\b", "抽象类：链接器不支持"),
)

#: 面板**不引用**的局中秘密字段。
FORBIDDEN_FIELDS = ("heat", "seed", "reversal", "accuracy", "prey_index")

_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:type\s+)?(?P<names>[^'\"]+?)\s*from\s*['\"](?P<module>[^'\"]+)['\"]",
    re.MULTILINE,
)


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """剥掉注释。

    扫描要针对**代码**，不是注释 —— 面板顶部的说明里本来就要写"禁
    ``import()``""不显示 heat"这类字样，扫注释等于自伤。
    """
    without_block = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", without_block)


def _relative_imports(source: str) -> list[str]:
    """相对导入的模块说明符。"""
    return [
        match.group("module")
        for match in _IMPORT_RE.finditer(source)
        if match.group("module").startswith(".")
    ]


def _bare_imports(source: str) -> list[str]:
    """裸模块导入（相对路径之外的）。"""
    return [
        match.group("module")
        for match in _IMPORT_RE.finditer(source)
        if not match.group("module").startswith(".")
    ]


def _resolve_relative(module: str) -> Path | None:
    """把相对导入解析成实际文件（``.tsx`` / ``.ts`` / ``.jsx`` / ``.js`` / 目录索引）。"""
    base = (_PANEL.parent / module).resolve()
    candidates = [
        Path(str(base) + suffix) for suffix in (".tsx", ".ts", ".jsx", ".js")
    ]
    candidates.extend(
        base / f"index{suffix}" for suffix in (".tsx", ".ts", ".jsx", ".js")
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------- 基本结构


def test_panel_entry_exists() -> None:
    assert _PANEL.is_file(), "ui/panel.tsx 不存在"
    assert _PANEL.stat().st_size > 0


def test_panel_has_a_default_exported_function() -> None:
    """入口必须是 ``export default function``（不是 class、不是箭头常量）。"""
    source = _panel_source()
    assert re.search(r"export\s+default\s+function\s+\w+", source), (
        "面板必须导出 default function 组件"
    )


def test_panel_imports_the_surface_props_type() -> None:
    source = _panel_source()
    assert "PluginSurfaceProps" in source
    assert "from \"@neko/plugin-ui\"" in source or "from '@neko/plugin-ui'" in source


# ---------------------------------------------------------------- 导入规则


def test_only_plugin_ui_may_be_imported() -> None:
    """**禁** npm 包与外部裸模块。"""
    for module in _bare_imports(_panel_source()):
        assert module in ALLOWED_BARE_MODULES, f"不允许的裸模块导入：{module}"


def test_no_dynamic_import() -> None:
    code = _strip_comments(_panel_source())
    assert "import(" not in code.replace("import (", "")


def test_relative_dependencies_stay_inside_the_plugin() -> None:
    """相对依赖必须留在插件根目录内。"""
    for module in _relative_imports(_panel_source()):
        resolved = _resolve_relative(module)
        if resolved is None:
            continue
        assert str(resolved).startswith(str(_PLUGIN_ROOT.resolve())), (
            f"相对依赖越出了插件根目录：{module}"
        )


def test_relative_dependency_budget() -> None:
    """相对依赖 ≤32 文件 / ≤512 KiB。"""
    files: list[Path] = []
    total = 0
    for module in _relative_imports(_panel_source()):
        resolved = _resolve_relative(module)
        if resolved is None or resolved in files:
            continue
        files.append(resolved)
        total += resolved.stat().st_size
    assert len(files) <= MAX_RELATIVE_FILES, f"相对依赖 {len(files)} 个文件，超限"
    assert total <= MAX_RELATIVE_BYTES, f"相对依赖 {total} 字节，超限"


# ---------------------------------------------------------------- 禁用构造


@pytest.mark.parametrize("pattern,reason", BANNED_PATTERNS)
def test_banned_constructs_are_absent(pattern: str, reason: str) -> None:
    code = _strip_comments(_panel_source())
    match = re.search(pattern, code, re.MULTILINE)
    assert match is None, f"{reason}（命中：{match.group(0) if match else ''}）"


def test_no_multideclarator_or_destructured_exports() -> None:
    """链接器只支持"简单单绑定"导出。"""
    code = _strip_comments(_panel_source())
    assert not re.search(r"^\s*export\s+const\s+[^=\n]*,\s*\w+\s*=", code, re.MULTILINE), (
        "多声明符导出：链接器不支持"
    )
    assert not re.search(r"^\s*export\s+(const|let|var)\s*[\{\[]", code, re.MULTILINE), (
        "解构导出：链接器不支持"
    )


# ---------------------------------------------------------------- 局中隐藏


def test_panel_never_references_forbidden_fields() -> None:
    """面板**不引用**任何局中秘密字段。"""
    code = _strip_comments(_panel_source())
    for field in FORBIDDEN_FIELDS:
        assert not re.search(rf"\b{field}\b", code), f"面板引用了局中秘密字段：{field}"


def test_brackets_are_balanced() -> None:
    """括号配平（忽略字符串与注释）—— 手写 JSX 最容易犯的低级错。"""
    code = _strip_comments(_panel_source())
    # 去掉字符串字面量
    code = re.sub(r"'(?:\\.|[^'\\])*'", "''", code)
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    code = re.sub(r"`(?:\\.|[^`\\])*`", "``", code)
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for char in code:
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            assert stack, f"多了一个 {char}"
            assert stack.pop() == pairs[char], f"{char} 没有配对"
    assert not stack, f"还有没闭合的：{stack}"


def test_panel_implements_every_contract_block() -> None:
    """P5 契约 §4.2 的 11 个区块都要在面板里出现。"""
    source = _panel_source()
    for marker in (
        "kline_stock_ids",  # ⑦ K 线
        "positions",  # ④ 持仓
        "concentration",  # ⑤ 集中度
        "stocks",  # ⑥ 行情
        "catgirl",  # ⑨ 猫娘
        "news_today",  # ⑩ 新闻
        "settlement",  # ⑪ 结算报告
        "daily_reject",  # ⑧ 二道贩子拒绝
        "daily_claim",
        "daily_loan",
        "analyze",
        "news_detail",
    ):
        assert marker in source, f"面板缺少区块：{marker}"
