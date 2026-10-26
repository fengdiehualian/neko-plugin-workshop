"""plugin.toml 的 [[plugin.entries]] 必须与 @plugin_entry 装饰器一一对应。

宿主重扫插件时只按静态声明重建入口列表（没有 plugin.meta.json 时），漏声明的
入口在一次 /plugins/refresh 之后就从面板消失。用 ast 读源码，不 import 插件。
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _decorated_entries() -> dict[str, str]:
    tree = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and getattr(deco.func, "id", None) == "plugin_entry"):
                continue
            kw = {k.arg: k.value for k in deco.keywords}
            entry_id = kw["id"].value if "id" in kw else node.name
            found[entry_id] = kw["name"].value if "name" in kw else ""
    return found


def test_manifest_declares_every_decorated_entry() -> None:
    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    declared = {e["id"]: e.get("name", "") for e in manifest["plugin"]["entries"]}
    assert declared == _decorated_entries()
