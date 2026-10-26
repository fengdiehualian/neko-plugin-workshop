"""``settings_schema`` 的迁移对照与一致性守卫。

默认值、白名单、入口 JSON schema、dashboard 快照四层现在都由 ``settings_schema``
生成。生成式的东西最容易出的问题是**抄错一个默认值**——那种错静默、且会改变线上
行为（比如把 ``attention_consume_ratio`` 抄成 0.01，手感立刻变）。所以这里把迁移前
的默认值原样钉住。
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest
from plugin.plugins.qq_auto_reply import settings_schema
from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

#: 迁移前 ``config_store.default_config()`` 的原值快照（2026-09 归并前逐键抄下来的）。
#: 改这里的值 = 有意改默认行为，不是重构 —— 请单独说明。
FROZEN_DEFAULTS: dict[str, object] = {
    "qq_connection_mode": "napcat",
    "onebot_url": "ws://0.0.0.0:6199",
    "token": "",
    "qq_open_app_id": "",
    "qq_open_client_secret": "",
    "qq_open_sandbox_enabled": False,
    "qq_open_identity_probe_enabled": False,
    "trusted_users": [],
    "trusted_groups": [],
    "speaker_trust_profiles": {},
    "normal_relay_probability": 0.1,
    "show_onboarding": True,
    "guide_step_napcat_done": False,
    "guide_step_config_done": False,
    "guide_step_runtime_done": False,
    "max_concurrent_messages": 3,
    "ai_connect_timeout_seconds": 10.0,
    "ai_turn_timeout_seconds": 60.0,
    "handler_shutdown_timeout_seconds": 10.0,
    "napcat_directory": "",
    "show_napcat_window": False,
    "reply_mode": "text",
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_batch_message_gain": 0.25,
    "attention_base_rise_rate": 0.02,
    "attention_message_boost": 0.15,
    "attention_keyword_boost_ratio": 1.8,
    "attention_fall_rate": 0.015,
    # 2026-09-29 使用者口径「满 10 的注意力发 6 条就踩线了」→ 0.10 降到 0.05
    # （满格 12 条才到焦点线、16 条才到在聊线）。见 test_qq_attention_behavior.py。
    "attention_consume_ratio": 0.05,
    "attention_maintenance_interval_seconds": 60.0,
    # 有意删除（不在此快照里）：`attention_honeymoon_seconds`(60) /
    # `attention_fall_seconds`(30) / `attention_fall_boost_attenuation`(0.3)
    # 随 rise/fall 相位机一起退役（2026-09-29）：那套"蜜月 + 让位"的前提是
    # 同一时刻只有一个群能说话。热度改由 `attention_heat_warm_gap_seconds` 决定。
    "attention_heat_warm_gap_seconds": 120,
    # 有意删除（不在此快照里）：「冷场破冰」整套的四个键 —— 相位时代的
    # `icebreaker_cold_threshold` 先被换成 `icebreaker_idle_seconds`，同一天稍后
    # 主动破冰与休眠**整套删除**（使用者口径「干脆不要这个先」），四个键一起退役：
    # icebreaker_idle_seconds / icebreaker_hold_seconds /
    # icebreaker_dormant_enabled / icebreaker_dormant_seconds，
    # 连带只服务破冰的话题表 `proactive_topics`。见 `test_icebreaker_family_is_gone`。
    "retroactive_review_min_unreviewed": 5,
    "retroactive_review_cooldown_seconds": 300,
    "group_memory_digest_interval_seconds": 300,
    "backlog_retention_limit": 200,
    "backlog_summary_threshold": 10,
    "backlog_notify_cooldown_seconds": 900,
    "backlog_issue_notify_threshold": 1,
    # 有意删除（不在此快照里）："strategy_mode"（原默认 "neko_dynamic"）。
    # 模式合并后它只剩单值 = 假旋钮，连配置键一起删了（SESSION-HANDOFF §24）。
    # 有意删除（不在此快照里）："enable_group_attention"（原默认 True）。
    # 它也是**假旋钮** —— 唯一让它为真的模式（neko_dynamic，出厂默认）下会被强制设回
    # True；而 neko_scene 下门控根本不跑（message_dispatcher 只在 neko_dynamic 下
    # 调 attention_gate_service）。现在注意力账本恒开（attention_service._enabled），
    # 是否门控由调用方按连接模式决定。
    "neko_dynamic_idle_timeout_seconds": 10.0,
    # 有意删除（不在此快照里）：`neko_dynamic_waking_users` /
    # `neko_dynamic_waking_keywords`（原默认 []）。两条都是"已废弃"的墓碑键，
    # 却留在表里当着零消费方的旋钮；昵称/别名能力已由 `addressee_names` +
    # `addressing.py` 接管，键进 `_LEGACY_ZOMBIE_KEYS`，见 `test_waking_keys_are_gone`。
    "retroactive_review_max_messages": 30,
    "retroactive_review_max_reply": 5,
    "group_buffer_enabled": True,
    "private_buffer_enabled": True,
    "auto_start_on_launch": False,
    "group_memory_enabled": False,
    "group_member_memory_enabled": False,
    "private_participant_memory_enabled": False,
    "allow_cross_group_context": False,
    "prompt_overrides": {},
    "group_prompts": {},
}


def _store() -> QQAutoReplyConfigStore:
    return QQAutoReplyConfigStore(pathlib.Path(tempfile.mkdtemp()))


def test_migrated_defaults_are_unchanged():
    """归并不得改动任何一个既有键的默认值。"""
    got = _store().default_config()
    wrong = {
        key: (expected, got.get(key, "<缺失>"))
        for key, expected in FROZEN_DEFAULTS.items()
        if got.get(key, "<缺失>") != expected
    }
    assert not wrong, f"默认值在归并中变了：{wrong}"


def test_backlog_labels_default_is_intact():
    """``backlog_labels`` 是结构值，单独比（它的默认由 config_store 的工厂给出）。"""
    got = _store().default_config()["backlog_labels"]
    assert got == [{
        "id": "mention",
        "label": "点名",
        "keywords": ["@全体成员"],
        "priority": 60,
    }]


def test_schema_covers_every_config_key():
    """表必须覆盖 ``default_config()`` 的每一个键 —— 漏掉的键就没有单一真相。"""
    missing = sorted(set(_store().default_config()) - set(settings_schema.BY_KEY))
    assert not missing, f"这些键不在 settings_schema 的声明表里：{missing}"


def test_defaults_are_fresh_objects_per_call():
    """可变默认值必须每次给新对象 —— 否则一处改动会污染所有调用方。"""
    first = _store().default_config()
    second = _store().default_config()
    assert first["trusted_users"] is not second["trusted_users"]
    assert first["backlog_labels"] is not second["backlog_labels"]
    assert first["attention_emotion_multipliers"] is not second["attention_emotion_multipliers"]


def test_input_schema_includes_aliases():
    """别名必须在入口 JSON schema 里，否则带 additionalProperties=False 的校验会拒掉那次保存。"""
    props = settings_schema.input_schema_properties()
    for spec in settings_schema.SETTINGS:
        for alias in spec.aliases:
            assert alias in props, f"{alias} 是 {spec.key} 的别名，却没进 input_schema"


def test_ui_ids_are_unique():
    """同一个元素 id 被两个键引用，界面会互相覆盖。"""
    ids = [s.ui.id for s in settings_schema.SETTINGS if s.ui and s.ui.id]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"UI 元素 id 重复：{dupes}"


def test_emotion_multiplier_defaults_match_attention_service():
    """情绪倍率表的默认值必须与 attention_service 的实现一致（归并前逐字抄的）。"""
    from plugin.plugins.qq_auto_reply.attention_service import _EMOTION_MULTIPLIER

    assert settings_schema.DEFAULT_EMOTION_MULTIPLIERS == _EMOTION_MULTIPLIER


@pytest.mark.parametrize("bad", ["", "{}", "{", "[]", "null", {"calm": "x"}, {"": 1.0}, {"calm": float("inf")}])
def test_emotion_multiplier_normalizer_falls_back_on_garbage(bad):
    """非法输入整份回退默认 —— 半份坏数据比没有数据更难查。"""
    got = QQAutoReplyConfigStore.normalize_emotion_multipliers(bad)
    assert got == settings_schema.DEFAULT_EMOTION_MULTIPLIERS


def test_emotion_multiplier_normalizer_accepts_valid():
    assert QQAutoReplyConfigStore.normalize_emotion_multipliers('{"calm": 0.5}') == {"calm": 0.5}
    assert QQAutoReplyConfigStore.normalize_emotion_multipliers({"a": 1, "b": -2}) == {"a": 1.0, "b": -2.0}


# ==========================================================================
# 死键守卫：能存的键必须真的被读过
# ==========================================================================

BASE = pathlib.Path(__file__).resolve().parents[1]

#: 纯管道模块 —— 它们**必然**出现键名（声明/归一/白名单/快照），所以不算"被读过"。
#: 一个键如果只在这几个文件里出现，那它就是死键（`retroactive_review_max_reply`
#: 都曾如此：默认值/保存/校验/界面全都有，运行时没人读）。
_PLUMBING = {
    "settings_schema.py",
    "config_store.py",
    "settings_service.py",
    "dashboard_service.py",
}

#: **不由插件业务代码读取**的键。每加一个都要在这里说明理由 ——
#: 这份名单是"死键"与"合法的别处消费"之间唯一的界线。
#:
#: ⚠️ 理由必须**为真**。这个测试只检查"名字在表里 + 理由非空"，检查不了理由本身，
#: 所以写假理由就能把死键放过去 —— 已经发生过：下面两条曾经写着"纯前端开关"，
#: 而前端是把值读进一个**从不被读取**的变量。写理由前请先按名字搜一遍消费方。
_EXEMPT: dict[str, str] = {
    "guide_step_napcat_done": "引导进度标记：由界面读写并回显，后端只做透传存储",
    "guide_step_runtime_done": "同上（dashboard 的 guide 块另有一份派生判断）",
    # 这两条曾经的理由是"纯前端开关 / 由界面读写并回显"，**不成立**（2026-09-23 核对）：
    #   guide_step_config_done: 前端只在 script.js:167 读进 state.config.guideStepConfigDone，
    #       而该字段全文只出现 2 次（初始化 + 这次赋值），**从未被读取**；
    #       也没有任何前端代码以 action:'save' 提交它（对比 guide_step_napcat_done 有：
    #       script.js:96）。后端同样无消费方。
    #   show_onboarding: 同上（script.js:50/165），值进了一个死变量。
    # 保留键不删是为了配置兼容 + 将来接回引导页；但**不要**把它们当成"已经接通的开关"。
    "guide_step_config_done": "残留键：无界面提交、无消费方（值只进死变量）。保留待接回",
    "show_onboarding": "残留键：无消费方（前端读进死变量）。保留待接回",
    # 服务端只负责持久化与回显：语言选择由前端 `onLangChange` 提交、`s.locale` 读回，
    # 插件业务代码不需要按语言分支（i18n 解析在前端/宿主侧）。
    "locale": "界面语言偏好：前端提交并回显，服务端只透传存储",
    # 这两个由**连接器工厂**读（决定拨正式/沙箱域名、是否写取证行）。消费方是
    # utils.connection.onebot 的 create_onebot_connection：现在读本仓的 _vendor 副本，
    # PR #2996 合并后读宿主包 —— 所以它们永远不会出现在插件的业务代码里。
    "qq_open_sandbox_enabled": "由连接器工厂读取（本仓 _vendor 副本 / 合并后宿主包）",
    "qq_open_identity_probe_enabled": "同上：连接器据它决定是否写取证行",
}


def _business_sources() -> dict[str, str]:
    """非管道、非测试的插件源码。``__init__.py`` 算业务代码（里面有真实读取）。"""
    out: dict[str, str] = {}
    for path in sorted(BASE.glob("*.py")):
        if path.name in _PLUMBING:
            continue
        out[path.name] = path.read_text(encoding="utf-8")
    return out


def test_every_saveable_key_is_actually_read():
    """**死键守卫**：每个 ``saveable`` 的键都必须在业务代码里被读过。

    这条是本轮重构的核心护栏。参数页上多一个能改、能存、能回显、却对行为毫无影响的
    旋钮，比没有这个旋钮更糟 —— 用户会以为自己调了。历史上真的发生过两次
    （``retroactive_review_max_reply`` 等），而当时没有任何测试
    能发现。加键时若这条红了：要么去业务代码里真的读它，要么进 ``_UI_ONLY`` 并写明理由。
    """
    sources = _business_sources()
    unread: list[str] = []
    for spec in settings_schema.SETTINGS:
        if not spec.saveable or spec.key in _EXEMPT:
            continue
        needle = f'"{spec.key}"'
        if not any(needle in src for src in sources.values()):
            unread.append(spec.key)
    assert not unread, (
        f"这些键能存能显示，但没有任何业务代码读它们（改了不生效）：{unread}。"
        f"接上消费端，或加进 _EXEMPT 并写明为什么插件侧不需要读。"
    )


def test_exemptions_stay_small_and_justified():
    """豁免名单不能悄悄变长 —— 每一条都得有理由。"""
    assert len(_EXEMPT) <= 8, f"_EXEMPT 膨胀到 {len(_EXEMPT)} 条，重新审视一遍"
    for key, why in _EXEMPT.items():
        assert key in settings_schema.BY_KEY, f"_EXEMPT 里的 {key} 不是表里的键"
        assert why.strip(), f"{key} 没有写明豁免理由"


# ==========================================================================
# 界面与后端的一致性
# ==========================================================================

def _html() -> str:
    return (BASE / "static" / "napcat.html").read_text(encoding="utf-8")


def _html_range(html: str, element_id: str) -> dict[str, float]:
    """抠出某个 input 的 min/max（只认带 id 的那个标签）。"""
    import re

    m = re.search(r'<input[^>]*id="%s"[^>]*>' % re.escape(element_id), html)
    assert m, f"napcat.html 里找不到 id={element_id} 的 input"
    tag = m.group(0)
    out: dict[str, float] = {}
    for attr in ("min", "max"):
        hit = re.search(r'%s="([-0-9.]+)"' % attr, tag)
        if hit:
            out[attr] = float(hit.group(1))
    return out


def test_ui_input_ranges_do_not_exceed_backend_clamps():
    """界面**不得**提供后端会钳掉的值。

    这两处过去是手工镜像（``settings_service`` 里那句"与前端 max=10 对齐"就是证据），
    会静默漂移：界面写到 20、后端钳到 10，用户填 20 保存后刷新又变回 10。
    生成式之后新字段天然一致，这条守的是**既有的手写字段**。
    """
    html = _html()
    problems: list[str] = []
    for spec in settings_schema.SETTINGS:
        if not spec.ui or not spec.ui.id or spec.ui.kind != "number":
            continue
        rng = _html_range(html, spec.ui.id)
        if spec.floor is not None and "min" in rng and rng["min"] < float(spec.floor):
            problems.append(f"{spec.key}: 界面 min={rng['min']} < 后端 floor={spec.floor}")
        if spec.ceiling is not None and "max" in rng and rng["max"] > float(spec.ceiling):
            problems.append(f"{spec.key}: 界面 max={rng['max']} > 后端 ceiling={spec.ceiling}")
    assert not problems, "界面与后端范围不一致：\n" + "\n".join(problems)


def test_every_ui_field_is_both_loaded_and_saved():
    """界面字段必须**既回显又提交**。

    只提交不回显 = 存得下但刷新后显示不出已保存的值（``local_stt_url`` 就是这么漏的，
    它连快照都没进）。只回显不提交 = 改了不生效。两个方向都要挡住。
    """
    html = _html()
    missing: list[str] = []
    for spec in settings_schema.SETTINGS:
        if not spec.ui or not spec.ui.id or not spec.saveable:
            continue
        # 数**单引号引用的 id**：markup 用双引号（id="cfg-x"），JS 一律用单引号
        # （getElementById('cfg-x') 或 intVal('cfg-x', d)），所以这个计数就是
        # "JS 里引用了几次"。数字字段的提交走 intVal/floatVal 助手，getElementById
        # 在助手内部 —— 按 getElementById 计数会把它误判成没提交。
        occurrences = html.count("'%s'" % spec.ui.id)
        if occurrences < 2:
            missing.append(f"{spec.key} ({spec.ui.id}): JS 里只引用 {occurrences} 次（需 ≥2：回显+提交）")
    assert not missing, "界面字段没有被同时回显与提交：\n" + "\n".join(missing)


def test_every_exempt_key_is_actually_referenced_somewhere():
    """豁免名单里的键必须在**设置管线之外**真的被引用过。

    `_EXEMPT` 原先只有"名字在表里 + 理由非空"两道检查，**理由本身没人验** ——
    写一句"纯前端开关"就能把死键放过去（`show_onboarding` /
    `guide_step_config_done` 就是这么藏了很久）。

    这条做的是能机械验证的那一半：把设置管线（真源、写盘、快照、入口）和测试排除后，
    键名必须在剩下的代码里出现过 —— 界面、`_vendor` 连接器都算。它挡不住"引用了但
    引用无效"（那需要人读），但能挡住"彻底孤儿键长期挂在豁免名单里"。
    """
    plumbing = {
        "settings_schema.py", "settings_service.py", "dashboard_service.py",
        "__init__.py", "config_store.py",
    }
    haystack: list[str] = []
    for path in BASE.rglob("*"):
        if not path.is_file() or "tests" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix not in {".py", ".js", ".html", ".json"}:
            continue
        if path.name in plumbing:
            continue
        haystack.append(path.read_text(encoding="utf-8", errors="replace"))

    orphans = [
        key for key in _EXEMPT
        if not any(key in text for text in haystack)
    ]
    assert not orphans, (
        "这些键在豁免名单里，但设置管线之外**任何地方**都没有引用 —— "
        f"要么删掉键，要么删掉豁免: {sorted(orphans)}"
    )


def test_mode_enums_derive_from_the_schema(monkeypatch):
    """`reply_mode` 的合法取值必须**派生自真源**，不能手工镜像。

    `config_store` 里的归一化是「不在集合里就**静默**改成默认值」。这份集合曾经是
    手抄的副本，于是往真源 `enum` 里加一个新取值时：界面能选、能存下来，运行时却被
    无声改回旧默认 —— 能配置但无效，且没有日志。

    `qq_connection_mode` 早就是从真源派生的（`CONNECTION_MODES`），所以这不是
    "有意分家"，是漏改。

    `strategy_mode` 那一份随「回复策略」一起删除了（SESSION-HANDOFF §24）：
    它已经没有第二个取值，归一化本身没有意义。
    """
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    assert set(QQAutoReplyConfigStore.VALID_REPLY_MODES) == set(
        settings_schema.BY_KEY["reply_mode"].enum or ()
    )

    # 模拟「往真源加了一个新取值」：归一化必须认得它。手工镜像的写法会在这里失败
    # （新值被静默降级成默认值）。
    monkeypatch.setattr(
        QQAutoReplyConfigStore, "VALID_REPLY_MODES", frozenset({"text", "voice", "both", "brand_new"})
    )
    assert QQAutoReplyConfigStore.normalize_reply_mode("brand_new") == "brand_new", (
        "归一化没有引用 VALID_REPLY_MODES，枚举是硬编码的 —— 新增取值会被静默改回默认"
    )


def test_reply_strategy_setting_is_gone():
    """「回复策略」删除契约（SESSION-HANDOFF §24）。

    它在模式合并后只剩 `neko_dynamic` 一个取值 —— 界面上选不动、代码里恒真，
    是个假旋钮。整套（配置键 + 归一化 + 归一化助手）都删掉了；谁把它加回来，
    这条会红。
    """
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    assert "strategy_mode" not in settings_schema.BY_KEY, "策略模式键被加回来了"
    assert "strategy_mode" not in settings_schema.defaults(), "策略模式又写进默认值了"
    assert not hasattr(QQAutoReplyConfigStore, "_normalize_strategy_mode"), "归一化助手被加回来了"
    assert not hasattr(QQAutoReplyConfigStore, "VALID_STRATEGY_MODES"), "枚举集合被加回来了"


def test_waking_keys_are_gone():
    """两条 `neko_dynamic_waking_*` 墓碑键的删除契约（2026-09-27）。

    它们的描述一直写着「已废弃（改用 attention + backlog_labels）」，但键留在真源表
    里 —— 于是配置里挂着两个**看起来能调、实际全仓零消费方**的旋钮（与
    `strategy_mode`、`enable_group_attention` 同一类假旋钮）。

    昵称/别名那一半能力现在由 `addressee_names` + `addressing.py` 正式接管：
    本体的名字自动生效，用户只需补别名。谁把这两条加回来，这条会红。
    """
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    for key in ("neko_dynamic_waking_users", "neko_dynamic_waking_keywords"):
        assert key not in settings_schema.BY_KEY, f"{key} 被加回真源表了"
        assert key not in settings_schema.defaults(), f"{key} 又写进默认值了"
        assert key in QQAutoReplyConfigStore._LEGACY_ZOMBIE_KEYS, (
            f"{key} 不在僵尸键名单里 —— 老配置文件里的残留值会被原样传下去"
        )
    # 新机制的键在位（删旧的不是把功能删掉）。
    assert "addressee_names" in settings_schema.BY_KEY
    assert "addressee_penalty" in settings_schema.BY_KEY


def test_icebreaker_family_is_gone():
    """「主动破冰 + 休眠」删除契约（2026-09-29）。

    使用者口径：「不能用时间判断，需要换一个策略」→ 四个事件驱动备选给他选 →
    「干脆不要这个先」。于是触发判据（`icebreaker_idle_seconds`）、按住时长
    （`icebreaker_hold_seconds`）、休眠开关与自动醒秒数、以及只服务破冰的话题表
    （`proactive_topics`）一起退役；更早的 `icebreaker_cold_threshold` 已经在
    §40 那轮进过僵尸名单。谁把它们加回来，这条会红。
    """
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    for key in (
        "icebreaker_cold_threshold",
        "icebreaker_idle_seconds",
        "icebreaker_hold_seconds",
        "icebreaker_dormant_enabled",
        "icebreaker_dormant_seconds",
        "proactive_topics",
    ):
        assert key not in settings_schema.BY_KEY, f"{key} 被加回真源表了"
        assert key not in settings_schema.defaults(), f"{key} 又写进默认值了"
        assert key in QQAutoReplyConfigStore._LEGACY_ZOMBIE_KEYS, (
            f"{key} 不在僵尸键名单里 —— 老配置文件里的残留值会被原样传下去"
        )


def test_auto_wake_key_is_gone():
    """「到点自动醒」删除契约（2026-09-29）。

    使用者口径：「到点自动醒也不要。注意，这个逻辑只在脚本内处理」。
    删掉的是**整条唤醒路径**：脚本里不该再有"睡够多久就醒"的判据，
    休眠状态也不该再有「到点醒的时刻」这种字段（`dormant` 就是个布尔量）。
    """
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    key = "dormancy_auto_wake_seconds"
    assert key not in settings_schema.BY_KEY, "被加回真源表了"
    assert key not in settings_schema.defaults(), "又写进默认值了"
    assert key in QQAutoReplyConfigStore._LEGACY_ZOMBIE_KEYS, (
        "不在僵尸键名单里 —— 老配置文件里的残留值会被原样传下去"
    )


def test_legacy_strategy_key_is_dropped_on_load(tmp_path):
    """老配置里残留的 `strategy_mode` 必须被当僵尸键清掉，而不是原样传下去。

    与 `enable_group_attention` 那类"留着无害"的残留不同：这里主动删，
    因为一个只剩单值的旋钮留在配置里，只会让下一个人以为它还能调。
    """
    import asyncio

    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    store = QQAutoReplyConfigStore(tmp_path)
    store.path.write_text(
        '{"strategy_mode": "neko_scene", "reply_mode": "text"}', encoding="utf-8"
    )
    loaded = asyncio.run(store.load())
    assert "strategy_mode" not in loaded
    saved = asyncio.run(store.save(loaded))
    assert "strategy_mode" not in saved
