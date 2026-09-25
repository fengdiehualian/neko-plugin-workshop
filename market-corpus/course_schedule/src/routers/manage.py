"""课程管理入口：学期、课程、上课安排、节次、例外。"""

from __future__ import annotations

import re

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry
from plugin.sdk.shared.core.router import PluginRouter

from .._time import parse_date

_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")
_VALID_SLOTS = ("morning", "afternoon", "evening")


class ManageRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="manage")

    @property
    def repo(self):
        return self.main_plugin.repo

    @plugin_entry(
        id="add_semester",
        name="添加学期",
        description=(
            "创建一个新学期并（默认）设为当前学期，会自动种入默认节次作息时间。"
            "total_weeks 留空则按 (end-start)/7+1 估算。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "学期名，如 2025秋季"},
                "start_date": {"type": "string", "description": "学期开始日期 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "学期结束日期 YYYY-MM-DD"},
                "total_weeks": {"type": "integer", "description": "总周数；不填则按起止日期估算"},
                "activate": {"type": "boolean", "description": "是否设为当前学期，默认 true"},
            },
            "required": ["name", "start_date", "end_date"],
        },
    )
    async def add_semester(
        self, name: str, start_date: str, end_date: str, total_weeks: int = 0, activate: bool = True, **_
    ):
        s = parse_date(start_date)
        e = parse_date(end_date)
        if not s or not e:
            return Err(SdkError("start_date/end_date 需为 YYYY-MM-DD"))
        if e < s:
            return Err(SdkError("end_date 早于 start_date"))
        if not total_weeks:
            total_weeks = (e - s).days // 7 + 1
        rec = await self.repo.add_semester(
            name=name, start_date=s, end_date=e, total_weeks=total_weeks, activate=activate
        )
        self.logger.info("学期已创建: {} ({})", name, rec["id"])
        return Ok({"semester": rec, "active": activate})

    @plugin_entry(id="list_semesters", name="学期列表", description="列出所有学期并标记当前学期")
    async def list_semesters(self, **_):
        sems = await self.repo.list_semesters()
        return Ok({"count": len(sems), "semesters": sems})

    @plugin_entry(
        id="switch_semester",
        name="切换学期",
        description="把指定学期设为当前学期",
        input_schema={
            "type": "object",
            "properties": {"semester_id": {"type": "integer", "description": "学期 ID"}},
            "required": ["semester_id"],
        },
    )
    async def switch_semester(self, semester_id: int, **_):
        ok = await self.repo.activate_semester(semester_id)
        if not ok:
            return Err(SdkError(f"未找到学期 id={semester_id}"))
        return Ok({"active_semester_id": int(semester_id)})

    @plugin_entry(
        id="add_course",
        name="添加课程",
        description=(
            "在当前（或指定）学期添加一门课，可同时带多个上课安排 sessions。"
            "sessions=[{weekday,period_no,weeks}]，weekday 1-7（1=周一），weeks 留空=每周、"
            "可填周次列表如 [1,3,5] 表示单周上。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "课程名，如 高等数学"},
                "code": {"type": "string", "description": "课程代码"},
                "teacher": {"type": "string", "description": "教师"},
                "location": {"type": "string", "description": "上课地点"},
                "color": {"type": "string", "description": "颜色标识"},
                "note": {"type": "string", "description": "备注"},
                "semester_id": {"type": "integer", "description": "学期 ID；不填用当前学期"},
                "sessions": {
                    "type": "array",
                    "description": "上课安排列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "weekday": {"type": "integer", "description": "周几 1-7（1=周一）"},
                            "period_no": {"type": "integer", "description": "第几节"},
                            "weeks": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "周次列表，空=每周",
                            },
                        },
                    },
                },
            },
            "required": ["name"],
        },
    )
    async def add_course(
        self,
        name: str,
        code: str = "",
        teacher: str = "",
        location: str = "",
        color: str = "",
        note: str = "",
        semester_id: int = 0,
        sessions: list | None = None,
        **_,
    ):
        if semester_id:
            sem = {"id": int(semester_id)}
        else:
            sem = await self.repo.get_active_semester()
        if not sem:
            return Err(SdkError("没有当前学期，请先 add_semester"))
        course = await self.repo.add_course(
            semester_id=sem["id"],
            name=name,
            code=code or None,
            teacher=teacher or None,
            location=location or None,
            color=color or None,
            note=note or None,
        )
        created = []
        for s in sessions or []:
            try:
                wd = int(s.get("weekday", 0))
                pno = int(s.get("period_no", 0))
            except (TypeError, ValueError, AttributeError):
                return Err(SdkError(f"sessions 中 weekday/period_no 必须为整数: {s}"))
            if not (1 <= wd <= 7) or pno < 1:
                return Err(SdkError(f"weekday 需 1-7、period_no 需 ≥1: {s}"))
            created.append(
                await self.repo.add_session(course_id=course["id"], weekday=wd, period_no=pno, weeks=s.get("weeks"))
            )
        return Ok({"course": course, "sessions": created})

    @plugin_entry(
        id="add_session",
        name="添加上课安排",
        description="为已有课程增加一条上课安排（周几 + 第几节 + 周次）",
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "description": "课程 ID"},
                "weekday": {"type": "integer", "description": "周几 1-7"},
                "period_no": {"type": "integer", "description": "第几节"},
                "weeks": {"type": "array", "items": {"type": "integer"}, "description": "周次列表，空=每周"},
            },
            "required": ["course_id", "weekday", "period_no"],
        },
    )
    async def add_session_entry(self, course_id: int, weekday: int, period_no: int, weeks: list | None = None, **_):
        if not (1 <= int(weekday) <= 7) or int(period_no) < 1:
            return Err(SdkError("weekday 需 1-7、period_no 需 ≥1"))
        rec = await self.repo.add_session(
            course_id=int(course_id), weekday=int(weekday), period_no=int(period_no), weeks=weeks
        )
        return Ok({"session": rec})

    @plugin_entry(
        id="add_exception",
        name="添加例外",
        description=(
            "为某天添加例外：cancel=取消该天某节（需 course_id+period_no）；"
            "add=新增单次安排（course_id 可空，用 title 作课名）。调课可用 cancel+add 组合。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                "kind": {"type": "string", "description": "cancel 或 add"},
                "course_id": {"type": "integer", "description": "关联课程 ID（cancel 必填，add 可空）"},
                "title": {"type": "string", "description": "add 单次活动的课名"},
                "period_no": {"type": "integer", "description": "第几节"},
                "location": {"type": "string", "description": "地点（add 时可覆盖）"},
                "note": {"type": "string", "description": "备注"},
            },
            "required": ["date", "kind"],
        },
    )
    async def add_exception(
        self,
        date: str,
        kind: str,
        course_id: int = 0,
        title: str = "",
        period_no: int = 0,
        location: str = "",
        note: str = "",
        **_,
    ):
        d = parse_date(date)
        if not d:
            return Err(SdkError("date 需为 YYYY-MM-DD"))
        if kind not in ("cancel", "add"):
            return Err(SdkError("kind 需为 cancel 或 add"))
        if kind == "cancel" and (not course_id or not period_no):
            return Err(SdkError("cancel 需提供 course_id 与 period_no"))
        sem = await self.repo.get_active_semester()
        if not sem:
            return Err(SdkError("没有当前学期"))
        rec = await self.repo.add_exception(
            semester_id=sem["id"],
            course_id=int(course_id) or None,
            title=title or None,
            date=d,
            kind=kind,
            period_no=int(period_no) or None,
            location=location or None,
            note=note or None,
        )
        return Ok({"exception": rec})

    @plugin_entry(id="list_courses", name="课程列表", description="列出当前（或指定）学期的所有课程")
    async def list_courses(self, semester_id: int = 0, **_):
        if semester_id:
            sid = int(semester_id)
        else:
            sem = await self.repo.get_active_semester()
            if not sem:
                return Err(SdkError("没有当前学期"))
            sid = sem["id"]
        courses = await self.repo.list_courses(sid)
        return Ok({"count": len(courses), "courses": courses})

    @plugin_entry(
        id="set_period_times",
        name="设置节次时间",
        description="覆盖当前/指定学期的节次作息时间。periods=[{period_no,start_time,end_time,slot}]",
        input_schema={
            "type": "object",
            "properties": {
                "semester_id": {"type": "integer", "description": "学期 ID；不填用当前学期"},
                "periods": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "period_no": {"type": "integer"},
                            "start_time": {"type": "string", "description": "HH:MM"},
                            "end_time": {"type": "string", "description": "HH:MM"},
                            "slot": {"type": "string", "description": "morning/afternoon/evening"},
                        },
                    },
                },
            },
            "required": ["periods"],
        },
    )
    async def set_period_times(self, periods: list, semester_id: int = 0, **_):
        if not periods:
            return Err(SdkError("periods 不能为空"))
        # 校验并归一化
        cleaned: list[dict] = []
        seen_nos: set[int] = set()
        for p in periods:
            if not isinstance(p, dict):
                return Err(SdkError(f"periods 每项必须是对象: {p!r}"))
            try:
                pno = int(p.get("period_no"))
            except (TypeError, ValueError):
                return Err(SdkError(f"节次号必须是整数: {p.get('period_no')!r}"))
            if pno < 1 or pno > 30:
                return Err(SdkError(f"节次号需在 1-30 之间: 第{pno}节"))
            st = str(p.get("start_time") or "").strip()
            et = str(p.get("end_time") or "").strip()
            if not _HHMM_RE.match(st) or not _HHMM_RE.match(et):
                return Err(SdkError(f"第{pno}节时间格式错误，需为 HH:MM（如 08:00）"))
            sh, sm = (int(x) for x in st.split(":"))
            eh, em = (int(x) for x in et.split(":"))
            if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
                return Err(SdkError(f"第{pno}节时间超出合法范围"))
            slot = str(p.get("slot") or "morning").strip()
            if slot not in _VALID_SLOTS:
                slot = "morning"
            if pno in seen_nos:
                continue
            seen_nos.add(pno)
            cleaned.append({"period_no": pno, "start_time": st, "end_time": et, "slot": slot})
        if not cleaned:
            return Err(SdkError("没有有效的节次时间"))
        cleaned.sort(key=lambda x: x["period_no"])
        if semester_id:
            sid = int(semester_id)
        else:
            sem = await self.repo.get_active_semester()
            if not sem:
                return Err(SdkError("没有当前学期"))
            sid = sem["id"]
        n = await self.repo.set_period_times(sid, cleaned)
        self.logger.info("节次作息已更新: 学期 {} 共 {} 节", sid, n)
        return Ok({"updated": n, "semester_id": sid, "periods": cleaned})
