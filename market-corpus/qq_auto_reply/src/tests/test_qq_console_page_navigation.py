# -*- coding: utf-8 -*-
"""看门狗：控制台页之间的跳转必须「每次进入都拿新的一份」，不许再手写 ?v=N。

为什么值得钉：

1. 用户明确要求这两个页面能回到 `index.html`（接入方式选择页）。它们是**被跳转进来的**，
   页内导航（侧栏）只切页内分节，没有回上层的出口 —— 只能靠浏览器后退。
2. 静态页的响应头是 `public, max-age=3600`（**强缓存 1 小时**）。以前靠手写 `?v=N` 击穿，
   数字散落在 index 与两页互相跳转的链接里，**只提一处**就会让用户拿到「新页面 + 旧缓存」
   的混合版本，而且现象是"改了没生效"，很难查。
3. 使用者拍板（2026-09-27）：「在 index 打开子级页面的时候能不能刷新一次」。于是改成
   `static/nav.js` 在**点击那一刻**生成版本号（`?v=<Date.now()>`）—— 每次进子页都是新的
   URL，浏览器必然重新取；手写版本号直接退休。回程（返回首页）同样带：index.html 自己
   也是强缓存的，回程不带的话改完 index 仍会看到旧的一份。
"""

from __future__ import annotations

import pathlib
import re

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
PAGES = ("static/napcat.html", "static/open_platform.html")

#: 跳转助手。
NAV_HELPER = "static/nav.js"

#: 会互相跳转的页面 → 它**必须**带 data-nav 指向的目标（含回程）。
EXPECTED_NAV: dict[str, tuple[str, ...]] = {
    "static/index.html": ("status.html", "napcat.html", "open_platform.html", "old.html"),
    "static/napcat.html": ("index.html", "open_platform.html"),
    "static/open_platform.html": ("index.html", "napcat.html"),
    "static/status.html": ("index.html",),
}


def _text(rel: str) -> str:
    return (PLUGIN_DIR / rel).read_text(encoding="utf-8")


def test_both_console_pages_have_a_back_to_index_control():
    for rel in PAGES:
        html = _text(rel)
        assert 'href="index.html"' in html, f"{rel} 没有返回 index.html 的链接"
        assert 'id="btn-back-index"' in html, f"{rel} 的返回入口缺少稳定 id（前端/测试都靠它定位）"
        assert "ui.shared.topbar.back_index" in html, f"{rel} 的返回入口没接 i18n"


def test_back_index_label_exists_in_both_bundles():
    import json

    for name in ("zh-CN.json", "en.json"):
        data = json.loads((PLUGIN_DIR / "i18n" / name).read_text(encoding="utf-8"))
        assert data.get("ui.shared.topbar.back_index"), f"{name} 缺 ui.shared.topbar.back_index"


def test_page_to_page_links_go_through_the_nav_helper():
    """每个页面间跳转都必须挂 data-nav，并且所在页加载了 nav.js。

    这条替掉了原来的「?v= 数字三处一致」—— 现在没有数字可对了，改对**机制**。
    """
    helper = _text(NAV_HELPER)
    for rel, targets in EXPECTED_NAV.items():
        html = _text(rel)
        # 认的是 <script src="…nav.js"> 这个**标签**，不是"文本里提过 nav.js"（注释里提一句不算）
        assert re.search(r'<script[^>]+src="[^"]*nav\.js', html), (
            f"{rel} 没有加载 {NAV_HELPER}（跳转就带不上新版本号了）"
        )
        for target in targets:
            assert f'data-nav="{target}"' in html, (
                f"{rel} 缺少 data-nav=\"{target}\" —— 这条跳转不会带版本号，"
                f"用户会拿到强缓存里那份旧页面"
            )
    assert "data-nav" in helper, "nav.js 没有接管 data-nav 的跳转"
    assert "qq_connection_mode" in helper, "nav.js 丢了 data-nav-mode 那半（原来写在 index 的 go() 里）"


def test_navigation_versions_are_generated_at_click_time():
    """不许再写死 `.html?v=<数字>`，版本号必须由 nav.js 运行时生成。"""
    offenders: list[str] = []
    for rel in ("static/index.html", *PAGES, "static/status.html"):
        for hit in re.findall(r"[A-Za-z_]+\.html\?v=\d+", _text(rel)):
            offenders.append(f"{rel}: {hit}")
    assert not offenders, (
        "这些跳转又变成了手写版本号（要靠人记得 +1，忘了就看到旧页面）：\n" + "\n".join(offenders)
    )
    assert 'onclick="go(' not in _text("static/index.html"), (
        "index.html 里还有老的 go(...) 内联跳转 —— 那条不带版本号"
    )

    helper = _text(NAV_HELPER)
    assert "Date.now()" in helper, "nav.js 的版本号不是运行时生成的（写死等于没击穿缓存）"
    assert "'v='" in helper, "nav.js 没有把版本号拼进 URL"
