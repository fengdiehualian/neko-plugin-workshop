import asyncio
import os
import subprocess
import sys
from pathlib import Path

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from .agent_status import AgentStatus
from .chat_commands import balance_answer
from .companion import Companion
from .fleet import Fleet
from .recording import Recording
from .rhythm import Rhythm
from .server import start_server
from .takeover import TakeoverState, companion_result
from .widget_lifecycle import stop_widget


@neko_plugin
class NekoTokenMonitorPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.monitor = None
        self.server = None
        self.server_thread = None
        self.widget = None
        self.recording = None
        self.rhythm = None
        self.companion = None
        self.takeover = TakeoverState()

    def show_widget(self):
        if sys.platform not in ("darwin", "win32"):
            return {"shown": False, "reason": "桌面挂件支持 macOS 与 Windows", "url": "http://127.0.0.1:48923/widget"}
        if self.widget and self.widget.poll() is None:
            return {"shown": True}
        executable = (
            Path(__file__).parent / "bin" / "NekoBudgetWidget.app" / "Contents" / "MacOS" / "neko-budget-widget"
        )
        arguments = [str(executable)]
        if sys.platform == "darwin":
            arguments += ["--recording-dir", str(self.recording.path.parent)]
            if not self.recording.snapshot().get("active"):
                arguments.append("--launcher-only")
        if sys.platform == "win32":
            executable = Path(__file__).parent / "bin" / "windows" / "CatFood.exe"
            self.stop_file = Path(self.data_path("widget.stop"))
            self.stop_file.unlink(missing_ok=True)
            arguments = [str(executable), str(os.getpid()), "http://127.0.0.1:48923", str(self.stop_file),
                         "--recording-dir", str(self.recording.path.parent)]
            if not self.recording.snapshot().get("active"):
                arguments.append("--launcher-only")
        else:
            executable.chmod(executable.stat().st_mode | 0o100)
        self.logger.info("Budget widget: launching native window")
        log_path = self.data_path("widget-native.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as native_log:
            os.chmod(log_path, 0o600)
            self.widget = subprocess.Popen(
                arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=native_log, close_fds=False
            )
        self.logger.info("Budget widget: native process launched")
        return {"shown": True, "pid": self.widget.pid}

    def announce(self, text, character_name=None):
        result = self.push_message(
            source="neko_token_monitor",
            visibility=["chat"],
            ai_behavior="blind",
            parts=[{"type": "text", "text": text}],
            priority=5,
            target_lanlan=character_name or "YUI",
        )
        return bool(result.get("submitted"))

    def request_reply(self, line=None, character_name=None):
        try:
            cue = (
                (
                    "猫粮小游戏或低余额提醒的角色喊话。请沿用你原有的人设和已配置语音，仅说下面这句简短台词，不执行任何工具，不增加说明："
                    + repr(line)
                )
                if line
                else (
                    "用户刚在「猫粮」挂件主动点击了「让原版 YUI 回应」。请沿用当前人设说一句简短、关心但略带傲娇的话，句末带上“喵～”，不要编造余额，不执行工具。"
                )
            )
            result = self.push_message(
                source="neko_token_monitor",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": cue}],
                priority=5,
                target_lanlan=character_name or (self.companion.snapshot()["character_name"] if self.companion else "YUI"),
            )
            if result.get("submitted"):
                return {"submitted": True, "message": "已交给原软件；回应和出声由 NEKO 当前设置决定。"}
        except Exception:
            pass
        return {"submitted": False, "error": "原软件暂未接收回应请求，请确认 NEKO 已连接对话模型。"}

    @lifecycle(id="startup")
    async def startup(self, **_):
        self.logger.info("Budget widget: startup entered")
        if self.monitor is None:
            monitor = Fleet(self.data_path("balance-monitor"))
            self.logger.info("Budget widget: storage ready")
            self.recording = Recording(self.data_path("recording"))
            self.recording.start()
            self.rhythm = Rhythm(self.data_path("rhythm"))
            agents = AgentStatus()
            self.companion = Companion(self.data_path("yui-care"), monitor, self.recording, self.request_reply, self.announce, agent_status=agents)
            server, thread = start_server(
                monitor, request_reply=self.request_reply, agent_status=agents, recording=self.recording,
                takeover=self.takeover, rhythm=self.rhythm, companion=self.companion,
            )
            self.logger.info("Budget widget: loopback server ready")
            self.monitor, self.server, self.server_thread = monitor, server, thread
            monitor.start()
            self.companion.start()
        self.register_static_ui("launcher", cache_control="no-store")
        self.logger.info("Budget widget: UI registered")
        try:
            snapshot = self.recording.snapshot()
            if snapshot.get("active"):
                self.takeover.begin(snapshot["id"])
            show = sys.platform in ("darwin", "win32") or snapshot.get("active") or not snapshot.get("initialized")
            widget = self.show_widget() if show else {"shown": False, "reason": "已退出记录模式；可从插件入口再次打开"}
        except OSError:
            widget = {"shown": False, "reason": "桌面挂件未能启动，请查看插件面板"}
        return Ok({"panel": "http://127.0.0.1:48923", "widget": widget})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self.companion:
            await asyncio.to_thread(self.companion.close)
            self.companion = None
        if self.rhythm:
            await asyncio.to_thread(self.rhythm.close)
            self.rhythm = None
        if self.recording:
            result = await asyncio.to_thread(self.recording.action, "end")
            if result.get("summary"):
                self.announce(result["summary"])
        if self.widget and self.widget.poll() is None:
            await asyncio.to_thread(stop_widget, self.widget, sys.platform, getattr(self, "stop_file", None))
        self.widget = None
        if self.recording:
            await asyncio.to_thread(self.recording.close)
        if self.server:
            await asyncio.to_thread(self.server.shutdown)
            self.server.server_close()
            await asyncio.to_thread(self.server_thread.join, 3)
            self.server = None
        if self.monitor:
            await asyncio.to_thread(self.monitor.close)
            self.monitor = None
        return Ok({"stopped": True})

    @plugin_entry(
        id="get_balance",
        name="猫粮还剩多少",
        description="查询用户配置的猫粮余额或预算。estimated_tokens 仅为按用户价格估算；stale 为真时必须说明结果已过期，不可称为实时余额。",
    )
    async def get_balance(self, **_):
        if self.monitor is None:
            return Ok({"message": "请先启动猫粮余额插件"})
        return Ok(await asyncio.to_thread(self.monitor.refresh))

    def start_companion(self):
        if self.monitor is None or self.recording is None:
            return {"shown": False, "takeover": "failed", "message": "猫粮插件还没有启动喵～"}
        try:
            widget = self.show_widget()
        except OSError:
            return {"shown": False, "takeover": "failed", "message": "猫粮挂件暂时打不开喵～请检查插件安装。"}
        if not widget.get("shown"):
            return {"shown": False, "takeover": "failed", "message": widget.get("reason", "无法打开猫粮挂件喵～")}
        state = self.recording.action("start")
        if not state.get("active"):
            return {"shown": False, "window_opened": True, "monitor_active": False, "takeover": "failed", "message": state.get("error", "互动记录无法启动喵～")}
        self.takeover.begin(state["id"])
        return {"shown": True, "takeover": "pending", "session_id": state["id"]}

    async def open_companion(self):
        result = await asyncio.to_thread(self.start_companion)
        if result["takeover"] != "pending":
            return result
        outcome = await asyncio.to_thread(self.takeover.wait, result["session_id"], 42)
        state = self.recording.snapshot()
        if (not state.get("active") or state.get("id") != result["session_id"]) and (outcome is None or outcome.get("ok")):
            outcome = {"ok": False, "reason": "session_ended"}
        return companion_result(outcome)

    @plugin_entry(id="open_panel", name="打开猫粮面板", description="打开猫粮挂件并尝试暂停原版人物、进入 YUI 图片替代模式。")
    async def open_panel(self, **_):
        return Ok(await self.open_companion())

    @plugin_entry(
        id="show_widget",
        name="显示猫粮挂件",
        description="显示透明桌面猫娘和余额气泡，并接管原版人物。退出记录模式后可通过这个入口重新打开。",
    )
    async def show_widget_entry(self, **_):
        return Ok(await self.open_companion())

    @llm_tool(
        name="catfood_remaining",
        description="当用户用文字问“帮我看看猫粮还剩多少”“猫粮余额多少”“查询猫粮/模型剩余额度”时调用。查询猫粮插件当前选中的模型或账户，不打开挂件。请保留返回消息里的具体来源、单位、过期或手动记录说明，回复句尾带“喵～”；不要自行编造余额。",
    )
    async def catfood_remaining(self, **_):
        if self.monitor is None:
            return {"message": "猫粮插件还没有启动喵～"}
        state = await asyncio.to_thread(self.monitor.refresh)
        return {"message": balance_answer(state)}

    @llm_tool(
        name="catfood_open_monitor",
        description="当用户说“监控模式启动”“查看猫粮监测功能”“打开猫粮监测”“显示猫粮挂件/面板”时调用。只有 monitor_active=true 且 takeover=confirmed 才能说监控已启动、人物已替换。failed 或 pending 时按返回 message 说明未启动、未替换，不能因为 window_opened=true 就称已打开监控；只有 reason=accessibility 才可说权限不足，不能猜测原因。回复句尾带“喵～”。",
    )
    async def catfood_open_monitor(self, **_):
        return await self.open_companion()
