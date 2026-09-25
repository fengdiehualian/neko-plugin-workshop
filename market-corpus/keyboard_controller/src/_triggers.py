# -*- coding: utf-8 -*-
"""条件触发器：检测画面变化→自动执行操作。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from ._screen_advanced import find_color


def _parse_region(region: Any) -> tuple[int, int, int, int] | None:
    """把工具层传来的 region 解析成 (x, y, w, h)。

    工具描述统一 region 为 'x,y,w,h' 字符串，但这里拿到的是未解析的
    原始参数 —— 字符串直接进 find_color/frame_diff 会在解包时抛
    TypeError，然后被 except 吞掉，触发器静默永不命中。
    """
    if region is None:
        return None
    if isinstance(region, str):
        parts = [p.strip() for p in region.split(",")]
        if len(parts) != 4:
            return None
        try:
            region = [int(p) for p in parts]
        except ValueError:
            return None
    if isinstance(region, (list, tuple)) and len(region) == 4:
        try:
            return tuple(int(v) for v in region)
        except (TypeError, ValueError):
            return None
    return None


# change 触发器签名尺寸：64x64=4096 个灰度采样点（~4KB），替代常驻
# 整帧 PIL Image（1080p 下数 MB~数十 MB，多触发器线性叠加）。
_SIGNATURE_SIZE = 64


def _frame_signature(frame) -> list[int]:
    """帧的降采样灰度签名。"""
    img = frame.convert("L").resize((_SIGNATURE_SIZE, _SIGNATURE_SIZE))
    return list(img.getdata())


def _diff_signature(
    prev_sig: list[int],
    frame,
    threshold: int = 16,
    min_area: int = 64,
) -> dict[str, Any]:
    """对比上一帧签名与当前帧，返回与 frame_diff 兼容的 {count} 结果。

    count 按采样比例换算回"等效变化像素数"，与 min_area 的全分辨率
    语义大致可比（触发器只需要"变化够不够大"的判断）。
    """
    cur = _frame_signature(frame)
    if len(prev_sig) != len(cur):
        return {"count": 0, "note": "signature size mismatch"}
    changed = sum(1 for a, b in zip(prev_sig, cur) if abs(a - b) >= threshold)
    width, height = frame.size
    scale = max(1, (width * height) // len(cur))
    equivalent = changed * scale
    return {
        "count": equivalent if equivalent >= min_area else 0,
        "changed_samples": changed,
        "equivalent_pixels": equivalent,
    }


class Trigger:
    """一个触发器定义。"""

    def __init__(
        self,
        trigger_id: str,
        condition: str,
        params: dict[str, Any],
        action: str,
        action_params: dict[str, Any],
        cooldown_s: float = 0,
        max_fires: int = 0,
    ):
        self.id = trigger_id
        self.condition = condition        # "color", "change"（"text"/"window" 未实现，见下）
        self.params = params
        self.action = action              # "press", "click", "notify", "run_command", "sequence"
        self.action_params = action_params
        self.cooldown_s = cooldown_s
        self.max_fires = max_fires
        self._fire_count = 0
        self._last_fire = 0.0
        self._enabled = True

    def can_fire(self) -> bool:
        if not self._enabled:
            return False
        if self.max_fires > 0 and self._fire_count >= self.max_fires:
            return False
        if self.cooldown_s > 0 and (time.time() - self._last_fire) < self.cooldown_s:
            return False
        return True

    def mark_fired(self):
        self._fire_count += 1
        self._last_fire = time.time()

    def to_dict(self) -> dict[str, Any]:
        # 过滤内部状态键（如 _prev_frame 存的是 PIL Image，不可 JSON 序列化）
        safe_params = {k: v for k, v in self.params.items() if not str(k).startswith("_")}
        return {
            "id": self.id,
            "condition": self.condition,
            "params": safe_params,
            "action": self.action,
            "action_params": self.action_params,
            "cooldown_s": self.cooldown_s,
            "max_fires": self.max_fires,
            "fire_count": self._fire_count,
            "enabled": self._enabled,
            "last_fire": self._last_fire,
        }


class TriggerEngine:
    """触发器引擎：管理触发器列表，每轮 tick 检查条件并执行动作。"""

    def __init__(self, logger=None):
        self._triggers: dict[str, Trigger] = {}
        self._logger = logger
        self._action_handler: Optional[Callable] = None  # async (action, params) -> Any

    @property
    def count(self) -> int:
        return len(self._triggers)

    def set_action_handler(self, handler: Callable):
        """设置动作执行器：async handler(action, params) -> result。"""
        self._action_handler = handler

    def add(self, t: Trigger) -> dict[str, Any]:
        self._triggers[t.id] = t
        return {"ok": True, "id": t.id, "total": len(self._triggers)}

    def remove(self, trigger_id: str) -> dict[str, Any]:
        if trigger_id in self._triggers:
            del self._triggers[trigger_id]
            return {"ok": True, "id": trigger_id, "total": len(self._triggers)}
        return {"ok": False, "error": f"触发器不存在：{trigger_id}"}

    def enable(self, trigger_id: str, enabled: bool = True) -> dict[str, Any]:
        if trigger_id in self._triggers:
            self._triggers[trigger_id]._enabled = enabled
            return {"ok": True, "id": trigger_id, "enabled": enabled}
        return {"ok": False, "error": f"触发器不存在：{trigger_id}"}

    def list_all(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._triggers.values()]

    def clear(self):
        self._triggers.clear()

    async def tick(self, frame) -> list[dict[str, Any]]:
        """执行一轮检查。

        frame: 当前帧 (PIL Image) 或 None。由调用方异步抓取后传入，
        避免在事件循环内同步阻塞截图。
        """
        fired: list[dict[str, Any]] = []

        for t in list(self._triggers.values()):
            if not t.can_fire():
                continue

            matched = await self._check_condition(t, frame)
            if matched:
                t.mark_fired()
                result = await self._execute_action(t)
                fired.append({
                    "trigger_id": t.id,
                    "condition": t.condition,
                    "action": t.action,
                    "result": result,
                    "fire_count": t._fire_count,
                })
        return fired

    async def _check_condition(
        self, t: Trigger, frame=None
    ) -> bool:
        """检查单个触发器条件。

        像素级运算（find_color / frame_diff）是纯 Python 双重循环，
        1080p 帧最坏可跑秒级 —— 必须丢进 to_thread，否则每个
        change/color 触发器每轮 tick 都会把整个插件宿主的事件循环
        卡住（同进程所有插件的 IPC 心跳一起遭殃）。
        """
        try:
            cond = t.condition
            if cond == "color":
                if frame is None:
                    return False
                target = t.params.get("target")
                if not target:
                    return False
                tolerance = t.params.get("tolerance", 8)
                region = _parse_region(t.params.get("region"))
                max_results = t.params.get("max_results", 1)
                result = await asyncio.to_thread(
                    find_color, frame, tuple(target), tolerance, region, max_results
                )
                return result.get("count", 0) > 0
            elif cond == "change":
                # 帧差异检测（记录上一帧的降采样签名，不常驻整帧 Image）
                if frame is None:
                    return False
                prev = t.params.get("_prev_signature")
                threshold = t.params.get("threshold", 16)
                min_area = t.params.get("min_area", 64)
                if prev is None:
                    t.params["_prev_signature"] = await asyncio.to_thread(
                        _frame_signature, frame
                    )
                    return False
                result = await asyncio.to_thread(
                    _diff_signature, prev, frame, threshold, min_area
                )
                t.params["_prev_signature"] = await asyncio.to_thread(
                    _frame_signature, frame
                )
                return result.get("count", 0) > 0
            elif cond in ("text", "window"):
                # 未实现：text 需要 OCR 注入（调用方从未注入过），
                # window 需要标题轮询。注册时应在 dispatch 层拒绝，
                # 这里兜底返回 False 防止旧触发器崩掉 tick。
                return False
            return False
        except Exception:
            return False

    async def _check_text(self, params: dict[str, Any]) -> bool:
        """文字条件检查（由调用方注入 OCR 能力）。"""
        return False  # 默认不支持，需注入

    async def _execute_action(self, t: Trigger) -> Any:
        """执行触发器动作。"""
        if self._action_handler:
            try:
                return await self._action_handler(t.action, t.action_params)
            except Exception as e:
                return {"error": str(e)}
        return {"ok": True, "note": "action_handler not set"}


# ── 高级触发器工厂 ──────────────────────────────────────────────────


def create_color_trigger(
    trigger_id: str,
    target: tuple[int, int, int],
    action: str,
    action_params: dict[str, Any],
    tolerance: int = 8,
    region: tuple[int, int, int, int] | None = None,
    cooldown_s: float = 2.0,
    max_fires: int = 0,
) -> Trigger:
    """创建颜色触发器：画面出现指定颜色时执行动作。"""
    return Trigger(
        trigger_id=trigger_id,
        condition="color",
        params={
            "target": list(target),
            "tolerance": tolerance,
            "region": region,
        },
        action=action,
        action_params=action_params,
        cooldown_s=cooldown_s,
        max_fires=max_fires,
    )


def create_change_trigger(
    trigger_id: str,
    action: str,
    action_params: dict[str, Any],
    threshold: int = 16,
    min_area: int = 64,
    cooldown_s: float = 1.0,
    max_fires: int = 0,
) -> Trigger:
    """创建变化触发器：画面出现明显变化时执行动作。"""
    return Trigger(
        trigger_id=trigger_id,
        condition="change",
        params={
            "threshold": threshold,
            "min_area": min_area,
        },
        action=action,
        action_params=action_params,
        cooldown_s=cooldown_s,
        max_fires=max_fires,
    )


def create_text_trigger(
    trigger_id: str,
    query: str,
    action: str,
    action_params: dict[str, Any],
    mode: str = "target",
    cooldown_s: float = 2.0,
    max_fires: int = 0,
) -> Trigger:
    """创建文字触发器：画面出现指定文字时执行动作。"""
    return Trigger(
        trigger_id=trigger_id,
        condition="text",
        params={
            "query": query,
            "mode": mode,
        },
        action=action,
        action_params=action_params,
        cooldown_s=cooldown_s,
        max_fires=max_fires,
    )