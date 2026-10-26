"""desktop_deliver：桌面扩展包投放逻辑测试。

覆盖：三浏览器目录创建（Chrome/Edge/360）、扩展本体复制、bat 编码（GBK+CRLF）、
txt 编码（UTF-8 BOM+CRLF）、三扩展 zip 压缩包（独立文件夹+名字）、幂等跳过、
版本变更重新投放、缺失扩展返回 None。
"""

from __future__ import annotations

import os
import zipfile

# SDK 顶层导入链依赖 utils.plugin_card_fields，测试必须在函数内导入插件模块
# （desktop_deliver 本身是纯 stdlib，无此问题，但包 __init__ 有）

_EXT_DIRS = ("bili-bridge-chrome", "bili-bridge-edge", "bili-bridge-360")
_EXT_FILES = ("manifest.json", "background.js", "popup.html", "popup.js", "offscreen.html", "offscreen.js")


def _make_plugin_dir(tmp_path) -> str:
    plugin_dir = os.path.join(str(tmp_path), "plugin_root")
    for ext in _EXT_DIRS:
        d = os.path.join(plugin_dir, "browser_extension", ext)
        os.makedirs(d, exist_ok=True)
        for f in _EXT_FILES:
            with open(os.path.join(d, f), "w", encoding="utf-8") as fh:
                fh.write("{}")
    with open(os.path.join(plugin_dir, "plugin.toml"), "w", encoding="utf-8") as fh:
        fh.write('version = "9.9.9"\n')
    return plugin_dir


def test_delivers_folder_and_files(tmp_path):
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    desktop = os.path.join(str(tmp_path), "Desktop")
    delivery = desktop_deliver.ensure_desktop_package(plugin_dir, desktop=desktop)
    assert delivery and delivery == os.path.join(desktop, "B站接入桥扩展")
    for ext in _EXT_DIRS:
        for f in _EXT_FILES:
            assert os.path.isfile(os.path.join(delivery, ext, f))
    for name in ("Chrome", "Edge", "360"):
        assert os.path.isfile(os.path.join(delivery, f"一键安装B站接入桥({name}).bat"))
    assert os.path.isfile(os.path.join(delivery, "使用说明.txt"))
    assert os.path.isfile(os.path.join(delivery, ".delivered_version"))


def test_bat_is_gbk_crlf(tmp_path):
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    delivery = desktop_deliver.ensure_desktop_package(
        plugin_dir, desktop=os.path.join(str(tmp_path), "Desktop")
    )
    bat = os.path.join(delivery, "一键安装B站接入桥(Chrome).bat")
    data = open(bat, "rb").read()
    # 无 UTF-8 BOM，首字节是 '@'（@echo off）
    assert data[:3] != b"\xef\xbb\xbf"
    assert data[0] == ord("@")
    # 无 LF-only 行（CRLF 编码）
    assert b"\n" not in data.replace(b"\r\n", b"")
    # GBK 可解码且包含关键中文
    text = data.decode("gbk")
    assert "B站接入桥" in text
    assert "@echo off" in text
    assert "chrome://extensions" in text


def test_edge_bat_uses_edge_extensions_page(tmp_path):
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    delivery = desktop_deliver.ensure_desktop_package(
        plugin_dir, desktop=os.path.join(str(tmp_path), "Desktop")
    )
    bat = os.path.join(delivery, "一键安装B站接入桥(Edge).bat")
    text = open(bat, encoding="gbk").read()
    assert "bili-bridge-edge" in text
    assert "edge://extensions" in text
    assert "msedge.exe" in text


def test_360_bat_mentions_360_exe_and_page(tmp_path):
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    delivery = desktop_deliver.ensure_desktop_package(
        plugin_dir, desktop=os.path.join(str(tmp_path), "Desktop")
    )
    bat = os.path.join(delivery, "一键安装B站接入桥(360).bat")
    text = open(bat, encoding="gbk").read()
    assert "bili-bridge-360" in text
    assert "chrome://extensions" in text
    assert "360se" in text or "360Chrome" in text


def test_txt_is_utf8_bom_crlf(tmp_path):
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    delivery = desktop_deliver.ensure_desktop_package(
        plugin_dir, desktop=os.path.join(str(tmp_path), "Desktop")
    )
    txt = os.path.join(delivery, "使用说明.txt")
    data = open(txt, "rb").read()
    assert data[:3] == b"\xef\xbb\xbf"
    assert b"\n" not in data.replace(b"\r\n", b"")
    text = data.decode("utf-8-sig")
    assert "B站接入桥" in text
    assert "一键安装B站接入桥(Chrome).bat" in text
    assert "一键安装B站接入桥(Edge).bat" in text
    assert "一键安装B站接入桥(360).bat" in text


