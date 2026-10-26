# -*- coding: utf-8 -*-
"""看门狗：「回复过于频繁 → 强制静默」这道**硬闸不许回来**。

使用者口径（2026-09-27，看到日志里那行 `[Gate] 群985066274 回复过于频繁，强制静默` 之后）：

> 「不要这个，有注意力控制频率了」

删它的三条理由（详见 `attention_gate_service` 里那段墓碑注释）：

1. **它不看上下文**：真机 19:16 那次，她在 985066274 连发 3 条之后使用者紧接着回了
   一句 —— 被这道闸静默。与 17:37 那次「破冰完没有后续」是同一类毛病：她刚开口、
   这是第一条回应，却被"你太频繁了"挡住。@ / 引用 / 关键词能绕，普通回复不能。
2. **频率本来就有两处在管**：注意力（焦点竞争 + 分数消耗 + 频率增速缩放）决定她把时间
   花在哪个群，`pacing_hint` 在她说得偏密时提醒她自己收敛 —— 两者都是"坡"，
   这道硬闸是唯一的"断崖"，也是唯一一个**不看内容只看计数**的出口。
3. 一天 17 次命中里 16 次在热闹群（那边确实刷），但剩下那一次正好落在一次正常的一来
   一往上 —— 代价与收益不成比例。

这个文件钉住两件事：**闸的代码不许回来**；**没有闸之后，她仍然会被告知"说得有点密"**
（频率不是没人管了，只是从断崖换成坡）。
"""

from __future__ import annotations

import io
import pathlib
import tokenize

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService

PLUGIN = pathlib.Path(__file__).resolve().parents[1]
GATE_SOURCE = (PLUGIN / "attention_gate_service.py").read_text(encoding="utf-8")


def _code_only(source: str) -> str:
    """剥掉注释后的源码。

    必须剥：本次**删除机制的墓碑注释**里就会写 `reply_burst_limit` /
    `_check_reply_burst` / `_reply_timestamps`（"这些东西删了、为什么删"），
    直接扫原文会把墓碑当成闸还在。
    """
    parts: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            continue
        parts.append(tok.string)
    return " ".join(parts)


CODE = _code_only(GATE_SOURCE)


# ── 1. 闸的代码不许回来 ────────────────────────────────────────────

def test_the_burst_decision_is_gone():
    assert "reply_burst_limit" not in CODE, (
        "reply_burst_limit 又回到门控里了 —— 使用者 2026-09-27 明确不要这道闸"
    )
    assert "_check_reply_burst" not in CODE, "硬闸的判定函数又回来了"


def test_the_burst_counter_is_gone():
    """它自己的计数器（与 attention 的频率环是两份）也一起删掉了。"""
    assert "_reply_timestamps" not in CODE, "硬闸的计数器还在（死代码）"
    assert "_record_reply" not in CODE, "硬闸的记账函数还在（死代码）"


def test_the_gate_service_no_longer_holds_burst_state():
    gate = QQAttentionGateService.__new__(QQAttentionGateService)
    assert not hasattr(gate, "_reply_timestamps")


def test_the_removal_is_documented_where_it_used_to_live():
    """删机制要留墓碑：为什么删、谁要求的、现在频率归谁管。"""
    assert "这道硬闸**已删除**" in GATE_SOURCE
    assert "不要这个，有注意力控制频率了" in GATE_SOURCE
    assert "pacing_hint" in GATE_SOURCE


# ── 2. 频率仍有人管（换成坡） ──────────────────────────────────────

def test_pacing_hint_still_exists_and_is_the_single_frequency_mechanism():
    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

    assert hasattr(QQAttentionService, "pacing_hint"), "软提示也没了 —— 频率就没人管了"
    assert hasattr(QQAttentionService, "recent_reply_count")


def test_the_burst_config_keys_survive_as_pacing_references():
    """两个 `reply_burst_*` 键留着（软提示的窗口与参考条数），但不再触发静默。"""
    from plugin.plugins.qq_auto_reply import settings_schema

    for key in ("reply_burst_window_seconds", "reply_burst_max_replies"):
        spec = settings_schema.BY_KEY[key]
        assert spec.saveable is True, f"{key} 不该被删 —— 软提示还要用它"
        assert "静默" not in (spec.description or ""), (
            f"{key} 的说明还在承诺「强制静默」：{spec.description!r}"
        )


def test_the_ui_copy_no_longer_promises_silencing():
    """界面文案也不许再承诺强制静默（用户会照着它理解行为）。"""
    import json

    for name in ("zh-CN.json", "en.json"):
        bundle = json.loads((PLUGIN / "i18n" / name).read_text(encoding="utf-8"))
        for key in ("ui.pacing.burst_max.hint", "ui.pacing.burst_window.hint"):
            text = bundle.get(key, "")
            assert "静默" not in text and "silenced" not in text.lower(), (
                f"{name} 的 {key} 还在说会被静默: {text!r}"
            )
