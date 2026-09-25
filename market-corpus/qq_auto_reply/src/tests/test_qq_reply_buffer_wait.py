"""发送延迟：脚本按正态分布取样，不再由 LLM 用 ``<wait>`` 标签指定。

原先模型要在正文里额外产出一个秒数（还要求它每次自己变随机），既占生成预算又不稳。
改成脚本取样后，这里钉住：取值被夹在合理区间、私聊基数确实比群聊大，以及**标签机制
已整体移除**（提示词、解析、剥离一处不留）。
"""
from __future__ import annotations

import statistics

from plugin.plugins.qq_auto_reply import prompt_fragment_templates as templates
from plugin.plugins.qq_auto_reply.reply_buffer_service import QQReplyBufferService as S

SAMPLES = 2000


# ── 取样区间与中心 ──────────────────────────────────────────

def test_samples_stay_inside_the_clamp():
    """正态分布的尾巴理论无界 —— 必须夹住，否则偶尔甩出十几秒的静默。"""
    for private in (False, True):
        vals = [S.sample_wait_seconds(private=private) for _ in range(SAMPLES)]
        assert min(vals) >= S.MIN_WAIT_SECONDS
        assert max(vals) <= S.MAX_WAIT_SECONDS


def test_group_waits_longer_than_private():
    """**群聊等得更久，私聊等得短。**

    一屋子人在聊，晚一点插话更自然；私聊是两个人面对面，对方正等着你回。
    （此前把这两个基数接反了 —— 起因是把一条**死常量**的注释当成了意图，
    而那个常量从没被任何代码路径用过，注释也就从没被验证过。）
    """
    group = statistics.fmean(S.sample_wait_seconds(private=False) for _ in range(SAMPLES))
    private = statistics.fmean(S.sample_wait_seconds(private=True) for _ in range(SAMPLES))

    assert group > private + 1.0, (group, private)


def test_values_actually_vary():
    """退化成常数就说明 gauss 没接上（或 sigma 被写成 0）。"""
    vals = {S.sample_wait_seconds(private=False) for _ in range(50)}
    assert len(vals) > 10
    assert statistics.pstdev([S.sample_wait_seconds() for _ in range(SAMPLES)]) > 0.5


# ── 标签机制已整体移除 ──────────────────────────────────────


# ── 提示词里不再要求模型产出 <wait> ─────────────────────────

def test_no_prompt_template_asks_for_wait_tags():
    """延迟已由脚本决定 —— 提示词里再出现 <wait> 指令就是退回了 LLM 决定的老路。"""
    offenders = [name for name, value in vars(templates).items()
                 if isinstance(value, str) and "<wait>" in value]
    assert offenders == []


# ── 老的解析路径已经拆掉 ────────────────────────────────────

def test_no_wait_tag_machinery_remains():
    """解析与剥离都已删除 —— 提示词不再产出它，插件也不再处理它。

    这是**看门狗**：谁把"可选覆盖"或"防御性剥离"加回来，它就红。
    """
    assert not hasattr(S, "extract_wait_seconds")
    assert not hasattr(S, "strip_wait_tags")
