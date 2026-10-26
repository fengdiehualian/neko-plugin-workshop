"""Windows 媒体会话（SMTC）读取 —— 让插件知道"视频播到第几秒"。

## 为什么需要它

插件原来只能用「检测到视频起算的墙钟时间」估计播放位置（见 _schedule.py）。
一旦用户**拖动进度条**或**开倍速**，估算立刻失真 —— 这是实测踩到的问题。

SMTC（System Media Transport Controls）是 Windows 自带的媒体状态通道：
浏览器会把正在播放的视频上报给它。实测（联想浏览器 + bilibili）能读到：

    status=Playing/Paused   rate=倍速   pos=位置(秒)   end=总长(秒)   title=视频标题

三点实测结论（决定了 _playback.py 的外推算法）：
  1. `pos` **只在媒体事件点更新**（播放/暂停/拖动/变速），播放中不逐秒刷新；
     所以要靠 `upd` 时间戳做外推。
  2. 暂停时的 `pos` 是准确位置（实测 547.43，与外推值一致）。
  3. `rate` 真实反映倍速（实测读到 2 / 3）。

## 为什么用常驻 PowerShell 子进程

  - 插件要"人人下载即用"，**不能加 pip 依赖**（winsdk / winrt 都需要装包）。
  - Windows 自带的 PowerShell 能直接访问 WinRT API（已实测），零安装。
  - 启动一次（实测 ~0.4s）后常驻，每秒吐一行 JSON，Python 侧读 stdout。
  - 放在插件进程外，崩了也不影响插件：任何环节失败都静默降级为 None，
    插件退回墙钟估算，功能不受影响。

## 隐私

只读"当前正在播放的媒体标题/进度"这类系统本来就会显示在音量弹窗里的
信息，不读任何个人数据，不写盘，不上传。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time

__all__ = ["SmtcReader"]


# PowerShell 脚本：常驻循环，每 interval 毫秒输出一行 JSON 数组。
# ★ 用 [Console]::Out.WriteLine 直接写标准输出（重定向下缓冲行为最稳）。
# ★ 空列表要显式写 "[]"：ConvertTo-Json -InputObject @() 什么都输出不出来。
_PS_SCRIPT_TEMPLATE = r"""
param([int]$ParentPid = __PARENT_PID__, [int]$IntervalMs = __INTERVAL_MS__)
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $task = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
  if (-not $task.Wait(4000)) { throw 'await-timeout' }
  return $task.Result
}
$mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
while ($true) {
  # ★ 父进程（插件）没了就自杀：否则插件被强杀/崩溃时，这个子进程会变成孤儿
  #   并一直跑下去（shutdown 钩子在强杀时不一定执行得到）。
  if ($ParentPid -gt 0 -and -not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) {
    break
  }
  $rows = @()
  try {
    $mgr = Await ($mgrType::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
    foreach ($s in $mgr.GetSessions()) {
      $r = [ordered]@{ app = "$($s.SourceAppUserModelId)" }
      try {
        $pb = $s.GetPlaybackInfo()
        $r['status'] = "$($pb.PlaybackStatus)"
        try { $r['rate'] = $pb.PlaybackRate } catch { $r['rate'] = 0 }
      } catch { $r['status'] = 'ERR' }
      try {
        $tl = $s.GetTimelineProperties()
        $r['pos'] = [math]::Round($tl.Position.TotalSeconds, 2)
        $r['end'] = [math]::Round($tl.EndTime.TotalSeconds, 2)
        $r['upd'] = [double]$tl.LastUpdatedTime.ToUnixTimeMilliseconds() / 1000.0
      } catch { $r['pos'] = -1 }
      try {
        $mp = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
        $r['title'] = "$($mp.Title)"
      } catch { $r['title'] = '' }
      $rows += [pscustomobject]$r
    }
  } catch {
    $rows = @([pscustomobject]@{ err = "$($_.Exception.Message)" })
  }
  if ($rows.Count -eq 0) {
    [Console]::Out.WriteLine('[]')
  } else {
    [Console]::Out.WriteLine((ConvertTo-Json -InputObject @($rows) -Compress -Depth 4))
  }
  Start-Sleep -Milliseconds __INTERVAL_MS__
}
"""


class SmtcReader:
    """常驻读取 Windows 媒体会话。全部失败路径都静默降级。

    用法：
        reader = SmtcReader(logger=self.logger)
        reader.start()            # 懒启动：第一次检测到 B站窗口时调用
        frames = reader.latest()  # 每次轮询取最新一帧（list[dict] 或 None）
        reader.stop()             # 插件 shutdown 时调用
    """

    def __init__(self, *, interval_ms: int = 1000, logger=None) -> None:
        self._interval_ms = max(300, int(interval_ms))
        self._logger = logger
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._frames: list[dict] | None = None
        self._rx_at: float = 0.0          # 最近一次收到数据的时间（本地时钟）
        self._lock = threading.Lock()
        self._stopping = False

    # ------------------------------------------------------------------ 生命周期

    def available(self) -> bool:
        """平台是否支持（非 Windows 一律不可用）。"""
        return sys.platform == "win32"

    def start(self) -> bool:
        """启动常驻读取进程（幂等）。返回是否已就绪。"""
        if not self.available() or self._stopping:
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True
        try:
            script = (
                _PS_SCRIPT_TEMPLATE
                .replace("__PARENT_PID__", str(os.getpid()))
                .replace("__INTERVAL_MS__", str(self._interval_ms))
            )
            # -EncodedCommand：脚本以 UTF-16LE base64 传入，不落盘、不受执行策略影响
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            self._proc = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand", encoded,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:  # noqa: BLE001 - 起不来就降级
            self._log("启动 SMTC 读取失败（降级为墙钟估算）: %s", exc)
            self._proc = None
            return False

        self._thread = threading.Thread(
            target=self._read_loop, name="neko_video_smtc", daemon=True
        )
        self._thread.start()
        self._log("SMTC 读取已启动（interval=%dms）", self._interval_ms)
        return True

    def stop(self) -> None:
        """停止子进程（插件 shutdown 时调用）。"""
        self._stopping = True
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._log("SMTC 读取已停止")

    def alive(self) -> bool:
        """读取进程是否还活着（用于决定要不要受控重启）。"""
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------ 读取

    def latest(self) -> list[dict] | None:
        """最新一帧媒体会话列表（附加本地接收时间 `at`）。

        返回 None 表示：不可用 / 还没收到数据 / 数据太旧（进程可能已死）。
        调用方拿到 None 时应退回墙钟估算。
        """
        with self._lock:
            frames = self._frames
            rx = self._rx_at
        if frames is None:
            return None
        # 数据超过 10 秒没更新 → 视为失联（正常情况每秒都有一行）
        if time.time() - rx > 10.0:
            return None
        out = []
        for f in frames:
            if isinstance(f, dict) and "err" not in f:
                g = dict(f)
                g["at"] = rx
                out.append(g)
        return out

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:  # noqa: BLE001 - 坏行忽略
                    continue
                if not isinstance(data, list):
                    data = [data]
                with self._lock:
                    self._frames = [d for d in data if isinstance(d, dict)]
                    self._rx_at = time.time()
        except Exception:  # noqa: BLE001 - 读流异常：静默退出，下次 start() 会重启
            pass
        finally:
            # 进程结束：清掉引用，允许下次 start() 重启
            with self._lock:
                self._frames = None
                self._rx_at = 0.0
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ 杂项

    def _log(self, msg: str, *args) -> None:
        if self._logger is not None:
            try:
                self._logger.info(msg, *args)
            except Exception:  # noqa: BLE001
                pass