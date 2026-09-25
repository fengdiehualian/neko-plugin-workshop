"""插件模块里 ``self.plugin.X`` 引用的属性，必须真的存在于插件上。

**为什么要有这条看门狗**：``01ce81c``（入口收束 48→7）把 ``_record_backlog_message``
并进了 ``backlog_service.record_message`` 并把前者删掉，但 ``message_dispatcher.py``
那个调用点漏改了。后果是**每一条进来的 QQ 消息**都在 ``AttributeError`` 上炸掉，
处理链根本没往下走 —— 而且它在静态检查、单测、CI 里**全绿**，只有真机收到消息
那一刻才暴露。

这类"重构删了方法、调用点留下"的漏洞，靠跑测试是抓不住的：出错的分支得先被走到。
所以这里把整个引用面直接钉死 —— 方法一旦被删而调用点还在，这条立刻红。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]

#: 不在类上、由宿主 / SDK 在运行时提供的属性（不是插件自己定义的方法）。
#: 每加一个都要说明来源 —— 这个白名单一旦放宽，看门狗就形同虚设。
_RUNTIME_PROVIDED = {
    "i18n",       # 宿主注入的翻译表，插件代码大量使用
    "logger",     # SDK 基类在实例上设的
    "plugin_id",  # 同上
}


def _scan() -> tuple[dict[str, list[str]], set[str]]:
    """返回（``self.plugin.X`` 的引用表, 插件自己赋值过的属性集合）。"""
    refs: dict[str, list[str]] = {}
    assigned: set[str] = set()

    for f in sorted(_PLUGIN_ROOT.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)):
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "self":
                assigned.add(node.attr)                       # self.X = ...
            elif (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                  and target.value.id == "self" and target.attr == "plugin"):
                assigned.add(node.attr)                       # self.plugin.X = ...

        for m in re.finditer(r"self\.plugin\.([A-Za-z_]\w*)", src):
            line = src[:m.start()].count("\n") + 1
            refs.setdefault(m.group(1), []).append(f"{f.name}:{line}")

    return refs, assigned


def test_every_self_plugin_reference_resolves():
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    refs, assigned = _scan()
    known = assigned | set(dir(QQAutoReplyPlugin)) | _RUNTIME_PROVIDED
    missing = {name: where for name, where in refs.items() if name not in known}

    assert not missing, (
        "以下 `self.plugin.X` 引用在插件上不存在 —— 多半是重构删了方法、调用点漏改：\n"
        + "\n".join(f"  {name}  ← {', '.join(where[:3])}"
                    for name, where in sorted(missing.items())))
