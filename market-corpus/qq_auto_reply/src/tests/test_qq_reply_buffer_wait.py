"""发送延迟：由脚本给出，不再由 LLM 用 ``<wait>`` 标签指定。

原先模型要在正文里额外产出一个秒数（还要求它每次自己变随机），既占生成预算又不稳。
改成脚本决定后，这里钉住：群聊的停顿确实比私聊长，以及**标签机制已整体移除**
（提示词、解析、剥离一处不留）。

**不再钉"取值有随机性"** —— 那是有意去掉的：改成下限语义（见 reply_buffer_service._send_at）
之后总时长归收集窗口管，这个停顿只剩「别秒回」这一个作用，正态取样与区间夹取都没有
意义，四个旋钮并成了一个。
"""
from __future__ import annotations

from plugin.plugins.qq_auto_reply import prompt_fragment_templates as templates
from plugin.plugins.qq_auto_reply.reply_buffer_service import QQReplyBufferService as S


def test_group_pauses_longer_than_private():
    """**群聊停得更久，私聊停得短。**

    一屋子人在聊，晚一点插话更自然；私聊是两个人面对面，对方正等着你回。
    （此前把这两个基数接反了 —— 起因是把一条**死常量**的注释当成了意图，
    而那个常量从没被任何代码路径用过，注释也就从没被验证过。）
    """
    group = S.send_pause_seconds(private=False)
    private = S.send_pause_seconds(private=True)

    assert group > private + 1.0, (group, private)


def test_pause_is_one_configured_value_not_a_sample():
    """**不再取样**：同一个配置读多少次都是同一个值。

    谁把 gauss 加回来这条就红 —— 那意味着 sigma / 下限 / 上限三个旋钮又回来了，
    而它们在下限语义下没有意义。
    """
    settings = {"buffer_delay_mean_seconds": 2.5}

    vals = {S.send_pause_seconds(settings=settings) for _ in range(20)}

    assert vals == {2.5}


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
