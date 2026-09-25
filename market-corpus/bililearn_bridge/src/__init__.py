"""BiliLearn Bridge —— 让 N.E.K.O 猫娘桥接独立的 BiliLearn Web 程序。

设计目标：
    BiliLearn（B站 AI 学习机器人）打包成独立 exe 后，默认「启动不弹出面板」，
    仅在系统托盘提供入口。本插件作为 N.E.K.O 与这个独立程序之间的桥：
      * 宿主启动时按配置拉起 exe（无头 serve 模式，不弹浏览器面板）；
      * 通过 BiliLearn 自带的 Web API（http://127.0.0.1:<port>）查询/控制状态；
      * 提供「打开面板」入口，由猫娘/用户在需要时手动弹出网页面板。

零第三方依赖：仅使用标准库（subprocess / urllib / webbrowser / asyncio）。

注意：N.E.K.O 的市场发布校验器（``neko-plugin check -r``）要求入口类
**必须在本文件（插件包的 ``__init__.py``）中定义**，而非从子模块 re-export，
因此类直接定义于此。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import webbrowser
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)

PLUGIN_ID = "bililearn_bridge"
DEFAULT_PORT = 18083
# 健康端点（web_launcher.is_our_panel 依赖的契约）
HEALTH_PATH = "/api/health"


@neko_plugin
class BiliLearnBridgePlugin(NekoPluginBase):
    """猫娘的 BiliLearn 桥接插件：拉起独立 exe 并桥接其 Web API。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        # 运行期配置
        self.exe_path: str = ""
        self.port: int = DEFAULT_PORT
        self.launch_mode: str = "serve"
        self.auto_launch: bool = True
        # 由本插件拉起的子进程句柄（仅当我们自己启动的才负责停止）
        self._proc: asyncio.subprocess.Process | None = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    @lifecycle(id="startup")
    async def on_startup(self, **_) -> Any:
        # 配置读取：启动阶段 config 总线可能尚未完全就绪，限定超时并兜底默认值，
        # 绝不让 startup 因 config 卡死而触发宿主 10s 超时。
        try:
            cfg = await self.config.dump(timeout=2.0)
        except Exception as exc:
            self.logger.warning("读取插件配置失败（回退默认值）: %s", exc)
            cfg = {}
        bc = cfg.get(PLUGIN_ID) or {}
        self.exe_path = (bc.get("exe_path") or "").strip()
        self.port = int(bc.get("port") or DEFAULT_PORT)
        self.launch_mode = (bc.get("launch_mode") or "serve").strip() or "serve"
        self.auto_launch = bool(bc.get("auto_launch", True))

        exe = self._resolve_exe()
        if not exe:
            self.logger.warning(
                "未找到 BiliLearn Web.exe，请在插件配置 exe_path 中指定；桥接功能处于待命状态。"
            )
            return Ok({"status": "no_exe", "auto_launch": self.auto_launch})

        if not self.auto_launch:
            return Ok({"status": "idle", "exe_found": True, "port": self.port})

        # 已在运行则直接附接，不重复拉起
        if await self._is_healthy():
            self.logger.info("检测到 BiliLearn 已在运行，直接附接（port=%s）", self.port)
            return Ok({"status": "attached", "port": self.port, "url": self._url()})

        # 拉起 exe，但【不在 startup 内阻塞等待就绪】：N.E.K.O 给 startup 仅 10s 预算，
        # 而 PyInstaller onefile 首次冷启动解包可能 >10s。改为：发起拉起后最多等待 budget
        # 秒，即便未就绪也立即返回（后续由 start/status/open_panel 入口按需确认）。
        if not await self._spawn(exe):
            return Err(SdkError("BiliLearn 启动失败（无法拉起进程），请检查 exe 路径与日志。"))
        ready = await self._wait_ready(2.0)
        if ready:
            return Ok({"status": "started", "port": self.port, "url": self._url()})
        self.logger.info("BiliLearn 已发起拉起（pid=%s），仍在后台就绪中。", self._proc.pid)
        return Ok({"status": "launching", "port": self.port, "url": self._url()})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_) -> Any:
        await self._stop_managed()
        return Ok({"status": "stopped"})

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _resolve_exe(self) -> str | None:
        """按 配置路径 -> PATH -> 常见构建/安装位置 顺序定位 exe。"""
        if self.exe_path:
            p = shutil.which(self.exe_path) or self.exe_path
            if os.path.isfile(p):
                return p
        # PATH 中的命令名
        found = shutil.which("BiliLearn Web") or shutil.which("BiliLearnWeb")
        if found:
            return found
        candidates = [
            # 本工作区构建产物（build_windows_exe.bat 的输出）
            r"C:\Users\Administrator\WorkBuddy\2026-08-13-16-21-23\bilibili_learning_bot\dist\BiliLearn Web\BiliLearn Web.exe",
            # 常见安装位置
            os.path.expandvars(r"%LOCALAPPDATA%\BiliLearn\BiliLearn Web.exe"),
            r"C:\Program Files\BiliLearn\BiliLearn Web.exe",
            r"C:\Program Files (x86)\BiliLearn\BiliLearn Web.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    async def _is_healthy(self) -> bool:
        """探测 BiliLearn 健康端点是否就绪。"""
        try:
            res = await asyncio.to_thread(self._request, "GET", HEALTH_PATH, timeout=2.0)
        except Exception:
            return False
        return bool(res.get("ok")) and res.get("status") == 200

    async def _spawn(self, exe: str) -> bool:
        """以无头模式拉起 exe（立即返回，不等待就绪）。"""
        mode = self.launch_mode if self.launch_mode in ("serve", "silent") else "serve"
        env = os.environ.copy()
        env["WEB_PORT"] = str(self.port)
        # 即便面板自身有自动弹出逻辑也强制关闭（双保险）
        env["BILI_WEB_AUTO_OPEN"] = "0"
        # 桥接模式下由 N.E.K.O 完全托管：隐藏系统托盘，面板改由 open_panel 入口按需弹出
        env["BILI_TRAY_DISABLED"] = "1"
        try:
            self._proc = await asyncio.create_subprocess_exec(
                exe, f"--{mode}",
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.logger.info("已拉起 BiliLearn 子进程（pid=%s, mode=%s）", self._proc.pid, mode)
            return True
        except (OSError, ValueError) as exc:
            self.logger.error("拉起 BiliLearn 失败: %s", exc)
            self._proc = None
            return False

    async def _wait_ready(self, timeout: float = 30.0) -> bool:
        """轮询健康端点直到就绪或超时（调用方自行保证不超预算）。"""
        ticks = max(1, int(timeout / 0.5))
        for _ in range(ticks):
            if await self._is_healthy():
                return True
            proc = self._proc
            if proc is not None and proc.returncode is not None:
                self.logger.error("BiliLearn 子进程已退出（code=%s）", proc.returncode)
                self._proc = None
                return False
            await asyncio.sleep(0.5)
        self.logger.warning("BiliLearn 在 %.0fs 内未就绪（可能端口被占用或依赖缺失）", timeout)
        return False

    async def _launch(self, exe: str, wait: float = 30.0) -> bool:
        """拉起 exe 并等待其就绪（供 start/restart 入口使用，预算充裕）。"""
        if not await self._spawn(exe):
            return False
        return await self._wait_ready(wait)

    async def _stop_managed(self) -> None:
        """仅停止由本插件拉起的子进程；附接的已有实例不碰。"""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=8)
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass
        except ProcessLookupError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("停止 BiliLearn 异常: %s", exc)

    # ---- HTTP 桥接（标准库 urllib，运行于线程，避免阻塞事件循环） ----
    def _request(self, method: str, path: str, *, json_body: Any = None,
                 timeout: float = 10.0) -> dict:
        """同步请求 BiliLearn Web API，返回统一结构。"""
        import urllib.error
        import urllib.request

        url = f"{self._url()}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                payload = json.loads(raw) if raw else {}
                return {"ok": True, "status": resp.status, "data": payload}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return {"ok": False, "status": exc.code, "data": payload}
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {"ok": False, "status": 0, "data": {"error": str(exc)}}

    async def _api(self, method: str, path: str, *, json_body: Any = None,
                   timeout: float = 10.0) -> dict:
        if not await self._is_healthy():
            return {"ok": False, "status": 0, "data": {"error": "BiliLearn 未运行"}}
        return await asyncio.to_thread(self._request, method, path,
                                       json_body=json_body, timeout=timeout)

    # ------------------------------------------------------------------ #
    # 桥接入口
    # ------------------------------------------------------------------ #
    @plugin_entry(
        id="start",
        name="启动 BiliLearn",
        description="确保 BiliLearn 程序在运行：若已在运行则附接，否则以无头模式拉起 exe（不弹面板）。返回访问地址。",
        timeout=40.0,
    )
    async def start(self, **_) -> Any:
        exe = self._resolve_exe()
        if not exe:
            return Err(SdkError("未找到 BiliLearn Web.exe，请在插件配置 exe_path 指定路径。"))
        if await self._is_healthy():
            return Ok({"status": "running", "port": self.port, "url": self._url()})
        if await self._launch(exe):
            return Ok({"status": "started", "port": self.port, "url": self._url()})
        return Err(SdkError("BiliLearn 启动失败，请检查 exe 路径、端口占用或依赖。"))

    @plugin_entry(
        id="stop",
        name="停止 BiliLearn",
        description="停止由本插件拉起的 BiliLearn 进程。若 BiliLearn 是手动/其它方式启动的，本操作不会终止它。",
        timeout=15.0,
    )
    async def stop(self, **_) -> Any:
        await self._stop_managed()
        return Ok({"status": "stopped", "still_running": await self._is_healthy()})

    @plugin_entry(
        id="restart",
        name="重启 BiliLearn",
        description="先停止本插件管理的进程，再以无头模式重新拉起。",
        timeout=50.0,
    )
    async def restart(self, **_) -> Any:
        await self._stop_managed()
        return await self.start()

    @plugin_entry(
        id="status",
        name="BiliLearn 状态",
        description="查询 BiliLearn 是否运行、端口与访问地址；若运行则附带 /api/info 摘要。",
    )
    async def status(self, **_) -> Any:
        healthy = await self._is_healthy()
        info = None
        if healthy:
            info_res = await self._api("GET", "/api/info", timeout=5)
            info = info_res.get("data") if info_res.get("ok") else None
        exe = self._resolve_exe()
        return Ok({
            "running": healthy,
            "port": self.port,
            "url": self._url() if healthy else None,
            "exe_found": bool(exe),
            "managed": self._proc is not None and self._proc.returncode is None,
            "info": info,
        })

    @plugin_entry(
        id="open_panel",
        name="打开面板",
        description="在默认浏览器中弹出 BiliLearn 网页面板（手动触发，启动默认不弹）。",
    )
    async def open_panel(self, **_) -> Any:
        if not await self._is_healthy():
            return Err(SdkError("BiliLearn 尚未运行，请先调用 start。"))
        webbrowser.open(self._url())
        return Ok({"opened": True, "url": self._url()})

    @plugin_entry(
        id="bot_start",
        name="启动机器人",
        description="通过 BiliLearn Web API 启动刷视频/学习机器人（mode 可选：auto/normal/lite）。",
        timeout=20.0,
    )
    async def bot_start(
        self,
        mode: str = "auto",
    ) -> Any:
        res = await self._api("POST", "/api/bot/start", json_body={"mode": mode}, timeout=15)
        if res["ok"]:
            return Ok(res["data"])
        return Err(SdkError(f"启动机器人失败: {res['data']}"))

    @plugin_entry(
        id="bot_stop",
        name="停止机器人",
        description="停止当前运行的机器人主循环。",
        timeout=20.0,
    )
    async def bot_stop(self, **_) -> Any:
        res = await self._api("POST", "/api/bot/stop", timeout=15)
        if res["ok"]:
            return Ok(res["data"])
        return Err(SdkError(f"停止机器人失败: {res['data']}"))

    @plugin_entry(
        id="bot_status",
        name="机器人状态",
        description="返回机器人运行状态、模式与近期输出摘要。",
    )
    async def bot_status(self, **_) -> Any:
        res = await self._api("GET", "/api/info", timeout=8)
        if res["ok"]:
            data = res["data"] or {}
            return Ok({
                "running": bool(data.get("bot", {}).get("running")),
                "mode": data.get("bot", {}).get("mode"),
                "raw": data,
            })
        return Err(SdkError(f"查询机器人状态失败: {res['data']}"))


__all__ = ["BiliLearnBridgePlugin", "PLUGIN_ID", "DEFAULT_PORT", "HEALTH_PATH"]
