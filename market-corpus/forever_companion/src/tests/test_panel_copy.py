"""Panel copy contracts: readable wording without changing runtime behavior."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
PANEL_PREFIXES = ("panel.", "onboarding.", "actions.", "plugin.", "settings.anniversary.")
RETIRED_WORDING = (
    "身体",
    "主人",
    "生命状态",
    "客观评价",
    "黏人",
    "十二个",
    "翻旧账",
    "调教",
    "赌气",
    "亲密",
    "医学",
    "生理",
    "月经",
    "排卵",
)


def _locale(locale: str) -> dict[str, str]:
    return json.loads((ROOT / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))


def _action_refs() -> list[tuple[Path, ast.Call, str]]:
    refs = []
    for path in sorted((ROOT / "mixins").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("actions.")
            ):
                continue
            refs.append((path, node, node.args[0].value))
    return refs


@pytest.mark.parametrize("locale", LOCALES)
def test_all_locale_placeholders_match_chinese(locale: str) -> None:
    reference = _locale("zh-CN")
    messages = _locale(locale)
    assert set(messages) == set(reference), f"{locale}: locale key set differs from zh-CN"
    drift = []
    for key, text in messages.items():
        assert isinstance(text, str) and text.strip(), f"{locale}: empty/non-text key {key}"
        expected = set(PLACEHOLDER.findall(reference[key]))
        actual = set(PLACEHOLDER.findall(text))
        if actual != expected:
            drift.append(f"{key}: expected={sorted(expected)} actual={sorted(actual)}")
    assert not drift, f"{locale}: placeholder drift:\n" + "\n".join(drift)


def test_chinese_panel_copy_avoids_retired_wording() -> None:
    messages = _locale("zh-CN")
    panel_copy = {key: text for key, text in messages.items() if key.startswith(PANEL_PREFIXES)}
    assert panel_copy, "No panel copy was selected"
    problems = [
        f"{key}: {word}"
        for key, text in panel_copy.items()
        for word in RETIRED_WORDING
        if word in text
    ]
    assert not problems, "Retired panel wording:\n" + "\n".join(problems)


def test_capability_labels_share_chinese_source() -> None:
    from forever_companion.core.capabilities import CAPABILITY_SPECS

    messages = _locale("zh-CN")
    for capability, spec in CAPABILITY_SPECS.items():
        key = f"panel.features.cap.{capability}.label"
        assert messages[key] == spec.label, f"{capability}: label differs from Python source"


def test_mixin_action_defaults_match_chinese_locale() -> None:
    messages = _locale("zh-CN")
    refs = _action_refs()
    assert refs, "No actions.* references found in mixins"
    problems = []
    for path, node, key in refs:
        location = f"{path.relative_to(ROOT)}:{node.lineno} {key}"
        defaults = [argument.value for argument in node.keywords if argument.arg == "default"]
        if len(defaults) != 1:
            problems.append(f"{location}: needs one explicit default")
            continue
        default = defaults[0]
        if not isinstance(default, ast.Constant) or not isinstance(default.value, str):
            problems.append(f"{location}: default must be a text literal")
        elif messages.get(key) != default.value:
            problems.append(f"{location}: default differs from zh-CN")
    assert not problems, "Action defaults out of sync:\n" + "\n".join(problems)


def test_plugin_description_matches_chinese_locale() -> None:
    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    assert manifest["plugin"]["description"] == _locale("zh-CN")["plugin.description"]


def test_copy_update_does_not_reopen_completed_onboarding() -> None:
    from forever_companion.core.onboarding import _GUIDE_VERSION, wizard_pending

    assert _GUIDE_VERSION == "2"
    assert not wizard_pending({"wizard": "done", "version": "2"})
    assert not wizard_pending({"wizard": "skip", "version": "2"})


@pytest.mark.parametrize(
    ("key", "scope"),
    (
        ("actions.clear_diary.confirm", ("当前角色", "时光日记", "手记", "对话片段")),
        ("actions.clear_review.confirm", ("当前角色", "我的日记", "归档", "素材")),
        ("actions.clear_stats.confirm", ("当前角色", "相处统计", "起点", "热力图", "月报")),
        ("actions.delete_diary_item.confirm", ("这条", "对话片段")),
        ("actions.gallery_remove.confirm", ("图库", "这项壁纸", "如果正在使用", "取消壁纸")),
        ("actions.prune_lanlan.confirm", ("角色", "残留数据", "周期", "情绪", "日记", "旧版周记")),
        (
            "actions.reset.confirm",
            ("当前角色", "快进天数", "情绪状态", "时光日记", "个人日记", "我的日记", "归档", "素材", "相处统计", "待写"),
        ),
    ),
)
def test_destructive_confirmations_keep_warning_and_scope(key: str, scope: tuple[str, ...]) -> None:
    text = _locale("zh-CN")[key]
    assert "无法恢复" in text or "不可恢复" in text, f"{key}: irreversible warning missing"
    assert all(part in text for part in scope), f"{key}: destructive scope is incomplete"


def test_reset_confirmation_distinguishes_preserved_records() -> None:
    text = _locale("zh-CN")["actions.reset.confirm"]
    assert "模拟开关" in text and "默认设置" in text
    statements = re.split(r"[。；]", text)
    assert any(
        all(part in statement for part in ("周期起点", "参数", "全局设置", "壁纸", "其他角色"))
        and "保留" in statement
        for statement in statements
    ), "Reset must distinguish preserved settings, wallpapers and other characters"


def test_clear_statistics_keeps_diaries_separate() -> None:
    text = _locale("zh-CN")["actions.clear_stats.confirm"]
    assert "三本日记" in text and "不受影响" in text


def test_review_copy_identifies_model_generation_not_character_writing() -> None:
    messages = _locale("zh-CN")
    hint = messages["panel.review.hint"]
    assert "模型" in hint and "相处记录" in hint
    assert "不是" in hint and "评分" in hint
    purpose = messages["panel.capintro.review.purpose"]
    assert "模型" in purpose and "不是角色亲笔" in purpose and "偏差" in purpose
    assert "模型" in messages["panel.review.emptyDesc"]
    assert "偏差" in messages["panel.review.emptyDesc"]
    assert "后台模型" in messages["panel.review.writingHint"]
    for key in ("panel.review.writeNow", "actions.write_review_now.label"):
        assert "生成" in messages[key] and "邀请" not in messages[key], key


def test_personal_journal_copy_distinguishes_manual_generation_from_auto_invitation() -> None:
    messages = _locale("zh-CN")
    for key in ("panel.journal.invite", "actions.invite_journal.label"):
        assert "邀请" in messages[key] and "日记" in messages[key], key
    purpose = messages["panel.capintro.journal.purpose"]
    assert "角色口吻" in purpose and "后台模型" in purpose and "直接生成" in purpose
    limit = messages["panel.capintro.journal.limit2"]
    assert "自动邀请" in limit and "手动写作" in limit and "不再等待" in limit
    pending = messages["panel.journal.invitePending"]
    assert "邀请" in pending and "角色" in pending and "决定是否" in pending
    assert "直接生成" in messages["panel.journal.emptyDesc"]


@pytest.mark.parametrize("capability", ("tone_sense", "fragments", "review"))
def test_model_backed_features_disclose_external_services(capability: str) -> None:
    messages = _locale("zh-CN")
    limits = " ".join(
        text for key, text in messages.items()
        if key.startswith(f"panel.capintro.{capability}.limit")
    )
    assert "外部服务" in limits, f"{capability}: external model disclosure missing"
