"""作业 / 考试 / 倒计时入口。"""

from __future__ import annotations

from datetime import datetime

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry
from plugin.sdk.shared.core.router import PluginRouter

from .._time import parse_date, week_number


class TasksRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="tasks")

    @property
    def repo(self):
        return self.main_plugin.repo

    def _tz(self):
        return self.main_plugin.tz

    def _resolve_semester(self, sem, semester_id):
        if semester_id:
            return {"id": int(semester_id)}
        if not sem:
            return None
        return sem

    # ── 作业 ──

    @plugin_entry(
        id="add_homework",
        name="添加作业",
        description="为当前（或指定）学期的课程添加一条作业，due_at 为截止日期 YYYY-MM-DD。",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "作业标题"},
                "course_id": {"type": "integer", "description": "关联课程 ID（可空）"},
                "due_at": {"type": "string", "description": "截止日期 YYYY-MM-DD"},
                "note": {"type": "string", "description": "备注/要求"},
                "semester_id": {"type": "integer", "description": "学期 ID；不填用当前学期"},
            },
            "required": ["title"],
        },
    )
    async def add_homework(
        self, title: str, course_id: int = 0, due_at: str = "", note: str = "", semester_id: int = 0, **_
    ):
        sem = await self.repo.get_active_semester()
        sem_ref = self._resolve_semester(sem, semester_id)
        if not sem_ref:
            return Err(SdkError("没有当前学期，请先 add_semester"))
        d = parse_date(due_at) if due_at else None
        rec = await self.repo.add_assignment(
            semester_id=sem_ref["id"],
            kind="homework",
            title=title,
            course_id=int(course_id) or None,
            due_at=str(d) if d else None,
            note=note or None,
        )
        return Ok({"homework": rec})

    @plugin_entry(
        id="list_homework",
        name="作业列表",
        description="列出当前（或指定）学期的作业，可按状态 pending/done 过滤。",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "pending 或 done；不填=全部"},
                "course_id": {"type": "integer", "description": "按课程过滤"},
                "semester_id": {"type": "integer", "description": "学期 ID；不填用当前学期"},
            },
        },
    )
    async def list_homework(self, status: str = "", course_id: int = 0, semester_id: int = 0, **_):
        sem = await self.repo.get_active_semester()
        sem_ref = self._resolve_semester(sem, semester_id)
        if not sem_ref:
            return Err(SdkError("没有当前学期"))
        rows = await self.repo.list_assignments(
            semester_id=sem_ref["id"],
            kind="homework",
            status=status or None,
            course_id=int(course_id) or None,
        )
        today = datetime.now(self._tz()).date()
        for r in rows:
            d = parse_date(r.get("due_at"))
            r["overdue"] = bool(d and d < today and not r.get("done"))
        return Ok({"count": len(rows), "homework": rows})

    @plugin_entry(
        id="done_homework",
        name="完成作业",
        description="把作业标记为已完成（done=1）或取消完成（done=0）。",
        input_schema={
            "type": "object",
            "properties": {
                "homework_id": {"type": "integer", "description": "作业 ID"},
                "undone": {"type": "boolean", "description": "true=取消完成，false/不填=标记完成"},
            },
            "required": ["homework_id"],
        },
    )
    async def done_homework(self, homework_id: int, undone: bool = False, **_):
        rec = await self.repo.update_assignment(int(homework_id), done=0 if undone else 1)
        if not rec:
            return Err(SdkError(f"未找到作业 id={homework_id}"))
        return Ok({"homework": rec})

    @plugin_entry(
        id="delete_homework",
        name="删除作业",
        description="删除一条作业。",
        input_schema={
            "type": "object",
            "properties": {"homework_id": {"type": "integer", "description": "作业 ID"}},
            "required": ["homework_id"],
        },
    )
    async def delete_homework(self, homework_id: int, **_):
        ok = await self.repo.delete_assignment(int(homework_id))
        if not ok:
            return Err(SdkError(f"未找到作业 id={homework_id}"))
        return Ok({"deleted": True, "id": int(homework_id)})

    # ── 考试 ──

    @plugin_entry(
        id="add_exam",
        name="添加考试",
        description="为当前学期添加一条考试安排，due_at 为考试日期 YYYY-MM-DD，location 为考场。",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "考试名称，如期中考试"},
                "course_id": {"type": "integer", "description": "关联课程 ID（可空）"},
                "due_at": {"type": "string", "description": "考试日期 YYYY-MM-DD"},
                "location": {"type": "string", "description": "考场"},
                "note": {"type": "string", "description": "考试范围/备注"},
                "semester_id": {"type": "integer", "description": "学期 ID；不填用当前学期"},
            },
            "required": ["title"],
        },
    )
    async def add_exam(
        self,
        title: str,
        course_id: int = 0,
        due_at: str = "",
        location: str = "",
        note: str = "",
        semester_id: int = 0,
        **_,
    ):
        sem = await self.repo.get_active_semester()
        sem_ref = self._resolve_semester(sem, semester_id)
        if not sem_ref:
            return Err(SdkError("没有当前学期，请先 add_semester"))
        d = parse_date(due_at) if due_at else None
        rec = await self.repo.add_assignment(
            semester_id=sem_ref["id"],
            kind="exam",
            title=title,
            course_id=int(course_id) or None,
            due_at=str(d) if d else None,
            location=location or None,
            note=note or None,
        )
        return Ok({"exam": rec})

    @plugin_entry(
        id="list_exams",
        name="考试列表",
        description="列出当前学期的所有考试。",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "pending 或 done；不填=全部"},
                "semester_id": {"type": "integer", "description": "学期 ID；不填用当前学期"},
            },
        },
    )
    async def list_exams(self, status: str = "", semester_id: int = 0, **_):
        sem = await self.repo.get_active_semester()
        sem_ref = self._resolve_semester(sem, semester_id)
        if not sem_ref:
            return Err(SdkError("没有当前学期"))
        rows = await self.repo.list_assignments(
            semester_id=sem_ref["id"],
            kind="exam",
            status=status or None,
        )
        today = datetime.now(self._tz()).date()
        for r in rows:
            d = parse_date(r.get("due_at"))
            r["overdue"] = bool(d and d < today and not r.get("done"))
        return Ok({"count": len(rows), "exams": rows})

    @plugin_entry(
        id="delete_exam",
        name="删除考试",
        description="删除一条考试安排。",
        input_schema={
            "type": "object",
            "properties": {"exam_id": {"type": "integer", "description": "考试 ID"}},
            "required": ["exam_id"],
        },
    )
    async def delete_exam(self, exam_id: int, **_):
        ok = await self.repo.delete_assignment(int(exam_id))
        if not ok:
            return Err(SdkError(f"未找到考试 id={exam_id}"))
        return Ok({"deleted": True, "id": int(exam_id)})

    # ── 倒计时 ──

    @plugin_entry(
        id="get_countdown",
        name="倒计时",
        description="查看距离下一场考试、最近作业截止、学期结束还有多少天。",
        llm_result_fields=["summary", "days_until_next_exam", "days_until_semester_end"],
    )
    async def get_countdown(self, **_):
        sem = await self.repo.get_active_semester()
        if not sem:
            return Err(SdkError("没有当前学期"))
        today = datetime.now(self._tz()).date()
        sid = sem["id"]

        # 下一场考试
        exams = await self.repo.get_upcoming_assignments(semester_id=sid, kind="exam", limit=5)
        next_exam = None
        days_exam = None
        for e in exams:
            d = parse_date(e.get("due_at"))
            if d and d >= today:
                next_exam = e
                days_exam = (d - today).days
                break

        # 最近作业截止
        hws = await self.repo.get_upcoming_assignments(semester_id=sid, kind="homework", limit=5)
        next_hw = None
        days_hw = None
        for h in hws:
            d = parse_date(h.get("due_at"))
            if d and d >= today:
                next_hw = h
                days_hw = (d - today).days
                break

        # 学期结束
        end = parse_date(sem.get("end_date"))
        days_end = (end - today).days if end else None

        # 周数
        wn = week_number(sem, today)
        total = int(sem.get("total_weeks") or 0)

        parts = []
        if days_exam is not None:
            name = next_exam.get("course_name") or next_exam.get("title") or "考试"
            parts.append(f"距离「{name}」考试还有 {days_exam} 天")
        if days_hw is not None:
            parts.append(f"距离「{next_hw.get('title')}」作业截止还有 {days_hw} 天")
        if days_end is not None:
            parts.append(f"距离学期结束还有 {days_end} 天")
        if wn and total:
            parts.append(f"当前第 {wn}/{total} 周")
        summary = "；".join(parts) if parts else "暂无即将到来的考试或作业"

        return Ok(
            {
                "today": today.isoformat(),
                "days_until_next_exam": days_exam,
                "next_exam": next_exam,
                "days_until_next_homework": days_hw,
                "next_homework": next_hw,
                "days_until_semester_end": days_end,
                "week": wn,
                "total_weeks": total,
                "summary": summary,
            }
        )
