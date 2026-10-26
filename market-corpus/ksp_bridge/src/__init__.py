# 和猫娘一起坐火箭（KSP）插件 —— 连接 Kerbal Space Program
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""KSP Bridge Plugin（和猫娘一起坐火箭）

连接《坎巴拉太空计划 Kerbal Space Program》，让猫娘读懂当前飞行：
- 飞船状态：高度、速度、姿态、质量、乘员
- 轨道根数：远点/近点/离心率/倾角/周期，以及「算不算入轨」
- 资源余量：燃料、氧化剂、电、单组元
- 星系与目标：天体数据、锁定的目标与相对速度

**这个版本是只读的** —— 插件不发送任何控制指令，飞船始终由玩家驾驶。

依赖：游戏内需安装配套的 N.E.K.O-KSP-bridge 模组（见 ../ksp-bridge）。
该模组在 127.0.0.1:21579 提供 HTTP 接口。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

DEFAULT_BRIDGE_URL = "http://127.0.0.1:21579"
_USER_AGENT = "NekoKspBridge/0.1 (+https://xhth.top/)"

# 自动播报：push_message 的合并键。同一个 key 的排队消息只会保留最新一条，
# 所以后台按固定间隔反复推也不会把角色的上下文越堆越长。
AUTO_BRIEF_COALESCE_KEY = "ksp_auto_brief"
AUTO_BRIEF_PREFIX = "【KSP 自动播报】"


# ---------------------------------------------------------------------------
# 取值辅助
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(n, low), high)


def _num(value: Any) -> Optional[float]:
    """把游戏给的数字转成 float；null / NaN 一律返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / Inf
        return None
    return f


def _fmt(value: Any, unit: str = "", digits: int = 1) -> str:
    """把数字格式化成人类可读的形式，取不到就返回「未知」。"""
    f = _num(value)
    if f is None:
        return "未知"
    text = f"{f:,.{digits}f}"
    return f"{text}{unit}" if unit else text


def _fmt_dist(value: Any) -> str:
    """距离：自动换算成 m / km / Mm。"""
    f = _num(value)
    if f is None:
        return "未知"
    af = abs(f)
    if af >= 1_000_000:
        return f"{f / 1_000_000:,.2f} Mm"
    if af >= 1_000:
        return f"{f / 1_000:,.2f} km"
    return f"{f:,.1f} m"


def _fmt_time(value: Any) -> str:
    """秒 -> 「1天2小时3分」这种。"""
    f = _num(value)
    if f is None:
        return "未知"
    neg = f < 0
    f = abs(f)
    total = int(f)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: List[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if not parts:
        parts.append(f"{seconds}秒")
    return ("-" if neg else "") + "".join(parts)


# ---------------------------------------------------------------------------
# 游戏知识（给不了解 KSP 的模型看）
# ---------------------------------------------------------------------------

_KSP_DOC = """【坎巴拉太空计划是什么】
KSP 是一款「用乐高式零件拼火箭、按真实开普勒轨道飞」的航天模拟游戏。
核心循环：造火箭 → 发射 → 入轨 → 变轨 → 对接 → 登陆 → 返回。

和《航天模拟器》不同，KSP 用的是**开普勒轨道**的航天模拟，默认星系（原版）是缩小了约 10 倍的：
- Kerbin 半径 600 km，重力 1.0 g，大气 70 km —— 相当于地球的 1/10 尺寸
- 因此环绕速度约 2.3 km/s（现实中是 7.9 km/s）
- 入轨判定看**近点是否高过大气层顶**（原版 Kerbin 是 70 km，不是 100 km 卡门线）

【星球名字必须来自游戏数据，不要背】
上面这些数字只是**原版 Kerbin** 的参考值。玩家可能装了 Realism Overhaul / RSS 之类的
真实化模组，那时候母星叫 **Earth（地球）**，半径 6371 km，大气 140 km，环绕速度 7.9 km/s，
完全不是 Kerbin 那一套。
- **用户问「我在哪颗星球」「这个星球多大」「大气多高」时，只依据 /state 里的 body 字段、
  /orbit 里的 body、或 list_ksp_bodies 的返回来说话**，这些是游戏真实值。
- 不要因为「KSP 默认是 Kerbin」就替玩家把 Earth 说成 Kerbin。
- 数值也只引用接口返回的，别把原版数字套到真实化星系上。

