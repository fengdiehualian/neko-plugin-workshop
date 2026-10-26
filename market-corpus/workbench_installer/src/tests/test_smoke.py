"""workbench_installer 冒烟测试:静态结构 + install/status/launch 三入口真实逻辑。

安装只走官方安装版 exe(exe 来源),因此运行时用例用本地假 exe + 打桩的安装器,
全程离线、不触网、不写真实安装目录。需要 N.E.K.O SDK 在路径上
(python -m pytest tests/ 于 N.E.K.O 环境,或设 WB_NEKO_REPO 指向 N.E.K.O 源码根),
SDK 不在时自动跳过运行时用例,静态用例照常执行。
"""
import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------- 静态结构 ----------


def test_plugin_manifest_exists() -> None:
    manifest = ROOT / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "workbench_installer"' in text
    assert 'entry = "plugin.plugins.workbench_installer:WorkbenchInstallerPlugin"' in text
    # 只支持安装版:manifest 不应再把便携 zip 写进默认配置
    assert "Portable.zip" not in text


def test_entry_class_declared() -> None:
    # 实现位于 plugin/plugins/workbench_installer/(与 entry 包路径同形)
    impl = ROOT / "plugin" / "plugins" / "workbench_installer" / "__init__.py"
    source = impl.read_text(encoding="utf-8")
    assert "@neko_plugin" in source
    assert "class WorkbenchInstallerPlugin(NekoPluginBase)" in source
    # 根 __init__.py 是入口垫片:必须把实现类再导出(挂载后 entry 从包根取类)
    shim = (ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "WorkbenchInstallerPlugin" in shim


def test_no_zip_extraction_anymore() -> None:
    # 明确回归:不再做本地解压安装
    impl = ROOT / "plugin" / "plugins" / "workbench_installer" / "__init__.py"
    source = impl.read_text(encoding="utf-8")
    assert "zipfile" not in source
    assert "extractall" not in source


# ---------- 运行时(入口真实逻辑)----------


def _ensure_sdk_on_path() -> bool:
    try:
        # 可用性探测:importlib 动态导入,不产生未用导入绑定(官方 CI ruff --ignore-noqa 会查 F401)
        importlib.import_module("plugin.sdk")

        return True
    except Exception:
        pass
    for cand in (os.environ.get("WB_NEKO_REPO") or "", os.environ.get("NEKO_REPO_ROOT") or ""):
        if cand and (Path(cand) / "plugin" / "sdk").is_dir():
            sys.path.insert(0, cand)
            return True
    for parent in Path(__file__).resolve().parents:
        if (parent / "plugin" / "sdk").is_dir():
            sys.path.insert(0, str(parent))
            return True
    return False


def _load_module():
    if not _ensure_sdk_on_path():
        pytest.skip("N.E.K.O SDK 不在路径上(设置 WB_NEKO_REPO 后重跑)")
    spec = importlib.util.spec_from_file_location(
        "workbench_installer_under_test", ROOT / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_plugin(mod, tmp_path):
    p = mod.WorkbenchInstallerPlugin(mock.MagicMock())
    cache, data = tmp_path / "cache", tmp_path / "data"
    p.cache_path = lambda *parts: cache.joinpath(*parts)  # 离线隔离,不碰真实缓存目录
    p.data_path = lambda *parts: data.joinpath(*parts)
    # 全程不触网:健康探测打桩固定 None(否则会真请求 127.0.0.1:5099,
    # CI 上端口被占时 status 会误报 running,测试不稳定)
    p._health = mock.AsyncMock(return_value=None)
    return p


def _fake_setup(target: Path):
    """假安装器:像官方 --silent 一样把程序文件落到目标目录,并保留用户数据目录。"""

    def _run(exe_path: str) -> dict:
        target.mkdir(parents=True, exist_ok=True)
        (target / "start.cmd").write_text("@echo off\r\necho wb\r\n", encoding="utf-8")
        (target / "wb-studio.mjs").write_text("// stub", encoding="utf-8")
        (target / "runtime").mkdir(exist_ok=True)
        (target / "workspace").mkdir(exist_ok=True)
        return {"ok": True, "returncode": 0}

    return _run


def _val(res):
    return res.value if hasattr(res, "value") else res


def _bind_default_target(mod, target: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod._impl_mod, "_default_target", lambda: str(target))


def test_status_not_installed(tmp_path) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    res = asyncio.run(p.status(target=str(tmp_path / "nope")))
    v = _val(res)
    assert not v.get("installed"), v


def test_install_exe_then_refuses_overwrite_then_overwrites(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    target = tmp_path / "wb"
    _bind_default_target(mod, target, monkeypatch)
    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"MZ-stub")
    p = _make_plugin(mod, tmp_path)
    p._run_setup_exe = _fake_setup(target)
    params = dict(exe=str(exe), launch=False, shortcut=False)

    res = asyncio.run(p.install(mod.InstallParams(**params)))
    v = _val(res)
    assert v.get("already_installed") is False, v
    assert (target / "start.cmd").is_file()

    # 已安装:拒绝覆盖(必须显式 overwrite=True)
    res2 = asyncio.run(p.install(mod.InstallParams(**params)))
    v2 = _val(res2)
    assert v2.get("already_installed") is True, v2
    assert "overwrite" in str(v2.get("message", "")), v2

    # 显式覆盖:允许重装
    res3 = asyncio.run(p.install(mod.InstallParams(**{**params, "overwrite": True})))
    v3 = _val(res3)
    assert v3.get("already_installed") is False, v3
    assert (target / "start.cmd").is_file()


def test_install_rejects_non_exe(tmp_path) -> None:
    # 只认官方安装版:传 zip/其它格式必须明确拒绝,不做本地解压
    mod = _load_module()
    pkg = tmp_path / "pkg.zip"
    pkg.write_bytes(b"PK-stub")
    p = _make_plugin(mod, tmp_path)
    res = asyncio.run(p.install(mod.InstallParams(exe=str(pkg), launch=False, shortcut=False)))
    assert isinstance(res, mod.Err), res
    assert "exe" in str(res.error).lower()


def test_install_missing_local_exe(tmp_path) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    res = asyncio.run(
        p.install(mod.InstallParams(exe=str(tmp_path / "nope.exe"), launch=False, shortcut=False))
    )
    assert isinstance(res, mod.Err), res
    assert "不存在" in str(res.error)


def test_install_reports_setup_failure(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    target = tmp_path / "wb"
    _bind_default_target(mod, target, monkeypatch)
    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"MZ-stub")
    p = _make_plugin(mod, tmp_path)
    p._run_setup_exe = lambda _path: {"ok": False, "error": "exit=1 权限不足"}
    res = asyncio.run(p.install(mod.InstallParams(exe=str(exe), launch=False, shortcut=False)))
    assert isinstance(res, mod.Err), res
    assert "安装程序" in str(res.error)


def test_install_detects_missing_start_cmd_after_setup(tmp_path, monkeypatch) -> None:
    # 安装器"成功退出"但没落盘(被杀软拦截等):必须报错而不是假装成功
    mod = _load_module()
    target = tmp_path / "wb"
    _bind_default_target(mod, target, monkeypatch)
    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"MZ-stub")
    p = _make_plugin(mod, tmp_path)
    p._run_setup_exe = lambda _path: {"ok": True, "returncode": 0}
    res = asyncio.run(p.install(mod.InstallParams(exe=str(exe), launch=False, shortcut=False)))
    assert isinstance(res, mod.Err), res
    assert "start.cmd" in str(res.error)


def test_launch_without_start_cmd_fails(tmp_path) -> None:
    mod = _load_module()
    empty = tmp_path / "empty"
    empty.mkdir()
    p = _make_plugin(mod, tmp_path)
    res = asyncio.run(p.launch(target=str(empty)))
    assert isinstance(res, mod.Err), res


@pytest.mark.skipif(os.name != "nt", reason="creationflags 仅 Windows")
def test_spawn_uses_compatible_creation_flags(tmp_path, monkeypatch) -> None:
    # DETACHED_PROCESS 与 CREATE_NEW_CONSOLE 互斥,同用会 WinError 87 启动静默失败
    import subprocess as sp

    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    target = tmp_path / "wbdir"
    target.mkdir()
    (target / "start.cmd").write_text("@echo off", encoding="utf-8")

    captured = {}

    class _FakeProc:
        pid = 12345

    def fake_popen(cmd, **kw):
        captured.update(kw)
        return _FakeProc()

    monkeypatch.setattr(sp, "Popen", fake_popen)
    res = p._spawn(str(target))
    assert res.get("ok") is True, res
    flags = captured.get("creationflags", 0)
    assert flags == sp.CREATE_NEW_CONSOLE
    assert not (flags & sp.DETACHED_PROCESS)


# ---------- 下载回落:GitHub 优先 → 码云镜像 ----------


def _stub_downloads(p, monkeypatch, outcomes, calls):
    """按顺序返回 outcomes 里的结果;记录每次调用的 (url, read_timeout)。"""

    async def fake_download(url, dest, read_timeout=None):
        calls.append((url, read_timeout))
        result = outcomes[len(calls) - 1]
        if result.get("ok"):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"x" * 8)
        return dict(result)

    monkeypatch.setattr(p, "_download", fake_download)


