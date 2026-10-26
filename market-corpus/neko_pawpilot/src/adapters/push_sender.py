"""推送调度：respond（人设归宿主 LLM）与 blind（直出气泡）双通道。

**优先级与排队（2026-10 主人定调："紧急先走，闲聊排队"）**：
- `critical` 立即发，并把队列里的闲聊全部丢掉，之后 60 秒内不再插闲聊
  （事故/翻车/熄火/紧急气压 —— 这些时候主人没空听电台）；
- `important` 有内容的事件（任务分段、到货结算、加油判断、机务告警…）：
  车道上一条刚发过就排队等位，不会和上一条挤在一起；
- `chat` 陪玩维度/电台/途经地点/风景识屏 —— 一律排队，车道空了才说。

任何一个通道都不会丢弃重要内容：排队项按优先级取最高的一条，超时才作废。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

CRITICAL = "critical"
IMPORTANT = "important"
CHAT = "chat"

_ORDER = {CRITICAL: 0, IMPORTANT: 1, CHAT: 2}


class PushSender:
    """封装宿主推送通道 + 优先级排队。"""

    CRITICAL = CRITICAL
    IMPORTANT = IMPORTANT
    CHAT = CHAT

    def __init__(self, plugin: Any, dry_run: bool = True, on_push: Any = None,
                 gap_important: float = 6.0, gap_chat: float = 45.0,
                 mute_after_critical: float = 60.0, queue_max: int = 8,
                 topic_gap: float = 300.0) -> None:
        self.plugin = plugin
        self.dry_run = dry_run
        self._on_push = on_push
        self.gap_important = gap_important
        self.gap_chat = gap_chat
        self.mute_after_critical = mute_after_critical
        self.queue_max = max(1, int(queue_max))
        self.topic_gap = max(0.0, float(topic_gap))
        self._queue: List[Dict[str, Any]] = []
        self._last_sent = 0.0
        self._chat_mute_until = 0.0
        self._topic_last: Dict[str, float] = {}
        self.dropped = 0
        self.recent: List[Dict[str, Any]] = []   # 陪玩时间线（最近 30 条，含被拦原因）

    def _record(self, status: str, priority: str, kind: str, topic: str, text: str) -> None:
        """把每次"决定"记进时间线：说了 / 排队 / 被去重 / 静默期。

        为什么要记被拦的：主人问过"她怎么不说话"，而日志里只能看到说了什么。
        """
        self.recent.insert(0, {
            "ts": round(time.time(), 1),
            "status": status,
            "priority": priority,
            "kind": kind,
            "topic": topic or "",
            "text": " ".join(str(text or "").split())[:70],
        })
        del self.recent[30:]

    # ── 跨模块话题去重 ──
    # 电台、途经地点、陪玩维度、画面识景分属不同模块，很容易各说一遍同一件事
    # （比如"这段路风景真好"被 scenery / 电台 / 识屏各说一次）。所以每个模块
    # 给自己说的话打个 topic，同一个 topic 在 topic_gap 内只放行一次。
    def topic_blocked(self, topic: str, now: Optional[float] = None) -> bool:
        if not topic or self.topic_gap <= 0:
            return False
        now = now or time.time()
        return (now - self._topic_last.get(topic, -1e9)) < self.topic_gap

    def mark_topic(self, topic: str, now: Optional[float] = None) -> None:
        if topic:
            self._topic_last[topic] = now or time.time()

    def snapshot(self) -> Dict[str, Any]:
        return {"pending": len(self._queue), "dropped": self.dropped,
                "chat_mute_s": max(0.0, round(self._chat_mute_until - time.time())),
                "topics": len(self._topic_last),
                "recent": list(self.recent[:20])}

    # ── 日志/回调 ──
    def _log(self, level: str, msg: str, *args: Any) -> None:
        try:
            logger = getattr(self.plugin, "logger", None)
            if logger is not None:
                getattr(logger, level)(msg, *args)
        except Exception:                    # noqa: BLE001
            pass

    def _note(self, kind: str, text: str, priority: str) -> None:
        head = " ".join(str(text or "").split())[:100]
        self._log("info", "[推送·%s/%s] %s", priority, kind, head)
        if self._on_push is not None:
            try:
                self._on_push()
            except Exception:                # noqa: BLE001
                pass

    # ── 车道 ──
    def gap_for(self, priority: str) -> float:
        if priority == CRITICAL:
            return 0.0
        return self.gap_important if priority == IMPORTANT else self.gap_chat

    def lane_free(self, priority: str, now: Optional[float] = None) -> bool:
        now = now or time.time()
        if priority == CHAT and now < self._chat_mute_until:
            return False
        return (now - self._last_sent) >= self.gap_for(priority)

    def pending(self) -> int:
        return len(self._queue)

    def _enqueue(self, text: str, kind: str, priority: str, ttl: float,
                 lanlan: str = "", topic: str = "") -> None:
        self._queue.append({"text": text, "kind": kind, "priority": priority,
                            "ts": time.time(), "ttl": max(10.0, ttl),
                            "lanlan": lanlan, "topic": topic})
        # 队列按 (优先级, 入队时间) 排序，超额时丢最不重要的最旧项
        self._queue.sort(key=lambda q: (_ORDER.get(q["priority"], 9), q["ts"]))
        if len(self._queue) > self.queue_max:
            self.dropped += 1
            self._queue.pop()

    async def _transmit(self, text: str, kind: str, priority: str,
                        lanlan: str = "") -> bool:
        if self.dry_run:
            self._log("info", "[dry_run] %s: %s", kind, text)
            return True
        try:
            if kind == "respond":
                result = self.plugin.push_message(
                    source="neko_pawpilot", visibility=[], ai_behavior="respond",
                    parts=[{"type": "text", "text": text}], priority=5,
                    target_lanlan=lanlan)
            else:
                result = self.plugin.push_message(
                    source="neko_pawpilot", visibility=["chat"], ai_behavior="blind",
                    parts=[{"type": "text", "text": text}], priority=5)
            ok = bool(result and result.get("submitted"))
            if ok:
                self._last_sent = time.time()
                self._note(kind, text, priority)
            else:
                self._log("warning", "[推送·%s] 宿主未接收: %s", priority,
                          " ".join(str(text).split())[:80])
            return ok
        except Exception as exc:             # noqa: BLE001
            self._log("warning", "%s push failed: %s", kind, exc)
            return False

    # ── 对外接口 ──
    async def say(self, text: str, priority: str = IMPORTANT, kind: str = "respond",
                  ttl: float = 240.0, lanlan: str = "", topic: str = "") -> bool:
        """统一入口：按优先级决定立刻发还是排队；topic 用于跨模块去重。"""
        now = time.time()
        if topic and priority != CRITICAL and self.topic_blocked(topic, now):
            self.dropped += 1
            self._log("info", "[推送·去重] topic=%s 已说过，本轮跳过", topic)
            self._record("dropped_topic", priority, kind, topic, text)
            return False
        if priority == CRITICAL:
            # 紧急先走：丢掉排队的闲聊，并让闲聊安静一会儿
            self._queue = [q for q in self._queue if q["priority"] != CHAT]
            self._chat_mute_until = now + self.mute_after_critical
            ok = await self._transmit(text, kind, priority, lanlan)
            self._record("sent" if ok else "failed", priority, kind, topic, text)
            if ok:
                self.mark_topic(topic, now)
            return ok
        if priority == CHAT and now < self._chat_mute_until:
            self._record("muted", priority, kind, topic, text)
            return False                     # 紧急善后期，闲聊不插嘴
        if self.lane_free(priority, now) and not self._queue:
            ok = await self._transmit(text, kind, priority, lanlan)
            self._record("sent" if ok else "failed", priority, kind, topic, text)
            if ok:
                self.mark_topic(topic, now)
            return ok
        self._enqueue(text, kind, priority, ttl, lanlan, topic)
        self._record("queued", priority, kind, topic, text)
        return False

    async def drain(self) -> bool:
        """车道上一条够久远时，放行队列里优先级最高的一条。只发一条。"""
        now = time.time()
        if self._queue:
            self._queue = [q for q in self._queue if now - q["ts"] <= q["ttl"]]
        if not self._queue:
            return False
        head = self._queue[0]
        if not self.lane_free(head["priority"], now):
            return False
        if self.topic_blocked(head.get("topic", ""), now):
            self._queue.pop(0)
            self.dropped += 1
            self._record("dropped_topic", head["priority"], head["kind"],
                         head.get("topic", ""), head["text"])
            return False
        self._queue.pop(0)
        ok = await self._transmit(head["text"], head["kind"], head["priority"],
                                  head.get("lanlan", ""))
        self._record("sent" if ok else "failed", head["priority"], head["kind"],
                     head.get("topic", ""), head["text"])
        if ok:
            self.mark_topic(head.get("topic", ""), now)
        return ok

    async def push_fact(self, fact: str, target_lanlan: str = "",
                        priority: str = IMPORTANT, ttl: float = 240.0,
                        topic: str = "") -> bool:
        """respond 模式：事实交给宿主，由当前人设决定措辞。"""
        return await self.say(fact, priority=priority, kind="respond",
                              ttl=ttl, lanlan=target_lanlan, topic=topic)

    async def push_direct(self, text: str, priority: str = CHAT,
                          ttl: float = 240.0, topic: str = "") -> bool:
        """blind 模式：直出聊天气泡，不触发语音。

        默认按闲聊走队列——这类"存在感话术/硬编码短句"本质上都是陪聊内容。
        """
        return await self.say(text, priority=priority, kind="blind", ttl=ttl,
                              topic=topic)

    async def push_image(self, image_bytes: bytes, caption: str = "") -> bool:
        """落盘图片到 data/static/cards/ 并以 markdown 直出。"""
        if self.dry_run:
            self._log("info", "[dry_run] image: %s", caption)
            return True
        try:
            cards = Path(self.plugin.data_path("static", "cards"))
            cards.mkdir(parents=True, exist_ok=True)
            name = f"card_{int(time.time() * 1000)}.png"
            (cards / name).write_bytes(image_bytes)
            url = f"/plugin/neko_pawpilot/ui/static/cards/{name}"
            md = f"![img]({url})" + (f"\n{caption}" if caption else "")
            return await self.push_direct(md)
        except Exception:                    # noqa: BLE001
            return await self.push_direct(caption or "（图片推送失败）")
