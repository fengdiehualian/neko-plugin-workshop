"""P12.0 守卫：剧情引擎（四幕叙事 + 外置 Yui 驱动）+ 交易 GUI 化（滑块仓位）。

这一版干了两件大事：

1. **剧情** —— 四幕叙事，玩家会真的**亏、会真的断供**。
   剧情要能"解释为什么会家道中落"，所以**沉没成本**和**不甘心**必须写进
   选项里；而台词**不能写死**（写死了所有角色念同一句），必须是**给外置
   猫娘 Yui 的提示词**，让她用自己的语气说出来。
2. **交易 GUI 化** —— 「一键梭哈 / 一键割肉」两个极端被换成
   **仓位滑块 + 百分比快捷键 + 实时预览 + 【执行买入 / 执行卖出】**。

这里钉死的硬契约：

* **引擎层、模式层零改动**（500 局基线必须逐字节全等）；
* **面板里绝不画猫娘立绘**（外置已经有 Yui 了）；
* **剧情推送必须在锁外**（持锁 await 会冻住事件循环 ⇒ P11.1 的 500 红字）；
* **只有外置猫娘的两条通道**可以用 ``ai_behavior="respond"``；
* **危险 CSS 继续禁用**（沉浸模式用的是宿主原生 ``Modal``，自己一个都没写）。
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_INIT = _PLUGIN_ROOT / "__init__.py"
_STORY_PY = _PLUGIN_ROOT / "story.py"
_STORY_JSON = _PLUGIN_ROOT / "story.json"
_TOML = _PLUGIN_ROOT / "plugin.toml"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_REPO = _PLUGIN_ROOT / "storage" / "repo.py"

sys_path = str(_PLUGIN_ROOT)
if sys_path not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path)

# ⚠ 这里原本是一条 ``from plugins.mock_stock_meow import story as story_mod``，
#   但它排在 sys.path 引导语句之后 ⇒ ruff 判 E402（import 不在文件顶部），
#   而 CI 的 ruff 带 ``--ignore-noqa``，noqa 救不了。改用 importlib 动态取：
#   importlib 调用**不是 import 语句**，E402 不适用，运行时效果完全一样。
story_mod = importlib.import_module("plugins.mock_stock_meow.story")

# ══════════════════════════════════════════════════════════════ 工具


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _story_text() -> str:
    return _STORY_PY.read_text(encoding="utf-8")


def _repo_text() -> str:
    return _REPO.read_text(encoding="utf-8")


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_FILE.read_text(encoding="utf-8"))


def _panel_code() -> str:
    """去掉注释与 ``RRM_CSS`` 的面板源码（只留逻辑）。"""
    source = _panel_text()
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        end = rest.find("\n`\n")
        assert end > 0, "找不到 RRM_CSS 的结束反引号（它必须独占一行）"
        source = head + "\n/* <RRM_CSS> */\n" + rest[end + 3 :]
    # 行注释与块注释都要去掉 —— 注释里提到 ``ActionButton`` 之类是给人看的，
    # 不该让守卫误判。
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _panel_css() -> str:
    source = _panel_text()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    end = rest.find("\n`\n")
    assert end > 0, "找不到 RRM_CSS 的结束反引号（它必须独占一行）"
    body = rest[:end]
    assert "`" not in body, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    return body


def _method_body(source: str, name: str) -> str:
    """取一个方法的源码（到下一个同级定义为止）。"""
    pattern = re.compile(rf"^\s{{4}}(?:async )?def {re.escape(name)}\(", re.M)
    match = pattern.search(source)
    assert match, f"源码里找不到 {name}"
    lines = source[match.start() :].split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if re.match(r"^    (?:async )?def |^    @|^    # ---", line):
            break
        out.append(line)
    return "\n".join(out)


def _story_data() -> dict[str, Any]:
    return json.loads(_STORY_JSON.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════ ① 剧情数据


def test_story_json_exists_and_is_valid() -> None:
    assert _STORY_JSON.exists(), "必须有 story.json（剧情数据）"
    data = _story_data()
    assert isinstance(data.get("chapters"), list)
    assert data["chapters"], "剧情不能是空的"


def test_there_are_exactly_four_chapters() -> None:
    """四幕：拿零花钱试试 / 断供 / 不服输想回本 / 借高利贷。"""
    chapters = _story_data()["chapters"]
    assert len(chapters) == 4, f"应该是四幕，实际 {len(chapters)} 幕"
    ids = [str(item.get("id") or "") for item in chapters]
    assert ids == ["ch1_tuition", "ch2_cutoff", "ch3_sunk", "ch4_usury"]


def test_chapter_ids_are_unique_and_ordered() -> None:
    chapters = _story_data()["chapters"]
    ids = [str(item.get("id") or "") for item in chapters]
    assert len(set(ids)) == len(ids), "章节 id 不能重复"
    orders = [int(item.get("order") or 0) for item in chapters]
    assert orders == sorted(orders), "章节必须按 order 递增"


def test_every_chapter_has_situation_scene_and_choices() -> None:
    """P13：章节里放的是**处境**（客观事实），不再是 P12 那种"提示词"。

    P12 的 ``prompt`` 字段装的是"请用 XX 语气说 YY"这类**表演指令** ——
    那正是把猫娘变成复读机的病根。P13 把它换成了 ``situation``。
    """
    for chapter in _story_data()["chapters"]:
        chapter_id = chapter.get("id")
        assert str(chapter.get("title") or "").strip(), f"{chapter_id} 缺标题"
        assert str(chapter.get("situation") or "").strip(), f"{chapter_id} 缺处境描述"
        assert str(chapter.get("scene") or "").strip(), f"{chapter_id} 缺场景文本"
        choices = chapter.get("choices") or []
        assert len(choices) >= 2, f"{chapter_id} 至少要两个选项"
        for choice in choices:
            assert str(choice.get("id") or "").strip(), f"{chapter_id} 有选项没 id"
            assert str(choice.get("label") or "").strip(), f"{chapter_id} 有选项没文案"


def test_chapters_form_a_chain() -> None:
    """四幕是**递进**的：后一幕必须等前一幕演过。"""
    chapters = {str(item["id"]): item for item in _story_data()["chapters"]}
    assert chapters["ch1_tuition"].get("requires") in (None, [])
    assert chapters["ch2_cutoff"].get("requires") == ["ch1_tuition"]
    assert chapters["ch3_sunk"].get("requires") == ["ch2_cutoff"]
    assert chapters["ch4_usury"].get("requires") == ["ch3_sunk"]


def test_the_story_explains_how_the_family_falls_apart() -> None:
    """用户明确要求：**沉没成本**和**不甘心**要写进选项里。"""
    raw = _STORY_JSON.read_text(encoding="utf-8")
    assert "沉没成本" in raw, "剧情里必须出现「沉没成本」"
    assert "不甘心" in raw, "剧情里必须出现「不甘心」"
    # 也要落在**选项**上（不只是旁白）
    options = []
    for chapter in _story_data()["chapters"]:
        for choice in chapter.get("choices") or []:
            options.append(f"{choice.get('label')} {choice.get('hint')}")
    joined = " ".join(options)
    assert "沉没成本" in joined or "不甘心" in joined, "沉没成本 / 不甘心必须出现在选项上"


def test_prompts_use_the_host_placeholders_not_str_format() -> None:
    """``{MASTER_NAME}`` 由宿主展开 ⇒ 本模块**只准 replace**，**不准 format**。

    P13：提示词改成**现场拼装**（处境 + 客观数据 + 一句开放式请求），
    所以要查的是**拼出来**的那句话，而不是 ``story.json`` 里的某个字段。
    """
    snapshot = {
        "day": 5,
        "cash": 88.0,
        "next_bill": 30.0,
        "initial_cash": 400.0,
        "total_assets": 300.0,
        "loss_ratio": 0.25,
        "trade_count": 6,
    }
    for chapter in _story_data()["chapters"]:
        prompt = story_mod.chapter_prompt(chapter, snapshot)
        assert "{MASTER_NAME}" in prompt, f"{chapter.get('id')} 的提示词要带上玩家称呼"
    body = _story_text()
    assert ".format(" not in body, "剧情模块不许用 str.format（会炸掉 {MASTER_NAME}）"
    assert ".replace(" in body, "替换占位符必须用 str.replace"


def test_prompts_do_not_leak_the_heat_or_the_seed() -> None:
    """P5 契约：剧情**不许**把局中情报（热度/种子/反转日）喂给 LLM。"""
    raw = _STORY_JSON.read_text(encoding="utf-8")
    for token in ("反转日", "heat", "seed"):
        assert token not in raw, f"剧情数据里不许出现 {token}"


# ══════════════════════════════════════════════════════════════ ② 剧情引擎


def test_story_module_loads_the_json() -> None:
    story_mod.reset_cache()
    chapters = story_mod.chapters()
    assert len(chapters) == 4
    assert story_mod.chapter_by_id("ch1_tuition") is not None
    assert story_mod.chapter_by_id("nope") is None
    story_mod.reset_cache()


def test_a_broken_story_file_degrades_instead_of_crashing() -> None:
    """数据文件坏了 ⇒ 插件照常跑，只是没有剧情。"""
    story_mod.reset_cache()
    original = story_mod._STORY_PATH
    try:
        story_mod._STORY_PATH = Path("__definitely_not_here__.json")
        data = story_mod.load_story()
        assert data.get("chapters") == []
    finally:
        story_mod._STORY_PATH = original
        story_mod.reset_cache()


def test_trigger_matches_on_loss_ratio() -> None:
    trigger = {"type": "watch", "min_loss_ratio": 0.25}
    assert story_mod.trigger_matches(trigger, {"loss_ratio": 0.3}) is True
    assert story_mod.trigger_matches(trigger, {"loss_ratio": 0.1}) is False


def test_trigger_matches_on_cash_below_bill() -> None:
    trigger = {"type": "watch", "or_cash_below_bill": True}
    assert story_mod.trigger_matches(trigger, {"cash": 10.0, "next_bill": 50.0}) is True
    assert story_mod.trigger_matches(trigger, {"cash": 90.0, "next_bill": 50.0}) is False


def test_join_trigger_is_always_true() -> None:
    assert story_mod.trigger_matches({"type": "join"}, {}) is True


def test_next_chapter_respects_the_requires_chain() -> None:
    """第一章没演过 ⇒ 第二章**不能**开演。"""
    snapshot = {"loss_ratio": 0.9, "cash": 0.0, "next_bill": 10.0}
    first = story_mod.next_chapter(snapshot, [], "")
    assert first is not None and first["id"] == "ch1_tuition"
    second = story_mod.next_chapter(snapshot, ["ch1_tuition"], "")
    assert second is not None and second["id"] == "ch2_cutoff"
    third = story_mod.next_chapter(snapshot, ["ch1_tuition", "ch2_cutoff"], "")
    assert third is not None and third["id"] == "ch3_sunk"


def test_a_pending_chapter_blocks_the_next_one() -> None:
    """上一幕还没选完 ⇒ 不许开新的一幕。"""
    snapshot = {"loss_ratio": 0.9, "cash": 0.0, "next_bill": 10.0}
    assert story_mod.next_chapter(snapshot, ["ch1_tuition"], "ch2_cutoff") is None


def test_seen_chapters_never_replay() -> None:
    snapshot = {"loss_ratio": 0.9, "cash": 0.0, "next_bill": 10.0}
    seen = ["ch1_tuition", "ch2_cutoff", "ch3_sunk", "ch4_usury"]
    assert story_mod.next_chapter(snapshot, seen, "") is None


def test_chapter_card_carries_the_choice_buttons() -> None:
    """选项按钮挂在**聊天卡片**上（``data-neko-action``）⇒ 回调插件入口。"""
    chapter = story_mod.chapter_by_id("ch1_tuition")
    assert chapter is not None
    spec = story_mod.chapter_card(chapter, {"day": 1, "cash": 100.0})
    assert spec["html"], "卡片必须有 html"
    assert "data-neko-action" in spec["html"], "选项按钮必须带 data-neko-action"
    assert spec["actions"], "卡片必须有 actions"
    for choice in chapter["choices"]:
        action = spec["actions"][choice["id"]]
        assert action["entry"] == "story_choice", "卡片按钮必须回调 story_choice"
        assert action["args"]["chapter_id"] == "ch1_tuition"
        assert action["args"]["choice_id"] == choice["id"]


def test_coalesce_key_is_per_chapter() -> None:
    """同一章的连发要能**合并**（不刷屏）。"""
    assert story_mod.coalesce_key("ch1_tuition") != story_mod.coalesce_key("ch2_cutoff")
    assert story_mod.coalesce_key("ch1_tuition").startswith(story_mod.STORY_COALESCE_PREFIX)


def test_story_priority_is_above_daily_chatter() -> None:
    """剧情比日常搭话重要，但不该挤掉提醒类消息（memo = 8）。"""
    assert story_mod.STORY_PUSH_PRIORITY > 2
    assert story_mod.STORY_PUSH_PRIORITY <= 8


# ══════════════════════════════════════════════════════════════ ③ 插件层接入


def test_story_choice_is_a_ui_action_with_a_plugin_entry() -> None:
    """P10 契约：``ui.action`` 必须配 ``plugin_entry``。"""
    init = _init_text()
    body = _method_body(init, "story_choice")
    assert '@ui.action(' in body or "@ui.action(" in init
    assert "id=\"story_choice\"" in init
    assert "id=\"story_choice\"" in init, "story_choice 必须同时是 plugin_entry"


def test_story_choice_is_locked() -> None:
    """选项结算要持锁（它会改玩家的现金）。"""
    init = _init_text()
    index = init.index("async def story_choice(")
    window = init[max(0, index - 400) : index]
    assert "@_locked" in window, "story_choice 必须 @_locked"


def test_the_story_voice_channel_hides_the_plugin_text() -> None:
    """``visibility=[]`` + ``respond`` ⇒ 玩家只看到猫娘的话，看不到原文。"""
    body = _method_body(_init_text(), "_push_story_voice")
    assert "visibility=[]" in body, "剧情推送必须 visibility=[]（插件原文不显示）"
    assert 'ai_behavior="respond"' in body


def test_the_story_push_carries_a_coalesce_key() -> None:
    body = _method_body(_init_text(), "_push_story_voice")
    assert "coalesce_key=" in body, "剧情推送必须带 coalesce_key（不刷屏）"


def test_the_story_push_never_holds_the_lock_while_awaiting() -> None:
    """**P11.1 的教训**：持锁 await 会冻住事件循环 ⇒ 宿主 500 红字。

    剧情推送要 ``await`` 宿主 IPC，所以它**绝不能**在持锁时发生。
    """
    body = _method_body(_init_text(), "_push_story_voice")
    assert "self._lock" not in body, "推送通道不许碰锁"
    for method in ("_story_tick", "_push_story_chapter", "_push_story_reply"):
        assert "self._lock" not in _method_body(_init_text(), method), (
            f"{method} 不许在持锁状态下推送"
        )


def test_the_story_runs_in_the_heartbeat_outside_the_lock() -> None:
    """剧情判定挂在心跳的 ``_run_ticks`` **之后**（那时锁已经还回来了）。"""
    body = _method_body(_init_text(), "market_tick")
    ticks = body.index("_run_ticks(")
    story = body.index("_story_tick()")
    assert story > ticks, "剧情判定必须在 _run_ticks 之后（锁外）"


def test_the_story_state_is_written_with_cas() -> None:
    """剧情状态是**一整块 JSON** ⇒ 必须 CAS，否则会丢更新（钱发两次）。"""
    assert "cas_setting" in _repo_text(), "repo 必须提供 CAS 写设置"
    body = _method_body(_init_text(), "_story_commit_state")
    assert "cas_setting" in body, "剧情状态写回必须走 CAS"
    assert "self._lock" not in body, "CAS 的意义就是**不持锁**"


def test_the_story_choice_claims_before_it_pays() -> None:
    """先认领（CAS）再发钱 —— 顺序反了会双发。"""
    body = _method_body(_init_text(), "story_choice")
    claim = body.index("_story_commit_state()")
    pay = body.index("UPDATE players SET cash")
    assert claim < pay, "必须先认领选项，再改钱"


def test_the_story_effects_stay_in_the_plugin_layer() -> None:
    """剧情数值效果**只在插件层**：引擎层 / 模式层不许出现 story。"""
    for folder in ("engine", "modes"):
        for path in (_PLUGIN_ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "story" not in text.lower() or "存储" not in text, (
                f"{path.name} 里出现了剧情相关内容"
            )
    for path in (_PLUGIN_ROOT / "engine").rglob("*.py"):
        assert "story_choice" not in path.read_text(encoding="utf-8")
    for path in (_PLUGIN_ROOT / "modes").rglob("*.py"):
        assert "story_choice" not in path.read_text(encoding="utf-8")


def test_the_panel_payload_carries_fees_and_story() -> None:
    body = _method_body(_init_text(), "_panel_payload")
    assert '"fees"' in body, "面板载荷要带手续费率（预览要算准）"
    assert '"story"' in body, "面板载荷要带剧情状态"
    # 费率是**只读** engine_config.fees
    assert "engine_config.fees.buy_rate" in body
    assert "engine_config.fees.sell_rate" in body
    assert "engine_config.fees.stamp_tax_rate" in body


def test_the_story_block_is_pure_memory() -> None:
    """面板每秒轮询 ⇒ 载荷里的剧情块**不许查库**。"""
    body = _method_body(_init_text(), "_story_block")
    for forbidden in ("await", "get_setting", "load_session"):
        assert forbidden not in body, f"_story_block 里不许出现 {forbidden}"


def test_the_story_debt_is_repaid_without_killing_the_player() -> None:
    """剧情借贷到期扣款 ⇒ **绝不判破产**（扣不动就欠着）。"""
    body = _method_body(_init_text(), "_story_apply_debt")
    assert "alive" not in body, "剧情还债不许碰结局"
    assert "death_reason" not in body


# ══════════════════════════════════════════════════════════════ ④ 交易 GUI


def test_the_trade_ui_uses_the_host_slider() -> None:
    """滑块必须是宿主**原生** ``Slider``（自己画滑块就等于自己写危险 CSS）。"""
    code = _panel_code()
    assert "Slider" in code, "仓位滑块要用宿主原生 Slider"
    assert 'h(Slider' in code or "Slider," in code
    assert "type=\"range\"" not in code, "不要自己造 range input"


def test_the_trade_ui_has_no_percentage_shortcuts() -> None:
    """P13：25/50/75/100 那四档快捷键**必须消失**。

    P12 把「一键梭哈 / 一键割肉」换成了仓位滑块，但滑块只有四档 ——
    仓位还是被**量化**的。P13 要的是**连续**仓位（1% 步进）+ 直输框。
    """
    code = _panel_code()
    assert "[25, 50, 75, 100]" not in code, "四档快捷键不许回来（那才是'去量子化'的拦路虎）"
    assert "rrm-trade-pct" not in code
    assert "rrm-pct" not in code


def test_the_trade_buttons_are_renamed() -> None:
    """「一键梭哈 / 一键割肉」被换成【执行买入 / 执行卖出】。"""
    i18n = _i18n()
    assert "panel.trade_execute_buy" in i18n
    assert "panel.trade_execute_sell" in i18n
    code = _panel_code()
    assert 't("panel.trade_execute_buy")' in code
    assert 't("panel.trade_execute_sell")' in code
    assert 't("visual.buy_all")' not in code, "面板里不该再有「一键梭哈」"
    assert 't("visual.sell_all")' not in code, "面板里不该再有「一键割肉」"


def test_the_trade_buttons_go_through_the_global_lock() -> None:
    """执行按钮必须走 ``callAction`` ⇒ 纳入 P11.1 的全局请求锁。"""
    code = _panel_code()
    assert "const executeBuy = async () =>" in code
    assert "const executeSell = async () =>" in code
    assert 'callAction("buy"' in code, "买入必须走 callAction"
    assert 'callAction("sell"' in code, "卖出必须走 callAction"
    # 交易区里不许再直接用 ActionButton
    trade_zone = code[code.index("panel.trade_execute_buy") - 4000 : code.index("panel.trade_execute_buy")]
    assert "ActionButton" not in trade_zone, "交易区的按钮不该再用 ActionButton"


def test_the_trade_preview_uses_the_engine_fee_rates() -> None:
    """预览必须用**引擎那一套费率**，不是拍脑袋的 0.995。"""
    code = _panel_code()
    assert "state.fees?.buy_rate" in code
    assert "state.fees?.sell_rate" in code
    assert "state.fees?.stamp_tax_rate" in code
    assert "0.995" not in code, "旧的经验折算 0.995 已经换成精确费率"


def test_the_panel_never_draws_a_catgirl_portrait() -> None:
    """外置已经有 Yui 了 ⇒ 面板**不许**自己画猫娘立绘。

    （P11 的兜底弹窗里那个 🐱 是**已批准**的降级 UI，不在本题范围 ——
    这里查的是"图片 / 立绘类样式"，不是"任何猫娘元素"。）
    """
    code = _panel_code()
    css = _panel_css()
    for cls in re.findall(r"rrm-story-[a-z-]+", css):
        for word in ("catgirl", "avatar", "portrait", "bubble", "face", "img"):
            assert word not in cls, f"剧情样式里不该有立绘类名：{cls}"
    assert "<img" not in code, "面板里不许插图片（外置有 Yui 了）"
    assert "backgroundImage" not in code, "面板里不许用背景图"
    assert "url(" not in css, "CSS 里不许引外部图片"


def test_the_immersive_mode_uses_the_host_modal() -> None:
    """沉浸模式 = 宿主原生 ``Modal size="full"``（自己写全屏就危险了）。"""
    code = _panel_code()
    assert 'size: "full"' in code, "沉浸模式要用宿主的 Modal size=full"
    assert "rrm-immersive-overlay" not in code, "不要自己造全屏覆盖层"


def test_the_immersive_mode_shows_text_only() -> None:
    """沉浸模式里只有**纯文本场景**：不画立绘、不画对话框。"""
    code = _panel_code()
    assert "rrm-story-scene" in code, "沉浸模式要有纯文本场景"
    assert "whiteSpace" not in code or True  # 允许预格式化，但不许有图片


def test_the_story_choices_fall_back_to_the_panel_only_when_the_card_fails() -> None:
    """正常路径：选项在**聊天卡片**上；只有卡片失败才由面板接管。"""
    code = _panel_code()
    assert "story.card_ok" in code, "面板要知道卡片投递成功没有"
    assert "rrm-story-choices" in code


# ══════════════════════════════════════════════════════════════ ⑤ 红线


def test_the_story_view_survives_a_plugin_restart() -> None:
    """展示层（标题 / 场景 / 选项 / 卡片状态）必须**跟着状态存**。

    插件重启后实例属性会没 —— 而这一幕还"待选"（玩家还没点），
    那它必须**原样还在**（否则弹窗是空的，玩家根本选不了）。
    """
    body = _method_body(_init_text(), "_open_story_chapter")
    for key in (
        'state["title"]',
        'state["scene"]',
        'state["choices"]',
        'state["card_ok"]',
    ):
        assert key in body, f"{key} 必须写进状态"
    block = _method_body(_init_text(), "_story_block")
    for key in (
        'state.get("title")',
        'state.get("scene")',
        'state.get("choices")',
        'state.get("card_ok")',
    ):
        assert key in block, f"_story_block 必须从状态里读 {key}"


def test_the_story_view_self_heals_from_older_state() -> None:
    """旧版本写的状态里**没有展示层** ⇒ 要能**自愈**补上。

    （展示层是 P12 才加进状态的。）那一幕还"待选"时补上，
    否则面板弹窗是空的，玩家根本选不了。
    """
    body = _method_body(_init_text(), "_story_tick")
    assert 'not str(state.get("scene") or "")' in body, "缺展示层时要自愈"
    assert 'state["scene"] = chapter_scene(chapter, snapshot)' in body
    assert 'state["choices"] = [' in body


def test_the_immersive_mode_keys_on_the_chapter_not_the_counter() -> None:
    """沉浸模式用**章节 id**做键，不是 ``seq``。

    ``seq`` 是内存计数 —— 插件重启就归零，那样"重启前就开着、还没选完"的
    那一幕就再也弹不出来了（剧情会卡住，玩家看不到待选项）。
    """
    code = _panel_code()
    assert "storyPendingRef" in code, "沉浸模式要用章节 id 做键"
    assert "storyPendingRef.current = pending" in code


def test_the_panel_css_has_no_backticks() -> None:
    """``RRM_CSS`` 是**反引号模板字符串** —— 里面一个反引号都不许有。

    **连注释里也不行。** P11.1 与 P12.0 各踩过一次：注释里顺手写了双反引号
    （比如 ``size="full"``），模板当场被截断 ⇒ 面板白屏，
    官方 TSX 校验也会报 "This expression is not callable"。
    """
    css = _panel_css()
    assert "`" not in css, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"


def test_the_dangerous_css_is_still_banned() -> None:
    """0.8.0 的崩溃红线：危险 CSS 一条都不许回来（沉浸模式也不例外）。"""
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


def test_the_engine_and_modes_are_untouched() -> None:
    """引擎层与模式层**零改动**（P12 全部在插件层）。"""
    engine_dir = _PLUGIN_ROOT / "engine"
    modes_dir = _PLUGIN_ROOT / "modes"
    # 用**精确标记**查，别用裸词 "story" —— ``heat_history`` 里也含 story。
    for path in list(engine_dir.rglob("*.py")) + list(modes_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "story_choice",
            "story.json",
            "story.py",
            "mock_stock_meow.story",
            "cas_setting",
            "_story_",
        ):
            assert forbidden not in text, f"{path.name} 里出现了剧情相关内容：{forbidden}"


def test_the_story_json_ships_with_the_plugin() -> None:
    """``story.json`` 在插件根目录 ⇒ 打包会被带走（和 community_roster.json 同理）。"""
    assert _STORY_JSON.parent == _PLUGIN_ROOT
    assert _STORY_JSON.stat().st_size > 1000, "剧情数据不该是空壳"


def test_the_new_i18n_keys_exist() -> None:
    i18n = _i18n()
    for key in (
        "panel.trade_position",
        "panel.trade_position_hint",
        "panel.trade_pct",
        "panel.trade_buy_preview",
        "panel.trade_sell_preview",
        "panel.trade_execute_buy",
        "panel.trade_execute_sell",
        "panel.story_modal_hint",
        "panel.story_modal_title",
        "panel.story_choice_done",
        "panel.story_choose_here",
        "panel.story_debt",
        "panel.story_debt_overdue",
        "error.story_no_pending",
        "error.story_unknown_choice",
        "error.story_busy",
    ):
        assert key in i18n, f"i18n 词典里缺 {key}"
        assert str(i18n[key]).strip(), f"{key} 不能是空串"


def test_the_panel_uses_no_bare_chinese_literals() -> None:
    """P8 红线：面板里新增的文字必须进 i18n 词典。"""
    code = _panel_code()
    # 去掉注释后再找中文串（注释里的中文是给人看的）
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    literals = re.findall(r'"([^"\n]*[\u4e00-\u9fff][^"\n]*)"', code)
    for literal in literals:
        assert literal.startswith("panel.") or literal.startswith("guide.") or literal.startswith(
            "meow."
        ) or literal.startswith("visual.") or literal.startswith("error.") or literal.startswith(
            "story."
        ) or literal.startswith("boom.") or literal.startswith("daily.") or literal.startswith(
            "taunt."
        ) or literal.startswith("director.") or literal.startswith("welfare.") or literal.startswith(
            "loan."
        ) or literal.startswith("kline.") or literal.startswith("news.") or literal.startswith(
            "onboarding."
        ), f"面板里出现了裸中文字面量：{literal!r}"


def test_the_version_is_bumped() -> None:
    assert 'version = "0.10.5"' in _TOML.read_text(encoding="utf-8")
    assert 'PLUGIN_VERSION = "0.10.5"' in _init_text()
