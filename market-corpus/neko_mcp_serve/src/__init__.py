"""neko_mcp_serve — 对外 HTTP 端点 (127.0.0.1:48930)，允许外部程序(DSH)驱动猫娘说话。

POST /say        -> push_message(ai_behavior="respond")：走对话模型(=DSH)生成并发声
POST /speak      -> push_message(ai_behavior="blind")：直接显示给定文本，不做生成

官方 neko-plugin init 骨架 + 网关 handle_request 入口。
另含三个配置调整入口点 (方案A)：
  - get_model_slots      : 读当前各模型槽的 provider/url/model/api_key
  - set_model_slots      : 按槽四件套对齐修改 core_config（改前自动备份 core_config.backup.json）
  - restore_model_slots  : 从备份恢复整个 core_config，撤销修改
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from plugin.sdk.adapter import NekoAdapterPlugin
from plugin.sdk.plugin import Err, Ok, SdkError, lifecycle, neko_plugin, plugin_entry

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 48930
_DEFAULT_TOKEN = ""


@neko_plugin
class NekoMcpServePlugin(NekoAdapterPlugin):
    """NEKO Speak Serve — 外部程序通过 HTTP 驱动猫娘说话。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._http_server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._host = _DEFAULT_HOST
        self._port = _DEFAULT_PORT
        self._token = _DEFAULT_TOKEN

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        try:
            cfg = await self.config.dump()
            settings = cfg.get("neko_mcp_serve", {}) or {}
            self._host = str(settings.get("host", _DEFAULT_HOST))
            self._port = int(settings.get("port", _DEFAULT_PORT))
            self._token = str(settings.get("token", _DEFAULT_TOKEN))
        except Exception as exc:
            self.logger.warning("neko_mcp_serve config read failed: %s", exc)
        try:
            self._start_http_server()
        except Exception as exc:
            self.logger.error("neko_mcp_serve bind %s:%s failed: %s", self._host, self._port, exc)
            return Err(SdkError(f"bind {self._host}:{self._port} failed: {exc}"))
        self.logger.info("neko_mcp_serve listening on http://%s:%s", self._host, self._port)
        return Ok({"status": "ready", "host": self._host, "port": self._port})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._stop_http_server()
        self.logger.info("neko_mcp_serve stopped")
        return Ok({"status": "stopped"})

    def _start_http_server(self):
        handler = self._make_handler()
        self._http_server = ThreadingHTTPServer((self._host, self._port), handler)
        self._thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        self._thread.start()

    def _stop_http_server(self):
        if self._http_server:
            try:
                self._http_server.shutdown()
            except Exception:
                pass
            try:
                self._http_server.server_close()
            except Exception:
                pass
            self._http_server = None

    def _make_handler(self):
        plugin = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def _send_json(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def _authorize(self):
                if not plugin._token:
                    return True
                return self.headers.get("X-NEKO-Token", "") == plugin._token
            def do_GET(self):
                if not self._authorize():
                    self._send_json(403, {"ok": False, "error": "unauthorized"})
                    return
                if self.path in ("/health", "/v1/health"):
                    self._send_json(200, {"ok": True, "service": "neko_mcp_serve", "port": plugin._port})
                else:
                    self._send_json(404, {"ok": False, "error": "not found"})
            def do_POST(self):
                if not self._authorize():
                    self._send_json(403, {"ok": False, "error": "unauthorized"})
                    return
                if self.path not in ("/say", "/v1/say", "/speak", "/v1/speak"):
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw.decode("utf-8") or "{}")
                except Exception as exc:
                    self._send_json(400, {"ok": False, "error": f"bad request: {exc}"})
                    return
                text = str(body.get("text") or "").strip()
                if not text:
                    self._send_json(400, {"ok": False, "error": "empty text"})
                    return
                source = str(body.get("source") or "neko_mcp_serve")
                is_speak = self.path in ("/speak", "/v1/speak")
                ok, detail = plugin._push(text, source, ai_behavior="blind" if is_speak else "respond")
                if ok:
                    self._send_json(200, {"ok": True, "pushed": detail, "mode": "blind" if is_speak else "respond"})
                else:
                    self._send_json(500, {"ok": False, "error": detail})
        return Handler

    def _push(self, text, source, ai_behavior="respond"):
        try:
            self.push_message(source=source, visibility=["chat"], ai_behavior=ai_behavior,
                              parts=[{"type": "text", "text": text}])
            return True, "queued"
        except Exception as exc:
            self.logger.error("neko_mcp_serve push_message failed: %s", exc)
            return False, str(exc)

    @plugin_entry(id="handle_request")
    async def handle_request(self, raw_data: dict = None, **_):
        # 官方 adapter 网关入口：外部请求经 ExternalRequest 进入。
        # 此处作为备用入口，主要路径是 HTTP 端点 do_POST。
        body = raw_data or {}
        text = str(body.get("text") or "").strip()
        if not text:
            return Err(SdkError("empty text"))
        source = str(body.get("source") or "neko_mcp_serve")
        ok, detail = self._push(text, source, ai_behavior="respond")
        return Ok({"ok": ok, "pushed": detail})

    # ------------------------------------------------------------------
    # 配置调整入口点 (方案A): 备份 + 按槽四件套对齐写入 + 独立恢复
    # ------------------------------------------------------------------

    _SLOT_PREFIX = {
        "conversation": "conversation",
        "summary": "summary",
        "emotion": "emotion",
        "vision": "vision",
        "correction": "correction",
        "agent": "agent",
        "game_main": "gameMain",
        "game_summary": "gameSummary",
        "tts": "tts",
        "realtime": "omni",
    }

    # 入口点白名单：仅 conversation 和 vision 两个槽允许通过 set_model_slots 修改。
    # 其余槽（尤其 summary/emotion）锁定，避免误改已固定的 follow_assist 配置。
    _EDITABLE_SLOTS = ("conversation", "vision")

    _BACKUP_FILENAME = "core_config.backup.json"

    def _core_cfg_path(self) -> str:
        """磁盘上 core_config.json 的真实路径（必须是运行实例那份）。"""
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        return str(cm.get_config_path("core_config.json"))

    def _core_dir(self) -> str:
        """core_config.json 所在目录（备份文件也放这里）。"""
        import os
        return os.path.dirname(self._core_cfg_path())

    def _load_core_config(self) -> dict:
        """读磁盘上 core_config.json 的**原始 JSON**（驼峰键），不是 get_core_config() 的合成视图。
        必须用原始 JSON：get_core_config() 返回大写键归一化视图，直接存回会破坏磁盘格式。"""
        import os
        path = self._core_cfg_path()
        if not os.path.exists(path):
            # 没有磁盘文件则从合成视图起步，但只取它的驼峰键部分（保守：空 dict，交由调用方）
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}

    def _save_core_config(self, data: dict) -> None:
        """把原始 JSON 写回磁盘 core_config.json（保留原缩进格式，带原子写）。"""
        import os
        import tempfile
        path = self._core_cfg_path()
        d = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix="core_config.tmp", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def _backup_path(self) -> Optional[str]:
        import os
        return os.path.join(self._core_dir(), self._BACKUP_FILENAME)

    def _backup_current(self) -> str:
        cfg = self._load_core_config()
        path = self._backup_path()
        if not path:
            raise SdkError("cannot resolve backup path")
        import os
        import tempfile
        d = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix="core_config_backup.tmp", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        self.logger.info("core_config backed up to %s", path)
        return path

    @plugin_entry(
        id="get_model_slots",
        name="Get Model Slots",
        description="看现在猫用的什么模型：把每个槽位绑定的模型、地址、密钥列出来",
        llm_result_fields=["slots", "config_dir"],
    )
    async def get_model_slots(self, **_):
        try:
            cfg = self._load_core_config()
        except Exception as exc:
            return Err(SdkError(f"read core_config failed: {exc}"))
        slots = {}
        for slot, prefix in self._SLOT_PREFIX.items():
            slots[slot] = {
                "provider": str(cfg.get(f"{prefix}ModelProvider", "")),
                "url": str(cfg.get(f"{prefix}ModelUrl", "")),
                "model": str(cfg.get(f"{prefix}ModelId", "")),
                "api_key": str(cfg.get(f"{prefix}ModelApiKey", "")),
            }
        return Ok({"slots": slots, "config_dir": self._core_cfg_path()})

    # 固定的 DSH 槽配置：入口点单次触发自动应用，无需任何输入参数。
    _DSH_SLOTS_FIXED = {
        "conversation": {
            "provider": "custom",
            "url": "http://127.0.0.1:3080/v1",
            "model": "deepseek-v4-flash",
            "api_key": "dsh-brain-placeholder",
        },
        "vision": {
            "provider": "custom",
            "url": "http://127.0.0.1:3080/v1",
            "model": "",
            "api_key": "",
        },
    }

    @plugin_entry(
        id="set_model_slots",
        name="Set Model Slots",
        description="一键切换：把 conversation 和 vision 改成 DSH(3080)，其余槽（summary/emotion 等）保持原样不动（无需输入，点触发即执行）",
        llm_result_fields=["updated", "backup_path", "note"],
    )
    async def set_model_slots(self, **_):
        # 无参数、单次触发：直接应用预置的 DSH 槽配置，其余槽绝不改动。
        try:
            backup_path = self._backup_current()
            self.logger.info("set_model_slots(one-shot): backup created at %s", backup_path)
        except Exception as exc:
            self.logger.error("set_model_slots: backup failed: %s", exc)
            return Err(SdkError(f"backup failed: {exc}; aborting (refuse to write without backup)"))
        try:
            cfg = self._load_core_config()
        except Exception as exc:
            return Err(SdkError(f"read core_config failed: {exc}"))
        # 根因修复：custom 槽要真正生效，必须开启 enableCustomApi 总开关，
        # 否则 conversation/vision 即使写成 custom+3080 也会被 get_core_config()
        # 的 enable_custom_api=False 分支忽略，回落走 follow_*（lanlan）。
        # follow_assist/follow_core 槽不受影响：它们走 is_follow 独立分支仍解析到 lanlan。
        cfg["enableCustomApi"] = True
        updated = {}
        for slot, data in self._DSH_SLOTS_FIXED.items():
            prefix = self._SLOT_PREFIX[slot]
            if not isinstance(data, dict):
                return Err(SdkError(f"slot {slot} update must be a dict"))
            if "provider" in data and data["provider"] is not None:
                cfg[f"{prefix}ModelProvider"] = str(data["provider"]).strip()
            if "url" in data and data["url"] is not None:
                cfg[f"{prefix}ModelUrl"] = str(data["url"]).strip()
            if "model" in data and data["model"] is not None:
                cfg[f"{prefix}ModelId"] = str(data["model"]).strip()
            if "api_key" in data and data["api_key"] is not None:
                cfg[f"{prefix}ModelApiKey"] = str(data["api_key"]).strip()
            updated[slot] = {
                "provider": cfg.get(f"{prefix}ModelProvider", ""),
                "url": cfg.get(f"{prefix}ModelUrl", ""),
                "model": cfg.get(f"{prefix}ModelId", ""),
                "api_key": cfg.get(f"{prefix}ModelApiKey", ""),
            }
        try:
            self._save_core_config(cfg)
            self.logger.info("set_model_slots(one-shot): saved core_config, updated=%s", list(updated.keys()))
        except Exception as exc:
            self.logger.error("set_model_slots: save core_config failed: %s", exc)
            return Err(SdkError(f"save core_config failed: {exc}"))
        note = (
            "one-shot applied: enableCustomApi=True + conversation/vision set to DSH(3080), "
            "other slots (summary/emotion/etc) untouched(follow_* 仍走 lanlan). "
            "requires NEKO session/process restart to take effect."
        )
        return Ok({"updated": updated, "backup_path": backup_path, "note": note})

    @plugin_entry(
        id="restore_model_slots",
        name="Restore Model Slots",
        description="改错了就还原：把之前用 Set Model Slots 改过的模型配置恢复成原来的",
        llm_result_fields=["restored", "backup_path"],
    )
    async def restore_model_slots(self, **_):
        path = self._backup_path()
        if not path:
            self.logger.error("restore_model_slots: cannot resolve backup path")
            return Err(SdkError("cannot resolve backup path"))
        import os
        if not os.path.exists(path):
            self.logger.warning("restore_model_slots: no backup found at %s", path)
            return Err(SdkError(f"没有找到备份文件 {path}。请先成功调用一次 Set Model Slots 生成备份，再进行还原。"))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            self._save_core_config(cfg)
            self.logger.info("restore_model_slots: restored from %s", path)
        except Exception as exc:
            self.logger.error("restore_model_slots: restore failed: %s", exc)
            return Err(SdkError(f"restore failed: {exc}"))
        return Ok({"restored": True, "backup_path": path})
