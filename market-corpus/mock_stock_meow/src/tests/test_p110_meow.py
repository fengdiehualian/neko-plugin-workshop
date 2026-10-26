"""P11.0 守卫：猫娘外置交互引导（主动搭话 + 呼叫猫娘 + 兜底弹窗）。

这一版解决的是**外置猫娘（桌宠）一直很安静**这件事。核心结论先钉死：

1. **宿主支持插件让猫娘主动开口**
   —— 官方 ``push_message`` v2 有两个**正交**的轴：
      * ``visibility``：插件的 parts 渲染到哪（``["chat"]`` / ``["hud"]`` / 空）；
      * ``ai_behavior``：``"respond"``（**喂给 LLM 并立刻触发一个 AI 回合**，
        AI 的回复作为猫娘气泡进聊天框）/ ``"read"``（只进上下文）/
        ``"blind"``（完全不喂 LLM）。

   官方 schema 自己的映射表写得很清楚：``delivery="proactive"`` 等价于
   ``([], "respond")`` —— 也就是说"主动搭话"是**官方支持**的既有能力，
   不需要"面板模拟"。

2. **提示词不是台词**
   —— ``respond`` 会让猫娘用**自己的语气**生成回复，所以 text 写的是
   "请提醒玩家点【玩法说明】"这类**指令**；占位符用 ``{MASTER_NAME}``，
   **不许**硬编码"主人"/"用户"（官方"Writing role-aware text"）。

3. **既有推送语义不许被改**
   —— 卡片 / 新闻推送本来就是 ``blind``（AI 看不见，否则会泄题 —— P4 §11），
   P11 是一条**独立的、显式的**新通道，不许把 ``blind`` 改成 ``respond``。

4. **节流闸门必须有**
   —— 猫娘开口会打断玩家。每一次主动搭话都要先过闸门（全局一次 / 每局一次 /
   冷却窗口），绝不允许刷屏。

5. **红线继续有效**
   —— 引擎层与模式层零改动、危险 CSS 继续禁用、i18n 键不许裸奔、
   推送失败**绝不能**拖垮游戏。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_INIT = _PLUGIN_ROOT / "__init__.py"
_MEOW = _PLUGIN_ROOT / "meow.py"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_TOML = _PLUGIN_ROOT / "plugin.toml"


# ---------------------------------------------------------------- 读文件的家伙


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _meow_text() -> str:
    return _MEOW.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    kept: list[str] = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped.startswith("//"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _panel_code() -> str:
    source = _strip_comments(_panel_text())
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        body, _, tail = rest.partition("`")
        source = head + "\n/* <RRM_CSS> */\n" + tail
    return source


def _panel_css() -> str:
    source = _panel_text()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    # CSS 是一整块反引号模板字符串，它的**结束**反引号独占一行。
    # 用"独占一行"来找结尾 —— 注释里混进来的反引号（哪怕在行中间）骗不过它。
    end = rest.find("\n`\n")
    assert end > 0, "找不到 RRM_CSS 的结束反引号（它必须独占一行）"
    body = rest[:end]
    # 块内**只允许**结束那一个反引号；多出来的就说明注释里混了反引号 ⇒
    # 模板字符串被截断 ⇒ 面板白屏。
    assert "`" not in body, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    return body


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_FILE.read_text(encoding="utf-8"))


def _method_body(source: str, name: str) -> str:
    """截出某个方法/函数的函数体（到下一个同级定义/注释块为止）。"""
    for marker in (f"    def {name}(", f"    async def {name}("):
        if marker in source:
            break
    else:
        raise AssertionError(f"源码里找不到 {name}")
    _, _, tail = source.partition(marker)
    kept: list[str] = []
    for line in tail.split("\n")[1:]:
        if (
            line.startswith("    def ")
            or line.startswith("    @")
            or line.startswith("    # ---")
            or line.startswith("    async def ")
        ):
            break
        kept.append(line)
    return "\n".join(kept)


# ══════════════════════════════════════════════════════════════ ① 调研结论钉死
#
# 官方文档里**有**"插件主动唤起猫娘说话"的机制：``push_message`` v2 的
# ``ai_behavior="respond"``。下面这几条把结论钉死，防止有人误以为宿主不支持
# 而白折腾，或者反过来把"卡片推送"也改成 respond 造成泄题。


def test_push_message_v2_axes_are_used() -> None:
    """主动搭话必须走 v2 的两个正交轴：``visibility=[]`` + ``ai_behavior="respond"``。"""
    body = _method_body(_init_text(), "_push_proactive")
    assert 'ai_behavior="respond"' in body, "主动搭话必须用 respond（这才会触发 AI 回合）"
    assert "visibility=[]" in body, "visibility 必须是空列表（玩家只看猫娘的气泡）"
    assert 'source=PLUGIN_ID' in body
    assert "parts=" in body and '"type": "text"' in body


def test_legacy_v1_push_fields_are_not_used() -> None:
    """v1 的 ``delivery`` / ``reply`` / ``content`` / ``message_type`` 一律不许再用。"""
    body = _method_body(_init_text(), "_push_proactive")
    for token in ("delivery=", "reply=", "content=", "message_type="):
        assert token not in body, f"主动搭话里不许再用已废弃的 v1 字段 {token}"


def test_existing_cards_stay_blind() -> None:
    """P5 契约：卡片 / 新闻推送**保持** ``blind``（AI 看不见，否则会泄题）。"""
    init = _init_text()
    push_card = _method_body(init, "_push_card")
    assert 'ai_behavior="blind"' in push_card, "卡片推送必须保持 blind"
    assert 'ai_behavior="respond"' not in push_card
    # 整个插件里，"respond" 的**真实代码行**只许出现在**外置猫娘开口**的
    # 那两条通道上：P11 的主动搭话（``_push_proactive``）与 P12 的剧情
    # （``_push_story_voice``）。别的地方一律不许 —— 卡片/新闻改成 respond
    # 就等于把局中情报喂给 LLM（P5 §11 的泄题防线就破了）。
    # （注释里提到它是正常的 —— 注释是给人看的。）
    responds = re.findall(r'^\s+ai_behavior="respond",\s*$', init, re.M)
    assert len(responds) == 2, (
        "respond 只许用在两条外置开口通道上（主动搭话 + 剧情），"
        f"实际 {len(responds)} 处"
    )
    for method in ("_push_proactive", "_push_story_voice"):
        body = _method_body(init, method)
        assert 'ai_behavior="respond"' in body, f"{method} 必须是 respond 通道"


def test_proactive_bridge_maps_respond_to_proactive() -> None:
    """宿主的映射：``respond`` → ``delivery_mode="proactive"``（触发 AI 回合）。"""
    bridge = (
        _PLUGIN_ROOT.parents[3]
        / "统一开发文档"
        / "N.E.K.O-main"
        / "plugin"
        / "server"
        / "messaging"
        / "proactive_bridge.py"
    )
    if not bridge.exists():  # pragma: no cover - 只读源码树不在时跳过
        return
    text = bridge.read_text(encoding="utf-8")
    assert '"respond"' in text
    assert '"proactive"' in text


# ══════════════════════════════════════════════════════════════ ② 占位符纪律


def test_prompts_use_master_name_placeholder() -> None:
    """提示词必须用 ``{MASTER_NAME}`` 占位符（宿主按 session 展开）。"""
    from plugins.mock_stock_meow import meow

    assert "{MASTER_NAME}" in meow.GREET_PROMPT
    assert "{MASTER_NAME}" in meow.NAG_PROMPT
    for key, prompt in meow.CALL_PROMPTS.items():
        assert "{MASTER_NAME}" in prompt, f"{key} 的提示词里没有 {{MASTER_NAME}}"


def test_prompts_do_not_hardcode_the_master() -> None:
    """**不许**硬编码"主人"/"用户"（官方"Writing role-aware text"）。"""
    from plugins.mock_stock_meow import meow

    texts = [meow.GREET_PROMPT, meow.NAG_PROMPT, *meow.CALL_PROMPTS.values()]
    for banned in ("主人", "用户", "master", "owner"):
        for text in texts:
            assert banned not in text, f"提示词里不许硬编码「{banned}」"


def test_plugin_never_expands_placeholders_itself() -> None:
    """插件**不许**自己展开占位符（可见性过滤在宿主端，插件拿不到名字）。"""
    from plugins.mock_stock_meow import meow

    # 主动搭话通道里不许有任何"自己替换名字"的写法。
    body = _method_body(_init_text(), "_push_proactive")
    for banned in (".replace(", ".format(", "{MASTER_NAME}"):
        assert banned not in body, f"主动搭话里不许自己展开占位符（{banned}）"
    # 话术模块里也不许。
    assert ".replace(" not in _meow_text()
    assert ".format(" not in _meow_text()
    assert meow.MASTER == "{MASTER_NAME}"


def test_fallback_lines_have_no_placeholders() -> None:
    """兜底台词是**字面中文**（面板不展开占位符）。"""
    from plugins.mock_stock_meow import meow

    for key, line in meow.FALLBACK_LINES.items():
        assert "{" not in line, f"{key} 的兜底台词里不该有占位符"
        assert "}" not in line, f"{key} 的兜底台词里不该有占位符"
        assert line.strip(), f"{key} 的兜底台词是空的"


# ══════════════════════════════════════════════════════════════ ③ 三个场景都在


def test_first_load_greeting_entry_exists() -> None:
    """场景一：首次加载引导 —— ``meow_greet`` 入口 + 全局一次的闸门。"""
    init = _init_text()
    assert 'id="meow_greet"' in init
    assert "async def meow_greet(" in init
    assert "_SETTING_MEOW_GREETED" in init
    body = _method_body(init, "meow_greet")
    assert "_meow_greeted" in body, "打招呼必须有闸门（不许每次都打扰玩家）"
    assert "GREET_PROMPT" in body


def test_panel_calls_greet_on_mount() -> None:
    """面板挂载时调一次 ``meow_greet``（一次挂载只试一次）。"""
    code = _panel_code()
    assert 'props.api.call("meow_greet"' in code
    assert "meowGreetTried" in code, "必须有「只试一次」的 ref（面板每 3 秒轮询）"


def test_call_meow_entry_and_button_exist() -> None:
    """场景二：【呼叫猫娘】—— 入口 + 面板按钮 + 冷却。"""
    init = _init_text()
    assert 'id="meow_call"' in init
    assert "async def meow_call(" in init
    body = _method_body(init, "meow_call")
    assert "MEOW_CALL_COOLDOWN_SECONDS" in body, "必须有防连点冷却"
    assert "call_prompt(" in body

    code = _panel_code()
    assert 'callAction("meow_call"' in code
    assert "rrm-meow-call" in code, "标题栏里必须有【呼叫猫娘】按钮"
    assert 't("panel.meow_call")' in code


def test_nag_is_wired_to_both_advance_entries() -> None:
    """场景三：新手期唠叨 —— ``skip_day`` 与 ``advance_tick`` 都得挂上。"""
    init = _init_text()
    assert "_maybe_meow_nag" in init
    skip_body = _method_body(init, "skip_day")
    advance_body = _method_body(init, "advance_tick")
    assert "_maybe_meow_nag" in skip_body, "【跳过本日】必须能触发唠叨"
    assert "_maybe_meow_nag" in advance_body, "【推进一拍】必须能触发唠叨"
    # 两个入口的返回体里都要带上 meow 块（面板据此弹兜底弹窗）。
    assert '"meow": meow' in skip_body
    assert '"meow": meow' in advance_body


def test_nag_requires_day1_and_zero_trades() -> None:
    """唠叨的判据：第 1 天 **且** 成交笔数为 0。"""
    init = _init_text()
    body = _method_body(init, "_maybe_meow_nag")
    assert '!= 1' in body, "必须是第 1 天才唠叨"
    assert "trade_count" in body, "必须判成交笔数"
    assert "trade_count) > 0" in body, "买了就不该再唠叨"
    assert "NAG_PROMPT" in body


def test_nag_fires_at_most_once_per_player() -> None:
    """每一局最多唠叨一次（记 player_id）。"""
    init = _init_text()
    assert "_SETTING_MEOW_NAG_PLAYER" in init
    body = _method_body(init, "_maybe_meow_nag")
    assert "_meow_nag_player" in body


# ══════════════════════════════════════════════════════════════ ④ 节流闸门


def test_meow_settings_keys_are_persisted() -> None:
    """三个闸门键都落在 ``settings`` 表里（插件自己的偏好，与宿主配置无关）。"""
    from plugins.mock_stock_meow import (
        _SETTING_MEOW_GREETED,
        _SETTING_MEOW_LAST_AT,
        _SETTING_MEOW_NAG_PLAYER,
    )

    assert _SETTING_MEOW_GREETED == "meow_greeted"
    assert _SETTING_MEOW_NAG_PLAYER == "meow_nag_player"
    assert _SETTING_MEOW_LAST_AT == "meow_last_at"


def test_cooldowns_are_positive() -> None:
    """冷却常量必须是正数（0 等于没节流）。"""
    from plugins.mock_stock_meow import meow

    assert meow.MEOW_CALL_COOLDOWN_SECONDS > 0
    assert meow.MEOW_AUTO_COOLDOWN_SECONDS > 0


def test_meow_block_is_pure_memory() -> None:
    """面板载荷的 ``meow`` 块**不能**查库、不能问宿主。"""
    body = _method_body(_init_text(), "_meow_block")
    assert "await" not in body, "meow 块必须是纯内存"
    assert "self._repo" not in body
    assert "self.ctx" not in body


# ══════════════════════════════════════════════════════════════ ⑤ 兜底弹窗


def test_fallback_modal_exists_in_panel() -> None:
    """兜底：面板里必须有"拟态外置对话"弹窗。"""
    code = _panel_code()
    assert "function MeowChatModal(" in code
    assert "rrm-meow-thread" in code
    assert "rrm-meow-bubble" in code


def test_fallback_is_driven_by_sequence() -> None:
    """兜底弹窗由 ``fallback_seq`` 驱动（序号变了 ⇒ 有新消息）。"""
    code = _panel_code()
    assert "fallback_seq" in code
    assert "meowFallbackSeq" in code
    assert "setMeowFallback" in code


def test_fallback_notice_says_it_is_simulated() -> None:
    """兜底弹窗必须写明"这是模拟"（不能让玩家以为桌宠真的没说话）。"""
    i18n = _i18n()
    assert "meow.fallback_push" in i18n
    assert "meow.fallback_generic" in i18n
    assert "面板模拟" in i18n["meow.fallback_push"]
    assert "meow.modal_title" in i18n
    assert "meow.ack" in i18n


def test_fallback_line_is_carried_by_the_payload() -> None:
    """兜底台词要进面板载荷（``meow.fallback_line``）。"""
    init = _init_text()
    assert '"fallback_line"' in init
    assert '"fallback_seq"' in init
    assert "_record_meow_fallback" in init


# ══════════════════════════════════════════════════════════════ ⑥ 真跑一遍
#
# 上面都是"源码长得对"，下面是"跑起来对"。用假 ctx 实跑插件的主动搭话路径。


class _Recorder:
    """记录 ``push_message`` 调用参数的假宿主。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.boom = boom

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.boom:
            raise RuntimeError("宿主不支持主动搭话")


