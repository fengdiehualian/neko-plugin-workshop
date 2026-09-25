"""Coordinates action transport, tracking, feedback and reconnect recovery."""

import asyncio
import uuid
from typing import Any, Dict, Iterable, Optional

from .feedback import ActionFeedbackHandler
from .models import TERMINAL_STATUSES, ActionRecord, ActionTracker
from .registry import ActionRegistry, ActionValidationError

ACTION_EVENT_TYPES = frozenset({"maid_action_progress", "maid_action_finished"})
REMOTE_LOAD_RETRY_COUNT = 5
REMOTE_LOAD_RETRY_DELAY = 0.1


class MaidActionService:
    def __init__(self, plugin, *, progress_interval: float = 1.5, clock=None):
        self.plugin = plugin
        self.tracker = ActionTracker()
        self.registry = ActionRegistry()
        self.feedback = ActionFeedbackHandler(
            plugin, progress_interval=progress_interval, clock=clock
        )
        self._event_consumer = None
        self._owners: Dict[str, tuple[str, str]] = {}

    def register_event_consumer(self, consumer) -> None:
        """Install the high-level skill event consumer.

        There is intentionally only one body-orchestration consumer.  Child
        actions claimed with the ``internal`` feedback policy still update the
        tracker, but never wake the main LLM independently of their skill.
        """
        self._event_consumer = consumer

    def unregister_event_consumer(self, consumer=None) -> None:
        if consumer is None or self._event_consumer is consumer:
            self._event_consumer = None

    def claim_action(self, action_id: str, owner_id: str, *, feedback_policy: str = "internal") -> None:
        action_id = str(action_id or "").strip()
        owner_id = str(owner_id or "").strip()
        policy = str(feedback_policy or "internal").strip().lower()
        if not action_id or not owner_id:
            raise ValueError("action_id and owner_id are required")
        if policy not in {"internal", "external"}:
            raise ValueError("feedback_policy must be internal or external")
        self._owners[action_id] = (owner_id, policy)

    def release_action(self, action_id: str, owner_id: str = "") -> None:
        action_id = str(action_id or "").strip()
        current = self._owners.get(action_id)
        if current is None:
            return
        if owner_id and current[0] != str(owner_id):
            return
        self._owners.pop(action_id, None)

    async def handle_message(self, message: Dict[str, Any]) -> bool:
        msg_type = str((message or {}).get("type") or "")
        if msg_type not in ACTION_EVENT_TYPES:
            return False
        payload = self._payload(message)
        record, accepted = self.tracker.apply(payload)
        if not accepted or record is None:
            status = str(payload.get("status") or "").upper()
            if status in TERMINAL_STATUSES:
                current = self.tracker.get(str(payload.get("action_id") or ""))
                logger = getattr(self.plugin, "logger", None)
                if logger is not None:
                    logger.warning(
                        "[MaidAgent] terminal event rejected action_id=%s "
                        "incoming=%s/%s current=%s/%s",
                        payload.get("action_id"), payload.get("generation"),
                        payload.get("sequence"),
                        getattr(current, "generation", None),
                        getattr(current, "sequence", None),
                    )
            return True
        await self._dispatch_record(msg_type, record, payload)
        return True

    async def _dispatch_record(
        self, event_type: str, record: ActionRecord, payload: Dict[str, Any]
    ) -> None:
        owner = self._owners.get(record.action_id)
        consumer = self._event_consumer
        claimed = owner is not None
        if not claimed and consumer is not None:
            claims = getattr(consumer, "claims", None)
            if callable(claims):
                claimed = bool(claims(record.action_id))

        consumed = False
        if claimed and consumer is not None:
            callback = getattr(consumer, "on_action_event", None)
            if callable(callback):
                try:
                    callback_result = await callback(
                        event_type, record.as_dict(), dict(payload or {})
                    )
                    # Existing consumers that return None historically mean
                    # "consumed". A deliberate False means the owner/checkpoint
                    # was missing, so terminal feedback must fall back to the
                    # ordinary LLM path instead of disappearing silently.
                    consumed = callback_result is not False
                except Exception as exc:
                    logger = getattr(self.plugin, "logger", None)
                    if logger is not None:
                        logger.exception(
                            "[MaidAgent] skill event consumer failed for %s: %s",
                            record.action_id, exc,
                        )

        internal = consumed and (owner is None or owner[1] == "internal")
        if not internal:
            if event_type == "maid_action_finished" or record.status in TERMINAL_STATUSES:
                await self.feedback.finished(record)
            elif bool(payload.get("requires_decision", False)
                      or payload.get("decision_required", False)):
                await self.feedback.decision_required(record)
            else:
                await self.feedback.progress(record)

        if record.terminal:
            logger = getattr(self.plugin, "logger", None)
            if logger is not None:
                logger.info(
                    "[MaidAgent] terminal dispatched action_id=%s generation=%s "
                    "sequence=%s claimed=%s consumed=%s route=%s",
                    record.action_id, record.generation, record.sequence,
                    claimed, consumed, "skill" if internal else "action_fallback",
                )
            self.release_action(record.action_id)

    async def start_action(
        self,
        *,
        action_id: str,
        maid_id: str,
        kind: str,
        args: Dict[str, Any],
        timeout_ms: Optional[int] = None,
        replace_existing: bool = True,
        owner_id: str = "",
        feedback_policy: str = "external",
    ) -> Dict[str, Any]:
        """Start an action without passing through an LLM tool wrapper."""
        if not getattr(self.plugin, "connected", False):
            return self._error("NOT_CONNECTED", "Not connected to Minecraft")
        if not getattr(self.plugin, "_maid_agent_enabled", True):
            return self._error("MAID_AGENT_DISABLED", "Maid Agent actions are disabled")
        maid_id = str(maid_id or "").strip()
        if not maid_id:
            return self._error("NO_MAID_ASSIGNED", "No maid assigned")
        action_id = str(action_id or uuid.uuid4()).strip()
        kind = str(kind or "").strip().lower()
        try:
            normalized_args = self.registry.normalize(kind, args or {})
            if (
                kind == "harvest_blocks"
                and self.registry.is_ore_selector(normalized_args.get("selector"))
            ):
                # 无论从公开还是内部入口启动，矿石勘探都必须持续进行；
                # 调用方传入的有限超时不得静默限制搜索。
                timeout_ms = 0
            elif timeout_ms is None:
                # Returning through a deep/branched mine is intentionally a
                # long-running server-owned operation.  A generic one-minute
                # default would terminate a healthy return midway, especially
                # when the route must repair support or bridge a gap.
                timeout_ms = 0 if kind == "return_to_position" else 60000
            timeout_ms = int(timeout_ms)
        except (ActionValidationError, TypeError, ValueError) as exc:
            return self._error("INVALID_ACTION_ARGUMENTS", str(exc), action_id=action_id)
        if timeout_ms != 0 and not 1000 <= timeout_ms <= 120000:
            return self._error(
                "INVALID_ACTION_ARGUMENTS",
                "timeout_ms must be 0 or between 1000 and 120000",
                action_id=action_id,
            )

        if owner_id:
            self.claim_action(
                action_id, owner_id, feedback_policy=feedback_policy
            )
        request = {
            "type": "start_maid_action",
            "data": {
                "action_id": action_id,
                "maid_id": maid_id,
                "kind": kind,
                "timeout_ms": timeout_ms,
                "replace_existing": bool(replace_existing),
                "args": normalized_args,
            },
        }
        try:
            response = await self.send_start_request(request)
        except Exception as exc:
            self.release_action(action_id, owner_id)
            return self._error("REQUEST_FAILED", str(exc), action_id=action_id)
        if response.get("type") == "error":
            self.release_action(action_id, owner_id)
            return self._error(
                "REQUEST_FAILED", str(response.get("data", {})), action_id=action_id
            )
        records = self.observe_response(response)
        data = self._payload(response)
        accepted = data.get("accepted", data.get("success", True))
        if not accepted:
            self.release_action(action_id, owner_id)
            return self._error(
                str(data.get("error_code") or "ACTION_REJECTED"),
                str(data.get("error") or data.get("message")
                    or data.get("rejection_reason") or "Action rejected"),
                action_id=action_id,
                response=data,
            )
        snapshot = records[0].as_dict() if records else {}
        return {"success": True, "accepted": True, "action_id": action_id, **data, **snapshot}

    async def send_start_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """发送动作启动请求，并透明等待远程女仆的 NBT 加载。"""
        response: Dict[str, Any] = {}
        for attempt in range(REMOTE_LOAD_RETRY_COUNT):
            response = await self.plugin._send_request(request)
            if response.get("type") == "error":
                return response
            data = self._payload(response)
            accepted = data.get("accepted", data.get("success", True))
            error_code = str(data.get("error_code") or data.get("rejection_reason") or "")
            if accepted or error_code != "MAID_LOAD_PENDING":
                return response
            if attempt + 1 < REMOTE_LOAD_RETRY_COUNT:
                await asyncio.sleep(REMOTE_LOAD_RETRY_DELAY)
        return response

    async def cancel_action(self, action_id: str, *, maid_id: str = "") -> Dict[str, Any]:
        if not getattr(self.plugin, "connected", False):
            return self._error("NOT_CONNECTED", "Not connected to Minecraft")
        action_id = str(action_id or "").strip()
        if not action_id:
            return self._error("INVALID_ACTION_ARGUMENTS", "action_id is required")
        data = {"action_id": action_id}
        if maid_id:
            data["maid_id"] = str(maid_id)
        response = await self.plugin._send_request(
            {"type": "cancel_maid_action", "data": data}
        )
        if response.get("type") == "error":
            return self._error("REQUEST_FAILED", str(response.get("data", {})), action_id=action_id)
        self.observe_response(response)
        result = self._payload(response)
        if not result.get("accepted", result.get("success", True)):
            return self._error(
                str(result.get("error_code") or "CANCEL_REJECTED"),
                str(result.get("error") or result.get("message")
                    or result.get("rejection_reason") or "Cancel rejected"),
                action_id=action_id,
                response=result,
            )
        return {"success": True, "accepted": True, "action_id": action_id, **result}

    async def get_action_status(self, action_id: str) -> Optional[Dict[str, Any]]:
        action_id = str(action_id or "").strip()
        if not action_id or not getattr(self.plugin, "connected", False):
            return None
        response = await self.plugin._send_request({
            "type": "get_maid_action_status",
            "data": {"action_id": action_id},
        }, timeout=5)
        if self._is_not_found(response):
            return None
        if response.get("type") == "error":
            return {
                "action_id": action_id,
                "_query_error": True,
                "error": self._payload(response),
            }
        records = self.observe_response(response)
        if records:
            return records[0].as_dict()
        data = self._payload(response)
        return data or None

    async def list_active_actions(self, *, maid_id: str = "") -> list[Dict[str, Any]]:
        result = await self.query_active_actions(maid_id=maid_id)
        return list(result.get("actions") or []) if result.get("success") else []

    async def query_active_actions(self, *, maid_id: str = "") -> Dict[str, Any]:
        """Query the authoritative server list without hiding transport errors.

        ``list_active_actions`` keeps its legacy best-effort list contract for
        existing callers.  Activity arbitration must use this strict variant:
        an empty list and a failed query have very different safety meanings.
        """
        if not getattr(self.plugin, "connected", False):
            return {
                "success": False,
                "error_code": "NOT_CONNECTED",
                "error": "Not connected to Minecraft",
                "actions": [],
            }
        data = {"maid_id": str(maid_id)} if maid_id else {}
        try:
            response = await self.plugin._send_request(
                {"type": "list_active_maid_actions", "data": data}, timeout=5
            )
        except Exception as exc:
            return {
                "success": False,
                "error_code": "REQUEST_FAILED",
                "error": str(exc),
                "actions": [],
            }
        if not isinstance(response, dict):
            return {
                "success": False,
                "error_code": "INVALID_RESPONSE",
                "error": "list_active_maid_actions returned a non-object response",
                "actions": [],
            }
        if response.get("type") == "error":
            return {
                "success": False,
                "error_code": "REQUEST_FAILED",
                "error": self._payload(response),
                "actions": [],
            }
        payload = self._payload(response)
        if payload.get("error"):
            return {
                "success": False,
                "error_code": str(
                    payload.get("error_code") or "LIST_ACTIONS_FAILED"
                ),
                "error": payload.get("error"),
                "actions": [],
            }
        self.observe_response(response)
        actions = [
            dict(item) for item in self._extract_actions(payload)
        ]
        return {"success": True, "actions": actions}

    def observe_response(self, message: Dict[str, Any]) -> list:
        """Update local snapshots from request responses without emitting feedback."""
        msg_type = str((message or {}).get("type") or "")
        payload = self._payload(message)
        records = []
        if msg_type == "maid_action_list":
            items = self._extract_actions(payload)
        elif msg_type in {
            "maid_action_start_result",
            "maid_action_cancel_result",
            "maid_action_status",
        }:
            items = [payload]
        else:
            items = []
        for item in items:
            record, accepted = self.tracker.apply(item)
            if accepted and record is not None:
                records.append(record)
        return records

    async def reconcile(
        self, *, expected_action_ids: Iterable[str] = (), maid_ids: Iterable[str] = ()
    ) -> Dict[str, Any]:
        """Adopt server actions and recover terminal states after a reconnect."""
        maid_id = ""
        resolver = getattr(self.plugin, "_resolve_maid_id", None)
        if callable(resolver):
            maid_id = str(resolver() or "")
        explicit_maids = [str(value) for value in maid_ids if str(value or "").strip()]
        if explicit_maids and not maid_id:
            maid_id = explicit_maids[0]
        list_data = {"maid_id": maid_id} if maid_id else {}
        response = await self.plugin._send_request(
            {"type": "list_active_maid_actions", "data": list_data}, timeout=5
        )
        if response.get("type") == "error":
            return {"success": False, "error": response.get("data", {})}

        data = self._payload(response)
        server_items = self._extract_actions(data)
        server_ids = set()
        adopted = []
        for item in server_items:
            action_id = str(item.get("action_id") or "")
            if not action_id:
                continue
            server_ids.add(action_id)
            was_known = self.tracker.get(action_id) is not None
            record, accepted = self.tracker.apply(item)
            if accepted and record is not None and not was_known:
                adopted.append(action_id)

        recovered = []
        lost = []
        unresolved = []
        expected = {
            str(action_id) for action_id in expected_action_ids
            if str(action_id or "").strip()
        }
        local_by_id = {record.action_id: record for record in self.tracker.active()}
        ids_to_query = set(local_by_id) | expected
        for action_id in sorted(ids_to_query):
            if action_id in server_ids:
                continue
            status_response = await self.plugin._send_request({
                "type": "get_maid_action_status",
                "data": {"action_id": action_id},
            }, timeout=5)
            if self._is_not_found(status_response):
                record = local_by_id.get(action_id)
                if record is not None:
                    lost_record, accepted = self.tracker.mark_server_state_lost(record)
                    if accepted:
                        lost.append(action_id)
                        await self._dispatch_record(
                            "maid_action_finished", lost_record, lost_record.as_dict()
                        )
                else:
                    lost.append(action_id)
            elif status_response.get("type") != "error":
                status_data = self._payload(status_response)
                updated, accepted = self.tracker.apply(status_data)
                if accepted and updated is not None:
                    recovered.append(action_id)
                    await self._dispatch_record(
                        "maid_action_finished" if updated.terminal else "maid_action_progress",
                        updated,
                        status_data,
                    )
            else:
                unresolved.append(action_id)

        return {
            "success": True,
            "active": [record.as_dict() for record in self.tracker.active()],
            "adopted": adopted,
            "recovered": recovered,
            "lost": lost,
            "unresolved": unresolved,
        }

    @staticmethod
    def _payload(message: Dict[str, Any]) -> Dict[str, Any]:
        data = (message or {}).get("data", {})
        return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def _extract_actions(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        for key in ("actions", "active_actions"):
            items = payload.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    @staticmethod
    def _is_not_found(message: Dict[str, Any]) -> bool:
        payload = MaidActionService._payload(message)
        code = str(payload.get("error_code") or payload.get("code") or "").upper()
        if code in {"ACTION_NOT_FOUND", "NOT_FOUND", "UNKNOWN_ACTION"}:
            return True
        text = str(payload.get("message") or payload.get("error") or "").lower()
        return "not found" in text or "unknown action" in text

    @staticmethod
    def _error(code: str, message: str, **details: Any) -> Dict[str, Any]:
        return {
            "success": False,
            "error_code": str(code or "REQUEST_FAILED"),
            "error": str(message or "Request failed"),
            **details,
        }
