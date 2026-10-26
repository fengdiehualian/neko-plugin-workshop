# -*- coding: utf-8 -*-
"""看门狗：破坏性操作不许再用浏览器原生 `confirm()`，必须走页内确认框。

**为什么要钉**（2026-09-27 使用者报的「napcat页点击删除无效」）：

1. 原生 `confirm()` 在嵌入式 / 沙箱化页面里**会被静默拦掉** —— 不弹窗、直接返回 false；
   于是 `if(!confirm(...)) return;` 这一句就成了"点了完全没反应"，连提示都没有。
2. 现场取证（docs/SESSION-HANDOFF.md §18）：后端删除本身是好的（可逆演练删掉了真实
   表情包再逐字节还原），前端函数也是好的（真 Chromium 里点一下确实走到
   `asset action=delete_sticker`），但使用者那次点击**一次入口调用都没有** ——
   中间只剩原生确认框这一道门，而它连弹窗都没出现。
3. 页内确认框（`static/ui-confirm.js`）不依赖任何浏览器策略：点一下必然产生可观察的结果，
   而且**焦点落在取消上**（回车不该顺手删东西）。

这条看门狗同时钉住"换成页内确认"这件事不会被悄悄改回原生 confirm。
"""

from __future__ import annotations

import pathlib
import re

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
STATIC = PLUGIN_DIR / "static"

#: 会用到确认框的前端（含共享脚本）。
FRONTENDS = ("napcat.html", "open_platform.html", "status.html", "script.js", "nav.js", "ui-confirm.js")

#: 破坏性处理函数 → 它必须在文件里出现 UIConfirm.ask(
DESTRUCTIVE_HANDLERS = (
    ("napcat.html", "deleteSticker"),
    ("napcat.html", "doForgetGroupMemory"),
    ("napcat.html", "resetPromptOverride"),
    ("open_platform.html", "deleteSticker"),
    ("open_platform.html", "resetPromptOverride"),
)

#: 需要加载确认框的页面。
PAGES_WITH_CONFIRM = ("napcat.html", "open_platform.html")


def _text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_no_native_confirm_alert_or_prompt_in_frontends():
    """原生 confirm/alert/prompt 一律不许再出现（ui-confirm.js 的**注释**除外）。"""
    offenders: list[str] = []
    for name in FRONTENDS:
        path = STATIC / name
        if not path.is_file():
            continue
        for lineno, line in enumerate(_text(name).split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith(("/*", "*", "//", "<!--")):
                continue
            for m in re.finditer(r"\b(confirm|alert|prompt)\s*\(", line):
                offenders.append(f"{name}:{lineno}: {m.group(0)}")
    assert not offenders, (
        "前端又用上了原生对话框（沙箱里会被静默拦掉，表现为“点了没反应”）：\n"
        + "\n".join(offenders)
    )


def test_destructive_handlers_use_the_in_page_confirmer():
    for name, func in DESTRUCTIVE_HANDLERS:
        text = _text(name)
        at = text.find(f"function {func}(")
        assert at >= 0, f"{name} 里找不到 {func}"
        body = text[at:at + 400]
        assert "UIConfirm.ask(" in body, (
            f"{name} 的 {func} 没有走页内确认框 —— 换回原生 confirm 就会重演“点了没反应”"
        )


def test_pages_load_the_shared_confirmer():
    for name in PAGES_WITH_CONFIRM:
        text = _text(name)
        assert re.search(r'<script[^>]+src="[^"]*ui-confirm\.js', text), (
            f"{name} 没有加载 ui-confirm.js —— UIConfirm 会是 undefined，删除照样点不动"
        )


def test_confirmer_is_self_contained_and_safe():
    text = _text("ui-confirm.js")
    assert "global.UIConfirm" in text, "ui-confirm.js 没有挂出 UIConfirm"
    assert "textNode.textContent" in text, (
        "确认框文案要用 textContent 写（消息里带 < > 不能被当 HTML 解析）"
    )
    assert "resolve(false)" in text, "拿不到 document 时必须拒绝动手（不能静默放行）"
    assert "cancel.focus()" in text, "焦点要落在取消上：回车不该顺手删东西"
    # 中文 + 英文两份文案键都得在包里（bundle 键集一致性由 i18n 那条看门狗兜着）
    for key in ("ui.shared.btn.cancel", "ui.shared.btn.confirm"):
        assert key in text, f"ui-confirm.js 没引用 {key}"
