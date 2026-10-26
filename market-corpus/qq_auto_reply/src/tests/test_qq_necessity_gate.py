# -*- coding: utf-8 -*-
"""必要性判定的**集成**测试：真正走 `QQAttentionGateService.evaluate()`。

单测覆盖公式，这里覆盖接线与作用域：

- trusted 群：安静闲聊 / 短反应 → 不接；群里忙起来 → 接
- **normal 群不受这一关影响**（它们走 relay 转发，若在这里 ignore，转发也会被跳过）
- 阈值配成 0 → 一键关闭这一关
- 连续不接 → 空闲退避真的生效（第二次起会以 `necessity_backoff` 拦下）
- 退避可被积压绕过（BYPASS_PENDING 条）
- @ 是唯一旁路，永远不受必要性/退避影响
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService
from plugin.plugins.qq_auto_reply.reply_necessity import IdleBackoff

GROUP = "g1"


class _FakeAttention:
    def __init__(self, *, score: float = 5.0, now: int = 1000):
        self._score = score
        self._now = now
        self.calls: list[str] = []

    def _enabled(self) -> bool:
        return True

    def _current_time(self) -> int:
        return self._now

    def _score_for(self, group_id: str) -> float:
        return float(self._score)

    def get_state(self, group_id: str):
        return SimpleNamespace(attention_score=self._score_for(group_id))

    def is_in_conversation(self, group_id: str) -> bool:
        # 「本群在聊」= 本群分数过保持线。跨群取舍删除后，必要性里原来那个
        # `focus_active`（+40）读的就是这个判据。
        return self._score_for(group_id) >= self.conversation_threshold()

    def conversation_threshold(self) -> float:
        return 2.0

    def get_group_multiplier(self, group_id: str) -> float:
        return 1.0

    def _minimum_threshold(self) -> float:
        return 1.0

    def _focus_threshold(self) -> float:
        return 4.0

    async def update_on_message(self, message: dict) -> None:
        self.calls.append("update_on_message")

    def mark_focus(self, group_id: str) -> None:
        pass

    def lock_group(self, group_id: str) -> None:
        pass

    def wake_boost(self, group_id: str) -> None:
        pass

    def update_on_reply(self, group_id: str):
        async def _noop():
            return None

        return _noop()


def _plugin(*, level: str = "trusted", settings: dict | None = None, attention=None) -> SimpleNamespace:
    base = {"backlog_labels": [], "buffer_max_count": 17}
    base.update(settings or {})
    file_lines: list[str] = []
    return SimpleNamespace(
        attention_service=attention or _FakeAttention(),
        qq_client=SimpleNamespace(needs_attention=True, _sent_message_ids={}),
        permission_mgr=None,
        group_permission_mgr=SimpleNamespace(get_group_level=lambda gid: level),
        # 收集「文件日志」：门控决策必须走 logger.info，不能只进内存环
        _file_lines=file_lines,
        logger=SimpleNamespace(
            info=lambda msg, *a, **k: file_lines.append(str(msg)),
            warning=lambda *a, **k: None,
        ),
        _qq_settings=base,
        _emit_log=lambda *a, **k: None,
        _run_with_session_lock=None,
        reply_buffer_service=None,
        session_memory_service=None,
        reply_pipeline=None,
        runtime_service=None,
        _admin_qq="0",
        _build_session_key=lambda **k: "",
    )


def _evaluate(plugin, gate, **kwargs):
    payload = dict(group_id=GROUP, sender_id="u1", is_at_bot=False,
                   message_text="今天天气不错", quoted_message_id="", timestamp=1000)
    payload.update(kwargs)
    return asyncio.run(gate.evaluate(**payload))


def _gate(plugin) -> QQAttentionGateService:
    return QQAttentionGateService(plugin)


def test_trusted_group_stays_quiet_when_she_hogs_the_conversation():
    """她一个人刷屏（窗口里八成都是她说的）→ 普通闲聊让一让。这是 40 档的主力机制。

    口径变化（2026-09-29 使用者：「6 条就休息频率有点少了，发言惩罚减轻一点」）：
    这条**不再是"说几句就停"**。旧常数的同一个用例是"10 句她 + 4 句别人"就静音；
    减轻之后那个场景会接（见 `test_she_can_keep_talking_after_six_of_ten`），
    要她占到八成、且群里没有积压，才会被压到线下（实测 40 + 4 − 15 = 29 → ignore）。
    """
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    for _ in range(12):
        gate._speech.record_self(GROUP, now=980)
    for i in range(2):
        gate._speech.record(GROUP, now=990 + i, speaker=f"u{i}")

    decision = _evaluate(plugin, gate)

    assert decision.action == "ignore"
    assert decision.reason.startswith("necessity_wait(")


def test_she_can_keep_talking_after_six_of_ten():
    """她说了六成（6 句她 + 4 句别人，窗口 11 条含当前这条）→ 现在接得住。

    使用者口径（2026-09-29）：「6 条就休息频率有点少了，发言惩罚减轻一点」。
    同一条消息在两套常数下的实测：
        旧（免费区 0.25 / 满罚 25）：40 + 12 − 21 = 31 → ignore（这就是"6 条就休息"）
        新（免费区 0.45 / 满罚 15）：40 + 12 − 4  = 48 → reply
    """
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    for _ in range(6):
        gate._speech.record_self(GROUP, now=980)
    for i in range(4):
        gate._speech.record(GROUP, now=990 + i, speaker=f"u{i}")

    decision = _evaluate(plugin, gate, timestamp=1000)

    assert decision.action == "reply", decision.reason


def test_trusted_group_speaks_when_the_group_gets_busy():
    """积压到阈值（buffer_max_count-1 = 10 条）→ 压力分满，普通消息也值得接。"""
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    for i in range(10):
        gate._speech.record(GROUP, now=990 + i, speaker=f"u{i}")
    decision = _evaluate(plugin, gate)
    assert decision.action == "reply"
    assert decision.reason == "in_conversation"


def test_trusted_group_ignores_pure_short_reaction():
    plugin = _plugin(level="trusted")
    decision = _evaluate(plugin, _gate(plugin), message_text="哈哈哈")
    assert decision.action == "ignore"
    assert decision.reason.startswith("necessity_wait(")


def test_normal_group_is_not_touched_by_necessity_gate():
    """normal 群靠下游 relay 转发给主人；在这一关 ignore 会把转发一起吃掉。

    用短反应（默认 40 档下 trusted 必然 necessity_wait）来证明：这一关只对 trusted 生效。
    normal 群连注意力竞争都不参与，因此走的是更早的放行分支（2026-09-27 起）。
    """
    plugin = _plugin(level="normal")
    decision = _evaluate(plugin, _gate(plugin), message_text="哈哈哈")
    assert decision.action == "reply"
    assert decision.reason == "normal_group_passthrough"


def test_threshold_zero_disables_the_gate():
    plugin = _plugin(level="trusted", settings={"reply_necessity_threshold": 0})
    decision = _evaluate(plugin, _gate(plugin), message_text="哈哈哈")
    assert decision.action == "reply"


def test_at_bot_bypasses_necessity_and_backoff():
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    for _ in range(3):                      # 先攒够退避
        _evaluate(plugin, gate)
    decision = _evaluate(plugin, gate, is_at_bot=True, message_text="哈哈")
    assert decision.action == "reply"
    assert decision.force_reply is True


def test_repeated_waits_arm_the_backoff():
    """连续不接 → 第二次起进入退避，后续消息以 necessity_backoff 被拦下。

    用短反应当「必然不接」的消息（默认 40 档下普通闲聊是会接的）。
    """
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    first = _evaluate(plugin, gate, timestamp=1000, message_text="哈哈哈")
    second = _evaluate(plugin, gate, timestamp=1001, message_text="哈哈哈")
    assert first.reason.startswith("necessity_wait(")
    assert second.reason.startswith("necessity_wait(")       # 第 2 次才设定退避
    third = _evaluate(plugin, gate, timestamp=1002, message_text="哈哈哈")
    assert third.reason.startswith("necessity_backoff(")

    # 时钟推进到退避之外 → 重新按分数判定
    later = _evaluate(plugin, gate, timestamp=1001 + int(IdleBackoff.BASE_SECONDS) + 1, message_text="哈哈哈")
    assert later.reason.startswith("necessity_wait(")


def test_backoff_is_bypassed_when_messages_pile_up():
    """积压到 BYPASS_PENDING 条 → 绕过退避重新评估（热闹起来不能错过）。"""
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    _evaluate(plugin, gate, timestamp=1000)
    _evaluate(plugin, gate, timestamp=1001)                  # 退避已武装
    for i in range(IdleBackoff.BYPASS_PENDING):
        gate._speech.record(GROUP, now=1001 + i, speaker=f"u{i}")
    decision = _evaluate(plugin, gate, timestamp=1002)
    assert not decision.reason.startswith("necessity_backoff(")


def test_her_own_replies_count_towards_presence():
    """她说过话之后，存在感占比升高 → 同样内容更难通过（切回不接）。

    减轻之后（2026-09-29）这条的**量级变了**：对半说（窗口里一半是她）不再有影响
    —— 她连说 10 句时同一个群、同一条消息仍然接（实测 40 + 53 − 1 = 92）。
    要连说 20 句、把窗口占掉三分之二，才扣 8 分把普通闲聊压到 40 线下（40 + 0 − 8 = 32）。
    """
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    for i in range(10):
        gate._speech.record(GROUP, now=990 + i, speaker=f"u{i}")
    assert _evaluate(plugin, gate).action == "reply"

    for i in range(10):
        gate._speech.record_self(GROUP, now=995 + i)
    assert _evaluate(plugin, gate, timestamp=1005).action == "reply", "对半说也被压下去了"

    # 再连说 10 句（共 20 句）→ 窗口占比 0.65、她之后没有新积压 → 压到线下
    for i in range(10):
        gate._speech.record_self(GROUP, now=1006 + i)
    decision = _evaluate(plugin, gate, timestamp=1015)
    assert decision.action == "ignore"
    assert decision.reason.startswith("necessity_wait(")


def test_decision_goes_to_the_file_logger_not_only_the_ring():
    """决策必须同时写**文件日志**。

    历史上这些行只走 `_emit_log`（内存环，maxlen 500），插件一重载就没了 ——
    于是「她为什么这一轮没接」事后无法回查，live 验证也没有凭据。
    """
    plugin = _plugin(level="trusted")
    decision = _evaluate(plugin, _gate(plugin), message_text="哈哈哈")

    assert decision.action == "ignore"
    lines = [ln for ln in plugin._file_lines if "[Necessity]" in ln]
    assert lines, f"没有写进文件日志：{plugin._file_lines}"
    assert "本轮不接" in lines[-1]
    assert "score=" in lines[-1]


def test_backoff_line_goes_to_the_file_logger():
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    _evaluate(plugin, gate, timestamp=1000, message_text="哈哈哈")
    _evaluate(plugin, gate, timestamp=1001, message_text="哈哈哈")
    decision = _evaluate(plugin, gate, timestamp=1002, message_text="哈哈哈")

    assert decision.reason.startswith("necessity_backoff(")
    assert any("空闲退避中" in ln for ln in plugin._file_lines), plugin._file_lines


def test_pass_line_goes_to_the_file_logger():
    """通过也要留痕：否则「她接了」与「她没被这一关拦」分不清。"""
    plugin = _plugin(level="trusted")
    gate = _gate(plugin)
    for i in range(10):
        gate._speech.record(GROUP, now=990 + i, speaker=f"u{i}")
    decision = _evaluate(plugin, gate)

    assert decision.action == "reply"
    assert any("接（score=" in ln for ln in plugin._file_lines), plugin._file_lines