def test_idempotent_skip_when_version_unchanged(tmp_path):
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    desktop = os.path.join(str(tmp_path), "Desktop")
    delivery = desktop_deliver.ensure_desktop_package(plugin_dir, desktop=desktop)
    bat = os.path.join(delivery, "一键安装B站接入桥(Chrome).bat")
    mtime = os.path.getmtime(bat)
    # 同一版本再次调用：跳过重建
    delivery2 = desktop_deliver.ensure_desktop_package(plugin_dir, desktop=desktop)
    assert delivery2 == delivery
    assert os.path.getmtime(bat) == mtime


def test_version_change_re_delivers(tmp_path):
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    desktop = os.path.join(str(tmp_path), "Desktop")
    desktop_deliver.ensure_desktop_package(plugin_dir, version="0.6.8", desktop=desktop)
    delivery = desktop_deliver.ensure_desktop_package(plugin_dir, version="0.6.9", desktop=desktop)
    mark = os.path.join(delivery, ".delivered_version")
    assert open(mark, encoding="utf-8").read().strip() == "0.6.9"


def test_missing_extension_returns_none(tmp_path):
    from plugin.plugins.bili_viewer import desktop_deliver

    empty = os.path.join(str(tmp_path), "empty")
    os.makedirs(empty, exist_ok=True)
    assert (
        desktop_deliver.ensure_desktop_package(empty, desktop=os.path.join(str(tmp_path), "Desktop"))
        is None
    )


def test_zip_contains_three_isolated_extension_folders(tmp_path):
    """v0.7.5：桌面生成含三扩展的 zip，各扩展独立文件夹+名字，含三安装脚本与说明。"""
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    desktop = os.path.join(str(tmp_path), "Desktop")
    desktop_deliver.ensure_desktop_package(plugin_dir, version="0.7.5", desktop=desktop)
    zip_path = os.path.join(desktop, "B站接入桥扩展_v0.7.5.zip")
    assert os.path.isfile(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for ext in _EXT_DIRS:
            for f in _EXT_FILES:
                assert f"{ext}/{f}" in names
        for name in ("Chrome", "Edge", "360"):
            assert f"一键安装B站接入桥({name}).bat" in names
        assert "使用说明.txt" in names
        assert zf.read(".delivered_version").decode("utf-8").strip() == "0.7.5"


def test_zip_idempotent_same_version(tmp_path):
    """v0.7.5：同一版本二次投放不重打 zip（mtime 不变）。"""
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    desktop = os.path.join(str(tmp_path), "Desktop")
    desktop_deliver.ensure_desktop_package(plugin_dir, version="0.7.5", desktop=desktop)
    zip_path = os.path.join(desktop, "B站接入桥扩展_v0.7.5.zip")
    mtime = os.path.getmtime(zip_path)
    desktop_deliver.ensure_desktop_package(plugin_dir, version="0.7.5", desktop=desktop)
    assert os.path.getmtime(zip_path) == mtime


def test_zip_version_change_re_packs(tmp_path):
    """v0.7.5：版本变化后重打新版本 zip，旧版本 zip 保留不删。"""
    from plugin.plugins.bili_viewer import desktop_deliver

    plugin_dir = _make_plugin_dir(tmp_path)
    desktop = os.path.join(str(tmp_path), "Desktop")
    desktop_deliver.ensure_desktop_package(plugin_dir, version="0.7.5", desktop=desktop)
    desktop_deliver.ensure_desktop_package(plugin_dir, version="0.7.6", desktop=desktop)
    assert os.path.isfile(os.path.join(desktop, "B站接入桥扩展_v0.7.5.zip"))
    zip6 = os.path.join(desktop, "B站接入桥扩展_v0.7.6.zip")
    assert os.path.isfile(zip6)
    with zipfile.ZipFile(zip6) as zf:
        assert zf.read(".delivered_version").decode("utf-8").strip() == "0.7.6"


def test_desktop_dir_probe_returns_existing(tmp_path, monkeypatch):
    from plugin.plugins.bili_viewer import desktop_deliver

    # 注册表探测路径不可测（无 winreg 注入点），验证 fallback 至少返回一个目录
    d = desktop_deliver._desktop_dir()
    assert isinstance(d, str) and d
    # 隔离真实桌面：注入测试桌面后 ensure 应写到注入路径
    plugin_dir = _make_plugin_dir(tmp_path)
    desktop = os.path.join(str(tmp_path), "Desktop2")
    delivery = desktop_deliver.ensure_desktop_package(plugin_dir, desktop=desktop)
    assert delivery and os.path.isdir(os.path.join(desktop, "B站接入桥扩展"))
