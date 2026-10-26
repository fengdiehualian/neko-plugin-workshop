"""主动陪伴（L3 低频）：像朋友一样参与游戏——换饵建议 / 换岛探索 / 赌场氛围 / 连竿情绪。"""

from __future__ import annotations

from typing import Any, Dict, Optional


class Proactive:
    """场景化陪伴：用当前状态（快照 + 会话统计）生成一句自然的互动。"""

    def __init__(self) -> None:
        self._last: Dict[str, float] = {}

    def _cooldown(self, key: str, now: float, seconds: float) -> bool:
        if now - self._last.get(key, 0) < seconds:
            return True
        self._last[key] = now
        return False

    def propose(self, now: float, st: Any, session: Dict[str, Any]) -> Optional[str]:
        """按状态给一句陪伴（无则 None）。"""
        if st is None or not st.connected:
            return None
        # 连竿情绪：本局钓到多条，夸一夸（参与感）
        if session.get("caught", 0) >= 5 and not self._cooldown("streak_praise", now, 600):
            return f"本局已经 {session['caught']} 条了喵！手感火热，这波是钓鱼大师~"
        # 长时间等待没咬钩 → 换饵建议（像朋友支招）
        if st.phase == "waiting" and not self._cooldown("bait", now, 300):
            return "这一竿等好久了喵… 要不要换个饵试试？说不定是饵不对路~"
        # 图鉴缺鱼 → 鼓励换岛
        if st.journal_total and st.journal_count < st.journal_total \
                and not self._cooldown("island", now, 600):
            remain = st.journal_total - st.journal_count
            if remain > 0:
                return f"图鉴还差 {remain} 种喵，想集齐的话换个岛碰碰运气？"
        # 赚了钱 → 升级建议（本局卖鱼 > 200）
        if session.get("sold_total", 0) >= 200 and not self._cooldown("upgrade", now, 600):
            return "今天卖鱼赚了不少喵！要不要去商店升级下鱼竿/配件？"
        # Boss 备战氛围（Boss 在场时给参与感）
        if st.boss_active and not self._cooldown("boss", now, 300):
            return f"Boss 还有 {st.boss_hp}/{st.boss_max_hp} 血喵，我陪你盯着，别让它跑了！"
        return None
