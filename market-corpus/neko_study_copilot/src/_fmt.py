"""统一输出排版记号。

以前各模块各写各的：``'=' * 8 标题 '=' * 8``、``  · 条目``、靠空格对齐的数字。
在聊天里勉强能看，在面板里就是一坨等宽文本——既不美观也不好读。

现在统一成一组记号，面板前端（``static/index.html`` 的 ``renderRich``）认识它们，
会渲染成小节标题、列表、进度条、提示条；聊天里这些记号本身也是可读的纯文本。

=========== ==============================
``## 标题``  小节标题
``- 文本``   无序列表项
``1. 文本``  有序列表项
``> 文本``   提示条（需要提醒注意的事）
``**粗体**`` 强调
``---``      分隔线
``[[bar:.62]]`` 进度条，值域 0~1
=========== ==============================
"""

from __future__ import annotations

from typing import Any, Iterable


def title(text: Any) -> str:
    """小节标题。"""
    return f"## {text}"


def rule() -> str:
    """分隔线。"""
    return "---"


def bullet(text: Any) -> str:
    """无序列表项。"""
    return f"- {text}"


def bullets(items: Iterable[Any]) -> str:
    """一组无序列表项（自动跳过空项）。"""
    return "\n".join(bullet(item) for item in items if str(item).strip())


def numbered(items: Iterable[Any]) -> str:
    """有序列表项。"""
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1) if str(item).strip())


def note(text: Any) -> str:
    """提示条：想让用户真的注意到的事写在里面。"""
    return f"> {text}"


def bold(text: Any) -> str:
    return f"**{text}**"


def kv(label: Any, value: Any) -> str:
    """`- **标签**：值` —— 面板会渲染成一行「标签 + 值」。"""
    return f"- **{label}**：{value}"


def bar(rate: float, width: int = 12) -> str:
    """进度条记号，值会被夹到 0~1。"""
    try:
        value = float(rate)
    except (TypeError, ValueError):
        value = 0.0
    value = min(max(value, 0.0), 1.0)
    filled = int(round(value * width))
    return f"[[bar:{value:.2f}|{width}]]" if filled >= 0 else ""


def pct(rate: float) -> str:
    """百分比文本（容错，非法值当 0）。"""
    try:
        return f"{float(rate) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


def section(heading: Any, body: Any) -> str:
    """标题 + 正文，中间不空行，避免面板里出现大片空白。"""
    text = str(body or "").strip()
    return f"{title(heading)}\n{text}" if text else str(title(heading))


def join(*blocks: Any) -> str:
    """把小节拼起来，自动丢掉空块，并统一成「小节之间空一行」。"""
    parts = [str(block).strip() for block in blocks if str(block or "").strip()]
    return "\n\n".join(parts)
