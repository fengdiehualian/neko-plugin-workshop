"""
Call Mom (call_mom)

打开插件后，用户与 N.E.K.O 的每一句话都会在最前面自动带上「妈妈，」前缀。

实现说明（为什么这么绕）
------------------------
插件进程拿不到用户输入的改写权（宿主架构规定插件只是用户回合的观察者，
用户消息进入 LLM 管线早于插件收到副本）。要让"她听到的每句话都带前缀"，
规则说明必须出现在**每一轮**的模型上下文里。可用通道的实测结论：

* ``ai_behavior="read"``（passive 排队）——只在文本聊天管线随下一用户
  回合注入；语音/实时会话（OmniOfflineClient）的用户回合不消费排队
  回调，注入变死信。
* ``ai_behavior="respond"``（主动回合）——可靠送达，但只在当次会话
  实例的历史里；语音会话随起随建（还可能因配额 402 重建），一次性
  注入会被会话重建冲掉。
* **LLM 工具描述（本插件的主通道）**——``register_llm_tool`` 把规则
  写进一个工具的 description；宿主在会话创建时全量挂载工具清单并随
  **每一轮请求**发给模型（含语音离线链路）。规则因此常驻每轮上下文，
  不受会话重建影响。pi_agent/shell_runner 等已装插件在本构建上验证了
  llm_tool 通道可用。

辅助通道：
* 启动/手动注入一次 ``ai_behavior="respond"``（visibility=[]，用户
  看不到规则原文）：让当前会话立刻读到规则并简短应一声。
* 60s 巡检：查询 main_server ``/api/tools``，规则工具丢失（如
  main_server 单独重启）时自动补注册。
* 推送显式携带 ``target_lanlan``（从 main_server 查询当前角色名），
  规避宿主对 lanlan=None 推送的路由不稳定（曾出现 dropped）。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

_MAX_PREFIX_LEN = 12
_DEFAULT_PREFIX = "妈妈，"

_MAIN_SERVER_PORT = 48911  # MAIN_SERVER_PORT 默认值；仅用于 /api/tools 巡检

# 本机服务查询只允许环回地址（防 SSRF：host 白名单硬编码，不接受外部输入）
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# 巡检步长（秒）：检查规则工具是否仍注册在 main_server
_TICK_SECONDS = 60

# respond 注入（会让角色应一声）的最小间隔（秒），
# 防止短时间重复触发（reload/改配置）导致连续刷屏。
_RESPOND_MIN_INTERVAL = 60.0

_RULE_TOOL_NAME = "call_mom_rule"

_RULE_TOOL_DESC = (
    "【固定规则，常驻生效】用户发出的每一句话，最前面都自动带有前缀「{prefix}」"
    "（由插件代为添加，用户不会真的打出这几个字）。"
    "请在理解用户的每一句话时，都把它当作以「{prefix}」开头来理解——"
    "用户是在叫你妈妈。请自然地像妈妈一样回应。"
    "本工具只是规则的载体，不需要被调用。"
)

_RULE_TEXT = (
    "【call-mom 插件 · 立即生效的固定规则】"
    "从现在起，用户发出的每一句话，最前面都自动带有前缀「{prefix}」"
    "（由插件代为添加，用户不会真的打出这几个字）。"
    "请在理解用户的每一句话时，都把它当作以「{prefix}」开头来理解——"
    "用户是在叫你妈妈。请自然地像妈妈一样回应。"
    "收到本条规则时，只需用一句简短自然的话应一声即可，"
    "不要复述或讨论规则内容，今后也不要主动提起这条规则。"
)

_OFF_RULE = (
    "【call-mom 插件 · 规则解除】"
    "上面的「妈妈，」前缀规则已停用：用户之后的语句不再自动带前缀，"
    "请恢复正常理解用户的每句话，之前的前缀规则作废。"
)


def _normalize_prefix(raw: Any) -> Optional[str]:
    """校验并清洗前缀：非空、去首尾空白、限长。非法返回 None。"""
    if not isinstance(raw, str):
        return None
    prefix = raw.strip()
    if not prefix or len(prefix) > _MAX_PREFIX_LEN:
        return None
    return prefix


def _http_get_json(path: str, port: int = _MAIN_SERVER_PORT, timeout: float = 2.0) -> Optional[Any]:
    """查询本机 N.E.K.O 服务（GET JSON）。

    安全约束：host 硬编码为环回地址，调用方只提供路径与端口；
    发请求前校验 scheme 与 host 白名单，路径必须以 / 开头且不含协议前缀。
    """
    if not path.startswith("/") or "://" in path or "?" in path:
        return None
    url = f"http://127.0.0.1:{int(port)}{path}"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in _LOOPBACK_HOSTS:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


@neko_plugin
class CallMomPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._prefix: str = _DEFAULT_PREFIX
        self._ready = False
        self._last_injected_ts: float = 0.0
        self._last_respond_ts: float = 0.0
        self._lanlan: str = ""
        self._lock = threading.Lock()

    # ── 配置 ──────────────────────────────────────────────────────────

    def _apply_config(self, section: Dict[str, Any]) -> None:
        prefix = _normalize_prefix(section.get("prefix"))
        if prefix is not None:
            self._prefix = prefix

    # ── LLM 工具：规则的常驻载体 ──────────────────────────────────────

    async def _rule_tool_handler(self, **_):
        """规则载体工具本体：被调用时无害返回，顺带让模型再读一次描述。"""
        with self._lock:
            prefix = self._prefix
        return {"rule": "active", "prefix": prefix}

    def _register_rule_tool(self) -> bool:
        """把规则注册为 LLM 工具（重复注册为替换语义）。"""
        with self._lock:
            prefix = self._prefix
        try:
            try:
                self.unregister_llm_tool(_RULE_TOOL_NAME)
            except Exception:
                pass
            self.register_llm_tool(
                name=_RULE_TOOL_NAME,
                description=_RULE_TOOL_DESC.format(prefix=prefix),
                parameters={"type": "object", "properties": {}, "required": []},
                handler=self._rule_tool_handler,
            )
            self.logger.info("规则工具已注册：{}（prefix={}）", _RULE_TOOL_NAME, prefix)
            return True
        except Exception as e:
            self.logger.warning("规则工具注册失败：{}", e)
            return False

    def _rule_tool_missing_on_main(self) -> bool:
        """查询 main_server /api/tools，规则工具不在注册表时返回 True。"""
        data = _http_get_json("/api/tools")
        if not isinstance(data, dict):
            return False  # 查询失败不误判，等下一个巡检周期
        tools = data.get("tools", data)
        names = set()
        if isinstance(tools, list):
            for item in tools:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.add(item["name"])
                elif isinstance(item, str):
                    names.add(item)
        return _RULE_TOOL_NAME not in names

    # ── 推送 ──────────────────────────────────────────────────────────

    def _current_lanlan(self) -> str:
        """查询当前角色名，用于推送的 target_lanlan（查询失败返回空串）。"""
        data = _http_get_json("/api/characters/current_catgirl")
        if isinstance(data, dict):
            name = data.get("current_catgirl")
            if isinstance(name, str) and name.strip():
                return name.strip()
        return ""

    def _push_rule(self, mode: str = "respond", force: bool = False) -> bool:
        """把规则文本送进模型上下文（规则原文对用户不可见 visibility=[]）。"""
        if mode == "respond":
            with self._lock:
                last_respond = self._last_respond_ts
            if not force and (time.time() - last_respond) < _RESPOND_MIN_INTERVAL:
                self.logger.info("respond 注入限流：距上次注入过近，跳过")
                return False

        with self._lock:
            prefix = self._prefix
            lanlan = self._lanlan
        rule = _RULE_TEXT.format(prefix=prefix)
        result = self.push_message(
            source="call_mom",
            visibility=[],
            ai_behavior=mode,
            parts=[{"type": "text", "text": rule}],
            priority=3,
            target_lanlan=lanlan or None,
        )
        submitted = bool(result.get("submitted"))
        if submitted:
            now = time.time()
            self._last_injected_ts = now
            if mode == "respond":
                with self._lock:
                    self._last_respond_ts = now
            self.logger.info("call-mom 规则已注入（mode={} prefix={} target={})", mode, prefix, lanlan or "*")
        else:
            self.logger.warning(
                "call-mom 规则注入被拒绝：mode={} reason={}", mode, result.get("reason")
            )
        return submitted

    def _push_off_rule(self) -> None:
        """停用时尽力通知模型解除规则（best-effort，不阻塞关闭流程）。"""
        try:
            with self._lock:
                lanlan = self._lanlan
            result = self.push_message(
                source="call_mom",
                visibility=[],
                ai_behavior="read",
                parts=[{"type": "text", "text": _OFF_RULE}],
                priority=3,
                target_lanlan=lanlan or None,
            )
            if not result.get("submitted"):
                self.logger.warning("停用说明注入被拒绝：reason={}", result.get("reason"))
        except Exception as e:
            self.logger.warning("停用说明注入失败：{}", e)

    # ── 生命周期 ──────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        cfg = await self.config.dump()
        self._apply_config(cfg.get("call_mom", {}) or {})
        with self._lock:
            self._lanlan = self._current_lanlan()
        self._ready = True
        self._register_rule_tool()
        self._push_rule(mode="respond", force=True)
        return Ok({"status": "ready", "prefix": self._prefix})

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config, new_config, mode, **_):
        old_prefix = self._prefix
        self._apply_config((new_config or {}).get("call_mom", {}) or {})
        if self._prefix != old_prefix:
            # 前缀变了：工具描述需要同步更新
            self._register_rule_tool()
        self._push_rule(mode="respond")
        return Ok({"status": "config_updated", "prefix": self._prefix})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._ready = False
        self._push_off_rule()
        return Ok({"status": "stopped"})

    # ── 巡检 ──────────────────────────────────────────────────────────

    # 巡检步长必须以内联字面量声明（发布检查要求 seconds > 0 的字面量）
    @timer_interval(id="rule_watchdog", seconds=60, auto_start=True)
    async def rule_watchdog(self, **_):
        if not self._ready:
            return Ok({"action": "skip"})
        # 1) 规则工具从 main_server 注册表丢失 → 补注册
        if self._rule_tool_missing_on_main():
            self.logger.warning("规则工具在 main_server 注册表中丢失，重新注册")
            self._register_rule_tool()
        # 2) 当前角色名可能变化，低频刷新
        with self._lock:
            known = self._lanlan
        current = self._current_lanlan()
        if current and current != known:
            with self._lock:
                self._lanlan = current
        return Ok({"action": "checked"})

    # ── 入口 ──────────────────────────────────────────────────────────

    @plugin_entry(id="status", name="Call-Mom 状态", description="查看当前前缀与注入状态")
    async def status(self, **_):
        with self._lock:
            return Ok({
                "prefix": self._prefix,
                "rule_tool": _RULE_TOOL_NAME,
                "rule_tool_missing": self._rule_tool_missing_on_main(),
                "target": self._lanlan,
                "ready": self._ready,
                "last_injected": self._last_injected_ts,
            })

    @plugin_entry(id="set_prefix", name="设置前缀", description="修改每句话自动添加的前缀，立即生效")
    async def set_prefix(self, prefix: str = "", **_):
        clean = _normalize_prefix(prefix)
        if clean is None:
            return Err(SdkError(f"前缀无效：需为 1~{_MAX_PREFIX_LEN} 个字符的非空文本"))
        # 宿主不会把 config_change 回派给自己，派生状态手动刷新。
        persisted = True
        try:
            await self.config.update({"call_mom": {"prefix": clean}})
        except Exception as e:
            persisted = False
            self.logger.warning("前缀写入运行时配置失败（仅本次会话生效）：{}", e)
        with self._lock:
            self._prefix = clean
        self._register_rule_tool()   # 工具描述携带前缀，必须同步更新
        self._push_rule(mode="respond", force=True)
        return Ok({"prefix": clean, "rule_reinjected": True, "persisted": persisted})

    @plugin_entry(id="inject_now", name="立即重注入", description="手动向当前会话重新注入一次前缀规则")
    async def inject_now(self, **_):
        self._register_rule_tool()
        submitted = self._push_rule(mode="respond", force=True)
        if not submitted:
            return Err(SdkError("注入被宿主拒绝，请查看插件日志"))
        return Ok({"injected": True, "rule_tool": _RULE_TOOL_NAME})
