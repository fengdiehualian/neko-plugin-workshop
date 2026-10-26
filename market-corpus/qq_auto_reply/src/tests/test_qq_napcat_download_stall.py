"""下载卡住的两条兜底：**停滞检测**与**每个源都上报**。

使用者的现场：「一键部署会卡在下载，走不动进度条」。

核实到的事实：
* 后端下载本身是好的（本机实测 2.5 秒下完 29,482,717 字节、451 次进度回调）。
* 但 `httpx.Timeout(300)` 是**全超时**：连接/读/写都是 300 秒。镜像**连上之后不再
  给数据**（第三方中转的常见故障）时，会一声不响地挂满 5 分钟 —— 界面上就是
  「进度停在最后一行不动」。而且换源、续传**完全没有上报**，用户看不出它在重试。
* `status.html`（一键部署那页）**没有进度条元素**，部署反馈是文字日志；带进度条的是
  引导页，而那根条由 9 个步骤的完成度驱动，下载期间本来就不会动。

这个文件钉住两条修复：
1. 两次收到数据之间超过 `STALL_TIMEOUT_SECONDS` → 立刻判这条源废掉（交给下一个候选源）。
2. 每个候选源都调 `on_attempt`（界面据此报「尝试下载源 i/N」+ 续传字节数）。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from plugin.plugins.qq_auto_reply import napcat_install as ni

ASSET = "NapCat.Shell.zip"


class _StallingStream:
    """先给一段数据，然后**永远不再给**（模拟连上了却不再发数据的镜像）。"""

    def __init__(self, first: bytes) -> None:
        self._first = first
        self._sent = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._sent:
            self._sent = True
            return self._first
        await asyncio.sleep(3600)      # 永远不返回
        raise StopAsyncIteration


class _StallingResponse:
    status_code = 200
    headers = {"content-length": "1000000"}

    def __init__(self, first: bytes) -> None:
        self._stream = _StallingStream(first)

    def raise_for_status(self) -> None:
        return None

    def aiter_bytes(self, chunk_size: int = 0):
        return self._stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _StallingClient:
    def __init__(self, resp) -> None:
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None):
        return self._resp


def test_stalled_stream_fails_fast_instead_of_hanging(monkeypatch, tmp_path):
    """连上之后不再给数据 → 必须在**停滞超时**内失败，而不是耗完 300 秒预算。"""
    # 把停滞窗口压到 0.5 秒，测试不必真等 30 秒；被测的是"有没有这个闸"。
    monkeypatch.setattr(ni, "STALL_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(
        ni.httpx, "AsyncClient",
        lambda *a, **k: _StallingClient(_StallingResponse(b"x" * 1024)),
    )

    dest = tmp_path / "out.bin"
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(ni._fetch("https://m.example/x.zip", dest,
                              progress=None, timeout=300, fallback_total=1000000))
    elapsed = time.monotonic() - started

    assert "停滞" in str(exc.value), f"报错没说清是停滞: {exc.value}"
    assert elapsed < 5, f"没有快速失败（耗时 {elapsed:.1f}s）—— 停滞闸没生效"
    assert dest.read_bytes() == b"x" * 1024, "已经收到的字节应当保留（续传靠它）"


def test_stall_guard_does_not_fire_while_data_keeps_coming(monkeypatch, tmp_path):
    """正常流式下载不能被停滞闸误杀。"""
    body = b"y" * (256 * 1024)

    class _Stream:
        def __init__(self):
            self._chunks = [body[i:i + 65536] for i in range(0, len(body), 65536)]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._chunks:
                raise StopAsyncIteration
            return self._chunks.pop(0)

    class _Resp(_StallingResponse):
        def __init__(self):
            self._stream = _Stream()
            self.headers = {"content-length": str(len(body))}

    monkeypatch.setattr(ni, "STALL_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(ni.httpx, "AsyncClient", lambda *a, **k: _StallingClient(_Resp()))

    dest = tmp_path / "out.bin"
    asyncio.run(ni._fetch("https://m.example/x.zip", dest,
                          progress=None, timeout=300, fallback_total=len(body)))
    assert dest.read_bytes() == body


def test_connect_timeout_is_short_not_the_whole_budget():
    """建连超时必须短：连不上就该马上换源，而不是耗完 300 秒。"""
    assert ni.CONNECT_TIMEOUT_SECONDS <= 30, (
        "建连超时太长 —— 源连不上时用户要干等"
    )
    assert ni.CONNECT_TIMEOUT_SECONDS < ni.DOWNLOAD_TIMEOUT_SECONDS


def test_every_candidate_source_is_reported(monkeypatch, tmp_path):
    """`on_attempt` 必须每个源都调一次，并带上续传字节数。"""
    seen: list[tuple[int, int, str, int]] = []

    async def _fails(url, dest, *, progress=None, timeout=0, fallback_total=0, resume_from=0):
        # 第一条先失败，制造出"换源"的局面；同时留下半截文件让续传有据可依
        Path(dest).write_bytes(b"z" * 4096)
        raise RuntimeError("boom")

    monkeypatch.setattr(ni, "_fetch", _fails)
    monkeypatch.setattr(ni, "candidate_urls", lambda asset, mirrors=None: [
        "https://a.example/x.zip", "https://b.example/x.zip",
    ])

    with pytest.raises(RuntimeError):
        asyncio.run(ni.download_asset(
            ASSET, tmp_path, on_attempt=lambda *a: seen.append(a),
        ))

    assert [s[0] for s in seen] == [1, 2], f"没有逐个源上报: {seen}"
    assert all(s[1] == 2 for s in seen), "总数不对"
    assert seen[0][3] == 0, "第一条源不该有续传字节"
    assert seen[1][3] == 4096, "换源时应报告从多少字节续传"


def test_deploy_emits_a_line_per_source():
    """deploy_service 必须把 `on_attempt` 变成一行可见的进度。"""
    import inspect

    from plugin.plugins.qq_auto_reply import deploy_service as ds

    source = inspect.getsource(ds.QQDeployService._download_and_extract) \
        if hasattr(ds.QQDeployService, "_download_and_extract") else inspect.getsource(ds.QQDeployService)
    assert "on_attempt=" in source, (
        "没有把 on_attempt 传下去 —— 换源/续传在界面上又变成静默的"
    )
    assert "尝试下载源" in source, "没有为每个源生成可见的进度文案"


def test_stall_timeout_is_far_below_the_overall_budget():
    """停滞窗口必须显著小于整次下载预算，否则"卡住"仍会挂很久。"""
    assert ni.STALL_TIMEOUT_SECONDS <= 60, (
        f"停滞窗口 {ni.STALL_TIMEOUT_SECONDS}s 太大 —— 卡住时用户要等太久"
    )
    assert ni.STALL_TIMEOUT_SECONDS < ni.DOWNLOAD_TIMEOUT_SECONDS
