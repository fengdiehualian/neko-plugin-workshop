"""课程表查询入口：今日 / 明日 / 本周 / 下节课 / 周数进度。"""

from __future__ import annotations

from datetime import datetime, timedelta

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry
from plugin.sdk.shared.core.router import PluginRouter

from .._time import format_schedule, period_to_datetime, week_number, weekday_of


class QueryRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="query")

    @property
    def repo(self):
        return self.main_plugin.repo

    def _tz(self):
        return self.main_plugin.tz

    async def _resolve(self, target_date):
        sem = await self.repo.get_active_semester()
        if not sem:
            return None, None, None
        sessions = await self.repo.resolve_sessions_for_date(sem, target_date)
        period_times = await self.repo.get_period_times(sem["id"])
        return sem, sessions, period_times

    @plugin_entry(
        id="get_today_schedule",
        name="今日课表",
        description="查询今天的课程安排",
        llm_result_fields=["summary", "count", "week"],
    )
    async def get_today_schedule(self, **_):
        today = datetime.now(self._tz()).date()
        sem, sessions, pt = await self._resolve(today)
        if not sem:
            return Err(SdkError("没有当前学期，请先 add_semester"))
        return Ok(
            {
                "date": today.isoformat(),
                "weekday": weekday_of(today),
                "week": week_number(sem, today),
                "count": len(sessions),
                "summary": format_schedule(today, sessions, pt),
                "sessions": sessions,
            }
        )

    @plugin_entry(
        id="get_tomorrow_schedule",
        name="明日课表",
        description="查询明天的课程安排",
        llm_result_fields=["summary", "count", "week"],
    )
    async def get_tomorrow_schedule(self, **_):
        tomorrow = datetime.now(self._tz()).date() + timedelta(days=1)
        sem, sessions, pt = await self._resolve(tomorrow)
        if not sem:
            return Err(SdkError("没有当前学期"))
        return Ok(
            {
                "date": tomorrow.isoformat(),
                "weekday": weekday_of(tomorrow),
                "week": week_number(sem, tomorrow),
                "count": len(sessions),
                "summary": format_schedule(tomorrow, sessions, pt),
                "sessions": sessions,
            }
        )

    @plugin_entry(
        id="get_week_schedule",
        name="本周课表",
        description="查询本周（或指定偏移周，0=本周、1=下周、-1=上周）每天的课表",
        llm_result_fields=["summary", "count"],
    )
    async def get_week_schedule(self, week_offset: int = 0, **_):
        sem = await self.repo.get_active_semester()
        if not sem:
            return Err(SdkError("没有当前学期"))
        today = datetime.now(self._tz()).date()
        monday = today - timedelta(days=today.isoweekday() - 1) + timedelta(weeks=int(week_offset))
        pt = await self.repo.get_period_times(sem["id"])
        days = []
        total = 0
        for i in range(7):
            d = monday + timedelta(days=i)
            sessions = await self.repo.resolve_sessions_for_date(sem, d)
            days.append(
                {
                    "date": d.isoformat(),
                    "weekday": weekday_of(d),
                    "week": week_number(sem, d),
                    "count": len(sessions),
                    "summary": format_schedule(d, sessions, pt),
                }
            )
            total += len(sessions)
        return Ok(
            {
                "week_offset": int(week_offset),
                "count": total,
                "days": days,
                "summary": "\n\n".join(d["summary"] for d in days),
            }
        )

    @plugin_entry(
        id="get_schedule_view",
        name="课表视图数据",
        description=(
            "返回周课表网格（weekday × period_no）的数据，用于前端渲染。"
            "返回 grid[weekday][period_no] = [course_block, ...]，"
            "其中 course_block 含课程名、教师、地点、周次等。"
        ),
    )
    async def get_schedule_view(self, semester_id: int = 0, **_):
        sid = int(semester_id or 0)
        if not sid:
            sem = await self.repo.get_active_semester()
            if not sem:
                return Err(SdkError("没有当前学期"))
            sid = sem["id"]
        import json as _json

        async with await self.repo._session() as session:
            cur = await session.execute(
                "SELECT cs.weekday, cs.period_no, cs.weeks, "
                "c.name, c.teacher, c.location, c.color "
                "FROM course_sessions cs JOIN courses c ON c.id = cs.course_id "
                "WHERE c.semester_id = ? "
                "ORDER BY cs.weekday, cs.period_no",
                (sid,),
            )
            blocks = cur.fetchall()

        pt = await self.repo.get_period_times(sid)
        # period_times 排序后的 period_no 列表
        sorted_periods = sorted(pt.keys()) if pt else list(range(1, 12))

        # 构造 grid: {weekday: {period_no: [block, ...]}}
        grid: dict[int, dict[int, list[dict]]] = {wd: {pno: [] for pno in sorted_periods} for wd in range(1, 8)}
        for r in blocks:
            b = dict(r)
            wd = int(b["weekday"])
            pno = int(b["period_no"])
            if wd not in grid:
                continue
            if pno not in grid[wd]:
                grid[wd][pno] = []
            weeks = []
            if b.get("weeks"):
                try:
                    weeks = _json.loads(b["weeks"])
                except Exception:
                    pass
            grid[wd][pno].append(
                {
                    "name": b["name"],
                    "teacher": b.get("teacher") or "",
                    "location": b.get("location") or "",
                    "color": b.get("color") or "",
                    "weeks": weeks,
                    "period_no": pno,
                    "weekday": wd,
                }
            )

        return Ok(
            {
                "semester_id": sid,
                "period_times": pt,
                "periods": sorted_periods,
                "grid": grid,
                "weekdays": [1, 2, 3, 4, 5, 6, 7],
            }
        )

    @plugin_entry(
        id="get_next_class",
        name="下节课",
        description="查询下一节课是什么、几点开始、还有多久（分钟）",
        llm_result_fields=["summary", "minutes_until"],
    )
    async def get_next_class(self, **_):
        sem = await self.repo.get_active_semester()
        if not sem:
            return Err(SdkError("没有当前学期"))
        now = datetime.now(self._tz())
        today = now.date()
        pt = await self.repo.get_period_times(sem["id"])
        for offset in range(0, 8):
            d = today + timedelta(days=offset)
            sessions = await self.repo.resolve_sessions_for_date(sem, d)
            for s in sorted(sessions, key=lambda x: x.get("period_no") or 0):
                pinfo = pt.get(s["period_no"])
                if not pinfo:
                    continue
                start = period_to_datetime(d, pinfo["start_time"], self._tz())
                if start and (offset > 0 or start > now):
                    minutes_until = int((start - now).total_seconds() // 60)
                    name = s.get("name") or "未命名"
                    loc = s.get("location") or ""
                    summary = (
                        f"下节课：第{s['period_no']}节 {pinfo['start_time']}-{pinfo['end_time']} {name}"
                        + (f"（{loc}）" if loc else "")
                        + f"，约 {minutes_until} 分钟后"
                    )
                    return Ok(
                        {
                            "date": d.isoformat(),
                            "period_no": s["period_no"],
                            "start_at": start.isoformat(),
                            "minutes_until": minutes_until,
                            "course": name,
                            "location": loc,
                            "summary": summary,
                        }
                    )
        return Ok({"summary": "最近一周没有课", "minutes_until": None})

    @plugin_entry(
        id="get_week_info",
        name="周数与进度",
        description="当前是第几周、学期进度",
        llm_result_fields=["summary", "week", "progress"],
    )
    async def get_week_info(self, **_):
        sem = await self.repo.get_active_semester()
        if not sem:
            return Err(SdkError("没有当前学期"))
        today = datetime.now(self._tz()).date()
        wn = week_number(sem, today)
        total = int(sem.get("total_weeks") or 0)
        progress = round(min(wn, total) / total, 2) if (wn and total) else None
        if wn:
            summary = f"学期「{sem['name']}」当前为第 {wn}/{total} 周"
        else:
            summary = f"今天不在学期「{sem['name']}」范围内"
        return Ok(
            {
                "semester": sem["name"],
                "week": wn,
                "total_weeks": total,
                "progress": progress,
                "today": today.isoformat(),
                "summary": summary,
            }
        )
