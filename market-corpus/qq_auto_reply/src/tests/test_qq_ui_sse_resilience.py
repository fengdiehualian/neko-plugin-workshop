# -*- coding: utf-8 -*-
"""看门狗：页面「刷新」不许因为 SSE 死连接而变慢。

**为什么钉**（2026-09-27 使用者问「napcat 页的刷新有延迟，为什么」）：

真 Chromium 实测（`.dsh-artifacts/measure-refresh-functions.py`）：

| 场景 | 每次刷新 |
|---|---|
| SSE 正常 | 5~12 ms |
| 把 EventSource `close()` 掉（死连接） | **2010~2022 ms**（修复前） |
| 只走兜底轮询（完全没有 SSE） | **2000 ms 整**（修复前） |

两个成因，都已修：

1. `ensureEs()` 原本是 `if (es) return es` —— EventSource 掉到 **CLOSED(2) 是终态**
   （浏览器只对 CONNECTING(0) 自动重连），死单例被**永远**返回下去，于是这个页面余生
   每次 `call()` 都吃满兜底轮询、状态/日志推送也再收不到（只剩 30s 兜底）；
   现在遇到 CLOSED 会丢掉重建，`onerror` 里再补一个 3s 后的重建兜底。
2. 兜底轮询原本是**固定 2000ms**：SSE 一旦不在工作，第一次刷新就白等一整个周期；
   现在是「100ms 起步、每次 ×1.6、上限 2s」的退避 —— 实测无 SSE 时第一次刷新 109ms。
"""

from __future__ import annotations

import pathlib
import re

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
STATIC = PLUGIN_DIR / "static"
SSE = "ui-sse.js"

#: 载入 ui-sse.js 的页面 —— 版本号必须一致（改了这个文件却不提版本号，浏览器会一直用旧的）。
PAGES_USING_SSE = ("napcat.html", "open_platform.html", "status.html", "old.html")

#: 当前资源版本号。
SSE_VERSION = "3"


def _text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_dead_event_source_is_dropped_and_rebuilt():
    text = _text(SSE)
    # 认**那一行**：只写 "readyState === 2" 太松 —— onerror 里也有一处同样的判断，
    # 删掉 ensureEs 里这行丢连接的代码它照样能通过（第一版变异就是这么漏掉的）。
    assert "if (es && es.readyState === 2) { es = null; }" in text, (
        "ensureEs() 不再丢掉 CLOSED(2) 的死连接 —— 它会被一直返回，"
        "页面余生每次刷新都吃满兜底轮询（实测 2s）"
    )
    assert "function scheduleReopen" in text, "丢了 3s 后的重建兜底（没人在等时也能自愈）"
    assert "es.onerror" in text, "没有 onerror —— 掉到 CLOSED 时没人触发重建"


def test_fallback_poll_starts_fast_then_backs_off():
    text = _text(SSE)
    assert "opts.pollInterval || 100" in text, "兜底轮询的起步间隔不是 100ms（回到固定慢轮询了？）"
    assert "maxPollInterval" in text and "maxPollDelay" in text, "没有退避上限"
    assert "Math.round(pollDelay * 1.6)" in text, "退避因子不见了"
    assert "opts.pollInterval || 2000" not in text, "兜底轮询又变回固定 2000ms 起步"


def test_state_probe_exists_for_diagnosis():
    """排查用：UISSE.state() 要能看到连接状态与重建次数（0=连接中 1=已连 2=已关）。"""
    text = _text(SSE)
    assert "state: function ()" in text, "UISSE.state() 不见了 —— 下次排查只能靠猜"
    assert "reopenCount" in text, "state() 里没有重建次数"


def test_all_pages_use_the_same_ui_sse_version():
    seen: dict[str, set[str]] = {}
    for name in PAGES_USING_SSE:
        for version in re.findall(r"ui-sse\.js\?v=(\d+)", _text(name)):
            seen.setdefault(name, set()).add(version)
        assert seen.get(name), f"{name} 没有加载 ui-sse.js?v=<版本号>"
        assert seen[name] == {SSE_VERSION}, (
            f"{name} 上的 ui-sse.js 版本号是 {sorted(seen[name])}，期望 {SSE_VERSION} —— "
            "改了 ui-sse.js 必须把每一处引用一起提版本，否则浏览器一直用缓存里的旧文件"
        )
