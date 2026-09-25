"""课程表插件数据库表结构。"""

from __future__ import annotations

import json

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS semesters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        total_weeks INTEGER NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS period_times (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        semester_id INTEGER NOT NULL,
        period_no INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        slot TEXT NOT NULL DEFAULT 'morning',
        UNIQUE(semester_id, period_no),
        FOREIGN KEY (semester_id) REFERENCES semesters(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        semester_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        code TEXT,
        teacher TEXT,
        location TEXT,
        color TEXT,
        note TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (semester_id) REFERENCES semesters(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS course_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        weekday INTEGER NOT NULL,
        period_no INTEGER NOT NULL,
        weeks TEXT,
        note TEXT,
        FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS exceptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        semester_id INTEGER NOT NULL,
        course_id INTEGER,
        title TEXT,
        date TEXT NOT NULL,
        kind TEXT NOT NULL,
        period_no INTEGER,
        location TEXT,
        note TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (semester_id) REFERENCES semesters(id) ON DELETE CASCADE,
        FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        semester_id INTEGER NOT NULL,
        course_id INTEGER,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        due_at TEXT,
        location TEXT,
        note TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (semester_id) REFERENCES semesters(id) ON DELETE CASCADE,
        FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL
    )
    """,
]

# 中国高校常见作息时间，建学期时自动种入，可被 set_period_times 覆盖。
DEFAULT_PERIOD_TIMES = [
    (1, "08:00", "08:45", "morning"),
    (2, "08:55", "09:40", "morning"),
    (3, "10:00", "10:45", "morning"),
    (4, "10:55", "11:40", "morning"),
    (5, "14:00", "14:45", "afternoon"),
    (6, "14:55", "15:40", "afternoon"),
    (7, "16:00", "16:45", "afternoon"),
    (8, "16:55", "17:40", "afternoon"),
    (9, "19:00", "19:45", "evening"),
    (10, "19:55", "20:40", "evening"),
    (11, "20:50", "21:35", "evening"),
]


async def ensure_schema(session) -> None:
    for stmt in SCHEMA_SQL:
        await session.execute(stmt)
    await session.commit()


def weeks_to_json(weeks) -> str | None:
    """把多种输入归一化为 JSON 周次数组字符串；None 表示每周都上。"""
    if weeks is None:
        return None
    if isinstance(weeks, str):
        s = weeks.strip()
        if s == "" or s.lower() in ("all", "每周", "全周"):
            return None
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return json.dumps([int(w) for w in parsed]) if parsed else None
        except json.JSONDecodeError:
            pass
        return None
    if isinstance(weeks, (list, tuple)):
        if len(weeks) == 0:
            return None
        return json.dumps([int(w) for w in weeks])
    return None


def parse_weeks(stored) -> list[int]:
    """返回活跃周次列表；空列表表示每周都上。"""
    if not stored:
        return []
    try:
        data = json.loads(stored)
    except (json.JSONDecodeError, TypeError):
        return []
    return [int(w) for w in data] if isinstance(data, list) else []
