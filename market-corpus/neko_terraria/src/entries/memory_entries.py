"""记忆入口：@llm_tool 记住/回忆主人与世界的重要事情（SQLite 持久化）。"""

from typing import Any, Dict

from plugin.sdk.plugin import Ok, llm_tool


class MemoryEntriesMixin:
    """记忆工具：terraria_remember / terraria_recall / terraria_forget。

    存储实例惰性创建（首次调用时开 SQLite，路径 data/neko_memory.db），
    由 @lifecycle(shutdown) 关闭（见 LifecycleMixin.on_unload）。
    """

    def _memory_store(self):
        store = getattr(self, "_memory", None)
        if store is None:
            from ..memory.store import MemoryStore
            try:
                store = MemoryStore(str(self.data_path("neko_memory.db")))
            except Exception:
                store = MemoryStore(":memory:")   # 兜底：内存模式
            self._memory = store
        return store

    def _close_memory(self) -> None:
        store = getattr(self, "_memory", None)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
            self._memory = None

    @llm_tool(
        name="terraria_remember",
        description="记住关于主人的重要事情（偏好/约定/事实/成就）时用。"
                    "⚠️只在主人明确表达或观察到值得长期记住的事时调用，不要记琐碎聊天。",
        parameters={"type": "object",
                    "properties": {
                        "key": {"type": "string",
                                "description": "记忆的简短标签，如'主人偏好-喜欢挖矿'"},
                        "value": {"type": "string",
                                  "description": "记忆内容"},
                    },
                    "required": ["key", "value"]},
    )
    async def llm_remember(self, *, key: str, value: str, **_) -> Dict[str, Any]:
        try:
            self._memory_store().remember(key, value)
        except Exception as e:
            return Ok({"output": f"记不住喵（{e}）"})
        return Ok({"output": f"记住啦：{key}"})

    @llm_tool(
        name="terraria_recall",
        description="回忆关于主人或世界的记忆（偏好/约定/以前的事）时用。"
                    "主人问'你还记得...'或需要历史信息时调用。",
        parameters={"type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "回忆的关键词，如'铁矿''主人'"},
                    },
                    "required": ["query"]},
    )
    async def llm_recall(self, *, query: str, **_) -> Dict[str, Any]:
        try:
            items = self._memory_store().recall(query, limit=5)
        except Exception as e:
            return Ok({"output": f"回忆失败喵（{e}）"})
        if not items:
            return Ok({"output": "我没想起相关的记忆喵~"})
        return Ok({"output": "；".join(f"{i['key']}: {i['value']}"
                                       for i in items)})

    @llm_tool(
        name="terraria_forget",
        description="删除一条记忆时用。⚠️主人明确要求忘记某件事时调用。",
        parameters={"type": "object",
                    "properties": {
                        "key": {"type": "string",
                                "description": "要删除的记忆标签"},
                    },
                    "required": ["key"]},
    )
    async def llm_forget(self, *, key: str, **_) -> Dict[str, Any]:
        try:
            self._memory_store().forget(key)
        except Exception as e:
            return Ok({"output": f"忘不掉喵（{e}）"})
        return Ok({"output": f"忘掉啦：{key}"})
