# -*- coding: utf-8 -*-
"""看门狗：缓冲区的决策必须进**文件日志**，不能只进 UI 面板的内存环形缓冲。

由来（使用者 2026-09-27：「为什么破冰完没有后续的回复，看」）：

真机日志里 17:37:53 生成了一条 24 字回复，之后**什么都没有** —— 没有 `[Send]`、
没有 `未投递`、也没有任何"丢弃/合并/作废"的记录。全天统计：

```
生成 151 轮 ｜ [Send] 成功 30 条 ｜ 生成后 30 秒内没发出的 104 轮
```

其中一部分是设计上的合并（多发消息合成一次总结），但 17:37 那次不是：那条消息之后
**23 分钟没有新消息**，缓冲没有理由合并它。

而缓冲区的**全部 14 处**日志走的都是 `plugin._emit_log` —— 它写的是 UI 面板的
内存环形缓冲（重启即失，实测只剩 15 行）。也就是说：**"她明明生成了却没说话"
这条路上的每一个决策，文件日志里都查不到**，诊断只能停在"生成之后消失了"。

这个文件钉住三件事：

1. `_log()` **两路都写**（文件 + 面板），任何一路失败都不影响另一路；
2. 那几个会**静默丢弃**一条已生成回复的分支（等待被取消 / 归属检查未通过 /
   投递抛异常 / 投递未确认）必须留痕；
3. 缓冲区不许再出现"只进内存"的 `_emit_log` 决策日志。
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.reply_buffer_service import QQReplyBufferService

PLUGIN = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (PLUGIN / "reply_buffer_service.py").read_text(encoding="utf-8")


class _Recorder:
    def __init__(self) -> None:
        self.file_lines: list[tuple[str, str]] = []
        self.panel_lines: list[tuple[str, str]] = []

        class _Logger:
            def __init__(self, sink):
                self._sink = sink

            def _write(self, level, msg):
                self._sink.append((level, str(msg)))

            def debug(self, msg):
                self._write("DEBUG", msg)

            def info(self, msg):
                self._write("INFO", msg)

            def warning(self, msg):
                self._write("WARN", msg)

            def error(self, msg):
                self._write("ERROR", msg)

        self.logger = _Logger(self.file_lines)
        self._emit_log = lambda level, msg: self.panel_lines.append((level, str(msg)))


def _service(plugin=None) -> tuple[QQReplyBufferService, _Recorder]:
    rec = plugin if plugin is not None else _Recorder()
    return QQReplyBufferService(rec), rec


# ── 1. 两路都写 ────────────────────────────────────────────────────

def test_log_writes_to_both_sinks():
    svc, rec = _service()
    svc._log("INFO", "[Buffer] 排定投递（group:1，等待 1.0s）")

    assert rec.file_lines == [("INFO", "[Buffer] 排定投递（group:1，等待 1.0s）")], (
        "文件日志没收到 —— 这正是本次要修的那个盲区"
    )
    assert rec.panel_lines and rec.panel_lines[0][0] == "INFO", "面板那路不该被砍掉"


def test_levels_map_to_the_logger_methods():
    svc, rec = _service()
    for level in ("DEBUG", "INFO", "WARN", "ERROR"):
        svc._log(level, f"{level} 测试")
    assert [lvl for lvl, _ in rec.file_lines] == ["DEBUG", "INFO", "WARN", "ERROR"]


def test_prefix_is_added_when_missing():
    svc, rec = _service()
    svc._log("INFO", "没有前缀的一句话")
    assert rec.file_lines[0][1] == "[Buffer] 没有前缀的一句话"


def test_a_broken_logger_cannot_break_the_panel():
    """任何一路失败都不能影响另一路（日志本身绝不该影响投递）。"""
    rec = _Recorder()

    def _boom(msg):
        raise RuntimeError("文件日志坏了")

    rec.logger.info = _boom
    svc, _ = _service(rec)
    svc._log("INFO", "[Buffer] 测试")
    assert rec.panel_lines, "文件日志抛异常时面板那路也没写 —— 两路必须互相独立"


def test_a_missing_logger_is_tolerated():
    """轻量宿主/单测桩可能没有 logger 属性。"""
    panel: list[tuple[str, str]] = []
    bare = SimpleNamespace(_emit_log=lambda lvl, msg: panel.append((lvl, str(msg))))
    svc, _ = _service(bare)
    svc._log("INFO", "[Buffer] 无 logger 也要活着")
    assert panel, "没有 logger 时连面板那路都没写"


# ── 2. 静默丢弃点必须留痕 ──────────────────────────────────────────

def test_every_silent_drop_point_now_says_why():
    """四个分支各自对应的日志文案必须在源码里（删掉就红）。"""
    for marker in (
        "[Buffer] 排定投递",          # 等待开始（有它才排得出"等了多久")
        "[Buffer] 等待被取消",        # 静默 return #1（原来是空的）
        "[Buffer] 归属检查未通过",     # 静默 return #2（最隐蔽的那个）
        "[Buffer] 记忆授权已撤销，丢弃缓冲中的旧回复",
        "[Buffer] 单条投递失败",       # 原来只进内存
        "[Buffer] 投递未确认，草稿保持未投递",
        "[Buffer] 单条投递完成",
        "[Buffer] 缓冲 ",
    ):
        assert marker in SOURCE, f"这条留痕不见了: {marker}"


def test_buffer_decisions_no_longer_go_to_memory_only():
    """决策类日志不许再用 memory-only 的 `_emit_log`（两路都写请用 `_log`）。"""
    offenders = [
        line.strip()
        for line in SOURCE.splitlines()
        if "_emit_log(" in line and "[Buffer]" in line and "_log(" not in line
    ]
    assert not offenders, (
        "这些 [Buffer] 日志仍然只进内存环形缓冲（重启即失、文件里查不到）：\n"
        + "\n".join(offenders)
    )