class _FakePlayer:
    def __init__(self, day: int, trade_count: int, player_id: str = "p_test") -> None:
        self.day = day
        self.trade_count = trade_count
        self.id = player_id


class _FakeSession:
    def __init__(self, player: _FakePlayer) -> None:
        self.player = player


def _bind_push(plugin: Any, recorder: _Recorder) -> None:
    """把假 ``push_message`` 绑到插件 ctx 的宿主侧（SDK 会转发过去）。"""
    plugin.ctx._host_ctx.push_message = recorder


def test_push_proactive_sends_respond_and_empty_visibility(plugin: Any) -> None:
    """实跑：``_push_proactive`` 真的按 v2 双轴发出去。"""
    recorder = _Recorder()
    _bind_push(plugin, recorder)

    assert run(plugin._push_proactive("提示词", reason="test")) is True
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["ai_behavior"] == "respond"
    assert call["visibility"] == []
    assert call["parts"] == [{"type": "text", "text": "提示词"}]
    assert call["source"] == "mock_stock_meow"


def test_push_proactive_survives_a_broken_host(plugin: Any) -> None:
    """宿主炸了也只能返回 False，**绝不能**把异常抛出去拖垮游戏。"""
    _bind_push(plugin, _Recorder(boom=True))
    assert run(plugin._push_proactive("提示词", reason="test")) is False


