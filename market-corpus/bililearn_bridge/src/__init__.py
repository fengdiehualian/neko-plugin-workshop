"""BiliLearn Bridge v2 —— 让 N.E.K.O 猫娘完整操控独立的 BiliLearn Web 程序。

设计目标（在 v1 的“拉起 exe + 三个接口”基础上做全功能嫁接）：

* **开箱即用**：用户不需要手填 exe 路径。插件按
  「运行时已解包 → 仓库自带安装包 → 自动从 GitHub 下载 → 本地已装扫描」
  的顺序取得 BiliLearn，并自动解包到插件数据目录后无头拉起
  （不弹面板、不建托盘）；已在运行则直接附接，绝不重复拉起。
* **连得上**：面板对 ``/api/*`` 有免责声明 + 登录两道门槛。本插件自动完成
  「确认免责声明 → 登录」握手（默认账号 ``yaya``，可在插件配置里改），
  并保持会话 Cookie，让后续每个调用都落在已登录态上。
* **全能控**：把 BiliLearn 3.1.6 的 **308 个 Web API** 全部接入——
  日常功能有语义化入口（``bililearn_bot_start`` 等），长尾功能走
  ``bililearn_api_call`` / ``api_batch`` 通用透传，配合
  ``api_groups`` / ``api_search`` / ``api_describe`` 做能力发现。

零第三方依赖：仅标准库（subprocess / urllib / http.cookiejar / zipfile / asyncio）。

注意：N.E.K.O 的市场发布校验器（``neko-plugin check -r``）要求入口类
**必须在本文件（插件包的 ``__init__.py``）中定义**，而非从子模块 re-export，
因此类直接定义于此；功能入口由 ``director.BiliLearnDirector`` 路由器挂载。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import webbrowser
from pathlib import Path
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

from . import catalog, installer
from .client import BiliLearnClient
from .director import BiliLearnDirector

PLUGIN_ID = "bililearn_bridge"
DEFAULT_PORT = 18083
# 健康端点（web_launcher.is_our_panel 依赖的契约）
HEALTH_PATH = "/api/health"
# BiliLearn 3.1.6 面板服务标识（/api/health 返回的 service 字段）
SERVICE_ID = "bilibili-learning-bot-web"

# exe 名称（安装包 BiliLearn-Web-<版本>-Windows-x64.zip 解包后形如
# "BiliLearn Web/BiliLearn Web.exe"）。
_EXE_NAMES = ("BiliLearn Web.exe", "BiliLearnWeb.exe")
# 仅在配置未指定 search_dirs 时探测的常见位置（不含全盘扫描）。
_COMMON_ROOTS = (
    r"%LOCALAPPDATA%\BiliLearn",
    r"%LOCALAPPDATA%\Programs\BiliLearn",
    r"%USERPROFILE%\BiliLearn",
    r"%USERPROFILE%\Downloads",
    r"%USERPROFILE%\Desktop",
    r"C:\BiliLearn",
    r"D:\BiliLearn",
)
_MAX_SCAN_DEPTH = 3


@neko_plugin
class BiliLearnBridgePlugin(NekoPluginBase):
    """猫娘的 BiliLearn 桥接插件：拉起独立 exe 并全局桥接其 Web API。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        # 运行期配置
        self.exe_path: str = ""
        self.port: int = DEFAULT_PORT
        self.launch_mode: str = "serve"
        self.auto_launch: bool = True
        self.panel_username: str = ""
        self.panel_password: str = ""
        self.auto_setup: bool = True
        self.request_timeout: float = 20.0
        self.search_dirs: tuple[str, ...] = ()
        # 开箱即用开关：本地找不到 exe 时是否自动下载/解包（默认开）
        self.auto_install: bool = True
        # 由本插件拉起的子进程句柄（仅当我们自己启动的才负责停止）
        self._proc: asyncio.subprocess.Process | None = None
        # 实际生效端口（可能从账户注册表读到的真实端口）
        self._effective_port: int = DEFAULT_PORT
        self._client: BiliLearnClient | None = None
        # 全功能入口：bot / monitor / standby / kb / goal / agent ... + 通用透传
        self.include_router(BiliLearnDirector(prefix="bililearn_"))

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
        self.panel_username = (bc.get("panel_username") or "").strip()
        self.panel_password = bc.get("panel_password") or ""
        self.auto_setup = bool(bc.get("auto_setup", True))
        try:
            self.request_timeout = max(3.0, float(bc.get("request_timeout") or 20.0))
        except (TypeError, ValueError):
            self.request_timeout = 20.0
        raw_dirs = bc.get("search_dirs") or []
        if isinstance(raw_dirs, str):
            raw_dirs = [raw_dirs]
        self.search_dirs = tuple(str(item).strip() for item in raw_dirs if str(item).strip())
        self.auto_install = bool(bc.get("auto_install", True))

        self._effective_port = self._discover_port()
        self._reset_client()

        exe = await self._ensure_exe()
        if not exe:
            self.logger.warning(
                "未找到 BiliLearn 安装包/程序（自带包缺失、下载失败、本地未装）；"
                "可在插件配置 exe_path 中手动指定。桥接功能处于待命状态。"
            )
            return Ok({"status": "no_exe", "auto_launch": self.auto_launch})

        if not self.auto_launch:
            return Ok({"status": "idle", "exe_found": True, "port": self._effective_port})

        # 已在运行则直接附接，不重复拉起
        if await self._is_healthy():
            self.logger.info("检测到 BiliLearn 已在运行，直接附接（port=%s）", self._effective_port)
            return Ok({"status": "attached", "port": self._effective_port, "url": self._url()})

        # 拉起 exe，但【不在 startup 内阻塞等待就绪】：N.E.K.O 给 startup 仅 10s 预算，
        # 而 PyInstaller onefile 首次冷启动解包可能 >10s。改为：发起拉起后最多等待 budget
        # 秒，即便未就绪也立即返回（后续由 start/status 等入口按需确认）。
        if not await self._spawn(exe):
            return Err(SdkError("BiliLearn 启动失败（无法拉起进程），请检查 exe 路径与日志。"))
        # 启动后账户注册表可能落定真实端口，重新解析一次。
        self._effective_port = self._discover_port()
        self._reset_client()
        ready = await self._wait_ready(2.0)
        if ready:
            return Ok({"status": "started", "port": self._effective_port, "url": self._url()})
        self.logger.info("BiliLearn 已发起拉起（pid=%s），仍在后台就绪中。", self._proc.pid if self._proc else "?")
        return Ok({"status": "launching", "port": self._effective_port, "url": self._url()})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_) -> Any:
        await self._stop_managed()
        return Ok({"status": "stopped"})

    # ------------------------------------------------------------------ #
    # 内部工具：端口 / exe 定位
    # ------------------------------------------------------------------ #
    def _url(self) -> str:
        return f"http://127.0.0.1:{self._effective_port}"

    def _account_root(self) -> Path | None:
        """BiliLearn 的用户数据根目录（与 exe 的解析顺序保持一致）。"""
        override = os.getenv("BILI_ACCOUNTS_ROOT", "").strip()
        if override:
            return Path(override).expanduser()
        explicit = os.getenv("BILI_USER_DATA_DIR", "").strip()
        if explicit:
            return Path(explicit).expanduser()
        local = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "BiliLearn"
        pointer = local / "Data" / "storage_location.json"
        try:
            target = str(json.loads(pointer.read_text(encoding="utf-8-sig")).get("path") or "").strip()
            if target:
                return Path(target).expanduser()
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return local

    def _discover_port(self) -> int:
        """账户模式会把端口写进 accounts.json；以它为准，避免端口错位。

        BiliLearn 打包版首次启动会建立主账号（account-001）并固定端口，
        之后 ``WEB_PORT`` 环境变量不再生效，因此这里必须读注册表。
        """
        root = self._account_root()
        if root is None:
            return self.port
        index = root / "accounts.json"
        try:
            data = json.loads(index.read_text(encoding="utf-8-sig"))
            accounts = data.get("accounts") if isinstance(data, dict) else None
            if isinstance(accounts, list):
                for account in accounts:
                    if isinstance(account, dict) and account.get("id") == "account-001":
                        value = account.get("port")
                        if isinstance(value, int) and 1024 <= value <= 65535:
                            return value
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        config = root / "Data" / "config.json"
        try:
            value = json.loads(config.read_text(encoding="utf-8-sig")).get("web", {}).get("port")
            if isinstance(value, int) and 1024 <= value <= 65535:
                return value
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return self.port

    def _resolve_exe(self) -> str | None:
        """按 运行时已解包 -> 配置路径 -> PATH -> 常见位置 -> 自定义 search_dirs 顺序定位 exe。

        只做同步的本地查找；不含下载/解包（那些由 :meth:`_ensure_exe` 负责）。
        """
        # 0) 之前已解包到插件数据目录的运行时（开箱即用的落地结果）
        try:
            state_dir = self.data_path()
        except Exception:  # noqa: BLE001 - storage 未就绪时跳过
            state_dir = None
        found = installer.find_runtime_exe(state_dir)
        if found:
            return str(found)
        if self.exe_path:
            candidate = Path(self.exe_path).expanduser()
            if not candidate.is_absolute():
                candidate = self.plugin_dir / candidate
            if candidate.is_file():
                return str(candidate)
            found = shutil.which(self.exe_path)
            if found:
                return found
        # PATH 中的命令名
        for name in _EXE_NAMES:
            found = shutil.which(name)
            if found:
                return found
        # 常见安装位置（含安装包解包后的 "BiliLearn Web/" 子目录）
        roots = [Path(os.path.expandvars(path)) for path in _COMMON_ROOTS]
        roots.extend(Path(item).expanduser() for item in self.search_dirs)
        roots.append(self.plugin_dir.parent)
        for root in roots:
            found = self._find_exe_under(root, _MAX_SCAN_DEPTH)
            if found:
                return str(found)
        return None

    async def _ensure_exe(self) -> str | None:
        """保证有一个可用的 exe：本地没有就自动下载/解包（开箱即用）。

        顺序：本地已解包/已装 -> 仓库自带安装包 -> 自动下载安装包。
        下载与解包都放到线程里执行，避免阻塞事件循环。
        """
        exe = self._resolve_exe()
        if exe:
            return exe
        if not self.auto_install:
            return None
        try:
            state_dir = self.data_path()
        except Exception:  # noqa: BLE001 - storage 未就绪时降级
            state_dir = None
        return await asyncio.to_thread(self._install_blocking, state_dir)

    def _install_blocking(self, state_dir: Path | None) -> str | None:
        """同步执行「取得安装包 -> 解包 -> 返回 exe」。在工作线程中调用。"""
        # 1) 本地缓存（之前下载/拼接过）
        cached = installer.find_cached_installer(state_dir)
        if cached:
            self.logger.info("使用已缓存的 BiliLearn 安装包：%s", cached.name)
            exe = installer.ensure_runtime_exe(cached, state_dir, logger=self.logger)
            if exe:
                return str(exe)
        # 2) 自动从 GitHub release 下载（多镜像源自动切换）
        self.logger.info("本地没有 BiliLearn，开始自动下载最新版（多镜像源）…")
        downloaded, note = installer.download_installer(
            state_dir,
            timeout=max(60.0, self.request_timeout * 3),
            logger=self.logger,
        )
        if downloaded:
            self.logger.info("%s", note)
            exe = installer.ensure_runtime_exe(downloaded, state_dir, logger=self.logger)
            if exe:
                return str(exe)
        else:
            self.logger.error("自动下载 BiliLearn 失败：%s", note)
        # 3) 仓库自带的安装包（若附带：分卷或整包）
        bundled = installer.reassemble_bundled(self.plugin_dir, state_dir, logger=self.logger)
        if not bundled:
            bundled = installer.find_bundled_installer(self.plugin_dir)
        if bundled:
            self.logger.info("使用仓库自带的 BiliLearn 安装包：%s", bundled.name)
            exe = installer.ensure_runtime_exe(bundled, state_dir, logger=self.logger)
            if exe:
                return str(exe)
        return None

    @staticmethod
    def _find_exe_under(root: Path, depth: int) -> Path | None:
        """在 root 下浅层扫描 exe（带上限，避免卡住启动）。"""
        try:
            if not root.is_dir():
                return None
        except OSError:
            return None
        if depth <= 0:
            return None
        try:
            entries = list(os.scandir(root))
        except OSError:
            return None
        for entry in entries:
            try:
                if entry.is_file() and entry.name in _EXE_NAMES:
                    return Path(entry.path)
            except OSError:
                continue
        for entry in entries:
            try:
                if not entry.is_dir() or entry.name.startswith((".", "_", "$")):
                    continue
            except OSError:
                continue
            found = BiliLearnBridgePlugin._find_exe_under(Path(entry.path), depth - 1)
            if found:
                return found
        return None

    # ------------------------------------------------------------------ #
    # 内部工具：HTTP / 健康
    # ------------------------------------------------------------------ #
    def _reset_client(self) -> None:
        try:
            state_dir = self.data_path()
        except Exception:  # noqa: BLE001 - storage 未就绪时降级为内存会话
            state_dir = None
        self._client = BiliLearnClient(
            port=self._effective_port,
            username=self.panel_username,
            password=self.panel_password,
            auto_setup=self.auto_setup,
            state_dir=state_dir,
            timeout=self.request_timeout,
            logger=self.logger,
        )

    def _get_client(self) -> BiliLearnClient:
        if self._client is None or self._client.port != self._effective_port:
            self._reset_client()
        assert self._client is not None
        return self._client

    async def _is_healthy(self) -> bool:
        """探测面板健康端点是否就绪（会识别“是不是我们的面板”）。"""
        try:
            return await self._get_client().health()
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("健康检查失败: %s", exc)
            return False

    async def _api_call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict:
        """统一的 API 调用入口：确保服务在跑 → 自动登录 → 发请求。"""
        if not await self._is_healthy():
            # 端口可能变了（账户注册表重建），重新解析后再试一次
            self._effective_port = self._discover_port()
            if not await self._is_healthy():
                return {"ok": False, "status": 0, "data": {"message": "BiliLearn 未运行，请先调用 start。"}}
        return await self._get_client().call(method, path, body=body, query=query, timeout=timeout)

    @staticmethod
    def _shape(res: dict, action: str) -> Any:
        """把原始响应塑形为 Ok/Err，让失败以对话形式呈现而不是异常。"""
        status = res.get("status")
        data = res.get("data")
        if not res.get("ok"):
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or data.get("error") or "")
            if not message:
                message = f"HTTP {status}" if status else "请求失败"
            return Err(SdkError(f"{action}失败：{message}"))
        if isinstance(data, dict):
            payload = dict(data)
            if payload.get("ok") is False:
                message = str(payload.get("message") or "操作未成功")
                return Err(SdkError(f"{action}未成功：{message}"))
            payload.setdefault("status", status)
            return Ok(payload)
        return Ok({"status": status, "data": data})

    # ------------------------------------------------------------------ #
    # 内部工具：进程
    # ------------------------------------------------------------------ #
    async def _spawn(self, exe: str) -> bool:
        """以无头模式拉起 exe（立即返回，不等待就绪）。"""
        mode = self.launch_mode if self.launch_mode in ("serve", "silent") else "serve"
        env = os.environ.copy()
        env["WEB_PORT"] = str(self.port)
        # 即便面板自身有自动弹出逻辑也强制关闭（双保险）
        env["BILI_WEB_AUTO_OPEN"] = "0"
        # 已确认免责声明（终端层）：面板仍要求浏览器侧确认，由 client 自动完成
        env["BILI_DISCLAIMER_SKIP"] = "1"
        # 桥接模式下由 N.E.K.O 完全托管：隐藏系统托盘，面板改由 open_panel 入口按需弹出
        env["BILI_TRAY_DISABLED"] = "1"
        # 不要跟着宿主就自动跑机器人：让猫娘显式下指令
        env["BILI_BOT_AUTO_START"] = "0"
        try:
            self._proc = await asyncio.create_subprocess_exec(
                exe, f"--{mode}",
                cwd=str(Path(exe).parent),
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
        self.logger.warning("BiliLearn 在 %.0fs 内未就绪（冷启动解包可能较慢，端口 %s）", timeout, self._effective_port)
        return False

    async def _launch(self, exe: str, wait: float = 45.0) -> bool:
        """拉起 exe 并等待其就绪（供 start/restart 入口使用，预算充裕）。"""
        if not await self._spawn(exe):
            return False
        self._effective_port = self._discover_port()
        self._reset_client()
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

    # ------------------------------------------------------------------ #
    # 桥接入口：程序生命周期
    # ------------------------------------------------------------------ #
    @plugin_entry(
        id="start",
        name="启动 BiliLearn",
        description="确保 BiliLearn 程序在运行：若已在运行则附接，否则以无头模式拉起 exe（不弹面板）。返回访问地址。",
        timeout=60.0,
    )
    async def start(self, **_) -> Any:
        exe = await self._ensure_exe()
        if not exe:
            return Err(SdkError(
                "未找到 BiliLearn（自带包缺失、自动下载失败且本地未安装）；"
                "请在插件配置 exe_path 手动指定，或检查网络后重试。"
            ))
        self._effective_port = self._discover_port()
        self._reset_client()
        if await self._is_healthy():
            return Ok({"status": "running", "port": self._effective_port, "url": self._url()})
        if await self._launch(exe):
            auth = await self._get_client().ensure_auth()
            return Ok({"status": "started", "port": self._effective_port, "url": self._url(), "panel": auth})
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
        timeout=80.0,
    )
    async def restart(self, **_) -> Any:
        await self._stop_managed()
        return await self.start()

    @plugin_entry(
        id="status",
        name="BiliLearn 状态",
        description="查询程序是否运行、端口、地址与面板登录状态；运行中附带 /api/info 摘要（机器人是否在跑、B站登录、人格、心情等）。",
        timeout=30.0,
    )
    async def status(self, **_) -> Any:
        healthy = await self._is_healthy()
        info = None
        panel = None
        if healthy:
            auth = await self._get_client().ensure_auth()
            panel = auth
            if auth.get("authenticated"):
                info_res = await self._api_call("GET", "/api/info", timeout=8)
                if info_res.get("ok") and isinstance(info_res.get("data"), dict):
                    data = info_res["data"]
                    info = {
                        "version": data.get("version"),
                        "bot_running": data.get("bot_running"),
                        "bot_pid": data.get("bot_pid"),
                        "bili_logged_in": data.get("bili_logged_in"),
                        "bili_profile": data.get("bili_profile"),
                        "persona": (data.get("persona") or {}).get("active"),
                        "mood": data.get("mood"),
                        "agent_enabled": data.get("agent_enabled"),
                        "pm_enabled": data.get("pm_enabled"),
                        "standby_running": data.get("standby_running"),
                        "uptime": data.get("uptime"),
                    }
        exe = self._resolve_exe()
        return Ok({
            "running": healthy,
            "port": self._effective_port,
            "url": self._url() if healthy else None,
            "exe_found": bool(exe),
            "exe_path": exe,
            "managed": self._proc is not None and self._proc.returncode is None,
            "panel_auth": panel,
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

    # ------------------------------------------------------------------ #
    # 桥接入口：面板登录
    # ------------------------------------------------------------------ #
    @plugin_entry(
        id="login",
        name="登录面板",
        description="确保桥接会话已登录 BiliLearn 面板（自动确认免责声明并登录）。可选传入 username/password 覆盖配置。",
        timeout=30.0,
    )
    async def login(self, username: str = "", password: str = "", **_) -> Any:
        if not await self._is_healthy():
            return Err(SdkError("BiliLearn 未运行，请先调用 start。"))
        client = self._get_client()
        if username:
            client.username = username.strip()
        if password:
            client.password = password
        auth = await client.ensure_auth(force=bool(username or password))
        if auth.get("authenticated"):
            return Ok(auth)
        return Err(SdkError(str(auth.get("message") or "登录失败")))

    @plugin_entry(
        id="panel_set_credentials",
        name="设置面板凭据",
        description="保存面板登录账号到插件数据目录（下次自动登录用它），随后立即验证一次。",
        timeout=30.0,
    )
    async def panel_set_credentials(self, username: str = "", password: str = "", **_) -> Any:
        if not username.strip() or not password:
            return Err(SdkError("username 与 password 都不能为空。"))
        self.panel_username = username.strip()
        self.panel_password = password
        client = self._get_client()
        client.username, client.password = self.panel_username, self.panel_password
        client._save_credentials(self.panel_username, self.panel_password)  # noqa: SLF001 - 同包协作
        if not await self._is_healthy():
            return Ok({"saved": True, "verified": False, "message": "凭据已保存；BiliLearn 未运行，暂未验证。"})
        auth = await client.ensure_auth(force=True)
        if auth.get("authenticated"):
            return Ok({"saved": True, "verified": True, "message": "凭据已保存并验证通过。"})
        return Err(SdkError(f"凭据已保存，但登录失败：{auth.get('message')}"))


__all__ = ["BiliLearnBridgePlugin", "PLUGIN_ID", "DEFAULT_PORT", "HEALTH_PATH", "SERVICE_ID", "catalog"]
