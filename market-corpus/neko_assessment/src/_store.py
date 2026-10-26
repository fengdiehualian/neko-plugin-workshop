"""本地记录存储（data/records.json）。

心理健康数据属于敏感信息：**只写本机，不上传、不联网**，可一键导出/清空。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

MAX_RECORDS = 300


class RecordStore:
    def __init__(self, path: Path, logger=None):
        self.path = Path(path)
        self.logger = logger

    def _load(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self, rows: list) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            if self.logger:
                self.logger.warning("[assessment] 记录写入失败：{}", exc)

    def add(self, scale_id: str, name: str, kind: str, result: dict, answers: list) -> dict:
        rows = self._load()
        record = {
            "id": f"{int(time.time() * 1000)}{uuid.uuid4().hex[:6]}",
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "ts": int(time.time()),
            "scale_id": scale_id,
            "name": name,
            "kind": kind,
            "total": result.get("total"),
            "level": result.get("level"),
            "type": result.get("type"),
            "code": result.get("code"),
            "dims": result.get("dims", []),
            "answers": list(answers or []),
        }
        rows.append(record)
        if len(rows) > MAX_RECORDS:
            rows = rows[-MAX_RECORDS:]
        self._save(rows)
        return record

    def list(self) -> list:
        rows = self._load()
        # 列表里不带逐题答案，省流量也省隐私风险
        light = []
        for row in rows:
            light.append({k: v for k, v in row.items() if k != "answers"})
        return light

    def get(self, record_id: str) -> Optional[dict]:
        for row in self._load():
            if str(row.get("id")) == str(record_id):
                return row
        return None

    def delete(self, record_id: str) -> bool:
        rows = self._load()
        left = [row for row in rows if str(row.get("id")) != str(record_id)]
        if len(left) == len(rows):
            return False
        self._save(left)
        return True

    def clear(self) -> int:
        count = len(self._load())
        self._save([])
        return count

    def history_for(self, scale_id: str) -> list:
        """同一量表的历次得分，用于看趋势。"""
        rows = self._load()
        return [
            {
                "id": row.get("id"),
                "time": row.get("time"),
                "ts": row.get("ts", 0),
                "total": row.get("total"),
                "level": row.get("level"),
                "dims": row.get("dims", []),
            }
            for row in rows
            if row.get("scale_id") == scale_id
        ]

    def counts(self) -> dict:
        rows = self._load()
        out: dict[str, int] = {}
        for row in rows:
            out[row.get("scale_id", "?")] = out.get(row.get("scale_id", "?"), 0) + 1
        return out


def load_prefs(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(path: Path, prefs: dict) -> None:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
