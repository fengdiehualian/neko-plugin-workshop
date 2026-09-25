"""语气感知（emotion sense，0.6.0）测试。

覆盖：recent.json 水位 diff（基线/新轮/滚动窗口 hash 兜底）、门控链
（总开关/check_rate 概率/min_interval 节流）、筛选模式命中矩阵与阶段灵敏度
偏置、校正模式趋势窗口（偏暖→rising_tide 提醒 / 偏冷→换状态提醒）、
error 降级与 CSRF 缓存、调试入口 peek 语义、update_settings 新字段；
以及 0.6.0 模型槽位改造：tone_slot 校验、_resolve_tone_slot 解析链
（follow_assist/follow_core/follow_conversation 递归/custom/具名 provider 回落/
free 降级/防环）、_load_core_config 缓存与坏文件降级、直连回复解析容错、
直连全链路（不碰宿主端点）。

依赖 tests/conftest.py 的 ``plugin_factory_full`` fixture。
运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio
from datetime import date


def run(coro):
    return asyncio.run(coro)


class _SenseHttp:
    """宿主 HTTP 桩：角色解析 + 主动搭话开关 + health(CSRF) + recent_file + emotion/analysis。"""

    def __init__(self, current="YUI", emotion=("neutral", 0.9)):
        self.current = current
        self.master = True
        self.turns: list[dict] = []  # recent.json 的 content
        self.emotion = emotion  # (label, confidence)；None = 端点返回 error
        self.analysis_calls: list[dict] = []

    def set_turns(self, pairs: list[tuple[str, str]]) -> None:
        """pairs: [(用户消息, 她的回复), ...] 按时间顺序。"""
        self.turns = []
        for user_text, her_text in pairs:
            self.turns.append({"type": "human", "data": {"content": user_text}})
            self.turns.append({"type": "ai", "data": {"content": her_text}})

    def add_turn(self, user_text: str, her_text: str) -> None:
        self.turns.append({"type": "human", "data": {"content": user_text}})
        self.turns.append({"type": "ai", "data": {"content": her_text}})

    async def __call__(self, method, path, body=None, headers=None):
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": self.current}
        if path == "/api/characters":
            return {"猫娘": {self.current: {}}}
        if path == "/health":
            return {"status": "ok", "instance_id": "test-csrf-token"}
        if path.startswith("/api/memory/recent_file"):
            # 模拟宿主 resolve_recent_file_path：逻辑文件名必须 recent_<角色>.json，
            # 裸 recent.json 在真实宿主是 400（urllib 异常 → 插件拿到 None）
            import re as _re
            from urllib.parse import parse_qs as _parse_qs
            from urllib.parse import urlparse as _urlparse

            fn = (_parse_qs(_urlparse(path).query).get("filename") or [""])[0]
            if not _re.match(r"^recent_(.+)\.json$", fn):
                return None
            return {"content": list(self.turns), "fingerprint": str(len(self.turns))}
        if path == "/api/emotion/analysis":
            self.analysis_calls.append({"body": body, "headers": headers})
            if self.emotion is None:
                return {"error": "情绪分析模型配置缺失: API密钥未提供且配置中未设置默认密钥"}
            return {"emotion": self.emotion[0], "confidence": self.emotion[1]}
        if path == "/api/proactive/settings" and method == "GET":
            return {"settings": {"proactiveChatEnabled": self.master}}
        if path == "/api/proactive/settings" and method == "POST":
            self.master = bool(body.get("proactiveChatEnabled"))
            return {"success": True}
        return None


def _pushes_of(p, message_type):
    return [m for m in p._pushed if m.get("metadata", {}).get("message_type") == f"forever_companion.{message_type}"]


def _sense_plugin(plugin_factory_full, http=None, sense_cfg=None):
    p = plugin_factory_full(http=http or _SenseHttp())
    if sense_cfg:
        p._emotion_sense_cfg = dict(sense_cfg)
    return p


# ---------- recent.json 水位 diff ----------


def test_first_poll_builds_baseline_without_analysis(plugin_factory_full) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    assert run(p._poll_recent_turns(p._get_shard("YUI"), lanlan="YUI")) is None  # 首趟只建基线
    assert not http.analysis_calls


def test_new_turn_detected_and_paired(plugin_factory_full) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    shard = p._get_shard("YUI")
    run(p._poll_recent_turns(shard, lanlan="YUI"))  # 基线
    http.add_turn("今天好累", "辛苦啦，抱抱你")
    turn = run(p._poll_recent_turns(shard, lanlan="YUI"))
    assert turn == ("今天好累", "辛苦啦，抱抱你")
    # 无新轮时不重复返回
    assert run(p._poll_recent_turns(shard, lanlan="YUI")) is None


def test_poll_requests_character_scoped_recent_file(plugin_factory_full) -> None:
    """回归（运行时实测根因）：宿主 /api/memory/recent_file 要求逻辑文件名
    recent_<角色>.json；裸 recent.json 会 400 静默失败，语气感知全断。
    桩对非法文件名返回 None（模拟 urllib 400），此测试在严格桩下必须仍能取数。"""
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    shard = p._get_shard("YUI")
    assert run(p._poll_recent_turns(shard, lanlan="YUI", peek=True)) == ("你好", "你好呀")
    # 中文角色名必须 URL 编码且同样命中
    http2 = _SenseHttp(current="皖萱")
    http2.set_turns([("在吗", "在呀")])
    p2 = _sense_plugin(plugin_factory_full, http=http2)
    assert run(p2._poll_recent_turns(p2._get_shard("皖萱"), lanlan="皖萱", peek=True)) == ("在吗", "在呀")


def test_peek_does_not_advance_watermark(plugin_factory_full) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    shard = p._get_shard("YUI")
    assert run(p._poll_recent_turns(shard, lanlan="YUI", peek=True)) == ("你好", "你好呀")
    assert shard.last_turn_marker == ""  # peek 不建基线


# ---------- 门控链 ----------


def test_disabled_switch_skips_everything(plugin_factory_full) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http, sense_cfg={"enabled": False})
    shard = p._get_shard("YUI")
    run(p._maybe_tone_sense("YUI", shard))
    http.add_turn("哼", "……")
    assert run(p._maybe_tone_sense("YUI", shard)) is False
    assert not http.analysis_calls


def test_check_rate_zero_skips_screen(plugin_factory_full) -> None:
    http = _SenseHttp(emotion=("angry", 0.95))
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http, sense_cfg={"enabled": True, "check_rate": 0})
    shard = p._get_shard("YUI")
    run(p._maybe_tone_sense("YUI", shard))
    http.add_turn("你真是太过分了", "我就这样，怎么了")
    assert run(p._maybe_tone_sense("YUI", shard)) is False
    assert not http.analysis_calls  # 概率 0：分析都没发生


def test_check_rate_random_gate(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp(emotion=("angry", 0.95))
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http, sense_cfg={"enabled": True, "check_rate": 0.5})
    shard = p._get_shard("YUI")
    run(p._maybe_tone_sense("YUI", shard))
    http.add_turn("过分", "怎么了")
    monkeypatch.setattr(tm.random, "random", lambda: 0.9)  # >= 0.5 → 抽不中
    assert run(p._maybe_tone_sense("YUI", shard)) is False
    assert not http.analysis_calls
    # 抽样跳过 = 这轮不查且水位已推进；新一轮到来后再抽
    http.add_turn("第二句", "第二轮")
    monkeypatch.setattr(tm.random, "random", lambda: 0.1)  # < 0.5 → 抽中
    assert run(p._maybe_tone_sense("YUI", shard)) is True
    assert len(http.analysis_calls) == 2  # 她的回复 + 用户消息各一次独立分析


def test_min_interval_throttles_analysis(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp(emotion=("angry", 0.95))
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    shard = p._get_shard("YUI")
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))
    http.add_turn("过分", "怎么了")
    assert run(p._maybe_tone_sense("YUI", shard)) is True
    http.add_turn("第二句", "第二轮回复")
    assert run(p._maybe_tone_sense("YUI", shard)) is False  # 同一时刻被 min_interval 节流
    assert len(http.analysis_calls) == 2  # 她 + 用户各一次
    now[0] += 61
    assert run(p._maybe_tone_sense("YUI", shard)) is False  # 提醒被 10 分钟筛选节流挡住
    assert len(http.analysis_calls) == 4  # 但 min_interval 已过，分析确实执行了（又 2 次）


# ---------- 筛选模式命中矩阵 + 阶段灵敏度 ----------


def _run_screen(plugin_factory_full, p, http, tm, monkeypatch, label, conf, user="……", her="……"):
    if label is not None:
        http.emotion = (label, conf)
    shard = p._get_shard("YUI")
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))  # 基线
    http.add_turn(user, her)
    fired = run(p._maybe_tone_sense("YUI", shard))
    return fired


def test_screen_cold_label_fires_neutral_nudge(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    assert _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "angry", 0.8) is True
    nudges = _pushes_of(p, "tone_screen")
    assert len(nudges) == 1
    text = nudges[0]["parts"][0]["text"]
    assert "波动" in text and "不用勉强" in text  # 中性文案，不诱导
    assert "mood_ripple" in text and "mood_drift_bottle" in text
    assert nudges[0].get("target_lanlan") == "YUI"


def test_screen_happy_fires_positive_nudge(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    assert _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "happy", 0.9) is True
    text = _pushes_of(p, "tone_screen")[0]["parts"][0]["text"]
    assert "mood_spring_tide" in text


def test_screen_low_confidence_or_neutral_does_not_fire(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    # 固定"今天"到平稳期（锚 2026-08-01 后第 20 天）：阈值不做敏感期下移，
    # 与真实日期脱钩（真实日期落潮汐期/回升期/活跃期时阈值 0.6-0.15，用例会误判）
    monkeypatch.setattr(tm, "resolve_today", lambda *_a, **_k: date(2026, 8, 20))
    # 置信度低于阈值 0.6
    assert _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "angry", 0.5) is False
    assert not _pushes_of(p, "tone_screen")
    # neutral 永不触发筛选
    assert _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "neutral", 0.99) is False


def test_screen_nudge_throttled_10min(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp(emotion=("angry", 0.9))
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http, sense_cfg={"min_interval_sec": 1})
    shard = p._get_shard("YUI")
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))
    for i in range(3):
        http.add_turn(f"过分{i}", f"哼{i}")
        now[0] += 2
        run(p._maybe_tone_sense("YUI", shard))
    assert len(http.analysis_calls) == 6  # 分析发生了三轮（每轮她 + 用户各一次）
    assert len(_pushes_of(p, "tone_screen")) == 1  # 但提醒只递了一次（10 分钟节流）


def test_phase_sensitivity_lowers_threshold(plugin_factory_full, tm, monkeypatch) -> None:
    """潮汐期（menstrual）阈值 0.6-0.15=0.45：conf 0.5 命中；平静期（luteal）不命中。"""
    from datetime import date, timedelta

    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    today = date.today()
    # menstrual：anchor=今天 → 第 1 天
    p = _sense_plugin(plugin_factory_full, http,
                      sense_cfg={"min_interval_sec": 1, "check_rate": 1.0})
    p._tide_cfg["anchor_date"] = today.isoformat()
    assert _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "angry", 0.5) is True

    # luteal：anchor=23 天前 → 第 24 天（排卵 14±3 之后，周期 28 内）
    http2 = _SenseHttp()
    http2.set_turns([("你好", "你好呀")])
    p2 = _sense_plugin(plugin_factory_full, http2,
                       sense_cfg={"min_interval_sec": 1, "check_rate": 1.0})
    p2._tide_cfg["anchor_date"] = (today - timedelta(days=23)).isoformat()
    assert _run_screen(plugin_factory_full, p2, http2, tm, monkeypatch, "angry", 0.5) is False


def test_phase_sensitivity_switch_off(plugin_factory_full, tm, monkeypatch) -> None:
    from datetime import date

    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http, sense_cfg={
        "min_interval_sec": 1, "phase_sensitivity_enabled": False,
    })
    p._tide_cfg["anchor_date"] = date.today().isoformat()  # menstrual 也不加灵敏度
    assert _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "angry", 0.5) is False


# ---------- 校正模式趋势窗口 ----------


def _run_correction_rounds(p, http, tm, monkeypatch, rounds, label):
    http.emotion = (label, 0.9)
    shard = p._get_shard("YUI")
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))  # 基线
    fired = []
    for i in range(rounds):
        http.add_turn(f"用户{i}", f"回复{i}")
        now[0] += 61
        fired.append(run(p._maybe_tone_sense("YUI", shard)))
    return fired


def test_correction_warm_trend_nudges_rising_tide(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    run(p.set_mood(action="ebb_tide", _ctx={"lanlan_name": "YUI"}))
    fired = _run_correction_rounds(p, http, tm, monkeypatch, 2, "happy")
    assert fired == [False, True]  # 攒满 2 轮的第二次判定（0.6.8 起窗口默认 2）
    nudges = _pushes_of(p, "tone_correction")
    assert len(nudges) == 1
    assert "mood_rising_tide" in nudges[0]["parts"][0]["text"]
    assert nudges[0].get("target_lanlan") == "YUI"
    # 校正模式分析对象只含她的回复，不拼用户消息
    assert "用户" not in http.analysis_calls[-1]["body"]["text"]


def test_correction_window_resets_after_verdict(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    run(p.set_mood(action="ebb_tide", _ctx={"lanlan_name": "YUI"}))
    _run_correction_rounds(p, http, tm, monkeypatch, 2, "happy")
    assert p._get_shard("YUI").tone_window == []  # 判定后清空重攒


def test_correction_cold_trend_during_positive_mood(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    run(p.set_mood(action="spring_tide", _ctx={"lanlan_name": "YUI"}))
    fired = _run_correction_rounds(p, http, tm, monkeypatch, 2, "sad")
    assert fired[-1] is True
    text = _pushes_of(p, "tone_correction")[0]["parts"][0]["text"]
    assert "沉下来" in text and "mood_drift_bottle" in text


def test_correction_no_majority_no_nudge(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp()
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    run(p.set_mood(action="ebb_tide", _ctx={"lanlan_name": "YUI"}))
    # happy, sad, happy → 窗口满时 warm=2/3 会命中，改用 1 暖 2 冷：不命中偏暖分支
    http.emotion = ("happy", 0.9)
    shard = p._get_shard("YUI")
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))
    seq = ["happy", "sad", "angry"]
    for i, label in enumerate(seq):
        http.emotion = (label, 0.9)
        http.add_turn(f"用户{i}", f"回复{i}")
        now[0] += 61
        run(p._maybe_tone_sense("YUI", shard))
    assert not _pushes_of(p, "tone_correction")  # 重度负面中偏冷不是校正触发条件


# ---------- 降级与 CSRF ----------


def test_endpoint_error_goes_dormant(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp(emotion=None)  # 端点返回 error 字段
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    assert _run_screen(plugin_factory_full, p, http, tm, monkeypatch, None, 0.9) is False
    assert not _pushes_of(p, "tone_screen")
    assert len(http.analysis_calls) == 1  # 调用了但按降级处理


def test_csrf_token_cached_and_cleared_on_failure(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp(emotion=("angry", 0.9))
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "angry", 0.9)
    assert http.analysis_calls[0]["headers"]["X-CSRF-Token"] == "test-csrf-token"
    # 不断字面量端口，断不变式：Origin 必须等于实际拼 URL 的 base
    assert http.analysis_calls[0]["headers"]["Origin"] == p._proactive_api_base()
    assert p._csrf_token == "test-csrf-token"  # 已缓存


def test_emotion_origin_follows_custom_host_port(plugin_factory_full, tm, monkeypatch) -> None:
    """宿主跑在自定义 MAIN_SERVER_PORT 上时，Origin 跟着变（不写死 48911）。

    宿主 CSRF 守卫要 token ∧ Origin 同时成立；写死端口时只能吃它的 hostname
    降级分支（为 Docker 端口映射而写），一旦收紧就 403 静默休眠。
    """
    http = _SenseHttp(emotion=("angry", 0.9))
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http)
    p._proactive_api_base_cache = "http://127.0.0.1:55555"
    _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "angry", 0.9)
    assert http.analysis_calls, "没发出语气分析请求"
    sent = http.analysis_calls[0]["headers"]["Origin"]
    assert sent == "http://127.0.0.1:55555"
    assert sent != "http://127.0.0.1:48911"


def test_default_path_carries_no_model_override(plugin_factory_full, tm, monkeypatch) -> None:
    """默认/emotion 槽走宿主端点：请求体不携带 model/api_key 覆盖（由宿主情感槽配置全权决定）。"""
    http = _SenseHttp(emotion=("happy", 0.9))
    http.set_turns([("你好", "你好呀")])
    for slot in ("", "emotion"):
        http.analysis_calls.clear()
        p = _sense_plugin(plugin_factory_full, http, sense_cfg={"slot": slot})
        _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "happy", 0.9)
        body = http.analysis_calls[0]["body"]
        assert "model" not in body
        assert "api_key" not in body
        # 不带 lanlan_name：纯静默分析，不触发宿主把结果推给前端改头像表情
        assert "lanlan_name" not in body


# ---------- 调试入口 ----------


def test_debug_emotion_sense_peek_and_details(plugin_factory_full, tm, monkeypatch) -> None:
    http = _SenseHttp(emotion=("angry", 0.77))
    http.set_turns([("你冷落我", "谁让你先不理我")])
    p = _sense_plugin(plugin_factory_full, http)
    # 固定"今天"到平稳期：effective_threshold 恒为 0.6（敏感期会下移到 0.45），与真实日期脱钩
    monkeypatch.setattr(tm, "resolve_today", lambda *_a, **_k: date(2026, 8, 20))
    res = run(p._debug_emotion_sense(lanlan="YUI"))
    assert res.value["analyzed"] is True
    assert res.value["emotion"] == "angry"
    assert res.value["confidence"] == 0.77
    assert res.value["mode"] == "screen"
    assert res.value["would_screen_nudge"] is True
    assert res.value["effective_threshold"] == 0.6
    assert "你冷落我" not in res.value["her_reply_preview"]  # preview 只含她的回复
    # peek 语义：不推进水位
    assert p._get_shard("YUI").last_turn_marker == ""


def test_debug_emotion_sense_no_turns(plugin_factory_full) -> None:
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    res = run(p._debug_emotion_sense(lanlan="YUI"))
    assert res.value["analyzed"] is False


# ---------- update_settings 新字段 ----------


def test_update_settings_emotion_sense_fields(plugin_factory_full) -> None:
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    res = run(p.update_settings(
        emotion_sense_enabled=False,
        tone_check_rate=0.5,
        tone_phase_sensitivity_enabled=False,
        tone_phase_sensitivity=0.25,
        tone_slot="conversation",
        _ctx={"lanlan_name": "YUI"},
    ))
    assert getattr(res, "error", None) is None
    cfg = p._emotion_sense_cfg
    assert cfg["enabled"] is False
    assert cfg["check_rate"] == 0.5
    assert cfg["phase_sensitivity_enabled"] is False
    assert cfg["phase_sensitivity"] == 0.25
    assert cfg["slot"] == "conversation"
    # Store 覆盖层持久化
    assert p.store.data["settings"]["emotion_sense"]["check_rate"] == 0.5
    assert p.store.data["settings"]["emotion_sense"]["slot"] == "conversation"
    # snapshot 回显槽位选择
    assert p._settings_snapshot()["tone_slot"] == "conversation"


def test_update_settings_tone_slot_validated(plugin_factory_full) -> None:
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    res = run(p.update_settings(tone_slot="tts", _ctx={"lanlan_name": "YUI"}))
    assert getattr(res, "error", None) is not None  # tts 不是文本槽位，拒绝
    assert "slot" not in p._emotion_sense_cfg
    # 空串 = 回到宿主默认情感模型槽
    run(p.update_settings(tone_slot="vision", _ctx={"lanlan_name": "YUI"}))
    res = run(p.update_settings(tone_slot="", _ctx={"lanlan_name": "YUI"}))
    assert getattr(res, "error", None) is None
    assert p._emotion_sense_cfg["slot"] == ""


# ---------- 直连槽位：_resolve_tone_slot ----------

# 一份"装配好"的宿主 core_config：assist=qwen 管理簿 + 已保存 URL，槽位默认 follow_assist
_CORE_CFG = {
    "coreApi": "qwen",
    "coreApiKey": "sk-core",
    "assistApi": "qwen",
    "assistApiKeyQwen": "sk-assist-qwen",
    "resolvedProviderUrls": {
        "core": {"qwen": "https://core.example.com/v1"},
        "assist": {"qwen": "https://assist.example.com/v1", "deepseek": "https://ds.example.com/v1"},
    },
    "conversationModelProvider": "follow_assist",
    "conversationModelId": "qwen-conv",
    "emotionModelProvider": "follow_assist",
    "emotionModelId": "qwen-emotion",
    "summaryModelProvider": "follow_conversation",
    "summaryModelId": "qwen-sum",
}


def test_resolve_follow_assist_default_chain(plugin_factory_full) -> None:
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    resolved = p._resolve_tone_slot(dict(_CORE_CFG), "emotion")
    assert resolved == {
        "model": "qwen-emotion",
        "api_key": "sk-assist-qwen",
        "base_url": "https://assist.example.com/v1",
    }


def test_resolve_assist_default_when_assist_api_missing(plugin_factory_full) -> None:
    """assistApi 缺失：coreApi!='free' → 默认 qwen；coreApi=='free' → free（不可直连）。"""
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    cfg = {k: v for k, v in _CORE_CFG.items() if k != "assistApi"}
    assert p._resolve_tone_slot(cfg, "emotion")["api_key"] == "sk-assist-qwen"
    cfg["coreApi"] = "free"
    assert p._resolve_tone_slot(cfg, "emotion") is None  # free 是宿主内部代理，直连会 401


def test_resolve_follow_core(plugin_factory_full) -> None:
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    cfg = dict(_CORE_CFG, conversationModelProvider="follow_core")
    resolved = p._resolve_tone_slot(cfg, "conversation")
    assert resolved == {
        "model": "qwen-conv",  # 模型名仍取槽位自己的 ModelId
        "api_key": "sk-core",
        "base_url": "https://core.example.com/v1",
    }
    # coreApi=free → 不可直连
    cfg_free = dict(cfg, coreApi="free")
    assert p._resolve_tone_slot(cfg_free, "conversation") is None


def test_resolve_follow_conversation_recursion(plugin_factory_full) -> None:
    """summary → follow_conversation → conversation 的 custom 端点；自环防死循环。"""
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    cfg = dict(
        _CORE_CFG,
        conversationModelProvider="custom",
        conversationModelUrl="http://127.0.0.1:11434/v1",
        conversationModelId="local-llm",
        conversationModelApiKey="",
    )
    resolved = p._resolve_tone_slot(cfg, "summary")
    assert resolved == {
        "model": "local-llm",
        "api_key": "",  # custom 无管理簿，空 key 允许（本地端点常无鉴权）
        "base_url": "http://127.0.0.1:11434/v1",
    }
    # 自环：conversation follow 自己 → None（防环）
    cfg_loop = dict(_CORE_CFG, conversationModelProvider="follow_conversation")
    assert p._resolve_tone_slot(cfg_loop, "conversation") is None


def test_resolve_named_provider_key_fallback(plugin_factory_full) -> None:
    """具名 provider 槽位：ModelApiKey 为空时回落该 provider 的管理簿 key。"""
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    cfg = dict(
        _CORE_CFG,
        assistApiKeyDeepseek="sk-book-ds",
        visionModelProvider="deepseek",
        visionModelUrl="https://ds.example.com/v1",
        visionModelId="deepseek-chat",
        visionModelApiKey="",
    )
    resolved = p._resolve_tone_slot(cfg, "vision")
    assert resolved["api_key"] == "sk-book-ds"
    # 槽位自带 key 优先于管理簿
    cfg["visionModelApiKey"] = "sk-slot"
    assert p._resolve_tone_slot(cfg, "vision")["api_key"] == "sk-slot"


def test_resolve_missing_model_or_url_degrades(plugin_factory_full) -> None:
    """custom 槽位缺 URL/模型 → 回落 assist；assist 链上模型为空 → None。"""
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    cfg = dict(
        _CORE_CFG,
        visionModelProvider="custom",
        visionModelUrl="",
        visionModelId="",
    )
    resolved = p._resolve_tone_slot(cfg, "vision")  # 回落 assist，但 visionModelId 也是空
    assert resolved is None
    cfg["visionModelId"] = "qwen-vision"
    resolved = p._resolve_tone_slot(cfg, "vision")  # 回落 assist 成功
    assert resolved == {
        "model": "qwen-vision",
        "api_key": "sk-assist-qwen",
        "base_url": "https://assist.example.com/v1",
    }
    # 未知槽位 / 未知 provider 管理簿 → None
    assert p._resolve_tone_slot(cfg, "tts") is None
    cfg2 = dict(cfg, assistApi="some_unknown_provider", visionModelId="x")
    cfg2["visionModelProvider"] = "follow_assist"
    assert p._resolve_tone_slot(cfg2, "vision") is None


# ---------- 直连槽位：_load_core_config ----------


def test_load_core_config_reads_and_caches(plugin_factory_full, tmp_path, monkeypatch) -> None:
    import json as _json

    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    cfg_file = tmp_path / "core_config.json"
    cfg_file.write_text(_json.dumps({"coreApi": "qwen"}), encoding="utf-8")
    monkeypatch.setattr(p, "_core_config_path", lambda: cfg_file)
    assert p._load_core_config()["coreApi"] == "qwen"
    # 5 秒缓存：改文件后不立即反映
    cfg_file.write_text(_json.dumps({"coreApi": "glm"}), encoding="utf-8")
    assert p._load_core_config()["coreApi"] == "qwen"


def test_load_core_config_bad_file_degrades(plugin_factory_full, tmp_path, monkeypatch) -> None:
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    missing = tmp_path / "nope" / "core_config.json"
    monkeypatch.setattr(p, "_core_config_path", lambda: missing)
    assert p._load_core_config() == {}
    p2 = _sense_plugin(plugin_factory_full, _SenseHttp())
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(p2, "_core_config_path", lambda: bad)
    assert p2._load_core_config() == {}


# ---------- 直连槽位：回复解析容错 ----------


def test_parse_tone_result_tolerances(plugin_factory_full) -> None:
    p = _sense_plugin(plugin_factory_full, _SenseHttp())
    # ```json 围栏
    assert p._parse_tone_result('```json\n{"emotion": "sad", "confidence": 0.8}\n```') == ("sad", 0.8)
    # 中文标签归一化为英文
    assert p._parse_tone_result('{"emotion": "难过", "confidence": 0.7}') == ("sad", 0.7)
    assert p._parse_tone_result('{"emotion": "平静"}') == ("neutral", 0.65)  # 缺 confidence → 0.65
    # 非法 confidence / 非法 JSON / 不识别标签 → None
    assert p._parse_tone_result('{"emotion": "happy", "confidence": "high"}') is None
    assert p._parse_tone_result("not json at all") is None
    assert p._parse_tone_result('{"emotion": "euphoric", "confidence": 0.9}') is None


# ---------- 直连槽位：全链路 ----------


def test_direct_slot_full_path(plugin_factory_full, tm, monkeypatch) -> None:
    """slot=conversation：不走宿主端点，直连解析出的端点；心情提醒按解析结果施加。"""
    http = _SenseHttp(emotion=("angry", 0.9))  # 宿主端点桩不应被调用
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http, sense_cfg={"slot": "conversation"})
    monkeypatch.setattr(p, "_load_core_config", lambda: dict(_CORE_CFG))
    post_calls = []

    def _fake_post(base_url, api_key, model, prompt):
        post_calls.append({"base_url": base_url, "api_key": api_key, "model": model, "prompt": prompt})
        return '{"emotion": "angry", "confidence": 0.9}'

    monkeypatch.setattr(p, "_post_chat_completion", _fake_post)
    fired = _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "angry", 0.9)
    assert fired is True
    assert not http.analysis_calls  # 宿主 /api/emotion/analysis 未被调用
    assert len(post_calls) == 2  # 她的回复 + 用户消息各一次
    call = post_calls[0]
    assert call["base_url"] == "https://assist.example.com/v1"
    assert call["api_key"] == "sk-assist-qwen"
    assert call["model"] == "qwen-conv"
    assert "……" in call["prompt"]  # 输入文本拼进了 prompt
    assert len(_pushes_of(p, "tone_screen")) == 1


def test_direct_slot_unresolved_degrades_silently(plugin_factory_full, tm, monkeypatch) -> None:
    """槽位解析不出端点（free/缺配置）→ 返回 None，不递提醒也不碰宿主端点。"""
    http = _SenseHttp(emotion=("angry", 0.9))
    http.set_turns([("你好", "你好呀")])
    p = _sense_plugin(plugin_factory_full, http, sense_cfg={"slot": "conversation"})
    monkeypatch.setattr(p, "_load_core_config", lambda: {"coreApi": "free"})
    fired = _run_screen(plugin_factory_full, p, http, tm, monkeypatch, "angry", 0.9)
    assert fired is False
    assert not http.analysis_calls
    assert not _pushes_of(p, "tone_screen")


def test_debug_emotion_sense_reports_slot(plugin_factory_full, monkeypatch) -> None:
    """调试入口回显 slot；直连槽位附脱敏的 model/base_url，永不输出 key。"""
    http = _SenseHttp(emotion=("angry", 0.77))
    http.set_turns([("你冷落我", "谁让你先不理我")])
    p = _sense_plugin(plugin_factory_full, http)
    res = run(p._debug_emotion_sense(lanlan="YUI"))
    assert res.value["slot"] == ""

    p2 = _sense_plugin(plugin_factory_full, http, sense_cfg={"slot": "conversation"})
    monkeypatch.setattr(p2, "_load_core_config", lambda: dict(_CORE_CFG))
    monkeypatch.setattr(
        p2, "_post_chat_completion",
        lambda *args: '{"emotion": "angry", "confidence": 0.77}',
    )
    res2 = run(p2._debug_emotion_sense(lanlan="YUI"))
    assert res2.value["slot"] == "conversation"
    assert res2.value["analyzed"] is True
    assert res2.value["resolved_model"] == "qwen-conv"
    assert res2.value["resolved_base_url"] == "https://assist.example.com/…"
    assert "sk-assist-qwen" not in str(res2.value)
