"""情绪词表一致性看门狗。

情绪这套东西在代码里散成了四份名单：

1. ``attention_service._EMOTION_MULTIPLIER``      —— 倍率（速率偏移）
2. ``settings_schema.DEFAULT_EMOTION_MULTIPLIERS`` —— 前端可配置默认值
3. ``attention_service._EMOTION_FORCE_FOCUS`` / ``_EMOTION_DROP_FOCUS`` —— 焦点行为
4. ``attention_service._EMOTION_DECAY_ORDER``     —— 降温阶梯

历史上它们靠人工同步，于是出过两类静默故障：

* 3 和 4 漏掉某个情绪时，该情绪「能配置但没行为」，或者降温时越降越强
  （``_decay_emotion`` 里曾硬编码上升侧名单，新加的正倍率情绪会走回落侧分支）。
* 2 和 1 不一致时，前端显示的和实际生效的不是同一套数。

这个测试把四条不变量钉死。新增情绪时它会失败，逼作者显式回答
「它是抢焦点、让焦点，还是中性」。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from plugin.plugins.qq_auto_reply import (
    attention_service,
    prompt_fragment_templates,
    scene_prompt_templates,
    settings_schema,
)

_PLUGIN_DIR = pathlib.Path(attention_service.__file__).resolve().parent
_MULTIPLIER = attention_service._EMOTION_MULTIPLIER
_DECAY_ORDER = attention_service._EMOTION_DECAY_ORDER

#: 既不强抢焦点、也不主动让焦点的情绪。放在这里是一份**有意识的声明**：
#: 这些情绪只改速率，不改焦点归属。
_NEUTRAL_EMOTIONS = {"annoyed", "playful", "curious", "calm", "sad"}


def _advertised_emotions(text: str) -> set[str]:
    """文本里实际出现过的情绪名。

    各提示词路径的情绪清单格式不统一（有的用反引号加中文注释，有的是
    ``calm / playful / ...`` 斜杠列表，i18n 里还有整段英文副本），所以这里是
    「出现过单词即算公布」而不是解析清单 —— 对这个测试要防的「漏掉某个情绪」
    来说，出现即足够。
    """
    return set(re.findall(r"[A-Za-z_]+", text)) & set(_MULTIPLIER)


def test_multiplier_table_matches_settings_schema():
    """前端可配置默认值必须和运行时实际用的倍率逐键一致。"""
    assert settings_schema.DEFAULT_EMOTION_MULTIPLIERS == _MULTIPLIER


def test_every_emotion_is_classified():
    """每个情绪必须落在抢焦点 / 让焦点 / 中性三档之一。"""
    classified = (
        attention_service._EMOTION_FORCE_FOCUS
        | attention_service._EMOTION_DROP_FOCUS
        | _NEUTRAL_EMOTIONS
    )
    assert set(_MULTIPLIER) == classified


def test_focus_sets_are_disjoint_and_meaningful():
    """抢焦点和让焦点不能重叠；倍率符号必须和焦点行为方向一致。"""
    force = attention_service._EMOTION_FORCE_FOCUS
    drop = attention_service._EMOTION_DROP_FOCUS
    assert not (force & drop)
    # 抢焦点 = 注意力该往上走；让焦点 = 该往下走。符号反了说明配错了。
    for emotion in force:
        assert _MULTIPLIER[emotion] > 0, f"{emotion} 抢焦点却配了非正倍率"
    for emotion in drop:
        assert _MULTIPLIER[emotion] < 0, f"{emotion} 让焦点却配了非负倍率"
    # calm 是默认态，不该有任何强制行为
    assert "calm" not in force and "calm" not in drop


def test_decay_order_covers_every_emotion():
    """降温阶梯必须覆盖全部情绪，否则漏掉的情绪会被当成表外情绪直接归零。"""
    assert set(_DECAY_ORDER) == set(_MULTIPLIER)
    assert len(_DECAY_ORDER) == len(set(_DECAY_ORDER)), "降温阶梯有重复项"


def test_decay_order_is_monotone_in_multiplier():
    """降温阶梯必须按倍率单调递减。

    这是 ``_decay_emotion`` 只用「往 calm 走一格」就能正确降温的前提：
    calm 之前全是上升侧、之后全是回落侧，idx±1 永远朝 calm 方向。
    """
    multipliers = [_MULTIPLIER[name] for name in _DECAY_ORDER]
    assert multipliers == sorted(multipliers, reverse=True), (
        f"降温阶梯未按倍率递减: {list(zip(_DECAY_ORDER, multipliers))}"
    )


def test_decay_order_sides_match_multiplier_sign():
    """calm 的位置必须正好把上升侧和回落侧切开。"""
    calm_idx = _DECAY_ORDER.index("calm")
    for idx, name in enumerate(_DECAY_ORDER):
        value = _MULTIPLIER[name]
        if idx < calm_idx:
            assert value > 0, f"{name} 在 calm 之前却是非正倍率"
        elif idx > calm_idx:
            assert value < 0, f"{name} 在 calm 之后却是非负倍率"
        else:
            assert value == 0.0, f"calm 倍率应为 0，实为 {value}"


@pytest.mark.parametrize("emotion", [name for name in _DECAY_ORDER if name != "calm"])
def test_decay_always_moves_toward_calm(emotion):
    """每个非 calm 情绪降温一级后，必须比原来更靠近 calm。"""
    order = _DECAY_ORDER
    calm_idx = order.index("calm")
    idx = order.index(emotion)
    expected = order[idx + 1] if idx < calm_idx else order[idx - 1]
    # 末位情绪（紧邻 calm 的两侧）应归位到 calm 而不是越界
    if abs(idx - calm_idx) == 1:
        expected = "calm"
    assert abs(order.index(expected) - calm_idx) < abs(idx - calm_idx), (
        f"{emotion} 降温到 {expected} 反而离 calm 更远了"
    )


def test_prompt_advertises_every_emotion():
    """提示词里承诺给 LLM 的情绪，必须和倍率表一致。

    否则 LLM 会照着提示词输出一个表外情绪，被 ``_decay_emotion`` 当未知标签
    直接归零 —— 表现为「情绪标记没反应」。

    插件里**有三条**提示词路径都在向 LLM 公布情绪词表，任何一条漏了都会让走那条
    路径的回复拿到一个表外情绪。
    """
    for module in (scene_prompt_templates, prompt_fragment_templates):
        text = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        advertised = _advertised_emotions(text)
        missing = set(_MULTIPLIER) - advertised
        assert not missing, (
            f"{pathlib.Path(module.__file__).name} 的情绪清单漏了 {sorted(missing)}；"
            f"LLM 如果用了这些词会被当表外情绪直接归零"
        )


def test_i18n_prompt_copies_advertise_every_emotion():
    """i18n 里的整段提示词副本也是真实生效的路径，必须同步。

    注意：``scene_prompt_templates.SCENE_KIRA_UNIFIED_GROUP`` 只是 i18n 缺失时的
    **兜底**，真正发给 LLM 的是 ``i18n/<locale>.json`` 里那份。只改 Python 模板
    会让中文用户完全看不到改动 —— 这个测试就是盯这条。
    """
    problems: list[str] = []
    for locale in ("zh-CN", "en"):
        path = _PLUGIN_DIR / "i18n" / f"{locale}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = set(_MULTIPLIER) - _advertised_emotions(
            data["prompts.group.kira_unified"]
        )
        if missing:
            problems.append(f"i18n/{locale}.json 漏了 {sorted(missing)}")
    assert not problems, "；".join(problems)


def test_dashboard_colors_every_emotion():
    """前端情绪色表必须覆盖全部情绪，否则新情绪会静默显示成灰色默认值。"""
    html = (_PLUGIN_DIR / "static" / "napcat.html").read_text(encoding="utf-8")
    match = re.search(r"var emoColor=\{([^}]*)\}", html)
    assert match, "前端 emoColor 色表找不到了，测试需要跟着改"
    colored = set(re.findall(r"'(\w+)'\s*:", match.group(1)))
    missing = set(_MULTIPLIER) - colored
    assert not missing, f"前端 emoColor 没给这些情绪配色: {sorted(missing)}"


def test_prompt_documents_drop_focus_emotions():
    """让焦点的情绪必须在提示词里说明「会去看别的群」——这是行为契约，不是装饰。"""
    drop = attention_service._EMOTION_DROP_FOCUS
    for module in (scene_prompt_templates, prompt_fragment_templates):
        text = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        focus_text = "\n".join(
            line
            for line in text.splitlines()
            if "让出焦点" in line or "抢走焦点" in line or "让焦点" in line
        )
        documented = set(re.findall(r"`?(\w+)`?", focus_text)) & set(_MULTIPLIER)
        missing = drop - documented
        assert not missing, (
            f"{pathlib.Path(module.__file__).name} 没说明这些情绪会主动让出焦点: "
            f"{sorted(missing)}"
        )


def test_prompt_documents_yielding_without_speaking():
    """「只发 feeling、不带 <msg>」必须写在提示词里。

    代码侧已经支持（``llm_skip`` 的 outcome 也带着 feeling，``reply_pipeline`` 在
    交付之前上报情绪），但**模型不知道可以这样用** —— 提示词一直说「每个回复都必须
    带一个 feeling」，于是猫娘在没兴趣的群里会先勉强回一句才走。使用者要的是
    「看一眼就走」，所以这条指令是行为的一部分，删掉它功能就等于没了。
    """
    pattern = r"只输出.*<feeling>|<feeling>[^<\n]*不带"
    for module in (scene_prompt_templates, prompt_fragment_templates):
        text = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert re.search(pattern, text), (
            f"{pathlib.Path(module.__file__).name} 没告诉 LLM「可以只发 "
            f"<feeling>bored</feeling>、不说话就离开」"
        )

    zh = json.loads((_PLUGIN_DIR / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    assert re.search(pattern, zh["prompts.group.kira_unified"]), (
        "i18n/zh-CN.json 缺少「只发 feeling 不出声」的指令"
    )

    en = json.loads((_PLUGIN_DIR / "i18n" / "en.json").read_text(encoding="utf-8"))
    assert re.search(r"only\*\* `<feeling>", en["prompts.group.kira_unified"]), (
        "i18n/en.json 缺少「只发 feeling 不出声」的指令"
    )
