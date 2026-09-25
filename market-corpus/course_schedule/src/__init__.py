"""课程表插件 (Course Schedule)

通用课程表工具，采用「周重复 + 例外」时间模型。
P1：学期管理、手动录入课程、今日/明日/本周/下节课查询、周数与进度。
P2：上课提醒、作业/考试管理、倒计时。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    neko_plugin,
    timer_interval,
)
from plugin.sdk.plugin.ui import UI_ACTION_META_ATTR
from plugin.sdk.plugin.ui import context as ui_context

from ._repo import ScheduleRepo
from ._time import period_to_datetime, week_number, weekday_of
from .routers import AcademicRouter, ClearDataRouter, ImportExportRouter, ManageRouter, QueryRouter, TasksRouter

_DEFAULT_TZ = "Asia/Shanghai"


@neko_plugin
class CourseSchedulePlugin(NekoPluginBase):
    # 声明 router 类，供主进程静态扫描 entry 元数据（UI 面板依赖）
    __routers__ = [
        ManageRouter,
        QueryRouter,
        TasksRouter,
        ImportExportRouter,
        AcademicRouter,
        ClearDataRouter,
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.repo = ScheduleRepo(self)
        self._tz = ZoneInfo(_DEFAULT_TZ)
        self._remind_enabled = False
        self._remind_lead = 10
        self._reminded: set[tuple[str, int]] = set()
        self._reminded_date: str | None = None
        for router_cls in self.__routers__:
            self.include_router(router_cls())
        # 批量给所有 entry handler 加默认 UI action 元数据（宿主从这里构建白名单）
        self._attach_default_ui_action_meta()

    def _attach_default_ui_action_meta(self) -> None:
        """遍历所有 router 的 entry，给没带 @ui.action 的 handler 加默认元数据。

        bound method 不能 setattr，必须 setattr 到 __func__ 指向的原始函数。
        宿主的 _get_ui_action_meta() 会遍历 wrapper chain（含 __func__），
        所以 setattr 到原始函数上能被正确识别。
        """
        count = 0
        for router in getattr(self, "_routers", []) or []:
            try:
                entries = router.collect_entries() or {}
            except Exception:
                continue
            for entry_id, event_handler in entries.items():
                handler = getattr(event_handler, "handler", event_handler)
                # bound method 不能 setattr，拿底层 __func__（原始函数）
                target = getattr(handler, "__func__", handler)
                seen: set[int] = set()
                current: Any = target
                did_set = False
                while callable(current) and id(current) not in seen:
                    seen.add(id(current))
                    if not hasattr(current, UI_ACTION_META_ATTR):
                        try:
                            setattr(
                                current,
                                UI_ACTION_META_ATTR,
                                {
                                    "id": entry_id,
                                    "label": entry_id,
                                    "tone": "default",
                                    "group": None,
                                    "order": 0,
                                    "confirm": False,
                                    "refresh_context": True,
                                },
                            )
                            did_set = True
                        except Exception:
                            pass
                    current = getattr(current, "__wrapped__", None)
                if did_set:
                    count += 1
        self.logger.info("Attached default UI action meta to {} entries", count)

    @property
    def tz(self):
        return self._tz

    @ui_context(id="dashboard", title="课程表")
    async def get_main_ui_context(self):
        """Hosted UI 面板的状态数据。actions 白名单由宿主从 @ui.action 装饰器自动构建。"""
        try:
            semesters = await self.repo.list_semesters()
            active = next((s for s in semesters if s["is_active"]), None)
            sem_count = len(semesters)
        except Exception:
            sem_count = 0
            active = None

        return {
            "semesters_count": sem_count,
            "active_semester_id": active["id"] if active else None,
            "remind_enabled": self._remind_enabled,
        }

    def _load_course_cfg(self, cfg: dict) -> None:
        cs_cfg = cfg.get("course") if isinstance(cfg.get("course"), dict) else {}
        tz_name = str(cs_cfg.get("timezone", _DEFAULT_TZ)).strip()
        try:
            self._tz = ZoneInfo(tz_name)
        except Exception:
            self._tz = ZoneInfo(_DEFAULT_TZ)
        self._remind_enabled = bool(cs_cfg.get("remind_enabled", False))
        self._remind_lead = max(1, int(cs_cfg.get("remind_lead_minutes", 10)))

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._load_course_cfg(cfg)
        try:
            await self.repo.ensure_schema()
        except Exception as exc:
            self.logger.exception("ensure_schema failed: {}", exc)
            return Ok({"status": "degraded", "error": str(exc)})
        sem_count = len(await self.repo.list_semesters())
        self.logger.info(
            "CourseSchedule started, tz={}, semesters={}, remind={}", self._tz, sem_count, self._remind_enabled
        )
        return Ok(
            {
                "status": "ready",
                "timezone": str(self._tz),
                "semesters": sem_count,
                "remind_enabled": self._remind_enabled,
            }
        )

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self.logger.info("CourseSchedule shutdown")
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config, new_config, mode, **_):
        new = new_config or {}
        self._load_course_cfg(new)
        self.logger.info(
            "CourseSchedule config changed, mode={}, tz={}, remind={}", mode, self._tz, self._remind_enabled
        )
        return Ok({"status": "config_updated"})

    @timer_interval(
        id="class_reminder_check",
        seconds=60,
        auto_start=True,
        name="上课提醒检查",
        description="每分钟检查是否有即将开始的课程并发送提醒",
    )
    async def check_upcoming_classes(self, **_):
        if not self._remind_enabled:
            return Ok({"skipped": "remind_disabled"})
        sem = await self.repo.get_active_semester()
        if not sem:
            return Ok({"skipped": "no_semester"})
        now = datetime.now(self._tz)
        today = now.date()
        today_str = today.isoformat()

        # 日期变更则清空提醒记录
        if self._reminded_date != today_str:
            self._reminded.clear()
            self._reminded_date = today_str

        sessions = await self.repo.resolve_sessions_for_date(sem, today)
        if not sessions:
            return Ok({"skipped": "no_classes_today"})
        pt = await self.repo.get_period_times(sem["id"])
        wn = week_number(sem, today)
        reminded_count = 0

        for s in sorted(sessions, key=lambda x: x.get("period_no") or 0):
            pno = s.get("period_no")
            if not pno:
                continue
            key = (today_str, int(pno))
            if key in self._reminded:
                continue
            pinfo = pt.get(pno)
            if not pinfo:
                continue
            start = period_to_datetime(today, pinfo["start_time"], self._tz)
            if not start:
                continue
            minutes_until = int((start - now).total_seconds() // 60)
            if 0 <= minutes_until <= self._remind_lead:
                name = s.get("name") or "未命名"
                loc = s.get("location") or ""
                end_time = pinfo["end_time"]
                teacher = s.get("teacher") or ""
                text = (
                    f"⏰ 下节课提醒：第{pno}节 {pinfo['start_time']}-{end_time} {name}"
                    + (f"（{loc}）" if loc else "")
                    + (f" {teacher}" if teacher else "")
                    + f"，约 {minutes_until} 分钟后开始"
                )
                try:
                    self.ctx.push_message(
                        source="course_schedule",
                        visibility=[],
                        ai_behavior="respond",
                        parts=[{"type": "text", "text": text}],
                        priority=7,
                        metadata={
                            "event_type": "class_reminder",
                            "period_no": int(pno),
                            "weekday": weekday_of(today),
                            "week": wn,
                            "minutes_until": minutes_until,
                        },
                    )
                    self._reminded.add(key)
                    reminded_count += 1
                    self.logger.info("Class reminder sent: {} period {} ({}min)", name, pno, minutes_until)
                except Exception as exc:
                    self.logger.exception("push_message failed: {}", exc)

        return Ok({"checked": len(sessions), "reminded": reminded_count})
