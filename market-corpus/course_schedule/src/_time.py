"""课程表时间 / 周次解析纯函数。"""

from __future__ import annotations

from datetime import date, datetime


def parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def weekday_of(d: date) -> int:
    """1=周一 .. 7=周日。"""
    return d.isoweekday()


def week_number(semester: dict, d: date) -> int | None:
    """日期在学期范围内则返回周次（第1周起），否则 None。以 start/end 为权威边界。"""
    start = parse_date(semester.get("start_date")) if semester else None
    end = parse_date(semester.get("end_date")) if semester else None
    if not start or not end or d < start or d > end:
        return None
    return (d - start).days // 7 + 1


def active_in_week(weeks_list: list[int], week_no: int | None) -> bool:
    if week_no is None:
        return False
    return (not weeks_list) or (week_no in weeks_list)


def period_to_datetime(d: date, hhmm: str, tz=None) -> datetime | None:
    if not hhmm:
        return None
    try:
        hh, mm = (int(x) for x in hhmm.split(":")[:2])
        return datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
    except (ValueError, IndexError):
        return None


def parse_weekday(value) -> int | None:
    """解析星期字符串 / 数字为 1-7（1=周一）。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 7 else None
    s = str(value).strip()
    mapping = {
        "1": 1,
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "7": 7,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "日": 7,
        "天": 7,
        "周一": 1,
        "周二": 2,
        "周三": 3,
        "周四": 4,
        "周五": 5,
        "周六": 6,
        "周日": 7,
        "周天": 7,
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
    return mapping.get(s.lower())


def weekday_label(wd: int) -> str:
    return {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}.get(wd, f"周{wd}")


def format_schedule(date_obj: date, sessions: list[dict], period_times: dict) -> str:
    """把一天的课程渲染成文本，供 AI / 聊天展示。"""
    title = f"{date_obj.isoformat()} {weekday_label(weekday_of(date_obj))}"
    if not sessions:
        return f"{title}\n（无课）"
    lines = [title]
    for s in sorted(sessions, key=lambda x: x.get("period_no") or 0):
        pno = s.get("period_no")
        pt = period_times.get(pno) if pno else None
        time_str = f"{pt['start_time']}-{pt['end_time']}" if pt else ""
        name = s.get("name") or s.get("title") or "未命名"
        loc = s.get("location") or ""
        teacher = s.get("teacher") or ""
        meta = " ".join(p for p in [loc, teacher] if p)
        prefix = f"  第{pno}节 {time_str}".rstrip()
        lines.append(f"{prefix}  {name}" + (f"（{meta}）" if meta else ""))
    return "\n".join(lines)
