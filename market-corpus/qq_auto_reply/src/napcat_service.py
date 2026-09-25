from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Callable

from . import napcat_onebot_config, napcat_platform


def bundled_napcat_dir() -> Path:
    """插件自带 NapCat 的默认位置：``<插件包目录>/NapCat.Shell``。

    一键部署下载来的 NapCat 装在这里，打包链路（``prepare_nuitka_plugins``）也按这个
    位置把它搬进分发包 —— 两边约定同一个路径，所以部署完不需要用户再填
    ``napcat_directory``。
    """
    return Path(__file__).parent / "NapCat.Shell"


def _copy_over(src: Path, dst: Path) -> None:
    """把 src 覆盖拷贝到 dst，父目录按需创建。

    走 ``asyncio.to_thread`` 调用：``shutil.copyfile`` 是阻塞 IO，即使文件很小也不该
    占着事件循环。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


class QQNapcatService:
    """NapCat process / QR / readiness management -- a pure transport component that
    depends on no plugin object.

    Decoupled by injecting ``get_settings``/``get_qq_client``/``config_dir``/
    ``logger``/``emit_log``; the process and startup-error state is owned here
    (``_napcat_process``/``_manages_napcat_process``/``_startup_error``), so any
    plugin can create it to manage NapCat without exposing its own internals.
    """

    #: OneBot connect-timeout error -- transient: NapCat may still be starting
    #: (QR login / slow start) and connect later. It must not be treated as a hard
    #: failure that short-circuits retry, otherwise a genuinely starting NapCat
    #: would stop being polled for the late connection.
    TRANSIENT_TIMEOUT_ERROR = "NapCat 已尝试启动，但没有客户端连接到反向 WS 服务器"
    FORWARD_TRANSIENT_TIMEOUT_ERROR = "NapCat 已启动，但正向 WebSocket 连接未建立（NapCat 可能仍在登录，或未开启 WebSocket 服务器）"

    def __init__(
        self,
        *,
        get_settings: Callable[[], dict] | None = None,
        get_qq_client: Callable[[], Any] | None = None,
        config_dir: str | Path | None = None,
        logger: Any = None,
        emit_log: Any = None,
    ):
        self._get_settings = get_settings or (lambda: {})
        self._get_qq_client = get_qq_client or (lambda: None)
        # ``config_dir`` 的语义是**插件包目录**（SDK 的 config_dir 是 plugin_dir 的别名），
        # 静态资源在它下面的 static/。这里曾经多带一层 "static"，会让二维码落到
        # <包>/static/static/cache/ 而前端读 <包>/static/cache/ 读不到。
        self._config_dir = Path(config_dir) if config_dir else Path(__file__).parent
        self.logger = logger
        self._emit_log = emit_log or (lambda level, msg: None)
        # Process / error state owned by the transport layer.
        self._napcat_process: asyncio.subprocess.Process | None = None
        self._manages_napcat_process: bool = False
        self._startup_error: str | None = None

    @property
    def napcat_process(self) -> asyncio.subprocess.Process | None:
        return self._napcat_process

    @property
    def manages_napcat_process(self) -> bool:
        return self._manages_napcat_process

    def _transient_timeout_errors(self) -> set[str]:
        """All transient timeout texts, across connection modes (reverse + forward).

        Deciding whether a saved timeout error is transient must be **independent**
        of the current mode: a forward-mode timeout written before switching to
        reverse would otherwise be re-classified as a hard failure and
        ``wait_for_onebot_ready`` would short-circuit instead of polling the late
        connection.
        """
        return {self.TRANSIENT_TIMEOUT_ERROR, self.FORWARD_TRANSIENT_TIMEOUT_ERROR}

    def _transient_timeout_error(self) -> str:
        """OneBot connect-timeout text, chosen by mode (the one written at set time).

        Reverse: no client connected to our reverse WS server;
        forward: our dial-out has not reached NapCat (process still starting /
        logging in, or NapCat's WS server is off). Both are transient, not hard.
        """
        mode = str((self._get_settings() or {}).get("qq_connection_mode") or "napcat").strip()
        if mode == "napcat_forward":
            return self.FORWARD_TRANSIENT_TIMEOUT_ERROR
        return self.TRANSIENT_TIMEOUT_ERROR

    def has_hard_startup_error(self) -> bool:
        """Whether the failure is a "hard failure" -- retry is pointless
        (missing dir / launcher / process won't start).

        OneBot connect timeouts (reverse/forward texts) are transient -- NapCat may
        still be starting, so they are not hard: retry keeps polling for the late
        connection instead of short-circuiting.
        """
        err = self.get_startup_error()
        return bool(err) and err not in self._transient_timeout_errors()

    def get_configured_napcat_path(self) -> str:
        """用户显式配置的 NapCat 目录；未配置返回空串。

        ``"."`` 与空串同义 —— 它是 ``str(Path())`` 的样子，是"没有路径"的残留，
        不是一个有意义的显式选择（本插件从不把 NapCat 装在当前工作目录）。早期
        版本会把解析结果回填进设置，于是 "." 被当成用户填的目录存了下来，导致
        一键部署认定"目录里没有启动器"而拒绝安装；这里兜一层，老配置也能自愈。
        """
        raw = str((self._get_settings() or {}).get("napcat_directory") or "").strip()
        if raw in (".", "./", ".\\"):
            return ""
        return raw

    def get_napcat_directory(self) -> Path:
        configured = self.get_configured_napcat_path()
        if configured:
            configured_path = Path(configured)
            if configured_path.is_file():
                return configured_path.parent
            return configured_path
        # 未配置时回落到插件自带位置（一键部署装在那里，打包也搬那里）。
        # 只在**确实存在启动器**时才认：否则维持"未配置"语义，让
        # ensure_napcat_started 保持"用户可能自己开着 NapCat"的宽容行为 ——
        # 返回一个空目录会让它报一个其实没发生的硬错误。
        bundled = bundled_napcat_dir()
        if napcat_platform.find_launcher(bundled) is not None:
            return bundled
        return Path()

    def get_napcat_launch_target(self) -> Path:
        configured = self.get_configured_napcat_path()
        if configured:
            return Path(configured)
        return self.get_napcat_directory()

    def find_napcat_launcher(self) -> Path | None:
        """定位 NapCat 启动器。

        ``napcat_directory`` 按历史契约可以直接指向启动器文件本身，也可以指目录 ——
        前者原样返回，后者交给 :mod:`napcat_platform` 按平台挑候选（Windows 是 .bat
        系列，POSIX 是 ``napcat.mjs``）。
        """
        launch_target = self.get_napcat_launch_target()
        if launch_target.is_file():
            return launch_target
        return napcat_platform.find_launcher(launch_target)

    def _build_missing_launcher_error(self) -> str:
        launch_target = self.get_napcat_launch_target()
        configured = str((self._get_settings() or {}).get("napcat_directory") or "").strip()
        if configured:
            return f"NapCat 启动器不存在: {launch_target}，需要指向 launcher-user.bat、launcher.bat 或其所在目录"
        return f"NapCat 启动器不存在: {launch_target}，请先配置 napcat_directory 或确认内置 NapCat.Shell 完整"

    def clear_startup_error(self) -> None:
        self._startup_error = None

    def get_startup_error(self) -> str:
        return str(self._startup_error or "").strip()

    def set_startup_error(self, message: str | None) -> None:
        self._startup_error = str(message or "").strip() or None

    def _set_startup_error(self, message: str) -> None:
        self.set_startup_error(message)

    def _extract_onebot_port(self) -> int | None:
        raw_url = str((self._get_settings() or {}).get("onebot_url") or "").strip()
        if not raw_url:
            qq_client = self._get_qq_client()
            raw_url = str(getattr(qq_client, "onebot_url", "") or "").strip()
        if not raw_url:
            return None
        if raw_url.startswith("ws://"):
            raw_url = raw_url[5:]
        elif raw_url.startswith("wss://"):
            raw_url = raw_url[6:]
        host_port = raw_url.split("/", 1)[0]
        if ":" not in host_port:
            return 443 if raw_url.startswith("wss://") else 80
        try:
            return int(host_port.rsplit(":", 1)[1])
        except ValueError:
            return None

    async def wait_for_onebot_ready(self, *, timeout_seconds: float = 20.0, poll_interval: float = 0.5) -> bool:
        """Wait for NapCat to connect to this server's reverse WS.

        In reverse WS mode we don't dial the external port; we poll whether an
        OneBot client has connected to our server.
        """
        qq_client = self._get_qq_client()
        if qq_client and qq_client.is_connected():
            self.clear_startup_error()
            return True
        # Hard failure (missing dir / launcher / process won't start) returns
        # immediately instead of idle-waiting the full timeout -- otherwise the
        # frontend would report a false timeout while NapCat never started. OneBot
        # connect timeout is transient (NapCat may still be starting), not hard:
        # retry keeps polling for the late connection.
        if self.has_hard_startup_error():
            return False
        deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_seconds or 20.0))
        while asyncio.get_running_loop().time() < deadline:
            qq_client = self._get_qq_client()
            if qq_client and qq_client.is_connected():
                self.clear_startup_error()
                return True
            # If the launcher gets flagged hard during polling, short-circuit rather
            # than idling out the whole window.
            if self.has_hard_startup_error():
                return False
            await asyncio.sleep(max(0.1, float(poll_interval or 0.5)))
            # Sleep may cross the deadline; during it OneBot may have connected or a
            # hard error may have been written. Returning to the loop top would exit
            # because the while condition is now False, so do one final check here to
            # avoid reporting a false timeout or overwriting a real startup error.
            qq_client = self._get_qq_client()
            if qq_client and qq_client.is_connected():
                self.clear_startup_error()
                return True
            if self.has_hard_startup_error():
                return False
        self._set_startup_error(self._transient_timeout_error())
        return False

    def _napcat_log_dir(self) -> Path:
        return self.get_napcat_directory() / "logs"

    def get_webui_url(self) -> str:
        """Build the WebUI URL from NapCat config/webui.json."""
        import json as _json
        napcat_dir = self.get_napcat_directory()
        webui_json = napcat_dir / "config" / "webui.json"
        if not webui_json.exists():
            return ""
        try:
            with open(webui_json, "r", encoding="utf-8") as f:
                cfg = _json.loads(f.read())
            host = str(cfg.get("host") or "127.0.0.1").strip()
            if host in ("::", "0.0.0.0", ""):
                host = "127.0.0.1"
            port = int(cfg.get("port") or 6099)
            token = str(cfg.get("token") or "").strip()
            if token:
                return f"http://{host}:{port}/webui?token={token}"
            return f"http://{host}:{port}/webui"
        except Exception:
            return ""

    # ── 登录二维码 ────────────────────────────────────────────

    def get_napcat_qrcode_path(self) -> Path:
        """NapCat 写登录二维码的位置。

        未登录时 NapCat 会不断重写这个文件（实测 147×147 PNG），登录成功后就不再更新。
        """
        return self.get_napcat_directory() / "cache" / "qrcode.png"

    def _qrcode_static_path(self) -> Path:
        """插件静态目录里的二维码副本 —— 前端通过 ``ui/cache/qrcode.png`` 读它。

        ``_config_dir`` 是**插件包目录**（SDK 的 ``config_dir`` 是 ``plugin_dir`` 的
        兼容别名），所以落点是 ``<包>/static/cache/qrcode.png``，与
        ``runtime_service.build_runtime_status()`` 的判断和静态 UI 的挂载点一致。
        """
        return self._config_dir / "static" / "cache" / "qrcode.png"

    async def sync_napcat_qrcode_into_static(self) -> bool:
        """把 NapCat 当前的登录二维码拷进插件静态目录；返回是否有可用二维码。

        二维码的源在 NapCat 那边，插件静态目录是前端唯一能取到的路径（``runtime_service``
        靠 ``static/cache/qrcode.png`` 是否存在来决定要不要给前端 ``qrcode_url``）。
        NapCat 未登录期间每次都会重写源文件，所以这里每次都覆盖拷贝。

        **源不存在时会一并删掉副本**：否则登录成功后界面会一直挂着一张早就失效的二维码，
        而用户无从判断它已经没用了 —— 清掉后前端会显示「暂未检测到二维码」。
        """
        src = self.get_napcat_qrcode_path()
        dst = self._qrcode_static_path()
        try:
            if not src.is_file():
                if dst.is_file():
                    await asyncio.to_thread(dst.unlink)
                return False
            await asyncio.to_thread(_copy_over, src, dst)
            return True
        except Exception as e:
            self._emit_log("DEBUG", f"同步登录二维码失败: {e}")
            return False

    async def _read_napcat_webui_lines(self) -> list[str]:
        """Return NapCat WebUI access info."""
        url = self.get_webui_url()
        if url:
            return [f"NapCat WebUI: {url}"]
        return []

    def _sync_auto_login_account(self) -> bool:
        """按 ``config/`` 里已登录的账号补一次自动登录设置；改了返回 True。

        给"扫过码、却从没点过『补写 OneBot 配置』"的用户兜底：账号配置
        （``napcat_<uin>.json``）是 NapCat 登录成功后自己生成的，扫目录就知道登的
        是哪个号，不必等用户再点一次。

        幂等：已经是这个号就不写文件。
        """
        napcat_dir = self.get_napcat_directory()
        if not napcat_dir or not napcat_dir.is_dir():
            return False
        found = napcat_onebot_config.list_onebot_configs(napcat_dir)
        if not found:
            return False
        uin = napcat_onebot_config.uin_from_path(found[-1])
        if not uin or not napcat_onebot_config.set_auto_login_account(napcat_dir, uin):
            return False
        self._emit_log("INFO", f"已设置 NapCat 自动登录账号: {uin}")
        return True

    async def ensure_napcat_started(self) -> None:
        # After a hard failure (missing dir / launcher / process won't start) don't
        # retry: retrying is pointless and only repeats the error + relaunch attempt,
        # and the frontend gets no clear failure reason.
        if self.has_hard_startup_error():
            return
        # 判「有没有可用的 NapCat」而不是「设置项填没填」。插件自带 NapCat 时
        # （一键部署装到 <插件目录>/NapCat.Shell）用户根本没填 napcat_directory，
        # 但目录是可用的 —— 按设置项判会让这种情况**永远不启动**且毫无报错，
        # 表现就是"打开界面 NapCat 没起来"。
        #
        # ⚠️ 不能写成 ``if not self.get_napcat_directory()``：``pathlib.Path`` 没有
        # ``__bool__``，空 Path 也是 truthy，那个判据恒不成立（实测踩过）。
        # 只有"既没配置、自带位置也找不到启动器"才静默返回 —— 那是用户可能自己
        # 开着 NapCat 的情况，仍不是硬失败：wait_for_onebot_ready 会继续轮询它。
        if not self.get_configured_napcat_path() and self.find_napcat_launcher() is None:
            return
        if self._napcat_process and self._napcat_process.returncode is None:
            return
        launcher = self.find_napcat_launcher()
        if launcher is None:
            mode = str((self._get_settings() or {}).get("qq_connection_mode") or "napcat").strip()
            if mode == "napcat_forward":
                # For forward mode, local NapCat launch is **best-effort**: a missing
                # launcher only warns and doesn't set a hard error -- forward can still
                # connect to a remote / manually started NapCat, and bootstrap() should
                # not enter the failure branch (wait_for_onebot_ready polls the forward
                # dial result).
                self._emit_log("WARN", self._build_missing_launcher_error())
                return
            self._set_startup_error(self._build_missing_launcher_error())
            return
        # 启动前补一次自动登录账号：扫过码但没点过「补写 OneBot 配置」的用户，
        # 账号配置其实已经躺在 config/ 里了 —— 这里按目录扫出来记上，下次启动
        # 就能快速登录。幂等，所以每次启动跑一遍无妨。
        self._sync_auto_login_account()
        try:
            show_window = bool((self._get_settings() or {}).get("show_napcat_window", False))
            if not show_window:
                # 藏了窗口就没有控制台了。NapCat 默认只写控制台（fileLog=false），
                # 而我们把它的 stdout/stderr 都丢进了 DEVNULL —— 不补这一刀，
                # 它的日志就哪儿都不会留，出了事连它的输出都看不到。
                try:
                    if napcat_onebot_config.ensure_file_log(self.get_napcat_directory()):
                        self._emit_log("INFO", "已打开 NapCat 文件日志（后台启动，控制台不可见）")
                except Exception as e:
                    self._emit_log("WARN", f"打开 NapCat 文件日志失败: {e}")
            # 平台差异全在 launch_spec 里：Windows 走 cmd.exe + creationflags，
            # POSIX 走 node + start_new_session。这里不再无条件传 creationflags
            # —— 那个 kwarg 在 POSIX 上非法，会让启动直接抛错。
            spec = napcat_platform.launch_spec(launcher, show_window=show_window)
            self._napcat_process = await asyncio.create_subprocess_exec(
                *spec.argv,
                cwd=str(spec.cwd),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **spec.kwargs,
            )
            self._manages_napcat_process = True
            self.clear_startup_error()
            pid = self._napcat_process.pid
            if self.logger:
                self.logger.info(
                    f"Started NapCat: {launcher} (pid={pid}, show_window={show_window})"
                )
            self._emit_log("INFO", f"NapCat 已启动 PID={pid}")
        except Exception as e:
            self._set_startup_error(f"启动 NapCat 失败: {e}")
            if self.logger:
                self.logger.warning(f"Failed to start NapCat launcher {launcher}: {e}")

    async def stop_managed_napcat(self) -> None:
        if not self._manages_napcat_process:
            return
        process = self._napcat_process
        self._napcat_process = None
        self._manages_napcat_process = False
        if not process or process.returncode is not None:
            return
        pid = process.pid
        try:
            argv = napcat_platform.taskkill_argv(pid)
            if argv:
                # Windows：/T 连带子进程一起杀 —— NapCat 会拉起 QQ 本体，只杀
                # cmd 包装器会留下孤儿进程占着端口。
                kill_proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
                self._emit_log("INFO", f"NapCat 进程树已终止 PID={pid}")
            elif napcat_platform.signal_terminate(pid):
                # POSIX：``launch_spec`` 用了 start_new_session，子进程即进程组组长，
                # 整组发 SIGTERM。同样是为了带走 NapCat 拉起的 QQ 本体。
                self._emit_log("INFO", f"NapCat 进程组已终止 PID={pid}")
            else:
                raise RuntimeError("该平台没有可用的终止手段")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to kill NapCat process tree (PID={pid}): {e}")
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            # SIGTERM 没送走（POSIX 上 NapCat 带着子进程时常见）：补一发 SIGKILL。
            if not napcat_platform.is_windows() and napcat_platform.signal_terminate(pid, force=True):
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
