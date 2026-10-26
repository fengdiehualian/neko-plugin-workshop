"""群友复读时，猫娘跟着复读**一次**（使用者拍板的规则）。

规则原文（使用者）：

    让她跟着复读一次，只有复读的人大于5并且这个群是焦点的时候才会触发

所以触发要同时满足三条，缺一不可：

1. **同一个群、同一段文本**，窗口内由 **超过 5 个不同的人**发过（>5 ⇒ 至少 6 人；
   按**人**算不按条算 —— 一个人刷 6 条不算复读）
2. 触发那一刻**这个群自己在聊**（注意力关掉 ⇒ 永不触发）。原话是"这个群是焦点"，
   跨群取舍 2026-09-29 删除后读作"本群分数过保持线"（`is_in_conversation`）
3. 这一句**还没跟过**（同一句在同一个群里有冷却，免得群一直刷她就一直跟）

跟的内容是**那句原文**（剥掉 CQ 码与协议标签后逐字发出），不是让模型重写：
"跟着复读"这个行为本身就是照抄，交给模型等于每次都可能变成别的意思。

为什么值得单独一个服务：判定要**按人**去重、要跨消息累积状态，而现有三条路都拿不到
这个信息 —— 注意力按条计分（`attention_service`）、门控只看单条
（`attention_gate_service`）、缓冲只留文本不留发言人（`reply_buffer_service`）。

已验证的现状（同日实测，见 docs/SESSION-HANDOFF.md §4.0v）：不加这个服务时，群里复读
**20 次抽样里她一次都没跟着复读** —— 批量轮她会吐槽（"你是复读机吗喵？"），单条轮多半
只发 `<feeling>bored</feeling>` 走人。所以这是**新增行为**，不是修 bug。
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from utils.llm_client import AIMessage

#: `[CQ:at,qq=…]` 之类：复读时**不能原样发出去**（会变成 @某人 / 发表情 / 发文件）。
_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]*\]")
#: 协议标签（有人复读 `</msg>` 这类文本时，别让它进正文）。
_PROTOCOL_TAG_RE = re.compile(
    r"</?(?:msg|text|feeling|emoji|at|reply|sticker|poke|record|keyboard|ark|mark|forward)"
    r"\b[^>]*>",
    re.IGNORECASE,
)
#: 归一化：只留中日韩文字、拉丁字母与数字，其余（空格/标点/颜文字/表情）全去掉。
#: 这样「一江大气喵」「一江大气喵！」「[CQ:at,qq=1] 一 江大气喵」算同一句。
_NORMALIZE_DROP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff]+")

#: **不是"一句文本"**的消息标记 —— 这类消息没有"原文"可跟：
#:
#: * `[Image …]`：图片消息经 VLM 描述后写进正文的标记（`enrichment._inject_image_descriptions`）
#: * `戳一戳`：戳一戳通知被渲染成的文本
#: * 其余 CQ 段（file/record/forward/video/json/xml/face…）在正文里的残留
#:
#: 为什么必须挡：群里六个人连发**同一张图**是很常见的复读，而图片消息的正文是
#: `[Image 这是mc风格的精致Q版…]`。不挡的话她会把这段内部标记（连同别人的图描述）
#: 当成"复读原文"发进群里 —— 既是垃圾话，也把内部标记漏给了群。
_NON_TEXT_MARKERS = (
    "[image ", "[image]", "[图片", "戳一戳",
    "[cq:file", "[cq:record", "[cq:forward", "[cq:video", "[cq:json", "[cq:xml",
    "[cq:face", "[cq:mface", "[cq:music", "[cq:contact", "[cq:rps", "[cq:dice",
)


class QQRepeatEchoService:
    #: "复读的人大于5" —— 严格大于，所以阈值是 6 个**不同的人**。
    DEFAULT_MIN_SENDERS = 6
    #: 多久之内算"同一轮复读"。群安静超过这个时长后，之前的人头不再计入。
    DEFAULT_WINDOW_SECONDS = 180.0
    #: 同一句在同一个群里的冷却：跟过一次之后，这段时间内不再跟。
    DEFAULT_COOLDOWN_SECONDS = 600.0
    #: 太长的文本不当复读跟（6 个人同时复读一段长文的概率极低，而误跟的代价是
    #: 把一大段别人写的东西以猫娘的名义广播出去）。
    DEFAULT_MAX_TEXT_CHARS = 40
    #: 状态上限：群 × 文本 的组合不可能无限增长，超了就丢最旧的。
    MAX_EPISODES = 200

    def __init__(self, plugin: Any):
        self.plugin = plugin
        # (group_id, normalized_text) -> {"senders": set, "last_at": float,
        #                                 "echoed_at": float, "text": str}
        self._episodes: dict[tuple[str, str], dict[str, Any]] = {}
        #: 判定时"先占坑"记下的上一次 echoed_at：发送失败要还原回去
        #: （否则一次网络抖动就把这一轮跟的机会吃掉）。
        self._reservations: dict[tuple[str, str], float] = {}

    # ── 配置 ────────────────────────────────────────────────────

    def _setting(self, key: str, default: Any) -> Any:
        value = (getattr(self.plugin, "_qq_settings", {}) or {}).get(key)
        return default if value is None else value

    @property
    def min_senders(self) -> int:
        return max(1, int(self._setting("repeat_echo_min_senders", self.DEFAULT_MIN_SENDERS)))

    @property
    def window_seconds(self) -> float:
        return max(1.0, float(self._setting("repeat_echo_window_seconds", self.DEFAULT_WINDOW_SECONDS)))

    @property
    def cooldown_seconds(self) -> float:
        return max(0.0, float(self._setting("repeat_echo_cooldown_seconds", self.DEFAULT_COOLDOWN_SECONDS)))

    @property
    def max_text_chars(self) -> int:
        return max(1, int(self._setting("repeat_echo_max_text_chars", self.DEFAULT_MAX_TEXT_CHARS)))

    # ── 文本处理 ────────────────────────────────────────────────

    @classmethod
    def is_repeatable_text(cls, text: Any) -> bool:
        """这条消息是不是"一句可以跟着复读的文本"（排除图片/戳一戳/文件等）。"""
        raw = str(text or "")
        if not raw.strip():
            return False
        lowered = raw.casefold()
        if any(marker in lowered for marker in _NON_TEXT_MARKERS):
            return False
        # 只剩 CQ 段的消息（例如纯图片无描述、纯文件）：剥完就没内容了
        return bool(cls.normalize(raw))

    @classmethod
    def sanitize(cls, text: Any) -> str:
        """把一条群消息整理成"可以原样发出去"的文本。"""
        raw = str(text or "")
        raw = _CQ_CODE_RE.sub("", raw)
        raw = _PROTOCOL_TAG_RE.sub("", raw)
        return " ".join(raw.split()).strip()

    @classmethod
    def normalize(cls, text: Any) -> str:
        """判重用的形态：剥掉 CQ/标签后再去掉空格、标点、表情。"""
        return _NORMALIZE_DROP_RE.sub("", cls.sanitize(text).casefold())

    # ── 「这个群在聊」判定 ──────────────────────────────────────

    def _is_active_group(self, group_id: str) -> bool:
        """"这个群在聊" == 注意力开着 且 **本群自己的分数**过了保持线。

        2026-09-29 之前这里是"当前全局焦点群就是它"（`attention.get_focus_group()`）。
        跨群取舍删除后没有唯一焦点，判据换成每群各自的 `is_in_conversation()` ——
        语义仍是使用者原话里那句"这个群是焦点的时候"，只是"焦点"不再由别的群决定。

        注意力关掉时这个判据不成立（门控那边也是直接放行），所以**不触发**。
        """
        attention = getattr(self.plugin, "attention_service", None)
        if attention is None:
            return False
        try:
            if not attention._enabled():
                return False
            return bool(attention.is_in_conversation(str(group_id or "").strip()))
        except Exception as exc:  # noqa: BLE001
            self.plugin.logger.warning(f"[Repeat] 判本群在不在聊失败: {exc}")
            return False

    # ── 判定 ────────────────────────────────────────────────────

    def _prune(self, now: float) -> None:
        ttl = max(self.window_seconds, self.cooldown_seconds) + 60.0
        stale = [key for key, ep in self._episodes.items() if now - float(ep["last_at"]) > ttl]
        for key in stale:
            self._episodes.pop(key, None)
        if len(self._episodes) > self.MAX_EPISODES:
            ordered = sorted(self._episodes.items(), key=lambda kv: float(kv[1]["last_at"]))
            for key, _ep in ordered[: len(self._episodes) - self.MAX_EPISODES]:
                self._episodes.pop(key, None)

    def observe(
        self, *, group_id: str, sender_id: str, text: Any, now: Optional[float] = None,
    ) -> Optional[str]:
        """记一条群消息；返回"这一次该跟着复读的原文"，不该跟就返回 ``None``。

        注意返回值是**能直接发出去的原文**（CQ 码已剥），而判重用的是归一化文本。
        """
        group = str(group_id or "").strip()
        sender = str(sender_id or "").strip()
        if not group or not sender:
            return None
        if not self.is_repeatable_text(text):
            return None
        clean = self.sanitize(text)
        normalized = self.normalize(text)
        if not normalized or not clean or len(clean) > self.max_text_chars:
            return None

        now = time.time() if now is None else float(now)
        self._prune(now)

        key = (group, normalized)
        episode = self._episodes.get(key)
        if episode is None or now - float(episode["last_at"]) > self.window_seconds:
            # 新的一轮（或群安静太久）：人头重数，但**冷却保留** —— 否则同一句
            # 复读每隔几分钟就能再骗她跟一次。
            episode = {
                "senders": set(),
                "last_at": now,
                "echoed_at": float(episode["echoed_at"]) if episode else 0.0,
                "text": clean,
            }
            self._episodes[key] = episode
        episode["senders"].add(sender)
        episode["last_at"] = now

        if len(episode["senders"]) < self.min_senders:
            return None
        if not self._is_active_group(group):
            return None
        if now - float(episode["echoed_at"]) < self.cooldown_seconds:
            return None
        # 先占坑再返回：同一轮里并发/重复调用不该都拿到"跟"（调用点在入站
        # handler 里，会话锁之外仍有窗口）。失败了由 release_echo 还原。
        self._reservations[key] = float(episode["echoed_at"])
        episode["echoed_at"] = now
        # 跟的是**这一轮复读的第一句**的原文，不是最后那句的标点变体 ——
        # 同一句的「！」「～」写法算同一句，跟哪一份得是确定的。
        return str(episode["text"])

    def should_skip_batch_summary(
        self, *, group_id: str, texts: list[str], now: Optional[float] = None,
    ) -> bool:
        """这一批缓冲是不是"刚刚已经跟过的复读"？

        是的话缓冲不必再总结一条 —— 否则群里会连着看到两条互相矛盾的回复：
        她刚跟着复读了一句，紧接着又吐槽"你是复读机吗喵？"。

        判据要求**整批都是同一句**（混了别的内容就得正常总结），且这一句在冷却期内
        刚被她跟过（冷却过了就是新一轮，那批该照常总结；同一个群里隔一阵子又刷同一句
        时她要么再跟一次，要么正常回一句，不该整批沉掉）。
        """
        group = str(group_id or "").strip()
        if not group or not texts:
            return False
        normalized = {self.normalize(t) for t in texts}
        if len(normalized) != 1:
            return False
        only = next(iter(normalized))
        if not only:
            return False
        episode = self._episodes.get((group, only))
        if episode is None:
            return False
        echoed_at = float(episode["echoed_at"])
        if echoed_at <= 0.0:
            return False
        moment = time.time() if now is None else float(now)
        return 0.0 <= moment - echoed_at < self.cooldown_seconds

    def mark_echoed(
        self, *, group_id: str, text: Any, now: Optional[float] = None,
    ) -> None:
        """发送成功：占坑转正（冷却在 `observe` 判定时就已经落下）。"""
        key = (str(group_id or "").strip(), self.normalize(text))
        self._reservations.pop(key, None)

    def release_echo(self, *, group_id: str, text: Any) -> None:
        """发送失败：把 `echoed_at` 还原成占坑前的值，下一次复读还有机会跟上。"""
        key = (str(group_id or "").strip(), self.normalize(text))
        previous = self._reservations.pop(key, None)
        if previous is None:
            return
        episode = self._episodes.get(key)
        if episode is not None:
            episode["echoed_at"] = previous

    # ── 发送 ────────────────────────────────────────────────────

    async def maybe_echo(self, *, group_id: str, sender_id: str, text: Any) -> Optional[str]:
        """入站消息的钩子：该跟就跟着复读一次，返回实际发出去的文本。"""
        clean = self.observe(group_id=group_id, sender_id=sender_id, text=text)
        if not clean:
            return None
        try:
            sent = await self._send(group_id=group_id, text=clean)
        except Exception as exc:  # noqa: BLE001
            # 发送失败不能把消息处理带崩（调用点在入站 handler 里），也不落冷却。
            self.release_echo(group_id=group_id, text=clean)
            self.plugin.logger.warning(f"[Repeat] 跟着复读失败: {type(exc).__name__}: {exc}")
            self.plugin._emit_log("WARN", f"[Repeat] 跟着复读失败: {exc}")
            return None
        if sent:
            self.mark_echoed(group_id=group_id, text=clean)
        else:
            self.release_echo(group_id=group_id, text=clean)
        return clean if sent else None

    async def _send(self, *, group_id: str, text: str) -> bool:
        plugin = self.plugin
        plugin._ensure_qq_client_connected()
        group = plugin._validate_group_id(group_id)
        message = plugin._validate_outbound_message(text)
        episode_key = (group, self.normalize(message))
        senders = len((self._episodes.get(episode_key) or {}).get("senders") or ())
        # 出站守门：复读是**逐字把别人的话再喊一遍**，所以内容安全这一道必须过；
        # 但重复过滤要豁免（跟读本身就是故意重复，去重会把这个功能整个关掉）。
        guard = getattr(plugin, "outbound_guard_service", None)
        if guard is not None:
            verdict = guard.check(group_id=group, text=message, kind="echo")
            if verdict.blocked:
                plugin.logger.warning(
                    f"[Repeat] 复读被出站守门拦下（{verdict.reason}: {verdict.detail}）: {message[:30]!r}"
                )
                plugin._emit_log("WARN", f"[Repeat] 复读内容被拦截（{verdict.reason}）")
                return False
        mid = await plugin.qq_client.send_group_message(group, message)
        self._append_history_row(group, message)
        await self._bookkeep_reply(group)
        plugin.logger.info(
            f"[Repeat] 群 {group} 有 {senders} 人复读同一句，跟着复读一次: {message[:30]!r} mid={mid}"
        )
        plugin._emit_log(
            "INFO", f"[Repeat] {senders} 人在复读，跟着复读一次: {message[:20]}"
        )
        return True

    def _append_history_row(self, group_id: str, message: str) -> None:
        """把这一句记进群会话历史 —— 她得"记得"自己跟了一句。

        调用点在 `handle_group_message` 里，**那一刻会话锁已经持有**
        （见 `reply_buffer_service` 里"handler 已持本会话锁，重取会自锁死"的注释），
        所以这里直接追加，不再取锁。
        """
        sessions = getattr(self.plugin, "_user_sessions", None) or {}
        entry = sessions.get(f"group:{group_id}")
        session = entry.get("session") if isinstance(entry, dict) else None
        history = getattr(session, "_conversation_history", None)
        if isinstance(history, list):
            history.append(AIMessage(content=message))

    async def _bookkeep_reply(self, group_id: str) -> None:
        """跟一次也算一次回复：消耗注意力 + 记进回复频率窗口。"""
        qq_client = getattr(self.plugin, "qq_client", None)
        gate = getattr(self.plugin, "attention_gate_service", None)
        if gate is None or qq_client is None or not qq_client.needs_attention:
            return
        try:
            await gate.on_reply_sent(group_id)
        except Exception as exc:  # noqa: BLE001
            self.plugin.logger.warning(f"[Repeat] 回复后注意力簿记失败: {exc}")
