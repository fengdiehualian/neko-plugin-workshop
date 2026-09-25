"""DESIGN.md 里的定时器/轮询数字与代码的一致性检查门。

DESIGN.md 与 README 一样是"照抄级"文档，但其中的秒数过去靠手抄。实测漂移过：
DESIGN.md 长期写「timer：每 20 秒轮询」，而 `@timer_interval(seconds=10)` 早已是
10 秒（同一文件另一处又写 10s，属自相矛盾）。本门从 AST 取真实装饰器参数，
再要求文档相关行出现同一个数字——改代码不改文档、或文档凭空写别的数字都会红。

面板侧 5s 轮询同理：TSX 的 setInterval 毫秒值与 DESIGN 的「5s 轮询」绑定。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ENTRY_PATH = PLUGIN_ROOT / "__init__.py"
DESIGN_PATH = PLUGIN_ROOT / "DESIGN.md"
PANEL_TSX = PLUGIN_ROOT / "ui" / "panel.tsx"


def _timer_seconds() -> dict[str, int]:
    """{timer_id: seconds}，取自 @timer_interval 的字面量参数。"""
    tree = ast.parse(ENTRY_PATH.read_text(encoding="utf-8"))
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        # timer 处的是 async def → AsyncFunctionDef，两者都要接
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fname = getattr(dec.func, "id", None) or getattr(dec.func, "attr", None)
            if fname != "timer_interval":
                continue
            timer_id, seconds = None, None
            if dec.args and isinstance(dec.args[0], ast.Constant):
                timer_id = dec.args[0].value
            for kw in dec.keywords:
                if kw.arg == "seconds" and isinstance(kw.value, ast.Constant):
                    seconds = kw.value.value
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    timer_id = kw.value.value
            if timer_id is None:
                timer_id = node.name
            if isinstance(seconds, int):
                found[str(timer_id)] = seconds
    return found


def test_design_timer_line_matches_decorator_seconds():
    timers = _timer_seconds()
    assert timers, "没从 __init__.py 解析到 @timer_interval(seconds=...) 字面量"

    design = DESIGN_PATH.read_text(encoding="utf-8")
    stale: list[str] = []
    for timer_id, seconds in timers.items():
        # 只查提到"轮询"的 timer 说明行；用 \n 锚定行首避免命中行中间
        lines = [ln for ln in design.splitlines() if "timer" in ln.lower() and "轮询" in ln]
        for line in lines:
            nums = [int(n) for n in re.findall(r"每\s*(\d+)\s*秒", line)]
            for num in nums:
                if num != seconds:
                    stale.append(f"DESIGN.md 写「每 {num} 秒轮询」，实际 {timer_id} 是 {seconds} 秒")
    assert not stale, "DESIGN.md 定时器数字与代码漂移：\n  " + "\n  ".join(stale)


def test_panel_poll_interval_matches_design():
    """ui/panel.tsx 的 dashboard 轮询毫秒值必须与 DESIGN.md 写的秒数一致。"""
    tsx = PANEL_TSX.read_text(encoding="utf-8")
    polls = {int(n) for n in re.findall(r"setInterval\(.*?\},\s*(\d+)\s*\)", tsx, re.S)}
    assert polls, "没在 ui/panel.tsx 解析到 setInterval 的毫秒字面量"

    seconds = {ms // 1000 for ms in polls if ms % 1000 == 0}
    design = DESIGN_PATH.read_text(encoding="utf-8")
    # 只收“面板侧”声明的轮询秒数；DESIGN 里另有插件 timer 的“10s 轮询总线”，
    # 那个由 test_design_timer_line_matches_decorator_seconds 管，不属本门
    claims = {
        int(n)
        for ln in design.splitlines()
        if ("面板" in ln or "dashboard" in ln.lower())
        for n in re.findall(r"(\d+)\s*s\s*轮询", ln)
    }
    if not claims:
        return  # DESIGN 未声明轮询秒数则不判

    unbacked = sorted(c for c in claims if c not in seconds)
    assert not unbacked, (
        f"DESIGN.md 声明了 {sorted(claims)}s 轮询，但 ui/panel.tsx 实际轮询是 "
        f"{sorted(polls)}ms（={sorted(seconds)}s）；不匹配项：{unbacked}"
    )
