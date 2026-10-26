"""接续摘要：会话被回收时留一句"刚才聊到哪儿"，新会话开口就接得上。

**为什么需要**（使用者给的现场）：私聊里她说完"口误居然还全票通过啦"之后隔了 5 分半，
下一句是"哇，这个小猫娘好可爱！和我有点像欸" —— 明显割裂。原因不是模型抽风，而是
**会话被回收了**：

```
SESSION_IDLE_TIMEOUT_SECONDS = 300   # 空闲 5 分钟
→ flush_idle_memory_sessions：结算长期记忆 + **弹掉会话**
→ 下一条消息落在全新会话上（只有长期记忆，没有刚才那几轮）
```

群聊的 `group:{gid}` 是同一套阈值，所以"从 A 群切回 B 群"时 B 的上下文也是空的。

**这个服务做什么**：会话消失之前，从它的历史尾部截一小段**当次对话的接续摘要**
（默认最近 2 轮、每行 80 字），连同"最后活动时刻"落盘；新会话创建时把它作为启动
上下文注入（`ensure_generation_session` 拼进 instructions），**用完即弃**。

三条刻意设计：

1. **落盘，不只放内存**。触发这个需求的那次割裂，一半原因是插件重启把内存会话清空了
   —— 只放内存的摘要在"最需要它的时刻"正好不在。
2. **只在 `memory_enabled` 的会话上留存**。它不是长期记忆，但它会把对话原文写到磁盘，
   所以走与记忆结算同一道授权闸：会话被标成"不落记忆"时不留摘要。撤销授权 / 移除用户 /
   换人格时由 `forget()` 显式抹掉。
3. **一次性 + 有 TTL**。注入成功即 `consume()`；超过 `TTL_SECONDS` 的旧摘要不再注入
   （"刚才"过了半小时就不是刚才了，硬接反而更怪）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

#: 摘要里保留几轮对话（1 轮 = 一条对方发言 + 一条她的回复）。
MAX_TURNS = 2
#: 每行最多多少字（摘要不是记录，长了反而把新话题挤掉）。
MAX_LINE_CHARS = 80
#: 超过这个时长的摘要在注入侧就当没有（不删，`peek` 会顺手清）。
TTL_SECONDS = 1800.0
#: 会话表上限：摘要表不该无界增长。
MAX_NOTES = 500


class QQSessionHandoffService:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._notes: dict[str, dict[str, Any]] = {}
        self._loaded = False

    # ── 落盘 ────────────────────────────────────────────────────

    def _store_path(self):
        return self.plugin.data_path("session_handoff.json")

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            path = self._store_path()
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            notes = data.get("notes") if isinstance(data, dict) else None
            if isinstance(notes, dict):
                self._notes = {
                    str(key): value for key, value in notes.items()
                    if isinstance(value, dict)
                }
        except Exception as exc:  # noqa: BLE001
            # 坏文件不该拦住插件启动：当成"没有摘要"，并在下次写入时覆盖。
            self.plugin.logger.warning(f"[Handoff] 读取接续摘要失败（按空处理）: {exc}")
            self._notes = {}

    def _save(self) -> None:
        try:
            path = self._store_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "notes": self._notes}
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8",
            )
            temporary.replace(path)
        except Exception as exc:  # noqa: BLE001
            self.plugin.logger.warning(f"[Handoff] 写入接续摘要失败: {exc}")

    def _prune(self, now: float) -> None:
        expired = [
            key for key, note in self._notes.items()
            if now - float(note.get("at") or 0.0) > TTL_SECONDS * 4
        ]
        for key in expired:
            self._notes.pop(key, None)
        if len(self._notes) > MAX_NOTES:
            ordered = sorted(
                self._notes.items(), key=lambda kv: float(kv[1].get("at") or 0.0),
            )
            for key, _note in ordered[: len(self._notes) - MAX_NOTES]:
                self._notes.pop(key, None)

    # ── 摘要内容 ────────────────────────────────────────────────

    #: 这些标签的**内容**是协议负载（内部状态 / ID / QQ 号），不是她说过的话：
    #: 只脱壳会留下内容，摘要里就会出现 `你：playful看得到呀乖乖…` 这种句子
    #: （实测在真小票里就长这样）。`<text>` / `<record>` 的内容是话，必须留。
    _DROP_WITH_CONTENT = (
        "feeling", "sticker", "emoji", "poke", "at", "reply", "keyboard", "ark", "forward",
    )

    @classmethod
    def _row_text(cls, row: Any) -> str:
        """历史行 → 一行纯文本（去掉她自己的输出协议与多余空白）。"""
        content = getattr(row, "content", None)
        if not isinstance(content, str):
            content = str(content or "")
        import re
        for tag in cls._DROP_WITH_CONTENT:
            content = re.sub(
                rf"<{tag}\b[^>]*>.*?</{tag}>", "", content, flags=re.S | re.I,
            )
        # 剩下的容器标签（msg / text / record）只脱壳，内容保留
        content = re.sub(
            r"</?(?:msg|text|feeling|emoji|at|reply|sticker|poke|record|keyboard|ark|mark|forward)\b[^>]*>",
            "", content,
        )
        content = " ".join(content.split())
        return content[:MAX_LINE_CHARS]

    @staticmethod
    def _row_role(row: Any) -> str:
        name = type(row).__name__.lower()
        if "human" in name or "user" in name:
            return "对方"
        if "ai" in name or "assistant" in name:
            return "你"
        return ""

    def build_note(self, *, session_key: str, user_data: dict[str, Any]) -> Optional[dict[str, Any]]:
        """从会话历史尾部取一小段接续摘要；不值得留时返回 None。"""
        session = (user_data or {}).get("session")
        history = getattr(session, "_conversation_history", None)
        if not isinstance(history, list) or not history:
            return None
        # 未授权区间之后的内容不进摘要（与会话结算同一道闸：撤权后的行不许落盘）。
        cutoff = int((user_data or {}).get("nonconsent_history_end", 0) or 0)
        rows = history[cutoff:] if 0 < cutoff <= len(history) else history
        lines: list[tuple[str, str]] = []
        for row in rows:
            role = self._row_role(row)
            if not role:
                continue  # system 行 / 工具行不进摘要
            text = self._row_text(row)
            if not text:
                continue
            lines.append((role, text))
        if not lines:
            return None
        tail = lines[-(MAX_TURNS * 2):]
        return {
            "at": time.time(),
            "group_id": str((user_data or {}).get("group_id") or ""),
            "is_group": bool((user_data or {}).get("is_group")),
            # 摘要是**这个角色**的对话：换人格后不能把它交给新的她（那等于把上一个
            # 角色的临场上下文塞给另一个角色，而换人格在本仓是硬边界）。
            "her_name": str((user_data or {}).get("her_name") or ""),
            "lines": [f"{role}：{text}" for role, text in tail],
        }

    # ── 生命周期 ────────────────────────────────────────────────

    def capture(self, session_key: str, user_data: dict[str, Any]) -> Optional[dict[str, Any]]:
        """会话即将消失时调用。只对 `memory_enabled` 的会话留摘要。"""
        if not session_key or not isinstance(user_data, dict):
            return None
        if user_data.get("ephemeral_session"):
            # 一次性会话（主动发言那类合成轮）不是"一段对话"，别把她的主动搭话
            # 当成上次聊到的东西留给下一个会话。
            return None
        if not user_data.get("memory_enabled"):
            # 不落记忆的会话不留摘要 —— 摘要会把对话原文写到磁盘，属于同一类授权。
            # 顺手抹掉旧的：本会话既然没被授权，之前那份也不该继续挂在这里。
            self.forget(session_key)
            return None
        note = self.build_note(session_key=session_key, user_data=user_data)
        if note is None:
            return None
        self._load()
        now = time.time()
        self._notes[str(session_key)] = note
        self._prune(now)
        self._save()
        return note

    def peek(
        self, session_key: str, *, her_name: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """取（不消费）可用摘要；过期的、或属于别的角色的顺手清掉。"""
        if not session_key:
            return None
        self._load()
        note = self._notes.get(str(session_key))
        if not isinstance(note, dict):
            return None
        if time.time() - float(note.get("at") or 0.0) > TTL_SECONDS:
            self._notes.pop(str(session_key), None)
            self._save()
            return None
        stored_name = str(note.get("her_name") or "")
        if her_name is not None and stored_name and str(her_name) != stored_name:
            # 角色换过了：旧角色的临场上下文**不注入**给新角色。但条目留着 ——
            # 换回来（30 分钟内）时它依然是那个角色自己的上下文；删掉反而会把
            # 一次人格切换变成不可逆的上下文丢失。
            return None
        return note

    def consume(self, session_key: str) -> None:
        """注入成功之后调用：摘要是一次性的。"""
        self._load()
        if self._notes.pop(str(session_key), None) is not None:
            self._save()

    def forget(self, session_key: str) -> bool:
        """撤销授权 / 换人格 / 移除用户时抹掉摘要。"""
        self._load()
        if self._notes.pop(str(session_key), None) is None:
            return False
        self._save()
        return True

    def forget_all(self) -> int:
        self._load()
        removed = len(self._notes)
        if removed:
            self._notes.clear()
            self._save()
        return removed

    # ── 注入 ────────────────────────────────────────────────────

    def render_section(self, session_key: str, *, her_name: str | None = None) -> str:
        """摘要 → 拼进新会话 system prompt 的一段；没有就返回空串。"""
        note = self.peek(session_key, her_name=her_name)
        if not note:
            return ""
        lines = [str(line) for line in (note.get("lines") or []) if str(line).strip()]
        if not lines:
            return ""
        minutes = max(0, int((time.time() - float(note.get("at") or 0.0)) // 60))
        gap = f"{minutes} 分钟前" if minutes < 60 else f"{minutes // 60} 小时前"
        body = "\n".join(f"> {line}" for line in lines)
        return (
            "## 上一次对话的结尾（接续用）\n"
            f"你们上一次说话是 {gap}。下面是那次对话的最后几句：\n"
            f"{body}\n"
            "如果这次的话头与上面有关，自然接上即可；"
            "已经过去一段时间了，别假装刚才一直在聊，也别照抄上面的原话。\n"
        )
