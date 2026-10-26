# 和猫娘一起开飞机（简单飞机2副驾驶）插件 —— 副驾驶仪表盘（Hosted UI 层）
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 本文件是 sp2_copilot 插件的一部分，以 GNU General Public License v3.0 许可发布。
# 完整条款见仓库根目录的 LICENSE 文件。

"""副驾驶仪表盘：Hosted UI 的上下文（@ui.context）与动作（@ui.action）。

面板需要的能力分两类，故意用两种方式实现：

1. **插件里已经有的能力** —— 飞行状态、副驾驶简报。逻辑已经写在 ``__init__.py``
   里了，直接在原方法上叠加 ``@ui.action`` 把它们暴露给面板，本模块**不重写一遍**
   （两份实现迟早会跑偏，而且改一处忘一处）。
2. **只有面板才需要的能力** —— 连通性探测、聚合快照、设置保存。集中放在本模块，
   免得 ``__init__.py`` 继续变长。

约定：对外只返回 JSON 安全的 ``Ok``/``Err``；面板拿到的 ``result`` 就是这个 payload。

⚠️ 本插件是**只读副驾驶**：这里没有任何一个动作会给游戏发控制指令，
面板上也刻意不放任何「帮我操作」的按钮 —— 动手的人永远是玩家。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, ui

# 面板 surface 在 plugin.toml 里声明的 context id，两边必须一致，
# 否则 host 的 get_ui_context 会报 "UI context not found"。
PANEL_CONTEXT_ID = "dashboard"

# 配置段名：与 plugin.toml / config.example.toml 保持一致。
_CFG_SECTION = "sp2_copilot"

# self.config.update() 的预算。SDK 内部会把它压到 4.5 秒以内，
# 超时也只是退化成「内存里先生效」，所以不要给太大。
_CONFIG_WRITE_TIMEOUT = 4.5

# 连不上时别让面板卡住：探测用的超时比正常请求短得多。
_PING_TIMEOUT = 3.0

# 告警等级在面板上的显示文案与配色。后端只给 level，文案由面板统一维护。
UI_LEVELS: list = [
    {"id": "danger", "label": "紧急", "tone": "danger"},
    {"id": "warning", "label": "注意", "tone": "warning"},
    {"id": "caution", "label": "提示", "tone": "info"},
    {"id": "info", "label": "提示", "tone": "info"},
]

# 飞行阶段在面板上的显示文案。后端只给 phase id，文案由面板统一维护。
UI_PHASES: list = [
    {"id": "menu", "label": "未进入飞行", "tone": "default"},
    {"id": "parked", "label": "地面停机", "tone": "info"},
    {"id": "taxi", "label": "地面滑行", "tone": "info"},
    {"id": "takeoff_roll", "label": "起飞滑跑", "tone": "warning"},
    {"id": "rollout", "label": "着陆滑跑", "tone": "warning"},
    {"id": "initial_climb", "label": "初始爬升", "tone": "success"},
    {"id": "climb", "label": "爬升", "tone": "success"},
    {"id": "cruise", "label": "巡航", "tone": "success"},
    {"id": "descent", "label": "下降", "tone": "info"},
    {"id": "approach", "label": "进近着陆", "tone": "warning"},
    {"id": "crashed", "label": "已坠毁", "tone": "danger"},
    {"id": "unknown", "label": "阶段未知", "tone": "default"},
]


class Sp2UiMixin:
    """副驾驶仪表盘的上下文与动作，由 ``Sp2CopilotPlugin`` 混入。

    只依赖宿主类（``Sp2CopilotPlugin``）已有的属性与方法；下面的属性声明仅用于
    说明与静态检查，运行时不赋值，宿主类的 ``__init__`` 负责给真实值。
    """

    # ── 宿主类提供的运行时状态（见 __init__.py）──────────────────────────
    _bridge_url: str
    _timeout: float
    _poll_interval: float
    _thresholds: Dict[str, float]

    logger: Any
    config: Any
    ctx: Any

    # ── 内部工具 ─────────────────────────────────────────────────────────

    def _host(self) -> Any:
        """返回宿主类所在的模块（也就是 ``__init__.py``）。

        用它复用 ``__init__.py`` 里的纯函数（``_as_text`` / ``_num`` …），
        而**不**写成模块级 ``from . import ...``：宿主导入本混入时它自己还没执行完，
        那样会形成循环导入。这里是在运行时按类反查，所以永远拿得到。
        """
        import sys

        return sys.modules[type(self).__module__]

    def _ui_effective_config(self) -> Dict[str, Any]:
        """面板要用的「当前生效值」。"""
        th = self._thresholds if isinstance(self._thresholds, dict) else {}
        defaults = self._host()._default_thresholds()
        return {
            "bridge_url": str(getattr(self, "_bridge_url", "") or ""),
            "timeout_seconds": float(getattr(self, "_timeout", 12.0)),
            "poll_interval_seconds": float(getattr(self, "_poll_interval", 3.0)),
            "auto_brief_enabled": bool(getattr(self, "_auto_brief_enabled", True)),
            "auto_brief_interval_seconds": float(getattr(self, "_auto_brief_interval", 20.0)),
            "terrain_warning_agl_m": float(th.get("terrain_agl_m", defaults["terrain_agl_m"])),
            "sink_rate_warning_mps": float(th.get("sink_rate_mps", defaults["sink_rate_mps"])),
            "bank_warning_deg": float(th.get("bank_deg", defaults["bank_deg"])),
            "low_fuel_warning_percent": float(
                th.get("low_fuel_percent", defaults["low_fuel_percent"])
            ),
            "aoa_warning_deg": float(th.get("aoa_deg", defaults["aoa_deg"])),
            "overspeed_warning_ratio": float(th.get("overspeed_ratio", defaults["overspeed_ratio"])),
        }

    def _ui_remember(self, connected: bool, error: str = "") -> None:
        self._ui_last_connected = connected
        self._ui_last_error = error if not connected else ""

    async def _ui_ping(self) -> Tuple[bool, str, int]:
        """只读连通性探测：打一次 ``/ping``。

        返回 ``(connected, error, latency_ms)``。刻意不用 ``self._get_json``，
        因为它用的是 12 秒的业务超时 —— 游戏没开时面板会被吊住。
        """
        url = f"{str(getattr(self, '_bridge_url', '') or '').rstrip('/')}/ping"
        client = self._get_client()  # type: ignore[attr-defined]
        timeout = min(_PING_TIMEOUT, float(getattr(self, "_timeout", 12.0)))
        started = time.monotonic()
        try:
            response = await client.get(url, timeout=timeout)
        except Exception as exc:  # httpx 之外也可能是别的东西炸
            return False, self._ui_offline_message(exc), 0
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            return False, f"SP2-Bridge 返回状态 {response.status_code}。", latency
        return True, "", latency

    def _ui_offline_message(self, exc: Any = None) -> str:
        url = str(getattr(self, "_bridge_url", "") or "") or "（未配置地址）"
        message = (
            f"连不上 SP2-Bridge 桥接服务（{url}）。请依次确认："
            "1) 《简单飞机2》正在运行；2) 已装好 SP2-Bridge 模组；"
            "3) 装完模组后重启过游戏；4) 模组侧的端口和这里的地址一致。"
        )
        if exc is not None:
            message += f"（底层错误：{exc}）"
        return message

    def _ui_offline_payload(self, error: str) -> Dict[str, Any]:
        """离线时面板要的最小可用结构 —— 字段一个都不能少，
        否则面板会出现 undefined 的空卡片。"""
        return {
            "connected": False,
            "error": error,
            "latency_ms": 0,
            "bridge_url": str(getattr(self, "_bridge_url", "") or ""),
            "state": {},
            "aircraft": {},
            "navigation": {},
            "environment": {},
            "phase": "menu",
            "phase_label": "未连接",
            "status_summary": error,
            "warning_summary": "",
            "checklist_summary": "",
            "navigation_summary": "",
            "warnings": [],
            "checklist": {"items": [], "done_count": 0, "pending_count": 0, "unknown_count": 0},
            "errors": {},
            "fetched_at": time.time(),
        }

    # ── 面板上下文 ───────────────────────────────────────────────────────
    #
    # ⚠️ 这个 provider 会被 host 在**每次** hosted action 调用前重新求值
    # （ui_query_service 用它取动作白名单），所以它必须又快又不能抛异常：
    #   · 不做网络请求（连通性交给 sp2_ui_ping / sp2_ui_snapshot）；
    #   · 任何异常都自己吞掉，返回一个最小可用的 dict。

    @ui.context(id=PANEL_CONTEXT_ID, title="副驾驶仪表盘")
    async def get_dashboard_ui_context(self) -> Dict[str, Any]:
        try:
            return {
                "connected": getattr(self, "_ui_last_connected", None),
                "last_error": getattr(self, "_ui_last_error", ""),
                "bridge_url": str(getattr(self, "_bridge_url", "") or ""),
                "read_only": True,
                "config": self._ui_effective_config(),
                "levels": list(UI_LEVELS),
                "phases": list(UI_PHASES),
            }
        except Exception as exc:  # pragma: no cover - provider 绝不能抛
            self.logger.warning("[sp2_copilot] 面板上下文构造失败：{}", exc)
            return {
                "connected": None,
                "last_error": "",
                "bridge_url": "",
                "read_only": True,
                "config": {},
                "levels": list(UI_LEVELS),
                "phases": list(UI_PHASES),
            }

    # ── 连接与快照 ───────────────────────────────────────────────────────

    @ui.action(id="sp2_ui_ping", label="检测连接", tone="info", group="连接", order=10)
    @plugin_entry(
        id="sp2_ui_ping",
        name="检测简单飞机2连接",
        description=(
            "面板用：探测 SP2-Bridge 桥接服务是否在线。只发一次 /ping，"
            "不读遥测也不碰游戏状态，适合面板按秒轮询。"
        ),
        input_schema={"type": "object", "properties": {}},
        timeout=15,
        llm_result_fields=["connected", "message"],
    )
    async def sp2_ui_ping(self, **_):
        connected, error, latency = await self._ui_ping()
        self._ui_remember(connected, error)
        if not connected:
            return Ok({
                "connected": False,
                "latency_ms": 0,
                "bridge_url": str(getattr(self, "_bridge_url", "") or ""),
                "message": error or "连不上游戏。",
            })
        return Ok({
            "connected": True,
            "latency_ms": latency,
            "bridge_url": str(getattr(self, "_bridge_url", "") or ""),
            "message": f"已连接（{latency} 毫秒）",
        })

    @ui.action(id="sp2_ui_snapshot", label="刷新仪表盘", tone="primary", group="连接", order=20)
    @plugin_entry(
        id="sp2_ui_snapshot",
        name="读取副驾驶仪表盘快照",
        description=(
            "面板用：一次性读取连接状态、飞行遥测、机体数据、导航、环境，"
            "以及由插件算好的飞行阶段、告警清单与检查单。"
            "面板打开、点刷新、以及自动刷新时调用；比逐个 entry 调用少绕几趟。"
        ),
        input_schema={"type": "object", "properties": {}},
        timeout=45,
        llm_result_fields=["connected", "phase", "warning_summary"],
    )
    async def sp2_ui_snapshot(self, **_):
        connected, error, latency = await self._ui_ping()
        self._ui_remember(connected, error)
        if not connected:
            return Ok(self._ui_offline_payload(error))

        snap = await self._snapshot()  # type: ignore[attr-defined]
        if not snap.get("state"):
            # ping 通了但 /state 读不出来：把具体错误带回去，别让面板一脸懵。
            errors = snap.get("errors") or {}
            detail = errors.get("state") or "桥接服务在线，但读不到 /state。"
            self._ui_remember(False, detail)
            return Ok(self._ui_offline_payload(detail))

        briefing = self._build_briefing(snap)  # type: ignore[attr-defined]
        payload = self._ui_offline_payload("")
        payload.update({
            "connected": True,
            "error": "",
            "latency_ms": latency,
            "errors": briefing.get("errors", {}),
            "state": briefing.get("state", {}),
            "aircraft": briefing.get("aircraft", {}),
            "navigation": briefing.get("navigation", {}),
            "environment": briefing.get("environment", {}),
            "phase": briefing.get("phase", "unknown"),
            "phase_label": briefing.get("phase_label", "未知"),
            "status_summary": briefing.get("status_summary", ""),
            "warning_summary": briefing.get("warning_summary", ""),
            "checklist_summary": briefing.get("checklist_summary", ""),
            "navigation_summary": briefing.get("navigation_summary", ""),
            "warnings": briefing.get("warnings", []),
            "checklist": briefing.get("checklist", {}),
            "read_only": True,
            "fetched_at": time.time(),
        })
        return Ok(payload)

    # ── 设置保存 ─────────────────────────────────────────────────────────

    @ui.action(
        id="sp2_ui_save_settings",
        label="保存设置",
        tone="success",
        group="设置",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="sp2_ui_save_settings",
        name="保存副驾驶仪表盘设置",
        description=(
            "面板用：把桥接地址、超时、轮询间隔与告警阈值写回插件配置并立即生效。"
            "只影响「什么时候提醒你」，不影响游戏本身，也不会给游戏发任何指令。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "bridge_url": {"type": "string"},
                "timeout_seconds": {"type": "number"},
                "poll_interval_seconds": {"type": "number"},
                "auto_brief_enabled": {"type": "boolean"},
                "auto_brief_interval_seconds": {"type": "number"},
                "terrain_warning_agl_m": {"type": "number"},
                "sink_rate_warning_mps": {"type": "number"},
                "bank_warning_deg": {"type": "number"},
                "low_fuel_warning_percent": {"type": "number"},
                "aoa_warning_deg": {"type": "number"},
                "overspeed_warning_ratio": {"type": "number"},
            },
        },
        timeout=30,
        llm_result_fields=["ok", "message"],
    )
    async def sp2_ui_save_settings(
        self,
        bridge_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        poll_interval_seconds: Optional[float] = None,
        auto_brief_enabled: Optional[bool] = None,
        auto_brief_interval_seconds: Optional[float] = None,
        terrain_warning_agl_m: Optional[float] = None,
        sink_rate_warning_mps: Optional[float] = None,
        bank_warning_deg: Optional[float] = None,
        low_fuel_warning_percent: Optional[float] = None,
        aoa_warning_deg: Optional[float] = None,
        overspeed_warning_ratio: Optional[float] = None,
        **_,
    ):
        host = self._host()
        current = self._ui_effective_config()

        clean_url = host._as_text(bridge_url) or current["bridge_url"]
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            return Err(SdkError(
                "桥接地址必须以 http:// 或 https:// 开头，"
                "例如 http://127.0.0.1:21580。"
            ))

        def _number(value: Any, default: float, low: float, high: float) -> float:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return float(default)
            if number != number:  # NaN
                return float(default)
            return min(max(number, low), high)

        section = {
            "bridge_url": clean_url,
            "timeout_seconds": _number(timeout_seconds, current["timeout_seconds"], 3.0, 60.0),
            "poll_interval_seconds": _number(
                poll_interval_seconds, current["poll_interval_seconds"], 1.0, 30.0
            ),
            "auto_brief_enabled": (
                bool(auto_brief_enabled)
                if auto_brief_enabled is not None
                else bool(current["auto_brief_enabled"])
            ),
            "auto_brief_interval_seconds": _number(
                auto_brief_interval_seconds, current["auto_brief_interval_seconds"], 5.0, 120.0
            ),
            "terrain_warning_agl_m": _number(
                terrain_warning_agl_m, current["terrain_warning_agl_m"], 30.0, 1000.0
            ),
            "sink_rate_warning_mps": _number(
                sink_rate_warning_mps, current["sink_rate_warning_mps"], 1.0, 30.0
            ),
            "bank_warning_deg": _number(bank_warning_deg, current["bank_warning_deg"], 15.0, 90.0),
            "low_fuel_warning_percent": _number(
                low_fuel_warning_percent, current["low_fuel_warning_percent"], 1.0, 50.0
            ),
            "aoa_warning_deg": _number(aoa_warning_deg, current["aoa_warning_deg"], 5.0, 30.0),
            "overspeed_warning_ratio": _number(
                overspeed_warning_ratio, current["overspeed_warning_ratio"], 0.5, 1.0
            ),
        }

        try:
            await self.config.update({_CFG_SECTION: section}, timeout=_CONFIG_WRITE_TIMEOUT)
        except Exception as exc:
            self.logger.warning("[sp2_copilot] 面板保存配置失败：{}", exc)
            return Err(SdkError(f"保存设置失败：{exc}"))

        # 立刻应用到运行时：本插件没有 config_change 钩子，等框架回调就晚了。
        self._bridge_url = section["bridge_url"]
        self._timeout = section["timeout_seconds"]
        self._poll_interval = section["poll_interval_seconds"]
        self._auto_brief_enabled = section["auto_brief_enabled"]
        self._auto_brief_interval = section["auto_brief_interval_seconds"]
        self._thresholds = {
            "terrain_agl_m": section["terrain_warning_agl_m"],
            "sink_rate_mps": section["sink_rate_warning_mps"],
            "bank_deg": section["bank_warning_deg"],
            "low_fuel_percent": section["low_fuel_warning_percent"],
            "aoa_deg": section["aoa_warning_deg"],
            "overspeed_ratio": section["overspeed_warning_ratio"],
        }

        # 自动播报的开关/间隔也可能刚被改了：按新设置重起后台任务（幂等）。
        try:
            await self._stop_auto_brief()
            if self._auto_brief_enabled:
                self._start_auto_brief()
        except Exception as exc:  # pragma: no cover
            self.logger.debug("[sp2_copilot] 重启动自动播报失败：{}", exc)

        return Ok({
            "ok": True,
            "config": self._ui_effective_config(),
            "message": "设置已保存并立即生效（只影响提醒时机，不会动你的飞机）。",
        })


__all__ = ["Sp2UiMixin", "PANEL_CONTEXT_ID", "UI_LEVELS", "UI_PHASES"]
