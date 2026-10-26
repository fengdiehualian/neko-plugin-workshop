"""每日任务: 修炼/突破/签到/战斗/秘境/闭关 计数, 提交领奖。

事件由各 handler 调用 record() 累计(存 save.extra.daily), 次日自动重置。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from .player import PlayerSave

DAILY_TASKS: List[Dict[str, Any]] = [
    {"id": "sign", "name": "修仙签到", "target": 1, "reward": 200},
    {"id": "cultivate", "name": "修炼", "target": 3, "reward": 300},
    {"id": "breakthrough", "name": "突破/破体", "target": 1, "reward": 500},
    {"id": "battle", "name": "战斗(打劫/切磋/讨伐)", "target": 3, "reward": 400},
    {"id": "secret", "name": "探索秘境", "target": 2, "reward": 400},
    {"id": "seclusion", "name": "闭关", "target": 1, "reward": 300},
]


class DailyTask:
    """每日任务中间件。"""

    DAILY_TASKS = DAILY_TASKS   # 模块级常量暴露为类属性

    def __init__(self, game: Any) -> None:
        self.game = game

    def _state(self, save: PlayerSave) -> Dict[str, Any]:
        today = time.strftime("%Y-%m-%d")
        d = save.extra.setdefault("daily", {})
        if d.get("date") != today:
            d.clear()
            d["date"] = today
            d["counts"] = {t["id"]: 0 for t in DAILY_TASKS}
            d["claimed"] = False
        d.setdefault("counts", {t["id"]: 0 for t in DAILY_TASKS})
        return d

    def record(self, save: PlayerSave, task_id: str, n: int = 1) -> None:
        d = self._state(save)
        if not d.get("claimed"):
            d["counts"][task_id] = d["counts"].get(task_id, 0) + n

    def view(self, save: PlayerSave) -> Dict[str, Any]:
        d = self._state(save)
        rows = []
        for t in DAILY_TASKS:
            cur = min(d["counts"].get(t["id"], 0), t["target"])
            rows.append({"id": t["id"], "name": t["name"],
                         "cur": cur, "target": t["target"], "done": cur >= t["target"]})
        all_done = all(r["done"] for r in rows)
        return {"date": d["date"], "claimed": d.get("claimed", False),
                "rows": rows, "all_done": all_done}

    def claim(self, save: PlayerSave) -> Dict[str, Any]:
        v = self.view(save)
        d = self._state(save)
        if d.get("claimed"):
            return {"ok": False, "msg": "今日奖励已领取过喵"}
        if not v["all_done"]:
            return {"ok": False, "msg": "每日任务还没做完喵"}
        total = sum(t["reward"] for t in DAILY_TASKS)
        d["claimed"] = True
        save.add_lingshi(total)
        save.exp += 500
        return {"ok": True, "msg": f"完成全部每日任务!灵石+{total},修为+500 喵",
                "reward": total, "exp": 500}
