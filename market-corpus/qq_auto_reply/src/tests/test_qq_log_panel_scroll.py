"""日志面板必须**可滚动**，且持续刷新时不得把用户拽回底部。

现场：运行日志"滚不动"。两个面板的实现不一致：

- ``open_platform.html`` 的 ``#log-content`` 有 ``max-height:500px;overflow-y:auto``
  —— 元素自己滚动，``el.scrollTop = el.scrollHeight`` 有效；
- ``napcat.html`` 的同名元素**没有高度约束、也没有 overflow** —— 真正在滚的是外层
  ``#content``（``overflow-y:auto``）。于是 ``loadLogs()`` 里那行
  ``el.scrollTop = el.scrollHeight`` 是**空操作**：设在一个不滚动的元素上。

日志由 SSE（约 1.5s 节流）与 30s 兜底轮询**持续整体重写**，而重写会把滚动位置
重置到顶部、并清掉文本选区 —— 叠加"贴底失效"就表现为读不了、滚不动。

两个面板都已修：元素给出自己的高度与 overflow，并把"贴底"改成**仅在用户本就
在底部时才跟随**（距底 > 40px 说明人正在往上读，别动它）。
"""
from __future__ import annotations

import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parents[1]
PLATFORM_PAGES = ("napcat.html", "open_platform.html")


def _html(name: str) -> str:
    return (BASE / "static" / name).read_text(encoding="utf-8")


def _log_div_style(name: str) -> str:
    """抠出 ``#log-content`` 这个标签的 style 属性内容。"""
    html = _html(name)
    m = re.search(r'<div[^>]*id="log-content"[^>]*style="([^"]*)"', html)
    assert m, f"{name}: 找不到 id=log-content 的 div"
    return m.group(1)


def test_log_content_is_its_own_scroll_container():
    """``#log-content`` 必须有高度上限 + overflow，否则它根本不滚动。

    这是"滚不动"的直接原因：元素无限长时滚动发生在外层 ``#content``，
    而 ``loadLogs`` 把 scrollTop 设在 ``#log-content`` 上，等于没设。
    """
    for name in PLATFORM_PAGES:
        style = _log_div_style(name)
        assert "max-height" in style or "height" in style, (
            f"{name}: #log-content 没有高度约束，它不会成为滚动容器（style={style!r}）"
        )
        assert "overflow-y:auto" in style.replace(" ", ""), (
            f"{name}: #log-content 缺 overflow-y:auto（style={style!r}）"
        )


def test_auto_scroll_only_follows_when_already_at_the_bottom():
    """持续刷新时不许把正在往上读的用户拽回底部。

    判据必须**在重写 textContent 之前**读取 —— 重写本身就会把滚动位置重置到
    顶部，之后再量就永远是"不在底部"（那样自动滚动会彻底失效），或永远是
    "在底部"（那样用户被拽回）。两者都错，所以顺序是要钉住的行为。
    """
    for name in PLATFORM_PAGES:
        html = _html(name)
        # loadLogs 是"一行一个函数"的写法：从 async function loadLogs(){ 取到该行末尾
        m = re.search(r"async function loadLogs\(\)\{(.*)$", html, re.M)
        assert m, f"{name}: 找不到 loadLogs 函数体"
        body = m.group(1)
        assert "scrollHeight" in body and "scrollTop" in body, (
            f"{name}: loadLogs 不再处理滚动（读日志时会停在顶部）"
        )
        # 有"是否已贴底"的判断，而不是无条件贴底
        stick_check = re.search(r"scrollHeight\s*-\s*\w+\.scrollTop\s*-\s*\w+\.clientHeight\s*<=", body)
        assert stick_check, (
            f"{name}: loadLogs 缺少「距底 <= N 才跟随」的判断，会在用户上翻时把他拽回底部"
        )
        # 判断必须先于内容重写
        assign_at = body.find("textContent=")
        assert assign_at != -1, f"{name}: loadLogs 里找不到 textContent 赋值"
        assert stick_check.start() < assign_at, (
            f"{name}: 贴底判断写在 textContent 重写**之后** —— 重写已把滚动位置重置，"
            f"此时测量结果无意义"
        )


def test_log_poll_is_only_running_on_the_logs_page():
    """轮询不得在其他页面空转（否则切走后仍在后台打 /runs 拉日志）。"""
    for name in PLATFORM_PAGES:
        html = _html(name)
        assert "function stopLogPoll" in html, f"{name}: 缺 stopLogPoll"
        # 必须有某个分支停掉它，而不是只启动不停止
        assert re.search(r"stopLogPoll\(\)", html), f"{name}: stopLogPoll 从未被调用"
