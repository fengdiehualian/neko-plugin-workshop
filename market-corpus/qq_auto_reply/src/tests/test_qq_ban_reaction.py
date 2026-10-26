# -*- coding: utf-8 -*-
"""禁言反应：**只对「正在和她对话的人」**反应，方式是合成一条系统消息走正常管线。

使用者口径（2026-09-29）：

> 可以让猫娘对正在聊天的对象的禁言做出反应吗

选定：对象 = 「正在和她对话的人」（一来一回，见 `dialogue_partner`）；
反应 = 合成系统提示，让模型自己说一句；解禁也反应；
自己或全员被禁言时静默（那一条在连接层就不入队，见 `test_qq_group_ban_notice.py`）；
节流 = 每群冷却 600 秒 + 同一事件只反应一次。

为什么"只对正在对话的人"值得一道闸：每次反应 = 一次 LLM 调用，而群里有人被管理处理
（刷屏、吵架）在活跃群里并不罕见。少了这道闸，她会对着陌生人被禁言这件事反复开口。
"""
from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import pipeline_models
from plugin.plugins.qq_auto_reply.message_dispatcher import QQMessageDispatcher

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
GROUP = "1048307485"
BOT = "3281414178"
ALICE = "1782348687"
BOB = "3658428358"
ADMIN = "10001"


class _Recorder:
    """最小插件桩：记录"有没有开一轮生成"、日志、以及合成消息本身。"""

    def __init__(self, *, level: str = "trusted", in_dialogue: bool = True) -> None:
        self.pipeline: list[tuple] = []
        self.backlog: list = []
        self.emitted: list[tuple[str, str]] = []
        self._user_sessions: dict = {}
        self._poke_timestamps: dict[str, list[float]] = {}
        self._poke_storm: dict[str, list] = {}
        self.enricher = None
        self.group_permission_mgr = SimpleNamespace(get_group_level=lambda gid: level)
        self.permission_mgr = SimpleNamespace(
            get_permission_level=lambda qq: level, get_nickname=lambda qq: "",
        )
        self.attention_gate_service = SimpleNamespace(
            is_in_dialogue_with=lambda gid, uid: in_dialogue,
        )
        self.backlog_service = SimpleNamespace(record_message=self._record_backlog)
        self.qq_client = SimpleNamespace(
            needs_attention=True, self_id=BOT, is_group_muted=lambda gid: False,
        )
        self._qq_settings = {"backlog_labels": []}
        self.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        self._emit_log = lambda level_, msg: self.emitted.append((level_, msg))

    _sanitize_message_text = staticmethod(lambda text, **kw: text)
    _build_session_key = staticmethod(lambda **kw: "session")

    async def _record_backlog(self, message):
        self.backlog.append(message)

    async def _maybe_notify_backlog_summary(self, *args, **kwargs):
        return None


def _dispatcher(**kw):
    plugin = _Recorder(**kw)
    dispatcher = QQMessageDispatcher(plugin)

    async def _pipeline(*args, **kwargs):
        plugin.pipeline.append((args, kwargs))

    dispatcher.handle_group_message = _pipeline
    return dispatcher, plugin


def _ban(
    dispatcher,
    *,
    user: str = ALICE,
    sub_type: str = "ban",
    duration: int = 600,
    group: str = GROUP,
    nickname: str = "",
):
    message = {
        "message_type": "notice", "notice_type": "group_ban", "sub_type": sub_type,
        "group_id": group, "user_id": user, "operator_id": ADMIN,
        "duration": duration, "timestamp": 1_790_000_000,
    }
    if nickname:
        message["sender"] = {"nickname": nickname}
    asyncio.run(dispatcher.handle_message(message))
    return message


def _logs(plugin) -> str:
    return " | ".join(msg for _lvl, msg in plugin.emitted)


# ── 一、正在跟她对话的人被禁言 → 合成一轮 ────────────────────────────

