# -*- coding: utf-8 -*-
"""管理员私聊里的「有由头」主动搭话（A 方案，2026-10-02）。

## 为什么是「有由头」

使用者前后两次口径：

* 2026-09-29 删掉**主动破冰**：「干脆不要这个先」。它的判据是「群静默 1800 秒」——
  拿钟表当"该说话了"的理由，使用者明确否决了这种判据。
* 2026-10-02 又想做主动搭话，指定「**主要针对管理员的私聊**的主动」，并在三个候选里
  选了 **A = 有由头**：只在她**自己说过要交代**某件事时才开口。

所以本服务只做一件事：把她私聊里「晚点告诉你 / 明天给你看 / 等我查一下」这类**承诺**
记成待办；到期后（且不在静音时段、没超每日上限、间隔够）把**她当时那句话**当由头交回给
她，由她自己决定要不要主动找他、说什么。

## 时间在这里只做两件不是"理由"的事

1. **到期**：她说的「等一下」和「明天」不是同一个时刻，各自等到期；
2. **刹车**：静音时段、每日上限、最小间隔 —— 全是限制，没有一个是触发。

触发永远是「**她说过那句、且还没交代**」。

## 边界

* 只到**管理员私聊**（`plugin._admin_qq`），群聊不走这条路。
* 不扫描合成轮 / 她自己的主动发言（`SYNTHETIC_SOURCE_KINDS`），否则那条由头会自我续期，
  变成"她为了告诉你而承诺再告诉你"。
* **她可以拒绝**：那一轮走 `ephemeral_session=True`，空回复在
  `reply_postprocess_node` 的 empty 分支就变成"不发"，于是"没什么可说的"就等于不说。
* 落盘走 `plugin.data_path(...)`：宿主重启后还没交代的事接着等。写盘是"尽力而为"，
  失败只留 WARNING —— 一条待办丢了，不该把回复链路掀翻。

## 与已删除的主动破冰的关系

名字、键名、判据全都不同（`proactive_initiative_*` vs `icebreaker_*`），删除契约
（`test_qq_settings_schema.test_icebreaker_family_is_gone`、
`test_qq_per_group_maintenance.test_maintenance_tick_does_not_touch_icebreaker_state`）
一条都没动，仍然成立。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

from .pipeline_models import SYNTHETIC_SOURCE_KINDS

#: 落盘文件名（走 `plugin.data_path`）。宿主持久化里"还没交代的事"要能接着等。
STATE_FILE = "proactive_initiative.json"

#: 同一条由头最多试几次（每次失败退避）。别为一条说不出口的话刷日志。
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 900

#: 他刚跟她说过话之后的静默窗口：那会儿她本来就有正常回合，别抢在自己前面插话。
ACTIVITY_GRACE_SECONDS = 600

#: 待办最长存活时间（到期后再等这么久还没说成，就当这件事过去了）。
MAX_ITEM_AGE_SECONDS = 7 * 24 * 3600

#: 由头原文截断长度（提示词里引用她自己的话，太长没意义）。
QUOTE_MAX_CHARS = 120

#: 承诺里的**时间词 → 到期秒数**。命中多个取**最晚**那个：
#: 「明天晚点告诉你」该等到明天，不是半小时后。
_TIME_WORDS: tuple[tuple[str, int], ...] = (
    ("马上", 20 * 60),
    ("等我", 30 * 60),
    ("等一下", 30 * 60),
    ("等一会", 30 * 60),
    ("等会儿", 30 * 60),
    ("待会儿", 30 * 60),
    ("一会儿", 30 * 60),
    ("稍后", 60 * 60),
    ("回头", 2 * 3600),
    ("晚点", 3 * 3600),
    ("今晚", 4 * 3600),
    ("明早", 14 * 3600),
    ("明天", 20 * 3600),
    ("明晚", 24 * 3600),
    ("改天", 24 * 3600),
    ("下次", 24 * 3600),
    ("下回", 24 * 3600),
)

#: 光有时间词**不够**：「等一下，我不是那个意思」不是承诺。必须是"交代/告知/交付"类动词。
_TELL_WORDS: tuple[str, ...] = (
    "告诉", "说一声", "和你说", "跟你说", "给你说", "发你", "给你发", "给你看",
    "拿给你", "给你问", "帮你查", "查一下", "查查", "问一下", "问问", "弄好",
    "弄完", "搞好", "整理好", "写完", "做好", "结果", "答案", "结论",
)

#: 句子边界。承诺通常落在**一个分句**里，所以按强标点切；逗号也切一刀，
#: 免得"嗯嗯，我晚点告诉你"把前面那句也当成由头。
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n\r]+|(?<=[，,])")


def detect_promise(text: str) -> dict[str, Any] | None:
    """从她的一句话里认出「我稍后会交代某件事」的承诺。

    返回 ``{"quote": 原话分句, "due_seconds": 到期秒数}``；认不出返回 ``None``。
    纯函数（不碰时间、不碰状态），所以既能被服务调用，也能被测试直接钉。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    for clause in _SENTENCE_SPLIT.split(raw):
        piece = clause.strip(" \t，,。！？!?；;、~…")
        if not piece:
            continue
        if not any(word in piece for word in _TELL_WORDS):
            continue
        due = [seconds for word, seconds in _TIME_WORDS if word in piece]
        if not due:
            continue
        quote = piece if len(piece) <= QUOTE_MAX_CHARS else piece[:QUOTE_MAX_CHARS] + "…"
        return {"quote": quote, "due_seconds": max(due)}
    return None