def test_push_proactive_without_host_support(plugin: Any) -> None:
    """宿主没有 ``push_message``（老版本）时优雅降级。"""
    host = plugin.ctx._host_ctx
    if hasattr(host, "push_message"):
        del host.push_message
    assert run(plugin._push_proactive("提示词", reason="test")) is False


def test_greet_only_speaks_once(plugin: Any) -> None:
    """首次引导：全局只说一次（第二遍不再打扰玩家）。"""
    recorder = _Recorder()
    _bind_push(plugin, recorder)

    first = run(plugin.meow_greet())
    assert first.is_ok()
    assert first.value["spoken"] is True
    assert len(recorder.calls) == 1

    second = run(plugin.meow_greet())
    assert second.is_ok()
    assert second.value["spoken"] is False
    assert second.value["reason"] == "already_greeted"
    assert len(recorder.calls) == 1, "不该重复打扰玩家"


def test_greet_survives_a_broken_host_and_falls_back(plugin: Any) -> None:
    """宿主接不住 ⇒ 记一条兜底弹窗（面板弹拟态对话）。"""
    _bind_push(plugin, _Recorder(boom=True))
    result = run(plugin.meow_greet())
    assert result.is_ok()
    assert result.value["spoken"] is False
    assert result.value["reason"] == "push_failed"
    assert result.value["line"], "兜底台词不许是空的"
    assert plugin._meow_fallback_seq == 1


