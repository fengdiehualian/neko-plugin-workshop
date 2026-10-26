"""钓鱼知识库：mod 导出的图鉴 JSON + 机制知识问答（全量）。

数据源：data/knowledge/creatures.json（mod 从 GameInfo._allCreatures 导出：
物种/价值/基础重量/Boss/濒危 + 鱼饵及饵→鱼匹配权重）。
机制知识（收线/烤鱼/赌场/Boss/图鉴/海鸥）内置，不依赖数据文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"

# ── 机制知识（手写，钓鱼陪玩要"懂游戏"） ──

MECHANICS = {
    "reel": (
        "收线技巧喵：线收得越快拉力越大、鱼越容易挣脱；快慢交替（快速收线蓄力、"
        "松手缓一缓）是最稳的钓大鱼手法~"
    ),
    "bite": (
        "咬钩时机喵：浮漂一动先别急，等浮漂明显下沉/连续抖动再提竿，成功率最高；"
        "提早了容易跑鱼~"
    ),
    "bait": (
        "鱼饵学问喵：每种饵吸引的鱼不一样（游戏里饵对鱼有匹配权重），用对饵钓对鱼；"
        "饵还有丢失概率，贵重饵要省着用~"
    ),
    "grill": (
        "烤鱼喵：把鱼放上烤架，烤制会改变鱼的价值（有个烤度→价值的曲线），"
        "烤到合适火候再收，别烤糊了~"
    ),
    "casino": (
        "赌场规则喵：赌场可以押鱼当赌注，轮盘开奖押黑/红/绿三色；押对了赢、押错了输。"
        "小赌怡情，别把家底押进去喵~"
    ),
    "slot": (
        "老虎机喵：花金币下注转老虎机，开奖看运气，纯娱乐别上头~"
    ),
    "boss": (
        "Boss 战喵：Boss 有血量条和限时，全岛一起集火打；打完掉传奇战利品，"
        "是图鉴里最稀有的收藏~"
    ),
    "journal": (
        "图鉴收集喵：游戏里有几十种生物（鱼/蟹/鲸/鸟…），钓到就记录进图鉴；"
        "集齐全图鉴是终极目标，稀有/彩虹/滴液变种最难~"
    ),
    "seagull": (
        "海鸥喵：这些家伙会盯上你手上的鱼叼走！被抢过几次就懂它们的套路了~"
    ),
    "rod": (
        "鱼竿升级喵：更好的鱼竿/配件能钓更远、上钩更稳，攒钱去商店升级装备吧~"
    ),
    "shiny": (
        "彩虹皮肤喵：极小概率钓到带彩虹皮肤的鱼（传奇级收藏），图鉴里最闪耀的一页！"
    ),
    "drip": (
        "滴液变种喵：某些鱼有滴液变体（Dripper），比普通版更稀有，图鉴里有单独记录~"
    ),
}

MECHANIC_KEYWORDS: Dict[str, List[str]] = {
    "reel": ["收线", "拉力", "怎么收", "断线"],
    "bite": ["咬钩", "提竿", "时机", "浮漂", "怎么钓"],
    "bait": ["饵", "鱼饵", "什么饵"],
    "grill": ["烤", "烧烤", "烤鱼"],
    "casino": ["赌场", "赌", "轮盘", "押"],
    "slot": ["老虎机", "拉霸"],
    "boss": ["boss", "boss战", "鲸", "巨物"],
    "journal": ["图鉴", "收集", "全图鉴"],
    "seagull": ["海鸥", "鸟"],
    "rod": ["鱼竿", "竿", "装备", "升级"],
    "shiny": ["彩虹", "闪亮", "传奇"],
    "drip": ["滴液", "drip"],
}


class KnowledgeBase:
    """图鉴 + 钓鱼机制问答（全量）。"""

    def __init__(self) -> None:
        self.creatures: List[Dict[str, Any]] = []
        self.baits: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        """加载 mod 导出的图鉴 JSON（缺失不报错，可等 mod 首次运行生成）。"""
        try:
            p = KNOWLEDGE_DIR / "creatures.json"
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                self.creatures = data.get("creatures", []) if isinstance(data, dict) else []
                self.baits = data.get("baits", []) if isinstance(data, dict) else []
        except (OSError, json.JSONDecodeError):
            self.creatures = []
            self.baits = []

    def snapshot(self) -> Dict[str, Any]:
        return {"creatures": len(self.creatures), "baits": len(self.baits)}

    def creature_names(self) -> List[str]:
        """全部物种名（OCR 画面匹配用）。"""
        return [str(c.get("name", "")).strip() for c in self.creatures if c.get("name")]

    def bait_names(self) -> List[str]:
        """全部饵名（OCR 画面匹配用）。"""
        return [str(b.get("name", "")).strip() for b in self.baits if b.get("name")]

    # ── 图鉴查询 ──

    def _find(self, name: str) -> Optional[Dict[str, Any]]:
        """按名字找物种（模糊：名/类型包含）。"""
        name = (name or "").strip()
        if not name:
            return None
        for c in self.creatures:
            if name in str(c.get("name", "")) or name in str(c.get("type", "")):
                return c
        return None

    def creature_info(self, name: str) -> Optional[str]:
        """单物种资料：价值/重量/Boss/濒危。"""
        c = self._find(name)
        if not c:
            return None
        parts = [f"「{c.get('name')}」"]
        worth = c.get("worth", 0)
        parts.append(f"值 {worth} 金币" if worth else "不值钱")
        w = c.get("weight", 0)
        if w:
            parts.append(f"基础重量约 {w}kg")
        if c.get("endangered"):
            parts.append("濒危物种！")
        if c.get("boss") and c.get("boss") != "None":
            parts.append("Boss 级！")
        return "，".join(parts) + " 喵~"

    def most_valuable(self, top: int = 3) -> Optional[str]:
        """价值排行。"""
        if not self.creatures:
            return None
        ranked = sorted(self.creatures, key=lambda c: c.get("worth", 0), reverse=True)[:top]
        names = "、".join(f"{c.get('name', '?')}({c.get('worth', 0)})" for c in ranked)
        return f"最值钱的前几个：{names} 喵~"

    def bait_for(self, fish_name: str) -> Optional[str]:
        """什么饵钓 X：按饵→鱼匹配权重找最高。"""
        c = self._find(fish_name)
        target = (c.get("name") if c else fish_name).strip()
        if not self.baits or not target:
            return None
        best = None
        best_w = 0.0
        for b in self.baits:
            for w in (b.get("weights") or []):
                if target in str(w.get("item", "")) and float(w.get("weight", 0)) > best_w:
                    best = b
                    best_w = float(w.get("weight", 0))
        if best:
            return (f"钓{target}用「{best.get('name')}」最合适"
                    f"（匹配权重 {best_w:.0f}）喵~")
        return None

    def fish_for(self, bait_name: str) -> Optional[str]:
        """X 饵能钓什么：列出权重最高的几种鱼。"""
        bait = None
        for b in self.baits:
            if bait_name in str(b.get("name", "")):
                bait = b
                break
        if not bait:
            return None
        weights = sorted((bait.get("weights") or []),
                         key=lambda w: float(w.get("weight", 0)), reverse=True)[:4]
        if not weights:
            return None
        names = "、".join(str(w.get("item", "?")) for w in weights)
        return f"「{bait.get('name')}」容易吸引：{names} 喵~"

    # ── 机制问答 ──

    def mechanic_tip(self, text: str) -> Optional[str]:
        """机制知识：关键词命中即答。"""
        for key, kws in MECHANIC_KEYWORDS.items():
            if any(kw in text for kw in kws):
                return MECHANICS[key]
        return None

    # ── 统一入口 ──

    def game_tip(self, text: str) -> Optional[str]:
        """泛化问答（命中即答，未命中返回 None 由 runtime 兜底）。"""
        # 机制优先（饵/烤/赌/Boss 等明确词）
        tip = self.mechanic_tip(text)
        if tip:
            return tip
        # 图鉴/价值查询
        if "值" in text or "钱" in text or "贵" in text:
            if "最" in text:
                return self.most_valuable()
            name = self._extract_fish_name(text)
            if name:
                info = self.creature_info(name)
                if info:
                    return info
            return self.most_valuable(1)
        if "饵" in text and ("钓" in text or "鱼" in text):
            name = self._extract_fish_name(text)
            if name:
                tip = self.bait_for(name)
                if tip:
                    return tip
        if "什么鱼" in text or "能钓" in text or "钓什么" in text:
            bait = self._extract_bait_name(text)
            if bait:
                tip = self.fish_for(bait)
                if tip:
                    return tip
        # 直接物种名查询
        name = self._extract_fish_name(text)
        if name:
            info = self.creature_info(name)
            if info:
                return info
        return None

    def _extract_fish_name(self, text: str) -> str:
        """从问句里提取可能的物种名（数据里最长匹配）。"""
        for c in sorted(self.creatures, key=lambda c: len(str(c.get("name", ""))), reverse=True):
            n = str(c.get("name", ""))
            if n and n in text:
                return n
            t = str(c.get("type", ""))
            if t and len(t) > 2 and t in text:
                return t
        return ""

    def _extract_bait_name(self, text: str) -> str:
        for b in sorted(self.baits, key=lambda b: len(str(b.get("name", ""))), reverse=True):
            n = str(b.get("name", ""))
            if n and n in text:
                return n
        return ""
