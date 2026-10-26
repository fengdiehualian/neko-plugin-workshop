from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import os
import tomllib
import urllib.error
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.assistant import (
    ASSISTANT_PROTOCOL_VERSION,
    VisionAssistantClient,
    VisionAssistantProcess,
)
from plugin.plugins.hsr_companion.assistant_runtime.server import CaptureState

ROOT = Path(__file__).resolve().parents[1]


def test_assistant_client_only_accepts_loopback() -> None:
    assert VisionAssistantClient("http://localhost:8219").base_url == "http://127.0.0.1:8219"
    try:
        VisionAssistantClient("https://example.com:8219")
    except ValueError as exc:
        assert "本机" in str(exc)
    else:  # pragma: no cover - security regression guard
        raise AssertionError("remote assistant URL must be rejected")


def test_source_helper_is_a_separate_process_fallback(tmp_path: Path) -> None:
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    command, mode = manager.command()

    assert mode == "source_compatibility"
    assert command[1].endswith("assistant_runtime\\server.py") or command[1].endswith(
        "assistant_runtime/server.py"
    )
    assert command[-4:] == ["--port", "8219", "--parent-pid", str(os.getpid())]
    assert manager.snapshot()["source_available"] is True


def test_installed_entry_cannot_escape_component_root(tmp_path: Path) -> None:
    (tmp_path / "current.json").write_text(
        json.dumps({"executable": "../outside.exe"}),
        encoding="utf-8",
    )
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)

    assert manager.snapshot()["installed"] is False


def test_capture_contract_defaults_to_no_persisted_image() -> None:
    state = CaptureState()
    payload = state.snapshot(include_image=False)

    assert payload["protocol_version"] == ASSISTANT_PROTOCOL_VERSION
    assert payload["image_base64"] == ""
    assert payload["frame_seq"] == 0


def test_panel_keeps_necessary_helper_status_without_internal_details() -> None:
    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    panel = manifest["plugin"]["ui"]["panel"][0]
    source = (ROOT / panel["entry"]).read_text(encoding="utf-8")
    copy = (ROOT / "i18n/zh-CN.json").read_text(encoding="utf-8")

    assert manifest["plugin"]["version"] == "0.9.6"
    assert panel["entry"] == "ui/panel_v09.tsx"
    assert "安装独立视觉组件" not in source
    assert "运行方式：" not in source
    assert "当前可查询记录" not in source
    for text in ("无需开启语音或视频通话", "未下载独立助手", "正在运行", "启动失败或已退出",
                 "进程已启动，尚未连接", "重试下载视觉助手", "刷新状态"):
        assert text in copy
    assert 't("panel.privacyHelp")' in source[source.index('<Accordion title={t("panel.help")}>'):]
    assert "submit_game_screenshot" not in source
    assert "submit_roster_screenshot" not in source
    assert "test_proactive_message" not in source
    proactive_section = source[source.index('<Card title={t("panel.proactive")}>'):source.index('<Card title={t("diagnostics.title")}>')]
    assert "set_proactive_companionship" in proactive_section


def test_panel_keeps_download_and_action_results_visible() -> None:
    source = (ROOT / "ui" / "panel_v09.tsx").read_text(encoding="utf-8")
    copy = (ROOT / "i18n/zh-CN.json").read_text(encoding="utf-8")
    backend = (ROOT / "__init__.py").read_text(encoding="utf-8")

    assert "last_operation" in source
    assert "游戏资料和头像资料已经下载并校验完成" in backend
    assert "头像资料没有准备完成" in backend
    assert "头像资料下载失败" in copy
    assert "游戏资料下载失败" in copy
    assert "下载与校验已经完成" in copy
    assert "visibleNotice" in source


def test_frozen_host_never_launches_its_executable_as_python(tmp_path: Path, monkeypatch) -> None:
    module = importlib.import_module(VisionAssistantProcess.__module__)
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    assert manager.snapshot()["source_available"] is True
    assert manager.snapshot()["source_launchable"] is False
    with pytest.raises(RuntimeError, match="尚未下载"):
        manager.start()
    assert manager.snapshot()["mode"] == "unavailable"
    # Automatic observation remains usable through its fallback and retries are bounded.
    assert manager.start_if_due()["running"] is False


