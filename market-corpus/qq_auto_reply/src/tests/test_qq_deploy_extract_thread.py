"""解包挪进工作线程之后，进度回投不能断。

`extract_zip` 是同步的，直接跑在协程里会把事件循环整段堵死 —— 消息管线、SSE 心跳
全停，而且积压的进度事件会在解锁瞬间成堆乱序吐出（界面看着像"获取 NapCat 又跑了
两遍"，其实只有一遍）。所以它走 `asyncio.to_thread`。

但回调也跟着进了工作线程，而 emit 最终走到 `_spawn_push_ui_event` 里的
`asyncio.get_running_loop()` —— 在非循环线程上必然抛 RuntimeError 并**被静默吞掉**。
少了 `call_soon_threadsafe` 那一跳，解包进度会整段消失，且不报任何错。这条用例就是
把"emit 只能发生在事件循环线程上"钉死。

`_progress_last` 清零那条是源码级断言（跑整条 `deploy()` 要铺下载/解包/启动/等码
一串替身，不值当）—— 与 `test_qq_deploy_order.py` 同一个路子。
"""
from __future__ import annotations

import asyncio
import inspect
import threading
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import deploy_service as ds
from plugin.plugins.qq_auto_reply.deploy_service import QQDeployService


def _svc(tmp_path) -> QQDeployService:
    plugin = SimpleNamespace(
        data_path=lambda name: tmp_path / name,
        _emit_log=lambda *a, **k: None,
    )
    return QQDeployService(plugin)


def _stub_install(monkeypatch, tmp_path, *, extract_progress) -> None:
    """替换下载/解包。解包**在工作线程里**触发进度，与真实实现一致。"""
    monkeypatch.setattr(ds.napcat_platform, "node_available", lambda: (True, "v24.13.1"))
    monkeypatch.setattr(ds, "bundled_napcat_dir", lambda: tmp_path / "NapCat.Shell")
    monkeypatch.setattr(ds.napcat_install, "asset_name", lambda: "NapCat.Shell.zip")

    async def fake_download(asset, downloads, progress=None):
        zip_path = tmp_path / "NapCat.Shell.zip"
        zip_path.write_bytes(b"")
        return zip_path

    def fake_extract(zip_path, target, *, progress=None):
        def work():
            for done, total in extract_progress:
                # 真实实现是 extract_zip 里的同步循环，回调就在这个线程上触发
                progress(done, total)

        t = threading.Thread(target=work, name="fake-extract")
        t.start()
        t.join()
        target.mkdir(parents=True, exist_ok=True)
        return target

    monkeypatch.setattr(ds.napcat_install, "download_asset", fake_download)
    monkeypatch.setattr(ds.napcat_install, "extract_zip", fake_extract)


async def test_extract_progress_is_emitted_on_the_loop_thread(monkeypatch, tmp_path):
    svc = _svc(tmp_path)
    loop_thread = threading.current_thread()
    seen: list[tuple[dict, str]] = []

    # 记录 emit 时所在线程，而不是在 emit 里断言 —— 线程里抛的 AssertionError
    # 只会打到 stderr，测试照常"通过"。
    def emit(payload: dict) -> None:
        seen.append((payload, threading.current_thread().name))

    _stub_install(monkeypatch, tmp_path, extract_progress=[(14, 690), (35, 690)])

    await svc._fetch(lambda *a, **k: None, emit)
    await asyncio.sleep(0)  # 让 call_soon 投回来的汇报跑完

    extract = [(p, name) for p, name in seen if p.get("phase") == "extract"]
    assert [p["progress"] for p, _ in extract] == [2, 5], "解包进度没回报上来"

    names = {name for _, name in extract}
    assert names == {loop_thread.name}, (
        f"解包进度是在 {names} 上 emit 的，不是事件循环线程 {loop_thread.name!r} —— "
        "真实链路里 _spawn_push_ui_event 的 get_running_loop() 会抛 RuntimeError "
        "并被静默吞掉，解包进度会整段消失且不报错")


def test_extract_runs_off_the_event_loop():
    """解包必须经 to_thread：同步跑会把事件循环整段堵死。"""
    assert "asyncio.to_thread" in inspect.getsource(QQDeployService._fetch)


def test_deploy_resets_the_progress_throttle():
    """节流表是实例状态、跨次部署不清零：第二次跑到某个百分比时，若它恰好等于上次
    留下的最后值，那一行会被当成"重复"整行吞掉（进度凭空少一格）。"""
    assert "_progress_last.clear()" in inspect.getsource(QQDeployService.deploy)
