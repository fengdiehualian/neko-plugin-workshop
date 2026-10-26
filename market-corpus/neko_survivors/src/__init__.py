"""猫娘姐妹：幸存者（N.E.K.O 插件）

自带完整游戏的弹幕生存肉鸽。游戏本体是纯静态 HTML/JS，全部打包在插件的
``static/game/`` 下，由插件面板（/plugin/neko_survivors/ui/）直接伺服。

官方猫娘通道（第一百二十四/二十五轮，官方开发者确认的正确姿势）：

    游戏页面（与插件服务同源）
      → POST /plugin/neko_survivors/hosted-ui/action/<action>
        （插件管理器自带的 action 通道，同源 fetch 无跨域问题）
      → 本插件 @plugin_entry 动作
      → 官方宿主（127.0.0.1:48911）

两个动作：

* ``game_event`` —— 战况播报。``push_message(ai_behavior="respond")``，
  官方猫娘在聊天界面自由发挥一句（自带官方 Live2D 形象 + TTS）。
* ``speak`` —— **照念台词**。转发宿主 ``POST /api/game/<type>/speak``
  （B 层输出通道，和内置小游戏的台词输出同一条管线），官方 TTS 直接
  把指定台词说出来 —— 这就是"小小猫娘对话框技能/天气台词有声音"。

Live2D 形象（第一百二十五轮）：插件面板源（48916）上没有 NEKO 的
引擎脚本和模型文件，前端直读又被沙箱跨域拦死。本插件在 startup 时
从本机宿主（48911）把 **当前猫娘的模型全套 + Live2D 引擎脚本** 同步
进安装目录 ``static/vendor/``（插件包本身不含任何 NEKO 资源，许可干净；
每个用户同步到的是他自己 NEKO 的当前角色）。同步完成后，游戏 iframe
同源加载引擎与模型，右下角 Live2D 就和本地版一样挂起来。宿主没开时
同步静默失败，游戏照常（形象区维持隐藏）。

Python 壳同时是打包工具需要的入口类。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:  # 官方打包/运行环境：真实 SDK
    from plugin.sdk.plugin import (
        Err,
        NekoPluginBase,
        Ok,
        SdkError,
        lifecycle,
        neko_plugin,
        plugin_entry,
        tr,
        ui,
    )
except ModuleNotFoundError:  # 本地跑测试（无 NEKO SDK）时的等价兜底
    def neko_plugin(cls):  # type: ignore[misc]
        return cls

    def _pass_deco(**_kwargs):  # type: ignore[misc]
        def deco(fn):
            return fn
        return deco

    plugin_entry = _pass_deco
    lifecycle = _pass_deco

    class _UiStub:  # type: ignore[no-redef]
        action = staticmethod(_pass_deco)
        context = staticmethod(_pass_deco)

    ui = _UiStub()  # type: ignore[assignment]

    def tr(key, default=None, **_kwargs):  # type: ignore[misc]
        return default

    class Err:  # type: ignore[no-redef]
        def __init__(self, error=None):
            self.error = error

    class Ok:  # type: ignore[no-redef]
        def __init__(self, value=None):
            self.value = value

    class SdkError(Exception):  # type: ignore[no-redef]
        pass

    class NekoPluginBase:  # type: ignore[no-redef]
        def __init__(self, ctx=None):
            self.ctx = ctx


# ── 宿主（主服务器）常量 ──────────────────────────────────────

HOST_BASE = "http://127.0.0.1:48911"
GAME_TYPE = "neko_survivors"

# Live2D 引擎脚本（顺序不能变，与 NEKO 官方一致；basename 落盘到 vendor/engine/）
ENGINE_SCRIPTS = [
    "/static/libs/live2dcubismcore.min.js",
    "/static/libs/live2d.min.js",
    "/static/libs/pixi.min.js",
    "/static/libs/index.min.js",
    "/static/live2d/live2d-core.js",
    "/static/live2d/live2d-emotion.js",
    "/static/live2d/live2d-model.js",
]

# 每类战况事件的最小播报间隔（秒）—— ai_behavior="respond" 会真的让
# 模型生成一轮，必须有节流，不然波次事件会烧 token + 刷屏。
_MIN_GAPS: dict[str, float] = {
    "run_start": 10.0,
    "wave_start": 45.0,
    "level_up": 30.0,
    "low_hp": 60.0,
    "boss_spawn": 30.0,
    "boss_phase": 30.0,
    "boss_kill": 15.0,
    "weapon_combo": 45.0,
    "death": 10.0,
    "win": 10.0,
}
_GLOBAL_GAP = 12.0    # 战况播报（respond，走 LLM）的全局最小间隔
# 照念台词（speak）**不设 CD**：官方 TTS 管线不走 LLM、完全免费，排队与
# 打断由宿主管线自己处理（用户：限频会整句吞掉隐藏 Boss 战的连发提示）。

_UNSAFE = re.compile(r"[^A-Za-z0-9._\-/]")


def _safe_rel(rel: str) -> str:
    """模型引用的相对路径 → 允许落盘的相对路径（防穿越）。"""
    rel = str(rel or "").replace("\\", "/")
    rel = _UNSAFE.sub("_", rel)
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or not parts:
        raise ValueError(f"unsafe model ref: {rel!r}")
    return "/".join(parts)


@neko_plugin
class SurvivorsPlugin(NekoPluginBase):
    """猫娘姐妹：幸存者。"""

    def __init__(self, ctx=None):
        super().__init__(ctx)
        self._last_event_at: dict[str, float] = {}
        self._last_any_at = 0.0
        self._last_speak_at = 0.0
        self._lanlan_cache: tuple[float, str] = (0.0, "")
        self._sync_task: Any = None

    # ── 生命周期 ─────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        # 后台同步 Live2D 引擎 + 当前猫娘模型（失败静默，绝不阻塞启动）
        self._sync_task = asyncio.create_task(self._sync_avatar_quiet())
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
        return Ok({"status": "stopped"})

    # ── 动作一：战况播报（猫娘自由发挥） ─────────────────────

    # @ui.action 把这个入口挂进面板 action 白名单 —— 没有它，
    # hosted-ui/action/<id> 会 403 "not exposed by surface"。
    @ui.action(id="game_event", label=tr("entries.game_event.name", default="游戏战况播报"), icon="gamepad")
    @plugin_entry(
        id="game_event",
        name=tr("entries.game_event.name", default="游戏战况播报"),
        description=tr(
            "entries.game_event.description",
            default="接收游戏内事件（开波/Boss/结算等），让官方猫娘在聊天里播报吐槽。"
                    "由游戏页面自动调用，内置节流。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "event": {"type": "string", "description": "事件类型（run_start/wave_start/boss_spawn/boss_kill/win/death 等）"},
                "text": {"type": "string", "description": "战况一句话描述（第几波/难度/血量/击杀等）"},
            },
            "required": ["event", "text"],
            "additionalProperties": False,
        },
    )
    async def game_event(self, event: str = "", text: str = "", **_) -> Ok:
        """把游戏战况交给官方猫娘播报（节流后 push_message）。"""
        log = getattr(self, "logger", None)
        now = time.monotonic()
        gap = _MIN_GAPS.get(event, 30.0)
        if now - self._last_event_at.get(event, 0.0) < gap:
            if log:
                log.info("[neko_survivors] game_event %s 被节流（同类间隔）", event)
            return Ok({"status": "throttled", "reason": "event_gap"})
        if now - self._last_any_at < _GLOBAL_GAP:
            if log:
                log.info("[neko_survivors] game_event %s 被节流（全局间隔）", event)
            return Ok({"status": "throttled", "reason": "global_gap"})
        self._last_event_at[event] = now
        self._last_any_at = now

        safe_text = str(text or "").strip()[:300]
        if not safe_text:
            return Err(SdkError("empty text"))
        if log:
            log.info("[neko_survivors] game_event %s → push_message（%s）", event, safe_text[:60])
        self.push_message(
            source="neko_survivors",
            visibility=["chat"],
            ai_behavior="respond",
            parts=[{
                "type": "text",
                "text": (
                    "（主人正在玩你的游戏《猫娘姐妹：幸存者》，一波弹幕生存。"
                    f"现在的战况：{safe_text}。用你自己的口吻说一句话播报或吐槽，"
                    "一句话就好，别复述括号里的格式。）"
                ),
            }],
            metadata={"context_type": "neko_survivors_event", "event": str(event)[:40]},
        )
        return Ok({"status": "queued"})

    # ── 动作二：照念台词（官方 TTS，"本地版的感觉"） ─────────

    @ui.action(id="speak", label=tr("entries.speak.name", default="让猫娘说这句话"), icon="chat")
    @plugin_entry(
        id="speak",
        name=tr("entries.speak.name", default="让猫娘说这句话"),
        description=tr(
            "entries.speak.description",
            default="把指定台词通过宿主官方 TTS 管线说出来（与内置小游戏同一条 B 层输出通道）。"
                    "由游戏页面自动调用（宠物业/天气台词），内置 2.5 秒节流。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要让猫娘照念的台词"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    async def speak(self, text: str = "", **_):
        """照念台词：转发宿主 /api/game/<type>/speak（官方 TTS 管线，无 CD）。"""
        log = getattr(self, "logger", None)
        safe = str(text or "").strip()[:300]
        if not safe:
            return Err(SdkError("empty text"))

        lanlan = await self._current_lanlan()
        if not lanlan:
            if log:
                log.info("[neko_survivors] speak 跳过：读不到当前猫娘（宿主没开？）")
            return Ok({"status": "no_lanlan"})
        payload = json.dumps(
            {"lanlan_name": lanlan, "session_id": f"{GAME_TYPE}_panel", "line": safe},
            ensure_ascii=False,
        ).encode("utf-8")
        if log:
            log.info("[neko_survivors] speak → 宿主 TTS（%s）：%.60s", lanlan, safe)
        status, body = await asyncio.to_thread(
            self._http_request, "POST", f"/api/game/{GAME_TYPE}/speak", payload,
        )
        if status != 200:
            if log:
                log.info("[neko_survivors] speak 宿主不可达（HTTP %s）", status)
            return Ok({"status": "upstream_unavailable", "code": status})
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            data = {}
        if data.get("ok") is False:
            if log:
                log.info("[neko_survivors] speak 被宿主跳过：%s", data.get("reason"))
            return Ok({"status": "skipped", "reason": str(data.get("reason") or "")[:80]})
        return Ok({"status": "spoken", "method": str(data.get("method") or "")[:40]})

    # ── 动作三：手动重同步形象资源（诊断用） ─────────────────

    @ui.action(id="sync_avatar", label=tr("entries.sync_avatar.name", default="同步猫娘形象"), icon="refresh")
    @plugin_entry(
        id="sync_avatar",
        name=tr("entries.sync_avatar.name", default="同步猫娘形象"),
        description=tr(
            "entries.sync_avatar.description",
            default="从本机宿主重新同步 Live2D 引擎与当前猫娘模型到插件 vendor 目录。"
                    "插件启动时会自动做一次；宿主中途才打开时可用这个手动补。",
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    async def sync_avatar(self, **_):
        return Ok(await self._sync_avatar_payload())

    # ── 内部：宿主 HTTP 与形象同步 ───────────────────────────

    def _vendor_dir(self) -> Path:
        return Path(__file__).resolve().parent / "static" / "vendor"

    def _http_request(self, method: str, path: str, body: bytes | None = None,
                      timeout: float = 15.0) -> tuple[int, bytes]:
        req = urllib.request.Request(
            HOST_BASE + path, data=body, method=method,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except Exception:
            return 0, b""

    def _http_json(self, path: str, timeout: float = 15.0) -> dict:
        status, body = self._http_request("GET", path, timeout=timeout)
        if status != 200:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def _current_lanlan(self) -> str:
        now = time.monotonic()
        ts, name = self._lanlan_cache
        if name and now - ts < 120.0:
            return name
        chars = await asyncio.to_thread(self._http_json, "/api/characters")
        name = str(chars.get("当前猫娘") or "").strip()
        if name:
            self._lanlan_cache = (now, name)
        return name

    async def _sync_avatar_quiet(self) -> None:
        try:
            await self._sync_avatar_payload()
        except Exception:
            pass  # 任何失败都静默：形象是可选增强

    def _model_refs(self, model_json: dict) -> list[str]:
        """从 model3.json 收集 FileReferences 引用的相对路径。"""
        refs: list[str] = []
        fr = model_json.get("FileReferences") or {}
        for key in ("Moc", "Physics", "Pose", "DisplayInfo"):
            if isinstance(fr.get(key), str) and fr[key]:
                refs.append(fr[key])
        textures = fr.get("Textures")
        if isinstance(textures, list):
            refs.extend(t for t in textures if isinstance(t, str) and t)
        expressions = fr.get("Expressions")
        if isinstance(expressions, list):
            for item in expressions:
                if isinstance(item, dict) and isinstance(item.get("File"), str):
                    refs.append(item["File"])
        motions = fr.get("Motions")
        if isinstance(motions, dict):
            for group in motions.values():
                if isinstance(group, list):
                    for m in group:
                        if isinstance(m, dict):
                            for mk in ("File", "Sound"):
                                if isinstance(m.get(mk), str) and m[mk]:
                                    refs.append(m[mk])
        return refs

    async def _sync_avatar_payload(self) -> dict:
        """从宿主同步 Live2D 引擎 + 当前猫娘模型到 static/vendor/。"""
        total = 0
        # 0) 宿主可达性
        status, _ = await asyncio.to_thread(self._http_request, "GET", "/api/characters")
        if status != 200:
            return {"synced": False, "reason": "host_unreachable"}

        # 1) 当前猫娘的模型路径（game API 直接给 live2d_path）
        info = await asyncio.to_thread(
            self._http_json, f"/api/game/{GAME_TYPE}/character",
        )
        model_path = str(info.get("live2d_path") or "").strip()
        if info.get("model_type") != "live2d" or not model_path:
            return {"synced": False, "reason": "no_live2d_model"}

        # 2) 下载 model3.json 并解析引用
        status, blob = await asyncio.to_thread(
            self._http_request, "GET", model_path, timeout=30.0,
        )
        if status != 200 or not blob:
            return {"synced": False, "reason": "model_fetch_failed"}
        try:
            model_json = json.loads(blob.decode("utf-8"))
        except Exception:
            return {"synced": False, "reason": "model_json_invalid"}

        vendor = self._vendor_dir()
        model_dir = vendor / "model"
        model_dir.mkdir(parents=True, exist_ok=True)

        rel_model3 = _safe_rel(model_path.lstrip("/").split("/static/", 1)[-1])
        target = model_dir / rel_model3
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()
                == hashlib.sha256(blob).hexdigest()):
            target.write_bytes(blob)
            total += len(blob)

        # 3) 下载全部引用文件（幂等：字节相同就跳过）
        base_rel = rel_model3.rsplit("/", 1)[0] if "/" in rel_model3 else ""
        for ref in self._model_refs(model_json):
            try:
                safe_ref = _safe_rel(ref)
            except ValueError:
                continue
            ref_status, ref_blob = await asyncio.to_thread(
                self._http_request, "GET", f"{model_path.rsplit('/', 1)[0]}/{ref}",
                timeout=60.0,
            )
            if ref_status != 200 or not ref_blob:
                continue
            dest = model_dir / (base_rel + "/" + safe_ref if base_rel else safe_ref)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.read_bytes() == ref_blob:
                continue
            dest.write_bytes(ref_blob)
            total += len(ref_blob)
            if total > 200 * 1024 * 1024:
                return {"synced": False, "reason": "too_large"}

        # 4) Live2D 引擎脚本
        engine_dir = vendor / "engine"
        engine_dir.mkdir(parents=True, exist_ok=True)
        for script in ENGINE_SCRIPTS:
            s_status, s_blob = await asyncio.to_thread(
                self._http_request, "GET", script, timeout=30.0,
            )
            if s_status != 200 or not s_blob:
                continue
            dest = engine_dir / script.rsplit("/", 1)[-1]
            if dest.exists() and dest.read_bytes() == s_blob:
                continue
            dest.write_bytes(s_blob)
            total += len(s_blob)

        # 5) current.json —— 前端从这里解析"当前猫娘"
        entry = "/plugin/neko_survivors/ui/vendor/model/" + rel_model3
        current = {"name": str(info.get("lanlan_name") or ""), "file": entry}
        (vendor / "model" / "current.json").write_text(
            json.dumps(current, ensure_ascii=False), encoding="utf-8",
        )
        return {"synced": True, "bytes": total, "model": rel_model3}