def test_call_meow_rotates_topics(plugin: Any) -> None:
    """【呼叫猫娘】轮换话题（每次都是同一句很快就腻）。"""
    recorder = _Recorder()
    _bind_push(plugin, recorder)

    topics: list[str] = []
    for _ in range(4):
        result = run(plugin.meow_call())
        assert result.is_ok()
        topics.append(result.value["topic"])
    assert len(set(topics)) > 1, "话题不许一直是同一个"


def test_call_meow_respects_the_cooldown(plugin: Any) -> None:
    """连点会被冷却挡住（猫娘开口会打断玩家）。"""
    recorder = _Recorder()
    _bind_push(plugin, recorder)

    first = run(plugin.meow_call())
    assert first.value["spoken"] is True
    second = run(plugin.meow_call())
    assert second.value["spoken"] is False
    assert second.value["reason"] == "cooldown"
    assert len(recorder.calls) == 1, "冷却期内不该再推一次"


def test_nag_fires_on_day1_with_zero_trades(plugin: Any) -> None:
    """第 1 天零成交 → 唠叨一次。"""
    recorder = _Recorder()
    _bind_push(plugin, recorder)

    result = run(plugin._maybe_meow_nag(_FakeSession(_FakePlayer(day=1, trade_count=0))))
    assert result is not None
    assert result["spoken"] is True
    assert len(recorder.calls) == 1

    # 同一局不再唠叨。
    again = run(plugin._maybe_meow_nag(_FakeSession(_FakePlayer(day=1, trade_count=0))))
    assert again is None


