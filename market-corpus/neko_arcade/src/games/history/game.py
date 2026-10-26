"""历史上的今天: GameAdapter 实现。

数据: 百度百科 eventsOnHistory API(当天大事)。
猫娘化改造: 不是干巴巴的查询, 而是猫娘当历史讲解员——
拉取当天大事 → 渲染精美图片 + 猫娘开场白 → 可按年份/类型继续互动。

交互:
- 「历史上的今天」→ 拉当天数据, 渲染图片, 猫娘开场
- 「历史上的今天 1999」→ 只看 1999 年
- 「历史上的今天 出生/大事/逝世」→ 按类型过滤
- 「退出」→ 结束会话
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from ...core.contracts import GameAdapter, build_fact

game_class = "HistoryGame"

API_TEMPLATE = "https://baike.baidu.com/cms/home/eventsOnHistory/{month}.json"
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 事件类型 → 中文标签
TYPE_LABELS = {"birth": "出生", "death": "逝世", "event": "大事", "festival": "节日"}

# 进程级缓存: {month_key: {"fetched": ts, "data": {day: [...]}}}
_CACHE: Dict[str, Dict[str, Any]] = {}


def _clean_html_to_json(text: str) -> dict:
    """解析 API 返回(标准 UTF-8 JSON)并递归清洗字符串值里的 HTML。

    输入是合法 JSON(中文为 \\uXXXX 转义), 先 json.loads 保结构,
    再对字符串值去 HTML 标签 + 还原实体(desc/title 字段含 HTML)。
    """
    import html as html_mod

    data = json.loads(text)

    def clean_value(v):
        if isinstance(v, str):
            v = re.sub(r"<[^>]+>", "", v)
            v = html_mod.unescape(v).strip()
            return v
        if isinstance(v, dict):
            return {k: clean_value(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean_value(x) for x in v]
        return v

    return clean_value(data)


def _fetch_month(month: str, retry: int = 3) -> Optional[dict]:
    """拉取某月历史数据(带重试), 失败返回 None。

    注意: 响应是标准 UTF-8 JSON(中文为 \\uXXXX 转义), 必须用 utf-8 解码
    再 json.loads —— 原插件用 unicode_escape 解码会把 JSON 的转义引号
    破坏, 才需要那套脆弱的字符串清洗。
    """
    url = API_TEMPLATE.format(month=month)
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8")
            return _clean_html_to_json(raw)
        except Exception:
            if i < retry - 1:
                time.sleep(1.5)
    return None


def _get_day_entries(month: str, day: str) -> List[Dict[str, Any]]:
    """获取某天的历史条目(带进程级缓存, 当日有效)。"""
    cache_key = month
    today_key = month + day
    now = time.time()
    cached = _CACHE.get(cache_key)
    # 缓存 12 小时内有效(数据是按月的, 一天内不会变)
    if cached and now - cached.get("fetched", 0) < 12 * 3600:
        data = cached.get("data") or {}
        return data.get(today_key, []) or []
    data = _fetch_month(month)
    if not data:
        return []
    _CACHE[cache_key] = {"fetched": now, "data": data.get(month, {})}
    return (_CACHE[cache_key]["data"] or {}).get(today_key, []) or []


class HistoryGame(GameAdapter):
    id = "history"
    name = "历史上的今天"
    description = "猫娘当历史讲解员：拉取今天的大事件，陪你聊聊历史。"
    version = "0.1.0"
    icon = "📜"

    _RULES: List[Tuple[List[str], str, str]] = [
        (["历史上的今天", "今日历史", "历史今天", "今天的历史"], "查看今天的历史大事", "today"),
        (["退出", "结束", "不看了"], "结束历史漫游", "stop"),
    ]

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._cache: Dict[str, Dict[str, Any]] = {}

    # ── 存档 ─────────────────────────

    def _default_save(self) -> Dict[str, Any]:
        return {
            "last_date": "",   # 上次查看的日期(YYYY-MM-DD)
            "view_count": 0,   # 累计查看次数
            "fav": [],         # 收藏的年份
        }

    async def _load(self, user_id: str) -> Dict[str, Any]:
        if user_id in self._cache:
            return self._cache[user_id]
        data = await self.get_user_data(user_id, None) or {}
        save = self._default_save()
        if isinstance(data, dict):
            for k in save:
                if k in data:
                    save[k] = data[k]
        self._cache[user_id] = save
        return save

    async def _save(self, user_id: str, save: Dict[str, Any]) -> None:
        self._cache[user_id] = save
        await self.save_user_data(user_id, save)

    # ── 核心 ─────────────────────────

    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        c = (cmd or "").strip()
        save = await self._load(user_id)
        if not c:
            return {"outcome": "idle", "message": ""}

        # 显式指令优先
        for kws, _, handler in self._RULES:
            for kw in kws:
                if c == kw:
                    return await getattr(self, f"_h_{handler}")(user_id, save)

        # 退出词
        if any(w in c for w in ("退出", "结束", "不看了", "算了")):
            return await self._h_stop(user_id, save)

        # 年份过滤: 纯数字(3-4位年份, 或含年份)
        m_year = re.search(r"(19\d{2}|20\d{2}|1[0-8]\d{2})", c)
        # 类型过滤
        m_type = None
        for t, label in TYPE_LABELS.items():
            if label in c:
                m_type = t
                break

        if m_year or m_type:
            return await self._h_filter(user_id, save, c, m_year.group(1) if m_year else None, m_type)

        # 其他输入 → 提示
        return await self._h_help(user_id, save)

    async def _h_today(self, user_id: str, save: Dict[str, Any]) -> Dict[str, Any]:
        today = date.today()
        month = today.strftime("%m")
        day = today.strftime("%d")
        entries = await asyncio.to_thread(_get_day_entries, month, day)
        if not entries:
            msg = "呜……今天的历史数据拉取失败了喵，百度百科那边好像不太配合。稍后再试试吧~"
            # 「游戏适配插件」: 不自己 push, 返回 message 由 brain 统一推送
            return {"outcome": "error", "facts": [], "message": msg}

        save["last_date"] = today.isoformat()
        save["view_count"] = (save.get("view_count") or 0) + 1
        await self._save(user_id, save)

        # 渲染图片卡片(游戏负责生成数据, brain 负责推送)
        lines = [(f"{e['year']} · {TYPE_LABELS.get(e.get('type',''),'大事')}",
                  self._rarity(e)) for e in entries[:8]]
        # 渲染归本体: 只给数据块(render_page), 出图失败就不带图
        card = await self.render_page(
            title=f"历史上的今天 {today.month}月{today.day}日",
            subtitle=f"共 {len(entries)} 条大事",
            rows=[[left, right] for left, right in lines], theme="light")
        neko = self._pick_emotion("start", name=str(len(entries)), year="")
        images = []
        if card:
            images.append(self.build_image(neko, card, "image/png"))
        # 返回 images 数据交 brain 统一推送; message 是给用户的简短结算文本
        text = f"📜 历史上的今天 {today.month}月{today.day}日 · 共 {len(entries)} 条大事喵"
        return {"outcome": "today", "facts": [build_fact("history", count=len(entries))],
                "message": text, "images": images, "game": self.id,
                "game_name": self.name, "entries": len(entries)}

    async def _h_filter(self, user_id: str, save: Dict[str, Any], c: str,
                        year: Optional[str], ftype: Optional[str]) -> Dict[str, Any]:
        today = date.today()
        entries = await asyncio.to_thread(_get_day_entries,
                                          today.strftime("%m"), today.strftime("%d"))
        if not entries:
            msg = "呜……历史数据拉取失败了喵，稍后再试吧~"
            return {"outcome": "error", "facts": [], "message": msg}
        filtered = []
        for e in entries:
            if year and e.get("year") == year:
                filtered.append(e)
            elif ftype and e.get("type") == ftype:
                filtered.append(e)
        if not filtered:
            label = year or TYPE_LABELS.get(ftype or "", "")
            msg = f"喵……今天没有 {label} 的记录呢。换一个年份或类型试试?"
            return {"outcome": "empty_filter", "facts": [], "message": msg}
        lines = [(f"{e['year']} · {TYPE_LABELS.get(e.get('type',''),'大事')}", self._rarity(e))
                 for e in filtered[:8]]
        card = await self.render_page(
            title=f"筛选结果 {year or TYPE_LABELS.get(ftype or '', '')}",
            subtitle=f"共 {len(filtered)} 条",
            rows=[[left, right] for left, right in lines], theme="light")
        neko = self._pick_emotion("year_result", year=year or TYPE_LABELS.get(ftype or "", ""),
                                  count=str(len(filtered)))
        images = []
        if card:
            images.append(self.build_image(neko, card, "image/png"))
        label = year or TYPE_LABELS.get(ftype or "", "")
        msg = f"筛选结果: {label} 共 {len(filtered)} 条大事喵"
        return {"outcome": "filter", "facts": [build_fact("history_filter", count=len(filtered))],
                "message": msg, "images": images, "game": self.id}

    async def _h_stop(self, user_id: str, save: Dict[str, Any]) -> Dict[str, Any]:
        msg = self._pick_emotion("stop")
        return {"outcome": "stop", "facts": [], "message": msg, "game": self.id}

    async def _h_help(self, user_id: str, save: Dict[str, Any]) -> Dict[str, Any]:
        msg = ("喵~可以发「历史上的今天」看今天的大事; "
               "或加年份/类型过滤, 比如「历史上的今天 1999」「历史上的今天 出生」")
        return {"outcome": "help", "facts": [], "message": msg, "game": self.id}

    # ── 工具 ─────────────────────────

    @staticmethod
    def _rarity(e: Dict[str, Any]) -> str:
        y = int(e.get("year") or 0)
        if y < 1000:
            return "legendary"
        if y < 1800:
            return "epic"
        if y < 1900:
            return "rare"
        return "common"

    def _pick_emotion(self, key: str, **params: str) -> str:
        templates = getattr(self, "_emotion_templates", None) or {}
        pool = templates.get(key) or [f"喵,{key}"]
        import random
        text = random.choice(pool)
        for k, v in params.items():
            text = text.replace("{" + k + "}", str(v))
        return text