def test_frozen_host_uses_downloaded_executable(tmp_path: Path, monkeypatch) -> None:
    module = importlib.import_module(VisionAssistantProcess.__module__)
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    (tmp_path / "helper.exe").write_bytes(b"test executable")
    (tmp_path / "current.json").write_text(json.dumps({"executable": "helper.exe"}), encoding="utf-8")
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    command, mode = manager.command()
    assert command == [str(tmp_path / "helper.exe"), "--port", "8219", "--parent-pid", str(os.getpid())]
    assert mode == "external_bundle"
    assert manager.snapshot()["download_status"] == "installed"


def test_unmarked_packaged_host_is_not_a_python_interpreter(tmp_path: Path, monkeypatch) -> None:
    module = importlib.import_module(VisionAssistantProcess.__module__)
    monkeypatch.setattr(module.sys, "executable", "C:/N.E.K.O/projectneko_server.exe")
    monkeypatch.setattr(module.sys, "frozen", False, raising=False)
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    assert manager.snapshot()["source_launchable"] is False
    with pytest.raises(RuntimeError, match="尚未下载"):
        manager.command()


def test_exited_helper_is_not_reported_as_running(tmp_path: Path) -> None:
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    manager._process = SimpleNamespace(poll=lambda: 7, pid=123)
    status = manager.snapshot()
    assert status["running"] is False
    assert status["mode"] == "error"
    assert status["exit_code"] == 7
    assert "已退出" in status["last_error"]


def test_missing_release_produces_actionable_download_error(tmp_path: Path, monkeypatch) -> None:
    module = importlib.import_module(VisionAssistantProcess.__module__)

    def missing_release(*args, **kwargs):
        raise urllib.error.HTTPError("https://github.com/", 404, "Not Found", {}, None)

    monkeypatch.setattr(module.urllib.request, "urlopen", missing_release)
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    with pytest.raises(RuntimeError, match="发布页尚未提供"):
        manager.install_component()
    assert manager.snapshot()["installed"] is False


def test_component_download_verifies_hash_and_accepts_windows_bom(tmp_path: Path, monkeypatch) -> None:
    module = importlib.import_module(VisionAssistantProcess.__module__)
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("helper.exe", b"test executable")
    data = archive.getvalue()
    manifest = {
        "version": "0.9.2", "protocol_version": "1", "executable": "helper.exe",
        "archive_url": "https://github.com/WhiteFrostune/n.e.k.o_plugin_hsr_companion/releases/download/v0.9.2/helper.zip",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    responses = iter([json.dumps(manifest).encode("utf-8-sig"), data])
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: BytesIO(next(responses)))
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    assert manager.install_component()["installed"] is True
    assert manager.command()[0][0] == str(tmp_path / "0.9.2" / "helper.exe")


def test_helper_failure_does_not_block_fallback_observation(monkeypatch) -> None:
    from plugin.plugins.hsr_companion import HsrCompanionPlugin

    module = importlib.import_module(HsrCompanionPlugin.__module__)
    plugin = object.__new__(HsrCompanionPlugin)
    plugin._observer_status = {"enabled": True, "proactive_enabled": True}
    plugin._assistant_client = SimpleNamespace(health=lambda: {"connected": False})
    plugin._assistant_process = SimpleNamespace(
        start_if_due=lambda: {"mode": "unavailable"},
        snapshot=lambda: {"installed": False, "running": False},
    )
    plugin._assistant_frame_seq = 0
    async def invalidate(reason):
        return None
    plugin._invalidate_current_observation = invalidate
    window = SimpleNamespace(minimized=True, foreground=False, public=lambda: {"title": "崩坏：星穹铁道"})
    monkeypatch.setattr(module, "find_hsr_window", lambda: window)

    async def end_after_fallback(delay):
        if plugin._observer_status.get("state") == "paused":
            raise asyncio.CancelledError()

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(
        to_thread=asyncio.to_thread, sleep=end_after_fallback, CancelledError=asyncio.CancelledError,
    ))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plugin._observer_loop())
    assert plugin._observer_status["window"]["title"] == "崩坏：星穹铁道"
    assert plugin._observer_status["capture_backend"] == "plugin_compatibility"


