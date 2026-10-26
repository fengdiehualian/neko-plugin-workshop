"""status.html 的四个操作控件必须**搬出标题栏**，并且**任何滚动位置都够得着**。

**为什么要有这条**：`status.html` 有 6 张卡片（连接方式 / 开机自启 / 一键部署 /
登录二维码 / 扫码绑定 / 信任名单）。自动刷新、刷新、刷新二维码、返回 这四个控件原本
和标题挤在同一个 `<header>` 里（`flex-wrap`，窄一点就折行），使用者原话是
"太丑了"，而且滑到底想刷新还得先一路滚回顶部。

现在的结构是：`<header>` 只留猫娘标记 + 标题，四个控件搬进 `#actions`，
`#actions` 用 **`position:sticky; top:0`** 贴在视口顶部。

选 sticky 而不是 fixed 是刻意的：**sticky 不脱离文档流**，不会横向压住内容，
也不需要给 `.wrap` 预留遮挡空间（上一版用 fixed 浮动按钮，就得靠
`padding-bottom` 才能不盖住最后一张卡片）。这条也一并钉住了。

真正的交互行为（滚到底时操作条仍在视口内、点击真的调 load）由
`.dsh-artifacts/verify-fab.py` 用 Playwright 验，需要浏览器，不适合放进单测。
"""

from __future__ import annotations

import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parents[1]
STATUS = (BASE / "static" / "status.html").read_text(encoding="utf-8")

#: 四个控件各自的 i18n 键 —— 都是**已有**键，搬位置时不该换。
CONTROLS = {
    "auto": 'data-i18n="ui.status.auto"',
    "refresh": 'data-i18n="ui.status.refresh"',
    "refresh_qr": 'data-i18n="ui.status.refresh_qr"',
    "back": 'data-i18n="ui.status.back"',
}

_HEADER = re.search(r"<header>(.*?)</header>", STATUS, re.S)
_ACTIONS = re.search(r'<div id="actions">(.*?)</div>', STATUS, re.S)
_ACTIONS_CSS = re.search(r"#actions\s*\{(.*?)\}", STATUS, re.S)


def test_header_still_holds_the_identity():
    """标题栏只留标记 + 标题 —— 别把它改空了。"""
    assert _HEADER, "找不到 <header>"
    inner = _HEADER.group(1)
    assert "mascot" in inner, "标题栏应当保留猫娘标记"
    assert 'id="title"' in inner, "标题栏应当保留标题"


def test_the_four_controls_are_not_in_the_header():
    """这条就是使用者抱怨的那件事：四个控件别再挤在标题旁边。"""
    assert _HEADER, "找不到 <header>"
    inner = _HEADER.group(1)
    left_behind = [name for name, key in CONTROLS.items() if key in inner]
    assert not left_behind, (
        f"这些控件还留在 <header> 里: {left_behind} —— "
        "它们应当搬进 #actions（使用者反馈挤在标题旁边太丑）"
    )


def test_all_four_controls_live_in_the_action_bar():
    assert _ACTIONS, '找不到 <div id="actions"> —— 四个控件没有落点'
    inner = _ACTIONS.group(1)
    missing = [name for name, key in CONTROLS.items() if key not in inner]
    assert not missing, f"#actions 里缺少这些控件: {missing}"


def test_the_action_bar_sticks_to_the_top():
    """`sticky` 是关键：换回 static 就又要滚回顶部才能点刷新。"""
    assert _ACTIONS_CSS, "找不到 #actions 的样式规则"
    body = _ACTIONS_CSS.group(1)
    assert re.search(r"position\s*:\s*sticky", body), (
        "#actions 必须是 position:sticky，否则滚到底就点不到刷新"
    )
    assert re.search(r"top\s*:\s*0", body), (
        "#actions 需要 top:0，否则贴不住视口顶部"
    )


def test_it_is_sticky_not_fixed():
    """sticky 不脱离文档流，所以不会压住内容、也不需要预留遮挡空间。

    如果哪天有人改成 fixed，就必须同时补上 .wrap 的 padding-bottom；
    这条先红一下，提醒他别只改一半。"""
    assert _ACTIONS_CSS, "找不到 #actions 的样式规则"
    assert not re.search(r"position\s*:\s*fixed", _ACTIONS_CSS.group(1)), (
        "#actions 用的是 fixed —— 那会脱离文档流压住内容，"
        "需要同时给 .wrap 留出遮挡空间；这里刻意选的是 sticky"
    )


def test_refresh_is_wired_to_the_same_load():
    """按钮旁边必须有接线，而且是同一个 load()，不能是另一套刷新逻辑。"""
    assert re.search(
        r"getElementById\('btn-refresh'\)\.addEventListener\('click',\s*load\)", STATUS
    ), "刷新按钮没有接到 load()"


def test_no_new_i18n_keys_were_introduced():
    """搬位置不该顺手改文案键 —— 新增键会让 i18n 覆盖率看门狗要求补全所有语言包。"""
    for key in CONTROLS.values():
        assert STATUS.count(key) == 1, f"{key} 出现了 {STATUS.count(key)} 次（应当恰好 1 次）"
