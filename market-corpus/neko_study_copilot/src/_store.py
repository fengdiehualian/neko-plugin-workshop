"""本地学习数据库（SQLite，零依赖）。

两个必须注意的点：

1. 全部方法都是**同步**的，插件侧一律用 ``asyncio.to_thread(...)`` 调用。
2. ``sqlite3`` 的 ``with conn:`` 只管事务，**不会关闭连接**。这里统一用
   ``contextlib.closing`` 包装，避免长跑插件连接泄漏（Windows 下还会导致
   文件被占用、无法删除）。

数据库文件落在插件的 ``data/study.db``，``data/`` 已被 .gitignore 排除。
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    exam_type TEXT DEFAULT 'gaokao',
    region TEXT DEFAULT '',
    school TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    subjects TEXT DEFAULT '',
    target_score REAL DEFAULT 0,
    exam_date TEXT DEFAULT '',
    daily_minutes INTEGER DEFAULT 180,
    teach_style TEXT DEFAULT 'gentle',
    quiz_stage TEXT DEFAULT 'auto',
    psychology_level TEXT DEFAULT 'normal',
    credits TEXT DEFAULT '',
    updated REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mastery (
    point_id TEXT PRIMARY KEY,
    subject TEXT DEFAULT '',
    level REAL DEFAULT 0.5,
    attempts INTEGER DEFAULT 0,
    correct INTEGER DEFAULT 0,
    next_review REAL DEFAULT 0,
    updated REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id TEXT DEFAULT '',
    form TEXT DEFAULT '',
    stage TEXT DEFAULT '',
    correct INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    created REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT DEFAULT '',
    start_date TEXT DEFAULT '',
    end_date TEXT DEFAULT '',
    payload TEXT DEFAULT '{}',
    created REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT DEFAULT '',
    payload TEXT DEFAULT '{}',
    created REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id TEXT DEFAULT '',
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',
    source TEXT DEFAULT '',
    created REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    created REAL DEFAULT 0
);

-- ── 短期记忆：对话回合与材料（讲解、诊断、提问的原话），到期自动清理 ──
CREATE TABLE IF NOT EXISTS memory_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT DEFAULT '',       -- 话题/会话标识，同一件事的上下文串在一起
    role TEXT DEFAULT 'user',      -- user / assistant / material（如截图转写）
    text TEXT DEFAULT '',
    topic TEXT DEFAULT '',         -- 知识点名或科目，检索用
    ref_kind TEXT DEFAULT '',      -- vision / diagnose / quiz / teach / comfort …
    ref_id TEXT DEFAULT '',
    created REAL DEFAULT 0,
    expires REAL DEFAULT 0         -- 到点就该删；长期结论另行压进 memory_facts
);
CREATE INDEX IF NOT EXISTS idx_turns_created ON memory_turns(created);
CREATE INDEX IF NOT EXISTS idx_turns_session ON memory_turns(session);
CREATE INDEX IF NOT EXISTS idx_turns_topic ON memory_turns(topic);

-- ── 修行经验：只记真实学习行为产生的经验（答题、诊断、讲解、复习）──
CREATE TABLE IF NOT EXISTS exp_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT DEFAULT '',        -- attempt_correct / attempt_wrong / diagnose / teach / quiz / review …
    amount INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    created REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_exp_created ON exp_log(created);

-- ── 小游戏成绩：每局一条，用于最高分、连击、累计等记录 ──────────
CREATE TABLE IF NOT EXISTS game_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT DEFAULT '',        -- fruit / piano …
    score INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    max_combo INTEGER DEFAULT 0,
    duration REAL DEFAULT 0,
    sliced INTEGER DEFAULT 0,
    missed INTEGER DEFAULT 0,
    created REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_game_runs ON game_runs(game, score DESC);

-- ── 徽章：按 key 只发一次，条件都来自真实数据 ──────────────────
CREATE TABLE IF NOT EXISTS badges (
    key TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created REAL DEFAULT 0
);

-- ── 长期记忆：压缩后的事实（进度、薄弱点、偏好、反复错的原因），不自动删 ──
CREATE TABLE IF NOT EXISTS memory_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT DEFAULT 'note',      -- progress / weakness / mistake / preference / diagnosis / note
    key TEXT DEFAULT '',           -- 同一 (kind, key) 覆盖更新，避免堆重复
    text TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    importance INTEGER DEFAULT 3,  -- 1~5，越大越优先被召回
    hits INTEGER DEFAULT 0,        -- 被召回次数，用于排序
    created REAL DEFAULT 0,
    updated REAL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_key ON memory_facts(kind, key);
CREATE INDEX IF NOT EXISTS idx_facts_importance ON memory_facts(importance DESC, updated DESC);
"""

