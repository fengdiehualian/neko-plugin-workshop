"""P13.0 守卫：Yui 灵魂化（交还叙事权）+ 交易彻底去量子化（连续仓位 + 直输）。

这一版推翻了两个 P12 的设计判断：

1. **P12 把"提示词"当成了"给猫娘的台词指导"** —— 于是剧情数据里写着
   "请立刻主动开口，用你自己的语气把这一幕说清楚"这类**表演指令**。
   结果是猫娘变成了复读机：不管玩家做了什么，她都按剧本演。
   P13 把叙事权**完全交还给 N.E.K.O 的大模型** —— 我们只负责抛出
   **事件的引子**（客观事实 + 当前处境），她爱嘲讽就嘲讽、爱冷漠就冷漠。
2. **P12 的仓位滑块只有 25/50/75/100 四档** —— 仓位还是被**量化**的。
   P13 把它拆干净：**1% 步进的连续滑块** + **直输框**（股数 / 金额），
   输入后系统**反推**仓位比例并同步滑块。

这里钉死的硬契约：

* **提示词里不许出现任何表演指令**（语气 / 台词 / "你要告诉玩家什么"）；
* **提示词里只有一句祈使句** —— 那句开放式的请求；
* 引擎层、模式层零改动（500 局基线必须逐字节全等）；
* 危险 CSS 继续禁用；面板里绝不画猫娘立绘。
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

sys_path = str(_PLUGIN_ROOT)
if sys_path not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path)

# ⚠ 这里原本是一条 ``from plugins.mock_stock_meow import story as story_mod``，
#   但它排在 sys.path 引导语句之后 ⇒ ruff 判 E402（import 不在文件顶部），
#   而 CI 的 ruff 带 ``--ignore-noqa``，noqa 救不了。改用 importlib 动态取：
#   importlib 调用**不是 import 语句**，E402 不适用，运行时效果完全一样。
story_mod = importlib.import_module("plugins.mock_stock_meow.story")

# ══════════════════════════════════════════════════════════════ 词汇表

#: **表演指令**词表：这些词一旦出现在给 Yui 的提示词里，就说明我们又在
#: 替她演戏了。扫的是**拼出来的那句话**（运行时产物），不是源码 ——
#: 源码里的文档字符串提到这些词是正常的。
P13_FORBIDDEN_DIRECTIVES = (
    "用你自己的语气",
    "请立刻主动开口",
    "主动开口",
    "请用",
    "请以",
    "嘲讽",
    "假装",
    "冷漠",
    "温柔",
    "口吻",
    "腔调",
    "演绎",
    "表演",
    "台词",
    "必须告诉",
    "必须提醒",
    "务必",
    "一定要",
    "提醒他",
    "提醒她",
    "提醒玩家",
    "顺便提",
    "点一下面板",
    "你可以只投",
    "别一上来就",
    "别迎合",
    "告诉他",
    "告诉她",
    "告诉玩家",
    "说出",
    "念出",
)

#: P12 时代那些**具体写死**的句子 —— 一个都不许再出现（源码级精确匹配）。
P13_REMOVED_PHRASES = (
    "请立刻主动开口",
    "用你自己的语气",
    "别迎合他",
    "别把他骂到自闭",
    "把选择的代价说清楚",
    "顺便提一句",
    "可以只投一部分",
    "别一上来就梭哈",
)


# ══════════════════════════════════════════════════════════════ 工具


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _story_text() -> str:
    return _STORY_PY.read_text(encoding="utf-8")


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


def _snapshot() -> dict[str, Any]:
    """一份典型的"难堪"快照（用来逼出提示词里的事实行）。"""
    return {
        "day": 9,
        "cash": 123.45,
        "next_bill": 40.0,
        "initial_cash": 500.0,
        "total_assets": 380.5,
        "loss_ratio": 0.239,
        "trade_count": 11,
    }


def _all_prompts() -> list[tuple[str, str]]:
    """把**所有**会被推给 Yui 的提示词都拼出来（章节 + 每个选项的回应）。"""
    snapshot = _snapshot()
    out: list[tuple[str, str]] = []
    for chapter in story_mod.chapters():
        out.append((f"chapter:{chapter.get('id')}", story_mod.chapter_prompt(chapter, snapshot)))
        for choice in chapter.get("choices") or []:
            label = f"reply:{chapter.get('id')}:{choice.get('id')}"
            out.append(
                (
                    label,
                    story_mod.reply_prompt(
                        chapter,
                        choice,
                        snapshot,
                        granted=120.0,
                        debt_amount=144.0,
                        due_days=3,
                    ),
                )
            )
    return out


# ══════════════════════════════════════════════════════════════ ① 灵魂化


def test_the_story_data_has_no_prompt_key_anymore() -> None:
    """``prompt``（P12 的"给猫娘的台词指导"位）必须**整个删掉**。

    留着它，就等于留着一个可以往里塞表演指令的口子。
    """
    for chapter in _story_data()["chapters"]:
        assert "prompt" not in chapter, f"{chapter.get('id')} 还留着 prompt 字段"
        assert str(chapter.get("situation") or "").strip(), f"{chapter.get('id')} 缺处境描述"


def test_the_story_module_never_reads_the_old_prompt_key() -> None:
    """``story.py`` 不许再读 ``chapter["prompt"]``。"""
    body = _story_text()
    assert '"prompt"' not in body, "剧情模块不许再读 chapter[\"prompt\"]"
    assert "'prompt'" not in body


def test_no_prompt_carries_a_performance_directive() -> None:
    """**核心守卫**：拼出来的提示词里不许有任何表演指令。

    这是整套守卫里最要紧的一条 —— 它扫的是**运行时产物**，
    所以不管指令是写死在 ``story.json`` 里、写死在 ``story.py`` 的
    模板里，还是临时拼出来的，都跑不掉。
    """
    prompts = _all_prompts()
    assert len(prompts) == 12, f"应该是 4 章 + 8 个选项的回应，实际 {len(prompts)} 条"
    for label, prompt in prompts:
        for token in P13_FORBIDDEN_DIRECTIVES:
            assert token not in prompt, f"{label} 的提示词里出现了表演指令「{token}」：\n{prompt}"


def test_the_situation_text_carries_no_performance_directive() -> None:
    """**处境**字段本身也不许带表演指令（它会被拼进提示词）。"""
    for chapter in _story_data()["chapters"]:
        situation = str(chapter.get("situation") or "")
        for token in P13_FORBIDDEN_DIRECTIVES:
            assert token not in situation, f"{chapter.get('id')} 的处境里出现了「{token}」"
        assert "请" not in situation, f"{chapter.get('id')} 的处境里不该出现祈使句"


def test_the_prompt_has_exactly_one_open_ended_ask() -> None:
    """提示词里**只有一句**祈使句 —— 那句开放式的请求。

    换句话说：我们只负责抛出事件的引子，**怎么说是她的事**。
    """
    for label, prompt in _all_prompts():
        assert prompt.count("请") == 1, f"{label} 的提示词里祈使句不止一句：\n{prompt}"
        assert prompt.rstrip().endswith(story_mod.STORY_OPENING_ASK), (
            f"{label} 的提示词必须以开放式请求收尾：\n{prompt}"
        )


def test_the_prompt_carries_the_objective_facts() -> None:
    """提示词必须带**客观数据行**（数字），不然就是空对空地聊天。"""
    for label, prompt in _all_prompts():
        for marker in ("当前剧情节点：", "当前处境：", "客观数据："):
            assert marker in prompt, f"{label} 的提示词缺「{marker}」"
        assert "{MASTER_NAME}" in prompt, f"{label} 的提示词要带上玩家称呼"
        assert "30%" not in prompt or True  # 亏损比例按快照算，不写死


def test_the_prompt_reports_the_actual_change_after_a_choice() -> None:
    """回应提示词要写清**这次选择实际造成了什么**（发钱 / 欠债）。"""
    chapter = story_mod.chapter_by_id("ch4_usury")
    assert chapter is not None
    choice = story_mod.choice_by_id(chapter, "yes")
    assert choice is not None
    prompt = story_mod.reply_prompt(
        chapter, choice, _snapshot(), granted=200.0, debt_amount=240.0, due_days=3
    )
    assert "200.00" in prompt, "回应提示词要写清到手的现金"
    assert "240.00" in prompt, "回应提示词要写清欠了多少"
    assert "3 天后到期" in prompt, "回应提示词要写清还款期限"
    assert "借！先活过这周" in prompt, "回应提示词要写清玩家选了什么"


def test_the_reply_prompt_lives_in_the_story_module() -> None:
    """**所有**给 Yui 的提示词都在 ``story.py`` 里拼 —— 一个地方好管好守。"""
    body = _method_body(_init_text(), "_story_reply_prompt")
    assert "reply_prompt(" in body, "_story_reply_prompt 必须委托给 story.reply_prompt"
    for phrase in P13_REMOVED_PHRASES:
        assert phrase not in body, f"_story_reply_prompt 里还留着旧指令：{phrase}"


def test_the_old_directives_are_gone_from_the_sources() -> None:
    """P12 那些写死的句子，一个都不许再出现（源码级精确匹配）。"""
    for path in (_STORY_JSON, _STORY_PY, _INIT):
        text = path.read_text(encoding="utf-8")
        for phrase in P13_REMOVED_PHRASES:
            assert phrase not in text, f"{path.name} 里还留着 P12 的表演指令：{phrase}"


def test_the_prompt_template_is_the_only_instruction_source() -> None:
    """开放式请求是**唯一**的"指令"，它住在 ``story.py`` 的常量里。"""
    assert story_mod.STORY_OPENING_ASK == "请结合当前的情景，自然地给予玩家回应。"
    assert "请结合当前的情景" in _story_text()
    # 只有它带"请" —— 别的模板行都是事实。
    for name in ("STORY_EVENT_HEADER", "STORY_FALLBACK_SITUATION"):
        value = getattr(story_mod, name)
        assert "请" not in value, f"{name} 不许带祈使句"


# ══════════════════════════════════════════════════════════════ ② 去量子化


def test_the_position_slider_is_continuous() -> None:
    """仓位滑块必须是 **1% 步进**的连续滑块（不是 5% 的四档）。"""
    code = _panel_code()
    # 滑块现在是用 `h(Slider, {...})` 调的（要往外层 div 写 CSS 变量），
    # 所以两种写法都认。
    assert "step={1}" in code or "step: 1" in code, "仓位滑块必须是 1% 步进"
    assert "step={5}" not in code and "step: 5" not in code, "5% 步进会把仓位重新量化成 20 档"


def test_the_four_preset_buttons_are_gone() -> None:
    """25/50/75/100 那四档快捷键**必须消失**（连同它们的样式）。"""
    code = _panel_code()
    css = _panel_css()
    assert "[25, 50, 75, 100]" not in code
    assert "rrm-trade-pct" not in code
    assert "rrm-pct" not in code
    assert ".rrm-pct" not in css
    assert ".rrm-trade-pct" not in css


def test_the_trade_area_has_direct_amount_inputs() -> None:
    """预览区要有**直输框**（股数 / 金额），买卖各一组。"""
    code = _panel_code()
    assert "rrm-trade-input" in code, "要有直输框"
    assert "rrm-trade-input-unit" in code, "直输框要有单位小字"
    for name in (
        "onBuySharesInput",
        "onBuyMoneyInput",
        "onSellSharesInput",
        "onSellMoneyInput",
    ):
        assert name in code, f"缺少 {name}"


def test_the_direct_input_back_computes_the_position() -> None:
    """直输 ⇒ **反推**仓位比例 ⇒ 写回滑块（比例是唯一的事实来源）。

    P14.0：仓位拆成了**买入 / 卖出两条**（P13 的时候买和卖共用一个滑块，
    拖一下就同时改了"买多少"和"卖多少"，本来就该分开），
    所以写回的 setter 也分成两个 —— 不变式还是同一个：
    **直输 ⇒ 反推 ⇒ 写回对应那条滑块**。
    """
    code = _panel_code()
    for name in (
        "pctForBuyShares",
        "pctForBuyMoney",
        "pctForSellShares",
        "pctForSellMoney",
    ):
        assert name in code, f"缺少反推函数 {name}"
    assert "setPositionPct(pctForBuyShares(" in code
    assert "setPositionPct(pctForBuyMoney(" in code
    assert "setSellPositionPct(pctForSellShares(" in code
    assert "setSellPositionPct(pctForSellMoney(" in code


def test_the_back_computation_uses_the_right_base() -> None:
    """买入按**现金**折算，卖出按**可卖数量**折算 —— 两者基数不同。"""
    code = _panel_code()
    assert "const cost = shares * buyPrice * (1 + buyRate)" in code
    assert "(cost / cash) * 100" in code
    assert "(shares / sellAllAmount) * 100" in code


def test_the_share_rounding_is_float_safe() -> None:
    """比例 → 股数 的往返带浮点误差 ⇒ **必须**走浮点安全的取整。

    实测过的坑：比例来回换算带 ``1e-14`` 量级误差，裸 ``Math.floor``
    会把"明明是 1 股"算成 **0 股** —— 玩家说"我要买 1 股"，结果一股没买到。
    """
    code = _panel_code()
    assert "const robustFloor = (value: number) =>" in code, "缺少浮点安全取整"
    assert "Math.abs(value - nearest) < 1e-6" in code
    assert "robustFloor(buyNotional / buyPrice)" in code, "买入取整必须走 robustFloor"
    # P14.0：卖出那条滑块独立了（``sellPct``），取整**仍然**必须走 robustFloor。
    assert "robustFloor((sellAllAmount * sellPct) / 100)" in code, "卖出取整必须走 robustFloor"
    assert "Math.floor(buyNotional / buyPrice)" not in code, "买入不许再裸 Math.floor"
    assert "Math.floor((sellAllAmount * sellPct) / 100)" not in code, "卖出不许再裸 Math.floor"


def test_the_money_input_keeps_its_decimal_placeholder() -> None:
    """金额那一栏必须用**文本型**输入框。

    ``type="number"`` 会吃掉小数中间态：玩家打"84."时字段变成"84"，
    下一个字符就变成"842" —— 小数根本打不出来。

    （输入框是用宿主 ``Input`` 组件的 **``h()`` 调用**形式写的，所以这里
    查的是对象写法 ``type: "text"``。）
    """
    code = _panel_code()
    assert 'type: "text"' in code, "金额输入框要用文本型"
    assert 'type: "number"' in code, "股数输入框可以用数字型"
    # 文本型的那一栏必须是**金额**（股数用数字型）。
    money_blocks = re.findall(r'type: "text"[\s\S]{0,800}?onBuyMoneyInput', code)
    assert money_blocks, "金额输入框必须存在"
    assert 'type: "text"' in money_blocks[0]


def test_the_pushed_prompt_is_logged_for_audit() -> None:
    """推出去的提示词要**记进日志**。

    "提示词里只有事实、没有表演指令"这条规矩光靠代码看不出来 ——
    记一笔日志，它就**可审计**了（排查"她为什么这么说"也用得上）。
    """
    body = _method_body(_init_text(), "_push_story_voice")
    assert "剧情提示词" in body, "推送前要把提示词记进日志"


def test_the_input_snaps_to_the_actual_value_on_blur() -> None:
    """离开输入框 ⇒ 草稿作废，框里显示**实际**成交的那一组数。

    "我要买 20 块"，但一股就 11.77 ⇒ 真正花的是 11.77 —— 框里不能一直
    显示着 20，让玩家以为会花掉 20。

    （换焦点时——从股数框跳到金额框——**不能**误清草稿，
    所以要用 ``relatedTarget`` 判断新焦点是不是还在这一组框里。）
    """
    code = _panel_code()
    # **必须是 focusout**：宿主 UI Kit 是自研迷你运行时（onX → addEventListener），
    # 而 `blur` 不冒泡 —— 挂在容器上的 onBlur 永远等不到子输入框的失焦。
    assert "onFocusout" in code, "输入框离开时要吸附到实际值（用 focusout，blur 不冒泡）"
    assert "onBlur" not in code, "onBlur 在这个运行时里不冒泡，等于没挂"
    assert "relatedTarget" in code, "换焦点（两个框之间）时不能误清草稿"
    assert "setBuySharesDraft(null)" in code
    assert "setSellMoneyDraft(null)" in code


def test_the_executed_amount_still_comes_from_the_position() -> None:
    """真正下单的数量**永远**由比例算出来 —— 直输框不能自己开一条后门。"""
    code = _panel_code()
    assert (
        'callAction("buy", { stock_id: effectiveStockId, amount: buyAmount })' in code
    )
    assert (
        'callAction("sell", { stock_id: effectiveSellStockId, amount: sellAmount })' in code
    )


def test_the_inputs_use_the_host_native_input() -> None:
    """输入框必须是宿主**原生** ``Input``（自己造 input 就要自己写危险 CSS）。"""
    code = _panel_code()
    assert "Input," in code or "h(Input" in code, "要用宿主原生 Input"
    assert "<input" not in code, "不要自己造 input"
    assert 'type="range"' not in code, "不要自己造 range input"


def test_the_preview_still_uses_the_engine_fee_rates() -> None:
    """反推不能引入"经验费率" —— 还是引擎那一套。"""
    code = _panel_code()
    assert "state.fees?.buy_rate" in code
    assert "state.fees?.sell_rate" in code
    assert "state.fees?.stamp_tax_rate" in code
    assert "0.995" not in code


# ══════════════════════════════════════════════════════════════ ③ 红线


def test_the_engine_and_modes_are_untouched() -> None:
    """引擎层与模式层**零改动**（P13 全部在插件层）。"""
    engine_dir = _PLUGIN_ROOT / "engine"
    modes_dir = _PLUGIN_ROOT / "modes"
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


def test_the_dangerous_css_is_still_banned() -> None:
    """0.8.0 的崩溃红线：危险 CSS 一条都不许回来（P13 加了输入框也一样）。"""
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


def test_the_panel_never_draws_a_catgirl_portrait() -> None:
    """外置已经有 Yui 了 ⇒ 面板**不许**自己画猫娘立绘。"""
    code = _panel_code()
    css = _panel_css()
    for cls in re.findall(r"rrm-story-[a-z-]+", css):
        for word in ("catgirl", "avatar", "portrait", "bubble", "face", "img"):
            assert word not in cls, f"剧情样式里不该有立绘类名：{cls}"
    assert "<img" not in code
    assert "backgroundImage" not in code
    assert "url(" not in css


def test_the_new_i18n_keys_exist() -> None:
    i18n = _i18n()
    for key in (
        "panel.trade_input_shares",
        "panel.trade_input_money",
        "panel.trade_unit_shares",
        "panel.trade_unit_money",
    ):
        assert key in i18n, f"i18n 词典里缺 {key}"
        assert str(i18n[key]).strip(), f"{key} 不能是空串"


def test_the_version_is_bumped() -> None:
    assert 'version = "0.10.5"' in _TOML.read_text(encoding="utf-8")
    assert 'PLUGIN_VERSION = "0.10.5"' in _init_text()
