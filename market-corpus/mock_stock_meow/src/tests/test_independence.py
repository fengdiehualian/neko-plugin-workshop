"""引擎层独立性单测：引擎不认识 N.E.K.O 的存在。

这是"纯标准库、纯 Python、可脱离宿主独立测试"这一前提的结构性守卫。
"""

from __future__ import annotations

import ast
import pathlib
import sys

ENGINE_DIR = pathlib.Path(__file__).resolve().parents[1] / "engine"


def _absolute_import_roots(path: pathlib.Path) -> set[str]:
    """收集文件里所有**绝对** import 的顶层模块名（相对 import 不计）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import（from . import x）
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_engine_imports_nothing_from_neko_runtime():
    forbidden = {"plugin", "utils", "main_logic", "app", "services", "core", "brain"}
    offenders = []
    for path in sorted(ENGINE_DIR.glob("*.py")):
        for root in _absolute_import_roots(path):
            if root in forbidden:
                offenders.append((path.name, root))
    assert offenders == [], f"引擎层不得依赖宿主模块：{offenders}"


def test_engine_uses_standard_library_only():
    """所有绝对 import 都必须来自标准库（因此也排除了 numpy 等第三方库）。"""
    offenders = []
    for path in sorted(ENGINE_DIR.glob("*.py")):
        for root in _absolute_import_roots(path):
            if root not in sys.stdlib_module_names:
                offenders.append((path.name, root))
    assert offenders == [], f"引擎层只允许标准库：{offenders}"


def test_engine_does_not_use_numpy():
    for path in sorted(ENGINE_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "numpy" not in text, f"{path.name} 不应引用 numpy"


def test_engine_modules_have_no_top_level_side_effects():
    """模块顶层只允许常量、类定义与函数定义。

    这里用 AST 检查顶层语句里不出现函数调用（``random.Random()`` 之类的
    实例化），也不出现 import 之外的表达式语句。
    """
    offenders = []
    for path in sorted(ENGINE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # docstring
            if isinstance(node, ast.Assign):
                # 只允许常量赋值（字面量 / 冻结 dataclass / 简单调用）
                value = node.value
                if isinstance(value, ast.Call):
                    func = value.func
                    name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if name not in {"MappingProxyType", "field"}:
                        offenders.append((path.name, node.lineno, name))
                continue
            if isinstance(node, ast.AnnAssign):
                continue
            offenders.append((path.name, getattr(node, "lineno", 0), type(node).__name__))
    assert offenders == [], f"模块顶层存在重活：{offenders}"


def test_engine_has_no_toplevel_random_instance():
    for path in sorted(ENGINE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "rng" in target.id.lower():
                        offenders = target.id
                        assert not isinstance(node.value, ast.Call), (
                            f"{path.name} 顶层不应实例化 RNG（{offenders}）"
                        )
