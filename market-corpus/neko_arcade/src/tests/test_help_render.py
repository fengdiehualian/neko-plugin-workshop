"""帮助图渲染测试: 浏览器(HTML)渲染路径的构造逻辑 + 浏览器定位。

不依赖真实浏览器(PIL/Playwright 缺失也能跑): 只断言 HTML 构造与候选目录。
真实截图由 render_help_html 在运行环境里完成, 失败自动回退 PIL 版 render_help。
"""

from __future__ import annotations

import os
import sys

from plugin.plugins.neko_arcade.adapters.image_renderer import ImageRenderer


def test_build_help_html_contains_rows_title_and_brand() -> None:
    html = ImageRenderer().build_help_html(
        "测试游戏", [("开始", "开始游戏"), ("停止", "结束游戏")], "这里是说明")
    assert html.startswith("<!doctype html>")
    assert '<div class="hd">测试游戏 · 玩法帮助</div>' in html
    assert '<span class="k">开始</span>' in html
    assert '<span class="v">开始游戏</span>' in html
    assert "这里是说明" in html
    assert "N.E.K.O 猫娘小游戏 × 测试游戏" in html


def test_asset_resolver_prefers_injected_paths() -> None:
    """素材路径可由 AssetStore 注入（本地优先，缺则下载到缓存）。

    帮助图素材（头像/背景/立绘/徽章）现在走素材库解析：
      · 随包的 → local_mirror 命中本地，**不联网**；
      · 外置的 → 首次运行下载到缓存后注入；
    这里验证"注入路径优先于 code_dir 相对路径"这条契约。
    """
    import shutil
    from pathlib import Path

    from plugin.plugins.neko_arcade.core.help.themes import AssetResolver

    tmp = Path(__file__).resolve().parent.parent / ".tmp_theme_asset_test"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "code").mkdir(parents=True)
    (tmp / "cache").mkdir(parents=True)

    # 缓存里放一张真图（模拟"首次运行已下载补齐"）
    from PIL import Image
    img_path = tmp / "cache" / "yui竖.webp"
    Image.new("RGB", (32, 48), (120, 90, 60)).save(img_path, format="WEBP")

    res = AssetResolver(str(tmp / "code"), str(tmp / "cache"))
    assert res.uri("bg") == "", "代码目录没有素材时应优雅降级为空串"

    res.set_paths({"bg": str(img_path)})
    uri = res.uri("bg")
    assert uri.startswith("data:image/"), f"注入了路径就应出图: {uri[:30]}"

    shutil.rmtree(tmp, ignore_errors=True)


def test_page_tag_only_when_multiple_pages() -> None:
    ir = ImageRenderer()
    assert "(1/1)" not in ir.build_help_html("G", [("a", "b")], page=1, pages=1)
    assert "(2/3)" in ir.build_help_html("G", [("a", "b")], page=2, pages=3)


def test_footer_only_rendered_on_first_page() -> None:
    ir = ImageRenderer()
    assert "页脚说明" in ir.build_help_html("G", [("a", "b")], "页脚说明", page=1, pages=2)
    assert "页脚说明" not in ir.build_help_html("G", [("a", "b")], "页脚说明", page=2, pages=2)


def test_html_special_chars_are_escaped() -> None:
    """指令里出现 < > & 时不能破坏 HTML(否则帮助图整页错乱)。"""
    html = ImageRenderer().build_help_html("G", [("<script>x</script>", "a & b")])
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
    assert "a &amp; b" in html


def test_browser_roots_include_system_playwright_cache() -> None:
    """应用自带 Chromium 可能缺 exe(实测 0.9.0.2 如此), 必须也能找到系统缓存。"""
    roots = ImageRenderer()._browser_roots()
    assert any("ms-playwright" in r for r in roots), roots
    assert any("playwright_browsers" in r for r in roots), roots