def test_nag_does_not_fire_after_a_trade(plugin: Any) -> None:
    """买了就不该再唠叨。"""
    recorder = _Recorder()
    _bind_push(plugin, recorder)

    result = run(plugin._maybe_meow_nag(_FakeSession(_FakePlayer(day=1, trade_count=1))))
    assert result is None
    assert recorder.calls == []


def test_nag_does_not_fire_after_day1(plugin: Any) -> None:
    """第 2 天起不再唠叨（新手期已过）。"""
    recorder = _Recorder()
    _bind_push(plugin, recorder)

    result = run(plugin._maybe_meow_nag(_FakeSession(_FakePlayer(day=2, trade_count=0))))
    assert result is None
    assert recorder.calls == []


def test_meow_block_reports_the_state(plugin: Any) -> None:
    """``meow`` 块：打招呼状态 + 冷却 + 兜底序号。"""
    block = plugin._meow_block()
    assert block["greeted"] is False
    assert block["can_call"] is True
    assert block["fallback_seq"] == 0


# ══════════════════════════════════════════════════════════════ ⑦ 红线


def test_engine_and_modes_are_untouched() -> None:
    """引擎层与模式层的**代码**零改动。

    P15.0 起判定从「文件名 diff」升级为「**代码指纹**」：
    注释与 docstring 可以自由改（清理版权字眼需要），
    但任何**可执行代码**的改动都会被抓出来。
    """
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"


