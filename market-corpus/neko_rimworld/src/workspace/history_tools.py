"""One historical read surface for retained exchanges and full reports."""

import asyncio
import copy
import re

from ..reports import MAX_REPORT_BYTES, ReportError
from ..tool_contracts import ToolExecutionError, ToolSpec


def make_history_tool(*, storage=None, workspace_id=None, owner=None, turn_id=None, report_store=None, question=""):
    references = frozenset(re.findall(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", question))

    async def read(args):
        try:
            action = args["action"]
            if action == "list":
                offset, limit = args.get("offset", 0), args.get("limit", 5)
                result = {"entries": [], "reports": [], "total": 0, "offset": offset}
                if storage is not None:
                    result.update(await storage.call("history", workspace_id, owner, before_turn=turn_id, offset=offset, limit=limit))
                if report_store is not None and workspace_id is not None:
                    result.update(await asyncio.to_thread(report_store.list, offset, limit, workspace_id=workspace_id))
            elif action == "read":
                if args.get("report_id"):
                    if args.get("turn_id") or args.get("paths"):
                        raise ValueError("Choose one report or work turn")
                    if report_store is None:
                        raise ValueError("Historical reports are unavailable")
                    record = await asyncio.to_thread(report_store.read, args["report_id"])
                    same_work = workspace_id is not None and record.get("workspace_id") == workspace_id
                    if not same_work and record["report_id"] not in references:
                        raise ReportError("report_not_found", "This report is not available in the current work.")
                    if "observation_id" in args or "index" in args:
                        observations = record.get("observations", [])
                        if "observation_id" in args:
                            matches = [item for item in observations if item.get("observation_id") == args["observation_id"]]
                            if len(matches) != 1:
                                raise ValueError("Choose an observation from this report's evidence index")
                            observation = matches[0]
                        else:
                            index = args["index"]
                            if type(index) is not int or not 0 <= index < len(observations):
                                raise ValueError("Choose an observation index from this report's evidence index")
                            observation = observations[index]
                        result = {"report_id": record["report_id"], "workspace_id": record.get("workspace_id"), "observation": copy.deepcopy(observation)}
                    else:
                        result = report_store.reference(record)
                    result["reference_scope"] = "current_work" if same_work else "explicit_question_reference"
                else:
                    if storage is None or "turn_id" not in args:
                        raise ValueError("Provide a historical turn_id or report_id")
                    requested = await storage.call("turn", workspace_id, owner, args["turn_id"])
                    current = await storage.call("turn", workspace_id, owner, turn_id)
                    if requested["sequence"] >= current["sequence"]:
                        raise ValueError("Only earlier turns are available as history")
                    result = await storage.call("read", workspace_id, owner, args["turn_id"], observation_id=args.get("observation_id"), paths=args.get("paths"))
            else:
                raise ValueError("Use list or read")
        except (ReportError, OSError, KeyError, ValueError) as error:
            raise ToolExecutionError("workspace_history_invalid", str(error)) from error
        return {**result, "historical": True, "current_observation": False}

    return ToolSpec(
        "workspace_history",
        "Historical work/reports, not current game facts. list discovers records in this work; read accepts turn_id or report_id and optional observation_id/report index. Another work's report requires its exact ID in the question. Exchanges save automatically.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "read"]},
            "turn_id": {"type": "string"}, "report_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
            "observation_id": {"type": "string"},
            "index": {"type": "integer", "minimum": 0},
            "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 16},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }, "required": ["action"], "additionalProperties": False},
        read, max_result_chars=MAX_REPORT_BYTES + 4096,
    )
