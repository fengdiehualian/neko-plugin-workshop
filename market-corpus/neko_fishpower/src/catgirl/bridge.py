"""人设桥接：宿主导入人设 + 存在感话术（钓鱼场景，从配置读可覆盖）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXISTENCE_LINES = {
    "caught": [
        "上钩啦喵！让我看看是什么好货~",
        "有鱼上钩了！可别让它跑掉喵！",
    ],
    "bite": [
        "浮漂动了！来了来了！",
        "咬钩了喵！盯紧浮漂！",
    ],
    "miss": [
        "哎呀，跑掉了喵… 下次提竿再快点！",
        "差一点！这条鱼滑溜得很喵",
    ],
    "sold": [
        "卖完鱼啦喵，我帮你把金币收好了~",
        "今天的鱼获换钱咯，数数看赚了多少喵！",
    ],
    "grill_done": [
        "鱼烤好啦喵！闻着真香！",
        "烧烤大师出手，这条鱼色香味俱全喵~",
    ],
    "boss_death": [
        "Boss 解决啦喵！今晚加餐！",
        "打赢了！传奇战利品到手喵！",
    ],
    "game_end": [
        "今天钓了这么多，辛苦啦喵！收竿晚安~ 💤",
        "饵都收好了喵，明天继续！晚安！",
    ],
    "player_death": [
        "呜… 主人被 Boss 打倒了！快回出生点，我等你喵！",
        "主人倒下了喵！别灰心，重整旗鼓再来一次！",
    ],
}


def _load_mode_config() -> dict:
    """读 data/config/mode.json 的存在感话术覆盖（照 pawpilot）。"""
    try:
        p = Path(__file__).resolve().parent.parent / "data" / "config" / "mode.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        existence = data.get("existence")
        return existence if isinstance(existence, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class CatgirlBridge:
    """人设桥接：存在感话术（配置优先，代码默认兜底）。"""

    def __init__(self) -> None:
        override = _load_mode_config()
        self._existence = {**EXISTENCE_LINES, **override}

    def existence_line(self, event_name: str, **kw: Any) -> str:
        """存在感话术。"""
        lines = self._existence.get(event_name)
        if not lines or not isinstance(lines, (list, tuple)):
            return ""
        text = lines[0] if lines else ""
        for k, v in kw.items():
            text = text.replace("{" + k + "}", str(v))
        return text