def test_no_new_game_numbers() -> None:
    """主动搭话**不参与**任何游戏规则（提示词里不许出现游戏数值）。"""
    from plugins.mock_stock_meow import meow

    for text in [meow.GREET_PROMPT, meow.NAG_PROMPT, *meow.CALL_PROMPTS.values()]:
        # 提示词里不许出现 heat / seed / 反转日 / 新闻真假（局中不泄露）。
        for banned in ("heat", "seed", "反转", "真假", "surge", "crash"):
            assert banned not in text.lower(), f"提示词里不许泄露局中情报：{banned}"


def test_dangerous_css_is_still_banned() -> None:
    """危险 CSS 继续禁用。"""
    css = _panel_css()
    for token in (
        "position: absolute",
        "position: fixed",
        "z-index",
        "filter:",
        "clip-path",
        "inset:",
        "opacity",
        "@keyframes",
        "radial-gradient",
    ):
        assert token not in css, f"注入的 CSS 里不许再出现 {token}"
    for match in re.finditer(r"(?<![\w-])content\s*:", css):
        raise AssertionError(f"注入的 CSS 里不许再出现 content: 属性（{match.group(0)}）")


def test_dangerous_inline_styles_are_still_banned() -> None:
    """面板代码里不许出现危险的内联样式。"""
    code = _panel_code()
    for token in (
        'position: "absolute"',
        'position: "fixed"',
        "zIndex",
        "filter:",
        "animation:",
        "clipPath",
    ):
        assert token not in code, f"面板代码里不许再出现 {token}"

    # P14.0：内联的**线性渐变**解禁（档位卡 / 资金水位条要用它）。
    # 但角度仍然锁死：0.8.0 那条 X 线条 = 绝对定位 + **对角**渐变，
    # 定位已经被禁死，这里再禁一次对角 ⇒ 三重保险，X 线条不可能复活。
    for match in re.finditer(r"([a-z]+)-gradient\(([^)]*)\)", code):
        kind, body = match.group(1), match.group(2)
        assert kind == "linear", f"只用 linear-gradient，发现 {kind}-gradient"
        angle = re.search(r"(\d+(?:\.\d+)?)deg", body)
        if angle:
            assert float(angle.group(1)) in (0.0, 90.0, 180.0), (
                f"内联渐变只允许纵向 / 横向（0/90/180deg），发现 {angle.group(1)}deg"
            )


def test_css_has_no_backtick() -> None:
    """RRM_CSS 里绝不能有反引号（会当场截断，面板白屏）。"""
    css = _panel_css()
    assert "`" not in css


def test_new_strings_are_in_the_dictionary() -> None:
    """P11 新增的文案必须进 i18n 词典。"""
    i18n = _i18n()
    for key in (
        "panel.meow_call",
        "panel.meow_called",
        "panel.meow_fallback_toast",
        "meow.modal_title",
        "meow.ack",
        "meow.fallback_empty",
        "meow.fallback_push",
        "meow.fallback_generic",
    ):
        assert key in i18n, f"i18n 里缺 {key}"
        assert i18n[key].strip(), f"{key} 是空的"


def test_panel_uses_only_dictionary_keys() -> None:
    """面板里用到的 ``t()`` 键都必须在词典里。"""
    i18n = _i18n()
    used = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", _panel_code()))
    missing = sorted(key for key in used if key not in i18n)
    assert not missing, f"i18n 缺少面板用到的键：{missing}"


def test_version_is_0_9_5() -> None:
    """版本号 = 0.10.5。"""
    assert 'version = "0.10.5"' in _TOML.read_text(encoding="utf-8")
    assert 'PLUGIN_VERSION = "0.10.5"' in _init_text()
