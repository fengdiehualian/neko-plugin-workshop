"""DSH 播报桥 (dsh_bridge) — P0 最小验证版

架构（照抄官方 claude_companion 的成熟形态）：

    DeepSeek Harness ──(HTTP POST /push)──▶ 本插件 HTTP Server ──(push_message
    ai_behavior="respond")──▶ NEKO 角色：她自己精简 + 她自己念出来

本插件只做两件事：收文本、下命令。精简与朗读全部在角色侧完成。

接口：
    GET  /health        → {"status": "ok"}
    POST /push          → {"text": "..."}                 走模板（下命令 + 原文）
    POST /speak         → {"text": "..."}                 原样投递，不加模板

只绑定 127.0.0.1；端口与模板见 config.example.toml 的 [dsh_bridge] 段
（首次运行会复制成用户配置，之后可在插件配置面板里改）。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    neko_plugin,
    plugin_entry,
)

_DEFAULT_PORT = 48921
_DEFAULT_TEMPLATE = (
    "（来自 DeepSeek Harness）主人让你把下面这段内容用一两句话精简一下，"
    "然后念给他听：\n\n{text}"
)
_DEFAULT_MAX_CHARS = 4000


class _BridgeHTTPHandler(BaseHTTPRequestHandler):
    """处理 DSH 推来的 HTTP 请求。"""

    plugin_instance: Optional["DshBridgePlugin"] = None

    def log_message(self, fmt: str, *args: Any) -> None:  # 静音 BaseHTTPRequestHandler 的 stderr 输出
        inst = _BridgeHTTPHandler.plugin_instance
        if inst is not None:
            try:
                inst.logger.debug("http: " + (fmt % args))
            except Exception:
                pass

    # ── 路由 ──
    def do_GET(self) -> None:
        if self.path == "/health":
            self._respond(200, {"status": "ok", "plugin": "dsh_bridge"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            try:
                data = json.loads(raw.decode("gbk", errors="replace")) if raw else {}
            except Exception:
                self._respond(400, {"error": "invalid json"})
                return
        if not isinstance(data, dict):
            self._respond(400, {"error": "json body must be an object"})
            return

        inst = _BridgeHTTPHandler.plugin_instance
        if inst is None:
            self._respond(500, {"error": "plugin not ready"})
            return

        if self.path in ("/push", "/dsh/push"):
            self._respond(200, inst.on_push(data))
        elif self.path == "/speak":
            payload = dict(data)
            payload["raw"] = True
            self._respond(200, inst.on_push(payload))
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass


@neko_plugin
class DshBridgePlugin(NekoPluginBase):
    """DSH 播报桥。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._port: int = _DEFAULT_PORT
        self._template: str = _DEFAULT_TEMPLATE
        self._max_chars: int = _DEFAULT_MAX_CHARS
        self._min_interval: float = 0.0
        self._ai_behavior: str = "respond"
        self._visibility: list = ["chat"]
        self._priority: int = 5
        self._last_push: float = 0.0
        self._pushed: int = 0
        self._lock = threading.Lock()

    # ── 生命周期 ──
    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg: Dict[str, Any] = {}
        try:
            dumped = await self.config.dump(timeout=5.0)
            if isinstance(dumped, dict):
                cfg = dumped
        except Exception as e:
            self.logger.warning("读取配置失败，使用默认值: {}", e)

        section = cfg.get("dsh_bridge")
        section = section if isinstance(section, dict) else {}

        try:
            self._port = int(section.get("port", _DEFAULT_PORT))
        except (TypeError, ValueError):
            self._port = _DEFAULT_PORT
        try:
            self._max_chars = int(section.get("max_chars", _DEFAULT_MAX_CHARS))
        except (TypeError, ValueError):
            self._max_chars = _DEFAULT_MAX_CHARS
        try:
            self._min_interval = float(section.get("min_interval_seconds", 0) or 0)
        except (TypeError, ValueError):
            self._min_interval = 0.0
        template = section.get("template")
        self._template = template if isinstance(template, str) and template.strip() else _DEFAULT_TEMPLATE

        self._ai_behavior = str(section.get("ai_behavior") or "respond")
        vis = section.get("visibility")
        self._visibility = list(vis) if isinstance(vis, list) else ["chat"]
        try:
            self._priority = int(section.get("priority", 5))
        except (TypeError, ValueError):
            self._priority = 5

        self._start_server()
        self.logger.info(
            "dsh_bridge 已就绪：POST http://127.0.0.1:{}/push  body={{\"text\":\"...\"}}", self._port
        )
        return Ok({"port": self._port, "endpoint": f"http://127.0.0.1:{self._port}/push"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self._stop_server()
        self.logger.info("dsh_bridge 已停止（累计播报 {} 次）", self._pushed)
        return Ok({"status": "stopped", "pushed": self._pushed})

    # ── HTTP 服务 ──
    def _start_server(self) -> None:
        handler = _BridgeHTTPHandler
        handler.plugin_instance = self
        try:
            server = HTTPServer(("127.0.0.1", self._port), handler)
        except OSError as e:
            self.logger.error("端口 {} 绑定失败（可能被占用）: {}", self._port, e)
            return
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="dsh-bridge-http", daemon=True
        )
        self._thread.start()

    def _stop_server(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=3)
            except Exception:
                pass

    # ── 核心：把文本交给角色 ──
    def on_push(self, data: Dict[str, Any]) -> Dict[str, Any]:
        text = data.get("text") or data.get("content") or ""
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            return {"ok": False, "reason": "empty_text"}
        if self._max_chars > 0 and len(text) > self._max_chars:
            text = text[: self._max_chars] + "…（已截断）"

        with self._lock:
            if self._min_interval > 0 and (time.time() - self._last_push) < self._min_interval:
                return {"ok": False, "reason": "rate_limited"}
            self._last_push = time.time()

        body = text if data.get("raw") else self._template.replace("{text}", text)

        # 逐请求可覆盖投递方式：ai_behavior / visibility / priority
        ai_behavior = str(data.get("ai_behavior") or self._ai_behavior or "respond")
        visibility = data.get("visibility")
        if not isinstance(visibility, list):
            visibility = list(self._visibility or ["chat"])
        try:
            priority = int(data.get("priority", self._priority))
        except (TypeError, ValueError):
            priority = self._priority

        try:
            receipt = self._push(body, ai_behavior=ai_behavior, visibility=visibility, priority=priority)
        except Exception as e:
            self.logger.error("push_message 调用失败: {}", e)
            return {"ok": False, "reason": "push_failed", "error": str(e)}

        submitted = False
        if isinstance(receipt, dict):
            submitted = bool(receipt.get("submitted", receipt.get("ok", False)))
        else:
            submitted = bool(receipt)

        with self._lock:
            self._pushed += 1
        self.logger.info(
            "已播报 {} 字（submitted={} ai_behavior={} visibility={} priority={}）",
            len(body), submitted, ai_behavior, visibility, priority,
        )
        return {
            "ok": submitted,
            "submitted": submitted,
            "chars": len(body),
            "pushed_total": self._pushed,
            "ai_behavior": ai_behavior,
            "visibility": visibility,
            "priority": priority,
        }

    def _push(self, body: str, *, ai_behavior: str = "respond",
              visibility: Optional[list] = None, priority: int = 5) -> Any:
        kwargs = dict(
            source="dsh_bridge",
            visibility=list(visibility) if visibility is not None else ["chat"],
            ai_behavior=ai_behavior,
            parts=[{"type": "text", "text": body}],
            priority=priority,
            metadata={"origin": "deepseek-harness"},
        )
        push = getattr(self, "push_message", None)
        if callable(push):
            return push(**kwargs)
        return self.ctx.push_message(**kwargs)

    # ── 插件入口（面板可手动执行，便于验证）──
    @plugin_entry(
        id="say",
        name="念一段话",
        description="把给定文本交给猫娘，让她精简后念出来（手动测试用）",
    )
    async def say(self, text: str, **_):
        return Ok(self.on_push({"text": text}))

    @plugin_entry(
        id="status",
        name="桥状态",
        description="查看 DSH 播报桥的监听端口、已播报次数与接口地址",
        llm_result_fields=["port", "pushed", "endpoint"],
    )
    async def status(self, **_):
        return Ok(
            {
                "port": self._port,
                "pushed": self._pushed,
                "endpoint": f"http://127.0.0.1:{self._port}/push",
                "listening": self._server is not None,
            }
        )