【关键术语】
- 远点 apoapsis (Ap) —— 轨道最高点
- 近点 periapsis (Pe) —— 轨道最低点
- 离心率 eccentricity —— 0 是正圆，0~1 是椭圆，≥1 是逃逸
- 倾角 inclination —— 轨道面相对赤道的夹角
- 周期 period —— 绕一圈要多久
- 大气层顶 atmosphere depth —— 低于它就会被大气减速/烧毁

【常见飞行阶段的判断】
- PRELAUNCH 在发射台还没点火
- FLYING 在大气层内飞
- SUB_ORBITAL 近点还在地表以下（会掉回来）
- ORBITING 近点已经高过大气层顶（稳定轨道）
- ESCAPING 已经超过逃逸速度
- LANDED / SPLASHED 已着陆 / 溅落
- DOCKED 已对接

【资源】
- LiquidFuel + Oxidizer —— 液体燃料引擎用，两个要一起消耗
- MonoPropellant —— 姿控（RCS）用
- ElectricCharge —— 电力，太阳能板会回充
- SolidFuel —— 固体助推器用

燃料分「当前级」和「全船」。玩家常关心「还剩多少」，用 /resources 看总量。

【怎么帮玩家】
- 上下文里已经有一条「【KSP 自动播报】」时，**先直接用那条里的数字回答**，
  不要再为了同一批数据去调工具 —— 玩家已经抱怨过「听到了不说话、查到才回」。
- 自动播报里没有的细节才调工具：
  轨道根数全量/变轨窗口 → get_ksp_orbit；逐项资源 → get_ksp_resources；
  目标/对接 → get_ksp_target；星系 → list_ksp_bodies。
- 确实要查、又可能要等一会儿时，**先说一句「我看一眼仪表」**再调工具，
  不要一声不吭地卡着。

