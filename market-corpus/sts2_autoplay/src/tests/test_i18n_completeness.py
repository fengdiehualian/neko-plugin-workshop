"""随包语言的完整性：英文兜底会**直接漏到玩家眼前**。

实测事故：奖励屏的即时弹幕显示成 `Prefer rewards that patch the weaknesses of the current deck.`
—— `zh-CN.json` 有 `companion.suggestion.reward_preferred` 却漏了 `reward_default`，
于是走到那条兜底分支时用了 en 的原文。

这类缺口没人会主动发现（其它场景都正常，只有某一条分支露英文），所以用测试守住：
**en.json 里的 `companion.*` 键，所有随包语言都必须有译文。**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

I18N_DIR = Path(__file__).resolve().parents[1] / "i18n"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# 随包发布的语言（en 是兜底原文，其余都要译全）
LOCALES = ("zh-CN", "zh-TW", "ja", "ko", "es", "pt", "ru")


def _load(locale: str) -> dict:
    with open(I18N_DIR / f"{locale}.json", "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
def test_i18n_directory_and_english_baseline_exist() -> None:
    assert I18N_DIR.is_dir(), f"找不到 i18n 目录: {I18N_DIR}"
    base = _load("en")
    assert any(key.startswith("companion.") for key in base), "en.json 里应该有 companion.* 键"


@pytest.mark.unit
@pytest.mark.parametrize("locale", LOCALES)
def test_every_companion_key_is_translated(locale: str) -> None:
    base = _load("en")
    translated = _load(locale)
    missing = sorted(
        key for key in base
        if key.startswith("companion.") and key not in translated
    )

    assert missing == [], f"{locale}.json 缺这些 companion.* 译文（会退回英文原文）: {missing}"


@pytest.mark.unit
def test_reward_default_is_translated_in_chinese() -> None:
    """单独钉住闯过祸的那一条：它就是被即时弹幕直接念出来的文案。"""
    key = "companion.suggestion.reward_default"

    for locale in ("zh-CN", "zh-TW"):
        value = str(_load(locale).get(key) or "")
        assert value, f"{locale}.json 缺 {key}"
        assert not value.startswith("Prefer "), f"{locale} 的 {key} 还是英文原文"


@pytest.mark.unit
def test_plugin_toml_pins_the_default_locale_to_chinese() -> None:
    """**"弹幕说英文"的根因回归测试。**

    SDK 的 `PluginI18n.t()` 不传 locale 时只按 `default_locale` 查表，而该默认值是 `"en"`
    —— 于是 en.json 里存在的键一律说英文（en 里没有的键才退回代码里的中文 default，
    所以同一屏里半中半英）。插件侧拿不到运行时语区，只能把默认语区显式钉成中文。
    """
    text = (PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")

    assert "[plugin.i18n]" in text, "plugin.toml 必须有 [plugin.i18n] 段"
    section = text.split("[plugin.i18n]", 1)[1].split("[", 1)[0]
    assert 'default_locale = "zh-CN"' in section, "默认语区必须是中文，否则 en.json 里的键全说英文"
