"""
Open Rails Copilot Plugin
监控 Open Rails 火车模拟器的实时状态，并在需要时向 AI 报告
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None

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

from .core.arbiter import Arbiter
from .core.detector import Detector
from .core.push_sender import PushSender
from .core.safety_guard import SafetyGuard
from .core.snapshot import Snapshot, build_snapshot


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    val = dt or _utc_now()
    return val.astimezone(timezone.utc).isoformat()


@neko_plugin
class OpenRailsCopilotPlugin(NekoPluginBase):
    """Open Rails 火车模拟器副驾驶插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        
        # API 配置
        self.api_base_url = "http://localhost:2150"
        self.api_timeout = 3.0
        
        # 监控状态
        self.monitoring_enabled = False
        self.last_api_check = 0.0
        self.api_check_interval = 5.0  # 每5秒检查一次 API 可用性
        self.game_running = False
        
        # 历史数据用于异常检测
        self.last_speed = 0.0
        self.last_throttle = 0.0
        self.last_brake = 0.0
        self.last_status_report_time = 0.0
        self.status_report_interval = 60.0  # 每60秒报告一次状态
        
        # 异常检测阈值
        self.speed_change_threshold = 20.0  # 速度变化超过 20 km/h
        self.emergency_brake_threshold = 0.8  # 紧急制动阈值

        # ── 告警链路：detector → arbiter → push_sender ──────────────────────
        # 采样仍是 2 秒一次，但"能不能说出口"由 arbiter 判：场景门控 → 类别开关
        # → 冷却 → 抢占 → 全局限流（12 秒才放一条）。所以逐 tick 命中不会逐 tick
        # 推送。原实现靠"上升沿 + alert_cooldown"去重，实测 37 秒仍灌了 19 条；
        # 这套结构性地至多放一条出去。
        self.safety = SafetyGuard()
        self.arbiter = Arbiter(self.safety, logger=self.logger)
        self.detector = Detector(logger=self.logger)
        self.push_sender = PushSender(self, dry_run=False)
        self._prev_snapshot: Snapshot | None = None
        #: 角色名。留空 = 交给宿主选默认角色。
        self.target_lanlan = ""

        # HTTP 客户端
        self.client: httpx.AsyncClient | None = None

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """插件启动时初始化"""
        if httpx is None:
            self.logger.error("httpx 未安装，插件无法正常工作")
            return Err(SdkError("缺少依赖: httpx"))
        
        self.client = httpx.AsyncClient(timeout=self.api_timeout)
        self.logger.info("Open Rails Copilot 已启动")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件关闭时清理资源"""
        if self.client:
            await self.client.aclose()
        self.logger.info("Open Rails Copilot 已停止")
        return Ok({"status": "stopped"})

    async def _check_api_available(self) -> bool:
        """检查 Open Rails API 是否可用"""
        if not self.client:
            return False
        
        try:
            response = await self.client.get(f"{self.api_base_url}/API/CABCONTROLS")
            return response.status_code == 200
        except Exception as e:
            self.logger.debug(f"API 检查失败: {e}")
            return False

    async def _get_game_data(self) -> dict[str, Any] | None:
        """获取游戏实时数据"""
        if not self.client:
            return None
        
        try:
            # 获取驾驶舱控制数据
            cab_response = await self.client.get(f"{self.api_base_url}/API/CABCONTROLS")
            time_response = await self.client.get(f"{self.api_base_url}/API/TIME")
            
            if cab_response.status_code == 200 and time_response.status_code == 200:
                cab_data = cab_response.json()
                game_time = float(time_response.text)
                
                # 解析驾驶舱数据
                parsed_data = {"GameTime": game_time}
                skipped: list[str] = []
                for control in cab_data:
                    type_name = control.get("TypeName", "")
                    if not type_name:
                        continue
                    min_value = float(control.get("MinValue", 0.0))
                    max_value = float(control.get("MaxValue", 0.0))

                    # /API/CABCONTROLS 用 (TypeName, ControlIndex) 作为键，但
                    # 返回的结构体里没有 ControlIndex，所以同一个 TypeName 会
                    # 合法地重复出现（例如 0-300 和 0-160 两个速度表，多机重联
                    # 时整块控制表还会再来一遍）。其中还有 range 为 0 的空占位。
                    # OR 自己的 CabControls 网页把 range==0 渲染成 "-"（无值），
                    # 而按 TypeName 直接赋值会让空占位覆盖真实仪表——曾导致列车
                    # 行驶中速度被读成 0.0。这里跳过空占位，并保留第一个真实仪表。
                    if max_value == min_value or type_name in parsed_data:
                        skipped.append(type_name)
                        continue

                    range_fraction = float(control.get("RangeFraction", 0.0))
                    actual_value = min_value + (max_value - min_value) * range_fraction
                    parsed_data[type_name] = {
                        "value": actual_value,
                        "fraction": range_fraction,
                        "min": min_value,
                        "max": max_value
                    }
                if skipped:
                    self.logger.debug(
                        f"跳过 {len(skipped)} 个重复/无量程控制项: {sorted(set(skipped))}"
                    )

                return parsed_data
        except Exception as e:
            self.logger.debug(f"获取游戏数据失败: {e}")
        
        return None

    def _analyze_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """分析游戏数据，检测异常情况"""
        alerts = []
        
        # 提取关键数据
        speedometer = data.get("SPEEDOMETER", {})
        speed_kmh = speedometer.get("value", 0.0)  # 直接是 km/h
        
        throttle_data = data.get("THROTTLE", {})
        throttle = throttle_data.get("fraction", 0.0)  # 0-1
        
        brake_data = data.get("TRAIN_BRAKE", {})
        brake = brake_data.get("fraction", 0.0)  # 0-1
        
        speed_limit_data = data.get("SPEEDLIM_DISPLAY", {})
        speed_limit = speed_limit_data.get("value", 0.0)  # km/h
        
        # 检测急刹车
        if brake > self.emergency_brake_threshold:
            alerts.append({
                "type": "emergency_brake",
                "severity": "high",
                "message": f"检测到紧急制动！制动力：{brake*100:.1f}%",
            })
        
        # 检测速度剧变
        if self.last_speed > 0:
            speed_change = abs(speed_kmh - self.last_speed)
            if speed_change > self.speed_change_threshold:
                alerts.append({
                    "type": "speed_change",
                    "severity": "medium",
                    "message": f"速度急剧变化：从 {self.last_speed:.1f} km/h 到 {speed_kmh:.1f} km/h",
                })
        
        # 检测超速（使用游戏中的实际限速）
        if speed_limit > 0 and speed_kmh > speed_limit:
            alerts.append({
                "type": "overspeed",
                "severity": "medium",
                "message": f"当前速度 {speed_kmh:.1f} km/h 超过限速 {speed_limit:.1f} km/h",
            })
        
        # 更新历史数据
        self.last_speed = speed_kmh
        self.last_throttle = throttle
        self.last_brake = brake
        
        return {
            "speed_kmh": round(speed_kmh, 1),
            "speed_limit_kmh": round(speed_limit, 1),
            "throttle_percent": round(throttle * 100, 1),
            "brake_percent": round(brake * 100, 1),
            "alerts": alerts,
            "timestamp": _iso(),
        }

    # ── 快照采集（告警链路的数据面）──────────────────────────────────────
    async def _get_json(self, path: str) -> Any:
        if not self.client:
            raise RuntimeError("httpx client not initialised")
        response = await self.client.get(f"{self.api_base_url}{path}")
        response.raise_for_status()
        return response.json()

    async def _fetch_snapshot(self) -> Snapshot | None:
        """取四个数据面合成一份 Snapshot。

        单端点失败按"该面没有数据"处理（``build_snapshot`` 会把缺失字段留成
        None，detector 对 None 一律不下判定）。只有 Track Monitor 与机控器
        **同时**取不到才整体放弃——那意味着 OR 没在跑，而不是某个窗口没开。

        ``/API/HUD/1`` 与 ``/API/TRACKMONITORDISPLAY`` 在部分 OR 版本/设置下不
        一定存在，所以不能因为它们 404 就整个静默。
        """
        async def surface(path: str) -> Any:
            try:
                return await self._get_json(path)
            except Exception as exc:
                self.logger.debug("数据面 %s 不可用: %s", path, exc)
                return None

        tm = await surface("/API/TRACKMONITORDISPLAY")
        cab = await surface("/API/CABCONTROLS")
        if tm is None and cab is None:
            return None
        hud0 = await surface("/API/HUD/0")
        hud1 = await surface("/API/HUD/1")

        game_time: float | None = None
        try:
            if self.client:
                response = await self.client.get(f"{self.api_base_url}/API/TIME")
                response.raise_for_status()
                game_time = float(response.text)
        except Exception as exc:
            self.logger.debug("游戏时间不可用: %s", exc)

        return build_snapshot(tm, cab, hud0, hud1, game_time_s=game_time)

    @staticmethod
    def _status_line(snap: Snapshot) -> str:
        """心跳文案：只报确实读到的字段，读不到就不提。"""
        bits: list[str] = []
        if snap.speed_kmh is not None:
            bits.append(f"速度 {snap.speed_kmh:.0f} km/h")
        if snap.limit_kmh is not None:
            bits.append(f"限速 {snap.limit_kmh:.0f} km/h")
        if snap.signal_aspect:
            bits.append(f"信号 {snap.signal_aspect}")
        if snap.control_mode:
            bits.append(f"模式 {snap.control_mode}")
        return "[Open Rails 状态] 列车运行正常。" + "，".join(bits)

    @staticmethod
    def _snapshot_summary(snap: Snapshot) -> dict[str, Any]:
        return {
            "speed_kmh": snap.speed_kmh,
            "limit_kmh": snap.limit_kmh,
            "track_color": snap.track_color,
            "signal_aspect": snap.signal_aspect,
            "signal_distance_m": snap.signal_distance_m,
            "control_mode": snap.control_mode,
            "authority": snap.authority,
            "gradient_pct": snap.gradient_pct,
            "cab_orientation": snap.cab_orientation,
            "out_of_control": snap.out_of_control,
        }

    @timer_interval(
        id="monitor_openrails",
        seconds=2,  # 每2秒监控一次
        name="监控 Open Rails 状态",
        auto_start=True,
    )
    async def monitor_game(self, **_):
        """定时监控：采集 → 判定 → 仲裁 → 推送。"""
        if not self.monitoring_enabled:
            return Ok({"status": "monitoring_disabled"})

        now = time.time()
        # 定期检查 API 可用性：OR 关掉后不必每 2 秒去敲四个端点
        if now - self.last_api_check > self.api_check_interval:
            self.game_running = await self._check_api_available()
            self.last_api_check = now
            if not self.game_running:
                return Ok({"status": "game_not_running"})
        if not self.game_running:
            return Ok({"status": "game_not_running"})

        snapshot = await self._fetch_snapshot()
        if snapshot is None:
            return Ok({"status": "no_data"})

        # 作业场景决定哪些类别整体静默：停站保压时不谈限速/超速，调车时不套干线
        # 限速规则。这比逐条调阈值可靠——停站时制动缸本来就在高位、速度本来就是 0，
        # 照常跑规则会让"制动未缓解""超速"持续误报。
        self.arbiter.update_scenario(self.detector.classify_scenario(snapshot))
        self.arbiter.begin_cycle()

        spoken: list[str] = []
        for candidate in self.detector.detect(snapshot, self._prev_snapshot):
            allowed, _reason = self.arbiter.decide(candidate.event_id, now=now)
            if not allowed:
                continue
            if await self.push_sender.send_alert(
                candidate.spec, candidate.message, target_lanlan=self.target_lanlan
            ):
                spoken.append(candidate.event_id)
            else:
                # 通道失败本身是信号，不是噪声：窗口内连续失败会触发断路器急停
                self.safety.record_failure(now)

        self._prev_snapshot = snapshot

        # 心跳：本轮没说话才报"运行正常"（边说异常边说正常会自相矛盾）。
        # read + 固定 coalesce_key ⇒ 被动队列里永远只留最新的一张快照。
        if not spoken and now - self.last_status_report_time > self.status_report_interval:
            await self.push_sender.send_status(
                self._status_line(snapshot), target_lanlan=self.target_lanlan
            )
            self.last_status_report_time = now

        return Ok({
            "status": "monitoring",
            "scenario": self.arbiter.scenario,
            "spoken": spoken,
            "safety": self.safety.snapshot(),
            "snapshot": self._snapshot_summary(snapshot),
        })

    @plugin_entry(
        id="start_monitoring",
        name="开始监控",
        description="开始监控 Open Rails 游戏状态",
    )
    async def start_monitoring(self, **_):
        """开始监控游戏"""
        if self.monitoring_enabled:
            return Ok({"message": "监控已在运行中"})
        
        # 检查 API 是否可用
        available = await self._check_api_available()
        if not available:
            return Err(SdkError(
                "Open Rails 未运行或 API 不可用。"
                "请确保游戏已启动且 REST API 已启用（localhost:2150）"
            ))
        
        self.monitoring_enabled = True
        self.game_running = True
        self.last_status_report_time = time.time()

        # 复位告警链路。这也是断路器跳闸（通道连续失败）之后唯一的重启手段——
        # 没有这一步，一次传输故障会让插件静默到进程重启。
        self.safety.resume()
        self.arbiter.reset()
        self.detector.reset()
        self._prev_snapshot = None
        
        self.push_message(
            visibility=["hud"],
            ai_behavior="blind",
            parts=[{
                "type": "text",
                "text": "Open Rails 监控已启动",
            }],
        )
        
        return Ok({
            "message": "监控已启动",
            "api_url": self.api_base_url,
        })

    @plugin_entry(
        id="stop_monitoring",
        name="停止监控",
        description="停止监控 Open Rails 游戏状态",
    )
    async def stop_monitoring(self, **_):
        """停止监控游戏"""
        if not self.monitoring_enabled:
            return Ok({"message": "监控未在运行"})
        
        self.monitoring_enabled = False
        self.game_running = False
        
        self.push_message(
            visibility=["hud"],
            ai_behavior="blind",
            parts=[{
                "type": "text",
                "text": "Open Rails 监控已停止",
            }],
        )
        
        return Ok({"message": "监控已停止"})

    @plugin_entry(
        id="test_push",
        name="测试推送",
        description=(
            "发送一条受控 push_message，用于验证投递路由，开发告警时很有用："
            "ai_behavior=read 静默进上下文且不打断（下一次用户轮次才被带走）；"
            "respond 请求角色主动开口（走节流管理器，占每会话 2 次主动额度）；"
            "blind 只给用户看、不进 LLM。coalesce_key 留空则永不合并、只能靠队列上限兜底。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": "Open Rails 测试推送"},
                "ai_behavior": {"type": "string", "enum": ["read", "respond", "blind"]},
                "visibility": {"type": "string", "enum": ["none", "chat", "hud"]},
                "coalesce_key": {"type": "string"},
                "priority": {"type": "integer"},
            },
            "required": [],
            "additionalProperties": False,
        },
    )
    async def test_push(
        self,
        text: str = "Open Rails 测试推送",
        ai_behavior: str = "read",
        visibility: str = "none",
        coalesce_key: str = "",
        priority: int = 0,
        **_,
    ):
        """发送一条受控推送，便于在 host 日志里观测投递路由。"""
        vis = [] if visibility == "none" else [visibility]
        kwargs: dict[str, Any] = {
            "visibility": vis,
            "ai_behavior": ai_behavior,
            "parts": [{"type": "text", "text": text}],
            "priority": priority,
        }
        if coalesce_key:
            kwargs["coalesce_key"] = coalesce_key
        receipt = self.push_message(**kwargs)
        self.logger.info(f"test_push: ai_behavior={ai_behavior} visibility={vis} coalesce_key={coalesce_key or '(none)'}")
        return Ok({
            "text": text,
            "ai_behavior": ai_behavior,
            "visibility": vis,
            "coalesce_key": coalesce_key or "(none -> never coalesces)",
            "priority": priority,
            "receipt": receipt,
        })

    @plugin_entry(
        id="get_status",
        name="获取当前状态",
        description="获取 Open Rails 当前的游戏状态",
    )
    async def get_status(self, **_):
        """获取当前游戏状态"""
        if not self.monitoring_enabled:
            return Err(SdkError("监控未启动，请先调用 start_monitoring"))
        
        data = await self._get_game_data()
        if not data:
            return Err(SdkError("无法获取游戏数据，游戏可能未运行"))
        
        analysis = self._analyze_data(data)
        
        return Ok({
            "status": "running",
            "data": analysis,
            "raw_data": data,
        })

    @plugin_entry(
        id="check_api",
        name="检查 API 连接",
        description="检查 Open Rails REST API 是否可用",
    )
    async def check_api(self, **_):
        """检查 API 连接"""
        available = await self._check_api_available()
        
        if available:
            return Ok({
                "available": True,
                "message": f"API 可用：{self.api_base_url}",
            })
        else:
            return Ok({
                "available": False,
                "message": f"API 不可用：{self.api_base_url}。请确保 Open Rails 已启动",
            })
