import asyncio
import time
from collections import deque


class MinecraftPushRouter:
    def __init__(self, plugin, aggregate_window=8, throttle_window=30, throttle_limit=6):
        self._plugin = plugin
        self._aggregate_window = max(0, float(aggregate_window or 0))
        self._throttle_window = max(1, float(throttle_window or 1))
        self._throttle_limit = max(1, int(throttle_limit or 1))
        self._pending_low = []
        self._flush_task = None
        self._push_times = deque()

    async def push(self, text, ai_behavior="read", priority=1, metadata=None, aggregate=None, coalesce_key=None):
        if not text:
            return False
        if aggregate is None:
            aggregate = ai_behavior == "read" and priority <= 2
        if aggregate:
            self._pending_low.append((time.time(), text, priority, metadata or {}, coalesce_key))
            self._plugin._playmate_debug.record("push", route="aggregate_pending", ai_behavior=ai_behavior, priority=priority, pending=len(self._pending_low), text=str(text)[:160])
            if self._aggregate_window <= 0:
                await self._flush_pending()
                return True
            self._ensure_flush_task()
            return True
        return self._direct_push(
            text, ai_behavior=ai_behavior, priority=priority,
            metadata=metadata, coalesce_key=coalesce_key,
        )

    def recent_push_count(self, window_seconds=60):
        now = time.time()
        self._trim_push_times(now, window_seconds)
        return len(self._push_times)

    async def flush(self):
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        await self._flush_pending(ignore_throttle=True)

    def _ensure_flush_task(self):
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self):
        await asyncio.sleep(self._aggregate_window)
        await self._flush_pending()

    async def _flush_pending(self, ignore_throttle=False):
        if not self._pending_low:
            return
        now = time.time()
        self._trim_push_times(now, self._throttle_window)
        if not ignore_throttle and len(self._push_times) >= self._throttle_limit:
            self._plugin._playmate_debug.record("push", route="throttled", pending=len(self._pending_low), recent_push_count=len(self._push_times))
            if self._flush_task is None or self._flush_task.done() or self._flush_task is asyncio.current_task():
                # 节流重试时使用至少 1 秒间隔，避免 aggregate_window=0 时 busy loop
                retry_delay = max(1.0, self._aggregate_window)
                self._flush_task = asyncio.create_task(self._delayed_flush_with_delay(retry_delay))
            return
        items = self._pending_low
        self._pending_low = []
        # 按 coalesce_key 分组聚合：同类 read 用同一 key，宿主侧 newest-wins 去重旧快照
        groups = {}
        for item in items:
            key = item[4] if len(item) > 4 else None
            groups.setdefault(key, []).append(item)
        for key, group_items in groups.items():
            # A coalesce key represents a replaceable snapshot. Keep only the
            # newest value in this aggregation window; key-less events still
            # retain the existing multi-line aggregation behavior.
            if key is not None:
                group_items = [group_items[-1]]
            lines = []
            for _, text, _, _, _ in group_items:
                for line in str(text).splitlines():
                    line = line.strip()
                    if line:
                        lines.append(line if line.startswith("-") else f"- {line}")
            if not lines:
                continue
            merged = "Minecraft 陪玩上下文：\n" + "\n".join(lines)
            priority = max((item[2] for item in group_items), default=1)
            try:
                self._direct_push(merged, ai_behavior="read", priority=priority, coalesce_key=key)
            except Exception as e:
                # 推送失败时回退 pending，避免数据静默丢失
                self._plugin._playmate_debug.record("push", route="flush_error", error=str(e), recovered=len(group_items))
                self._pending_low = group_items + self._pending_low

    async def _delayed_flush_with_delay(self, delay):
        await asyncio.sleep(delay)
        await self._flush_pending()

    def _direct_push(self, text, ai_behavior="read", priority=1, metadata=None, coalesce_key=None):
        self._push_times.append(time.time())
        self._plugin._playmate_debug.record("push", route="direct", ai_behavior=ai_behavior, priority=priority, coalesce_key=coalesce_key, text=str(text)[:160])
        result = self._plugin.push_message(
            source="minecraft",
            ai_behavior=ai_behavior,
            parts=[{"type": "text", "text": text}],
            metadata=metadata,
            priority=priority,
            coalesce_key=coalesce_key,
        )
        queued = result is not False
        self._plugin._playmate_debug.record(
            "push", route="direct_enqueue_result", queued=queued,
            ai_behavior=ai_behavior, priority=priority,
            coalesce_key=coalesce_key,
        )
        return queued

    def _trim_push_times(self, now, window_seconds):
        while self._push_times and now - self._push_times[0] > window_seconds:
            self._push_times.popleft()
