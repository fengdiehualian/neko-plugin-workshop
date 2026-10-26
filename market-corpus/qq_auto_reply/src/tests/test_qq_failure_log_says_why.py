# -*- coding: utf-8 -*-
"""看门狗：**失败日志必须说得出失败的类型**。

真机现场（2026-09-27，检查插件时发现的）：

```
ERROR - [idle_timeout] 群 985066274 scoped 结算失败:            ← 冒号后面什么都没有
ERROR - [idle_timeout] 群 1048307485 一批 1 个成员记忆结算失败:
```

而且它在 `_error.log` 里也**没有 traceback**（是就地 catch 后 logger.error 打的），
所以「查不到原因」不是错觉：那几条日志等于记了个寂寞。

根因：`asyncio.TimeoutError` / `TimeoutError` / `CancelledError` 的 `str()` **就是空串**，
`f"{exc}"` 于是渲染成空。这些失败恰好集中在插件重载窗口（14:46 / 15:27），
是重载把在途的记忆结算请求拖超时了 —— 功能上没事（游标保留、18:13 自动补结算成功），
但日志里既看不出是超时、也看不出是取消。

同一个坑在 `napcat_service` 那边表现为另一种形态：`{e}` 打出一整坨
`Task <Task pending …> got Future … attached to a different loop`（重载后旧事件循环的
subprocess 句柄），一天 6 条、每条约 300 字符。

规矩：**catch 后写日志时，异常一律带上 `type(...).__name__`**（本仓库其它十几处
早就是这个写法，例如 `repeat_echo_service` / `plugin_tool_followup_service`）。
这个文件钉住本次修掉的那 4 处，防止改回去。

局限（说清楚）：这是**源码级**断言，不是行为级 —— 那 4 处分别埋在
会话结算的大方法与停机收尸路径里，为了"能看见日志"去搭一整套 harness 不划算。
它挡不住"换了别的写法"，但能挡住"把 `type(...)` 删掉"。
"""

from __future__ import annotations

import pathlib
import re

PLUGIN = pathlib.Path(__file__).resolve().parents[1]
MEMORY = (PLUGIN / "session_memory_service.py").read_text(encoding="utf-8")
NAPCAT = (PLUGIN / "napcat_service.py").read_text(encoding="utf-8")

#: 修掉的四处 → 各自**唯一**的源码标记（同名标记在文件里出现两次就区分不开）
SITES = (
    (MEMORY, "scoped 结算失败（群）", "群 {group_id} scoped 结算失败: "),
    (MEMORY, "scoped 结算失败（私聊）", "私聊 {sender_id} scoped 结算失败: "),
    (MEMORY, "一批成员记忆结算失败", "个成员记忆结算失败: "),
    (NAPCAT, "等待 NapCat 进程退出失败", "等待 NapCat 进程退出失败"),
)


def _window(text: str, marker: str, *, before: int = 140, after: int = 260) -> str:
    """取标记周围的源码窗口。

    日志正文常被拆成**相邻的两条 f-string**（一条写"失败: "、下一条插异常），
    所以不能只看标记所在的那一行 —— 取一个窗口，前后都算进来。
    """
    idx = text.index(marker)
    return text[max(0, idx - before): idx + after]


def test_settlement_failures_name_the_exception_type():
    """三处记忆结算失败日志都要写异常类型（`type(x).__name__`）。"""
    missing: list[str] = []
    for text, label, marker in SITES[:3]:
        chunk = _window(text, marker)
        if "type(" not in chunk or "__name__" not in chunk:
            missing.append(f"{label}: {chunk.strip()[-90:]}")
    assert not missing, (
        "这些失败日志又只插了 `{exc}` —— TimeoutError 的 str() 是空串，日志会变成"
        "「失败: 」这种查不出原因的东西：\n" + "\n".join(missing)
    )


def test_napcat_wait_warning_names_the_type_and_truncates():
    """停机收尸那条要带类型、并且**截断正文**（否则每次刷 300 字符的 Task repr）。"""
    text = NAPCAT
    idx = text.index("等待 NapCat 进程退出失败")
    chunk = text[idx:idx + 400]
    assert "__name__" in chunk, "这条警告又没带异常类型"
    assert re.search(r"str\(e\)\[\s*:\s*\d+\s*\]", chunk), (
        "这条警告没有截断异常正文 —— 它会打出整坨 Task repr 把日志刷爆"
    )


def test_the_empty_str_failure_modes_are_actually_empty():
    """把根因钉成事实：这几种异常的 `f"{exc}"` 确实是空串（谁改小字注释都得先看这条）。"""
    import asyncio

    empties = [TimeoutError(), asyncio.TimeoutError(), asyncio.CancelledError()]
    assert all(f"{e}" == "" for e in empties), (
        "这些异常的 str() 不再是空串了 —— 本文件的注释与那几处日志的取舍需要重新看"
    )
