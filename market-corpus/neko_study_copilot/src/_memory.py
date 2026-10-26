"""两级记忆：短期对话记忆 + 长期事实记忆。

为什么需要它：模型本身是**无状态**的，每次调用都从零开始。之前插件只在本地
存了掌握度和计划，**对话一个字都没留**——所以你发张截图诊断完，再问「第 3 题
再讲讲」，它根本不知道你在说哪张卷子。

设计（与用户提出的方案一致）：

============ ================================================== ==========
短期记忆      对话回合、截图转写、当次讲解与诊断的原话              7 天
长期记忆      进度、掌握情况、反复错的原因、偏好、诊断结论          不自动删
============ ================================================== ==========

关键在**清理前先压缩**：`purge()` 会在删掉过期对话前，把这一周涉及的知识点、
次数、时间范围压成一条长期事实（``progress:weekly-*``）写进 ``memory_facts``。
删的是原话，留下的是结论。

全部落在插件自己的 ``data/study.db``（已 gitignore），不上传、不联网。
"""

from __future__ import annotations

import time
from typing import Any, Optional

# 长期记忆的类别与默认重要度（越大越优先被召回）
FACT_KINDS: dict[str, int] = {
    "progress": 3,     # 进度：这周推进了什么
    "weakness": 4,     # 薄弱点：反复出错的知识点
    "mistake": 4,      # 错因：具体是怎么错的
    "diagnosis": 4,    # 诊断结论
    "preference": 5,   # 偏好：讲解风格、习惯（最该记住）
    "plan": 3,         # 学习计划要点
    "note": 2,         # 其它
}

_MEMORY_RULE = (
    "## 关于下面的「记忆」\n"
    "它是这台机器上真实发生过的记录，可以放心引用；但：\n"
    "- 与本次话题无关的，直接忽略，不要硬扯；\n"
    "- 记忆里没有的，就说没有，**不要编造你「记得」什么**；\n"
    "- 引用时用自然语气（「上次你说…」），不要贴数据库字段名。"
)


