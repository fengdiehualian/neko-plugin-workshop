"""`bored` 情绪（「没兴趣，去别的群看看」）的行为契约。

使用者原话：「我看一眼这个新的焦点群，如果没有我感兴趣的话题我就会回到旧焦点。
但是猫娘不会这样。」—— 猫娘缺的正是「没兴趣就走」这个信号。补法不是新造一套系统，
而是复用已有的情绪 → 焦点通道：LLM 在回复里打 `<feeling>bored</feeling>`，注意力
立刻让出焦点。

本文件同时钉住一个**升级陷阱**：情绪倍率表是用户配置的一部分，老配置是旧版本存的
快照。如果「配置里没这个键」被当成「倍率为 0」，新加的情绪对老用户会彻底静默失效 ——
`set_emotion` 直接 return，连日志都没有。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import attention_service, settings_schema
from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

#: 使用真实 business_config.json 里的参数
LIVE = {
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_base_rise_rate": 0.08,
    "attention_message_boost": 0.15,
    "attention_keyword_boost_ratio": 1.8,
    "attention_heat_warm_gap_seconds": 120,
    "attention_fall_rate": 0.015,
    "attention_consume_ratio": 0.1,
    "attention_at_bot_boost": 3.0,
    "attention_question_boost": 1.5,
    "attention_wake_boost_ratio": 0.75,
    "attention_decay_interval_seconds": 5.0,
    "attention_frequency_target_gap": 30.0,
    "attention_frequency_min_multiplier": 0.15,
    "attention_frequency_max_multiplier": 1.8,
    "backlog_labels": [],
}

#: 使用者当前那份 business_config.json 里的**真实**情绪表 —— 存于 `bored` 出现之前。
#: 这正是升级场景：新情绪在老配置里没有键。
STALE_CONFIG_TABLE = {
    "arguing": 1.2, "proud": 0.8, "annoyed": 0.5, "playful": 0.3, "curious": 0.2,
    "calm": 0.0, "sad": -0.4, "embarrassed": -0.6, "sulking": -0.9,
}


class _Clock:
    def __init__(self, start: int = 100_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now


def _service(groups=("A", "B"), *, emotion_table=None):
    settings = dict(LIVE)
    settings["attention_emotion_multipliers"] = (
        dict(settings_schema.DEFAULT_EMOTION_MULTIPLIERS)
        if emotion_table is None
        else dict(emotion_table)
    )
    plugin = SimpleNamespace(
        group_permission_mgr=SimpleNamespace(
            list_groups=lambda: [{"group_id": g} for g in groups]
        ),
        _qq_settings=settings,
        backlog_store=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    clock = _Clock()
    svc._current_time = clock
    return svc, clock


def _seed(svc, gid, score, *, now):
    st = svc._load_state(gid)
    st.attention_score = score
    st.last_decay_at = now
    st.last_message_at = now          # 刚刚还有人说话 → 热聊档
    st.focus_acquired_at = now
    st.last_focus_at = now
    svc._write_state(st)


def _feel(svc, gid, emotion):
    asyncio.run(svc.set_emotion(gid, emotion))


def _focus_of(svc, clock):
    states = [svc._load_state(g) for g in ("A", "B")]
    for s in states:
        svc._write_state(s)
    chosen = svc._choose_focus_state(states, clock.now)
    return chosen.group_id if chosen else ""


# ── 意图 1：bored 必须真的让出焦点 ──────────────────────────────────

def test_bored_yields_focus_to_another_active_group():
    """在 A 群没兴趣 → 焦点必须交给同样活跃的 B 群。"""
    svc, clock = _service()
    now = clock.now
    _seed(svc, "A", 6.0, now=now)      # 当前焦点群，聊得正热
    _seed(svc, "B", 5.0, now=now)      # 另一个活跃群，等着接
    assert _focus_of(svc, clock) == "A"

    _feel(svc, "A", "bored")

    assert _focus_of(svc, clock) == "B", (
        "标记 bored 后焦点没走 —— 猫娘还是赖在没兴趣的群里，"
        "这正是使用者说的「猫娘不会这样」"
    )


def test_bored_pushes_score_to_the_focus_line_and_enters_fall():
    """让分机制：把分数压到档位线。

    （2026-09-29 之前这里还会顺带把群打成 `fall` 相位；相位机删除后没有"回落相位"
    这个开关，压分本身 + 群自己安静下来就足以让她不再赖着 —— 冷却是按"这个群最后
    一条消息的时刻"派生的，不能由她自己的情绪直接改写。）
    """
    svc, clock = _service()
    _seed(svc, "A", 8.0, now=clock.now)
    _feel(svc, "A", "bored")

    st = svc._load_state("A")
    assert st.attention_score <= svc._focus_threshold() + 1e-6


def test_bored_is_weaker_than_sulking():
    """bored 是「没兴趣」，不是「赌气」—— 不该像 sulking 那样把分数打到最低。"""
    svc, clock = _service()
    _seed(svc, "A", 8.0, now=clock.now)
    _seed(svc, "B", 8.0, now=clock.now)
    _feel(svc, "A", "bored")
    _feel(svc, "B", "sulking")

    bored = svc._load_state("A").attention_score
    sulking = svc._load_state("B").attention_score
    assert bored > sulking, (
        f"bored({bored:.2f}) 不该和 sulking({sulking:.2f}) 一样狠："
        f"没兴趣只是走开，赌气才是清零"
    )


# ── 意图 2：老配置里没有 bored 这个键，也必须生效 ────────────────────

def test_new_emotion_works_for_a_config_saved_before_it_existed():
    """升级陷阱：老配置的快照里没有 `bored` 键，`bored` 仍须正常工作。

    使用者真实的 business_config.json 里就是这张 9 键表。若按「表里没有 → 0.0」
    处理，`set_emotion` 会在开头直接 return，标记完全没有反应。
    """
    svc, clock = _service(emotion_table=STALE_CONFIG_TABLE)
    assert "bored" not in svc.plugin._qq_settings["attention_emotion_multipliers"]

    assert svc._emotion_multiplier("bored") == attention_service._EMOTION_MULTIPLIER["bored"], (
        "老配置里缺键时新情绪倍率丢了 —— 升级后新情绪会静默失效"
    )

    _seed(svc, "A", 8.0, now=clock.now)
    _feel(svc, "A", "bored")
    st = svc._load_state("A")
    assert st.emotion == "bored", "老配置下 bored 被 set_emotion 拒绝了"
    assert st.attention_score <= svc._focus_threshold() + 1e-6, "bored 应当压分"


def test_configured_value_still_overrides_the_builtin_default():
    """覆盖表语义：用户写了的键以用户为准，包括显式 0.0（= 关掉这个情绪的影响）。"""
    table = dict(settings_schema.DEFAULT_EMOTION_MULTIPLIERS)
    table["bored"] = 0.0
    table["arguing"] = 2.5
    svc, _ = _service(emotion_table=table)

    assert svc._emotion_multiplier("bored") == 0.0, "用户显式写的 0.0 被默认值覆盖了"
    assert svc._emotion_multiplier("arguing") == 2.5


def test_invented_emotion_is_still_rejected():
    """补齐全表不能把「LLM 自己编的情绪」也放进来。"""
    svc, clock = _service(emotion_table=STALE_CONFIG_TABLE)
    _seed(svc, "A", 8.0, now=clock.now)
    _seed(svc, "B", 8.0, now=clock.now)
    _feel(svc, "A", "happy")
    _feel(svc, "B", "calm")

    assert svc._load_state("A").emotion == "calm", "表外情绪（LLM 自造）不该被写进状态"


def test_invalid_configured_table_falls_back_whole():
    """坏值仍然整份回退内置默认（半份坏数据比没有更难查）。"""
    svc, _ = _service(emotion_table={"arguing": "not-a-number"})
    assert svc._emotion_multiplier("arguing") == attention_service._EMOTION_MULTIPLIER["arguing"]


# ── 意图 3：情绪降温必须朝 calm 走，不能越降越强 ────────────────────

def _decay_chain(svc, start_emotion, *, steps=6):
    """返回连续降温经过的情绪序列。"""
    clock = svc._current_time
    now = clock()
    state = svc._load_state("A")
    state.emotion = start_emotion
    state.emotion_updated_at = now
    chain = [start_emotion]
    for step in range(1, steps + 1):
        svc._current_time = lambda t=now + step * 31: t
        svc._decay_emotion(state, now + step * 31)
        chain.append(state.emotion)
        if state.emotion == "calm":
            break
    return chain


def test_positive_emotion_decays_stepwise_toward_calm():
    """上升侧情绪必须逐级朝 calm 降，不能跳过中间档。

    `proud` 曾漏在降温阶梯外，被当成表外情绪**一步归零到 calm**；更糟的是
    `_decay_emotion` 里另抄了一份上升侧名单，新加的上升侧情绪会走回落侧分支、
    越衰减越强。
    """
    svc, _ = _service()
    chain = _decay_chain(svc, "proud")
    assert chain == ["proud", "annoyed", "playful", "curious", "calm"], (
        f"proud 降温路径不对: {chain}"
    )


def test_negative_emotion_decays_stepwise_toward_calm():
    svc, _ = _service()
    chain = _decay_chain(svc, "bored")
    assert chain == ["bored", "embarrassed", "sad", "calm"], f"bored 降温路径不对: {chain}"


def test_decay_never_strengthens_the_emotion():
    """对每个情绪、每一级降温，倍率都必须单调靠近 0（不能越降温越激动）。"""
    svc, _ = _service()
    table = attention_service._EMOTION_MULTIPLIER
    for start in table:
        if start == "calm":
            continue
        chain = _decay_chain(svc, start, steps=len(table) + 1)
        values = [abs(table[name]) for name in chain]
        assert values == sorted(values, reverse=True), (
            f"{start} 降温路径 {chain} 的强度不是单调递减: {values}"
        )
        assert chain[-1] == "calm", f"{start} 降温最终没回到 calm: {chain}"


# ── 意图 4：不出声也能走（「看一眼就走」）────────────────────────────
#
# 使用者的场景是「看一眼新焦点群，没兴趣就走」。如果 bored 必须**先回一句话**才能
# 表达，那猫娘在没兴趣的群里还得先发言一次，体验是反的。所以「只发 feeling、
# 不带 <msg>」必须能走通：解析出 feeling、判定为不回复、而情绪照旧生效。

def _postprocess_node():
    from plugin.plugins.qq_auto_reply.reply_postprocess_node import QQReplyPostprocessNode

    plugin = SimpleNamespace(
        _emit_log=lambda *a, **k: None,
        _sanitize_generated_reply=lambda text: text,
        i18n=SimpleNamespace(t=lambda key, default=None: default or key),
    )
    return QQReplyPostprocessNode(plugin)


def test_silent_boredom_is_parsed_as_feeling_without_a_reply():
    """`<feeling>bored</feeling>` 单独出现 = 不出声地让出焦点。"""
    node = _postprocess_node()
    context = SimpleNamespace(ephemeral_session=False, force_reply=False, permission_level="normal")
    model_result = SimpleNamespace(reply_text="<feeling>bored</feeling>")

    outcome = asyncio.run(node.finalize(context, model_result))

    assert outcome.feeling == "bored", "只发 feeling 时情绪没被解析出来"
    assert not outcome.reply_text, "这条不该真的发出消息"
    assert outcome.postprocess_reason == "llm_skip", (
        f"应判定为「不回复」（llm_skip），实际 {outcome.postprocess_reason!r}"
    )


def test_feeling_is_forwarded_before_buffering_and_cooldown():
    """情绪上报必须发生在缓冲/冷却/交付**之前**，否则「不出声就走」会被丢掉。

    ``reply_pipeline`` 里那段 ``# 情绪/标记：内部状态，先于缓冲/冷却/交付更新``
    是有意为之的顺序：llm_skip 的回复没有投递计划，一旦情绪上报被挪到交付之后，
    就只有「真的发了消息」的回复才能改情绪 —— 而没兴趣的场景恰恰是不发消息。
    这条顺序断言用源码顺序钉住，因为运行时的差异只在「不发消息」时体现。
    """
    import ast
    import pathlib

    source = pathlib.Path(
        __import__(
            "plugin.plugins.qq_auto_reply.reply_pipeline", fromlist=["x"]
        ).__file__
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    checked = 0
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        calls = [
            n.lineno
            for n in ast.walk(func)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "set_emotion"
        ]
        if not calls:
            continue
        checked += 1
        set_emotion_at = min(calls)
        buffer_at = [
            n.lineno
            for n in ast.walk(func)
            if isinstance(n, ast.Name) and n.id in ("skip_buffer", "buffer_on")
        ]
        assert buffer_at, "找不到缓冲判定，测试需要跟着改"
        assert set_emotion_at < min(buffer_at), (
            f"set_emotion (line {set_emotion_at}) 被挪到缓冲判定 "
            f"(line {min(buffer_at)}) 之后 —— 不发消息的情绪信号会丢失"
        )
    assert checked == 1, f"预期只有一处 set_emotion 上报点，找到 {checked} 处"
