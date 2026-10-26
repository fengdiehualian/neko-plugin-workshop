"""Read-only Hosted UI projections of durable work; never execute or recover it."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager

from .store import WorkspaceStore


class WorkspaceViews:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def database(self):
        if not self.path.is_file():
            yield None
            return
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN")
            WorkspaceStore._validate_schema(db)
            yield db
        finally:
            db.close()

    @staticmethod
    def _work(db, workspace_id):
        row = db.execute(
            "SELECT w.id AS workspace_id,w.owner,w.focus,w.work_state,a.colony_id "
            "FROM workspaces w LEFT JOIN active_work a ON a.workspace_id=w.id WHERE w.id=?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise ValueError("This work is not retained")
        return dict(row)

    @staticmethod
    def _turn(db, workspace_id, turn_id):
        row = db.execute("SELECT * FROM turns WHERE workspace_id=? AND id=?", (workspace_id, turn_id)).fetchone()
        if row is None:
            raise ValueError("This exchange is not retained in the selected work")
        return row

    def read(self, action, *, workspace_id="", turn_id="", observation_id="", offset=0, limit=20, current_colony_id=None, search="", colony_only=False):
        if not isinstance(search, str) or len(search) > 256 or type(colony_only) is not bool:
            raise ValueError("Invalid history filter")
        if any(not isinstance(value, str) or len(value) > 128 for value in (workspace_id, turn_id, observation_id)):
            raise ValueError("Invalid work reference")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid view page")
        if not isinstance(action, str) or action not in {"list", "work", "turn", "observation", "audit"}:
            raise ValueError("Unknown work view")
        with self.database() as db:
            if db is None:
                if action == "list":
                    return {"workspaces": [], "total": 0, "offset": offset, "next_offset": None}
                raise ValueError("No saved work is available")
            if action == "list":
                return self._list(db, offset, limit, current_colony_id, search, colony_only)
            work = self._work(db, workspace_id)
            if action == "work":
                return self._history(db, work, offset, limit, search)
            turn = self._turn(db, workspace_id, turn_id)
            if action == "observation":
                row = db.execute(
                    "SELECT payload,sha256 FROM observations WHERE turn_id=? AND observation_id=?",
                    (turn_id, observation_id),
                ).fetchone()
                if row is None or hashlib.sha256(row["payload"].encode()).hexdigest() != row["sha256"]:
                    raise ValueError("Original evidence is unavailable or failed its hash check")
                return {
                    "workspace_id": workspace_id,
                    "turn_id": turn_id,
                    "observation_id": observation_id,
                    "historical": True,
                    "current_observation": False,
                    "sha256": row["sha256"],
                    "observation": json.loads(row["payload"]),
                }
            if action == "audit":
                audit = json.loads(turn["audit"]) if turn["audit"] else None
                if audit is None:
                    raise ValueError("No saved audit is available for this exchange")
                events = audit.pop("events", [])
                return {
                    **audit,
                    "workspace_id": workspace_id,
                    "turn_id": turn_id,
                    "events": events[offset : offset + limit],
                    "events_page": {
                        "offset": offset,
                        "limit": limit,
                        "total": len(events),
                        "has_more": offset + limit < len(events),
                    },
                }
            delivery = db.execute("SELECT payload FROM workspace_deliveries WHERE turn_id=?", (turn_id,)).fetchone()
            return {
                "workspace": work,
                "workspace_id": workspace_id,
                "turn_id": turn_id,
                "question": turn["question"],
                "status": turn["status"],
                "result": json.loads(turn["result"]) if turn["result"] else None,
                "handoff": json.loads(delivery[0]) if delivery else None,
                "audit_available": turn["audit"] is not None,
                "evidence": self._reading_index(db, turn_id),
                "historical": True,
                "current_observation": False,
            }

    @staticmethod
    def _reading_index(db, turn_id):
        """Describe saved reads without returning or rewriting their game facts."""
        result = []
        for row in db.execute("SELECT observation_id,sha256,payload FROM observations WHERE turn_id=? ORDER BY rowid", (turn_id,)):
            item = {"observation_id": row["observation_id"], "sha256": row["sha256"]}
            try:
                observation = json.loads(row["payload"])
            except (ValueError, TypeError):
                observation = None
            if isinstance(observation, dict):
                request = observation.get("request")
                if isinstance(request, dict):
                    arguments = request.get("arguments")
                    arguments = arguments if isinstance(arguments, dict) else {}
                    # Root names support human labels; full arguments and facts
                    # stay behind the existing hash-checked observation read.
                    roots = arguments.get("requests")
                    args = ({"requests": [{"from": part.get("from")} for part in roots if isinstance(part, dict)]}
                            if isinstance(roots, list) else {"from": arguments.get("from")})
                    item["request"] = {"operation": request.get("operation"), "arguments": args}
                if isinstance(observation.get("received_at"), str):
                    item["received_at"] = observation["received_at"]
                response = observation.get("response")
                payload = response.get("payload") if isinstance(response, dict) else None
                if isinstance(payload, dict):
                    for key in ("status", "error_code"):
                        if isinstance(payload.get(key), str):
                            item[key] = payload[key]
            result.append(item)
        return result

    def _list(self, db, offset, limit, current_colony_id=None, search="", colony_only=False):
        conditions, parameters = [], []
        if colony_only:
            conditions.append("a.colony_id=?")
            parameters.append(current_colony_id)
        if search.strip():
            literal = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append("(w.owner LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM turns q WHERE q.workspace_id=w.id AND q.question LIKE ? ESCAPE '\\'))")
            parameters.extend(["%" + literal + "%"] * 2)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = db.execute(
            "SELECT w.id AS workspace_id,w.owner,w.work_state,a.colony_id,t.question AS last_question,"
            "t.status AS last_status,t.sequence AS last_sequence,"
            "(SELECT count(*) FROM turns WHERE workspace_id=w.id) AS turn_count "
            "FROM workspaces w LEFT JOIN active_work a ON a.workspace_id=w.id "
            "LEFT JOIN turns t ON t.sequence=(SELECT max(sequence) FROM turns WHERE workspace_id=w.id) "
            + where + " ORDER BY CASE WHEN a.colony_id=? THEN 0 ELSE 1 END,coalesce(t.sequence,0) DESC,w.id LIMIT ? OFFSET ?",
            (*parameters, current_colony_id, limit, offset),
        ).fetchall()
        total = db.execute("SELECT count(*) FROM workspaces w LEFT JOIN active_work a ON a.workspace_id=w.id" + where, parameters).fetchone()[0]
        return {
            "workspaces": [dict(r) for r in rows],
            "total": total,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    def _history(self, db, work, offset, limit, search=""):
        workspace_id = work["workspace_id"]
        where = "workspace_id=?"
        parameters = [workspace_id]
        if search.strip() and search.strip().casefold() not in work["owner"].casefold():
            literal = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where += " AND question LIKE ? ESCAPE '\\'"
            parameters.append("%" + literal + "%")
        rows = db.execute(
            "SELECT id AS turn_id,sequence,question,status,json_extract(result,'$.agent_text') AS preview,"
            "json_extract(result,'$.report.report_id') AS report_id,"
            "json_extract(result,'$.report.created_at') AS created_at "
            "FROM turns WHERE " + where + " ORDER BY sequence DESC LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        ).fetchall()
        total = db.execute("SELECT count(*) FROM turns WHERE " + where, parameters).fetchone()[0]
        return {
            "workspace": work,
            "workspace_id": workspace_id,
            "turns": [dict(r) for r in rows],
            "total": total,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
        }
