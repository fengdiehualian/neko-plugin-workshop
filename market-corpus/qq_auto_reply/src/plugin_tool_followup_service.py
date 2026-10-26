"""异步插件任务的**结果回投**：把"结果出来我告诉你"从他的一句空话变成真的。

## 背景（真机 15:00，见 `docs/SESSION-HANDOFF.md` §4.0ag-1）

插件那条桥一次对话只走**一个工具轮**（宿主 `max_tool_iterations=1`），所以当
`writer_power_analysis:analyze_text` 只回 `{"task_id": …, "status": "queued"}` 时，
本轮就结束了 —— 她当场对使用者说的"等结果出来我第一时间告诉你"**没有任何东西会
兑现**。上一轮的止血办法是**让她别说**（`_async_followup_note`）。

使用者 16:3x 拍板要**做成真的**：结果一到就由她主动回一条到同一个会话。

## 这条链路怎么走

1. **登记**：工具桥的 handler 在结果里认出 `task_id`（同名/`*_task_id`）时，调
   `remember(...)`，把 `(插件, task_id, 查状态的 entry, 会话身份)` 落盘登记；
2. **轮询**：后台协程按 `POLL_INTERVAL_SECONDS` 去问那个 entry，问出
   `done` / `error` 一类的终态就停；
3. **回投**：用**同一场对话的身份**（与那一轮完全一致的 is_group / 目标 / 记忆策略）
   组一个 `plugin_tool_result` 合成轮，把结果塞进 prompt，让她用自己的话说出去。

## 四道闸（都是"能不发就不发"的方向）

* **开关闸**：`qq_open_plugin_followup_enabled`（默认开，使用者可关）；
* **值班闸**：`plugin._running` 为假（值班已停）就不说话 —— 停着还主动发消息是越权；
* **通道闸**：只在与工具桥同一条通道上回投 —— 现在这条闸对**所有 QQ 通道**都开
  （开放平台 + OneBot 反向/正向；使用者 2026-10-02 放开），见 `plugin_tool_service.
  channel_allows_plugin_tools`；
* **上限闸**：同时在等的任务 ≤ `MAX_PENDING`，同一会话只留一条（避免一次涌出好几条）。

另外两条是**不许撒谎**：
* 结果永远只投一次（键 = 插件 + task_id），**投递管线说送到了**才出队；
* 插件重启/任务被清掉导致"查不到"时，回投的是**"那个任务跟丢了"**，不是假装成功
  也不是假装失败 —— 三种终态各有一套说法，见 `render_followup_prompt`。
"""

from __future__ import annotations

import json
import time
from typing import Any

from .pipeline_models import KIND_PLUGIN_TOOL_RESULT, QQReplyRequest
from .plugin_tool_service import emit_bridge_log

#: 落盘文件名（走 `plugin.data_path`）。宿主重启后仍在等的任务要能接着等 ——
#: 否则"我告诉你"又会变成一句空话，而且这次连日志都没有。
FOLLOWUP_STATE_FILE = "plugin_tool_followups.json"

#: 登记后先等一会儿再问第一次（任务刚提交，立刻查必然是 queued）。
FIRST_DELAY_SECONDS = 4.0

#: 轮询间隔（秒）。查询是跨插件 IPC 往返，5s 足够且不会把宿主问烦。
POLL_INTERVAL_SECONDS = 5.0

#: 一条任务最多等多久（秒）。超时后按 `timeout` 终态处理（会说"还没出来/跟丢了"）。
MAX_WAIT_SECONDS = 900.0

#: 单次最多问几次（与 `MAX_WAIT_SECONDS` 双保险，防止 tick 被高频触发时问爆）。
MAX_ATTEMPTS = 200

#: 同时在等的任务数上限。工具轮本来就稀少，给 4 个足够，且防"一次涌出好几条"。
MAX_PENDING = 4

#: 进提示词的"在办的事"最多列几项。这一行是给模型看的**状态**，不是清单：
#: 只列最老的两项，够她知道自己还欠着什么；再多就是每轮白烧预算。
PENDING_PROMPT_MAX_ITEMS = 2

#: 同一会话最多同时挂几条（1 = 一个会话一次只等一件事）。
MAX_PENDING_PER_CONVERSATION = 1

#: 连续多少次查询失败才认定"任务跟丢了"。单次失败可能只是插件正在重启。
ERRORS_BEFORE_LOST = 3

