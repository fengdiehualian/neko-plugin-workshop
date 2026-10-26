# -*- coding: utf-8 -*-
"""当前时间上下文（提示词时间层）。

从 ``fatigue_service.py`` 里救出来的：那段代码与疲劳无关，只是**寄居**在那个
模块里（``get_dynamic_time_context()``），而疲劳系统已经删除。它必须留下——
提示词里的「当前时间 / 星期 / 时段」以及「结合当前时间理解'刚刚''昨天''下周'」
这条指令，是记忆召回与时间表达理解的前提。

输出格式与迁移前**逐字节一致**（有测试钉住），因此删疲劳不会改变提示词。
"""

from __future__ import annotations


def build_time_context() -> str:
    """生成当前时间上下文（供 LLM 系统提示注入）。"""
    import datetime

    now = datetime.datetime.now()
    hour = now.hour + now.minute / 60.0

    ctx = f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[now.weekday()]}。\n"

    if hour < 6:
        ctx += "现在是深夜凌晨。\n"
    elif hour < 9:
        ctx += "现在是早晨。\n"
    elif hour < 12:
        ctx += "现在是上午。\n"
    elif hour < 14:
        ctx += "现在是中午/午后。\n"
    elif hour < 18:
        ctx += "现在是下午。\n"
    elif hour < 22:
        ctx += "现在是傍晚/晚间。\n"
    else:
        ctx += "现在是深夜。\n"

    ctx += '注意结合当前时间理解对话中的时间表达（如"刚刚""昨天""下周"等）。\n'
    return ctx
