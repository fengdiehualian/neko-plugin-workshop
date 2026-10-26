"""出站守门人：拦住「发出去会出问题」的文本。

两个**独立**规则，同一个收口点：

1. **黑名单**（内容安全）—— 复用关键词页里已有的黑名单词表（`backlog_labels` 里
   `priority < 0` 的那批，与入站拦截同一份真相）。为什么必须有这一道：我们有三条
   **把别人的原文发出去**的路 ——
   · `repeat_echo_service` 跟着复读（**逐字发出**，连 CQ 码都是剥掉后原样发）；
   · `reply_delivery_node` 的合并转发 `<forward>`（附上标记之后的群友原文）；
   · `runtime_ops_service.send_group_message`（别的插件原文直发到群）。
   群里有人发攻击性内容时，这三条会把原话再喊一遍。原来 `is_blacklisted` **只查入站**，
   出站没有任何闸。
2. **重复过滤** —— 同一个群在窗口内不重发**同一句**（治"卡带/复读机"）。
   两条豁免是刻意的：
   · **复读豁免**（`kind="echo"`）：跟着复读本来就是故意重复别人的话，被去重拦住等于关掉这个功能；
   · **短文本豁免**（`outbound_dedup_min_chars`）：`嗯嗯` `？` `哦` 这类应答重复是正常的，
     滤掉会把她变成哑巴。

判据与记账口径：`check()` **批准即记账**（`_recent`）。发送失败时那一句已经记进去了，
表现为"她更保守一点"，这是刻意选的失败方向 —— 反过来（发送成功却没记）会让去重漏掉。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .feedback_classifier import QQFeedbackClassifier

#: 去重指纹：剥掉 CQ 码、协议标签、空白与标点，只留中日韩文字与字母数字。
_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_KEEP_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")


@dataclass(frozen=True)
class OutboundVerdict:
    """出站检查结论。``allowed`` 为假时调用方**必须不发**，并落一条日志。"""

    allowed: bool
    reason: str = ""     # "" | "blacklist" | "duplicate"
    detail: str = ""     # 命中的词 / 上次发送距今秒数

    @property
    def blocked(self) -> bool:
        return not self.allowed


class QQOutboundGuardService:
    """群发出站守门人（私聊不查：私聊是主人自己的对话，误伤的代价更高）。"""

    #: 每个群保留多少条"最近发过"的指纹（配合窗口时间一起用）。
    MAX_RECENT = 40

    def __init__(self, plugin: Any):
        self.plugin = plugin
        #: group_id → [(指纹, 发送时刻)]
        self._recent: dict[str, list[tuple[str, float]]] = {}
        #: 累计拦截计数（给日志/排查用，不做行为开关）。
        self.blocked_count = {"blacklist": 0, "duplicate": 0}
        #: 「词表是空的」只提醒一次。
        #: 真机验收发现：闸在位、词表空 → 它永远拦不住任何东西，而使用者会以为
        #: 安全网开着（线上就是这种状态：`backlog_labels` 里一条 priority<0 都没有）。
        self._warned_empty_blacklist = False

    # ── 配置 ────────────────────────────────────────────────────

    def _setting(self, key: str, default: Any) -> Any:
        settings = getattr(self.plugin, "_qq_settings", None) or {}
        value = settings.get(key)
        return default if value is None else value

    def _enabled(self) -> bool:
        return bool(self._setting("outbound_guard_enabled", True))

    def _blacklist_enabled(self) -> bool:
        return bool(self._setting("outbound_blacklist_enabled", True))

    def _dedup_enabled(self) -> bool:
        return bool(self._setting("outbound_dedup_enabled", True))

    def _dedup_window(self) -> float:
        return max(0.0, float(self._setting("outbound_dedup_window_seconds", 1800) or 0))

    def _dedup_min_chars(self) -> int:
        return max(1, int(self._setting("outbound_dedup_min_chars", 4) or 4))

    @staticmethod
    def fingerprint(text: str) -> str:
        """去重指纹：CQ 码/标签/空白/标点全去掉，只留文字本身。

        这样"收到~"与"收到 ~"、带 `[CQ:reply,id=…]` 前缀的同一条正文都算同一句。
        """
        raw = str(text or "")
        raw = _CQ_RE.sub("", raw)
        raw = _TAG_RE.sub("", raw)
        return "".join(_KEEP_RE.findall(raw))

    # ── 主入口 ──────────────────────────────────────────────────

    def check(self, *, group_id: str, text: str, kind: str = "reply", now: float | None = None) -> OutboundVerdict:
        """发送前的唯一检查点。

        ``kind``：``reply``（正常回复，两条规则都查）/ ``echo``（跟着复读：查黑名单、
        **不查重复**）/ ``bridge``（别的插件原文直发）/ ``forward``（合并转发：查黑名单）。
        ``now``：只给测试注入时钟用；生产走 `time.time()`。
        """
        content = str(text or "")
        if not self._enabled() or not content.strip():
            return OutboundVerdict(True)

        if self._blacklist_enabled():
            labels = list((getattr(self.plugin, "_qq_settings", None) or {}).get("backlog_labels") or [])
            self._warn_if_blacklist_empty(labels)
            try:
                hit = QQFeedbackClassifier.is_blacklisted(content, labels)
            except Exception:  # noqa: BLE001 —— 词表坏掉不能把发送卡死
                hit = False
            if hit:
                self.blocked_count["blacklist"] += 1
                return OutboundVerdict(False, "blacklist", detail=self._matched_word(content, labels))

        if self._dedup_enabled() and kind not in ("echo", "forward"):
            verdict = self._check_duplicate(str(group_id or ""), content, now=now)
            if verdict.blocked:
                self.blocked_count["duplicate"] += 1
                return verdict

        self.note_sent(group_id=str(group_id or ""), text=content, now=now)
        return OutboundVerdict(True)

    def note_sent(self, *, group_id: str, text: str, now: float | None = None) -> None:
        """记下"这句发出去了"（指纹 + 时刻）。窗口外的旧记录顺手清掉。"""
        key = str(group_id or "").strip()
        if not key:
            return
        fp = self.fingerprint(text)
        if not fp:
            return
        ts = float(now if now is not None else time.time())
        rows = self._recent.setdefault(key, [])
        rows.append((fp, ts))
        window = self._dedup_window()
        rows[:] = [row for row in rows if ts - row[1] <= window][-self.MAX_RECENT:]

    def _check_duplicate(self, group_id: str, text: str, *, now: float | None = None) -> OutboundVerdict:
        fp = self.fingerprint(text)
        if len(fp) < self._dedup_min_chars():
            return OutboundVerdict(True)   # 短应答豁免
        ts = float(now if now is not None else time.time())
        window = self._dedup_window()
        for seen_fp, seen_at in reversed(self._recent.get(group_id, [])):
            if ts - seen_at > window:
                break
            if seen_fp == fp:
                return OutboundVerdict(False, "duplicate", detail=f"{int(ts - seen_at)}s 前发过")
        return OutboundVerdict(True)

    def forget(self, group_id: str) -> None:
        self._recent.pop(str(group_id or "").strip(), None)

    # ── 辅助 ────────────────────────────────────────────────────

    @staticmethod
    def blacklist_words(labels: list[dict[str, Any]] | None) -> list[str]:
        """词表里所有黑名单词（`priority < 0` 的标签的 keywords）。"""
        words: list[str] = []
        for label in labels or []:
            if not isinstance(label, dict):
                continue
            try:
                blacklist = int(label.get("priority") or 0) < 0
            except (TypeError, ValueError):
                blacklist = False
            if not blacklist:
                continue
            words.extend(str(w).strip() for w in (label.get("keywords") or []) if str(w).strip())
        return words

    def _warn_if_blacklist_empty(self, labels: list[dict[str, Any]]) -> None:
        """词表为空时提醒一次 —— 否则"闸在位"会被误读成"安全网开着"。

        真机实测（2026-09-27）：线上 `backlog_labels` 只有一条 `mention`（priority=60），
        一条黑名单都没有，于是这个功能在真实群里**永远不会命中**。
        """
        if self._warned_empty_blacklist or self.blacklist_words(labels):
            return
        self._warned_empty_blacklist = True
        try:
            self.plugin.logger.info(
                "[Outbound] 出站内容安全已启用，但关键词表里**没有任何黑名单词**"
                "（priority<0 的标签）—— 现在它不会拦下任何东西；"
                "需要拦的词请加到「关键词标签」页并把优先级填成负数。"
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _matched_word(text: str, labels: list[dict[str, Any]]) -> str:
        """尽力回一个命中的词，仅供日志（命中判定以 classifier 为准）。"""
        lowered = str(text or "").lower()
        best = ""
        for word in QQOutboundGuardService.blacklist_words(labels):
            needle = word.lower()
            if needle in lowered and len(needle) > len(best):
                best = needle
        return best
