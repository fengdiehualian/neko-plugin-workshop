"""猫娘智驾：提议→同意→自动驾驶→用户干预让渡/交还。

控制方式：pydirectinput 鼠标相对转向 + 键盘油门刹车（ETS2 支持鼠标转向）。

v2（2026-10 接力驾驶）：转向不再恒为 0（旧版只能直道定速），改为
**基于地图道路的前视点跟踪**：
- 车头方向**自标定**：用两次遥测位置差分估车头朝向，不依赖 rotationY 的
  轴向/正负约定（那个约定没实测标定过，猜错会打反方向）
- 前视点：取附近路网点在车头坐标系里的前后/左右分量，挑「前视距离≈lookahead、
  且横向偏差小」的点作为目标，横偏角 → 转向指令
- **转向符号自校准**：若连续若干拍指令让横向误差变大，就翻转符号并记住
- 安全兜底：横向误差发散 / 找不到路点 / 进入市区 / 超速 / 低油 → 立即交还

安全机制：
- 用户干预检测：user_steer/user_throttle 与注入目标偏差超阈值 → 立即让渡
- 抢夺计数：连续 N 次被抢 → 自动交还控制权（不再接管）
- 危险退出：超速/低油量/偏离车道/市区 → 主动退出
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

# 状态机
IDLE = "idle"
OFFER = "offer"          # 猫娘提议，等用户回复
ENGAGED = "engaged"      # 自动驾驶中
HANDING_OVER = "handing_over"  # 检测到干预，让渡中（短暂）

# 阈值（可配置）
STEER_DEADZONE = 0.15     # 转向偏差死区：|user_steer - target| 超过即算干预
THROTTLE_DEADZONE = 0.25  # 油门偏差死区
SNATCH_LIMIT = 3          # 连续被抢 N 次 → 交还
OFFER_TIMEOUT = 30.0      # 提议等待超时（秒）


class CatPilot:
    """猫娘智驾核心（v2：可沿道路行驶）。"""

    def __init__(self, persona: Any = None, map_kb: Any = None, cfg: Any = None) -> None:
        self.state = IDLE
        self.persona = persona
        self.map_kb = map_kb
        self.cfg = cfg
        self._snatches = 0
        self._offer_until = 0.0
        self._target_steer = 0.0
        self._target_throttle = 0.0
        self._target_brake = 0.0
        self._target_speed_kmh = 0.0
        self._last_tick = 0.0
        self._settle_ticks = 0
        self._handover_until = 0.0
        self._pdi = None
        self._import_input()
        self._kbd_down: set = set()
        # ── v2 前视点跟踪状态 ──
        self._fwd: Optional[tuple] = None      # 自标定车头方向 (fx, fz)
        self._last_pos: Optional[tuple] = None
        self._steer_sign = 1.0                 # 转向符号（自校准）
        self._sign_score = 0.0
        self._sign_samples = 0
        self._last_lateral: Optional[float] = None
        self._lateral_err_m = 0.0
        self._steer_cmd = 0.0
        self._no_road_ticks = 0

    def _import_input(self) -> None:
        """延迟导入 pydirectinput（vendor 路径）。"""
        try:
            import sys
            from pathlib import Path
            vendor = Path(__file__).resolve().parent.parent / "adapters" / "vendor"
            if str(vendor) not in sys.path:
                sys.path.insert(0, str(vendor))
            import pydirectinput
            # 智驾场景自己管理安全边界：关闭 (0,0) failsafe，避免绝对移动误触发
            pydirectinput.FAILSAFE = False
            self._pdi = pydirectinput
        except Exception:
            self._pdi = None

    # ── 状态查询 ──
    @property
    def available(self) -> bool:
        return self._pdi is not None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "available": self.available,
            "snatches": self._snatches,
            "snatch_limit": SNATCH_LIMIT,
            # v2 前视点跟踪诊断（面板可见，也用于实测标定方向符号）
            "heading_known": self._fwd is not None,
            "steer_cmd": round(self._steer_cmd, 3),
            "steer_sign": self._steer_sign,
            "lateral_m": self.lateral_error(),
            "no_road": self._no_road_ticks,
        }

    # ── 状态迁移 ──
    def offer(self) -> str:
        """猫娘提议接管（用户说累 / 猫娘主动）。"""
        if not self.available:
            return "（智驾不可用：输入注入组件缺失）"
        if self.state == ENGAGED:
            return "已经在开啦喵，你歇着就好~"
        self.state = OFFER
        self._offer_until = time.time() + OFFER_TIMEOUT
        return "我开了一会儿啦，要歇歇吗？我可以帮你开一段喵！"

    def accept(self, snap=None) -> str:
        """用户同意 → 进入自动驾驶（抢夺计数不清零：同一会话内累计）。"""
        if self.state != OFFER and self.state != IDLE:
            return "当前不在可接管状态喵"
        self.state = ENGAGED
        self._last_tick = time.time()
        # 接管沉降期：前 5 拍跳过干预判定（用户手刚离开方向盘/油门）
        self._settle_ticks = 5
        if snap is not None:
            # 目标对齐当前输入，防首个 tick 误判抢控
            self._target_steer = getattr(snap, "user_steer", 0.0)
            self._target_throttle = getattr(snap, "user_throttle", 0.0)
            self._target_brake = getattr(snap, "user_brake", 0.0)
        return "好嘞，交给我喵！你闭眼眯会儿，安全第一~"

    def decline(self) -> str:
        """用户拒绝 → 回 IDLE。"""
        self.state = IDLE
        self._release_input()
        return "好~ 那你自己开，我陪着你说说话喵"

    def release(self, reason: str = "user") -> str:
        """用户收回 / 主动交还 → 释放控制。"""
        self.state = IDLE
        self._release_input()
        if reason == "snatched":
            self._snatches += 1
            if self._snatches >= SNATCH_LIMIT:
                self._snatches = 0
                return "好啦好啦，方向盘还你喵！以后想让我开再说一声~"
            return "咦？你要开吗？那我让给你喵！"
        return "好，换你来喵！握紧方向盘哦~"

    def reset_session(self) -> None:
        """会话结束/新会话：清空抢夺计数。"""
        self._snatches = 0

    def auto_exit(self, reason: str) -> str:
        """危险/到达 → 自动退出。"""
        self.state = IDLE
        self._release_input()
        msgs = {
            "overspeed": "超速了喵！我先减速让给你，安全第一！",
            "low_fuel": "油量太低了喵，我开到服务区就交给你~",
            "arrived": "到啦！我停好车了，这次开得不错吧喵？",
            "error": "呜… 自动驾驶出问题了喵，先还给你！",
            "off_lane": "这条线我压不住了喵！方向盘还你，你来稳一下~",
            "no_road": "前面地图数据断了喵，我看不清路，交还给你！",
            "city": "前面要进市区了喵，复杂路段我不熟，你来开我帮你看路~",
        }
        return msgs.get(reason, f"自动驾驶退出喵（{reason}）")

    # ── 驾驶循环 ──
    def tick(self, snap) -> Optional[str]:
        """每 tick 调用；返回要播报的话（如无返回 None）。"""
        if self.state != ENGAGED:
            return None
        now = time.time()
        if now - self._last_tick < 0.2:  # 5Hz 控制频率
            return None
        self._last_tick = now

        # 1) 危险检测（始终执行，包括沉降期）
        danger = self._danger_check(snap)
        if danger:
            return self.auto_exit(danger)

        # 0) 接管沉降期：前 5 拍跳过干预判定，让手离开方向盘/油门
        if self._settle_ticks > 0:
            self._settle_ticks -= 1
            self._control(snap)
            return None

        # 2) 用户干预检测
        if self._user_intervening(snap):
            return self.release(reason="snatched")

        # 3) 控制输出
        self._control(snap)
        return None

    def tick_dry(self, snap) -> Optional[str]:
        """dry_run 模式：只跑状态机与播报，绝不注入输入。"""
        if self.state != ENGAGED:
            return None
        now = time.time()
        if now - self._last_tick < 0.2:
            return None
        self._last_tick = now
        danger = self._danger_check(snap)
        if danger:
            return self.auto_exit(danger)
        # dry_run 不注入，仅维持状态
        return None

    def _user_intervening(self, snap) -> bool:
        """用户是否在抢控制权：注入目标 vs 遥测 user_* 偏差。"""
        steer_delta = abs(snap.user_steer - self._target_steer)
        throttle_delta = abs(snap.user_throttle - self._target_throttle)
        brake_delta = abs(snap.user_brake - self._target_brake)
        return (steer_delta > STEER_DEADZONE
                or throttle_delta > THROTTLE_DEADZONE
                or brake_delta > THROTTLE_DEADZONE)

    def _danger_check(self, snap) -> Optional[str]:
        if snap.speed_kmh > snap.speed_limit_kmh + 10:
            return "overspeed"
        if snap.fuel_percent < 8:
            return "low_fuel"
        # 车道偏离发散：横向误差已超安全值 → 立刻交还（不能盲目继续开）
        if self._lateral_err_m > float(getattr(self.cfg, "pilot_max_lateral_m", 8.0) or 8.0):
            return "off_lane"
        # 连续多拍找不到路点（地图无数据/偏离路网）→ 交还
        if self._no_road_ticks >= 15:
            return "no_road"
        # 进入市区/复杂路段 → 交还（她只适合开阔路段）
        if getattr(snap, "is_city", False):
            return "city"
        return None

    # ── v2：车头方向自标定 + 前视点跟踪 ──
    def _update_heading(self, snap) -> None:
        """用位置差分估计车头方向（不依赖 rotationY 的轴向约定）。"""
        pos = (getattr(snap, "world_x", 0.0), getattr(snap, "world_z", 0.0))
        prev = self._last_pos
        self._last_pos = pos
        if prev is None:
            return
        if getattr(snap, "speed_kmh", 0.0) < 8.0:
            return
        dx, dz = pos[0] - prev[0], pos[1] - prev[1]
        norm = (dx * dx + dz * dz) ** 0.5
        if norm < 0.05:      # 位移太小，方向不可信
            return
        fx, fz = dx / norm, dz / norm
        if self._fwd is None:
            self._fwd = (fx, fz)
            return
        a = 0.3              # 低通，抑制抖动
        nfx = self._fwd[0] * (1 - a) + fx * a
        nfz = self._fwd[1] * (1 - a) + fz * a
        n = (nfx * nfx + nfz * nfz) ** 0.5 or 1.0
        self._fwd = (nfx / n, nfz / n)

    def lateral_error(self) -> float:
        """最近一次估算的横向偏差（米）——面板可见，用于判断她开得稳不稳。"""
        return round(self._lateral_err_m, 2)

    def _pick_target(self, snap, x: float, z: float):
        """在车头坐标系里挑前视目标点，返回 (前向距离 f, 横向偏差 l) 或 None。

        注意：地图是**无序路点云**，路段长度不一（实测量到 94 米的长段），
        所以查询半径要留足（否则长段中部取不到任何点 → 无法转向）。
        代价函数仍优先"离 lookahead 最近"的点，只有长段才会退化为远点。
        """
        if self.map_kb is None or self._fwd is None:
            return None
        look = float(getattr(self.cfg, "pilot_lookahead_m", 45.0) or 45.0)
        radius_km = max(0.25, look * 3.0 / 1000.0)
        f_max = max(look * 4.0, 120.0)
        try:
            pts = self.map_kb.road_points_near(x, z, radius_km=radius_km)
        except Exception:
            pts = []
        if not pts:
            return None
        fx, fz = self._fwd
        lx, lz = -fz, fx          # 车头左侧法向（左右含义未标定，靠符号自校准纠正）
        best = None
        best_cost = 1e18
        for (px, pz) in pts:
            dx, dz = px - x, pz - z
            f = dx * fx + dz * fz
            if f <= 2.0 or f > f_max:
                continue
            lat = dx * lx + dz * lz
            cost = abs(f - look) + 3.0 * abs(lat)
            if cost < best_cost:
                best_cost, best = cost, (f, lat)
        return best

    def _steer_command(self, snap) -> float:
        """前视点跟踪 → 转向指令（-1..1）。无可用路点时返回 0 并计数。"""
        target = self._pick_target(snap, getattr(snap, "world_x", 0.0),
                                   getattr(snap, "world_z", 0.0))
        if target is None:
            self._no_road_ticks += 1
            self._steer_cmd = 0.0
            return 0.0
        self._no_road_ticks = 0
        f_dist, lat = target
        self._lateral_err_m = abs(lat)
        err = math.atan2(lat, f_dist)   # 目标点相对车头的偏角
        gain = float(getattr(self.cfg, "pilot_steer_gain", 1.6) or 1.6)
        cmd = max(-1.0, min(1.0, err * gain)) * self._steer_sign
        # 符号自校准：若"打方向后横向误差反而变大"持续出现 → 翻转符号
        if self._last_lateral is not None and abs(cmd) > 0.05:
            grew = abs(lat) - self._last_lateral
            self._sign_score += (1.0 if grew > 0 else -1.0) * (1.0 if cmd > 0 else -1.0)
            self._sign_samples += 1
            if self._sign_samples >= 25:
                if self._sign_score > 8:      # 长期"越打越偏" → 方向反了
                    self._steer_sign *= -1.0
                self._sign_score = 0.0
                self._sign_samples = 0
        self._last_lateral = abs(lat)
        self._steer_cmd = cmd
        return cmd

    def _control(self, snap) -> None:
        """巡航 + 前视点跟踪转向。"""
        if self._pdi is None:
            return
        self._update_heading(snap)
        # 目标速度 = 限速（或巡航设定）
        target = snap.speed_limit_kmh if snap.speed_limit_kmh > 10 else 80.0
        err = target - snap.speed_kmh
        # 油门/刹车（比例控制）
        if err > 3:
            self._set_throttle(1.0)
            self._set_brake(0.0)
        elif err < -5:
            self._set_throttle(0.0)
            self._set_brake(1.0)
        else:
            self._set_throttle(0.0)
            self._set_brake(0.0)
        self._target_throttle = 1.0 if err > 3 else 0.0
        self._target_brake = 1.0 if err < -5 else 0.0
        # 转向：前视点跟踪（相对鼠标移动，不抢用户绝对位置）
        cmd = self._steer_command(snap)
        step = float(getattr(self.cfg, "pilot_steer_step", 6.0) or 6.0)
        steer_move = int(max(-step, min(step, cmd * step)))
        if abs(steer_move) < 1 and abs(cmd) > 0.02:
            steer_move = 1 if cmd > 0 else -1
        self._target_steer = cmd
        try:
            self._pdi.move(steer_move, 0, relative=True)
        except Exception:
            pass

    def _set_throttle(self, value: float) -> None:
        if self._pdi is None:
            return
        if value > 0.5:
            if "up" not in self._kbd_down:
                self._pdi.keyDown("up")
                self._kbd_down.add("up")
        else:
            if "up" in self._kbd_down:
                self._pdi.keyUp("up")
                self._kbd_down.discard("up")

    def _set_brake(self, value: float) -> None:
        if self._pdi is None:
            return
        if value > 0.5:
            if "down" not in self._kbd_down:
                self._pdi.keyDown("down")
                self._kbd_down.add("down")
        else:
            if "down" in self._kbd_down:
                self._pdi.keyUp("down")
                self._kbd_down.discard("down")

    def _release_input(self) -> None:
        """释放所有注入按键。"""
        if self._pdi is None:
            return
        for k in list(self._kbd_down):
            try:
                self._pdi.keyUp(k)
            except Exception:
                pass
        self._kbd_down.clear()
        self._target_steer = 0.0
        self._target_throttle = 0.0
        self._target_brake = 0.0

    def __del__(self):
        try:
            self._release_input()
        except Exception:
            pass