class QQProactiveInitiativeService:
    """管理员私聊的「有由头」主动搭话：登记承诺、到期说一句、其余时间闭嘴。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._logger = getattr(plugin, "logger", None)
        #: 管理员 → 待交代的那件事（同一个人只留最新一条，避免堆成任务清单）
        self._items: dict[str, dict[str, Any]] = {}
        #: 当天已开口次数（按本地日期翻篇）
        self._today: str = ""
        self._today_count: int = 0
        self._last_sent_at: float = 0.0
        #: 他最近一次跟她说话的**回复时刻**（代理"他刚说完、她刚回过"）
        self._last_admin_activity_at: float = 0.0
        self._load()

    # ── 设置 ────────────────────────────────────────────────────────

    def _settings(self) -> dict[str, Any]:
        return getattr(self.plugin, "_qq_settings", {}) or {}

    def _setting(self, key: str, default: Any) -> Any:
        value = self._settings().get(key)
        return default if value is None else value

    def enabled(self) -> bool:
        return bool(self._setting("proactive_initiative_enabled", False))

    def quiet_hours(self) -> tuple[int, int]:
        """静音时段 ``(起, 止)``，按整点小时。起止相同 = 没有静音时段。"""
        try:
            start = int(self._setting("proactive_initiative_quiet_start_hour", 23)) % 24
            end = int(self._setting("proactive_initiative_quiet_end_hour", 8)) % 24
        except (TypeError, ValueError):
            return (23, 8)
        return (start, end)

    def max_per_day(self) -> int:
        try:
            return max(0, int(self._setting("proactive_initiative_max_per_day", 2)))
        except (TypeError, ValueError):
            return 2

    def min_gap_seconds(self) -> int:
        try:
            return max(0, int(self._setting("proactive_initiative_min_gap_seconds", 10800)))
        except (TypeError, ValueError):
            return 10800

    def _admin(self) -> str:
        return str(getattr(self.plugin, "_admin_qq", "") or "").strip()

    # ── 登记（一轮结束后）──────────────────────────────────────────

    def note_turn(self, *, source: str, request: Any, outcome: Any) -> None:
        """登记入口：她这一轮说了什么、有没有留下"晚点告诉你"。

        **best effort** —— 由回复链路在收尾时调用，任何异常都不许影响那一轮。
        """
        try:
            self._note_turn_inner(source=source, request=request, outcome=outcome)
        except Exception as exc:  # noqa: BLE001 - 见 docstring
            self._warn(f"[Initiative] 登记失败（不影响本轮回复）: {exc}")

    def _note_turn_inner(self, *, source: str, request: Any, outcome: Any) -> None:
        if str(source or "") in SYNTHETIC_SOURCE_KINDS:
            # 合成轮（她的主动发言 / 回投 / 快速补发…）不参与：否则"为了告诉你而再承诺"
            return
        if bool(getattr(request, "is_group", False)):
            return
        admin = self._admin()
        if not admin:
            return
        if str(getattr(request, "sender_id", "") or "").strip() != admin:
            return
        now = time.time()
        changed = False
        # 他刚跟她说过话 —— 这一刻她本来就在正常回复，别在外面插一句。
        # 这个时刻要一起落盘（见 `_load`）：重启后清了它，宽限就失效了。
        if now - self._last_admin_activity_at > 1.0:
            self._last_admin_activity_at = now
            changed = True
        text = str(getattr(outcome, "reply_text", "") or "")
        promise = detect_promise(text) if text else None
        if promise:
            self._items[admin] = {
                "quote": promise["quote"],
                "created_at": now,
                "due_at": now + float(promise["due_seconds"]),
                "attempts": 0,
                "last_attempt_at": 0.0,
            }
            changed = True
            self._log(
                "INFO",
                f"[Initiative] 记下一条由头（{int(promise['due_seconds'] // 60)} 分钟后到期）: "
                f"{promise['quote'][:40]}",
            )
        if changed:
            self._save()

    # ── 判定 ────────────────────────────────────────────────────────

    def _count_today(self, now: float) -> int:
        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        return self._today_count if today == self._today else 0

    def _within_active_hours(self, now: float) -> bool:
        start, end = self.quiet_hours()
        if start == end:
            return True
        hour = datetime.fromtimestamp(now).hour
        if start < end:
            return not (start <= hour < end)
        # 跨零点：23 → 8 表示 23,0..7 点都静音
        return not (hour >= start or hour < end)

    def pending(self) -> dict[str, Any] | None:
        """当前待交代的那件事（界面与我自己的 tick 共用一份读法）。"""
        item = self._items.get(self._admin())
        return dict(item) if item else None

    def blocked_reason(self, now: float | None = None) -> str:
        """此刻为什么还不会开口（空串 = 该开口了）。给日志与界面用，判据与 tick 同源。"""
        if not self.enabled():
            return "开关关着"
        if not bool(getattr(self.plugin, "_running", False)):
            return "值班已停"
        item = self.pending()
        if not item:
            return "还没有由头"
        now = time.time() if now is None else now
        if now < float(item.get("due_at") or 0):
            return "还没到期"
        if now - float(item.get("created_at") or 0) > MAX_ITEM_AGE_SECONDS:
            return "这件事过去太久了"
        if not self._within_active_hours(now):
            return "静音时段"
        if self._count_today(now) >= self.max_per_day():
            return "今天已经说够了"
        if now - self._last_sent_at < self.min_gap_seconds():
            return "距上次太近"
        if now - self._last_admin_activity_at < ACTIVITY_GRACE_SECONDS:
            return "刚跟她说过话"
        if int(item.get("attempts") or 0) >= MAX_ATTEMPTS:
            return "试过太多次"
        if now - float(item.get("last_attempt_at") or 0) < RETRY_BACKOFF_SECONDS:
            return "等下一轮再试"
        return ""

    # ── 开口 ────────────────────────────────────────────────────────

    def build_prompt(self, item: dict[str, Any]) -> str:
        """把由头交回给她。**由头必须进提示词** —— 当年破冰冰冰冰开口没后续，就是缺这个。"""
        quote = str(item.get("quote") or "").strip()
        return (
            f"[系统] 你之前跟主人说过：「{quote}」—— 这件事你还没交代完。\n"
            "现在可以去跟他说清楚：一两句、说人话，别自称在完成任务，也别编新的承诺。\n"
            "如果这件事已经不需要再说，或者你现在不想说，就什么都不要回（空回复不会发出去）。"
        )

    async def tick(self) -> None:
        """维护循环每轮调一次：该开口就开口。所有异常就地吞掉（同维护循环的其它 tick）。"""
        try:
            await self._tick_inner()
        except Exception as exc:  # noqa: BLE001 - 一条主动消息不值得杀掉整个循环
            self._warn(f"[Initiative] 本轮跳过: {exc}")

    async def _tick_inner(self) -> None:
        now = time.time()
        reason = self.blocked_reason(now)
        if reason:
            # 到期而"过去太久"的由头顺手清掉，别一直挡着下一条。
            if reason == "这件事过去太久了":
                self._drop("过期")
            return
        admin = self._admin()
        item = self.pending() or {}
        attempts = int(item.get("attempts") or 0) + 1
        # 先记账再发：宁可少一条，也不为同一条由头连发两条。
        stored = self._items.get(admin)
        if stored is not None:
            stored["attempts"] = attempts
            stored["last_attempt_at"] = now
        self._save()
        self._log("INFO", f"[Initiative] 主动开口（第 {attempts} 次）由头: {str(item.get('quote') or '')[:40]}")

        from plugin.sdk.plugin import Ok

        try:
            result = await self.plugin.proactive_message_service.send_private_message(
                target=admin,
                message=self.build_prompt(item),
            )
        except Exception as exc:  # noqa: BLE001
            self._warn(f"[Initiative] 发送异常，留着下次再试: {exc}")
            return
        if isinstance(result, Ok):
            self._items.pop(admin, None)
            self._last_sent_at = now
            self._bump_today(now)
            self._save()
            self._log("INFO", "[Initiative] 已开口并结清这条由头")
            return
        # Err：要么她选择不说（ephemeral 轮的空回复 → GENERATE_FAILED），要么发送失败。
        # 两种情况都**不留着反复试**：拖着一条说不出口的话只会变成骚扰。
        detail = str(getattr(result, "error", "") or result)
        if "GENERATE_FAILED" in detail:
            self._items.pop(admin, None)
            self._save()
            self._log("INFO", "[Initiative] 她说没什么可说的，这条由头作废")
            return
        self._warn(f"[Initiative] 没发出去（第 {attempts} 次）: {detail[:160]}")

    # ── 界面快照 ────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        item = self.pending() or {}
        start, end = self.quiet_hours()
        return {
            "enabled": self.enabled(),
            "admin": self._admin(),
            "has_admin": bool(self._admin()),
            "pending": (
                {
                    "quote": item.get("quote") or "",
                    "due_at": float(item.get("due_at") or 0),
                    "created_at": float(item.get("created_at") or 0),
                    "attempts": int(item.get("attempts") or 0),
                }
                if item
                else None
            ),
            "blocked_reason": self.blocked_reason(now),
            "sent_today": self._count_today(now),
            "max_per_day": self.max_per_day(),
            "min_gap_seconds": self.min_gap_seconds(),
            "quiet_start_hour": start,
            "quiet_end_hour": end,
            "last_sent_at": self._last_sent_at,
            "now": now,
        }

    # ── 落盘 ────────────────────────────────────────────────────────

    def _state_path(self) -> Any:
        return self.plugin.data_path(STATE_FILE)

    def _load(self) -> None:
        try:
            path = self._state_path()
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - 坏文件只丢待办，不影响启动
            self._warn(f"[Initiative] 状态读取失败，按空处理: {exc}")
            return
        items = data.get("items")
        if isinstance(items, dict):
            self._items = {
                str(k): dict(v) for k, v in items.items() if isinstance(v, dict) and v.get("quote")
            }
        self._today = str(data.get("today") or "")
        try:
            self._today_count = max(0, int(data.get("today_count") or 0))
            self._last_sent_at = float(data.get("last_sent_at") or 0.0)
            # 「他最近一次说话」也要落盘：否则停机/重启会把它清成 0，于是"刚跟她说过话"
            # 这条宽限失效 —— 重启后第一轮就可能把一条旧由头抖出来。
            self._last_admin_activity_at = float(data.get("last_admin_activity_at") or 0.0)
        except (TypeError, ValueError):
            self._today_count = 0
            self._last_sent_at = 0.0
            self._last_admin_activity_at = 0.0

    def _save(self) -> None:
        try:
            path = self._state_path()
            path.write_text(
                json.dumps(
                    {
                        "items": self._items,
                        "today": self._today,
                        "today_count": self._today_count,
                        "last_sent_at": self._last_sent_at,
                        "last_admin_activity_at": self._last_admin_activity_at,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 - 落盘失败不该掀翻回复链路
            self._warn(f"[Initiative] 状态写盘失败: {exc}")

    def _bump_today(self, now: float) -> None:
        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        if today != self._today:
            self._today = today
            self._today_count = 0
        self._today_count += 1

    def _drop(self, reason: str) -> None:
        admin = self._admin()
        if self._items.pop(admin, None) is not None:
            self._save()
            self._log("INFO", f"[Initiative] 丢掉一条由头：{reason}")

    # ── 日志 ────────────────────────────────────────────────────────

    def _log(self, level: str, message: str) -> None:
        emit = getattr(self.plugin, "_emit_log", None)
        if callable(emit):
            try:
                emit(level, message)
                return
            except Exception:  # noqa: BLE001
                pass
        self._warn(message)

    def _warn(self, message: str) -> None:
        if self._logger is not None:
            self._logger.warning(message)
