# -*- coding: utf-8 -*-
"""看门狗：前端不得再提供已删除的权限级别。

`open` 级在权限收敛那轮被删除（原本是「按概率直接回复」，语义并入 trusted）。
后端有别名把残留的 "open" 归一成 trusted，所以**后端不会报错** —— 这正是危险之处：
前端还列着这个选项的话，用户选了它界面也不报错，但语义已经不是他以为的那个。
真机就是踩在这个盲区上：`status.html` 与 `napcat.html` 清了，
`open_platform.html` 与 `script.js` 漏了。
"""

from __future__ import annotations

import pathlib

from _ui_source import fn_body

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]

#: 前端文件里**不允许**再出现的两种写法（都是"提供 open 级别"的历史形态）。
FORBIDDEN = ('value="open"', "'open', 'open'")

FRONTENDS = ("static/napcat.html", "static/open_platform.html", "static/status.html", "static/script.js")


def test_frontend_does_not_offer_the_removed_open_level():
    offenders: list[str] = []
    for rel in FRONTENDS:
        p = PLUGIN_DIR / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for needle in FORBIDDEN:
            if needle in text:
                offenders.append(f"{rel}: {needle}")
    assert not offenders, "前端还在提供已删除的权限级别 open：\n" + "\n".join(offenders)


def test_backend_still_normalizes_legacy_open_level():
    """后端保留别名（老配置不用改），所以这条看门狗不能顺手把别名也删了。"""
    from plugin.plugins.qq_auto_reply.group_permission import GroupPermissionManager

    assert GroupPermissionManager._normalize_level("open") == "trusted"
    assert GroupPermissionManager._normalize_level("truth") == "trusted"


# ── 群默认档：名单里的群＝信任群 ──────────────────────────────────
#
# `normal` 的语义是「没被 @ 时就只按 low 概率转发给主人」，接近不说话。把它当
# 新增群聊的默认档，用户「加了群」却看不到她开口 —— 真机踩过这个静默默认。
# 显式选 `normal` 的入口必须留着，只是不能再是默认档。

def _select_snippet(text: str, select_id: str) -> str:
    start = text.index(f'id="{select_id}"')
    return text[start:text.index("</select>", start)]


def test_group_modal_defaults_to_trusted_in_napcat_panel():
    napcat = (PLUGIN_DIR / "static/napcat.html").read_text(encoding="utf-8")
    assert "showGroupModal('','trusted',null,null)" in napcat, "新增群聊的默认档还是普通群"
    assert "showGroupModal('','normal'" not in napcat
    # 选项现在由 levelOptionsHtml 在运行时生成，静态文本里没有 <option> 可查 ——
    # 「默认档」就落在它的第三个实参上。
    body = fn_body(napcat, "showGroupModal") or ""
    assert body, "找不到 showGroupModal"
    assert "levelOptionsHtml(" in body, "群弹窗的级别下拉没走唯一生成器"
    assert "],level,'trusted')" in body, "群弹窗的选中回退没跟着默认档走"
    assert "['normal'" in body, "显式选择普通群的入口不能删"


# ── 编辑弹窗必须回显**现有**档位（2026-10-02 真机） ─────────────────
#
# 现象：点「信任群聊」某行的「编辑」，级别下拉显示成「普通群」，不是这个群真实的档位。
# 根因是 HTML 的 selected 规则 —— 同一个 select 里**多个 selected 取最后一个**：老代码
# 给 trusted 标了一次 selected，又在「级别为空或等于 trusted」时给 normal 标了一次，
# 于是 trusted 群一打开就是 normal。用户只想改转发概率，一保存就把群降级了
# （`group_add` 整条覆盖，level 一起写回去；normal 群 = 只有被 @ 才按概率转发）。
#
# 不变量：级别下拉只经 `levelOptionsHtml` 生成，它按**唯一 value** 匹配，最多一个 selected。


def test_level_dropdowns_emit_exactly_one_selected_option():
    text = (PLUGIN_DIR / "static/napcat.html").read_text(encoding="utf-8")
    helper = fn_body(text, "levelOptionsHtml") or ""
    assert helper, "napcat.html 里没有 levelOptionsHtml —— 级别下拉又散回各弹窗手写了"
    assert helper.count("' selected'") == 1, (
        "生成器里出现了不止一处 selected 拼接 —— 多出来的那个会把真实档位盖掉"
    )
    for fn in ("showGroupModal", "showUserModal"):
        body = fn_body(text, fn) or ""
        assert body, f"找不到 {fn}"
        assert "levelOptionsHtml(" in body, f"{fn} 的级别下拉没走唯一生成器"
        assert "' selected'" not in body, (
            f"{fn} 又手写 selected：同一个 select 里多个 selected 时最后一个胜出"
        )


def test_modals_feed_the_current_level_into_the_dropdown():
    """回显的输入必须是**该条目现有的级别**（群用 level，用户用同一套归一化后的 lvl）。

    这两处接线对了，「只改概率」就不会顺带改档：提交的 `level` 正是下拉回显出来的那个值。
    """
    text = (PLUGIN_DIR / "static/napcat.html").read_text(encoding="utf-8")
    group = fn_body(text, "showGroupModal") or ""
    assert "],level,'trusted')" in group, "群弹窗没把现有档位喂给下拉 —— 编辑时必然显示错档"
    user = fn_body(text, "showUserModal") or ""
    assert "]],lvl,'trusted')" in user, "用户弹窗没把现有档位喂给下拉"
    for value in ("admin", "trusted", "normal", "blacklist"):
        assert f"['{value}'" in user, f"用户弹窗丢了 {value} 这一档"


def test_group_modal_defaults_to_trusted_in_open_platform_panel():
    op = (PLUGIN_DIR / "static/open_platform.html").read_text(encoding="utf-8")
    snippet = _select_snippet(op, "mg-level")
    trusted_at = snippet.index('value="trusted"')
    normal_at = snippet.index('value="normal"')
    assert "selected" in snippet[trusted_at:normal_at], "开放平台的群弹窗默认还是普通群"
    assert "selected" not in snippet[normal_at:], "普通群又被写成默认档"


def test_other_group_entry_points_keep_trusted_first():
    """status.html / script.js 的群级别选项本来就是 trusted 打头，代码取第一项当默认。"""
    snippet = _select_snippet((PLUGIN_DIR / "static/status.html").read_text(encoding="utf-8"), "g-level")
    assert snippet.index('value="trusted"') < snippet.index('value="normal"')
    assert "selected" not in snippet, "selected 会盖掉「第一项即默认」这个约定"
    script = (PLUGIN_DIR / "static/script.js").read_text(encoding="utf-8")
    assert ": [['trusted', 'trusted'], ['normal', 'normal']]" in script, "群级别选项列表被改了"
    assert "levelSelect.value = String(item?.level || options[0][0])" in script, "新条目的默认档不再取第一项"
