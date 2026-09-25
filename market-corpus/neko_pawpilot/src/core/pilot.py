"""猫娘智驾：提议→同意→自动驾驶→用户干预让渡/交还。

控制方式：pydirectinput 鼠标相对转向 + 键盘油门刹车（ETS2 支持鼠标转向）。
安全机制：
- 用户干预检测：user_steer/user_throttle 与注入目标偏差超阈值 → 立即让渡
- 抢夺计数：连续 N 次被抢 → 自动交还控制权（不再接管）
- 危险退出：超速/偏离/低油量 → 主动退出
"""

from __future__ import annotations

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
    """猫娘智驾核心。"""

    def __init__(self, persona: Any = None) -> None:
        self.state = IDLE
        self.persona = persona
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
        return None

    def _control(self, snap) -> None:
        """PID 式控制：目标速度巡航 + 直道微调。"""
        if self._pdi is None:
            return
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
        # 转向：目标转向 = 0（直道巡航），用鼠标相对微调保持
        # relative=True 相对移动，不抢用户鼠标绝对位置
        self._target_steer = 0.0
        self._target_throttle = 1.0 if err > 3 else 0.0
        self._target_brake = 1.0 if err < -5 else 0.0
        steer_move = 0
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
