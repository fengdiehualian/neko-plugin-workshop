"""提示词预算看门狗：把「每轮都在发、还按 token 收费」的那几段钉住。

**为什么要有这条**：QQ 插件的 system prompt 是**每轮请求都重发**的（宿主把
instructions 当 system 消息放进请求），而免费线**没有 prompt caching**
（`get_cache_kwargs('https://www.lanlan.tech/text/v1')` → 无 cache 头），所以这段
文本按全价计费。实测（2026-09-25，5 份真实群聊样本，见 docs/SESSION-HANDOFF.md §4.0u）：

    固定部分 ≈ 9,480 字符（输出格式 4,190 / 人设 3,217 / 群聊回复意愿 1,652 / …）
    变动部分 = 核心记忆 0~6,892 字符
    整段 11.1k~18.6k 字符 ≈ 7.9k~13.2k tokens（cl100k 实测 0.71 token/字符）

这段文本是靠**人肉加规则**长到 18k 的（每条规则当初都对应一个真实故障），
所以它会继续长：没有预算闸时，谁都不会发现。这里钉四类东西：

1. **预算**：固定模板有上限，涨上去就红，逼着「加规则」时先想能不能换掉旧的。
2. **目录条目上限**：两个目录是提示词里最贵的一块（实测占「输出格式」段的 49%），
   但条目本身是**能力面**（模型只能挑目录里出现过的 id），所以只允许「有上限的
   截断 + 留日志」，不允许无上限增长。
3. **模板里不许有 HTML 注释**：模板字符串整个进 system prompt，注释里的标签清单
   照样被发出去，模型会当成可用标签 —— 实测踩过（rps/dice/contact/music/mface/file）。
4. **情绪清单必须在**：`bored` 是让猫娘离开无聊群的情绪，从两处都删掉不会报错，
   只会让这个情绪永远用不出来。
"""

from __future__ import annotations

import json
import logging
import pathlib
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import prompt_fragment_templates as frag
from plugin.plugins.qq_auto_reply import scene_prompt_templates as scene
from plugin.plugins.qq_auto_reply.session_instruction_service import (
    QQSessionInstructionService,
)

#: 「固定模板」= 与记忆开关无关、每轮都在的静态层之和。
#:
#: 2026-09-27 审计**前**实测 5,971（上限当时是 6,100，几乎没余量）。那次删掉：
#: 角色设定段 91（与本体人设冲突）、时间信息段 342（死文本）、旧纯文本 Format 141、
#: 三个死场景模板 1,713，并把注意事项/细节约束/输出要求里与人设重复的句子删掉
#: （700→491、128→60、111→68）。
#:
#: 之后又长过两次，都记在这里（**上限是显式决定，不许静默抬**）：
#:   · 颜文字收紧（2026-09-27「颜文字也尽量少发」）：+68 → 实测 **5,286**，
#:     当时没有回头改这条注释，于是"预算余量"其实只剩 14 字符；
#:   · 问句预算（2026-09-27「别用提问代替回应」）：并进细节约束**已有的那条**，
#:     +47 → 实测 **5,333**。
#: 两条都是使用者点名要的行为规则、且能并进已有段（没有再起一段、没有重复收费），
#: 所以上限抬到 5,350（留 17 余量）。**下一次加规则前先想能不能换掉一条旧的。**
FIXED_TEMPLATE_BUDGET = 5_350
CATALOG_BUDGET = 2_200

FIXED_TEMPLATE_NAMES = (
    "ATTENTION_PROMPT_SECTION",
    "FORMAT_PROMPT_SECTION_NEKO_DYNAMIC",
    "CHARACTER_PROMPT_SECTION",
    "CHAT_ENV_PROMPT_SECTION",
    "USER_PROFILE_PROMPT_SECTION",
    "DETAIL_CONSTRAINTS_SECTION",
    "OUTPUT_PROMPT_SECTION",
    "ACCOUNTS_PROMPT_SECTION",
    "SESSIONS_PROMPT_SECTION",
)


def _fixed_template_chars() -> int:
    return sum(len(getattr(frag, name)) for name in FIXED_TEMPLATE_NAMES) + len(
        scene.SCENE_KIRA_UNIFIED_GROUP
    )