#: 投递失败（值班停了 / 通道换了）时的重试间隔下限（秒），避免每 5s 刷一条日志。
BLOCKED_LOG_INTERVAL_SECONDS = 60.0

#: 塞进 prompt 的结果长度上限。宿主也给 MCP 结果封顶（`MCP_TOOL_RESULT_MAX_TOKENS`），
#: 而这一轮还要再进一次 LLM，长文整段进去只会顶爆上下文。
PROMPT_RESULT_MAX_CHARS = 1200

#: 终态词表。别的插件用什么词我们管不了，所以按**语义分组**归一化。
DONE_TOKENS = frozenset({
    "done", "success", "succeeded", "complete", "completed", "finished", "ready", "ok",
})
FAILED_TOKENS = frozenset({
    "error", "failed", "failure", "canceled", "cancelled", "timeout", "timed_out", "aborted",
})
RUNNING_TOKENS = frozenset({
    "queued", "pending", "running", "processing", "in_progress", "started", "waiting",
    "accepted", "submitted", "created",
})

#: 从结果里找"进度"时看的键名（第一个命中的字符串说了算）。
STATUS_KEYS = ("status", "state", "task_status", "phase", "stage")

#: 从结果里找"正文"时看的键名（按顺序取第一个非空的）。
RESULT_KEYS = ("result", "data", "output", "text", "content", "summary", "answer", "report", "value")

#: 渲染兜底那份 payload 时要剔掉的记账字段（它们不是"结果"）。
BOOKKEEPING_KEYS = frozenset({
    "task_id", "status", "state", "task_status", "phase", "stage", "created_at", "started_at",
    "finished_at", "updated_at", "model", "mode", "article_chars", "progress", "percent",
})


def normalize_status(token: Any) -> str:
    """把别的插件报的进度词归一成 ``running`` / ``done`` / ``failed`` / ``unknown``。"""
    text = str(token or "").strip().lower().replace("-", "_")
    if not text:
        return "unknown"
    if text in DONE_TOKENS:
        return "done"
    if text in FAILED_TOKENS:
        return "failed"
    if text in RUNNING_TOKENS:
        return "running"
    return "unknown"


def status_token(payload: Any) -> str:
    """从结果里取"进度"那个词（认不出返回空串）。"""
    if isinstance(payload, dict):
        for key in STATUS_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def extract_result(payload: Any) -> str:
    """从终态结果里取"要告诉对方的那段正文"。"""
    if isinstance(payload, dict):
        for key in RESULT_KEYS:
            value = payload.get(key)
            if value:
                return render_payload(value)
        rest = {k: v for k, v in payload.items() if k not in BOOKKEEPING_KEYS and v}
        if rest:
            return render_payload(rest)
        return ""
    return render_payload(payload)


def render_payload(value: Any) -> str:
    """结果 → 文本（含长度上限，且**先脱敏**）。

    回投是把结果直接塞进她的 prompt，而她随后会当着群里的人说 —— 所以密钥形状
    （`sk-…` / `Bearer …` / `api key: …` / `****149a`）与"键名像密钥"的字段都要在
    进 prompt 之前掩掉（与工具桥同一套判据，见 `plugin_tool_service`）。
    """
    from .plugin_tool_service import redact_payload, redact_text

    value = redact_payload(value)
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
    text = redact_text(str(text or "").strip())
    if len(text) > PROMPT_RESULT_MAX_CHARS:
        text = text[:PROMPT_RESULT_MAX_CHARS] + f"…（原文过长，已截断 {len(text) - PROMPT_RESULT_MAX_CHARS} 字）"
    return text