注意：**这个插件是只读的**，你不能操作飞船。
要指点玩家怎么飞就给出建议，别假装你能替他按按钮。
"""


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------


@neko_plugin
class KspBridgePlugin(NekoPluginBase):
    """坎巴拉太空计划桥接插件（只读）。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self._cfg: Dict[str, Any] = {}
        self._bridge_url: str = DEFAULT_BRIDGE_URL
        self._timeout: float = 6.0
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Any = None

        # 自动播报（游戏在跑就自己在后台读遥测，推进角色上下文）
        self._auto_brief_enabled: bool = True
        self._auto_brief_interval: float = 30.0
        self._auto_brief_task: Any = None
        self._auto_brief_stop: Any = None
        self._auto_brief_last_text: str = ""
        self._auto_brief_last_ok: Optional[bool] = None

    # -- 基础设施 ---------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """按事件循环缓存客户端（宿主会在不同 asyncio.run 中调用）。"""
        try:
            loop: Any = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover
            loop = None

        if self._client is None or self._client.is_closed or self._client_loop is not loop:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._timeout,
                headers={"User-Agent": _USER_AGENT},
            )
            self._client_loop = loop
        return self._client

    async def _get_json(self, path: str) -> Dict[str, Any]:
        """GET 一个 JSON 接口。"""
        url = f"{self._bridge_url.rstrip('/')}{path}"
        client = self._get_client()
        try:
            response = await client.get(url, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise SdkError(
                "读取游戏数据超时。KSP 在加载、切场景或高倍加速时可能会卡一下，"
                "可以稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise SdkError(
                f"连不上 KSP 桥接服务（{self._bridge_url}）。请确认："
                "1) 游戏正在运行；2) 已把 N.E.K.O-KSP-bridge 装进 GameData；"
                "3) 装好后重启过游戏。"
            ) from exc

        if response.status_code >= 400:
            raise SdkError(f"KSP 桥接服务返回状态 {response.status_code}。")
        try:
            return response.json()
        except ValueError as exc:
            raise SdkError("KSP 桥接服务返回的内容不是合法 JSON。") from exc

    # -- 自动播报 ---------------------------------------------------------

    async def _auto_brief_once(self) -> None:
        """读一次遥测，把最新情况推进角色上下文（不打断对话）。

        用 `ai_behavior="read"`：消息进 LLM 上下文但不会立刻触发一轮对话，
        主人下次开口时角色已经知道飞船什么状况，不用再主动去调工具。
        """
        try:
            state = await self._get_json("/state")
            connected = True
        except SdkError:
            state = {}
            connected = False
        except Exception as exc:  # pragma: no cover
            self.logger.debug("自动播报读取失败: {}", exc)
            return

        if connected:
            text = _auto_brief_line(state)
            if not text:
                # 当前没有飞船（停机坪/编辑器），这条不用发，省点上下文。
                self._auto_brief_last_ok = True
                return
        else:
            if not self._auto_brief_last_ok:
                # 从来没连上过就别灌噪音了，主人自己会问。
                return
            text = f"{AUTO_BRIEF_PREFIX}游戏已经退出或断开了，读不到新的遥测数据。"

        if connected == self._auto_brief_last_ok and text == self._auto_brief_last_text:
            return

        try:
            result = self.push_message(
                visibility=[],
                ai_behavior="read",
                coalesce_key=AUTO_BRIEF_COALESCE_KEY,
                source="ksp_auto_brief",
                parts=[{"type": "text", "text": text}],
            )
            if not _as_dict(result).get("submitted"):
                self.logger.debug("自动播报未进队: {}", result)
        except Exception as exc:  # pragma: no cover
            self.logger.debug("自动播报推送失败: {}", exc)
            return

        self._auto_brief_last_ok = connected
        self._auto_brief_last_text = text

    def _start_auto_brief(self) -> None:
        """起后台轮询任务（幂等）。"""
        # 用 getattr：测试里常用 __new__ 造实例，没走 __init__。
        task = getattr(self, "_auto_brief_task", None)
        if task is not None and not task.done():
            return
        try:
            self._auto_brief_stop = asyncio.Event()
            self._auto_brief_task = asyncio.create_task(self._auto_brief_loop())
        except RuntimeError:  # pragma: no cover - 没有事件循环（单测/同步启动）
            self._auto_brief_stop = None
            self._auto_brief_task = None

    async def _stop_auto_brief(self) -> None:
        """停掉后台轮询任务（幂等）。"""
        # 用 getattr：测试里常用 __new__ 造实例，没走 __init__。
        task = getattr(self, "_auto_brief_task", None)
        stop = getattr(self, "_auto_brief_stop", None)
        self._auto_brief_task = None
        self._auto_brief_stop = None
        if stop is not None:
            stop.set()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover
                self.logger.debug("自动播报任务收尾出错: {}", exc)

    async def _auto_brief_loop(self) -> None:
        """按间隔循环播报，直到被叫停。"""
        stop = self._auto_brief_stop
        while stop is not None and not stop.is_set():
            try:
                await self._auto_brief_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                self.logger.debug("自动播报循环出错: {}", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._auto_brief_interval)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            return

    # -- 生命周期 ---------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取配置失败，使用默认值: {}", exc)
            cfg = {}
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg

        section = _as_dict(cfg.get("ksp_bridge"))
        self._bridge_url = _as_text(section.get("bridge_url")) or DEFAULT_BRIDGE_URL

        try:
            self._timeout = float(section.get("timeout_seconds", 6))
        except (TypeError, ValueError):
            self._timeout = 6.0
        self._timeout = min(max(self._timeout, 3.0), 60.0)

        self._auto_brief_enabled = _as_bool(section.get("auto_brief_enabled"), True)
        brief_interval = _num(section.get("auto_brief_interval_seconds"))
        self._auto_brief_interval = (
            min(max(brief_interval, 5.0), 300.0) if brief_interval is not None else 15.0
        )

        self.logger.info(
            "KspBridge 已就绪（只读模式），桥接地址={}，自动播报={}",
            self._bridge_url,
            f"开/{self._auto_brief_interval:g}s" if self._auto_brief_enabled else "关",
        )
        if self._auto_brief_enabled:
            self._start_auto_brief()
        return Ok({
            "status": "ready",
            "mode": "read-only",
            "bridge_url": self._bridge_url,
            "auto_brief": self._auto_brief_enabled,
            "auto_brief_interval_seconds": self._auto_brief_interval,
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        await self._stop_auto_brief()
        client = self._client
        self._client = None
        self._client_loop = None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception as exc:  # pragma: no cover
                self.logger.debug("关闭客户端出错: {}", exc)
        return Ok({"status": "stopped"})

    # -- 插件入口（面板 / 宿主调用）---------------------------------------

    @plugin_entry(
        id="ksp_status",
        name="坎巴拉状态",
        description="读取 KSP 当前飞行状态：飞船、轨道、资源、乘员与目标。",
        timeout=30,
        llm_result_fields=["summary", "connected", "state"],
    )
    async def ksp_status(self, **_):
        """读取游戏状态。"""
        try:
            state = await self._get_json("/state")
        except SdkError as exc:
            return Ok({"connected": False, "summary": str(exc)})
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取状态失败: {}", exc)
            return Ok({"connected": False, "summary": f"读取失败：{exc}"})

        return Ok({
            "connected": True,
            "summary": _summarize_state(state),
            "state": state,
        })

    @plugin_entry(
        id="ksp_orbit",
        name="坎巴拉轨道",
        description="读取 KSP 当前轨道的根数：远点、近点、离心率、倾角、周期。",
        timeout=30,
        llm_result_fields=["summary", "connected", "orbit"],
    )
    async def ksp_orbit(self, **_):
        """读取轨道。"""
        try:
            orbit = await self._get_json("/orbit")
        except SdkError as exc:
            return Ok({"connected": False, "summary": str(exc)})
        except Exception as exc:  # pragma: no cover
            return Ok({"connected": False, "summary": f"读取失败：{exc}"})

        return Ok({
            "connected": True,
            "summary": _summarize_orbit(orbit),
            "orbit": orbit,
        })

    # -- LLM 工具 ---------------------------------------------------------

    @llm_tool(
        name="get_ksp_status",
        description=(
            "读取《坎巴拉太空计划》当前飞行状态。"
            "用户说「我现在飞到哪了」「看看我的飞船」「高度多少」「速度多少」时调用。"
            "返回飞船名称、飞行阶段、高度、各种速度、姿态、质量、乘员，"
            "以及所在地天体。这是最常用的一个接口。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_ksp_status(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取飞行状态。"""
        try:
            state = await self._get_json("/state")
        except SdkError as exc:
            return {
                "output": {
                    "ok": False,
                    "connected": False,
                    "message": (
                        f"{exc}\n"
                        "请如实告诉用户现在连不上游戏，不要凭空编造飞行数据。"
                    ),
                },
                "is_error": False,
            }

        vessel = _as_dict(state.get("vessel"))
        if not _as_dict(vessel).get("exists"):
            note = _as_text(vessel.get("note")) or "当前没有可读的飞船。"
            return {
                "output": {
                    "ok": True,
                    "connected": True,
                    "vessel_exists": False,
                    "scene": state.get("scene"),
                    "message": f"{note}（当前场景：{state.get('scene')}）",
                },
                "is_error": False,
            }

        return {
            "output": {
                "ok": True,
                "connected": True,
                "vessel_exists": True,
                "scene": state.get("scene"),
                "summary": _summarize_state(state),
                "vessel": vessel,
                "throttle": vessel.get("throttle"),
                "orbit": _as_dict(state.get("orbit")),
                "resources": _as_dict(state.get("resources")).get("resources") or [],
                "crew": _as_dict(state.get("crew")).get("crew") or [],
                "target": _as_dict(state.get("target")),
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_ksp_orbit",
        description=(
            "读取《坎巴拉太空计划》当前轨道的详细根数。"
            "用户问「我入轨了吗」「轨道什么样」「远点近点多少」「什么时候到远点」时调用。"
            "返回值里 is_stable_orbit 直接说明算不算稳定轨道；"
            "大气层高度也一并给出，方便判断近点够不够高。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_ksp_orbit(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取轨道根数。"""
        try:
            orbit = await self._get_json("/orbit")
        except SdkError as exc:
            return {
                "output": {"ok": False, "message": str(exc)},
                "is_error": False,
            }

        if not _as_dict(orbit).get("exists"):
            return {
                "output": {
                    "ok": True,
                    "exists": False,
                    "message": "当前没有轨道数据（不在飞行场景，或飞船已毁）。",
                },
                "is_error": False,
            }

        return {
            "output": {
                "ok": True,
                "exists": True,
                "summary": _summarize_orbit(orbit),
                "orbit": orbit,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_ksp_resources",
        description=(
            "读取《坎巴拉太空计划》飞船上的资源余量："
            "液体燃料、氧化剂、单组元推进剂、电力、固体燃料等。"
            "用户问「油还够吗」「还剩多少电」时调用。"
            "每个资源给出总量、上限和百分比。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_ksp_resources(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取资源余量。"""
        try:
            data = await self._get_json("/resources")
        except SdkError as exc:
            return {
                "output": {"ok": False, "message": str(exc)},
                "is_error": False,
            }

        items = data.get("resources") or []
        if not items:
            return {
                "output": {
                    "ok": True,
                    "count": 0,
                    "message": "飞船上没有可读的资源（可能不在飞行场景，或这艘船确实没装资源）。",
                },
                "is_error": False,
            }

        lines = []
        for r in items:
            name = _as_text(r.get("name"))
            pct = _num(r.get("percent"))
            pct_text = f"{pct * 100:.0f}%" if pct is not None else "?"
            lines.append(f"{name} {_fmt(r.get('amount'), '', 1)} / {_fmt(r.get('max'), '', 1)}（{pct_text}）")

        return {
            "output": {
                "ok": True,
                "count": len(items),
                "summary": "资源余量：" + "；".join(lines),
                "resources": items,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_ksp_target",
        description=(
            "读取《坎巴拉太空计划》当前锁定的目标信息。"
            "用户问「我锁定的是什么」「离目标多远」「相对速度多少」时调用，"
            "对接和交会时最有用。返回目标名称、距离、相对速度。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_ksp_target(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取锁定目标。"""
        try:
            data = await self._get_json("/target")
        except SdkError as exc:
            return {
                "output": {"ok": False, "message": str(exc)},
                "is_error": False,
            }

        if not _as_bool(data.get("has_target")):
            return {
                "output": {
                    "ok": True,
                    "has_target": False,
                    "message": "当前没有锁定任何目标。",
                },
                "is_error": False,
            }

        kind = _as_text(data.get("kind"))
        name = _as_text(data.get("name"))
        dist = _fmt_dist(data.get("distance"))
        rel = _fmt(data.get("relative_speed"), " m/s")

        if kind == "vessel":
            text = f"目标飞船「{name}」距离 {dist}，相对速度 {rel}。"
        elif kind == "body":
            text = f"目标天体「{name}」距离 {dist}。"
        else:
            text = f"目标「{name}」距离 {dist}。"

        return {
            "output": {
                "ok": True,
                "has_target": True,
                "summary": text,
                "target": data,
            },
            "is_error": False,
        }

    @llm_tool(
        name="list_ksp_bodies",
        description=(
            "列出《坎巴拉太空计划》当前星系里的所有天体。"
            "用户问「有哪些星球」「Kerbin 多大」「哪个星球有大气」时调用。"
            "每个天体给出半径、表面重力、是否有大气、大气高度、引力球范围。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def list_ksp_bodies(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：列出天体。"""
        try:
            data = await self._get_json("/bodies")
        except SdkError as exc:
            return {
                "output": {"ok": False, "message": str(exc)},
                "is_error": False,
            }

        bodies = data.get("bodies") or []
        if not bodies:
            return {
                "output": {
                    "ok": True,
                    "count": 0,
                    "message": _as_text(data.get("note")) or "没有读到天体数据。",
                },
                "is_error": False,
            }

        lines = []
        for b in bodies:
            atm = (
                f"大气 {_fmt_dist(b.get('atmosphere_depth'))}"
                if _as_bool(b.get("has_atmosphere"))
                else "无大气"
            )
            lines.append(
                f"{_as_text(b.get('name'))}（半径 {_fmt_dist(b.get('radius'))}，"
                f"重力 {_fmt(b.get('gravity_asl'), ' g', 2)}，{atm}）"
            )

        return {
            "output": {
                "ok": True,
                "count": len(bodies),
                "summary": f"星系共 {len(bodies)} 个天体：" + "；".join(lines),
                "bodies": bodies,
            },
            "is_error": False,
        }

    @llm_tool(
        name="list_ksp_vessels",
        description=(
            "列出《坎巴拉太空计划》当前存档里的所有飞船及其状态。"
            "用户问「我有哪些飞船」「那个空间站在哪」时调用。"
            "给出每艘船的名称、飞行阶段、所在地体、高度和质量。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def list_ksp_vessels(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：列出飞船。"""
        try:
            data = await self._get_json("/vessels")
        except SdkError as exc:
            return {
                "output": {"ok": False, "message": str(exc)},
                "is_error": False,
            }

        vessels = data.get("vessels") or []
        if not vessels:
            return {
                "output": {
                    "ok": True,
                    "count": 0,
                    "message": _as_text(data.get("note")) or "当前没有飞船。",
                },
                "is_error": False,
            }

        lines = []
        for v in vessels:
            lines.append(
                f"{_as_text(v.get('name'))}（{_as_text(v.get('situation'))}，"
                f"在 {_as_text(v.get('body'))}，高度 {_fmt_dist(v.get('altitude'))}）"
            )

        return {
            "output": {
                "ok": True,
                "count": len(vessels),
                "summary": f"共 {len(vessels)} 艘飞船：" + "；".join(lines),
                "vessels": vessels,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_ksp_crew",
        description=(
            "读取《坎巴拉太空计划》当前飞船上的乘员名单。"
            "用户问「船上有谁」「我的小绿人在哪」时调用。"
            "给出每名乘员的名字、职业（驾驶员/工程师/科学家）和等级。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_ksp_crew(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取乘员。"""
        try:
            data = await self._get_json("/crew")
        except SdkError as exc:
            return {
                "output": {"ok": False, "message": str(exc)},
                "is_error": False,
            }

        crew = data.get("crew") or []
        if not crew:
            return {
                "output": {
                    "ok": True,
                    "count": 0,
                    "message": "这艘飞船上没有乘员（可能是无人探测器）。",
                },
                "is_error": False,
            }

        lines = []
        for m in crew:
            trait = _as_text(m.get("trait"))
            lvl = _num(m.get("experience"))
            lvl_text = f" {int(lvl)} 级" if lvl is not None else ""
            lines.append(f"{_as_text(m.get('name'))}（{trait}{lvl_text}）")

        return {
            "output": {
                "ok": True,
                "count": len(crew),
                "summary": f"船上共 {len(crew)} 名乘员：" + "；".join(lines),
                "crew": crew,
            },
            "is_error": False,
        }

    @llm_tool(
        name="read_ksp_docs",
        description=(
            "【必看】《坎巴拉太空计划》与本插件的说明。"
            "**如果你不了解这款游戏、不清楚它的术语（远点/近点/离心率/入轨判定），"
            "或者不知道该用哪个工具 —— 先调这个。**"
            "用户第一次找你玩这个游戏时，建议先读一遍。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=20,
    )
    async def read_ksp_docs(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读说明书。"""
        return {
            "output": {
                "ok": True,
                "docs": _KSP_DOC,
                "message": (
                    "以上是游戏与本插件的说明。"
                    "想了解玩家现在什么情况，可以调 get_ksp_status。"
                ),
            },
            "is_error": False,
        }


# ---------------------------------------------------------------------------
# 摘要生成（纯函数，便于测试）
# ---------------------------------------------------------------------------


def _summarize_state(state: Dict[str, Any]) -> str:
    """把 /state 的输出压成一句人话。"""
    scene = _as_text(state.get("scene"))
    vessel = _as_dict(state.get("vessel"))

    if not _as_bool(vessel.get("exists")):
        note = _as_text(vessel.get("note"))
        return f"{note}（场景：{scene}）" if note else f"当前没有可读的飞船（场景：{scene}）。"

    name = _as_text(vessel.get("name")) or "飞船"
    situation = _as_text(vessel.get("situation"))
    body = _as_text(vessel.get("body"))

    parts = [
        f"「{name}」当前在 {body}，状态 {situation}",
        f"海拔 {_fmt_dist(vessel.get('altitude'))}",
        f"地表速度 {_fmt(vessel.get('speed_surface'), ' m/s')}",
        f"轨道速度 {_fmt(vessel.get('speed_orbital'), ' m/s')}",
    ]

    pitch = _num(vessel.get("pitch"))
    if pitch is not None:
        parts.append(f"俯仰 {pitch:.0f}°")

    mass = _num(vessel.get("mass"))
    if mass is not None:
        parts.append(f"质量 {mass:,.2f} t")

    text = "，".join(parts) + "。"

    orbit = _as_dict(state.get("orbit"))
    if _as_bool(orbit.get("exists")):
        stable = _as_bool(orbit.get("is_stable_orbit"))
        text += " " + ("已进入稳定轨道。" if stable else "尚未进入稳定轨道。")
        text += (
            f"（远点 {_fmt_dist(orbit.get('apoapsis'))}，"
            f"近点 {_fmt_dist(orbit.get('periapsis'))}）"
        )
    return text


def _fuel_percent(state: Dict[str, Any]) -> Optional[float]:
    """从 /state 内嵌的 resources 里算「主要推进剂还剩百分之多少」。

    优先 LiquidFuel，退而求其次 SolidFuel / MonoPropellant。
    资源量变化快，但这是玩家最常问的问题之一，值得进自动播报。
    """
    resources = _as_dict(state.get("resources"))
    items = resources.get("resources")
    if not isinstance(items, list) or not items:
        return None
    wanted = ("LiquidFuel", "SolidFuel", "MonoPropellant")
    by_name: Dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        pct = _num(item.get("percent"))
        if name and pct is not None:
            by_name[name] = pct
    for name in wanted:
        if name in by_name:
            return by_name[name]
    return None


def _auto_brief_line(state: Dict[str, Any]) -> str:
    """把 /state 压成一行自动播报。

    没有飞船时返回空串，表示「这条不用发」—— 停机坪、编辑器、主菜单里
    反复播报「当前没有可读的飞船」只是浪费上下文。

    刻意压短：这一段会一直待在上下文里，跟着每一轮对话走。
    但凡玩家最常追问的几项（轨道根数、油门、燃料）能塞进来的都塞进来，
    这样「我的轨道怎么样」「油还够吗」就不必再走一次工具往返。
    """
    vessel = _as_dict(state.get("vessel"))
    if not _as_bool(vessel.get("exists")):
        return ""

    text = AUTO_BRIEF_PREFIX + _summarize_state(state)

    extra: List[str] = []
    throttle = _num(vessel.get("throttle"))
    if throttle is not None:
        extra.append(f"油门 {throttle * 100:.0f}%")

    orbit = _as_dict(state.get("orbit"))
    if _as_bool(orbit.get("exists")):
        ecc = _num(orbit.get("eccentricity"))
        inc = _num(orbit.get("inclination"))
        period = _fmt_time(orbit.get("period"))
        detail = []
        if ecc is not None:
            detail.append(f"离心率 {ecc:.4f}")
        if inc is not None:
            detail.append(f"倾角 {inc:.2f}°")
        if period and period != "-":
            detail.append(f"周期 {period}")
        if detail:
            extra.append("、".join(detail))

    fuel = _fuel_percent(state)
    if fuel is not None:
        extra.append(f"燃料 {fuel * 100:.0f}%")

    if extra:
        text += "（" + "，".join(extra) + "）"
    return text


def _summarize_orbit(orbit: Dict[str, Any]) -> str:
    """把 /orbit 的输出压成一句人话。"""
    if not _as_bool(orbit.get("exists")):
        return "当前没有轨道数据。"

    body = _as_text(orbit.get("body"))
    apo = _fmt_dist(orbit.get("apoapsis"))
    peri = _fmt_dist(orbit.get("periapsis"))
    ecc = _num(orbit.get("eccentricity"))
    inc = _num(orbit.get("inclination"))
    period = _fmt_time(orbit.get("period"))

    text = f"绕 {body} 的轨道：远点 {apo}，近点 {peri}"
    if ecc is not None:
        text += f"，离心率 {ecc:.4f}"
    if inc is not None:
        text += f"，倾角 {inc:.2f}°"
    text += f"，周期 {period}。"

    if _as_bool(orbit.get("is_stable_orbit")):
        text += " 近点高过大气层顶，是稳定轨道。"
    else:
        atm = _num(orbit.get("atmosphere_depth"))
        if atm is not None:
            text += f" 近点低于大气层顶（{_fmt_dist(atm)}），还会被大气拖下来。"
        else:
            text += " 近点低于地表，会撞上去。"

    t_apo = _fmt_time(orbit.get("time_to_apoapsis"))
    t_peri = _fmt_time(orbit.get("time_to_periapsis"))
    text += f" 距离远点还有 {t_apo}，距离近点还有 {t_peri}。"
    return text
