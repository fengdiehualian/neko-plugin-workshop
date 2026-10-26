"""Recall — low-friction companion vocabulary review for N.E.K.O."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    ui,
    unwrap_or,
)

if __package__:
    from .core import (
        create_session,
        dashboard,
        get_active_session,
        migrate_state,
        schedule_review,
        submit_review,
    )
    from .models import build_source_registry
else:
    # Standalone market repositories are checked with this directory as the
    # pytest root, so pytest imports this file without a package context.
    from core import (
        create_session,
        dashboard,
        get_active_session,
        migrate_state,
        schedule_review,
        submit_review,
    )
    from models import build_source_registry


STATE_KEY = "learning_state_v2"
LEGACY_STATE_KEY = "learning_state_v1"
MAX_CHARACTER_RESPONSE_BYTES = 2 * 1024 * 1024


def _main_server_port() -> int:
    for env_name in ("NEKO_MAIN_SERVER_PORT", "MAIN_SERVER_PORT"):
        raw = os.getenv(env_name)
        if raw:
            try:
                port = int(raw)
                if 1 <= port <= 65535:
                    return port
            except ValueError:
                pass
    try:
        from config import MAIN_SERVER_PORT as configured_port

        port = int(configured_port)
        if 1 <= port <= 65535:
            return port
    except (ImportError, TypeError, ValueError):
        pass
    return 48911


def _fetch_companion_identity() -> dict[str, Any]:
    url = (
        f"http://127.0.0.1:{_main_server_port()}"
        "/api/card-drop/active-character?include_avatar=true"
    )
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=4.0) as response:
        payload = response.read(MAX_CHARACTER_RESPONSE_BYTES + 1)
    if len(payload) > MAX_CHARACTER_RESPONSE_BYTES:
        raise ValueError("active character response is too large")
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("active character response is invalid")
    image = str(data.get("characterReferenceDataUrl") or data.get("dataUrl") or "")
    if image and not image.startswith("data:image/"):
        image = ""
    return {
        "available": True,
        "name": str(data.get("name") or "N.E.K.O."),
        "model_type": str(data.get("modelType") or ""),
        "model_key": str(data.get("modelKey") or ""),
        "data_url": image,
    }


@neko_plugin
class RecallPlugin(NekoPluginBase):
    """Owns vocabulary learning state, never the host's personal memory."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._sources = build_source_registry()
        self._lock = asyncio.Lock()
        self._default_source = "official.cet4.core.v1"
        self._new_limit = 5
        self._review_limit = 12

    @lifecycle(id="startup")
    async def startup(self, **_):
        self._default_source = str(
            await self.config.get("recall.default_source", self._default_source)
            or self._default_source
        )
        self._new_limit = max(
            1,
            min(
                30,
                int(await self.config.get("recall.new_words_per_session", self._new_limit) or self._new_limit),
            ),
        )
        self._review_limit = max(
            1,
            min(
                50,
                int(await self.config.get("recall.review_limit", self._review_limit) or self._review_limit),
            ),
        )
        self.register_static_ui("static")
        self.set_list_actions(
            [
                {
                    "id": "open_ui",
                    "kind": "ui",
                    "target": f"/plugin/{quote(self.plugin_id, safe='')}/ui/",
                    "open_in": "new_tab",
                }
            ]
        )
        async with self._lock:
            state = await self._load_state()
            await self._save_state(state)
        return Ok({"status": "ready", "source_count": len(self._sources)})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    async def _load_state(self) -> dict[str, Any]:
        value = unwrap_or(await self.store.get(STATE_KEY), None)
        if not isinstance(value, dict):
            value = unwrap_or(await self.store.get(LEGACY_STATE_KEY), None)
        return migrate_state(value)

    async def _save_state(self, state: dict[str, Any]) -> None:
        result = await self.store.set(STATE_KEY, state)
        if isinstance(result, Err):
            raise RuntimeError("RECALL_STATE_STORE_FAILED")

    def _source(self, source_id: str | None = None):
        key = str(source_id or self._default_source)
        source = self._sources.get(key)
        if source is None:
            raise ValueError("knowledge source is unavailable")
        return source

    @ui.context(id="recall", title="Recall")
    async def recall_context(self, **_):
        state = await self._load_state()
        return {"ready": True, **dashboard(state, self._source())}

    @plugin_entry(
        id="open_recall",
        name="打开 Recall 背单词",
        description=(
            "仅当用户明确想背、学或复习英语单词/词汇（例如 CET-4）时打开 Recall。"
            "不要把用户偏好、人物关系、日常事实或泛化的‘记住这件事’交给 Recall；那些仍属于 N.E.K.O 长期记忆。"
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "path", "open_in", "message"],
    )
    async def open_recall(self, **_):
        return Ok(
            {
                "available": self.get_static_ui_config() is not None,
                "path": f"/plugin/{quote(self.plugin_id, safe='')}/ui/",
                "open_in": "new_tab",
                "message": "Recall 已准备好，可以直接开始背单词。",
            }
        )

    @plugin_entry(
        id="get_dashboard",
        name="获取 Recall 今日概览",
        description="读取 Recall 自己的词汇学习进度与今日待复习数量。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["due_count", "learned_count", "new_count", "source", "memory_boundary"],
    )
    async def get_dashboard(self, **_):
        try:
            state = await self._load_state()
            return Ok(dashboard(state, self._source()))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="list_sources",
        name="列出 Recall 词汇来源",
        description="列出可用词汇知识源和已经预留但尚未开放的来源类型。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["sources", "reserved_source_kinds"],
    )
    async def list_sources(self, **_):
        return Ok(
            {
                "sources": [source.info.to_dict() for source in self._sources.values()],
                "reserved_source_kinds": {
                    "community": "planned_via_neko_community",
                    "local": "planned_private_source",
                    "user": "interface_reserved",
                    "external": "interface_reserved",
                },
            }
        )

    @plugin_entry(
        id="set_familiarity",
        name="设置词汇熟悉程度",
        description="保存首次开始前的轻量自评，不要求用户理解调度参数。",
        input_schema={
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["new", "some", "once", "unknown"],
                }
            },
            "required": ["level"],
        },
        llm_result_fields=["familiarity"],
    )
    async def set_familiarity(self, level: str, **_):
        if level not in {"new", "some", "once", "unknown"}:
            return Err(SdkError("invalid familiarity level"))
        async with self._lock:
            state = await self._load_state()
            state["familiarity"] = level
            await self._save_state(state)
        return Ok({"familiarity": level})

    @plugin_entry(
        id="start_session",
        name="开始一组词汇复习",
        description=(
            "为用户明确发起的英语词汇学习生成一组到期词和少量新词。"
            "该入口不接收普通长期记忆内容。"
        ),
        input_schema={
            "type": "object",
            "properties": {"source_id": {"type": "string", "default": "official.cet4.core.v1"}},
        },
        llm_result_fields=["session", "count", "source"],
    )
    async def start_session(self, source_id: str = "official.cet4.core.v1", **_):
        try:
            async with self._lock:
                state = await self._load_state()
                source = self._source(source_id)
                active = create_session(
                    state,
                    source,
                    intent="mixed",
                    new_limit=self._new_limit,
                    review_limit=self._review_limit,
                )
                await self._save_state(state)
            current = active.get("current_card")
            return Ok(
                {
                    "session": [current] if current else [],
                    "count": int(active.get("remaining") or 0),
                    "active_session": active,
                    "source": source.info.to_dict(),
                }
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="create_session",
        name="开始 Recall 学习会话",
        description="按用户意图开始到期复习、新词学习或混合会话；三种会话共享同一份持久化状态。",
        input_schema={
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "default": "official.cet4.core.v1"},
                "intent": {
                    "type": "string",
                    "enum": ["review_due", "learn_new", "mixed"],
                    "default": "mixed",
                },
                "resume": {"type": "boolean", "default": True},
            },
        },
        llm_result_fields=["id", "intent", "status", "remaining", "current_card", "source"],
    )
    async def create_recall_session(
        self,
        source_id: str = "official.cet4.core.v1",
        intent: str = "mixed",
        resume: bool = True,
        **_,
    ):
        try:
            async with self._lock:
                state = await self._load_state()
                source = self._source(source_id)
                session = create_session(
                    state,
                    source,
                    intent=intent,
                    new_limit=self._new_limit,
                    review_limit=self._review_limit,
                    resume=bool(resume),
                )
                await self._save_state(state)
            return Ok(session)
        except (TypeError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="get_active_session",
        name="继续 Recall 当前会话",
        description="恢复尚未完成的学习会话，页面刷新后不会从第一张重新开始。",
        input_schema={
            "type": "object",
            "properties": {"source_id": {"type": "string", "default": "official.cet4.core.v1"}},
        },
        llm_result_fields=["active_session"],
    )
    async def get_recall_active_session(self, source_id: str = "official.cet4.core.v1", **_):
        try:
            state = await self._load_state()
            active = get_active_session(state, self._source(source_id))
            return Ok({"active_session": active})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="get_companion_identity",
        name="读取当前 N.E.K.O 角色",
        description="供 Recall 界面读取用户当前使用的系统角色名称与形象。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "name", "model_type", "model_key", "data_url"],
    )
    async def get_companion_identity(self, **_):
        try:
            return Ok(await asyncio.to_thread(_fetch_companion_identity))
        except Exception:
            return Ok(
                {
                    "available": False,
                    "name": "N.E.K.O.",
                    "model_type": "",
                    "model_key": "",
                    "data_url": "",
                }
            )

    @plugin_entry(
        id="submit_review",
        name="提交 Recall 回忆结果",
        description="一次性更新复习事件、词条记忆状态、会话进度、下一张卡和陪伴提示。",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "item_id": {"type": "string"},
                "grade": {"type": "string", "enum": ["forgot", "hard", "good"]},
                "client_event_id": {"type": "string"},
            },
            "required": ["session_id", "item_id", "grade", "client_event_id"],
        },
        llm_result_fields=["event_id", "item_state", "schedule", "session", "next_card", "companion_cue", "dashboard"],
    )
    async def submit_recall_review(
        self,
        session_id: str,
        item_id: str,
        grade: str,
        client_event_id: str,
        **_,
    ):
        try:
            async with self._lock:
                state = await self._load_state()
                source = self._source()
                result = submit_review(
                    state,
                    source,
                    session_id=session_id,
                    item_id=item_id,
                    grade=grade,
                    client_event_id=client_event_id,
                )
                await self._save_state(state)
            return Ok(result)
        except (TypeError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="rate_review",
        name="记录一次词汇回忆结果",
        description="把忘记、吃力或记得的结果写入 Recall 学习状态并安排下次复习。",
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "grade": {"type": "string", "enum": ["forgot", "hard", "good"]},
            },
            "required": ["item_id", "grade"],
        },
        llm_result_fields=["item_id", "grade", "progress", "dashboard"],
    )
    async def rate_review(self, item_id: str, grade: str, **_):
        known_ids = {item.id for item in self._source().items()}
        if item_id not in known_ids:
            return Err(SdkError("unknown vocabulary item"))
        try:
            async with self._lock:
                state = await self._load_state()
                progress = schedule_review(state, item_id=item_id, grade=grade)
                await self._save_state(state)
            return Ok(
                {
                    "item_id": item_id,
                    "grade": grade,
                    "progress": progress,
                    "dashboard": dashboard(state, self._source()),
                }
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="companion_reply",
        name="生成 Recall 界面内陪伴回应",
        description="根据当前背词阶段给出短而不打扰的界面内回应；不写入长期记忆。",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "stage": {"type": "string"},
                "word": {"type": "string"},
            },
            "required": ["message"],
        },
        llm_result_fields=["reply"],
    )
    async def companion_reply(self, message: str, stage: str = "home", word: str = "", **_):
        text = str(message or "").strip()
        if not text:
            return Err(SdkError("message is required"))
        if any(token in text for token in ("不会", "忘", "难", "记不住")):
            reply = f"没关系，{word + ' ' if word else ''}想不起来也不用硬撑。"
        elif any(token in text for token in ("休息", "累", "停")):
            reply = "可以，今天背到这里就好。"
        elif stage == "question":
            reply = "先在脑子里找一下答案；真的空白再点开，想起来的那一下最重要。"
        else:
            reply = "我在。慢慢想，想不起来也没关系。"
        return Ok({"reply": reply, "stage": stage, "word": word})

    @plugin_entry(
        id="bridge_capabilities",
        name="Recall 插件协作能力",
        description="供其他插件查询 Recall 的卡片交接与知识源接口状态。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["contract", "capabilities"],
    )
    async def bridge_capabilities(self, **_):
        return Ok(
            {
                "contract": "recall.vocabulary-handoff.v1",
                "capabilities": {
                    "accept_vocabulary_cards": False,
                    "reason": "MVP 只启用内置英语词汇；跨插件卡片交接接口已保留。",
                    "future_sources": ["neko_community", "local_private", "user", "external"],
                },
            }
        )

    @plugin_entry(
        id="accept_vocabulary_cards",
        name="接收词汇卡片（预留）",
        description="预留给伴学等插件的词汇卡片交接入口；MVP 不接受写入。",
        input_schema={
            "type": "object",
            "properties": {
                "contract": {"type": "string"},
                "source_plugin": {"type": "string"},
                "cards": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["contract", "source_plugin", "cards"],
        },
        llm_result_fields=["accepted", "reason", "contract"],
    )
    async def accept_vocabulary_cards(
        self,
        contract: str,
        source_plugin: str,
        cards: list[dict[str, Any]],
        **_,
    ):
        return Ok(
            {
                "accepted": False,
                "reason": "not_enabled_in_mvp",
                "contract": "recall.vocabulary-handoff.v1",
                "received_count": len(cards) if isinstance(cards, list) else 0,
                "source_plugin": str(source_plugin or ""),
            }
        )