class QQPluginToolFollowupService:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        #: ``"<plugin_id>:<task_id>"`` → 记录（见 `remember`）。
        self._pending: dict[str, dict[str, Any]] = {}
        self._loop_task: Any = None
        self._load()

    # ── 开关 ────────────────────────────────────────────────────────

    def enabled(self) -> bool:
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        return bool(settings.get("qq_open_plugin_followup_enabled", True))

    def channel_allowed(self) -> bool:
        """当前通道是否挂着插件工具（回投与工具桥**同一条闸**，别再各写一份）。"""
        from .plugin_tool_service import channel_allows_plugin_tools

        return channel_allows_plugin_tools(getattr(self.plugin, "qq_client", None))

    # ── 登记 ────────────────────────────────────────────────────────

    async def register(
        self,
        *,
        plugin_id: str,
        entry_id: str,
        field: str,
        task_id: str,
        poller: str,
        conversation: dict[str, Any] | None,
    ) -> bool:
        """**登记入口**：先确认"这个 id 在那条 entry 里真的查得到进度"，再记账。

        为什么要多问这一句：真机上 `music_pusher:create_schedule_task` 也回
        `{"task_id": …, "status": "pending"}` —— 但它是个**排程任务**，永远没有
        "完成"那一刻，而 `status` 只是它自己的配置状态。照 `task_id` 的形状去登记，
        过 15 分钟她就会对使用者说一句"那个任务跟丢了"——**假警报比不说更糟**。
        所以登记前拿 `poller` + 那个字段名真问一次：查不到 / 认不出进度词 → 不登记，
        调用方也就不会说"我告诉你"。
        """
        probe = {
            "plugin_id": str(plugin_id or "").strip(),
            "poller": str(poller or "").strip(),
            "field": str(field or "task_id").strip() or "task_id",
            "task_id": str(task_id or "").strip(),
        }
        if not probe["plugin_id"] or not probe["task_id"] or not probe["poller"]:
            return False
        if not self.enabled():
            return False
        if not _conversation_target(dict(conversation or {})):
            return False
        if not await self.probe_is_watchable(probe):
            emit_bridge_log(self.plugin, 
                "INFO",
                f"[PluginTool·回投] {probe['plugin_id']}:{probe['poller']} 查不到 "
                f"{probe['task_id']} 的进度，不登记（也就不会承诺）",
            )
            return False
        return self.remember(
            plugin_id=probe["plugin_id"], entry_id=entry_id, field=probe["field"],
            task_id=probe["task_id"], poller=probe["poller"], conversation=conversation,
        )

    async def probe_is_watchable(self, record: dict[str, Any]) -> bool:
        """这一趟"问进度"能不能认出一个已知的进度词（且 id 对得上）。"""
        ok, payload = await self._query_task(record)
        if not ok:
            return False
        token = status_token(payload)
        if not token or normalize_status(token) == "unknown":
            return False
        if isinstance(payload, dict):
            echoed = str(payload.get("task_id") or payload.get(record.get("field") or "") or "").strip()
            if echoed and echoed != str(record.get("task_id") or ""):
                # 查是查到了，但不是这个任务（很多插件对未知 id 会回默认对象）。
                return False
        return True

    def remember(
        self,
        *,
        plugin_id: str,
        entry_id: str,
        field: str,
        task_id: str,
        poller: str,
        conversation: dict[str, Any] | None,
    ) -> bool:
        """登记一条"等结果"的任务（**纯记账**，不做任何 IO）。

        返回 True 表示**真的会回投**（调用方据此决定措辞）。入口请用 `register`：
        它会先确认"这个 id 真的查得到进度"再记账。
        """
        if not self.enabled():
            return False
        plugin_id = str(plugin_id or "").strip()
        task_id = str(task_id or "").strip()
        poller = str(poller or "").strip()
        field = str(field or "task_id").strip() or "task_id"
        if not plugin_id or not task_id:
            return False
        # `poller` 那道闸在 `register`（唯一入口）里 —— 这里不再重复判一遍：
        # 两处各判一次，拆掉任何一处都测不出来（另一处兜着），等于两道闸都没有证据。
        convo = dict(conversation or {})
        if not _conversation_target(convo):
            return False
        key = f"{plugin_id}:{task_id}"
        if key in self._pending:
            return False
        if len(self._pending) >= MAX_PENDING:
            emit_bridge_log(self.plugin, 
                "INFO",
                f"[PluginTool·回投] 已在等 {len(self._pending)} 个任务，"
                f"{key} 不再登记（上限 {MAX_PENDING}）",
            )
            return False
        if self._conversation_pending(convo) >= MAX_PENDING_PER_CONVERSATION:
            emit_bridge_log(self.plugin, "INFO", f"[PluginTool·回投] 这个会话已经在等结果了，{key} 不再登记")
            return False
        self._pending[key] = {
            "key": key,
            "plugin_id": plugin_id,
            "entry_id": str(entry_id or "").strip(),
            "field": field,
            "task_id": task_id,
            "poller": poller,
            "conversation": convo,
            "created_at": time.time(),
            "attempts": 0,
            "errors": 0,
            "status": "queued",
            "terminal": "",
            "output": "",
            "last_block_reason": "",
            "last_block_at": 0.0,
        }
        self._save()
        emit_bridge_log(self.plugin, 
            "INFO",
            f"[PluginTool·回投] 已登记异步任务 {key}（{_conversation_label(convo)}），"
            f"到点后去问 {plugin_id}:{poller}",
        )
        self.ensure_loop()
        return True

    def _conversation_pending(self, convo: dict[str, Any]) -> int:
        target = _conversation_target(convo)
        return sum(
            1 for row in self._pending.values()
            if _conversation_target(row.get("conversation") or {}) == target
        )

    # ── 后台轮询 ────────────────────────────────────────────────────

    def ensure_loop(self) -> None:
        """确保轮询协程在跑（幂等）。没在等任务时不占循环；没有事件循环时静默跳过。"""
        task = self._loop_task
        if task is not None and not task.done():
            return
        if not self._pending:
            return
        try:
            import asyncio

            self._loop_task = asyncio.get_running_loop().create_task(self._loop())
        except RuntimeError:
            self._loop_task = None

    async def _loop(self) -> None:
        import asyncio

        try:
            while self._pending:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.plugin.logger.warning("结果回投轮询出错", exc_info=True)
                if not self._pending:
                    break
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        finally:
            self._loop_task = None

    async def tick(self) -> None:
        """走一轮：问进度 / 投递。测试直接调它，不必真的等 `POLL_INTERVAL_SECONDS`。"""
        now = time.time()
        for key, record in list(self._pending.items()):
            if record.get("terminal"):
                await self._settle(key, record)
                continue
            if now - float(record.get("created_at") or 0.0) < FIRST_DELAY_SECONDS:
                continue
            if now - float(record.get("created_at") or 0.0) > MAX_WAIT_SECONDS:
                self._mark_terminal(record, "timeout")
                await self._settle(key, record)
                continue
            if int(record.get("attempts") or 0) >= MAX_ATTEMPTS:
                self._mark_terminal(record, "timeout")
                await self._settle(key, record)
                continue
            await self._poll_once(key, record)

    async def _poll_once(self, key: str, record: dict[str, Any]) -> None:
        record["attempts"] = int(record.get("attempts") or 0) + 1
        ok, payload = await self._query_task(record)
        if not ok:
            record["errors"] = int(record.get("errors") or 0) + 1
            if int(record["errors"]) >= ERRORS_BEFORE_LOST:
                emit_bridge_log(self.plugin, 
                    "WARN",
                    f"[PluginTool·回投] {key} 连续 {record['errors']} 次查不到"
                    f"（{record.get('last_error') or ''}），按「跟丢了」处理",
                )
                self._mark_terminal(record, "lost")
                await self._settle(key, record)
                return
            self._save()
            return
        record["errors"] = 0
        token = status_token(payload)
        state = normalize_status(token)
        changed = str(record.get("status") or "") != (token or state)
        record["status"] = token or state
        if state == "done":
            record["output"] = extract_result(payload)
            if not record["output"]:
                # 说是完成了却没有正文：不能编，按"跟丢了"处理。
                self._mark_terminal(record, "lost")
            else:
                self._mark_terminal(record, "done")
        elif state == "failed":
            record["output"] = extract_result(payload) or _error_text(payload)
            self._mark_terminal(record, "failed")
        else:
            if changed:
                emit_bridge_log(self.plugin, 
                    "INFO", f"[PluginTool·回投] {key} 进度：{token or '（认不出，继续等）'}"
                )
            self._save()
            return
        await self._settle(key, record)

    async def _query_task(self, record: dict[str, Any]) -> tuple[bool, Any]:
        """去问那个插件"任务好了没"。返回 ``(问到了吗, 结果)``。"""
        plugins = getattr(self.plugin, "plugins", None)
        call_entry = getattr(plugins, "call_entry", None)
        if not callable(call_entry):
            record["last_error"] = "宿主没有跨插件调用接口"
            return False, None
        target = f"{record['plugin_id']}:{record['poller']}"
        params = {record.get("field") or "task_id": record.get("task_id")}
        from .plugin_tool_service import CALL_TIMEOUT_SECONDS

        try:
            result = await call_entry(target, params, timeout=CALL_TIMEOUT_SECONDS)
        except Exception as exc:
            record["last_error"] = f"{type(exc).__name__}: {exc}"
            return False, None
        is_err = getattr(result, "is_err", None)
        if callable(is_err) and is_err():
            record["last_error"] = str(getattr(result, "error", result) or "")
            return False, None
        return True, getattr(result, "value", result)

    def _mark_terminal(self, record: dict[str, Any], terminal: str) -> None:
        record["terminal"] = terminal
        record["settled_at"] = time.time()
        if not record.get("output"):
            record["output"] = self._fallback_output(terminal)
        self._save()
        emit_bridge_log(self.plugin, 
            "INFO",
            f"[PluginTool·回投] {record.get('key')} 终态：{terminal}",
        )

    def _fallback_output(self, terminal: str) -> str:
        if terminal == "timeout":
            return "（等了很久也没有结果，任务可能卡住了）"
        if terminal == "lost":
            return "（任务查不到了：那个插件可能重启过，或者任务已经被清理）"
        return "（没有拿到具体内容）"

    # ── 回投 ────────────────────────────────────────────────────────

    async def _settle(self, key: str, record: dict[str, Any]) -> None:
        delivered, reason = await self.deliver(record)
        if delivered:
            self._pending.pop(key, None)
            self._save()
            return
        self._note_blocked(record, reason, key)

    def _note_blocked(self, record: dict[str, Any], reason: str, key: str) -> None:
        """投不出去（值班停了 / 通道换了）：留着下次再说，但别每 5s 刷一条日志。"""
        now = time.time()
        if (
            reason != record.get("last_block_reason")
            or now - float(record.get("last_block_at") or 0.0) >= BLOCKED_LOG_INTERVAL_SECONDS
        ):
            record["last_block_reason"] = reason
            record["last_block_at"] = now
            emit_bridge_log(self.plugin, 
                "INFO", f"[PluginTool·回投] {key} 暂时发不出去（{reason}），先留着"
            )
        self._save()

    async def deliver(self, record: dict[str, Any]) -> tuple[bool, str]:
        """把一条终态结果**主动**发回原会话。返回 ``(发出去了吗, 原因)``。"""
        if not self.enabled():
            return False, "开关关掉了"
        if not bool(getattr(self.plugin, "_running", False)):
            return False, "值班已停"
        if not self.channel_allowed():
            return False, "当前通道不挂插件工具"
        pipeline = getattr(self.plugin, "reply_pipeline", None)
        if pipeline is None:
            return False, "回复管线未就绪"
        convo = dict(record.get("conversation") or {})
        target = _conversation_target(convo)
        if not target:
            return False, "没有可投递的会话"
        is_group = bool(convo.get("is_group"))
        permission = str(convo.get("permission_level") or "").strip().lower()
        if not is_group and permission in ("", "none"):
            return False, "说话人已不在名册里"
        request = QQReplyRequest(
            message_text=self.render_followup_prompt(record),
            sender_id=str(convo.get("sender_id") or "") if not is_group else str(self._nominal_group_sender()),
            is_group=is_group,
            group_id=str(convo.get("group_id") or "") if is_group else None,
            user_nickname=convo.get("user_nickname") or None,
            source_kind=KIND_PLUGIN_TOOL_RESULT,
            # 与"那一轮"完全一致的记忆策略：这就是同一场对话的下一轮，
            # 不是另起一个临时会话。
            use_memory_context=convo.get("use_memory_context"),
            persist_memory=convo.get("persist_memory"),
            ephemeral_session=bool(convo.get("ephemeral_session", False)),
            group_facing=is_group,
            group_scene_mode=str(convo.get("group_scene_mode") or ("group_collective" if is_group else "")),
            fallback_to_text_on_voice_failure=False,
            permission_level_override=("trusted" if is_group else permission),
            force_reply=True,
        )
        # 「正要发」的记号**先落盘再发**：主动消息宁可少一条，也不能重复。
        # 如果在这一步之后进程挂了，重启时 `_load` 会认出这个记号并放弃它
        # （可能已经发出去了，只是我们没来得及记账）。
        record["delivering_at"] = time.time()
        self._save()
        try:
            outcome = await pipeline.run(request)
        except Exception as exc:
            record.pop("delivering_at", None)
            self._save()
            self.plugin.logger.warning(f"结果回投失败: {exc}", exc_info=True)
            return False, f"投递异常 {type(exc).__name__}"
        delivered, why = _read_delivery(outcome)
        if not delivered:
            # 没送出去（没生成 / 投递结果说没送）：把记号**撤掉**，重启后还能再试。
            record.pop("delivering_at", None)
            self._save()
            return False, why
        record["delivered_at"] = time.time()
        record["delivered_text"] = str(getattr(outcome, "reply_text", "") or "")[:500]
        try:
            self.plugin.runtime_service.record_pipeline_outcome(
                source=request.source_kind, request=request, outcome=outcome,
            )
        except Exception:
            pass
        emit_bridge_log(self.plugin, 
            "INFO",
            f"[PluginTool·回投] {record.get('key')} 已回投到"
            f"{_conversation_label(convo)}：{record['delivered_text'][:80]}",
        )
        return True, "已发出"

    def _nominal_group_sender(self) -> str:
        """群聊回投的名义发言人：与 `QQProactiveMessageService` 同口径（管理员）。"""
        return str(getattr(self.plugin, "_admin_qq", "") or "0")

    def render_followup_prompt(self, record: dict[str, Any]) -> str:
        """终态结果 → 这一轮的 prompt（也是给她的**行为约束**）。"""
        plugin_id = str(record.get("plugin_id") or "")
        entry_id = str(record.get("entry_id") or "")
        where = f"「{plugin_id}」的「{entry_id}」" if entry_id else f"「{plugin_id}」"
        output = str(record.get("output") or "").strip() or "（没有拿到具体内容）"
        terminal = str(record.get("terminal") or "")
        promise = "你之前答应过对方：这件事有结果就告诉他。"
        if terminal == "done":
            head = (
                f"【后台任务的结果】{promise}现在结果回来了 —— "
                f"这是你当时调用{where}拿到的内容："
            )
            tail = (
                "请用你自己的语气，直接把这个结论告诉对方（一两句话就够）。"
                "不要提到插件、系统、后台、异步任务、task_id 这类词；"
                "不要照着念上面的原文；也不要再问对方「要不要看」—— 你现在就是来告诉他结果的。"
            )
        elif terminal == "failed":
            head = (
                f"【后台任务的结果】{promise}"
                f"但你当时调用{where}的那个任务**失败了**，原始信息是："
            )
            tail = (
                "请用你自己的语气说一句：简短、别找借口、别编造结果；"
                "不要提到插件、系统或后台这类词。可以问问对方要不要再来一次。"
            )
        else:
            head = (
                f"【后台任务的结果】{promise}"
                f"但你当时调用{where}的那个任务**没能跟到最后**"
                "（可能中途重启过，或者任务已经没了），手上只有这些信息："
            )
            tail = (
                "请用你自己的语气如实说一句没跟上、别编造结果、也别假装成功；"
                "可以问问对方要不要重新来一次。不要提到插件内部、后台或 task_id。"
            )
        return f"{head}\n{output}\n\n{tail}"

    # ── 观测（界面/查询用） ─────────────────────────────────────────

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.time()
        rows: list[dict[str, Any]] = []
        for record in self._pending.values():
            convo = record.get("conversation") or {}
            rows.append({
                "plugin_id": record.get("plugin_id", ""),
                "entry_id": record.get("entry_id", ""),
                "task_id": record.get("task_id", ""),
                "poller": record.get("poller", ""),
                "conversation": _conversation_label(convo),
                "is_group": bool(convo.get("is_group")),
                "status": record.get("terminal") or record.get("status") or "queued",
                "attempts": int(record.get("attempts") or 0),
                "age_seconds": round(max(0.0, now - float(record.get("created_at") or now)), 1),
            })
        rows.sort(key=lambda row: row["age_seconds"], reverse=True)
        return rows

    def pending_count(self) -> int:
        return len(self._pending)

    def pending_items_for(
        self, *, is_group: bool, group_id: str = "", sender_id: str = "",
    ) -> list[str]:
        """**这条会话**还在等的结果，给提示词用（一行一项，没有就返回空列表）。

        为什么值得进 prompt：这是唯一一处"她手上还没交的活"——它不进记忆、也不在模型的
        消息历史里（模型只知道自己上一轮说过"出来了我告诉你"）。写进当轮提示词，她就不会
        （a）重复承诺一次、（b）在结果回来之前当成已经办完。
        量很小：只在真的有在办的事时出现，且最多 `PENDING_PROMPT_MAX_ITEMS` 项。
        """
        convo = {
            "is_group": bool(is_group),
            "group_id": str(group_id or "").strip(),
            "sender_id": str(sender_id or "").strip(),
        }
        target = _conversation_target(convo)
        if not target:
            return []
        now = time.time()
        rows = [
            record for record in self._pending.values()
            if _conversation_target(record.get("conversation") or {}) == target
        ]
        rows.sort(key=lambda record: float(record.get("created_at") or 0.0))
        items: list[str] = []
        for record in rows[:PENDING_PROMPT_MAX_ITEMS]:
            waited = int(max(0.0, now - float(record.get("created_at") or now)))
            items.append(f"{record.get('plugin_id') or '?'}（已等 {waited}s）")
        return items

    @staticmethod
    def max_pending() -> int:
        """同时在等的任务上限（界面显示用，别在两处各写一份数字）。"""
        return MAX_PENDING

    # ── 落盘 ────────────────────────────────────────────────────────

    def _state_path(self) -> str:
        return str(self.plugin.data_path(FOLLOWUP_STATE_FILE))

    def _load(self) -> None:
        import os

        path = self._state_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.loads(handle.read())
        except Exception:
            self.plugin.logger.warning("读取结果回投状态失败（当作没有）", exc_info=True)
            return
        now = time.time()
        rows = raw.get("pending") if isinstance(raw, dict) else raw
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip()
            created = float(row.get("created_at") or 0.0)
            if not key or not created or now - created > MAX_WAIT_SECONDS:
                continue
            if not _conversation_target(row.get("conversation") or {}):
                continue
            if row.get("delivering_at"):
                # 上次是在"正要发"的时候停的（宿主重启/进程挂了）：这条多半已经
                # 发出去了，只是没来得及记账。主动消息宁可少一条，不能重复 ——
                # 所以放弃它，而不是重启后再发一遍。
                emit_bridge_log(self.plugin, 
                    "INFO",
                    f"[PluginTool·回投] {key} 上次停在「正要发」，重启后不再重发（可能已经发出）",
                )
                continue
            self._pending[key] = row

    def _save(self) -> None:
        import os

        try:
            path = self._state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"pending": list(self._pending.values())}, handle, ensure_ascii=False)
        except Exception:
            self.plugin.logger.warning("写入结果回投状态失败", exc_info=True)