def test_a_ban_on_her_partner_opens_a_turn():
    dispatcher, plugin = _dispatcher()
    message = _ban(dispatcher, nickname="小张")

    assert len(plugin.pipeline) == 1, f"没有开一轮生成：{_logs(plugin)}"
    assert message["message_type"] == "group", "没有改写成一条群消息"
    assert message["user_id"] == ALICE, "合成消息的主语不是被禁言的那个人"
    assert message["is_at_bot"] is False, "别把它伪装成点名她"
    assert message["_synthetic_source"] == "group_ban_notice"
    assert message["message_id"].startswith("ban_")
    assert "小张" in message["content"] and "10 分" in message["content"], message["content"]
    assert "别评论管理员" in message["content"], "口径里明确要求不评价管理员"
    assert "不想说就不说" in message["content"], (
        "说不说又把模型排除在外了 —— 使用者口径是「派发层通过再让猫娘决定说不说」"
    )
    assert "小张" in _logs(plugin)


def test_a_lift_ban_also_opens_a_turn():
    dispatcher, plugin = _dispatcher()
    message = _ban(dispatcher, sub_type="lift_ban", duration=0, nickname="小张")

    assert len(plugin.pipeline) == 1
    assert "解除" in message["content"], message["content"]


def test_a_ban_without_a_duration_still_reads_naturally():
    dispatcher, plugin = _dispatcher()
    message = _ban(dispatcher, duration=0)

    assert len(plugin.pipeline) == 1
    assert "被管理员禁言了。" in message["content"], message["content"]


# ── 二、不是正在跟她对话的人 → 一个字都不说 ──────────────────────────

def test_a_ban_on_a_stranger_does_not_open_a_turn():
    dispatcher, plugin = _dispatcher(in_dialogue=False)
    _ban(dispatcher, user=BOB)

    assert plugin.pipeline == [], "陌生人被禁言也开口了"
    assert "不是正在和她对话的人" in _logs(plugin)


def test_only_trusted_groups_react():
    dispatcher, plugin = _dispatcher(level="normal")
    _ban(dispatcher)

    assert plugin.pipeline == [], "normal 群里她也开口了（那边是转达给主人的语义）"
    assert "不是 trusted" in _logs(plugin)


def test_a_notice_without_a_user_id_is_ignored():
    dispatcher, plugin = _dispatcher()
    asyncio.run(dispatcher.handle_message({
        "message_type": "notice", "notice_type": "group_ban", "sub_type": "ban",
        "group_id": GROUP, "user_id": "", "duration": 600,
    }))

    assert plugin.pipeline == []


# ── 三、节流：每群 600 秒 + 同一事件只反应一次 ───────────────────────

def test_the_same_event_only_reacts_once():
    dispatcher, plugin = _dispatcher()
    _ban(dispatcher)
    _ban(dispatcher)

    assert len(plugin.pipeline) == 1, f"同一件事反应了两次：{_logs(plugin)}"
    assert "同一事件刚反应过" in _logs(plugin)


def test_the_group_cooldown_blocks_a_second_person():
    """批量禁言（管理在清刷屏的人）：同一群里第二个人在冷却内不再开一轮。"""
    dispatcher, plugin = _dispatcher()
    _ban(dispatcher, user=ALICE)
    _ban(dispatcher, user=BOB)

    assert len(plugin.pipeline) == 1, f"同群冷却没生效：{_logs(plugin)}"
    assert "距上次禁言反应不到" in _logs(plugin)


def test_she_reacts_again_after_the_cooldown():
    dispatcher, plugin = _dispatcher()
    _ban(dispatcher, user=ALICE)
    assert len(plugin.pipeline) == 1

    # 把两条冷却记录都推到冷却之外（真机里这就是"十分钟过去了"）
    stale = 0.0
    dispatcher._ban_reactions = {k: stale for k in dispatcher._ban_reactions}
    dispatcher._ban_group_last = {k: stale for k in dispatcher._ban_group_last}

    _ban(dispatcher, user=BOB)
    assert len(plugin.pipeline) == 2, "冷却过去了还是不再反应"


def test_ban_and_lift_are_separate_events():
    """解禁与被禁言是两个事件：同一人在冷却内被禁言又被解禁 → 只反应第一个。"""
    dispatcher, plugin = _dispatcher()
    _ban(dispatcher, sub_type="ban")
    _ban(dispatcher, sub_type="lift_ban", duration=0)

    assert len(plugin.pipeline) == 1, "同一群冷却内不该既反应禁言又反应解禁"


# ── 四、"派发层通过 → 让猫娘决定说不说" ──────────────────────────────