_PROFILE_COLUMNS = (
    "exam_type", "region", "school", "grade", "subjects", "target_score",
    "exam_date", "daily_minutes", "teach_style", "quiz_stage",
    "psychology_level", "credits", "updated",
)

# 老库升级用：新增列的 DDL
_PROFILE_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("credits", "TEXT DEFAULT ''"),
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_payload(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class StudyStore:
    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """给已存在的旧库补上新列（直接在旧库上 INSERT 新列会报错）。"""
        with closing(self._connect()) as conn:
            existing = {row["name"] for row in self._read(conn, "PRAGMA table_info(profile)")}
            for column, ddl in _PROFILE_MIGRATIONS:
                if column not in existing:
                    conn.execute(f"ALTER TABLE profile ADD COLUMN {column} {ddl}")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _read(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    # ── 档案 ──────────────────────────────────────────────────
    def get_profile(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            rows = self._read(conn, "SELECT * FROM profile WHERE id = 1")
        if not rows:
            return {}
        data = rows[0]
        data.pop("id", None)
        return data

    def save_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        merged = dict(self.get_profile())
        merged.update({key: value for key, value in (profile or {}).items() if value is not None})
        merged["updated"] = time.time()
        values = [merged.get(column, "") for column in _PROFILE_COLUMNS]
        columns = ", ".join(_PROFILE_COLUMNS)
        placeholders = ", ".join("?" for _ in _PROFILE_COLUMNS)
        updates = ", ".join(f"{column} = excluded.{column}" for column in _PROFILE_COLUMNS)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    f"INSERT INTO profile (id, {columns}) VALUES (1, {placeholders}) "
                    f"ON CONFLICT(id) DO UPDATE SET {updates}",
                    values,
                )
        return merged

    # ── 掌握度 ────────────────────────────────────────────────
    def mastery_map(self, subject: str = "") -> dict[str, float]:
        with closing(self._connect()) as conn:
            if subject:
                rows = self._read(conn, "SELECT point_id, level FROM mastery WHERE subject = ?", (subject,))
            else:
                rows = self._read(conn, "SELECT point_id, level FROM mastery")
        return {row["point_id"]: _safe_float(row["level"], 0.5) for row in rows}

    def set_mastery(self, point_id: str, subject: str, level: float) -> float:
        level = min(1.0, max(0.0, _safe_float(level, 0.5)))
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO mastery (point_id, subject, level, attempts, correct, next_review, updated) "
                    "VALUES (?, ?, ?, 0, 0, 0, ?) "
                    "ON CONFLICT(point_id) DO UPDATE SET level = excluded.level, "
                    "subject = excluded.subject, updated = excluded.updated",
                    (point_id, subject, level, time.time()),
                )
        return level

    def update_mastery(self, point_id: str, subject: str, correct: bool) -> float:
        """按作答结果推进掌握度：答对向 1 收敛，答错按比例衰减。"""
        level = self.mastery_map().get(point_id, 0.5)
        if correct:
            level = level + 0.18 * (1.0 - level)
        else:
            level = level * 0.78
        level = min(1.0, max(0.02, level))
        # 艾宾浩斯式复习间隔：掌握越好，下一次复习越远
        interval_days = 1 if not correct else max(1, int(round(1 + level * 14)))
        next_review = time.time() + interval_days * 86400
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO mastery (point_id, subject, level, attempts, correct, next_review, updated) "
                    "VALUES (?, ?, ?, 1, ?, ?, ?) "
                    "ON CONFLICT(point_id) DO UPDATE SET level = excluded.level, "
                    "attempts = attempts + 1, correct = correct + ?, "
                    "next_review = excluded.next_review, updated = excluded.updated",
                    (point_id, subject, level, 1 if correct else 0, next_review, time.time(), 1 if correct else 0),
                )
                conn.execute(
                    "INSERT INTO attempts (point_id, form, stage, correct, note, created) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (point_id, "", "", 1 if correct else 0, "", time.time()),
                )
        return level

    def due_reviews(self, limit: int = 10) -> list[dict[str, Any]]:
        now = time.time()
        with closing(self._connect()) as conn:
            return self._read(
                conn,
                "SELECT point_id, subject, level, next_review FROM mastery "
                "WHERE next_review > 0 AND next_review <= ? ORDER BY next_review LIMIT ?",
                (now, limit),
            )

    def attempt_stats(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM attempts").fetchone()
            correct = conn.execute("SELECT COUNT(*) AS n FROM attempts WHERE correct = 1").fetchone()
        total_n = int(total["n"] or 0) if total else 0
        correct_n = int(correct["n"] or 0) if correct else 0
        return {
            "total": total_n,
            "correct": correct_n,
            "rate": round(correct_n / total_n, 3) if total_n else 0.0,
        }

    # ── 计划 ──────────────────────────────────────────────────
    def save_plan(self, title: str, start_date: str, end_date: str, payload: dict[str, Any]) -> int:
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO plans (title, start_date, end_date, payload, created) VALUES (?, ?, ?, ?, ?)",
                    (title, start_date, end_date, json.dumps(payload or {}, ensure_ascii=False), time.time()),
                )
                return int(cursor.lastrowid or 0)

    def latest_plan(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            rows = self._read(conn, "SELECT * FROM plans ORDER BY id DESC LIMIT 1")
        if not rows:
            return {}
        data = rows[0]
        data["payload"] = _load_payload(data.get("payload"))
        return data

    def list_plans(self, limit: int = 5) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = self._read(conn, "SELECT * FROM plans ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["payload"] = _load_payload(row.get("payload"))
        return rows

    # ── 诊断 ──────────────────────────────────────────────────
    def save_diagnosis(self, summary: str, payload: dict[str, Any]) -> int:
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO diagnoses (summary, payload, created) VALUES (?, ?, ?)",
                    (summary, json.dumps(payload or {}, ensure_ascii=False), time.time()),
                )
                return int(cursor.lastrowid or 0)

    def latest_diagnosis(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            rows = self._read(conn, "SELECT * FROM diagnoses ORDER BY id DESC LIMIT 1")
        if not rows:
            return {}
        data = rows[0]
        data["payload"] = _load_payload(data.get("payload"))
        return data

    # ── 资源 ──────────────────────────────────────────────────
    def save_resources(self, point_id: str, items: list[dict[str, Any]]) -> int:
        now = time.time()
        count = 0
        with closing(self._connect()) as conn:
            with conn:
                for item in items or []:
                    url = str(item.get("url") or "")
                    if not url:
                        continue
                    conn.execute(
                        "INSERT INTO resources (point_id, title, url, source, created) VALUES (?, ?, ?, ?, ?)",
                        (point_id, str(item.get("title") or "")[:120], url, str(item.get("source") or ""), now),
                    )
                    count += 1
        return count

    def list_resources(self, point_id: str = "", limit: int = 10) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            if point_id:
                return self._read(
                    conn,
                    "SELECT * FROM resources WHERE point_id = ? ORDER BY id DESC LIMIT ?",
                    (point_id, limit),
                )
            return self._read(conn, "SELECT * FROM resources ORDER BY id DESC LIMIT ?", (limit,))

    # ── 会话流水 ──────────────────────────────────────────────
    def add_session(self, kind: str, summary: str) -> int:
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO sessions (kind, summary, created) VALUES (?, ?, ?)",
                    (kind, summary[:2000], time.time()),
                )
                return int(cursor.lastrowid or 0)

    def list_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return self._read(conn, "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,))

    def overview(self) -> dict[str, Any]:
        mastery = self.mastery_map()
        tracked = len(mastery)
        average = round(sum(mastery.values()) / tracked, 3) if tracked else 0.0
        return {
            "profile": self.get_profile(),
            "tracked_points": tracked,
            "average_mastery": average,
            "attempts": self.attempt_stats(),
            "due_reviews": len(self.due_reviews()),
            "plan": bool(self.latest_plan()),
            "diagnosis": bool(self.latest_diagnosis()),
            "resources": len(self.list_resources(limit=100)),
        }

    # ── 修行经验与徽章 ────────────────────────────────────────
    def add_exp(self, kind: str, amount: int, note: str = "") -> int:
        """记一笔经验。``amount`` 由调用方按规则算好（见 ``_progress``）。"""
        value = int(amount)
        if value == 0:
            return 0
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO exp_log (kind, amount, note, created) VALUES (?, ?, ?, ?)",
                    (kind or "", value, (note or "")[:200], time.time()),
                )
        return value

    def exp_total(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS n FROM exp_log").fetchone()
        return int(row["n"] or 0) if row else 0

    def exp_recent(self, limit: int = 12) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return self._read(conn, "SELECT * FROM exp_log ORDER BY id DESC LIMIT ?", (int(limit),))

    def exp_between(self, start: float, end: float) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS n FROM exp_log WHERE created >= ? AND created <= ?",
                (float(start), float(end)),
            ).fetchone()
        return int(row["n"] or 0) if row else 0

    def exp_timestamps(self, limit: int = 2000) -> list[float]:
        """有记录的时间点，用来算连续天数与早起徽章。"""
        with closing(self._connect()) as conn:
            rows = self._read(conn, "SELECT created FROM exp_log ORDER BY created DESC LIMIT ?", (int(limit),))
        return [float(row["created"] or 0) for row in rows]

    def list_badges(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return self._read(conn, "SELECT * FROM badges ORDER BY created ASC")

    def has_badge(self, key: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT 1 FROM badges WHERE key = ?", (key or "",)).fetchone()
        return row is not None

    def award_badge(self, key: str, title: str, note: str = "") -> bool:
        """发徽章；已经有了就返回 False（天然幂等）。"""
        if not key or self.has_badge(key):
            return False
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO badges (key, title, note, created) VALUES (?, ?, ?, ?)",
                    (key, title, (note or "")[:200], time.time()),
                )
        return True

    def count_kind(self, table: str, kind: str = "") -> int:
        """按 kind 统计次数（sessions / exp_log 用）。"""
        if table not in ("sessions", "exp_log"):
            return 0
        with closing(self._connect()) as conn:
            if kind:
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE kind = ?", (kind,)).fetchone()
            else:
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"] or 0) if row else 0

    # ── 小游戏记录 ────────────────────────────────────────────
    def save_game_run(
        self,
        game: str,
        score: int,
        *,
        level: int = 1,
        max_combo: int = 0,
        duration: float = 0.0,
        sliced: int = 0,
        missed: int = 0,
    ) -> int:
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO game_runs (game, score, level, max_combo, duration, sliced, missed, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        game or "fruit",
                        int(score),
                        int(level),
                        int(max_combo),
                        float(duration),
                        int(sliced),
                        int(missed),
                        time.time(),
                    ),
                )
                return int(cursor.lastrowid or 0)

    def game_best(self, game: str = "fruit") -> dict[str, Any]:
        """历史最好成绩（没有记录时返回空 dict）。"""
        with closing(self._connect()) as conn:
            rows = self._read(
                conn,
                "SELECT * FROM game_runs WHERE game = ? ORDER BY score DESC, id ASC LIMIT 1",
                (game or "fruit",),
            )
        return rows[0] if rows else {}

    def game_totals(self, game: str = "fruit") -> dict[str, Any]:
        """累计记录：局数、总分、切中/漏掉总数、最长存活、最高连击、最高等级。"""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS runs, COALESCE(SUM(score), 0) AS score, "
                "COALESCE(SUM(sliced), 0) AS sliced, COALESCE(SUM(missed), 0) AS missed, "
                "COALESCE(MAX(max_combo), 0) AS combo, COALESCE(MAX(duration), 0) AS best_time, "
                "COALESCE(MAX(level), 0) AS level FROM game_runs WHERE game = ?",
                (game or "fruit",),
            ).fetchone()
        if not row:
            return {"runs": 0, "score": 0, "sliced": 0, "missed": 0, "combo": 0, "best_time": 0, "level": 0}
        return {
            "runs": int(row["runs"] or 0),
            "score": int(row["score"] or 0),
            "sliced": int(row["sliced"] or 0),
            "missed": int(row["missed"] or 0),
            "combo": int(row["combo"] or 0),
            "best_time": float(row["best_time"] or 0),
            "level": int(row["level"] or 0),
        }

    def game_recent(self, game: str = "fruit", limit: int = 8) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return self._read(
                conn,
                "SELECT * FROM game_runs WHERE game = ? ORDER BY id DESC LIMIT ?",
                (game or "fruit", int(limit)),
            )

    # ── 短期记忆（对话回合）────────────────────────────────────
    def add_turn(
        self,
        role: str,
        text: str,
        *,
        session: str = "",
        topic: str = "",
        ref_kind: str = "",
        ref_id: str = "",
        ttl_days: float = 7.0,
    ) -> int:
        """记一条短期记忆。``expires`` 到点后由 :meth:`purge_turns` 清掉。"""
        body = (text or "").strip()
        if not body:
            return 0
        now = time.time()
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO memory_turns (session, role, text, topic, ref_kind, ref_id, created, expires) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session or "",
                        role or "user",
                        body[:4000],
                        topic or "",
                        ref_kind or "",
                        ref_id or "",
                        now,
                        now + max(0.0, float(ttl_days)) * 86400,
                    ),
                )
                return int(cursor.lastrowid or 0)

    def recent_turns(
        self,
        limit: int = 8,
        *,
        session: str = "",
        topic: str = "",
        now: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """取最近的短期记忆（默认只取未过期的）。"""
        stamp = time.time() if now is None else float(now)
        sql = "SELECT * FROM memory_turns WHERE expires > ?"
        params: list[Any] = [stamp]
        if session:
            sql += " AND session = ?"
            params.append(session)
        if topic:
            sql += " AND topic = ?"
            params.append(topic)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with closing(self._connect()) as conn:
            rows = self._read(conn, sql, tuple(params))
        return list(reversed(rows))

    def expired_turns(self, now: Optional[float] = None, limit: int = 300) -> list[dict[str, Any]]:
        """取已过期、等着被清理的短期记忆（清理前压缩成长期事实要用）。"""
        stamp = time.time() if now is None else float(now)
        with closing(self._connect()) as conn:
            rows = self._read(
                conn,
                "SELECT * FROM memory_turns WHERE expires <= ? ORDER BY id DESC LIMIT ?",
                (stamp, int(limit)),
            )
        return list(reversed(rows))

    def purge_turns(self, keep_days: float = 7.0, now: Optional[float] = None) -> int:
        """清理过期短期记忆，返回删除条数。

        调用方通常会在清理前把值得长期留的信息压进 ``memory_facts``——
        删的是原话，留下的是结论。
        """
        stamp = time.time() if now is None else float(now)
        cutoff = stamp - max(0.0, float(keep_days)) * 86400
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    "DELETE FROM memory_turns WHERE expires <= ? OR created <= ?", (stamp, cutoff)
                )
                return int(cursor.rowcount or 0)

    def clear_turns(self, session: str = "") -> int:
        with closing(self._connect()) as conn:
            with conn:
                if session:
                    cursor = conn.execute("DELETE FROM memory_turns WHERE session = ?", (session,))
                else:
                    cursor = conn.execute("DELETE FROM memory_turns")
                return int(cursor.rowcount or 0)

    def count_turns(self, now: Optional[float] = None) -> int:
        stamp = time.time() if now is None else float(now)
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM memory_turns WHERE expires > ?", (stamp,)).fetchone()
        return int(row["n"] or 0) if row else 0

    # ── 长期记忆（压缩后的事实）────────────────────────────────
    def upsert_fact(
        self,
        kind: str,
        key: str,
        text: str,
        *,
        topic: str = "",
        importance: int = 3,
    ) -> int:
        """写一条长期事实；同 (kind, key) 覆盖更新，不会堆重复。"""
        body = (text or "").strip()
        if not body:
            return 0
        now = time.time()
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO memory_facts (kind, key, text, topic, importance, hits, created, updated) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?, ?) "
                    "ON CONFLICT(kind, key) DO UPDATE SET text = excluded.text, topic = excluded.topic, "
                    "importance = MAX(importance, excluded.importance), updated = excluded.updated",
                    (kind or "note", key or body[:48], body[:2000], topic or "", int(importance), now, now),
                )
        return 1

    def list_facts(self, *, kind: str = "", topic: str = "", limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memory_facts WHERE 1 = 1"
        params: list[Any] = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if topic:
            sql += " AND topic = ?"
            params.append(topic)
        sql += " ORDER BY importance DESC, updated DESC LIMIT ?"
        params.append(int(limit))
        with closing(self._connect()) as conn:
            return self._read(conn, sql, tuple(params))

    def search_facts(self, keyword: str, limit: int = 6) -> list[dict[str, Any]]:
        """按关键词在长期记忆里找：先按 topic/key 命中，再按正文命中。"""
        text = (keyword or "").strip()
        if not text:
            return []
        like = f"%{text}%"
        with closing(self._connect()) as conn:
            rows = self._read(
                conn,
                "SELECT * FROM memory_facts WHERE topic LIKE ? OR key LIKE ? OR text LIKE ? "
                "ORDER BY importance DESC, updated DESC LIMIT ?",
                (like, like, like, int(limit)),
            )
            for row in rows:
                conn.execute("UPDATE memory_facts SET hits = hits + 1 WHERE id = ?", (row["id"],))
            conn.commit()
        return rows

    def drop_fact(self, fact_id: int) -> int:
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute("DELETE FROM memory_facts WHERE id = ?", (int(fact_id),))
                return int(cursor.rowcount or 0)

    def memory_stats(self, now: Optional[float] = None) -> dict[str, Any]:
        stamp = time.time() if now is None else float(now)
        with closing(self._connect()) as conn:
            turns = conn.execute("SELECT COUNT(*) AS n FROM memory_turns WHERE expires > ?", (stamp,)).fetchone()
            expired = conn.execute("SELECT COUNT(*) AS n FROM memory_turns WHERE expires <= ?", (stamp,)).fetchone()
            facts = conn.execute("SELECT COUNT(*) AS n FROM memory_facts").fetchone()
            oldest = conn.execute("SELECT MIN(created) AS t FROM memory_turns WHERE expires > ?", (stamp,)).fetchone()
        return {
            "turns": int(turns["n"] or 0) if turns else 0,
            "expired": int(expired["n"] or 0) if expired else 0,
            "facts": int(facts["n"] or 0) if facts else 0,
            "oldest_turn": float(oldest["t"] or 0) if oldest else 0.0,
        }
