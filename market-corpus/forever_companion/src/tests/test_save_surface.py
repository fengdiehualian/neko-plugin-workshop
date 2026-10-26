# -*- coding: utf-8 -*-
"""面板保存面契约门（1.3.2）——"一页两个保存钮"的回归防线。

bug 的形状（实机反馈）：设置页既能改通用设置（时区/调试模式）、又能改面板外观，
但**通用设置在这页根本没有落点**——保存条只长在周期/情绪/日记三页，想存时区得先
换个页签点「保存设置」；而外观卡自带一枚「保存外观」。用户看到的是"这页只有一个
保存钮，它叫保存外观"，于是以为改过的设置没地方存、或以为按它就全存了。

本轮收口为**一页一枚、语义唯一**：

1. 外观落盘（``set_panel_appearance``）在 ui/ 里只准出现一次，且就在 panel.tsx
   的统一保存里——外观卡只留实时预览与「还原」，不得再自建写通道。
2. 设置页必须有保存条（ManagePane 的 footer 槽），且它是**唯一**一趟同时落
   设置与外观的入口（``saveSettingsPage``）；周期/情绪/日记三页的保存条
   维持只存设置（``saveSettings``），口径由主人 2026-09-20 拍板。
3. 随本轮淘汰的三键（卡内保存钮文案/在飞文案/外观成功 toast）在 ui/ 与 i18n/
   整文件钉死不得复活——复活即说明有人把钮又加了回来。

扫描面限定 ui/ 与 i18n/（本测试文件自身不在内，故可直接书写被钉死的字面量）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
I18N_DIR = ROOT / "i18n"

# 本轮淘汰的 i18n 键（八语同删）：卡内保存钮、钮上在飞文案、外观单独成功 toast
RETIRED_KEYS = [
    "panel.appearance.saveBtn",
    "panel.appearance.savingNow",
    "panel.appearance.applied",
]


def _read(rel: Path) -> str:
    return rel.read_text(encoding="utf-8")


def _ui_files() -> list[Path]:
    return sorted(p for p in UI_DIR.glob("*.ts*") if p.is_file())


def _count(rel: Path, needle: str) -> int:
    return _read(rel).count(needle)


def test_appearance_write_channel_is_unique() -> None:
    """外观只经 panel.tsx 一处落盘；外观卡不得自持写通道（第 1 条）。"""
    hits = {p.name: _count(p, '"set_panel_appearance"') for p in _ui_files()}
    nonzero = {name: n for name, n in hits.items() if n}
    assert nonzero == {"panel.tsx": 1}, f"外观落盘出口应唯一在 panel.tsx，实得 {nonzero}"

    card = UI_DIR / "appearance.tsx"
    assert _count(card, '"update_settings"') == 0, "外观卡不该写设置通道"
    assert "api.call" not in _read(card), "外观卡不该自己发入口调用（落盘归 panel.tsx）"


def test_settings_page_has_one_save_bar_wired_to_unified_handler() -> None:
    """设置页必须有保存条，且管两摊（第 2 条）。"""
    panel = _read(UI_DIR / "panel.tsx")
    assert panel.count("footer={<SaveBar") == 1, "设置页必须且只能注入一条保存条"
    assert 'onSave={saveSettingsPage}' in panel, "设置页保存条要接统一保存（设置+外观）"
    assert _count(UI_DIR / "manage.tsx", "{props.footer}") == 1, "ManagePane 必须有 footer 落点"

    # 统一保存里两半都在：设置走 postSettings、外观走 postAppearanceDraft
    body = panel.split("async function saveSettingsPage", 1)
    assert len(body) == 2, "找不到 saveSettingsPage"
    handler = body[1].split("\n  // ", 1)[0]
    assert "await postSettings()" in handler, "统一保存漏了设置半"
    assert "await postAppearanceDraft()" in handler, "统一保存漏了外观半"
    # 外观没动过就不发第二趟（省一次无谓写盘）
    assert re.search(r"if \(apDirty\)", handler), "外观半缺 dirty 闸"


def test_other_settings_pages_still_save_settings_only() -> None:
    """周期/情绪/日记三页维持只存设置（口径：只有设置页那枚管外观）。"""
    panel = _read(UI_DIR / "panel.tsx")
    assert panel.count("onSave={saveSettings} />") == 3, f"应恰有三页各一条保存条，实得 {panel.count('onSave={saveSettings} />')}"
    assert panel.count("<SaveBar") == 4, "全盘面共四枚保存条（三页 + 设置页 footer）"


def test_retired_button_copy_stays_dead() -> None:
    """三枚淘汰键整文件钉死（含注释），复活即红。"""
    scanned = [p for p in _ui_files()] + [p for p in sorted(I18N_DIR.glob("*.json"))]
    problems: list[str] = []
    for path in scanned:
        text = _read(path)
        for key in RETIRED_KEYS:
            if key in text:
                problems.append(f"{path.relative_to(ROOT)}: {key}")
    assert not problems, f"已淘汰的卡内保存文案复活了: {problems}"
