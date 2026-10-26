"""Local N.E.K.O plugin for the installed Yui Home desktop runtime."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr

_COMMAND_URL = "http://127.0.0.1:48931/api/command"
_SETUP_MESSAGE = (
    "请先解压 Yui的家完整项目包，运行安装脚本，再启动 N.E.K.O.。插件中心的包是配套控制插件，不包含桌面运行环境。"
)


def _supports_runtime():
    return sys.platform == "win32"


def _invalid(message):
    return Err(SdkError(message, code="invalid_argument"))


async def _call(action, **arguments):
    try:
        return Ok(await asyncio.to_thread(_request, action, **arguments))
    except RuntimeError as exc:
        return Err(SdkError(str(exc), code="home_runtime_unavailable"))


def _installation():
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    config = base / "YuiHome/installation.json"
    if not config.is_file():
        raise RuntimeError(_SETUP_MESSAGE)
    try:
        installation = json.loads(config.read_text(encoding="utf-8"))
        root = Path(installation["root"])
        exe = Path(installation["exe"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Yui的家安装登记无效，请重新运行完整项目包的安装脚本。") from exc
    if not root.is_absolute() or not exe.is_absolute():
        raise RuntimeError("Yui的家安装登记必须使用绝对路径。")
    if not (root / "desktop.cjs").is_file() or not (root / "preload.cjs").is_file():
        raise RuntimeError("Yui的家项目目录已移动或不完整，请重新安装。")
    if exe.name.lower() != "n.e.k.o.exe" or not exe.is_file():
        raise RuntimeError("未找到已登记的 N.E.K.O. 程序，请重新安装。")
    return {**installation, "root": str(root), "exe": str(exe)}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(action: str, **arguments):
    installation = _installation()
    try:
        session = json.loads((Path(installation["root"]) / "runtime/session.json").read_text(encoding="utf-8"))
        token = session["token"]
        if not isinstance(token, str) or not token:
            raise ValueError("invalid session")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("家园窗口尚未启动，请先打开 Yui的家。") from exc
    req = Request(
        _COMMAND_URL,
        data=json.dumps({"action": action, **arguments}).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-nekocraft-token": token},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(req, timeout=60) as response:
            result = json.load(response)
    except (URLError, OSError, ValueError) as exc:
        raise RuntimeError("无法连接本机 Yui的家，请确认家园窗口已经打开。") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(result.get("error", "Yui的家操作失败") if isinstance(result, dict) else "家园响应格式无效")
    return result


@neko_plugin
class YuiHomePlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        if not _supports_runtime():
            return Ok({"name": "Yui的家", "status": "unsupported_platform", "message": "当前完整项目仅支持 Windows。"})
        try:
            installation = await asyncio.to_thread(_installation)
        except RuntimeError as exc:
            return Ok({"name": "Yui的家", "status": "setup_required", "message": str(exc)})
        try:
            await asyncio.to_thread(
                subprocess.Popen,
                [installation["exe"], "--nekocraft-desktop"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return Err(SdkError("家园程序未能启动，请检查安装路径和程序权限。", code="home_launch_failed"))
        return Ok({"name": "Yui的家", "status": "started", "readiness": "launch_requested"})

    @plugin_entry(
        id="open_home",
        name=tr("entry.open_home.name", default="打开Yui的家"),
        description=tr("entry.open_home.description", default="显示真实Yui造型与同步到Minecraft的桌面家园。"),
    )
    async def open_home(self, **_):
        return await _call("show")

    @plugin_entry(
        id="build_home",
        name=tr("entry.build_home.name", default="一起建造小屋"),
        description=tr(
            "entry.build_home.description",
            default="逐块建造小屋；全过程记入YUI的长期记忆，并同步到真正的Minecraft测试世界。",
        ),
        input_schema={
            "type": "object",
            "properties": {"style": {"type": "string", "enum": ["oak", "cherry", "stone"], "default": "oak"}},
        },
    )
    async def build_home(self, style: str = "oak", **_):
        if not isinstance(style, str) or style not in ("oak", "cherry", "stone"):
            return _invalid("小屋风格应为 oak、cherry 或 stone。")
        return await _call("build", style=style, _actor="YUI")

    @plugin_entry(
        id="save_home",
        name=tr("entry.save_home.name", default="保存到我的世界"),
        description=tr(
            "entry.save_home.description",
            default="保存原版Minecraft结构NBT与完整单人存档快照；本机多人测试世界继续实时同步。",
        ),
    )
    async def save_home(self, **_):
        return await _call("export")

    @plugin_entry(
        id="status",
        name=tr("entry.status.name", default="家园与记忆状态"),
        description=tr("entry.status.description", default="查看真实游戏同步、建造进度和YUI长期记忆写入情况。"),
    )
    async def home_status(self, **_):
        return await _call("status")

    @plugin_entry(
        id="journal",
        name=tr("entry.journal.name", default="共同建造日记"),
        description=tr("entry.journal.description", default="查看主人和Yui共同建造记录以及每条记录的记忆保存凭据。"),
    )
    async def journal(self, **_):
        return await _call("journal")

    @plugin_entry(
        id="ask_yui",
        name=tr("entry.ask_yui.name", default="问问Yui的建造建议"),
        description=tr(
            "entry.ask_yui.description",
            default="在家园聊天气泡中输入，与 N.E.K.O. 当前真实 Yui 的原有对话连接商量建造。",
        ),
    )
    async def ask_yui(self, **_):
        return await _call("open_chat")

    @plugin_entry(
        id="collaborate",
        name=tr("entry.collaborate.name", default="邀请Yui自主共建"),
        description=tr(
            "entry.collaborate.description", default="让Yui自己决定想增建什么，仅在空位置一起施工，并写入共同记忆。"
        ),
    )
    async def collaborate(self, **_):
        return await _call("collaborate")

    @plugin_entry(
        id="notify_home",
        name=tr("entry.notify_home.name", default="交给正在聊天的Yui"),
        description=tr(
            "entry.notify_home.description", default="将家园现场交给现有 YUI 对话；不创建模型、不读取角色设定。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "context": {"type": "object"},
                "request_id": {"type": "string"},
                "behavior": {"type": "string", "enum": ["respond", "read", "blind"]},
            },
            "required": ["context", "request_id"],
        },
    )
    async def notify_home(self, context: dict, request_id: str, behavior: str = "respond", **_):
        if behavior not in ("respond", "read", "blind"):
            return _invalid("消息行为应为 respond、read 或 blind。")
        if not isinstance(context, dict):
            return _invalid("家园现场应为 JSON 对象。")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            return _invalid("家园事件编号无效。")
        if behavior == "blind":
            text = "Yui的家本机消息通道检查。无需阅读或回复。"
        else:
            # This is a scene event, never an invented assistant utterance.
            scene = {
                k: context[k]
                for k in (
                    "stage",
                    "question",
                    "preferences",
                    "autonomy",
                    "style",
                    "building",
                    "progress",
                    "houseOrigin",
                    "blockCount",
                    "availableFeatures",
                    "constraints",
                    "auto",
                    "result",
                )
                if k in context
            }
            try:
                scene_text = json.dumps(scene, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                return _invalid("家园现场包含无效 JSON 数据。")
            if len(scene_text.encode("utf-8")) > 32768:
                return _invalid("家园现场描述过长。")
            text = "[Yui的家现场事件，来源是家园插件] " + scene_text + "\n"
            if behavior == "respond":
                text += (
                    "{MASTER_NAME} 想与正在聊天的 {LANLAN_NAME} 一起商量这座家。请在你当前对话中自然表达真正的想法和理由；你也可以暂不施工。若愿意提交建议，使用 yui_home_control 的 propose，将 request_id 设为 "
                    + request_id
                    + "，填写 speech、reason、feature、style、wants_to_build。auto 为 true 时你愿意建造的建议会在自主共建开启时执行；否则等待主人采纳。只选择现场可用方案。不要把工具提交回执当作已建好。无需再调用询问建议的插件入口。"
                )
        try:
            result = self.ctx.push_message(
                parts=[{"type": "text", "text": text}],
                visibility=[],
                ai_behavior=behavior,
                source="yui_home",
                target_lanlan="YUI",
                metadata={"home_request_id": request_id, "kind": "house_scene"},
                coalesce_key="yui_home:" + request_id,
            )
        except SdkError as exc:
            return Err(exc)
        # SDK acceptance is not proof of generation, speech, or playback.
        receipt = {
            "submitted": result.get("submitted") is True,
            "message_id": result.get("message_id"),
            "reason": result.get("reason"),
        }
        callback = await _call("native_receipt", request_id=request_id, receipt=receipt)
        if callback.is_err():
            return Err(
                SdkError(
                    "家园事件已取得提交回执，但家园窗口未确认收到回执。", code="home_receipt_failed", details=receipt
                )
            )
        return Ok({"ok": receipt["submitted"], "receipt": receipt, "conversation": "YUI", "generated_by_plugin": False})

    @plugin_entry(
        id="farm",
        name=tr("entry.farm.name", default="和Yui照料农场"),
        description=tr(
            "entry.farm.description", default="真实Minecraft养猪、种田和钓鱼；消耗游戏背包物品并确认实体、作物与渔获。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "enum": ["status", "setup", "plant", "fertilize", "harvest", "feed", "fish", "cancel"],
                }
            },
            "required": ["task"],
        },
    )
    async def farm(self, task: str, **_):
        if not isinstance(task, str) or task not in (
            "status",
            "setup",
            "plant",
            "fertilize",
            "harvest",
            "feed",
            "fish",
            "cancel",
        ):
            return _invalid("农场操作无效。")
        return await _call("farm", task=task, _actor="YUI")

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"name": "Yui的家", "status": "plugin stopped; desktop can remain open"})
