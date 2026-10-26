# -*- coding: utf-8 -*-
"""「正在和她对话的人」：每群一份，回答"这个人现在算不算在跟她聊"。

使用者口径（2026-09-29）：

> 可以让猫娘对正在聊天的对象的禁言做出反应吗
> （选定）正在和她对话的人

判据刻意要求**一来一回**，缺一不可：

- **他找过她** —— @ 她 / 引用她 / 命中关键词（门控里的点名类路径，见
  `attention_gate_service.evaluate` 的第 2 步与第 4 步）；
- **她回过他** —— 某一条回复的触发者是他。

为什么不用"最近在本群说过话的人"：群里谁在说话**与她无关**。她本来就要判断
"绝大多数消息不是对我说的"（`addressing.py` + `reply_necessity`），拿"说过话"当判据，
一个从没跟她搭过话的人在群里被管理禁言也会触发她开口 —— 那不是"她正在聊天的人"，
只是"同一个群里的人"。

窗口（默认 `WINDOW_SECONDS`）内两条都成立才算。窗口同时也是禁言反应的冷却时长：
一个十分钟没跟她说过话的人，不该因为她被禁言而突然插一句。

状态是**内存态、重启即失** —— 与 `GroupSpeechTracker` / `IdleBackoff` 一致：这类
"最近怎么样"的状态没有回放价值（回放反而会把一次旧对话当成"正在进行"）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

#: 没有显式给 now 时用的时钟。
_clock = time.time


@dataclass
class _Partner:
    """一个人与她之间最近一次的"他找她"与"她回他"。"""

    addressed_at: float = 0.0
    replied_at: float = 0.0
    #: 最近一次被记录的时刻（**含单向**）。它只服务淘汰与排序 ——
    #: 不能拿 `max(addressed_at, replied_at)` 代替：刚建出来的条目两个字段都是 0，
    #: 那样"容量满了该淘汰谁"会先把**刚进来的那个人**淘汰掉（首版就是这么错的，
    #: 被 test_partners_are_capped_and_pruned 抓到）。
    touched_at: float = 0.0

    def in_dialogue(self, now: float, window: float) -> bool:
        return (
            self.addressed_at > 0.0
            and self.replied_at > 0.0
            and now - self.addressed_at <= window
            and now - self.replied_at <= window
        )

    def last_seen(self) -> float:
        return self.touched_at


class DialoguePartnerTracker:
    """内存态：``{group_id: {user_id: _Partner}}``。

    ``MAX_PARTNERS`` 是防无界增长的闸：一个群里人来人往，这个表只服务于
    "最近谁在跟她聊"，只留最近活跃的若干个（按最近一次互动时间淘汰）。
    """

    WINDOW_SECONDS = 600.0
    MAX_PARTNERS = 20

    def __init__(self, *, window_seconds: float = WINDOW_SECONDS) -> None:
        self.window_seconds = float(window_seconds or self.WINDOW_SECONDS)
        self._partners: dict[str, dict[str, _Partner]] = {}

    # ── 记录 ────────────────────────────────────────────────────────────

    def note_address(self, group_id: str, user_id: str, *, now: float | None = None) -> None:
        """他找她（@ / 引用她 / 关键词）。"""
        partner = self._touch(group_id, user_id, now=now)
        if partner is not None:
            partner.addressed_at = self._now(now)

    def note_reply(self, group_id: str, user_id: str, *, now: float | None = None) -> None:
        """她回他（这条回复的触发者是他）。"""
        partner = self._touch(group_id, user_id, now=now)
        if partner is not None:
            partner.replied_at = self._now(now)

    # ── 查询 ────────────────────────────────────────────────────────────

    def is_in_dialogue(self, group_id: str, user_id: str, *, now: float | None = None) -> bool:
        """这个人现在算不算"正在和她对话"。"""
        key = str(group_id or "").strip()
        uid = str(user_id or "").strip()
        if not key or not uid:
            return False
        bucket = self._prune(key, self._now(now))
        partner = bucket.get(uid)
        return bool(partner and partner.in_dialogue(self._now(now), self.window_seconds))

    def dialogue_partners(self, group_id: str, *, now: float | None = None) -> list[str]:
        """当前正在和她对话的人（按最近互动时间从新到旧）；供日志与排查。"""
        key = str(group_id or "").strip()
        if not key:
            return []
        moment = self._now(now)
        bucket = self._prune(key, moment)
        alive = [uid for uid, p in bucket.items() if p.in_dialogue(moment, self.window_seconds)]
        return sorted(alive, key=lambda uid: bucket[uid].last_seen(), reverse=True)

    def forget(self, group_id: str) -> None:
        self._partners.pop(str(group_id or "").strip(), None)

    # ── 内部 ────────────────────────────────────────────────────────────

    @staticmethod
    def _now(now: float | None) -> float:
        if now is None:
            return float(_clock())
        try:
            return float(now)
        except (TypeError, ValueError):
            return float(_clock())

    def _touch(self, group_id: str, user_id: str, *, now: float | None) -> _Partner | None:
        key = str(group_id or "").strip()
        uid = str(user_id or "").strip()
        if not key or not uid:
            return None
        moment = self._now(now)
        bucket = self._prune(key, moment)
        partner = bucket.get(uid)
        if partner is None:
            partner = _Partner()
            bucket[uid] = partner
        # 先盖时间戳再淘汰：否则新条目的 last_seen 还是 0，容量闸会把刚进来的这个人
        # 当成"最旧的"淘汰掉（首版就是这个错）。
        partner.touched_at = moment
        self._enforce_capacity(bucket)
        return partner

    def _prune(self, group_id: str, now: float) -> dict[str, _Partner]:
        bucket = self._partners.get(group_id)
        if bucket is None:
            bucket = {}
            self._partners[group_id] = bucket
            return bucket
        stale = [
            uid for uid, partner in bucket.items()
            if now - partner.last_seen() > self.window_seconds
        ]
        for uid in stale:
            bucket.pop(uid, None)
        return bucket

    def _enforce_capacity(self, bucket: dict[str, _Partner]) -> None:
        if len(bucket) <= self.MAX_PARTNERS:
            return
        for uid, _partner in sorted(bucket.items(), key=lambda item: item[1].last_seen())[
            : len(bucket) - self.MAX_PARTNERS
        ]:
            bucket.pop(uid, None)
