"""部署的 Node 前置检查必须**按平台**分流，而且不能和「下载」挤在同一步里。

真机现场（2026-10-03，用户报「下载 NapCat 卡住」）：`_fetch()` 的第一步是
`napcat_platform.node_available()`，而它对**所有平台**都是硬门槛。那台机器 PATH 里
没有 node，于是 `缺少 Node.js` 当场抛出 —— `download_asset()` 一行都没跑（日志里 6 次
deploy 全是"同一秒失败"，磁盘上没有任何 `.part`），而界面上停在这一步的标签是
「获取 NapCat」（下载 + 解包 + 校验都挂在它下面），用户读到的就是"卡在下载"。

而 Windows 根本不需要系统 Node：启动走 `cmd /c launcher*.bat` →
`NapCatWinBootMain.exe` 把 `loadNapCat.js` 注入 `QQ.exe`，`napcat.mjs` 跑在 **QQ 自带的
Node 运行时**里（NapCat 官方 Windows 教程也只要求"装好 QQ + 双击 launcher.bat"）。
只有 POSIX 才是真的 `node napcat.mjs`。

这个文件钉住四件事：

1. `node_requirement()` 的平台语义（Windows 否 / 非 Windows 是），且布尔量与解释同源；
2. Windows 缺 node：`_fetch` **照样走到下载**，Node 检查是**独立一步**；
3. 非 Windows 缺 node：仍然拒绝，且**一次下载都不发起**；
4. `[Deploy] …` 明细要**落盘**（面板那份是内存缓冲，事后只剩一句失败原因）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply import deploy_service as ds
from plugin.plugins.qq_auto_reply import napcat_install as ni
from plugin.plugins.qq_auto_reply import napcat_platform as np
from plugin.plugins.qq_auto_reply.deploy_service import QQDeployService

ASSET = "NapCat.Shell.zip"


# ── 一、平台语义 ─────────────────────────────────────────────

def test_windows_does_not_need_system_node():
    required, why = np.node_requirement(windows=True)
    assert required is False, "Windows 用 QQ 自带的 Node 运行时 —— 缺系统 node 不该拦住部署"
    assert "QQ" in why, "解释里要点出「用 QQ 自带的运行时」，否则用户会去装一个没用的 Node"
    assert np.needs_system_node(windows=True) is False


def test_posix_still_needs_system_node():
    required, why = np.node_requirement(windows=False)
    assert required is True, "POSIX 是 node napcat.mjs —— 缺 node 真起不来，必须拦"
    assert "node" in why.lower()
    assert np.needs_system_node(windows=False) is True


def test_bool_and_reason_come_from_the_same_place():
    """布尔量与解释必须同源：两处各判一次平台，迟早出现"判它要 node、解释说不必"。"""
    for windows in (True, False):
        required, _ = np.node_requirement(windows=windows)
        assert np.needs_system_node(windows=windows) is required


# ── 替身 ────────────────────────────────────────────────────

class _Recorder:
    """`logger` 桩：`_emit` 现在会把 `[Deploy] …` 同时落盘。"""

    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.events: list[dict] = []
        self.panel_lines: list[str] = []
        self.file_lines: list[str] = []

    def info(self, line: str, *a, **k) -> None:      # logger.info 的形状
        self.file_lines.append(str(line))


def _svc(tmp_path, rec: _Recorder) -> QQDeployService:
    plugin = SimpleNamespace(
        data_path=lambda name: tmp_path / name,
        _emit_log=lambda level, msg: rec.panel_lines.append(msg),
        logger=rec,
    )
    return QQDeployService(plugin)


def _step_of(svc: QQDeployService, rec: _Recorder, emit):
    """与 `deploy()` 里那个 `step` 闭包同形（含 `_emit` 这一跳）。"""

    def step(key: str, msg: str, **ex) -> None:
        rec.steps.append({"step": key, "message": msg, **ex})
        svc._emit(emit, key, msg, **ex)

    return step


def _stub(monkeypatch, tmp_path, *, node_available, requirement, downloads: list) -> None:
    monkeypatch.setattr(np, "node_available", lambda: node_available)
    monkeypatch.setattr(np, "node_requirement", lambda **kw: requirement)
    monkeypatch.setattr(ds, "bundled_napcat_dir", lambda: tmp_path / "NapCat.Shell")
    monkeypatch.setattr(ni, "asset_name", lambda: ASSET)

    async def fake_download(asset, downloads_dir, progress=None, on_attempt=None):
        downloads.append(asset)
        if on_attempt is not None:
            on_attempt(1, 2, f"https://gh-proxy.com/https://github.com/x/{asset}", 0)
        if progress is not None:
            progress(3, 29_482_717)
        downloads_dir.mkdir(parents=True, exist_ok=True)
        zip_path = Path(downloads_dir) / asset
        zip_path.write_bytes(b"")
        return zip_path

    monkeypatch.setattr(ni, "download_asset", fake_download)
    monkeypatch.setattr(
        ni, "extract_zip",
        lambda zip_path, target, *, progress=None: target.mkdir(parents=True, exist_ok=True),
    )


# ── 二、Windows 缺 node：照样下载 ───────────────────────────

async def test_windows_missing_node_still_reaches_the_download(monkeypatch, tmp_path):
    rec = _Recorder()
    svc = _svc(tmp_path, rec)
    downloads: list[str] = []
    emit = rec.events.append
    _stub(monkeypatch, tmp_path,
          node_available=(False, "未找到 node 可执行文件"),
          requirement=(False, "Windows 上 NapCat 跑在 QQ 自带的 Node 运行时里，不需要系统 Node"),
          downloads=downloads)

    target = await svc._fetch(_step_of(svc, rec, emit), emit)

    assert downloads == [ASSET], (
        "缺系统 node 时下载根本没发起 —— 这正是真机上「卡在下载 NapCat」："
        "下载代码一行没跑，用户却在等它下载")
    assert target == tmp_path / "NapCat.Shell"
    assert rec.steps[0]["step"] == "node", (
        "Node 检查必须**单独成步**：混在 fetch 里时，检查失败在界面上就显示成"
        "「获取/下载 NapCat」那一步失败 —— 用户只能理解为「卡在下载」")
    assert "未检测到系统 Node" in rec.steps[0]["message"]
    assert any(s["step"] == "fetch" for s in rec.steps), "下载这一步没有发生"


async def test_progress_lines_are_written_to_the_file_log(monkeypatch, tmp_path):
    """面板是内存缓冲（500 行、重启即失）；排查"卡在哪一步"只能靠文件日志。"""
    rec = _Recorder()
    svc = _svc(tmp_path, rec)
    emit = rec.events.append
    _stub(monkeypatch, tmp_path,
          node_available=(True, "v24.13.1"),
          requirement=(False, "Windows 上不需要系统 Node"),
          downloads=[])

    await svc._fetch(_step_of(svc, rec, emit), emit)

    joined = "\n".join(rec.file_lines)
    assert "[Deploy] Node 就绪: v24.13.1" in joined
    assert "尝试下载源 1/2: gh-proxy.com" in joined, "换源/续传那行没落盘 —— 事后看不出它在重试"
    assert "[Deploy] 已解包到" in joined
    assert rec.panel_lines, "面板那份（_emit_log）不许因为落盘而丢掉"


def test_file_logging_failure_does_not_break_the_deploy():
    """日志写失败只是少一条记录，不该把部署掀翻。"""
    class _BrokenLogger:
        def info(self, *a, **k):
            raise OSError("disk full")

    plugin = SimpleNamespace(
        data_path=lambda name: Path(".") / name,
        _emit_log=lambda *a, **k: None,
        logger=_BrokenLogger(),
    )
    svc = QQDeployService(plugin)
    seen: list[dict] = []
    svc._emit(seen.append, "fetch", "下载 NapCat.Shell.zip…")     # 不抛就算过
    assert seen and seen[0]["label"] == "下载并解包 NapCat"


# ── 三、非 Windows 缺 node：拦在下载之前 ────────────────────

async def test_posix_missing_node_refuses_before_any_download(monkeypatch, tmp_path):
    rec = _Recorder()
    svc = _svc(tmp_path, rec)
    downloads: list[str] = []
    emit = rec.events.append
    requirement = (True, "非 Windows 是 node napcat.mjs，需要系统 Node 18+")
    _stub(monkeypatch, tmp_path,
          node_available=(False, "未找到 node 可执行文件"),
          requirement=requirement, downloads=downloads)

    with pytest.raises(RuntimeError) as exc:
        await svc._fetch(_step_of(svc, rec, emit), emit)

    assert "缺少 Node.js" in str(exc.value)
    assert requirement[1] in str(exc.value), "报错要带上理由（用户才知道该装什么）"
    assert downloads == [], "非 Windows 缺 node 必须拦在下载之前，白下一个 29MB 没意义"


# ── 四、页面别在检查之前就喊"要下载几分钟" ──────────────────

def test_status_page_does_not_promise_a_download_before_checking():
    from _ui_source import read

    text = read("status.html")
    assert "首次需要下载 NapCat，可能需要几分钟" not in text, (
        "点击瞬间就宣称「要下载几分钟」，而失败可能发生在它根本不需要下载的时候 —— "
        "用户看到的就是「卡在下载」")
    assert "先检查运行环境" in text
