"""单文件小游戏示例：猜硬币。整个游戏只有一个文件。"""

import random
import time

from ...core.contracts import GameAdapter, build_fact

game_class = "CoinFlipGame"


class CoinFlipGame(GameAdapter):
    id = "coinflip"
    name = "猜硬币"
    description = "和猫娘玩猜硬币正反面"
    icon = "🪙"

    async def handle_action(self, user_id, cmd, args=None):
        c = (cmd or "").strip()
        # 只说游戏名没给正反 → 提示
        if not c or c in ("猜硬币", "猜", "硬币"):
            return {"facts": [], "outcome": "done",
                    "message": "说「猜硬币 正」或「猜硬币 反」喵"}

        data = await self.get_user_data(user_id, {"stats": {"wins": 0, "plays": 0}})
        # 每日次数限制（读 config.json，缺省 20）
        max_plays = int((getattr(self, "_config", None) or {}).get("max_plays_per_day", 20) or 20)
        today = time.strftime("%Y-%m-%d")
        if data.get("day") != today:
            data["day"] = today
            data["plays_today"] = 0
        if data.get("plays_today", 0) >= max_plays:
            return {"facts": [], "outcome": "limit",
                    "message": f"今天已经玩够 {max_plays} 局啦，明天再来喵~"}
        data["plays_today"] = data.get("plays_today", 0) + 1

        pick = "正" if ("正" in c) else "反"
        result = random.choice(["正", "反"])
        win = pick == result
        fact = build_fact("win" if win else "lose", pick=pick, result=result)
        outcome = "win" if win else "lose"

        # 持久化统计数据
        data["stats"]["plays"] += 1
        if win:
            data["stats"]["wins"] += 1
        await self.save_user_data(user_id, data)

        return {"facts": [fact],
                "outcome": outcome,
                "message": f"硬币落下…是{result}！你猜{pick}，" + ("赢啦！" if win else "输啦…")}

    async def get_status(self, user_id="default"):
        data = await self.get_user_data(user_id, {}) or {}
        stats = data.get("stats", {})
        return {"wins": stats.get("wins", 0), "plays": stats.get("plays", 0)}

    def support_panel(self):
        return {"schemas": [
            {"field": "max_plays_per_day", "label": "每日可玩次数", "component": "InputNumber",
             "props": {"min": 1, "max": 1000}, "help": "每天最多能玩几局猜硬币"},
        ]}