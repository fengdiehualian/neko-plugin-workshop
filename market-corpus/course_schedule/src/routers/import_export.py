"""课程表导入/导出入口。

支持四种来源：
1. 自描述 JSON（最通用，import_schedule format=json）
2. CSV 表格文本（import_schedule format=csv）
3. iCalendar .ics（import_schedule format=ics）
4. 结构化 dict（import_from_structured，供 AI 对话 / 教务适配器调用）

所有导入都会先"预览/校验"再入库，返回 created / skipped 统计。
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry
from plugin.sdk.shared.core.router import PluginRouter

from .._time import parse_date, parse_weekday

# ──────────────────────────────────────────────────────────────
# 纯函数：各种格式 → 统一中间结构
# 中间结构（normalized）：
#   {
#     "semester": {"name", "start_date", "end_date", "total_weeks?"},
#     "courses": [
#       {"name", "code?", "teacher?", "location?", "color?", "note?",
#        "sessions": [{"weekday": 1-7, "period_no": int, "weeks": [int...] or None}]}
#     ],
#     "exceptions": [{"date": "YYYY-MM-DD", "kind": "cancel"|"add",
#                     "course_match": {"name?"} 或 "title",
#                     "period_no", "location?", "note?"}]
#   }
# ──────────────────────────────────────────────────────────────


def _detect_format(content: str) -> str:
    s = content.lstrip()
    if s.startswith("{") or s.startswith("["):
        return "json"
    if s.startswith("BEGIN:VCALENDAR") or s.startswith("BEGIN:VEVENT"):
        return "ics"
    # 表格粘贴特征：制表符分隔 + 无 JSON/ICS 特征
    # (Excel 复制出来通常是 \t 分隔)
    first_line = content.splitlines()[0] if content else ""
    if "\t" in first_line:
        return "table"
    # 粗略判断 CSV：有表头逗号分隔
    if "," in first_line and any(k in first_line.lower() for k in ("课", "课程", "星期", "weekday", "name")):
        return "csv"
    return "table"  # 兜底：任何非 JSON/ICS 都按表格粘贴处理


_WEEKDAY_MAP: dict[str, int] = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 7,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
    "sun": 7,
}


def parse_table_paste(content: str) -> dict:
    """解析「从教务系统/Excel 直接复制粘贴」的课程表文本。

    这类文本没有标准 CSV 表头，列数和顺序因学校而异，
    但每行（或合并行）通常包含以下信息：
      - 课程名（最稳定，永远有）
      - 周几（周X / 星期X / weekday）
      - 第几节（第X节 / X节 / X-Y节 / period）
      - 周次范围（X-Y周 / 第X-Y周 / weeks）
      - 教师 / 地点（可选，常有 [xxx] 前缀编号）

    策略：先按制表符拆列（Excel 复制默认 \t），fallback 按多空格；
    再用正则从每一行里提取关键字段，不依赖固定列位置。
    同一门课（按规范化名称）聚合 sessions。
    """
    lines = [ln.rstrip("\r") for ln in content.splitlines() if ln.strip()]
    if not lines:
        return {"courses": []}

    # 第一行是否像表头？（包含明显不是值的词）
    header_words = (
        "课程",
        "course",
        "班级",
        "时间",
        "地点",
        "教师",
        "学分",
        "学时",
        "教师",
        "上课",
        "总学时",
        "修读",
        "选课",
        "星期",
        "节次",
    )
    first_lower = lines[0].lower()
    has_header = any(w.lower() in first_lower for w in header_words)
    data_lines = lines[1:] if has_header else lines

    courses_map: dict[str, dict] = {}

    def _add_session(
        name: str, wd: int | None, pno: int | None, weeks: list[int] | None, teacher: str | None, location: str | None
    ) -> None:
        if not name:
            return
        # 把类似 "[331006]体育与健康 (3)" 规范化成 "体育与健康"
        clean = _normalize_course_name(name)
        if not clean:
            return
        course = courses_map.setdefault(
            clean,
            {"name": clean, "teacher": teacher, "location": location, "sessions": []},
        )
        # 第一次出现时把 teacher/location 记下来（后续行如果为空则保留）
        if teacher and not course.get("teacher"):
            course["teacher"] = teacher
        if location and not course.get("location"):
            course["location"] = location
        if wd and pno:
            # 去重同一 (weekday, period_no)
            key = (wd, pno)
            if not any((s["weekday"], s["period_no"]) == key for s in course["sessions"]):
                course["sessions"].append({"weekday": wd, "period_no": pno, "weeks": weeks})

    for line in data_lines:
        cells = _split_row(line)
        flat = " ".join(cells)

        wd = _extract_weekday(flat, cells)
        pno = _extract_period(flat, cells)
        weeks = _extract_weeks(flat, cells)
        teacher = _extract_teacher(flat, cells)
        location = _extract_location(flat, cells)
        name = _extract_course_name(flat, cells)

        if name:
            _add_session(name, wd, pno, weeks, teacher, location)

    return {"courses": list(courses_map.values())}


def _split_row(line: str) -> list[str]:
    """按制表符拆列；fallback 按 2+ 空格。"""
    if "\t" in line:
        cells = line.split("\t")
    else:
        cells = re.split(r"\s{2,}", line)
    return [c.strip() for c in cells if c.strip()]


def _extract_weekday(flat: str, cells: list[str]) -> int | None:
    # 周[一二三四五六日天] / 星期[一二三四五六日天]
    m = re.search(r"(?:星期|周)\s*([一二三四五六日天MONTHUEWEDTHFRISATSUN])", flat, re.I)
    if m:
        k = m.group(1)[0].lower()
        if k in _WEEKDAY_MAP:
            return _WEEKDAY_MAP[k]
    # 数字独立出现 "周5" / "weekday=5"
    m = re.search(r"(?:星期|周|weekday)\s*[:=]?\s*([1-7])\b", flat, re.I)
    if m:
        return int(m.group(1))
    return None


def _extract_period(flat: str, cells: list[str]) -> int | None:
    # 第X节 / X节
    m = re.search(r"第\s*(\d+)\s*节?", flat)
    if m:
        return int(m.group(1))
    # X-Y 独立数字（优先匹配 "X-Y节"）
    m = re.search(r"\b(\d+)\s*[-~到至]\s*(\d+)\s*节?\b", flat)
    if m:
        return int(m.group(1))
    # 单独一个数字在末尾附近（最后一个 cell 常是节次）
    for c in reversed(cells):
        m = re.match(r"^(\d+)$", c)
        if m:
            return int(m.group(1))
    return None


def _extract_weeks(flat: str, cells: list[str]) -> list[int] | None:
    # 范围匹配优先：X-Y周 / 第X-Y周 / X周-Y周
    patterns_range = [
        r"(?:第\s*)?(\d{1,2})\s*[-~到至]\s*(\d{1,2})\s*周",
        r"(?:第\s*)?(\d{1,2})\s*周\s*[-~到至]\s*(\d{1,2})\s*周",
    ]
    for p in patterns_range:
        m = re.search(p, flat)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return list(range(min(a, b), max(a, b) + 1))
    # 单周：前面不能是 "-"（避免在 "1-18周" 里抓到 "18周"）
    m = re.search(r"(?<![-\d])(?:第\s*)?(\d{1,2})\s*周\b", flat)
    if m:
        return [int(m.group(1))]
    return None


def _extract_teacher(flat: str, cells: list[str]) -> str | None:
    """优先从 cells 中找带 [4+位编号]姓名 格式、且不是课程名的那个单元。"""
    BLACKLIST = {
        "体育",
        "体育与健康",
        "大学英语",
        "高等数学",
        "线性代数",
        "大学语文",
        "大学物理",
        "大学化学",
        "大学计算机",
        "思修",
        "毛概",
        "近代史",
        "马原",
        "形势与政策",
        "军训",
        "劳动教育",
        "心理健康",
        "已选中",
        "已选",
        "未选",
    }
    # 教师编号通常比课程编号长（4+ 位），课程编号常见短一些
    for c in cells:
        m = re.search(r"[\(\[【]\s*(\d{4,})\s*[\)\]】]\s*([\u4e00-\u9fff]{2,8})", c)
        if m:
            name = m.group(2).strip()
            if name not in BLACKLIST:
                return name
    # 备选：从 flat 中找 [4+位数字]姓名，跳过紧跟课程的 (数字) 之后的第一个
    m = re.search(
        r"[（(]\s*\d+\s*[）)]\s+"  # 课程的 "(3)" 后缀
        r"[\(\[【]\s*(\d{4,})\s*[\)\]】]\s*([\u4e00-\u9fff]{2,6})",
        flat,
    )
    if m and m.group(2) not in BLACKLIST:
        return m.group(2)
    return None


def _extract_location(flat: str, cells: list[str]) -> str | None:
    # ① 明确带 "楼/栋/教/馆/室" 字样的短语，左右不能是 "周/节/课" 等干扰字
    m = re.search(
        r"(?<![周长节课\w])"
        r"([A-Za-z\u4e00-\u9fff]{1,6}(?:楼|栋|教|馆|室|厅|场)"
        r"[\-A-Za-z0-9\u4e00-\u9fff]{0,10})"
        r"(?![周节课])",
        flat,
    )
    if m:
        return m.group(1).strip()
    # ② 显式 "地点/教室:" 标签
    m = re.search(r"(?:地点|教室|教学楼|上课地点)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9\-]+)", flat)
    if m:
        return m.group(1)
    # ③ 兜底：最后几个 cell 里不包含 "周/节/课/时/分/选/数字" 的短串
    for c in reversed(cells):
        if 2 <= len(c) <= 12 and not re.search(r"[周节课学时学分修读班级选\d]", c):
            return c
    return None


def _extract_course_name(flat: str, cells: list[str]) -> str | None:
    """优先匹配 [code]课程名(备注) 这种完整模式，其次带中文的长字符串。"""
    # 先从带 [] 的完整单元里抓最可能是课程的字段
    for c in cells:
        # [code]课程名(备注)  或  (code)课程名
        m = re.search(
            r"[\(\[【]\s*[A-Za-z0-9\-:]*\s*[\)\]】]\s*([\u4e00-\u9fffA-Za-z\s·\-]+?)"
            r"(?:\s*[\(\（\[【][^)）\]\】]{0,30}[\)\）\]\】])?\s*$",
            c,
        )
        if m and len(m.group(1).strip()) >= 2:
            return m.group(1).strip()
    # 兜底：第一个 3-25 字符的中文串
    for c in cells:
        if 3 <= len(c) <= 25 and re.search(r"[\u4e00-\u9fff]", c):
            return c
    return None


def _normalize_course_name(name: str) -> str:
    s = name.strip()
    # 移除 [xxx] (xxx) 前缀
    s = re.sub(r"^[\(\[【]\s*[A-Za-z0-9\-:]*\s*[\)\]】]\s*", "", s)
    # 移除末尾括号备注 (3) / [实验]
    s = re.sub(r"\s*[\(\（\[【].*?[\)\）\]】]\s*$", "", s)
    # 统一空白
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_json(content: str) -> dict:
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("JSON 根必须是对象")
    return data


def parse_csv(content: str) -> dict:
    """解析简单 CSV 课表。

    期望列（中文/英文任一即可）：
    weekday（星期,周几）, period_no（节次,period）, course_name（课程,name）,
    teacher（教师）, location（地点）, weeks（周次）
    同一门课可出现在多行，按 name 聚合。
    """
    reader = csv.DictReader(io.StringIO(content))
    courses_map: dict[str, dict] = {}
    for row in reader:
        name = (row.get("course_name") or row.get("课程") or row.get("name") or "").strip()
        if not name:
            continue
        wd = parse_weekday(row.get("weekday") or row.get("星期") or row.get("周几") or "")
        pno = row.get("period_no") or row.get("节次") or row.get("period") or ""
        try:
            pno = int(str(pno).strip())
        except ValueError:
            pno = 0
        weeks = _parse_weeks_field(row.get("weeks") or row.get("周次") or "")
        teacher = (row.get("teacher") or row.get("教师") or "").strip() or None
        location = (row.get("location") or row.get("地点") or "").strip() or None
        code = (row.get("code") or row.get("课程代码") or "").strip() or None
        note = (row.get("note") or row.get("备注") or "").strip() or None

        course = courses_map.setdefault(
            name,
            {
                "name": name,
                "code": code,
                "teacher": teacher,
                "location": location,
                "note": note,
                "sessions": [],
            },
        )
        if wd and pno:
            course["sessions"].append({"weekday": wd, "period_no": pno, "weeks": weeks})

    return {"courses": list(courses_map.values())}


def parse_ics(content: str) -> dict:
    """解析 iCalendar (RFC 5545)。

    策略：抓所有 VEVENT，按 SUMMARY 聚合成课程；从 RRULE / RDATE 推导 weekday+weeks；
    DTSTART 取 time-of-day 映射到 period_no（按默认作息时间）。
    """

    events: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in content.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("BEGIN:VEVENT"):
            cur = {}
        elif line.startswith("END:VEVENT") and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, _, val = line.partition(":")
            # 处理带参数的属性，如 DTSTART;TZID=Asia/Shanghai:2025-09-01T08:00:00
            prop = key.split(";")[0].upper()
            cur.setdefault(prop, []).append(val)

    # 中国高校默认作息（与 _schema.DEFAULT_PERIOD_TIMES 同步）
    _PERIOD_STARTS = [
        "08:00",
        "08:55",
        "10:00",
        "10:55",
        "14:00",
        "14:55",
        "16:00",
        "16:55",
        "19:00",
        "19:55",
        "20:50",
    ]

    def _time_to_period(dt_str: str) -> int | None:
        # DTSTART 可能是 20250901T080000 或 2025-09-01T08:00:00
        m = re.search(r"(\d{1,2}):(\d{2})", dt_str)
        if not m:
            m = re.search(r"T(\d{2})(\d{2})", dt_str)
        if not m:
            return None
        hhmm = f"{m.group(1)}:{m.group(2)}"
        for i, start in enumerate(_PERIOD_STARTS, start=1):
            if hhmm == start:
                return i
        return None

    courses_map: dict[str, dict] = {}
    for ev in events:
        summary = (ev.get("SUMMARY") or ["未命名"])[0].strip()
        desc = (ev.get("DESCRIPTION") or [""])[0].strip()
        loc = (ev.get("LOCATION") or [""])[0].strip() or None
        dtstart = (ev.get("DTSTART") or [""])[0]
        rrule = (ev.get("RRULE") or [""])[0]

        period_no = _time_to_period(dtstart)

        # weekday：从 RRULE 的 BYDAY 提取
        weekday = None
        m = re.search(r"BYDAY=([A-Z,]+)", rrule.upper())
        if m:
            day_map = {"MO": 1, "TU": 2, "WE": 3, "TH": 4, "FR": 5, "SA": 6, "SU": 7}
            for d in m.group(1).split(","):
                if d in day_map:
                    weekday = day_map[d]
                    break

        if not summary:
            continue
        course = courses_map.setdefault(
            summary,
            {
                "name": summary,
                "location": loc,
                "note": desc or None,
                "sessions": [],
            },
        )
        if weekday and period_no:
            # 同 (weekday, period) 去重
            already = {(s["weekday"], s["period_no"]) for s in course["sessions"]}
            if (weekday, period_no) not in already:
                course["sessions"].append({"weekday": weekday, "period_no": period_no, "weeks": None})

    return {"courses": list(courses_map.values())}


def _parse_weeks_field(s: str) -> list[int] | None:
    """解析 "1-8,10,12-15" 这类周次串，返回 [1,2,3,4,5,6,7,8,10,12,13,14,15]。空=每周。"""
    s = s.strip()
    if not s or s.lower() in ("all", "每周", "每", "全周", "*"):
        return None
    out: list[int] = []
    for part in re.split(r"[,，、\s]+", s):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-~到至]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            for w in range(min(a, b), max(a, b) + 1):
                out.append(w)
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return sorted(set(out)) if out else None


# ──────────────────────────────────────────────────────────────
# 入库逻辑
# ──────────────────────────────────────────────────────────────


async def _apply_normalized(router: PluginRouter, data: dict, semester_id: int) -> dict:
    """把 normalized dict 写入数据库，返回统计。"""
    repo = router.main_plugin.repo

    # 1) 如果 data 里带 semester 元数据、且没指定 semester_id → 创建
    sem_info = data.get("semester") or {}
    sem = None
    if semester_id:
        for s in await repo.list_semesters():
            if s["id"] == semester_id:
                sem = s
                break
    if sem is None and sem_info.get("start_date") and sem_info.get("end_date"):
        sem = await repo.add_semester(
            name=sem_info.get("name") or "导入学期",
            start_date=parse_date(sem_info["start_date"]),
            end_date=parse_date(sem_info["end_date"]),
            total_weeks=int(sem_info.get("total_weeks") or 0),
            activate=True,
        )
    if sem is None:
        sem = await repo.get_active_semester()
    if sem is None:
        # 自动创建当前学期，避免用户先手动 add_semester 再导入
        from datetime import date

        today = date.today()
        year = today.year
        m = today.month
        if 2 <= m <= 7:
            # 春季学期：2月1日 ~ 6月30日
            start = date(year, 2, 1)
            end = date(year, 6, 30)
            name = f"{year}春"
        else:
            # 秋季学期：9月1日 ~ 次年1月31日
            start = date(year, 9, 1)
            end = date(year + 1, 1, 31) if m >= 9 else date(year, 1, 31)
            name = f"{year}秋"
        sem = await repo.add_semester(
            name=name,
            start_date=start,
            end_date=end,
            total_weeks=20,
            activate=True,
        )

    created_courses = 0
    updated_courses = 0
    created_sessions = 0
    skipped_sessions = 0
    created_exceptions = 0

    # 2) 课程 + 上课安排 —— 单事务批量插入（原实现每课每节各开一个 session，
    #    一门课 10 节要 11+ 次 IPC 往返导致导入 30~40s 甚至超时）
    course_dicts = []
    for cd in data.get("courses", []):
        name = (cd.get("name") or "").strip()
        if not name:
            continue
        course_dicts.append(
            {
                "name": name,
                "code": cd.get("code") or None,
                "teacher": cd.get("teacher") or None,
                "location": cd.get("location") or None,
                "color": cd.get("color") or None,
                "note": cd.get("note") or None,
                "sessions": cd.get("sessions", []) or [],
            }
        )
    _inserted, created_sessions, skipped_sessions, updated_courses = await repo.import_courses_bulk(
        semester_id=sem["id"], courses=course_dicts
    )
    created_courses = len(_inserted)

    # 3) 例外（需要第二次遍历课程按 name 查找 course_id）
    courses_all = await repo.list_courses(sem["id"])
    for ed in data.get("exceptions", []) or []:
        d = parse_date(ed.get("date") or "")
        if not d or ed.get("kind") not in ("cancel", "add"):
            continue
        match_name = None
        cm = ed.get("course_match") or {}
        if isinstance(cm, str):
            match_name = cm
        else:
            match_name = cm.get("name") if isinstance(cm, dict) else None
        course_id = None
        if match_name:
            for c in courses_all:
                if c["name"] == match_name:
                    course_id = c["id"]
                    break
        await repo.add_exception(
            semester_id=sem["id"],
            course_id=course_id,
            title=ed.get("title"),
            date=d,
            kind=ed["kind"],
            period_no=int(ed.get("period_no")) if ed.get("period_no") else None,
            location=ed.get("location"),
            note=ed.get("note"),
        )
        created_exceptions += 1

    return {
        "semester_id": sem["id"],
        "created_courses": created_courses,
        "updated_courses": updated_courses,
        "created_sessions": created_sessions,
        "created_exceptions": created_exceptions,
        "skipped_sessions": skipped_sessions,
    }


# ──────────────────────────────────────────────────────────────
# 导出
# ──────────────────────────────────────────────────────────────


async def _build_normalized_for_export(router: PluginRouter, semester_id: int) -> dict:
    repo = router.main_plugin.repo

    # 用 execute 查询 sessions
    async with await repo._session() as session:
        sem_row = None
        cur = await session.execute("SELECT * FROM semesters WHERE id = ?", (int(semester_id),))
        sem_row = cur.fetchone()
        if not sem_row:
            raise ValueError(f"semester_id={semester_id} 不存在")
        sem = dict(sem_row)
        cur = await session.execute("SELECT * FROM courses WHERE semester_id = ? ORDER BY id", (int(semester_id),))
        courses_rows = [dict(r) for r in cur.fetchall()]
        cur = await session.execute(
            "SELECT cs.* FROM course_sessions cs JOIN courses c ON c.id = cs.course_id "
            "WHERE c.semester_id = ? ORDER BY c.id, cs.weekday, cs.period_no",
            (int(semester_id),),
        )
        sessions_rows = [dict(r) for r in cur.fetchall()]
        cur = await session.execute("SELECT * FROM exceptions WHERE semester_id = ? ORDER BY date", (int(semester_id),))
        exceptions_rows = [dict(r) for r in cur.fetchall()]

    sessions_by_course: dict[int, list[dict]] = {}
    for s in sessions_rows:
        sessions_by_course.setdefault(s["course_id"], []).append(
            {
                "weekday": int(s["weekday"]),
                "period_no": int(s["period_no"]),
                "weeks": json.loads(s["weeks"]) if s["weeks"] else None,
                "note": s["note"],
            }
        )

    courses_out = []
    for c in courses_rows:
        courses_out.append(
            {
                "name": c["name"],
                "code": c["code"],
                "teacher": c["teacher"],
                "location": c["location"],
                "color": c["color"],
                "note": c["note"],
                "sessions": sessions_by_course.get(c["id"], []),
            }
        )

    exceptions_out = []
    course_name_by_id = {c["id"]: c["name"] for c in courses_rows}
    for e in exceptions_rows:
        exceptions_out.append(
            {
                "date": e["date"],
                "kind": e["kind"],
                "course_match": {"name": course_name_by_id.get(e["course_id"])} if e["course_id"] else None,
                "title": e["title"],
                "period_no": e["period_no"],
                "location": e["location"],
                "note": e["note"],
            }
        )

    return {
        "semester": {
            "id": sem["id"],
            "name": sem["name"],
            "start_date": sem["start_date"],
            "end_date": sem["end_date"],
            "total_weeks": sem["total_weeks"],
        },
        "courses": courses_out,
        "exceptions": exceptions_out,
    }


# ──────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────


class ImportExportRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="import_export")

    @property
    def repo(self):
        return self.main_plugin.repo

    # ── 通用导入：文本 + 格式 ──

    @plugin_entry(
        id="import_schedule",
        name="导入课表（文本）",
        description=(
            "从文本内容导入课表，format=json|csv|ics|table|auto（auto=自动检测）。"
            "content 粘贴文件的原始文本内容。semester_id 留空则用当前学期。"
            "table 格式适配教务系统/Excel 复制粘贴的无固定表头课表。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "format": {"type": "string", "description": "json/csv/ics/table/auto，默认 auto"},
                "content": {"type": "string", "description": "课表文本内容"},
                "semester_id": {"type": "integer", "description": "目标学期 ID，可空"},
            },
            "required": ["content"],
        },
    )
    async def import_schedule(self, content: str, format: str = "auto", semester_id: int = 0, **_):
        fmt = (format or "auto").lower()
        if fmt == "auto":
            fmt = _detect_format(content)
        try:
            if fmt == "json":
                data = parse_json(content)
            elif fmt == "csv":
                data = parse_csv(content)
            elif fmt == "table":
                data = parse_table_paste(content)
            elif fmt == "ics":
                data = parse_ics(content)
            else:
                return Err(SdkError(f"不支持的 format: {format}"))
        except Exception as exc:
            return Err(SdkError(f"解析失败（{fmt}）: {exc}"))

        try:
            stats = await _apply_normalized(self, data, int(semester_id or 0))
        except Exception as exc:
            return Err(SdkError(f"入库失败: {exc}"))
        return Ok({"format": fmt, "stats": stats})

    # ── 文件上传导入：base64 二进制 ──

    @plugin_entry(
        id="import_schedule_file",
        name="导入课表（上传文件）",
        description=(
            "上传 xlsx / xls / csv / json / ics 文件导入课表。"
            "file_base64 为文件二进制的 base64 字符串（去掉 data:xxx;base64, 前缀）。"
            "filename 用于判断文件类型；也可传 format=xlsx|csv|json|ics 强制指定。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "file_base64": {"type": "string", "description": "文件 base64 内容"},
                "filename": {"type": "string", "description": "原始文件名，用于判断格式"},
                "format": {"type": "string", "description": "强制格式：xlsx/csv/json/ics"},
                "semester_id": {"type": "integer", "description": "目标学期 ID，可空"},
            },
            "required": ["file_base64"],
        },
    )
    async def import_schedule_file(
        self,
        file_base64: str,
        filename: str = "",
        format: str = "",
        semester_id: int = 0,
        **_,
    ):
        import base64

        # 去掉 data:xxx;base64, 前缀
        raw = file_base64.strip()
        if "," in raw and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]

        try:
            file_bytes = base64.b64decode(raw, validate=True)
        except Exception as exc:
            return Err(SdkError(f"base64 解码失败: {exc}"))

        fmt = (format or "").lower()
        if not fmt:
            ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
            fmt = {
                "xlsx": "xlsx",
                "xls": "xls",
                "csv": "csv",
                "json": "json",
                "ics": "ics",
            }.get(ext, "")
            if not fmt:
                # fallback：按魔数判断
                if file_bytes[:4] == b"\x50\x4b\x03\x04":  # ZIP
                    fmt = "xlsx"
                elif file_bytes[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # OLE2
                    fmt = "xls"

        if fmt in ("xlsx", "xls"):
            try:
                if fmt == "xls":
                    from .._xls_parser import parse_xls_bytes

                    matrix = parse_xls_bytes(file_bytes)
                else:
                    from .._xlsx_parser import parse_xlsx_bytes

                    matrix = parse_xlsx_bytes(file_bytes)
            except Exception as exc:
                return Err(SdkError(f"{fmt} 解析失败: {exc}"))
            if not matrix:
                return Err(SdkError(f"{fmt} 文件没有可读取的内容"))
            try:
                # 直接矩阵解析（处理周课表网格）
                from .._matrix_parser import _detect_weekly_grid, parse_matrix_to_courses

                data = parse_matrix_to_courses(matrix)
                # 如果 grid 检测也失败了，说明不是标准周课表网格
                grid_info = _detect_weekly_grid(matrix)
                if not data.get("courses") and not grid_info:
                    return Err(SdkError(
                        "未检测到周课表网格结构。"
                        "请确认 Excel 包含「星期X」或「一/二/三」表头，"
                        "或者使用手动录入 / AI 对话描述课程内容。"
                    ))
                if not data.get("courses") and grid_info:
                    # 检测到 grid 但没解析出课程 —— 返回调试信息帮 AI 诊断
                    return Err(SdkError(
                        f"检测到课表网格（{grid_info[4]}）但未能提取课程数据。"
                        f"请在 AI 对话中描述你的课程，或使用手动录入。"
                    ))
            except Exception as exc:
                return Err(SdkError(f"课表提取失败: {exc}"))
        elif fmt == "csv":
            try:
                text = file_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = file_bytes.decode("gbk", errors="replace")
            data = parse_csv(text)
        elif fmt == "json":
            try:
                text = file_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = file_bytes.decode("utf-8", errors="replace")
            data = parse_json(text)
        elif fmt == "ics":
            text = file_bytes.decode("utf-8-sig", errors="replace")
            data = parse_ics(text)
        else:
            return Err(SdkError(f"不支持的文件格式：{filename or fmt}"))

        try:
            stats = await _apply_normalized(self, data, int(semester_id or 0))
        except Exception as exc:
            return Err(SdkError(f"入库失败: {exc}"))

        n_courses = len(data.get("courses") or [])
        return Ok(
            {
                "format": fmt,
                "filename": filename,
                "courses_detected": n_courses,
                "stats": stats,
            }
        )

    # ── AI 对话 / 教务适配器：直接传结构化 dict ──

    @plugin_entry(
        id="import_from_structured",
        name="导入结构化课表",
        description=(
            "直接接受结构化 dict 入库（AI 对话录入、教务系统适配器都会用这个入口）。"
            "结构：{semester?:{...}, courses:[{name,sessions:[...]}], exceptions:[...]}"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "data": {"type": "object", "description": "结构化课表数据"},
                "semester_id": {"type": "integer", "description": "目标学期 ID，可空"},
            },
            "required": ["data"],
        },
    )
    async def import_from_structured(self, data: dict | None = None, semester_id: int = 0, **_):
        if data is None or not isinstance(data, dict):
            return Err(SdkError("data 必须是对象且不能为空，示例：{courses:[{name:'高等数学',sessions:[{weekday:1,period_no:1}]}]}"))
        try:
            stats = await _apply_normalized(self, data, int(semester_id or 0))
        except Exception as exc:
            return Err(SdkError(f"入库失败: {exc}"))
        return Ok({"stats": stats})

    # ── 导出 ──

    @plugin_entry(
        id="export_schedule",
        name="导出课表",
        description=("把指定（或当前）学期的课表导出为文本。format=json（默认）|csv|ics。"),
        input_schema={
            "type": "object",
            "properties": {
                "format": {"type": "string", "description": "json/csv/ics"},
                "semester_id": {"type": "integer", "description": "学期 ID，可空"},
            },
        },
    )
    async def export_schedule(self, format: str = "json", semester_id: int = 0, **_):
        sid = int(semester_id or 0)
        if not sid:
            sem = await self.repo.get_active_semester()
            if not sem:
                return Err(SdkError("没有当前学期"))
            sid = sem["id"]
        try:
            norm = await _build_normalized_for_export(self, sid)
        except Exception as exc:
            return Err(SdkError(f"导出失败: {exc}"))
        fmt = (format or "json").lower()
        if fmt == "json":
            text = json.dumps(norm, ensure_ascii=False, indent=2)
        elif fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["weekday", "period_no", "course_name", "teacher", "location", "weeks"])
            for c in norm["courses"]:
                for s in c["sessions"]:
                    weeks = ",".join(str(w) for w in s["weeks"]) if s.get("weeks") else ""
                    w.writerow(
                        [
                            s["weekday"],
                            s["period_no"],
                            c["name"],
                            c.get("teacher") or "",
                            c.get("location") or "",
                            weeks,
                        ]
                    )
            text = buf.getvalue()
        elif fmt == "ics":
            text = _build_ics(norm)
        else:
            return Err(SdkError(f"不支持的 format: {format}"))
        return Ok(
            {
                "format": fmt,
                "semester_id": sid,
                "content": text,
                "size": len(text),
            }
        )

    # ── Debug：预览文件解析结果 ──

    @plugin_entry(
        id="preview_schedule_file",
        name="预览课表文件解析",
        description=(
            "上传 xlsx / xls 文件，返回原始矩阵和解析结果。"
            "用于 AI 诊断为什么解析不出课程。file_base64 为文件二进制的 base64。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "file_base64": {"type": "string", "description": "文件 base64 内容"},
                "filename": {"type": "string", "description": "原始文件名，用于判断格式"},
            },
            "required": ["file_base64"],
        },
    )
    async def preview_schedule_file(self, file_base64: str, filename: str = "", **_):
        import base64

        raw = file_base64.strip()
        if "," in raw and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            file_bytes = base64.b64decode(raw, validate=True)
        except Exception as exc:
            return Err(SdkError(f"base64 解码失败: {exc}"))

        ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
        fmt = "xlsx" if ext == "xlsx" else "xls" if ext == "xls" else ""
        if not fmt:
            if file_bytes[:4] == b"\x50\x4b\x03\x04":
                fmt = "xlsx"
            elif file_bytes[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                fmt = "xls"
            else:
                return Err(SdkError("无法识别文件格式"))

        try:
            if fmt == "xls":
                from .._xls_parser import parse_xls_bytes
                matrix = parse_xls_bytes(file_bytes)
            else:
                from .._xlsx_parser import parse_xlsx_bytes
                matrix = parse_xlsx_bytes(file_bytes)
        except Exception as exc:
            return Err(SdkError(f"{fmt} 解析失败: {exc}"))

        from .._matrix_parser import _detect_weekly_grid, _find_period_col, parse_matrix_to_courses

        grid_info = _detect_weekly_grid(matrix)
        data = parse_matrix_to_courses(matrix)
        courses = data.get("courses") or []

        # 构造预览信息
        preview = {
            "file_format": fmt,
            "matrix_rows": len(matrix),
            "matrix_cols": max((len(r) for r in matrix), default=0),
            "grid_detected": grid_info is not None,
            "grid_info": {
                "top_row": grid_info[0],
                "bottom_row": grid_info[1],
                "left_col": grid_info[2],
                "right_col": grid_info[3],
                "weekday_cols": {str(k): v for k, v in grid_info[4].items()},
                "period_col": _find_period_col(matrix, grid_info[0]) if grid_info else None,
            } if grid_info else None,
            "courses_found": len(courses),
            "courses": [
                {
                    "name": c["name"],
                    "teacher": c.get("teacher"),
                    "location": c.get("location"),
                    "sessions": [
                        {"weekday": s["weekday"], "period_no": s["period_no"], "weeks": s.get("weeks")}
                        for s in c.get("sessions", [])
                    ],
                }
                for c in courses
            ],
        }

        # 添加 grid 区域的原始内容预览（最多显示 15 行）
        if grid_info:
            top, bottom, left, right, _wd = grid_info
            raw_grid = []
            for r in range(top, min(bottom, top + 15)):
                row = matrix[r] if r < len(matrix) else []
                raw_grid.append([(row[c] if c < len(row) else "")[:80] for c in range(left, right + 1)])
            preview["raw_grid_preview"] = raw_grid

        return Ok(preview)


class ClearDataRouter(PluginRouter):
    """清空/重置数据的管理入口。"""

    def __init__(self):
        super().__init__(name="clear_data")

    @property
    def repo(self):
        return self.main_plugin.repo

    @plugin_entry(
        id="clear_schedule_data",
        name="清空课表数据",
        description=(
            "清空指定学期的所有课程、session（上课安排）。"
            "semester_id 可空（空则清当前激活学期）。"
            "这通常用于导入了错误/垃圾数据后想重新开始。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "semester_id": {"type": "integer", "description": "学期 ID，留空则清当前学期"},
                "also_delete_semester": {"type": "boolean", "description": "是否同时删除学期本身，默认 false"},
            },
        },
    )
    async def clear_schedule_data(
        self,
        semester_id: int = 0,
        also_delete_semester: bool = False,
        **_,
    ):
        repo = self.repo
        sid = int(semester_id or 0)
        if not sid:
            sem = await repo.get_active_semester()
            if not sem:
                return Err(SdkError("没有当前学期"))
            sid = sem["id"]

        async with await repo._session() as session:
            # 查有多少数据会被删
            cur = await session.execute("SELECT COUNT(*) as n FROM courses WHERE semester_id = ?", (sid,))
            n_courses = cur.fetchone()["n"]
            cur = await session.execute(
                "SELECT COUNT(*) as n FROM course_sessions WHERE course_id IN "
                "(SELECT id FROM courses WHERE semester_id = ?)",
                (sid,),
            )
            n_sessions = cur.fetchone()["n"]
            cur = await session.execute("SELECT COUNT(*) as n FROM exceptions WHERE semester_id = ?", (sid,))
            n_exc = cur.fetchone()["n"]
            cur = await session.execute("SELECT COUNT(*) as n FROM assignments WHERE semester_id = ?", (sid,))
            n_hw = cur.fetchone()["n"]

            # 先删 exceptions / assignments，再删 courses（FK cascade）
            await session.execute("DELETE FROM exceptions WHERE semester_id = ?", (sid,))
            await session.execute("DELETE FROM assignments WHERE semester_id = ?", (sid,))
            await session.execute("DELETE FROM course_sessions WHERE course_id IN (SELECT id FROM courses WHERE semester_id = ?)", (sid,))
            await session.execute("DELETE FROM courses WHERE semester_id = ?", (sid,))

            if also_delete_semester:
                await session.execute("DELETE FROM period_times WHERE semester_id = ?", (sid,))
                await session.execute("DELETE FROM semesters WHERE id = ?", (sid,))

            await session.commit()

        return Ok(
            {
                "semester_id": sid,
                "deleted": {
                    "courses": n_courses,
                    "sessions": n_sessions,
                    "exceptions": n_exc,
                    "assignments": n_hw,
                    "semester": also_delete_semester,
                },
            }
        )


def _build_ics(norm: dict) -> str:
    """把 normalized dict 拼成最简 iCalendar。"""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//N.E.K.O CourseSchedule//EN"]
    # 默认作息起止
    start_map = {
        1: "080000",
        2: "085500",
        3: "100000",
        4: "105500",
        5: "140000",
        6: "145500",
        7: "160000",
        8: "165500",
        9: "190000",
        10: "195500",
        11: "205000",
    }
    end_map = {
        1: "084500",
        2: "094000",
        3: "104500",
        4: "114000",
        5: "144500",
        6: "154000",
        7: "164500",
        8: "174000",
        9: "194500",
        10: "204000",
        11: "213500",
    }
    week_day_abbr = {1: "MO", 2: "TU", 3: "WE", 4: "TH", 5: "FR", 6: "SA", 7: "SU"}

    sem = norm.get("semester") or {}
    (sem.get("start_date") or "2025-09-01")[:4]
    start_yyyymmdd = (sem.get("start_date") or "20250901").replace("-", "")

    for c in norm["courses"]:
        for s in c["sessions"]:
            pno = s["period_no"]
            st = start_map.get(pno, "080000")
            et = end_map.get(pno, "084500")
            byday = week_day_abbr.get(s["weekday"], "MO")
            weeks = s.get("weeks") or list(range(1, 21))
            ",".join(f"BYSETPOS={w}" for w in weeks)
            loc = c.get("location") or ""
            lines += [
                "BEGIN:VEVENT",
                f"DTSTART:{start_yyyymmdd}T{st}",
                f"DTEND:{start_yyyymmdd}T{et}",
                f"RRULE:FREQ=WEEKLY;BYDAY={byday}",
                f"SUMMARY:{c['name']}",
                f"LOCATION:{loc}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