def test_system_browser_candidates_cover_platform_browsers() -> None:
    """内置 Chromium 全不可用时, 还要能退到系统已装 Chrome/Edge(与宿主回退一致)。"""
    cands = ImageRenderer()._system_browser_candidates()
    assert cands, "系统浏览器候选列表不能为空"
    joined = " ".join(cands).replace("\\", "/").lower()
    if sys.platform == "win32":
        assert "chrome.exe" in joined
        assert "msedge.exe" in joined
    elif sys.platform == "darwin":
        assert "google chrome.app" in joined
    else:
        assert "chromium" in joined


def test_find_chromium_only_returns_real_files() -> None:
    """定位到的浏览器必须真实存在——不能是被剥空的目录(0.9.0.2 自带包就是这个坑)。"""
    exe = ImageRenderer()._find_chromium()
    assert exe is None or os.path.isfile(exe), exe


def test_host_browser_is_preferred(monkeypatch) -> None:
    """出图优先走宿主内置: 宿主自己的查找函数给出的浏览器必须排在最前。"""
    import types

    fake_exe = os.path.abspath(__file__)          # 任意真实文件充当"浏览器"

    def fake_import(name: str, *a, **kw):
        if name == "brain.browser_use_adapter":
            return types.SimpleNamespace(_find_chrome_path=lambda: fake_exe)
        raise ImportError(name)

    monkeypatch.setattr("importlib.import_module", fake_import)
    assert ImageRenderer._host_browser_hint() == fake_exe
    assert ImageRenderer._find_chromium() == fake_exe


def test_host_hint_ignores_missing_browser(monkeypatch) -> None:
    """宿主/依赖给的路径都不存在时必须返回 None, 不能返回坏路径。

    注意: CI(Linux)上 browser_use 依赖是装的, 且系统真有 /usr/bin/google-chrome*,
    所以必须把这两条来源都换成"指向不存在的文件"才能确定性断言(否则测试依赖环境)。
    """
    import sys
    import types

    def fake_import(name: str, *a, **kw):
        if name == "brain.browser_use_adapter":
            return types.SimpleNamespace(_find_chrome_path=lambda: r"/nope/chrome")
        raise ImportError(name)

    stub_watchdog = types.ModuleType(
        "browser_use.browser.watchdogs.local_browser_watchdog")

    class _Watchdog:
        @staticmethod
        def _find_installed_browser_path():
            return r"/nope/also-chrome"

    stub_watchdog.LocalBrowserWatchdog = _Watchdog
    monkeypatch.setattr("importlib.import_module", fake_import)
    monkeypatch.setitem(sys.modules,
                        "browser_use.browser.watchdogs.local_browser_watchdog",
                        stub_watchdog)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    assert ImageRenderer._host_browser_hint() is None


def test_find_chromium_none_when_nothing_available(monkeypatch) -> None:
    """所有来源都不可用时返回 None(不返回假路径)。"""
    monkeypatch.setattr(ImageRenderer, "_host_browser_hint",
                        classmethod(lambda cls: None))
    monkeypatch.setattr(ImageRenderer, "_browser_roots", classmethod(lambda cls: []))
    monkeypatch.setattr(ImageRenderer, "_system_browser_candidates",
                        staticmethod(lambda: []))
    assert ImageRenderer._find_chromium() is None


def test_launch_chain_prefers_host_environment(monkeypatch) -> None:
    """渲染尽量走宿主: 首选"不指定 exe"(宿主 PLAYWRIGHT_BROWSERS_PATH), 显式路径兜底。"""
    chain = ImageRenderer._launch_kwargs_chain()
    assert chain[0] == {"headless": True}
    assert "executable_path" not in chain[0]
    monkeypatch.setattr(ImageRenderer, "_find_chromium",
                        classmethod(lambda cls: r"C:\host\chrome.exe"))
    chain2 = ImageRenderer._launch_kwargs_chain()
    assert chain2[0] == {"headless": True}
    assert chain2[1]["executable_path"] == r"C:\host\chrome.exe"


def test_launch_chain_without_any_browser(monkeypatch) -> None:
    """找不到任何浏览器时链上只有宿主环境一项(交给 Playwright 自己解析)。"""
    monkeypatch.setattr(ImageRenderer, "_find_chromium", classmethod(lambda cls: None))
    assert ImageRenderer._launch_kwargs_chain() == [{"headless": True}]