def test_the_ban_reaction_bypasses_the_gate_but_is_not_forced():
    """禁言反应：绕过门控（脚本判完了），但**不强制**她开口 —— 说不说由模型决定。

    使用者口径（2026-09-29）：
      上一轮「先用『他不是正在和她对话的人 → 不反应』判断，再生成」
      → 本轮「派发层通过再让猫娘决定说不说」。
    也就是脚本只负责"这一轮该不该开"（trusted + 正在对话 + 冷却），开了之后
    由她决定说不说 —— 不替她决定，也不让注意力闸/必要性闸把同一件事再审一遍。
    """
    dispatcher, plugin = _dispatcher()
    message = _ban(dispatcher)

    source = str(message["_synthetic_source"])
    assert source == pipeline_models.KIND_GROUP_BAN_NOTICE
    assert dispatcher._is_gate_bypassed_synthetic(source) is True, "没过门控旁路"
    assert dispatcher._is_forced_synthetic(source) is False, (
        "替她决定了「必须开口」 —— 这一轮的说不说该由模型决定"
    )
    assert pipeline_models.is_synthetic_source(source) is True, (
        "没登记成合成轮 —— 它的 sender 是名义发言人（被禁言的人），"
        "读侧/写侧会把它当成他这一轮说的话"
    )
    assert pipeline_models.KIND_GROUP_BAN_NOTICE not in pipeline_models.BUFFER_INTERNAL_SOURCE_KINDS, (
        "与入群欢迎一样走正常投递，不该被投回缓冲"
    )


def test_the_welcome_turn_is_still_bypassed_and_forced():
    """原来写死的 `== "group_join_notice"` 拆成了两个集合，别把欢迎那条路弄丢。"""
    dispatcher, _plugin = _dispatcher()

    assert dispatcher._is_gate_bypassed_synthetic(pipeline_models.KIND_GROUP_JOIN_NOTICE) is True
    assert dispatcher._is_forced_synthetic(pipeline_models.KIND_GROUP_JOIN_NOTICE) is True
    for source in ("incoming_group", "", None):
        assert dispatcher._is_gate_bypassed_synthetic(source) is False
        assert dispatcher._is_forced_synthetic(source) is False


def test_handle_group_message_splits_bypass_from_force():
    """接线检查：门控旁路读一个集合、强制回复读另一个（别又合并成一处）。

    （不驱动整条 `handle_group_message`：它要跑回复管线、权限、backlog 与运行时记账，
    为这两行断言搭那么大一个桩不划算。这条扫描保证的是"两个判据都还接着集合"。）
    """
    text = (PLUGIN_DIR / "message_dispatcher.py").read_text(encoding="utf-8")

    assert "if self._is_gate_bypassed_synthetic(synthetic_source):" in text, (
        "handle_group_message 里的门控旁路没接到 GATE_BYPASS_SYNTHETIC_SOURCES"
    )
    assert "force_reply = self._is_forced_synthetic(synthetic_source)" in text, (
        "强制回复没接到 FORCED_SYNTHETIC_SOURCES"
    )
    assert 'synthetic_source == "group_join_notice"' not in text, (
        "又出现写死的来源判断了 —— 请用那两个集合"
    )


# ── 五、门控侧：她回他会被记进"正在和她对话的人" ─────────────────────

def test_on_reply_sent_records_who_she_answered():
    """「一来一回」的另外半条腿：她回了他。

    没有这一条，`dialogue_partner` 里永远只有"他找过她" —— 那个判据就退化成
    "谁 @ 过她"，于是任何叫过她一声的人被禁言都会触发她开口。
    """
    from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService

    class _Attention:
        def _current_time(self) -> int:
            return 1000

        async def update_on_reply(self, group_id: str) -> None:
            return None

    plugin = SimpleNamespace(
        attention_service=_Attention(),
        qq_client=SimpleNamespace(needs_attention=True, _sent_message_ids={}),
        permission_mgr=None,
        group_permission_mgr=None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _qq_settings={"backlog_labels": []},
        _emit_log=lambda *a, **k: None,
    )
    gate = QQAttentionGateService(plugin)
    assert gate.is_in_dialogue_with(GROUP, ALICE, now=1000) is False

    asyncio.run(gate.on_reply_sent(GROUP, user_id=ALICE))
    # 只有"她回他"还不够（缺"他找她"）
    assert gate.is_in_dialogue_with(GROUP, ALICE, now=1000) is False

    gate._dialogue.note_address(GROUP, ALICE, now=1000)
    assert gate.is_in_dialogue_with(GROUP, ALICE, now=1000) is True