def test_fixed_templates_stay_within_budget():
    total = _fixed_template_chars()
    assert total <= FIXED_TEMPLATE_BUDGET, (
        f"固定提示词层涨到 {total} 字符（上限 {FIXED_TEMPLATE_BUDGET}）。"
        "加规则前先看 docs/SESSION-HANDOFF.md §4.0u 的实测表：能不能换掉一条旧的，"
        "或者把新规则并进已有的段。"
    )


def test_the_shipped_templates_carry_no_html_comment():
    """模板里的 `<!-- … -->` 会被原样发出去（实测），不许再出现。"""
    offenders = [
        f"{module.__name__}.{name}"
        for module in (frag, scene)
        for name, value in vars(module).items()
        if isinstance(value, str) and "<!--" in value
    ]
    assert not offenders, f"这些模板里还有注释块，会连同没用到的标签一起发出去: {offenders}"


def test_unsupported_tags_are_not_advertised():
    """没接后端的标签不许出现在提示词里 —— 模型会照着用，然后被静默丢弃。"""
    corpus = frag.FORMAT_PROMPT_SECTION_NEKO_DYNAMIC + frag.FORMAT_PROMPT_SECTION_OPEN_PLATFORM
    advertised = [
        tag for tag in ("<rps/>", "<dice/>", "<contact", "<music", "<mface", "<file ")
        if tag in corpus
    ]
    assert not advertised, f"提示词里在宣传没实现的消息类型: {advertised}"


def test_emotion_list_still_reaches_the_prompt():
    """`bored` 必须至少在一个「群聊会用到」的段里被列出来。"""
    corpus = frag.FORMAT_PROMPT_SECTION_NEKO_DYNAMIC + scene.SCENE_KIRA_UNIFIED_GROUP
    for emotion in ("calm", "bored", "sulking", "arguing"):
        assert emotion in corpus, f"情绪清单里没有 {emotion}"


# ── 目录上限 ────────────────────────────────────────────────────

def _service(tmp_path: pathlib.Path, stickers: dict) -> QQSessionInstructionService:
    (tmp_path / "sticker.json").write_text(
        json.dumps(stickers, ensure_ascii=False), encoding="utf-8",
    )
    plugin = SimpleNamespace(
        logger=logging.getLogger("qq.test"),
        data_path=lambda name: tmp_path / name,
        i18n=SimpleNamespace(t=lambda key, default=None: default or key),
    )
    return QQSessionInstructionService(plugin)


def test_sticker_catalog_is_capped_and_logged(tmp_path, caplog):
    stickers = {str(i): {"desc": f"描述{i}", "path": f"{i}.png"} for i in range(1, 81)}
    service = _service(tmp_path, stickers)

    with caplog.at_level(logging.INFO, logger="qq.test"):
        catalog = service._load_sticker_catalog()

    assert len(catalog.splitlines()) == QQSessionInstructionService.MAX_STICKER_CATALOG_ENTRIES
    assert any("表情包目录超过" in r.message for r in caplog.records), (
        "截断必须留日志 —— 否则用户只会觉得「模型不选我新传的图」"
    )


def test_sticker_catalog_keeps_everything_under_the_cap(tmp_path):
    stickers = {str(i): {"desc": f"描述{i}", "path": f"{i}.png"} for i in range(1, 21)}
    service = _service(tmp_path, stickers)

    catalog = service._load_sticker_catalog()

    assert len(catalog.splitlines()) == 20, "没超上限就不许截断（那是能力面）"


def test_emoji_catalog_is_capped(tmp_path):
    service = _service(tmp_path, {})
    catalog = service._load_emoji_catalog()

    lines = catalog.splitlines()
    assert 1 <= len(lines) <= QQSessionInstructionService.MAX_EMOJI_CATALOG_ENTRIES, (
        f"emoji 目录 {len(lines)} 行超过上限 "
        f"{QQSessionInstructionService.MAX_EMOJI_CATALOG_ENTRIES}"
    )
    assert ":" in lines[0], "emoji 目录格式（id: 名称）不能变"


def test_catalogs_stay_within_budget(tmp_path):
    stickers = {str(i): {"desc": "猫耳少女发呆，适用于无语放空场景。", "path": "x.png"}
                for i in range(1, 61)}
    service = _service(tmp_path, stickers)

    total = len(service._load_sticker_catalog()) + len(service._load_emoji_catalog())
    assert total <= CATALOG_BUDGET, (
        f"两个目录合计 {total} 字符（上限 {CATALOG_BUDGET}）——它们是提示词里最贵的一块之一"
    )