def test_download_falls_back_to_mirror_on_timeout(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    p._cfg_url = "https://github.com/fengdiehualian/neko-plugin-workshop/releases/latest/download/x.exe"
    calls = []
    _stub_downloads(
        p,
        monkeypatch,
        [
            {"ok": False, "kind": "timeout", "error": "read timeout"},
            {"ok": True, "path": "x", "bytes": 8, "via": "direct"},
        ],
        calls,
    )

    async def fake_mirror(primary_url):
        return [
            "https://gitee.com/maple-butterfly-flower-love/neko-plugin-workshop/releases/download/v1.2.8/x.exe"
        ]

    monkeypatch.setattr(p, "_mirror_urls", fake_mirror)
    got = asyncio.run(p._download_preferring(p._cfg_url, tmp_path / "dl" / "setup.exe"))
    assert got.get("ok") is True, got
    assert got.get("fallback") is True
    assert "gitee" in got["via"]
    # 主源用 25 秒停滞阈值,镜像用完整下载超时(不传 → 默认)
    assert calls[0][1] == mod._impl_mod._PRIMARY_STALL_SECONDS
    assert calls[1][1] is None
    assert calls[1][0].startswith("https://gitee.com/")


def test_fallback_on_auth_error(tmp_path, monkeypatch) -> None:
    """401/403 也要回落:源站拒绝(私有化/IP 被限/网关拦截)时,公开镜像往往还能拉。"""
    mod = _load_module()
    for code in (401, 403):
        p = _make_plugin(mod, tmp_path / str(code))
        calls = []
        _stub_downloads(
            p,
            monkeypatch,
            [
                {"ok": False, "kind": "http", "status": code, "error": "denied"},
                {"ok": True, "path": "x", "bytes": 8, "via": "direct"},
            ],
            calls,
        )

        async def fake_mirror(primary_url):
            return ["https://gitee.com/mirror/x.exe"]

        monkeypatch.setattr(p, "_mirror_urls", fake_mirror)
        got = asyncio.run(p._download_preferring("https://github.com/a/b/x.exe", tmp_path / "x.exe"))
        assert got.get("ok") is True, (code, got)
        assert got.get("fallback") is True
        assert len(calls) == 2


def test_no_fallback_on_plain_client_error(tmp_path, monkeypatch) -> None:
    """普通 4xx(非 401/403/404/429)换源也没用,不该再拉一次。"""
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    calls = []
    _stub_downloads(
        p, monkeypatch, [{"ok": False, "kind": "http", "status": 400, "error": "bad request"}], calls
    )

    async def boom(primary_url):
        raise AssertionError("400 不应触发镜像回落")

    monkeypatch.setattr(p, "_mirror_urls", boom)
    got = asyncio.run(p._download_preferring("https://github.com/a/b/x.exe", tmp_path / "x.exe"))
    assert got.get("ok") is False
    assert got.get("status") == 400
    assert len(calls) == 1


def test_mirror_off_returns_primary_error(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    p._cfg_mirror = "off"
    p._cfg_url = "https://github.com/a/b/x.exe"
    calls = []
    _stub_downloads(
        p, monkeypatch, [{"ok": False, "kind": "conn", "error": "connect refused"}], calls
    )
    # 走真实 _mirror_url:配置 off 时必须直接返回空,不触网
    got = asyncio.run(p._download_preferring(p._cfg_url, tmp_path / "x.exe"))
    assert got.get("ok") is False
    assert "connect refused" in got.get("error", "")
    assert len(calls) == 1


def test_mirror_custom_url_used_directly(tmp_path) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    p._cfg_url = "https://github.com/a/b/x.exe"
    p._cfg_mirror = "https://cdn.example.com/setup.exe"
    got = asyncio.run(p._mirror_urls(p._cfg_url))
    assert got == ["https://cdn.example.com/setup.exe"]
    # 用户自定义了下载源时不再擅自换源
    p._cfg_mirror = ""
    assert asyncio.run(p._mirror_urls("https://other.example.com/x.exe")) == []


# ---------- 压缩包安装路径(码云镜像 >100MB 只能放压缩产物)----------


def _make_payload_archive(tmp_path: Path) -> Path:
    import tarfile

    stage = tmp_path / "stage"
    (stage / "runtime").mkdir(parents=True)
    (stage / "workspace" / "proj").mkdir(parents=True)
    (stage / "start.cmd").write_text("@echo off\r\necho wb\r\n", encoding="utf-8")
    (stage / "wb-studio.mjs").write_text("// stub", encoding="utf-8")
    # 包里的 runtime/workspace 是空壳,解压时必须跳过,不能覆盖用户数据
    (stage / "runtime" / "config.json").write_text("packaged", encoding="utf-8")
    (stage / "workspace" / "proj" / "a.txt").write_text("packaged", encoding="utf-8")
    archive = tmp_path / "payload.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        for item in sorted(stage.rglob("*")):
            tf.add(item, arcname=str(item.relative_to(stage)))
    return archive


def test_looks_like_exe_uses_magic_bytes(tmp_path) -> None:
    mod = _load_module()
    exe = tmp_path / "a.exe"
    exe.write_bytes(b"MZ\x90\x00rest")
    assert mod._impl_mod._looks_like_exe(str(exe)) is True
    xz = tmp_path / "b.exe"  # 名字是 exe,内容却是 xz
    xz.write_bytes(b"\xfd7zXZ\x00rest")
    assert mod._impl_mod._looks_like_exe(str(xz)) is False


def test_archive_kind_by_magic(tmp_path) -> None:
    mod = _load_module()
    impl = mod._impl_mod
    exe = tmp_path / "a"
    exe.write_bytes(b"MZ\x90\x00rest")
    assert impl._archive_kind(str(exe)) == "exe"
    sz = tmp_path / "b"
    sz.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 16)
    assert impl._archive_kind(str(sz)) == "7z"
    xz = tmp_path / "c"
    xz.write_bytes(b"\xfd7zXZ\x00rest")
    assert impl._archive_kind(str(xz)) == "xz"
    other = tmp_path / "d"
    other.write_bytes(b"PK\x03\x04")
    assert impl._archive_kind(str(other)) == "unknown"


def test_bundled_7zr_is_shipped() -> None:
    """插件必须自带 7zr,否则用户机器没装 7-Zip 就解不开镜像包。"""
    bundled = ROOT / "plugin" / "plugins" / "workbench_installer" / "bin" / "7zr.exe"
    assert bundled.is_file(), f"缺少内置解压器: {bundled}"
    assert bundled.stat().st_size > 300_000


def _make_7z_payload(tmp_path: Path) -> Path:
    """用内置 7zr 造一个测试包(含 runtime/workspace 空壳)。"""
    import subprocess

    mod = _load_module()
    seven = mod._impl_mod._find_7zr()
    if not seven:
        pytest.skip("7zr 不可用")
    stage = tmp_path / "stage7z"
    (stage / "runtime").mkdir(parents=True)
    (stage / "workspace" / "proj").mkdir(parents=True)
    (stage / "start.cmd").write_text("@echo off\r\necho wb\r\n", encoding="utf-8")
    (stage / "wb-studio.mjs").write_text("// stub", encoding="utf-8")
    (stage / "runtime" / "config.json").write_text("packaged", encoding="utf-8")
    (stage / "workspace" / "proj" / "a.txt").write_text("packaged", encoding="utf-8")
    archive = tmp_path / "payload.7z"
    subprocess.run(
        [seven, "a", "-t7z", str(archive), "./*"],
        cwd=str(stage),
        check=True,
        capture_output=True,
        timeout=120,
    )
    return archive


def test_install_from_7z_keeps_user_data(tmp_path) -> None:
    mod = _load_module()
    archive = _make_7z_payload(tmp_path)
    p = _make_plugin(mod, tmp_path)
    target = tmp_path / "install"
    (target / "runtime").mkdir(parents=True)
    (target / "runtime" / "config.json").write_text("user", encoding="utf-8")

    res = p._install_from_7z(str(archive), str(target))
    assert res.get("ok") is True, res
    assert (target / "start.cmd").is_file()
    assert (target / "runtime" / "config.json").read_text(encoding="utf-8") == "user"
    assert (target / "workspace" / "proj" / "a.txt").is_file()


# ---------- 下载全失败时的手动下载页 ----------


def test_manual_page_html_has_urls_and_copy(tmp_path) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    urls = [
        "https://github.com/a/b/releases/latest/download/x.exe",
        "https://gitee.com/c/d/releases/download/v1/x.7z",
    ]
    html = p._manual_page_html(urls, "github 超时 <timeout> & 镜像失败")
    assert "复制" in html and "copyUrl" in html
    for u in urls:
        assert u in html
    # 错误信息必须转义,不能把尖括号当标签
    assert "&lt;timeout&gt;" in html and "&amp;" in html


def test_install_failure_opens_manual_page(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    target = tmp_path / "install"
    _bind_default_target(mod, target, monkeypatch)

    tried = [
        "https://github.com/a/b/releases/latest/download/N.E.K.O.PluginWorkshop-Setup.exe",
        "https://gitee.com/c/d/releases/download/v1/N.E.K.O.PluginWorkshop-Payload.7z",
    ]

    async def fail(primary_url, dest):
        return {"ok": False, "kind": "timeout", "tried": tried, "error": "read timeout"}

    monkeypatch.setattr(p, "_download_preferring", fail)
    opened = {}

    def fake_open(urls, detail):
        opened["urls"] = urls
        opened["detail"] = detail
        return str(tmp_path / "manual-download.html")

    monkeypatch.setattr(p, "_open_manual_page", fake_open)

    res = asyncio.run(p.install(mod.InstallParams(launch=False, shortcut=False)))
    assert isinstance(res, mod.Err), res
    assert opened.get("urls") == tried
    err = str(res.error)
    assert "手动下载页" in err
    assert "gitee.com" in err or "github.com" in err


def test_install_failure_without_page_still_lists_urls(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    target = tmp_path / "install"
    _bind_default_target(mod, target, monkeypatch)
    tried = ["https://github.com/a/b/x.exe", "https://gitee.com/c/d/x.7z"]

    async def fail(primary_url, dest):
        return {"ok": False, "kind": "conn", "tried": tried, "error": "connect refused"}

    monkeypatch.setattr(p, "_download_preferring", fail)

    def boom(urls, detail):
        raise AssertionError("manual_page=False 时不该弹页")

    monkeypatch.setattr(p, "_open_manual_page", boom)

    params = mod.InstallParams(launch=False, shortcut=False, manual_page=False)
    res = asyncio.run(p.install(params))
    assert isinstance(res, mod.Err), res
    err = str(res.error)
    assert "https://github.com/a/b/x.exe" in err
    assert "https://gitee.com/c/d/x.7z" in err


def test_install_from_archive_keeps_user_data(tmp_path) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    archive = _make_payload_archive(tmp_path)
    target = tmp_path / "install"
    (target / "runtime").mkdir(parents=True)
    (target / "workspace").mkdir(parents=True)
    (target / "runtime" / "config.json").write_text("user", encoding="utf-8")

    res = p._install_from_archive(str(archive), str(target))
    assert res.get("ok") is True, res
    assert (target / "start.cmd").is_file()
    assert (target / "wb-studio.mjs").is_file()
    # 用户数据保留,未被包内空壳覆盖
    assert (target / "runtime" / "config.json").read_text(encoding="utf-8") == "user"
    assert (target / "workspace" / "proj" / "a.txt").is_file()


def test_install_accepts_archive_from_mirror(tmp_path, monkeypatch) -> None:
    """回落到码云拿到的是 tar.xz:走解压安装,不再调用 setup exe。"""
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    archive = _make_payload_archive(tmp_path)
    target = tmp_path / "install"
    _bind_default_target(mod, target, monkeypatch)

    async def fake_preferring(primary_url, dest):
        return {"ok": True, "path": str(archive), "bytes": archive.stat().st_size,
                "via": "direct (gitee 镜像)", "fallback": True}

    monkeypatch.setattr(p, "_download_preferring", fake_preferring)
    called = {"setup": False}

    def fake_setup(path):
        called["setup"] = True
        return {"ok": True, "returncode": 0}

    monkeypatch.setattr(p, "_run_setup_exe", fake_setup)

    params = mod.InstallParams(launch=False, shortcut=False)
    res = _val(asyncio.run(p.install(params)))
    assert res.get("ok") is True, res
    assert called["setup"] is False, "压缩包不该再走 setup exe"
    assert (target / "start.cmd").is_file()


# ---------- 码云分卷:139MB 安装器超过单附件 100MB,拆成 .part1/.part2 ----------


def _load_impl():
    """直接加载实现模块(不是包根垫片):模块级私有函数只有这里拿得到。"""
    if not _ensure_sdk_on_path():
        pytest.skip("N.E.K.O SDK 不在路径上(设置 WB_NEKO_REPO 后重跑)")
    spec = importlib.util.spec_from_file_location(
        "wb_installer_impl", ROOT / "plugin" / "plugins" / "workbench_installer" / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pick_mirror_assets_prefers_single_file() -> None:
    mod = _load_impl()
    assets = [
        {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.14.exe.part1", "browser_download_url": "u1"},
        {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.14.exe.part2", "browser_download_url": "u2"},
        {"name": "N.E.K.O.PluginWorkshop-Payload.tar.xz", "browser_download_url": "ux"},
        {"name": "N.E.K.O.PluginWorkshop-Payload.7z", "browser_download_url": "u7"},
    ]
    # 单文件压缩包优先(一次下载、可校验),分卷只是兜底
    assert mod._pick_mirror_assets(assets) == ["u7"]


def test_pick_mirror_assets_sorts_parts() -> None:
    mod = _load_impl()
    assets = [
        {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.14.exe.part2", "browser_download_url": "u2"},
        {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.14.exe.part1", "browser_download_url": "u1"},
    ]
    # 资产顺序不可靠,必须按 part 编号排
    assert mod._pick_mirror_assets(assets) == ["u1", "u2"]


def test_pick_mirror_assets_rejects_gap() -> None:
    mod = _load_impl()
    assets = [
        {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.14.exe.part1", "browser_download_url": "u1"},
        {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.14.exe.part3", "browser_download_url": "u3"},
    ]
    # 缺卷拼出来是坏文件,宁可判镜像不可用
    assert mod._pick_mirror_assets(assets) == []


def test_download_split_merges_parts(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    chunks = {"https://g/p1": b"MZ-AAA", "https://g/p2": b"BBB"}

    async def fake_download(url, dest, read_timeout=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(chunks[url])
        return {"ok": True, "path": dest, "bytes": len(chunks[url]), "via": "direct"}

    monkeypatch.setattr(p, "_download", fake_download)
    dest = tmp_path / "dl" / "Setup.exe"
    got = asyncio.run(p._download_split(["https://g/p1", "https://g/p2"], str(dest)))
    assert got["ok"] is True, got
    assert dest.read_bytes() == b"MZ-AAABBB"
    assert "分卷 2" in got["via"]
    assert got["fallback"] is True
    # 临时分卷必须清理干净,不能把 cache 撑爆
    assert not list((tmp_path / "dl").glob("*.part*"))


def test_download_split_rejects_non_exe(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)

    async def fake_download(url, dest, read_timeout=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"not-an-exe")
        return {"ok": True, "path": dest, "bytes": 10, "via": "direct"}

    monkeypatch.setattr(p, "_download", fake_download)
    got = asyncio.run(p._download_split(["https://g/p1", "https://g/p2"], str(tmp_path / "o.exe")))
    assert got["ok"] is False
    assert "MZ" in got["error"]


def test_download_preferring_uses_split_when_only_parts(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    p = _make_plugin(mod, tmp_path)
    p._cfg_url = "https://github.com/fengdiehualian/neko-plugin-workshop/releases/latest/download/x.exe"
    calls = []
    _stub_downloads(
        p, monkeypatch, [{"ok": False, "kind": "timeout", "error": "read timeout"}], calls
    )

    async def fake_mirror(primary_url):
        return ["https://g/p1", "https://g/p2"]

    monkeypatch.setattr(p, "_mirror_urls", fake_mirror)
    seen = {}

    async def fake_split(urls, dest):
        seen["urls"] = list(urls)
        return {"ok": True, "path": dest, "bytes": 6, "via": "gitee 镜像(分卷 2 卷)", "fallback": True}

    monkeypatch.setattr(p, "_download_split", fake_split)
    got = asyncio.run(p._download_preferring(p._cfg_url, str(tmp_path / "x.exe")))
    assert got.get("ok") is True, got
    assert seen["urls"] == ["https://g/p1", "https://g/p2"]
