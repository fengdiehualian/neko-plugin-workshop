"""一键部署编排：定位 → 获取 → 写 OneBot 配置 → 启动 → 出二维码。

每一步都通过 ``emit`` 回调上报（插件把它转成 SSE 推给界面）。下载实测可能几十秒到
几分钟（镜像速度不稳），没有进度反馈用户会以为卡死。

止于"出二维码" —— 扫码登录必须人工，绕不过去。
"""

from __future__ import annotations

import asyncio
import functools
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from . import napcat_install, napcat_onebot_config, napcat_platform
from .napcat_service import bundled_napcat_dir

#: 步骤名 → 界面展示用的标签
STEP_LABELS: dict[str, str] = {
    "locate": "定位 NapCat",
    "fetch": "获取 NapCat",
    "config": "写入 OneBot 配置",
    "start": "启动 NapCat",
    "qrcode": "等待登录二维码",
}

#: 启动后等二维码出现的上限。NapCat 首次启动要先拉起 QQ，比后续启动慢。
QRCODE_WAIT_SECONDS = 60.0


class QQDeployService:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        #: (step, phase) → 上次已上报的百分比，用于节流
        self._progress_last: dict[tuple[str, str], int] = {}

    # ── 内部 ────────────────────────────────────────────────

    def _settings(self) -> dict:
        s = getattr(self.plugin, "_qq_settings", None)
        return s if isinstance(s, dict) else {}

    def _emit(self, emit: Callable[[dict], None] | None, step: str,
              message: str, **extra: Any) -> None:
        payload = {"step": step, "label": STEP_LABELS.get(step, step),
                   "message": message, **extra}
        if emit is not None:
            try:
                emit(payload)
            except Exception:
                pass
        self.plugin._emit_log("INFO", f"[Deploy] {message}")

    def _progress(self, emit, step: str, done: int, total: int, *,
                  phase: str = "download") -> None:
        """上报一次进度。

        ``phase="download"`` 按 MB 显示，``"extract"`` 按条目数 —— 一次解包 690 个文件，
        套用 MB 格式会显示成 "690.0 / 690.0 MB"，纯属误导。

        按百分比节流：一次下载会产生几百个 chunk 回调，全推给 SSE 会把界面日志刷爆，
        也会把真正有用的行挤没。
        """
        pct = int(done * 100 / total) if total else 0
        if phase == "extract":
            text = f"{done}/{total} 个文件"
        elif total:
            text = f"{done / 1048576:.1f} / {total / 1048576:.1f} MB"
        else:
            text = f"已下载 {done / 1048576:.1f} MB"

        key = (step, phase)
        if self._progress_last.get(key) == pct:
            return
        self._progress_last[key] = pct
        self._emit(emit, step, text, progress=pct,
                   downloaded_bytes=done, total_bytes=total, phase=phase)

    async def _ensure_token(self, emit=None) -> str:
        """复用已有 token；为空则生成一个并**同时**持久化。

        两边 token 不一致是"连不上但毫无提示"的经典原因 —— 生成后立刻写回设置，
        NapCat 侧由 ``napcat_onebot_config`` 写同一份。
        """
        settings = self._settings()
        token = str(settings.get("token") or "").strip()
        if token:
            return token
        token = secrets.token_urlsafe(12)
        settings["token"] = token
        try:
            await self.plugin.settings_service.persist_business_config()
            self._emit(emit, "config", "已生成反向 WS Token 并保存")
        except Exception as e:
            self._emit(emit, "config", f"Token 已生成，但保存失败: {e}")
        return token

    # ── 主流程 ──────────────────────────────────────────────

    async def deploy(self, *, uin: str = "", force: bool = False,
                     auto_start: bool = True,
                     emit: Callable[[dict], None] | None = None) -> dict:
        """跑完整条部署。返回结果字典；失败抛 ``RuntimeError``。"""
        # 节流表是实例状态、跨次部署不清零：第二次跑到某个百分比时，若它恰好等于
        # 上次留下的最后值，那一行会被当成"重复"整行吞掉（表现为进度凭空少一格）。
        self._progress_last.clear()

        svc = self.plugin.napcat_service
        steps: list[dict] = []

        def step(key: str, msg: str, **ex: Any) -> None:
            steps.append({"step": key, "message": msg, **ex})
            self._emit(emit, key, msg, **ex)

        # ① 定位 -------------------------------------------------
        configured = svc.get_configured_napcat_path()
        napcat_dir = svc.get_napcat_directory()
        launcher = napcat_platform.find_launcher(napcat_dir) if napcat_dir else None

        if configured and launcher is None:
            # 用户显式指了一个目录却没有启动器 —— 不要"帮"他装到别处，
            # 那会让设置与实际位置长期不一致，下次更难看懂。
            raise RuntimeError(
                f"napcat_directory 指向的目录里没有 NapCat 启动器: {configured}"
            )
        if launcher is not None and not force:
            step("locate", f"已有可用 NapCat: {napcat_dir}")
        else:
            step("locate", "未发现可用 NapCat，准备下载")
            napcat_dir = await self._fetch(step, emit)

        # ③ 写配置（放在启动之前，省一次热读往返；不是硬性要求）--------
        mode = str(self._settings().get("qq_connection_mode") or "napcat")
        uin = str(uin or "").strip()
        # 先把目录/地址写回插件：一是配置页要显示得对，二是下面 _write_config
        # 读的就是这里补出来的 onebot_url —— 顺序反了两边就对不上。
        await self._persist_connection_settings(napcat_dir, mode, step, emit)
        if uin:
            await self._write_config(napcat_dir, uin, mode, step, emit)
            # 记下"这份配置是插件预写的"：登录轮询靠它区分判据 —— 预写过就不能再拿
            # ``onebot11_<uin>.json`` 出现当登录信号，那文件此刻就已经在了。
            self.plugin._deploy_prewrote_onebot_uin = str(uin)
        else:
            self.plugin._deploy_prewrote_onebot_uin = ""
            step("config", "未填机器人 QQ 号：扫码登录后请点「补写 OneBot 配置」",
                 needs_uin=True)

        # ④ 先起自动回复，**再**启动 NapCat ------------------------
        #
        # 反向模式下插件是**监听**方：先把耳朵竖起来，NapCat 起来后拨进来即可。
        # 顺序反过来的话，NapCat 会先往一个还没人听的端口拨，白失败几轮
        # （正向模式更明显：直接报连接错误）。
        auto: dict[str, Any] = {"ok": False, "status": "", "error": ""}
        if auto_start:
            auto = await self.plugin._restart_auto_reply_runtime(True)
            step("start", "自动回复已启动，等待 NapCat 接入" if auto["ok"]
                 else f"自动回复暂未启动：{auto['error']}")

        # ⑤ 启动 NapCat -------------------------------------------
        await svc.ensure_napcat_started()
        err = svc.get_startup_error()
        if err:
            raise RuntimeError(err)
        step("start", "NapCat 已启动")

        # ⑥ 二维码 -----------------------------------------------
        ready = await self._wait_qrcode(svc)
        step("qrcode", "二维码已就绪，请扫码登录" if ready
             else "暂未出现二维码，NapCat 可能仍在启动（可点「刷新二维码」重试）",
             ready=ready)

        return {
            "ok": True,
            "napcat_directory": str(napcat_dir),
            "uin": uin,
            "qrcode_ready": ready,
            "needs_uin": not uin,
            "steps": steps,
            **self.plugin._auto_start_fields(auto),
        }

    async def _fetch(self, step, emit) -> Path:
        """下载并解包到插件自带位置。"""
        ok, ver = napcat_platform.node_available()
        if not ok:
            raise RuntimeError(
                f"缺少 Node.js（{ver}）。NapCat 的发行包是 Node 程序，需要系统已安装 Node 18+；"
                "插件不会代为安装系统级软件包。"
            )
        self._emit(emit, "fetch", f"Node 就绪: {ver}")

        target = bundled_napcat_dir()
        asset = napcat_install.asset_name()
        downloads = self.plugin.data_path("downloads")

        step("fetch", f"下载 {asset}（{napcat_install.NAPCAT_VERSION}）…")
        zip_path = await napcat_install.download_asset(
            asset, downloads,
            progress=lambda d, t: self._progress(emit, "fetch", d, t),
        )
        step("fetch", f"下载完成并通过校验: {zip_path.stat().st_size / 1048576:.1f} MB")

        # 解包挪到工作线程。690 个文件的同步复制跑在协程里会把事件循环整段堵死：
        # 期间消息管线、SSE 心跳全停，而且积压的进度事件会在解锁瞬间成堆乱序吐出
        # （界面看着像"获取 NapCat 又跑了两遍"，其实只有一遍）。
        #
        # ⚠️ 回调也跟着进了工作线程，不能再直接 emit —— emit 最终走到
        # ``_spawn_push_ui_event`` 里的 ``asyncio.get_running_loop()``，在非事件循环
        # 线程里必然抛 RuntimeError 并**被静默吞掉**，解包进度会整段消失。
        # 所以先在线程外拿住循环，再用 ``call_soon_threadsafe`` 把汇报投回来 ——
        # ``_progress``（连同它节流用的 ``_progress_last``）仍然只跑在循环线程上。
        loop = asyncio.get_running_loop()

        def _on_extract_progress(done: int, total: int) -> None:
            try:
                loop.call_soon_threadsafe(
                    functools.partial(self._progress, emit, "fetch", done, total,
                                      phase="extract"))
            except RuntimeError:
                pass  # 循环已关（部署被打断/进程在退）：进度是尽力而为，别掀翻解包

        await asyncio.to_thread(
            napcat_install.extract_zip, zip_path, target,
            progress=_on_extract_progress,
        )
        # 校验通过、解包完成，zip 已无用；留着白占 28MB。
        zip_path.unlink(missing_ok=True)
        step("fetch", f"已解包到 {target}")
        return target

    async def _persist_connection_settings(self, napcat_dir: Path, mode: str,
                                           step, emit) -> None:
        """把这次部署**实际用到**的连接设置写回插件。

        不写回的话，NapCat 那边的配置其实是对的（``dial_url`` / ``host_port_of``
        对空地址都有兜底），但插件这边两个设置会一直是空的：

        - 配置页显示空白目录、空白地址，与实际状态对不上；
        - 插件反向监听的默认值与 NapCat 被写入的目标只是"各自的默认碰巧一致"，
          不是显式约定的同一个值 —— 哪天默认值改一处，两边就会静默错开。

        只填空值，不覆盖用户已经填过的。
        """
        settings = self._settings()
        changed: list[str] = []

        if not str(settings.get("napcat_directory") or "").strip():
            settings["napcat_directory"] = str(napcat_dir)
            changed.append(f"NapCat 目录 → {napcat_dir}")

        if not str(settings.get("onebot_url") or "").strip():
            default_url = self.plugin.settings_service._default_onebot_url(mode)
            settings["onebot_url"] = default_url
            changed.append(f"通信地址 → {default_url}")

        if not changed:
            return
        try:
            await self.plugin.settings_service.persist_business_config()
        except Exception as e:
            self._emit(emit, "config", f"连接设置已更新，但落盘失败: {e}")
            return
        step("config", "已写回连接设置：" + "；".join(changed))

    async def _write_config(self, napcat_dir: Path, uin: str, mode: str,
                            step, emit) -> Path:
        """写入连接配置 —— **反向和正向两条都写**。

        ``onebot_url`` 的含义随模式而变（反向=我们的监听地址，正向=NapCat 的服务端）。
        当前模式那侧用设置里的值，另一侧用该模式的默认值。两条都配好后，用户在
        N.E.K.O 里切模式**不用重配 NapCat、也不用重启它**；只写当前那一条的话，
        每次切模式都得把整套部署重跑一遍。
        """
        token = await self._ensure_token(emit)
        listen = str(self._settings().get("onebot_url") or "")

        if mode == "napcat_forward":
            fwd_host, fwd_port = napcat_onebot_config.host_port_of(listen)
            reverse_url = napcat_onebot_config.DEFAULT_REVERSE_DIAL
        else:
            fwd_host = napcat_onebot_config.DEFAULT_FORWARD_HOST
            fwd_port = napcat_onebot_config.DEFAULT_FORWARD_PORT
            reverse_url = napcat_onebot_config.dial_url(listen)

        path = napcat_onebot_config.onebot_config_path(napcat_dir, uin)
        napcat_onebot_config.apply(
            path, reverse_url=reverse_url, forward_host=fwd_host,
            forward_port=fwd_port, token=token,
        )
        step("config",
             f"已写入双向连接配置（反向: NapCat 连 {reverse_url}；"
             f"正向: NapCat 监听 {fwd_host}:{fwd_port}）: {path.name}")
        return path

    async def _wait_qrcode(self, svc) -> bool:
        deadline = time.time() + QRCODE_WAIT_SECONDS
        while time.time() < deadline:
            try:
                if await svc.sync_napcat_qrcode_into_static():
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
        return False

    async def poll_login(self, *, auto_start: bool = True,
                         emit: Callable[[dict], None] | None = None) -> dict:
        """扫一次"扫码登录成功了没"—— 一键部署之后的收尾轮询。

        判据两条，"真的登进去了"优先：

        ① **运行时已连上并报出 self_id**。填了 QQ 号时，插件在部署阶段就把 OneBot
           配置写好并指向自己了，NapCat 登录后直接拨进来 —— 这条最准，它就是登录
           成功本身。
        ② **``config/onebot11_<uin>.json`` 出现**。没填 QQ 号时插件没写过这个文件，
           它是 NapCat 登录后自己建的；这条在 OneBot 连上**之前**就可见，是那个阶段
           唯一能用的信号。

        ②只在插件**没预写过**配置时才算数：预写过的话该文件从部署那一刻就在，
        拿它当登录信号会在用户还没扫码时就误判成功。

        没出现就报 ``pending``，由前端继续轮询；确认了就就地收尾。自动登录账号在
        **登录确认之后**才记 —— 提前记的是用户手填的号，未必是他扫码登进去的那个。

        **防重入**：收尾会起运行时，而前端是定时轮询 —— 不记状态的话每轮都会重做。
        做完记下 uin，之后同一个号一律返回 ``already``。
        """
        svc = self.plugin.napcat_service
        napcat_dir = svc.get_napcat_directory()
        if not napcat_dir:
            raise RuntimeError("还没定位到 NapCat 目录，请先执行一键部署")

        prewrote = str(getattr(self.plugin, "_deploy_prewrote_onebot_uin", "") or "")
        uin = await self._detect_login(napcat_dir, prewrote=prewrote)
        if not uin:
            return {"status": "pending"}
        if getattr(self, "_login_applied_uin", "") == uin:
            return {"status": "already", "uin": uin}

        if napcat_onebot_config.set_auto_login_account(napcat_dir, uin):
            self._emit(emit, "config", f"已记录自动登录账号: {uin}")

        if prewrote:
            # 配置在部署阶段就写好了，登录后 NapCat 直接拨了进来 —— 此刻那条连接是
            # **活的**，重启运行时会把它掐断。所以这条分支只记录账号，不碰运行时。
            self._login_applied_uin = uin
            return {"status": "completed", "uin": uin,
                    **self.plugin._auto_start_fields(
                        {"ok": True, "status": "already_running", "error": ""})}

        result = await self.apply_onebot_config(uin=uin, restart=False, emit=emit)
        self._login_applied_uin = uin

        # ⚠️ 必须自己把自动回复拉起来。服务层的 ``apply_onebot_config`` 只做
        # "写配置 + 重启 NapCat"——"按新配置启动自动回复"那一步在**入口包装层**
        # （``_deploy_apply_onebot``）里。这里直接调服务，绕过了那层，不补这一步
        # 就是收尾收一半：配置对了、NapCat 也重启了，但自动回复没起来，
        # 用户还得手动点一次「启动」。
        auto = await self._restart_auto_reply_runtime(bool(auto_start))
        return {"status": "completed", **result,
                **self.plugin._auto_start_fields(auto)}

    async def _detect_login(self, napcat_dir: Path, *, prewrote: str) -> str:
        """已登录的 QQ 号；还没登录返回空串。判据见 ``poll_login`` 的说明。"""
        runtime = getattr(self.plugin, "runtime_service", None)
        if runtime is not None:
            try:
                payload = await runtime.fetch_login_status_payload()
            except Exception:
                payload = {}
            if isinstance(payload, dict) and payload.get("status") == "online":
                self_id = str(payload.get("self_id") or "").strip()
                if self_id:
                    return self_id
        if prewrote:
            # 插件预写过配置 → 文件判据此刻恒真，不能用
            return ""
        found = napcat_onebot_config.list_onebot_configs(napcat_dir)
        if not found:
            return ""
        return napcat_onebot_config.uin_from_path(found[-1])

    # ── 扫码登录后的补写 ────────────────────────────────────

    async def apply_onebot_config(self, *, uin: str = "", restart: bool = False,
                                  emit: Callable[[dict], None] | None = None) -> dict:
        """登录后补写 OneBot 配置（预置路径没走通时的兜底）。

        ``uin`` 为空时自动取最新的 ``onebot11_*.json`` —— 扫码登录成功后 NapCat 会
        自己建出那个文件，靠扫目录就能知道登的是哪个号。
        """
        svc = self.plugin.napcat_service
        napcat_dir = svc.get_napcat_directory()
        if not napcat_dir:
            raise RuntimeError("还没定位到 NapCat 目录，请先执行一键部署")

        uin = str(uin or "").strip()
        if not uin:
            found = napcat_onebot_config.list_onebot_configs(napcat_dir)
            if not found:
                raise RuntimeError(
                    "还没发现任何 onebot11_*.json —— 请先在 NapCat 里扫码登录成功后再试"
                )
            uin = napcat_onebot_config.uin_from_path(found[-1])
            self._emit(emit, "config", f"从配置目录发现已登录账号: {uin}")

        # 登录成功之后把自动登录记上：下次启动 NapCat 直接快速登录，不用再扫码。
        # 依据在 NapCat 本体（napcat.mjs）：启动时读 WebUIConfig.autoLoginAccount，
        # 有值就 quickLoginWithUin；登录态没缓存住时它自己回落二维码，不会卡死。
        if uin and napcat_onebot_config.set_auto_login_account(napcat_dir, uin):
            self._emit(emit, "config", f"已设置 NapCat 自动登录账号: {uin}")

        mode = str(self._settings().get("qq_connection_mode") or "napcat")
        # 与一键部署同一条口径：先把空白设置补上再写 NapCat 的配置。
        await self._persist_connection_settings(
            napcat_dir, mode, lambda k, m, **e: self._emit(emit, k, m, **e), emit)
        path = await self._write_config(napcat_dir, uin, mode,
                                        lambda k, m, **e: self._emit(emit, k, m, **e), emit)

        if restart:
            # **默认不重启**：NapCat 会热读 OneBot 配置，改完直接生效。
            # 早先默认重启，依据是"NapCat 启动时才读网络配置"——那个前提不成立；
            # 代价还很实在：刚扫码登录成功就把 NapCat 重启掉，会话被掐断，
            # 只能靠快速登录捞回来。这个参数留着是给"确实需要重来一遍"的场合。
            await svc.stop_managed_napcat()
            await svc.ensure_napcat_started()
            self._emit(emit, "start", "已重启 NapCat")

        return {"ok": True, "uin": uin, "config_path": str(path), "restarted": bool(restart)}