def _ago(stamp: float, now: float) -> str:
    """把时间戳说成「3 小时前」这种人话。"""
    delta = max(0.0, now - float(stamp or 0))
    if delta < 90:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    days = int(delta // 86400)
    if days <= 30:
        return f"{days} 天前"
    return f"{int(days // 30)} 个月前"


class MemoryKeeper:
    """把 :class:`~._store.StudyStore` 的记忆表包装成"会用"的记忆。"""

    def __init__(
        self,
        store: Any,
        *,
        enabled: bool = True,
        short_days: float = 7.0,
        recall_turns: int = 8,
        max_chars: int = 1600,
        logger: Any = None,
    ) -> None:
        self.store = store
        self.enabled = bool(enabled)
        self.short_days = max(0.5, float(short_days or 7.0))
        self.recall_turns = max(2, int(recall_turns or 8))
        self.max_chars = max(400, int(max_chars or 1600))
        self.logger = logger
        self._last_purge = 0.0

    # ── 写入 ──────────────────────────────────────────────────
    def remember_turn(
        self,
        role: str,
        text: str,
        *,
        topic: str = "",
        ref_kind: str = "",
        ref_id: str = "",
        session: str = "",
    ) -> int:
        if not self.enabled or not str(text or "").strip():
            return 0
        return self.store.add_turn(
            role,
            text,
            session=session,
            topic=topic,
            ref_kind=ref_kind,
            ref_id=ref_id,
            ttl_days=self.short_days,
        )

    def remember_fact(
        self,
        kind: str,
        key: str,
        text: str,
        *,
        topic: str = "",
        importance: Optional[int] = None,
    ) -> int:
        if not self.enabled or not str(text or "").strip():
            return 0
        level = FACT_KINDS.get(kind, 2) if importance is None else int(importance)
        return self.store.upsert_fact(kind, key, text, topic=topic, importance=level)

    # ── 召回 ──────────────────────────────────────────────────
    def build_context(self, *, topic: str = "", keywords: str = "") -> str:
        """拼一段「记忆」给模型，接在提示词前面。

        - 最近对话：短期记忆里最新的若干条（同一话题优先）；
        - 相关长期事实：按 topic / 关键词命中，按重要度排序；
        - 上一次诊断结论：诊断是最容易被追问的东西，单独带一条。
        """
        if not self.enabled:
            return ""
        try:
            now = time.time()
            recent = self.store.recent_turns(limit=self.recall_turns, now=now)
            same_topic = [row for row in self.store.recent_turns(limit=self.recall_turns, topic=topic, now=now)] \
                if topic else []
            facts: list[dict[str, Any]] = []
            seen: set[int] = set()
            for keyword in [topic, keywords]:
                if not keyword:
                    continue
                for row in self.store.search_facts(keyword, limit=5):
                    if row["id"] not in seen:
                        seen.add(row["id"])
                        facts.append(row)
            lines: list[str] = []
            if same_topic:
                lines.append("### 关于这件事之前聊过的")
                lines.extend(self._turn_line(row, now) for row in same_topic)
            if recent:
                lines.append("### 最近聊过（短期记忆，到期自动清理）")
                lines.extend(self._turn_line(row, now) for row in recent)
            if facts:
                lines.append("### 长期记忆（进度与薄弱点）")
                for row in facts[:6]:
                    lines.append(f"- [{row.get('kind')}] {row.get('text')}")
            diagnosis = self.store.latest_diagnosis()
            if diagnosis and diagnosis.get("summary"):
                lines.append("### 上一次诊断结论")
                lines.append(f"- {str(diagnosis.get('summary'))[:220]}")
            if not lines:
                return ""
            body = "\n".join(lines)
            if len(body) > self.max_chars:
                body = body[: self.max_chars] + "…"
            return f"{body}\n\n{_MEMORY_RULE}"
        except Exception as exc:  # 记忆坏了不能拖垮教学
            if self.logger is not None:
                self.logger.warning("[study_copilot] 记忆召回失败（忽略）: %s", exc)
            return ""

    @staticmethod
    def _turn_line(row: dict[str, Any], now: float) -> str:
        who = {"user": "他", "assistant": "你", "material": "材料"}.get(str(row.get("role")), "记录")
        text = " ".join(str(row.get("text") or "").split())
        return f"- [{_ago(float(row.get('created') or 0), now)}·{who}] {text[:180]}"

    # ── 维护 ──────────────────────────────────────────────────
    def purge(self, *, force: bool = False, min_interval_hours: float = 12.0) -> int:
        """清理过期短期记忆；**清理前先把这一周压成一条长期事实**。

        返回删除条数。默认 12 小时内只真正跑一次，避免每次请求都扫库。
        """
        if not self.enabled:
            return 0
        now = time.time()
        if not force and (now - self._last_purge) < min_interval_hours * 3600:
            return 0
        self._last_purge = now
        try:
            stats = self.store.memory_stats(now=now)
            removed = 0
            if stats.get("expired"):
                digest = self._weekly_digest(now)
                if digest:
                    week_key = time.strftime("weekly-%Y-%W", time.localtime(now))
                    self.store.upsert_fact("progress", week_key, digest, importance=FACT_KINDS["progress"])
                removed = self.store.purge_turns(keep_days=self.short_days, now=now)
                if self.logger is not None and removed:
                    self.logger.info("[study_copilot] 短期记忆已清理 %d 条（结论已并入长期记忆）", removed)
            return removed
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning("[study_copilot] 记忆清理失败（下次再试）: %s", exc)
            return 0

    def _weekly_digest(self, now: float) -> str:
        """把这一段将要过期的对话压成一句话：次数 + 涉及的知识点 + 时间范围。"""
        try:
            pick = self.store.expired_turns(now, limit=300)
        except Exception:
            return ""
        if not pick:
            return ""
        topics: list[str] = []
        for row in pick:
            topic = str(row.get("topic") or "").strip()
            if topic and topic not in topics:
                topics.append(topic)
        span_start = min(float(row.get("created") or 0) for row in pick)
        span_end = max(float(row.get("created") or 0) for row in pick)
        head = f"{time.strftime('%m-%d', time.localtime(span_start))}~{time.strftime('%m-%d', time.localtime(span_end))}"
        kinds = {str(row.get("ref_kind") or "") for row in pick}
        kinds.discard("")
        what = "、".join(sorted(kinds)) or "对话"
        detail = f"，涉及：{'、'.join(topics[:6])}" if topics else ""
        return f"{head} 共 {len(pick)} 条{what}记录{detail}。"

    # ── 展示与清理（面板 / 命令用）─────────────────────────────
    def snapshot(self, *, limit: int = 20) -> dict[str, Any]:
        now = time.time()
        return {
            "enabled": self.enabled,
            "short_days": self.short_days,
            "stats": self.store.memory_stats(now=now),
            "turns": [
                {
                    "role": row.get("role"),
                    "text": row.get("text"),
                    "topic": row.get("topic"),
                    "ref_kind": row.get("ref_kind"),
                    "created": row.get("created"),
                    "ago": _ago(float(row.get("created") or 0), now),
                }
                for row in self.store.recent_turns(limit=limit, now=now)
            ],
            "facts": [
                {
                    "id": row.get("id"),
                    "kind": row.get("kind"),
                    "text": row.get("text"),
                    "topic": row.get("topic"),
                    "importance": row.get("importance"),
                    "ago": _ago(float(row.get("updated") or 0), now),
                }
                for row in self.store.list_facts(limit=limit)
            ],
        }

    def forget(self, *, scope: str = "short", fact_id: int = 0) -> dict[str, Any]:
        """清记忆：``short`` 清短期，``all`` 短期+长期，``fact`` 删单条长期。"""
        result = {"scope": scope, "turns": 0, "facts": 0}
        if scope in ("short", "all"):
            result["turns"] = self.store.clear_turns()
        if scope == "all":
            with_facts = self.store.list_facts(limit=500)
            for row in with_facts:
                result["facts"] += self.store.drop_fact(int(row["id"]))
        elif scope == "fact" and fact_id:
            result["facts"] = self.store.drop_fact(int(fact_id))
        return result