def test_capture_dependency_failure_is_visible_in_health(monkeypatch) -> None:
    module = importlib.import_module(CaptureState.__module__)
    state = CaptureState()

    def missing_dependencies():
        state.stop.set()
        raise RuntimeError("missing_window_dependencies")

    monkeypatch.setattr(module, "_find_window", missing_dependencies)
    state.update()
    payload = state.snapshot(include_image=False)
    assert payload["capture"] == {"state": "error", "reason": "missing_window_dependencies"}


@pytest.mark.skipif(os.name != "nt", reason="Windows one-file helper lifecycle")
def test_stop_terminates_only_the_owned_helper_tree(tmp_path: Path, monkeypatch) -> None:
    module = importlib.import_module(VisionAssistantProcess.__module__)
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    stopped = []
    process = SimpleNamespace(pid=12345, poll=lambda: 0 if stopped else None, wait=lambda **kwargs: 0)
    manager._process = process
    calls = []

    def terminate_tree(command, **kwargs):
        calls.append(command)
        stopped.append(True)

    monkeypatch.setattr(module.subprocess, "run", terminate_tree)
    status = manager.stop()
    assert calls == [["taskkill", "/PID", "12345", "/T", "/F"]]
    assert status["running"] is False
    assert status["mode"] == "stopped"


def test_helper_stops_when_owning_plugin_disappears(monkeypatch) -> None:
    import psutil

    module = importlib.import_module(CaptureState.__module__)
    shutdown_calls = []
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(is_running=lambda: False))
    module._watch_parent(
        SimpleNamespace(shutdown=lambda: shutdown_calls.append(True)),
        SimpleNamespace(wait=lambda seconds: False),
        12345,
    )
    assert shutdown_calls == [True]


def test_concurrent_status_and_restart_only_launch_one_helper(tmp_path: Path, monkeypatch) -> None:
    import time

    module = importlib.import_module(VisionAssistantProcess.__module__)
    manager = VisionAssistantProcess(ROOT, assistant_root=tmp_path)
    calls = []

    def launch(*args, **kwargs):
        calls.append(args)
        time.sleep(0.05)
        return SimpleNamespace(pid=12345, poll=lambda: None)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda index: manager.start() if index % 2 else manager.start_if_due(), range(8)))
    assert len(calls) == 1
    assert all(result["running"] for result in results)


def test_concurrent_frame_readers_share_transfer_but_sequential_reads_do_not():
    import threading
    import time

    client = VisionAssistantClient()
    calls = []
    entered, release = threading.Event(), threading.Event()
    def transfer(path):
        calls.append(path)
        entered.set()
        assert release.wait(1)
        return {"frame_seq": 7, "timestamp": time.time(), "mime": "image/png",
                "image_base64": base64.b64encode(b"in-memory-image").decode(),
                "game": {"running": True, "pid": 1},
                "capture": {"state": "capturing", "isolated_window": True}}
    client._get_json = transfer
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(client.fetch_frame, 0)
        assert entered.wait(1)
        second = workers.submit(client.fetch_frame, 0)
        time.sleep(.05)
        release.set()
        a, b = first.result(), second.result()
    assert len(calls) == 1
    assert a.timestamp == b.timestamp and a.frame_seq == b.frame_seq == 7
    a.game["pid"] = 9
    assert b.game["pid"] == 1
    client.fetch_frame(0)
    assert len(calls) == 2
    assert client.fetch_frame(7) is None  # A new-sequence request cannot use seq 7.
    assert len(calls) == 3
