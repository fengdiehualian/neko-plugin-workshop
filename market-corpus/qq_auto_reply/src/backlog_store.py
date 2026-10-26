from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async

from .backlog_models import QQBacklogConversation, QQBacklogMessage, QQGroupBacklog


class QQBacklogStore:
    FILE_NAME = "backlog_state.json"

    def __init__(self, base_dir: Path, *, retention_limit: int = 200):
        self._path = Path(base_dir) / self.FILE_NAME
        self._lock = asyncio.Lock()
        self._retention_limit = max(20, int(retention_limit or 200))

    @property
    def path(self) -> Path:
        return self._path

    def default_state(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "conversations": {},
            "groups": {},
            "group_attention_state": {},
        }

    async def exists(self) -> bool:
        return self._path.is_file()

    async def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return self.default_state()
        payload = await read_json_async(self._path)
        if not isinstance(payload, dict):
            return self.default_state()
        merged = self.default_state()
        merged.update(payload)
        merged["conversations"] = payload.get("conversations") if isinstance(payload.get("conversations"), dict) else {}
        merged["groups"] = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
        for group in merged["groups"].values():
            if isinstance(group, dict):
                if not isinstance(group.get("label_counts"), dict):
                    group["label_counts"] = self._legacy_label_counts(group)
        return merged

    async def save(self, state: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            normalized = self.default_state()
            normalized.update(dict(state or {}))
            normalized["conversations"] = dict(normalized.get("conversations") or {})
            normalized["groups"] = dict(normalized.get("groups") or {})
            await atomic_write_json_async(self._path, normalized)
            return normalized

    async def update_group_attention_state(self, attention_state: dict[str, Any]) -> dict[str, Any]:
        """在**同一把锁内**读改写 ``group_attention_state``，返回落盘后的整份 state。

        注意力服务此前自己做 ``load()`` → 改字段 → ``save()``，而这三步不在
        ``QQBacklogStore`` 的锁里：``append_message`` 全程持锁期间，注意力那边
        可能已经 load 出一份**旧快照**，随后 save 把整份文档写回，于是这把锁
        白拿了——并发下要么丢刚 append 的消息，要么丢刚更新的注意力（``save``
        自身只在写入时取锁，挡不住这种交错）。

        修法不是加第二把锁，而是把读改写整体交给**已有的那把**：磁盘文档只有
        一个写入者，锁的语义才成立。注意力服务仍负责自己的内存缓存与
        ``cleanup_stale_cache``，这里只做原子落盘。
        """
        async with self._lock:
            state = await self.load()
            state["group_attention_state"] = dict(attention_state or {})
            await atomic_write_json_async(self._path, state)
            return state

    async def append_message(self, message: QQBacklogMessage, *, conversation_display_name: str, group_display_name: str | None = None) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            conversations = state["conversations"]
            groups = state["groups"]

            conversation = conversations.get(message.conversation_key)
            if not isinstance(conversation, dict):
                conversation = QQBacklogConversation(
                    conversation_key=message.conversation_key,
                    conversation_type=message.conversation_type,
                    source_id=message.source_id,
                    display_name=conversation_display_name,
                    group_id=message.group_id,
                ).to_dict()

            conversation["conversation_type"] = message.conversation_type
            conversation["source_id"] = message.source_id
            conversation["display_name"] = conversation_display_name
            conversation["group_id"] = message.group_id
            conversation["last_message_at"] = int(message.timestamp or 0)
            conversation["last_message_id"] = str(message.message_id or "")

            messages = list(conversation.get("messages") or [])
            if str(message.message_id or "") and any(str(item.get("message_id") or "") == str(message.message_id or "") for item in messages):
                return state
            messages.append(message.to_dict())
            if len(messages) > self._retention_limit:
                messages = messages[-self._retention_limit:]
            conversation["messages"] = messages
            conversation["unread_count"] = sum(1 for item in messages if item.get("review_status") == "unreviewed")
            conversations[message.conversation_key] = conversation

            if message.group_id:
                group = groups.get(message.group_id)
                if not isinstance(group, dict):
                    group = QQGroupBacklog(
                        group_id=message.group_id,
                        display_name=group_display_name or f"QQ群 {message.group_id}",
                    ).to_dict()
                group["display_name"] = group_display_name or group.get("display_name") or f"QQ群 {message.group_id}"
                group["last_message_at"] = int(message.timestamp or 0)
                group["last_message_id"] = str(message.message_id or "")
                keys = list(group.get("conversation_keys") or [])
                if message.conversation_key not in keys:
                    keys.append(message.conversation_key)
                group["conversation_keys"] = keys
                group["unread_count"] = sum(
                    int((conversations.get(key) or {}).get("unread_count") or 0)
                    for key in keys
                )
                group["label_counts"] = self._count_group_labels(conversations, keys)
                groups[message.group_id] = group

            state["conversations"] = conversations
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def purge_old_reviewed(self, *, max_age_seconds: int = 86400) -> int:
        """删除已审核超过 max_age_seconds 的消息，返回清除条数。"""
        async with self._lock:
            state = await self.load()
            conversations = state["conversations"]
            groups = state["groups"]
            now = int(__import__("time").time())
            cutoff = now - max_age_seconds
            total_removed = 0
            affected_groups: set[str] = set()

            for key, conv in list(conversations.items()):
                if not isinstance(conv, dict):
                    continue
                old = list(conv.get("messages") or [])
                kept = []
                removed_count = 0
                for item in old:
                    ts = int(item.get("timestamp") or item.get("recorded_at") or 0)
                    if item.get("review_status") == "reviewed" and ts < cutoff:
                        removed_count += 1
                        continue
                    kept.append(item)
                if removed_count > 0:
                    conv["messages"] = kept
                    # 更新未读计数：只统计 kept 中 unreviewed 的
                    conv["unread_count"] = sum(1 for m in kept if m.get("review_status") != "reviewed")
                    if "group_id" in (old[0] if old else {}):
                        affected_groups.add(str(old[0].get("group_id", "")))
                    conversations[key] = conv
                    total_removed += removed_count

            # 清理空会话
            empty_keys = [k for k, c in conversations.items() if isinstance(c, dict) and not c.get("messages")]
            for k in empty_keys:
                del conversations[k]
                for gid, group in list(groups.items()):
                    if isinstance(group, dict) and k in (group.get("conversation_keys") or []):
                        group["conversation_keys"] = [ck for ck in group.get("conversation_keys") or [] if ck != k]
                        groups[gid] = group

            # 更新受影响群的统计
            for gid in affected_groups:
                group = groups.get(gid)
                if not isinstance(group, dict):
                    continue
                keys = group.get("conversation_keys") or []
                total_unread = 0
                labels: dict[str, int] = {}
                for ck in keys:
                    c = conversations.get(ck)
                    if not isinstance(c, dict):
                        continue
                    total_unread += int(c.get("unread_count") or 0)
                    for msg in c.get("messages") or []:
                        if msg.get("review_status") == "unreviewed":
                            cat = str(msg.get("category") or "chat")
                            labels[cat] = int(labels.get(cat, 0)) + 1
                group["unread_count"] = total_unread
                group["label_counts"] = labels
                groups[gid] = group

            # 清理空群
            empty_groups = [gid for gid, g in groups.items() if isinstance(g, dict) and not g.get("conversation_keys")]
            for gid in empty_groups:
                del groups[gid]

            state["conversations"] = conversations
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return total_removed

    def _recount_conversation_review(self, conversation: dict[str, Any]) -> int:
        """按消息自身的 ``review_status`` 重算会话未审数，返回该值。

        增量维护（标记时 ``-1``）在"同一 message_id 出现在多个会话"或重复
        标记时会漂移；直接从消息重算既便宜又不会错。
        """
        messages = [item for item in list(conversation.get("messages") or []) if isinstance(item, dict)]
        unread = sum(1 for item in messages if item.get("review_status") == "unreviewed")
        conversation["unread_count"] = unread
        return unread

    async def mark_group_reviewed(self, group_id: str, *, message_ids: set[str] | None = None) -> dict[str, Any]:
        """把群内消息标记为已审核。

        ``message_ids`` 是**本次真正被消费掉的全集**（调用方从
        ``get_unreviewed_message_ids_since`` 取）。老实现无条件标掉该群所有会话
        的所有消息，而回溯只会把最新 ``retroactive_review_max_messages`` 条喂给
        模型 —— 超窗的旧消息因此被标成"已审"却从没被看到，永不补回。传入 ID 集
        后，标记边界就等于实际消费边界，剩下的留给下一轮。

        ``None``（默认）保留"全标"语义，供 ``relay_service`` 与手动入口使用；
        显式传空集表示"本次什么都没消费"，不改任何 review_status，只刷新群级
        统计与 ``last_notified_at``。
        """
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            conversations = state["conversations"]
            group = groups.get(group_id)
            if not isinstance(group, dict):
                return state
            now = int(__import__("time").time())
            keys = list(group.get("conversation_keys") or [])
            last_reviewed_message_id = ""
            for key in keys:
                conversation = conversations.get(key)
                if not isinstance(conversation, dict):
                    continue
                messages = []
                for item in list(conversation.get("messages") or []):
                    updated = dict(item) if isinstance(item, dict) else item
                    if isinstance(updated, dict):
                        mid = str(updated.get("message_id") or "")
                        should_mark = (
                            updated.get("review_status") == "unreviewed"
                            and (message_ids is None or mid in message_ids)
                        )
                        if should_mark:
                            updated["review_status"] = "reviewed"
                        if updated.get("review_status") == "reviewed":
                            last_reviewed_message_id = mid or last_reviewed_message_id
                    messages.append(updated)
                conversation["messages"] = messages
                self._recount_conversation_review(conversation)
                conversation["last_reviewed_at"] = now
                conversation["last_reviewed_message_id"] = last_reviewed_message_id or str(conversation.get("last_message_id") or "")
                conversations[key] = conversation
            group["unread_count"] = sum(
                int((conversations.get(key) or {}).get("unread_count") or 0)
                for key in keys
            )
            group["label_counts"] = self._count_group_labels(conversations, keys)
            group["last_notified_at"] = now
            groups[group_id] = group
            state["groups"] = groups
            state["conversations"] = conversations
            await atomic_write_json_async(self._path, state)
            return state

    async def ensure_group_placeholder(self, group_id: str, *, group_display_name: str | None = None) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            normalized_group_id = str(group_id or "").strip()
            if not normalized_group_id:
                return state
            group = groups.get(normalized_group_id)
            if not isinstance(group, dict):
                group = QQGroupBacklog(
                    group_id=normalized_group_id,
                    display_name=group_display_name or f"QQ群 {normalized_group_id}",
                ).to_dict()
            else:
                group["display_name"] = group_display_name or group.get("display_name") or f"QQ群 {normalized_group_id}"
            groups[normalized_group_id] = group
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def remove_group_placeholder(self, group_id: str) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            normalized_group_id = str(group_id or "").strip()
            if not normalized_group_id:
                return state
            group = groups.get(normalized_group_id)
            if not isinstance(group, dict):
                return state
            keys = list(group.get("conversation_keys") or [])
            if keys:
                return state
            groups.pop(normalized_group_id, None)
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def update_group_last_notified_at(self, group_id: str, timestamp: int) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            normalized_group_id = str(group_id or "").strip()
            if not normalized_group_id:
                return state
            group = groups.get(normalized_group_id)
            if not isinstance(group, dict):
                return state
            group["last_notified_at"] = int(timestamp or 0)
            groups[normalized_group_id] = group
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def set_forward_mark(
        self, group_id: str, *, message_id: str, timestamp: int,
    ) -> dict[str, Any]:
        """记下 `<mark/>` 的起点：群 + 那一条消息 + 当时的时刻。

        合并转发要的是「标记之后的多人多句」，所以起点必须落盘 —— 标记发生在
        某一轮回复里，而转发往往在几十条消息之后才由模型发起。
        """
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            normalized_group_id = str(group_id or "").strip()
            if not normalized_group_id:
                return state
            group = groups.get(normalized_group_id)
            if not isinstance(group, dict):
                # 群还没有 backlog 记录时**建一条**，而不是静默丢弃标记。
                # 旧写法在这里直接 return，于是"标记成功"和"标记被吃掉"从外面看
                # 一模一样 —— 提示词承诺的转发会莫名其妙地永远不触发。
                group = QQGroupBacklog(
                    group_id=normalized_group_id,
                    display_name=f"QQ群 {normalized_group_id}",
                ).to_dict()
            group["forward_mark"] = {
                "message_id": str(message_id or "").strip(),
                "timestamp": int(timestamp or 0),
            }
            groups[normalized_group_id] = group
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def get_forward_mark(self, group_id: str) -> dict[str, Any] | None:
        state = await self.load()
        group = state["groups"].get(str(group_id or "").strip())
        if not isinstance(group, dict):
            return None
        mark = group.get("forward_mark")
        return mark if isinstance(mark, dict) else None

    async def clear_forward_mark(self, group_id: str) -> dict[str, Any]:
        """转发用掉起点后清掉它 —— 同一个标记不能复用，否则下次转发会把
        早就发过的对话再抛一遍。"""
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            normalized_group_id = str(group_id or "").strip()
            group = groups.get(normalized_group_id)
            if not isinstance(group, dict):
                return state
            group.pop("forward_mark", None)
            groups[normalized_group_id] = group
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def get_recent_group_messages(
        self, group_id: str, *, limit: int = 5, exclude_message_id: str = "",
    ) -> list[dict[str, Any]]:
        state = await self.load()
        groups = state["groups"]
        conversations = state["conversations"]
        group = groups.get(str(group_id or "").strip())
        if not isinstance(group, dict):
            return []
        excluded = str(exclude_message_id or "").strip()
        timeline: list[dict[str, Any]] = []
        for key in list(group.get("conversation_keys") or []):
            conversation = conversations.get(key)
            if not isinstance(conversation, dict):
                continue
            for item in list(conversation.get("messages") or []):
                if not isinstance(item, dict):
                    continue
                message_id = str(item.get("message_id") or "").strip()
                if excluded and message_id == excluded:
                    continue
                timeline.append(item)
        timeline.sort(key=lambda item: (int(item.get("timestamp") or 0), str(item.get("message_id") or "")))
        if limit > 0:
            timeline = timeline[-int(limit):]
        return timeline


    async def get_unreviewed_messages_since(self, group_id: str, since_timestamp: int = 0, *, limit: int = 0) -> list[dict[str, Any]]:
        """取出群中自 since_timestamp 以来的未审核消息（供回溯补回使用）。

        ``limit > 0`` 只取**最新** limit 条——这是给模型看的窗口，不是"本次消费
        到的全集"。要标记已审请用 {@link get_unreviewed_message_ids_since} 取
        全集 ID，否则超窗消息会被 ``mark_group_reviewed`` 一起标掉却从没被看到，
        等于永久静默丢失（老代码正是这样）。
        """
        state = await self.load()
        groups = state["groups"]
        conversations = state["conversations"]
        group = groups.get(str(group_id or "").strip())
        if not isinstance(group, dict):
            return []
        results: list[dict[str, Any]] = []
        for key in list(group.get("conversation_keys") or []):
            conv = conversations.get(key)
            if not isinstance(conv, dict):
                continue
            for item in list(conv.get("messages") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("review_status") != "unreviewed":
                    continue
                ts = int(item.get("timestamp") or 0)
                if since_timestamp > 0 and ts < since_timestamp:
                    continue
                results.append(item)
        results.sort(key=lambda item: (int(item.get("timestamp") or 0), str(item.get("message_id") or "")))
        if limit > 0:
            results = results[-int(limit):]
        return results

    async def get_unreviewed_message_ids_since(self, group_id: str, since_timestamp: int = 0) -> set[str]:
        """群中自 since_timestamp 以来**全部**未审核消息的 ID（不受窗口限制）。

        与 {@link get_unreviewed_messages_since} 配对使用：那个给模型看窗口，
        这个给 ``mark_group_reviewed`` 定"已审"边界。返回空集代表没有未审消息。
        """
        messages = await self.get_unreviewed_messages_since(group_id, since_timestamp, limit=0)
        return {
            str(item.get("message_id") or "").strip()
            for item in messages
            if str(item.get("message_id") or "").strip()
        }

    async def mark_message_reviewed(self, message_id: str) -> None:
        """将指定消息标记为已审核（AI 回复后即时调用）。"""
        mid = str(message_id or "").strip()
        if not mid:
            return
        async with self._lock:
            state = await self.load()
            conversations = state["conversations"]
            groups = state["groups"]
            updated_group_ids: set[str] = set()
            for conv_key, conv in list(conversations.items()):
                if not isinstance(conv, dict):
                    continue
                for item in list(conv.get("messages") or []):
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("message_id") or "").strip() == mid and item.get("review_status") != "reviewed":
                        item["review_status"] = "reviewed"
                        conv["unread_count"] = max(0, int(conv.get("unread_count") or 0) - 1)
                        if "group_id" in item:
                            updated_group_ids.add(str(item["group_id"] or ""))
                        continue
                conversations[conv_key] = conv
            # 更新群级统计
            for gid in updated_group_ids:
                group = groups.get(gid)
                if isinstance(group, dict):
                    group["unread_count"] = int(group.get("unread_count") or 0) - 1
                    if group["unread_count"] < 0:
                        group["unread_count"] = 0
                    # 重新计算 label_counts
                    group["label_counts"] = {}
                    for key in list(group.get("conversation_keys") or []):
                        conv = conversations.get(key)
                        if not isinstance(conv, dict):
                            continue
                        for item in list(conv.get("messages") or []):
                            if not isinstance(item, dict):
                                continue
                            if item.get("review_status") == "unreviewed":
                                cat = str(item.get("category") or "chat")
                                group["label_counts"][cat] = int(group["label_counts"].get(cat, 0)) + 1
                    groups[gid] = group
            state["conversations"] = conversations
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)

    async def get_group_detail(self, group_id: str) -> dict[str, Any]:
        state = await self.load()
        groups = state["groups"]
        conversations = state["conversations"]
        group = groups.get(group_id)
        if not isinstance(group, dict):
            return {"group": None, "conversations": []}
        detail_conversations: list[dict[str, Any]] = []
        for key in list(group.get("conversation_keys") or []):
            conversation = conversations.get(key)
            if not isinstance(conversation, dict):
                continue
            detail_conversations.append({
                "conversation_key": key,
                "display_name": conversation.get("display_name") or key,
                "unread_count": int(conversation.get("unread_count") or 0),
                "messages": [item for item in list(conversation.get("messages") or []) if item.get("review_status") == "unreviewed"],
            })
        return {"group": group, "conversations": detail_conversations}

    @staticmethod
    def _count_group_labels(conversations: dict[str, Any], keys: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in keys:
            for item in list((conversations.get(key) or {}).get("messages") or []):
                if item.get("review_status") != "unreviewed":
                    continue
                category = str(item.get("category") or "chat").strip() or "chat"
                if category == "chat":
                    continue
                counts[category] = counts.get(category, 0) + 1
        return counts

    @staticmethod
    def _legacy_label_counts(group: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in ("issue", "feedback", "mention"):
            value = int(group.get(f"{key}_count") or 0)
            if value > 0:
                counts[key] = value
        return counts