def _read_delivery(outcome: Any) -> tuple[bool, str]:
    """这一轮到底**送出去了没有**。

    判据取投递管线自己的结论（`outcome.delivery_result.delivered`），不是"有没有
    生成出文字" —— 生成了但没送出去（投递计划为空、被吞掉）也是一条没兑现的承诺。
    只有拿不到 `delivery_result` 时才回退到"有 reply_text 就算送到"（老/旁路实现）。
    """
    result = getattr(outcome, "delivery_result", None)
    delivered = getattr(result, "delivered", None)
    reply_text = getattr(outcome, "reply_text", None)
    if delivered is None:
        return bool(reply_text), ("没生成出可发送的内容" if not reply_text else "已发出")
    if bool(delivered):
        return True, "已发出"
    if not reply_text:
        return False, "没生成出可发送的内容"
    return False, "生成了但投递没成功（delivered=False）"


def _error_text(payload: Any) -> str:
    """失败态里那句原因（取 ``error`` / ``message`` / 整份渲染）。"""
    if isinstance(payload, dict):
        for key in ("error", "message", "reason", "detail"):
            value = payload.get(key)
            if value:
                return render_payload(value)
    return render_payload(payload)


def _conversation_target(convo: dict[str, Any]) -> str:
    """这条会话能不能投递：群看 group_id，私聊看 sender_id。"""
    if bool(convo.get("is_group")):
        return str(convo.get("group_id") or "").strip()
    return str(convo.get("sender_id") or "").strip()


def _conversation_label(convo: dict[str, Any]) -> str:
    if bool(convo.get("is_group")):
        return f"群 {convo.get('group_id') or '?'}"
    return f"私聊 {convo.get('sender_id') or '?'}"
