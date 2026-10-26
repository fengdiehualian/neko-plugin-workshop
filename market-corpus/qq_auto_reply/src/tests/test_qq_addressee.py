# -*- coding: utf-8 -*-
"""「谁在跟谁说话」（addressee）：判据、减分、硬门控、提示词文案。

这个机制此前只以 6 个互斥字符串的形式活在提示词里，**不进分数、不进门控**：
一条明确「@ 了别人」的消息，它的必要性相关分与「谁都没提她」一样（都是
`PLAIN_SCORE = 0`），只能靠模型自己读那句"不要自作多情"来收敛。

原料其实早就在线上了 —— 连接器的 `_extract_interaction_context` 一直在算
`quoted_sender_id`（被引用的是谁）与 `mentioned_user_ids`，插件侧一个都没读；
`_record_human_pair` 的 docstring 甚至写着"拿被引用者的 uid 得额外 get_msg"，
**这个前提是错的**。本文件钉住四件事：

1. **判据阶梯**（`addressing.resolve_addressee`）：@她 > 引用她 > @全体 > 引用别人 >
   @别人 > 叫她的名字 > 她刚说完的第一条 > 群内闲聊；含段序（"首段 @"）。
2. **减分真的生效**（`score_necessity` + `addressee_penalty`）：明确指向别人的消息
   会从 trigger 翻成 wait —— 这是"强减分"的行为证据，不是"改了个字段"。
3. **硬门控是可选的**（`addressee_ignore_first_at_other`）：默认关，打开才拦；
   且只作用于**参与注意力竞争**的群（normal 群的 relay 语义不动）。
4. **文案有结论时更具体、没结论时逐字不变**：老标签一行都没删，开放平台通道与
   合成轮走的仍是它们（本轮**只管 NapCat 通道**，是使用者的决定）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import addressing
from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService
from plugin.plugins.qq_auto_reply.pipeline_models import QQReplyRequest
from plugin.plugins.qq_auto_reply.prompt_builder import QQPromptBuilder
from plugin.plugins.qq_auto_reply.prompting import QQAutoReplyPromptingMixin
from plugin.plugins.qq_auto_reply.reply_necessity import (
    NecessitySignals,
    score_necessity,
)

GROUP = "985066274"
HER = "10000"
OTHER = "20000"
OTHER2 = "30000"


def _seg(*types_and_qq: tuple[str, str]) -> list[dict]:
    return [{"type": kind, "data": {"qq": qq}} for kind, qq in types_and_qq]


# ==========================================================================
# 一、判据阶梯（纯函数）
# ==========================================================================

def test_at_bot_wins_over_mentioning_someone_else():
    """@ 了她（哪怕同时 @ 了别人）→ 冲她来的。"""
    v = addressing.resolve_addressee(
        self_id=HER, is_at_bot=True, mentioned_user_ids=[OTHER],
    )
    assert v.kind == addressing.KIND_AT_BOT
    assert v.points_at_bot and not v.points_elsewhere


def test_mentions_bot_in_the_id_list_counts_as_at_bot():
    """连接器的 `mentioned_user_ids` 里有她 = 被 @ 了（不依赖 is_at_bot 这个布尔量）。"""
    v = addressing.resolve_addressee(self_id=HER, mentioned_user_ids=[HER, OTHER])
    assert v.kind == addressing.KIND_AT_BOT


def test_reply_to_bot_comes_before_everything_else():
    v = addressing.resolve_addressee(
        self_id=HER, is_reply_to_bot=True, quoted_message_id="m1", quoted_sender_id=HER,
    )
    assert v.kind == addressing.KIND_REPLY_TO_BOT


def test_at_all_is_its_own_kind_and_not_pointed_elsewhere():
    """@全体是广播 —— 人人都被点到，不能算"在跟别人说话"。"""
    v = addressing.resolve_addressee(self_id=HER, mentions_all=True)
    assert v.kind == addressing.KIND_AT_ALL
    assert not v.points_elsewhere


def test_quoting_someone_else_carries_their_uid():
    """被引用者是谁，现在拿得到（连接器给的 `quoted_sender_id`）—— 这就是"谁在跟谁说话"。"""
    v = addressing.resolve_addressee(
        self_id=HER, quoted_message_id="m9", quoted_sender_id=OTHER,
    )
    assert v.kind == addressing.KIND_REPLY_TO_OTHER
    assert v.target_id == OTHER
    assert v.points_elsewhere
    assert OTHER in v.evidence


def test_quoting_someone_else_without_uid_still_points_elsewhere():
    """老形态只给 message_id、没有 uid：结论不变，只是带不出对象。"""
    v = addressing.resolve_addressee(self_id=HER, quoted_message_id="m9")
    assert v.kind == addressing.KIND_REPLY_TO_OTHER
    assert v.target_id == ""
    assert v.points_elsewhere


def test_mentioning_someone_else_points_elsewhere():
    v = addressing.resolve_addressee(self_id=HER, mentioned_user_ids=[OTHER, OTHER2])
    assert v.kind == addressing.KIND_AT_OTHER
    assert v.target_id == OTHER
    assert not v.points_at_bot


def test_named_bot_needs_two_characters():
    """单字名在群里必然误命中 —— 宁可漏判（漏判只退回 LLM 自判）。"""
    assert addressing.resolve_addressee(self_id=HER, text="猫在吗", names=["猫"]).kind \
        == addressing.KIND_GROUP_CHATTER
    v = addressing.resolve_addressee(self_id=HER, text="猫娘在吗", names=["猫娘"])
    assert v.kind == addressing.KIND_NAMED_BOT
    assert v.matched_name == "猫娘"


def test_named_bot_takes_the_longest_name():
    v = addressing.resolve_addressee(
        self_id=HER, text="小猫咪娘早上好", names=["猫咪", "小猫咪娘"],
    )
    assert v.matched_name == "小猫咪娘"


def test_named_bot_is_beaten_by_an_explicit_mention():
    """显式指向优先于名字命中：@ 了别人时不该因为文中带她的名字就翻成"在叫她"。"""
    v = addressing.resolve_addressee(
        self_id=HER, text="猫娘你看小明说的", mentioned_user_ids=[OTHER], names=["猫娘"],
    )
    assert v.kind == addressing.KIND_AT_OTHER


def test_after_own_speech_is_the_last_ladder_step():
    v = addressing.resolve_addressee(self_id=HER, first_reply_after_own_speech=True)
    assert v.kind == addressing.KIND_AFTER_OWN_SPEECH
    assert not v.points_elsewhere


def test_plain_chatter_is_the_fallback():
    assert addressing.resolve_addressee(self_id=HER, text="今天天气不错").kind \
        == addressing.KIND_GROUP_CHATTER


# ── 段序：首段 @ 的是谁 ──────────────────────────────────────────────

def test_first_segment_at_is_read_from_the_segment_array():
    assert addressing.first_at_target(_seg(("at", OTHER), ("text", ""))) == OTHER
    # 首段不是 @ 就为空 —— 判据本身就是"第一句就在跟别人说话"。
    assert addressing.first_at_target(_seg(("text", ""), ("at", OTHER))) == ""
    assert addressing.first_at_target([]) == ""
    assert addressing.first_at_target(None) == ""


def test_first_segment_at_supports_cq_strings():
    assert addressing.first_at_target("[CQ:at,qq=20000] 在吗") == OTHER
    assert addressing.first_at_target("你好 [CQ:at,qq=20000]") == ""


def test_first_at_pointing_at_her_is_not_other():
    """首段 @ 的是她自己 → 不是"@ 别人"（那种消息在第 1 档就返回 at_bot 了）。"""
    v = addressing.resolve_addressee(
        self_id=HER, is_at_bot=True, segments=_seg(("at", HER), ("text", "")),
    )
    assert v.first_at == ""
    assert v.first_at_other is False


def test_first_at_all_is_not_other():
    v = addressing.resolve_addressee(
        self_id=HER, mentions_all=True, segments=_seg(("at", "all")),
    )
    assert v.first_at == "all"
    assert v.first_at_other is False, "@全体是广播，不算在跟别人说话"


def test_first_at_other_flag():
    v = addressing.resolve_addressee(
        self_id=HER, mentioned_user_ids=[OTHER], segments=_seg(("at", OTHER), ("text", "")),
    )
    assert v.first_at_other is True


# ── 名字清单 ────────────────────────────────────────────────────────

def test_configured_names_merges_host_names_and_aliases():
    names = addressing.configured_names(
        {"addressee_names": ["猫娘", "喵喵"]}, host_names=["兰兰"],
    )
    assert names == ("兰兰", "猫娘", "喵喵")


def test_configured_names_accepts_a_comma_string_and_dedupes():
    """手改配置文件写成逗号串也要能用（与该文件其它 list 键的容错口径一致）。"""
    assert addressing.configured_names(
        {"addressee_names": "猫娘, 喵喵 ,猫娘"}, host_names=["猫娘"],
    ) == ("猫娘", "喵喵")


def test_configured_names_survives_garbage():
    assert addressing.configured_names({"addressee_names": None}, host_names=()) == ()
    assert addressing.configured_names(None) == ()


def test_host_names_returns_strings_or_nothing(monkeypatch):
    """取不到本体角色数据时返回空元组 —— 是降级（少一档判据），不是失败。

    断言不写"一定是空"：这台机器上本体配置可能是齐全的，那种情况下它**应该**返回
    真名字。所以这里只钉两件事：① 永不抛；② 拿到的东西都是非空字符串。
    异常那条路用 monkeypatch 造（否则测试会随本机配置漂移）。
    """
    names = addressing.host_names(object())
    assert isinstance(names, tuple)
    assert all(isinstance(n, str) and n.strip() for n in names)

    import utils.config_manager as cm

    def _boom():
        raise RuntimeError("本体配置读不到")

    monkeypatch.setattr(cm, "get_config_manager", _boom)
    assert addressing.host_names(object()) == ()


def test_host_names_includes_the_nickname_from_the_persona(monkeypatch):
    """人设里的「昵称」要**自动**进名单 —— 使用者就是这么叫她的，不该让用户再抄一遍。

    真机数据形如：
        data[1] = "宅久皖萱"
        data[3] = {"宅久皖萱": {"昵称": "皖萱", "性别": "女", …}}
    """
    import utils.config_manager as cm

    class _Manager:
        def get_character_data(self):
            return [
                "宅久", "宅久皖萱", {"档案名": "宅久"},
                {"宅久皖萱": {"昵称": "皖萱", "性别": "女"}},
                {}, "", "", "", "",
            ]

    monkeypatch.setattr(cm, "get_config_manager", lambda: _Manager())
    assert addressing.host_names(object()) == ("宅久皖萱", "皖萱")


def test_host_names_falls_back_to_the_only_profile_when_the_key_differs(monkeypatch):
    """人设刚改过名（`data[1]` 与档案的键对不上）时，只有一个档案就用它。"""
    import utils.config_manager as cm

    class _Manager:
        def get_character_data(self):
            return [
                "主人", "新名字", {},
                {"旧名字": {"昵称": "小旧"}},
            ]

    monkeypatch.setattr(cm, "get_config_manager", lambda: _Manager())
    assert addressing.host_names(object()) == ("新名字", "小旧")


def test_host_names_without_a_profile_still_returns_the_full_name(monkeypatch):
    """拿不到档案（老版本/残缺配置）时至少还有全名 —— 少一档不是失败。"""
    import utils.config_manager as cm

    class _Manager:
        def get_character_data(self):
            return ["主人", "兰兰"]

    monkeypatch.setattr(cm, "get_config_manager", lambda: _Manager())
    assert addressing.host_names(object()) == ("兰兰",)


def test_host_names_ignores_a_blank_nickname(monkeypatch):
    import utils.config_manager as cm

    class _Manager:
        def get_character_data(self):
            return ["主人", "兰兰", {}, {"兰兰": {"昵称": "   "}}]

    monkeypatch.setattr(cm, "get_config_manager", lambda: _Manager())
    assert addressing.host_names(object()) == ("兰兰",)


def test_segments_of_survives_a_missing_enricher():
    assert addressing.segments_of({"raw": {"message": []}}, None) is None
    assert addressing.segments_of({"raw": {"message": []}}, object()) is None


# ── 结论的往返与文案 ────────────────────────────────────────────────

def test_verdict_round_trips_through_the_request():
    v = addressing.resolve_addressee(
        self_id=HER, quoted_message_id="m", quoted_sender_id=OTHER, segments=_seg(("reply", "")),
    )
    request = QQReplyRequest(message_text="x", sender_id="1", **addressing.verdict_kwargs(v))
    back = addressing.verdict_from_request(request)
    assert back is not None
    assert back.kind == v.kind and back.target_id == v.target_id


def test_verdict_from_request_is_none_without_a_verdict():
    """没有结论（开放平台 / 合成轮 / 旁路）→ None，提示词层据此退回老标签。"""
    assert addressing.verdict_from_request(QQReplyRequest(message_text="x", sender_id="1")) is None
    assert addressing.verdict_kwargs(None) == {
        "addressee_kind": "", "addressee_target": "", "addressee_evidence": "",
    }


def test_aim_label_wording_keeps_the_legacy_phrasing():
    """文案里必须留着"不是冲你来的"这句 —— 提示词与既有测试都认它。"""
    elsewhere = addressing.resolve_addressee(self_id=HER, quoted_message_id="m")
    assert "不是冲你来的" in addressing.aim_label(elsewhere)
    named = addressing.resolve_addressee(self_id=HER, text="猫娘", names=["猫娘"])
    assert "提到了你" in addressing.aim_label(named)
    assert addressing.aim_label(None) == ""


# ==========================================================================
# 二、减分（`score_necessity`）
# ==========================================================================

def _signals(**over) -> NecessitySignals:
    base = dict(
        message_text="这个怎么看", is_group=True, focus_active=True,
        pending_count=1, pending_threshold=3,
    )
    base.update(over)
    return NecessitySignals(**base)


def test_pointing_elsewhere_subtracts_and_says_why():
    v = score_necessity(_signals(addressee_kind=addressing.KIND_AT_OTHER, addressee_target=OTHER))
    assert v.breakdown.addressee == -30
    assert any("指向别人" in r for r in v.breakdown.reasons)
    assert any(OTHER in r for r in v.breakdown.reasons), "依据里要能看出指向了谁"


def test_first_at_other_is_marked_in_the_reason():
    v = score_necessity(_signals(
        addressee_kind=addressing.KIND_AT_OTHER, addressee_first_at_other=True,
    ))
    assert any("首段@" in r for r in v.breakdown.reasons)


def test_the_penalty_can_flip_a_trigger_into_a_wait():
    """**行为证据**：同一条消息，只因为"明确在跟别人说话"就不接了。"""
    fresh = score_necessity(_signals(message_text="这个怎么做"))
    assert fresh.decision == "trigger", "对照组本身必须是本来会接的那一类"
    pointed = score_necessity(_signals(
        message_text="这个怎么做",
        addressee_kind=addressing.KIND_REPLY_TO_OTHER, addressee_target=OTHER,
    ))
    assert pointed.decision == "wait"
    assert pointed.score < fresh.score


def test_a_message_that_still_earns_its_place_gets_through():
    """减分不是硬门控：内容/积压足够重的消息仍然接得住（被 @ 别人时顺手问她）。"""
    v = score_necessity(_signals(
        message_text="这个怎么做", pending_count=6, pending_threshold=3,
        addressee_kind=addressing.KIND_AT_OTHER, addressee_target=OTHER,
    ))
    assert v.decision == "trigger"
    assert v.breakdown.addressee == -30


def test_penalty_is_configurable_and_zero_disables_it():
    signals = _signals(addressee_kind=addressing.KIND_AT_OTHER)
    assert score_necessity(signals, addressee_penalty=0.0).breakdown.addressee == 0
    assert score_necessity(signals, addressee_penalty=50.0).breakdown.addressee == -50


def test_named_bot_no_longer_scores_in_necessity():
    """叫她的名字**不再**在必要性打分里加分：它现在是门控层的等同 @ 旁路。

    使用者口径（2026-09-29）：「叫猫娘名字也等于@她」—— 门控第 2 步就返回了，
    打分这一关根本走不到，原 `NAMED_BOT_SCORE = 40` 已删。这里钉住"不加分"，
    免得有人看到 `named_bot` 又往打分里塞一次（那就成了两套口径）。
    """
    v = score_necessity(_signals(
        message_text="今天天气不错", focus_active=False,
        addressee_kind=addressing.KIND_NAMED_BOT,
    ))
    assert v.breakdown.addressee == 0
    assert not any("叫名字" in r for r in v.breakdown.reasons)


def test_no_verdict_means_no_change():
    """没有结论（空 kind）时打分与改动前逐字一致 —— 这是回归口径。"""
    v = score_necessity(_signals())
    assert v.breakdown.addressee == 0
    assert v.breakdown.raw == float(
        v.breakdown.relevance + v.breakdown.content + v.breakdown.pressure + v.breakdown.presence
    )


# ==========================================================================
# 三、门控：硬门控开关 + 作用域
# ==========================================================================

class _FakeAttention:
    def __init__(self, *, score: float = 5.0, now: int = 1000):
        self._score = score
        self._now = now

    def _enabled(self) -> bool:
        return True

    def _current_time(self) -> int:
        return self._now

    def get_state(self, group_id: str):
        return SimpleNamespace(attention_score=self._score)

    def is_in_conversation(self, group_id: str) -> bool:
        return float(self._score) >= self.conversation_threshold()

    def conversation_threshold(self) -> float:
        return 2.0

    def get_group_multiplier(self, group_id: str) -> float:
        return 1.0

    def _minimum_threshold(self) -> float:
        return 1.0

    def _focus_threshold(self) -> float:
        return 4.0

    def is_first_reply_after_own_speech(self, group_id: str) -> bool:
        return False

    async def update_on_message(self, message: dict) -> None:
        return None

    def mark_focus(self, group_id: str) -> None:
        pass

    def lock_group(self, group_id: str) -> None:
        pass

    def wake_boost(self, group_id: str) -> None:
        pass


def _gate(*, level: str = "trusted", settings: dict | None = None, self_id: str = HER):
    base = {"backlog_labels": [], "buffer_max_count": 17, "reply_necessity_threshold": 40.0}
    base.update(settings or {})
    file_lines: list[str] = []
    plugin = SimpleNamespace(
        attention_service=_FakeAttention(),
        qq_client=SimpleNamespace(needs_attention=True, self_id=self_id, _sent_message_ids={}),
        permission_mgr=None,
        group_permission_mgr=SimpleNamespace(get_group_level=lambda gid: level),
        logger=SimpleNamespace(
            info=lambda msg, *a, **k: file_lines.append(str(msg)),
            warning=lambda *a, **k: None,
        ),
        _qq_settings=base,
        _emit_log=lambda *a, **k: None,
        _file_lines=file_lines,
    )
    service = QQAttentionGateService(plugin)
    service._backoff = SimpleNamespace(
        delay_seconds=lambda *a, **k: 0.0,
        record_wait=lambda *a, **k: 30.0,
        reset=lambda *a, **k: None,
        bypass_pending=lambda: 6,
    )
    return service, plugin


def _evaluate(service, **over):
    kwargs = dict(
        group_id=GROUP, sender_id=OTHER, message_text="在吗", timestamp=1000,
        segments=_seg(("at", OTHER), ("text", "")), mentioned_user_ids=[OTHER],
    )
    kwargs.update(over)
    return asyncio.run(service.evaluate(**kwargs))


def test_hard_gate_is_off_by_default():
    """默认只减分、不硬拦 —— 使用者选的是"默认强减分，另给开关可切硬门控"。"""
    service, plugin = _gate()
    decision = _evaluate(service)
    assert not str(decision.reason).startswith("addressee_first_at_other")
    assert any("[Addressee]" in line for line in plugin._file_lines), "结论必须留痕"


def test_hard_gate_ignores_when_switched_on():
    service, _ = _gate(settings={"addressee_ignore_first_at_other": True})
    decision = _evaluate(service)
    assert decision.action == "ignore"
    assert decision.reason == f"addressee_first_at_other({OTHER})"


def test_hard_gate_does_not_touch_at_bot():
    """@ 她永远是唯一旁路：首段 @ 别人 + 同时 @ 她 = 冲她来的，硬门控不许拦。"""
    service, _ = _gate(settings={"addressee_ignore_first_at_other": True})
    decision = _evaluate(
        service, is_at_bot=True,
        segments=_seg(("at", OTHER), ("at", HER)), mentioned_user_ids=[OTHER, HER],
    )
    assert decision.action == "reply"
    assert decision.reason == "at_bot"


def test_hard_gate_only_applies_to_competing_groups():
    """normal 群本来就不回复（走 relay 转达），硬门控不该改变它的语义。"""
    service, _ = _gate(level="normal", settings={"addressee_ignore_first_at_other": True})
    assert _evaluate(service).reason == "normal_group_passthrough"


def test_hard_gate_ignores_a_middle_segment_at():
    """只有**首段** @ 别人才硬拦；中间 @ 别人只走减分。"""
    service, _ = _gate(settings={"addressee_ignore_first_at_other": True})
    decision = _evaluate(
        service, segments=_seg(("text", ""), ("at", OTHER)), mentioned_user_ids=[OTHER],
    )
    assert not str(decision.reason).startswith("addressee_first_at_other")


def test_open_platform_channel_is_untouched():
    """本轮只管 NapCat 通道：开放平台每条群消息本来都是 @ 她的，第一步就返回。"""
    service, plugin = _gate(settings={"addressee_ignore_first_at_other": True})
    plugin.qq_client.needs_attention = False
    decision = _evaluate(service)
    assert decision.reason == "no_attention_needed"
    assert not any("[Addressee]" in line for line in plugin._file_lines)


def test_resolve_addressee_for_is_the_single_entry_point():
    """门控与 dispatcher 共用同一个 resolver（不各写一份判据）。"""
    service, _ = _gate()
    verdict = service.resolve_addressee_for(
        message_text="猫娘在吗", group_id=GROUP, mentioned_user_ids=[],
    )
    # 没配别名、本体角色数据也取不到 → 判不出来，如实返回 group_chatter。
    assert verdict.kind == addressing.KIND_GROUP_CHATTER
    # 配上别名就认得出来（同一个入口，行为由配置决定）。
    service.plugin._qq_settings["addressee_names"] = ["猫娘"]
    service._addressee_names_cache = None
    assert service.resolve_addressee_for(
        message_text="猫娘在吗", group_id=GROUP, mentioned_user_ids=[],
    ).kind == addressing.KIND_NAMED_BOT


# ==========================================================================
# 四、提示词层：有结论更具体，没结论逐字不变
# ==========================================================================

def _turn(**over) -> str:
    args = dict(
        group_scene_mode="group_collective", user_title="小明", sender_id=OTHER,
        group_id=GROUP, message="在吗", current_message_id="m1",
    )
    args.update(over)
    return QQAutoReplyPromptingMixin._build_group_turn_message(**args)


def test_prompt_uses_the_verdict_when_present():
    v = addressing.resolve_addressee(self_id=HER, quoted_message_id="m9", quoted_sender_id=OTHER)
    out = _turn(addressee=v, quoted_message_id="m9")
    assert "不是冲你来的" in out
    assert "回复别人的消息" in out


def test_prompt_keeps_legacy_wording_without_a_verdict():
    out = _turn(quoted_message_id="m9")
    assert "对方在回复别人的消息" in out and "不是冲你来的" in out
    bare = _turn()
    assert "不是冲你来的" in bare and "不要每条都接" in bare


def test_prompt_says_named_bot_out_loud():
    v = addressing.resolve_addressee(self_id=HER, text="猫娘在吗", names=["猫娘"])
    out = _turn(addressee=v, message="猫娘在吗")
    assert "提到了你" in out


def test_prompt_builder_passes_the_verdict_down_through_all_three_layers():
    v = addressing.resolve_addressee(self_id=HER, mentioned_user_ids=[OTHER])
    plugin = SimpleNamespace(
        attention_service=None,
        _build_group_turn_message=QQAutoReplyPromptingMixin._build_group_turn_message,
    )
    builder = QQPromptBuilder(plugin)
    out = builder.build_prompt_message(
        is_group=True, group_facing=False, group_scene_mode="group_collective",
        user_title="小明", sender_id=OTHER, group_id=GROUP, message="在吗", addressee=v,
    )
    assert "对方在跟别人说话" in out


def test_private_turns_ignore_the_verdict():
    """私聊不走这段标注（它只服务群聊的"这条是不是冲你来的"）。"""
    v = addressing.resolve_addressee(self_id=HER, mentioned_user_ids=[OTHER])
    plugin = SimpleNamespace(
        attention_service=None,
        _build_group_turn_message=QQAutoReplyPromptingMixin._build_group_turn_message,
    )
    out = QQPromptBuilder(plugin).build_prompt_message(
        is_group=False, group_facing=False, group_scene_mode="",
        user_title="小明", sender_id=OTHER, group_id=None, message="在吗", addressee=v,
    )
    assert out == "在吗"
