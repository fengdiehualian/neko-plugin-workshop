"""四个静态页的"底色层"：谁有底图、谁是纯白 —— 源码级看门狗。

**为什么要有这条**：整页背景由 `theme.css` 的 `--page-bg-image` / `--page-scrim-color`
统一决定，页面自己的 `<style>` 在 `<link>` 之后、可以覆写它。于是"某页该不该有底图"
这件事**没有任何编译期约束**，只有一条容易忘的约定（见 theme.css 的注释）。
使用者已经拍过两次板（status 单独换森林、index 改纯白），所以把它钉住。

三条不变量：

1. `index.html`：**纯白无图**。两个变量都要清 —— 只清图不清纱时，那层
   `rgba(255,255,255,.45)` 会压在 `#f7f9fc` 上，出来不是纯白（实测 rgb(250,251,252)）。
2. `status.html`：森林那张 `.30`（它的卡片还要更实的底，见 assets/README）。
3. `napcat.html` / `open_platform.html`：跟 theme.css 的默认蓝白 `.45`（页内不许覆写）。

另一条同样钉住：页面的 `body` 规则不许自己写 `background-image`/`background` 简写
（写了会盖掉 theme.css 的整页背景层，而且顺序随 `<link>` 位置变化）。
"""

from __future__ import annotations

import pathlib
import re

STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"

THEME = (STATIC / "theme.css").read_text(encoding="utf-8")


def _html(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _own_style(html: str) -> str:
    """页面自己那个 <style>（不含 <link> 引的 theme.css）。"""
    match = re.search(r"<style>(.*?)</style>", html, re.S)
    return match.group(1) if match else ""


def _css_code(style: str) -> str:
    """去掉 CSS 注释 —— 注释里会提到 `--page-bg-image` 这类名字，
    不剥掉的话"源码里有没有引用底图"会被自己的说明文字误判。"""
    return re.sub(r"/\*.*?\*/", "", style, flags=re.S)


def test_theme_default_is_the_blue_wallpaper():
    """默认底图是蓝白图形 + 纱 .45（napcat / open_platform 靠它）。"""
    assert "page-bg-blue.webp" in THEME
    assert re.search(r"--page-scrim-color:\s*rgba\(255,\s*255,\s*255,\s*\.45\)", THEME)


def test_index_is_plain_white_without_any_wallpaper():
    style = _own_style(_html("index.html"))
    code = _css_code(style)

    assert re.search(r"--page-bg-image:\s*none", code), "index 的底图没清掉"
    assert re.search(r"--page-scrim-color:\s*transparent", code), (
        "index 只清了图没清纱 —— rgba 白纱压在 #f7f9fc 上不是纯白"
    )
    assert re.search(r"background-color:\s*#fff", code), "index 的底色不是纯白"
    assert "url(" not in code, "index 的样式里不该再引用任何图片"


def test_status_keeps_the_forest_wallpaper():
    style = _own_style(_html("status.html"))

    assert "page-bg-forest.webp" in style, "status 的森林底图被改掉了"
    assert re.search(r"--page-scrim-color:\s*rgba\(255,\s*255,\s*255,\s*\.30\)", style), (
        "森林那张偏暗，纱跟着一起改成 .30 的约定断了"
    )
    assert "--surface-strong" in style, "森林底下卡片要用更实的底，这行别丢"


def test_other_pages_do_not_override_the_background():
    for name in ("napcat.html", "open_platform.html"):
        style = _own_style(_html(name))
        assert "--page-bg-image" not in style, f"{name} 不该覆写底图（跟着默认走）"
        assert "--page-scrim-color" not in style, f"{name} 不该覆写纱"


def test_pages_do_not_set_their_own_body_background():
    """页面自己的 body 规则不许写背景 —— 会盖掉 theme.css 的整页背景层。

    （index 是唯一的例外，而且它的写法被上一条单独钉住。）
    """
    for name in ("status.html", "napcat.html", "open_platform.html"):
        style = _own_style(_html(name))
        for rule in re.findall(r"body\s*\{[^}]*\}", style):
            assert "background-image" not in rule, f"{name} 的 body 里写了 background-image"
            assert not re.search(r"(?<!-)\bbackground\s*:", rule), (
                f"{name} 的 body 里用了 background 简写"
            )
