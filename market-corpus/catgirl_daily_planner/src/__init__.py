"""
猫娘每日计划插件 v0.2 (Catgirl Daily Planner)

猫娘每天为主人定制专属计划,温柔地按点提醒该做什么事,并在年终/月末复盘主人的努力。

v0.2 升级:
  - 本地备份 ticker:即使 memo_reminder 没起来,catgirl 自己也按分钟巡检任务时间,推送提醒
  - 任务新增 category(分类) / priority(优先级) / recurring(重复规则)
  - 内置「工作日 / 休息日 / 学习日」快速模板,一键铺满一日
  - 新增 list_calendar / get_stats / apply_template / list_templates / set_task_meta / add_recurring / get_year_overview 等入口
  - 前端:年历 + 月历 + 统计 + 分类筛选 + 模板选择,UI 更现代

数据模型:
  plans/<date>  -> {
      "date": "2026-06-04",
      "created_at": "...",
      "tasks": [
          {
              "id": "abc123",
              "time": "09:00",
              "title": "写日报",
              "description": "...",
              "catgirl_message": "...",
              "category": "work",      # work/study/life/rest/exercise/fun/other
              "priority": 1,           # 1=高 2=中 3=低
              "status": "pending",     # pending / done / skipped
              "reminder_id": "...",    # memo_reminder 的提醒 id
              "local_fired_at": "...", # 本地备份 ticker 已推送过
              "recurring_id": "...",   # 若来自 recurring 任务,记下规则 id
              "acknowledged": false,   # 用户是否已确认该提醒
              "next_reminder_at": "..." # 下次提醒时间(ISO格式),用于延迟提醒
          },
          ...
      ],
      "morning_announced": false,
      "evening_recap_done": false
  }

  recurring/<id>  -> {"id":..., "weekdays": [0,1,2,3,4], "template": {...}, "created_at": ...}
  stats_<yyyy-mm> -> {"month":..., "total":..., "done":..., "by_category": {...}, "by_day": {...}}
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

# ── 常量 ──────────────────────────────────────────────────────────────

_PLAN_KEY_PREFIX = "plan:"
_RECURRING_KEY_PREFIX = "recur:"
_HISTORY_KEY = "plan_history"
_STATS_KEY_PREFIX = "stats:"
_DEFAULT_TZ = "Asia/Shanghai"
_MAX_HISTORY_DAYS = 365       # 保留一年的历史,够做年历了
_MAX_TASKS = 30
_STATS_RETENTION_MONTHS = 12  # 月度统计保留 12 个月

# 任务状态
_STATUS_PENDING = "pending"
_STATUS_DONE = "done"
_STATUS_SKIPPED = "skipped"

# 任务分类(单一字段;扩展时保持向后兼容)
_CATEGORIES: Dict[str, str] = {
    "work":     "💼 工作",
    "study":    "📚 学习",
    "life":     "🏠 生活",
    "rest":     "☕ 休息",
    "exercise": "💪 运动",
    "fun":      "🎮 娱乐",
    "other":    "📌 其他",
}
DEFAULT_CATEGORY = "other"

# 优先级(数字越小越重要)
PRIORITY_HIGH = 1
PRIORITY_MEDIUM = 2
PRIORITY_LOW = 3
_PRIORITY_LABELS = {1: "高", 2: "中", 3: "低"}

# 星期(0=周一 ... 6=周日),与 datetime.weekday() 一致
WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"]

# 早安/晚安/任务 推送的 ai_behavior
_BEHAVIOR = "respond"

# 本地提醒去重窗口(秒):同一任务在 1 小时内不重复推
_LOCAL_DEDUP_WINDOW = 3600

# ── 模板 ──────────────────────────────────────────────────────────────

_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "workday": {
        "id": "workday",
        "name": "工作日",
        "icon": "💼",
        "desc": "朝九晚六的标准工作日节奏",
        "tasks": [
            {"time": "07:30", "title": "起床 + 早餐",    "category": "life",     "priority": 2},
            {"time": "09:00", "title": "开始工作",        "category": "work",     "priority": 1},
            {"time": "10:30", "title": "短暂休息",        "category": "rest",     "priority": 3},
            {"time": "12:00", "title": "午餐",            "category": "life",     "priority": 2},
            {"time": "13:00", "title": "下午工作",        "category": "work",     "priority": 1},
            {"time": "15:30", "title": "喝杯茶 / 走动",  "category": "rest",     "priority": 3},
            {"time": "18:00", "title": "下班",            "category": "life",     "priority": 2},
            {"time": "19:00", "title": "晚餐",            "category": "life",     "priority": 2},
            {"time": "20:00", "title": "自由时间",        "category": "fun",      "priority": 3},
            {"time": "22:30", "title": "准备睡觉",        "category": "life",     "priority": 2},
        ],
    },
    "weekend": {
        "id": "weekend",
        "name": "休息日",
        "icon": "🌴",
        "desc": "放松充电,做点喜欢的事",
        "tasks": [
            {"time": "09:00", "title": "睡个懒觉",        "category": "rest",     "priority": 3},
            {"time": "11:00", "title": "早午餐",          "category": "life",     "priority": 2},
            {"time": "14:00", "title": "运动 / 外出",     "category": "exercise", "priority": 2},
            {"time": "16:00", "title": "自由安排",        "category": "fun",      "priority": 3},
            {"time": "19:00", "title": "晚餐",            "category": "life",     "priority": 2},
            {"time": "21:00", "title": "看部电影",        "category": "fun",      "priority": 3},
            {"time": "22:30", "title": "睡觉",            "category": "life",     "priority": 2},
        ],
    },
    "study": {
        "id": "study",
        "name": "学习日",
        "icon": "📚",
        "desc": "高强度学习,番茄钟节奏",
        "tasks": [
            {"time": "07:00", "title": "起床 + 早餐",     "category": "life",     "priority": 2},
            {"time": "08:00", "title": "晨读 1h",         "category": "study",    "priority": 1},
            {"time": "10:00", "title": "学习 2h",         "category": "study",    "priority": 1},
            {"time": "12:00", "title": "午餐 + 休息",     "category": "rest",     "priority": 2},
            {"time": "14:00", "title": "刷题 / 实践",     "category": "study",    "priority": 1},
            {"time": "17:00", "title": "复盘",            "category": "study",    "priority": 1},
            {"time": "19:00", "title": "晚餐",            "category": "life",     "priority": 2},
            {"time": "20:00", "title": "阅读",            "category": "study",    "priority": 2},
            {"time": "22:30", "title": "睡觉",            "category": "life",     "priority": 2},
        ],
    },
    "fitness": {
        "id": "fitness",
        "name": "健身日",
        "icon": "💪",
        "desc": "围绕训练 + 恢复",
        "tasks": [
            {"time": "07:00", "title": "起床 + 蛋白质早餐", "category": "life",     "priority": 2},
            {"time": "10:00", "title": "力量训练",         "category": "exercise", "priority": 1},
            {"time": "12:00", "title": "午餐",             "category": "life",     "priority": 2},
            {"time": "15:00", "title": "有氧 / 散步",      "category": "exercise", "priority": 2},
            {"time": "18:00", "title": "拉伸 + 恢复",      "category": "exercise", "priority": 2},
            {"time": "19:00", "title": "晚餐",             "category": "life",     "priority": 2},
            {"time": "21:00", "title": "冥想",             "category": "rest",     "priority": 3},
            {"time": "22:30", "title": "睡觉",             "category": "life",     "priority": 2},
        ],
    },
}


# ── 工具函数 ──────────────────────────────────────────────────────────

def _now_in_tz(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def _today_key(tz: ZoneInfo) -> str:
    return _now_in_tz(tz).strftime("%Y-%m-%d")


def _parse_hhmm(raw: str) -> Optional[dtime]:
    raw = (raw or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def _parse_date(raw: str, tz: ZoneInfo) -> Optional[str]:
    """把 YYYY-MM-DD / 今天 / 明天 / 后天 / YYYYMMDD 统一成 YYYY-MM-DD。"""
    if not raw:
        return _today_key(tz)
    s = raw.strip()
    if s in ("今天", "today"):
        return _today_key(tz)
    if s in ("明天", "tomorrow"):
        return (_now_in_tz(tz) + timedelta(days=1)).strftime("%Y-%m-%d")
    if s in ("后天",):
        return (_now_in_tz(tz) + timedelta(days=2)).strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _coerce_priority(p: Any) -> int:
    try:
        v = int(p)
    except (TypeError, ValueError):
        return PRIORITY_MEDIUM
    if v not in (1, 2, 3):
        return PRIORITY_MEDIUM
    return v


def _coerce_category(c: Any) -> str:
    s = str(c or "").strip().lower()
    return s if s in _CATEGORIES else DEFAULT_CATEGORY


def _make_catgirl_greeting(
    catgirl_name: str,
    master_name: str,
    task_count: int,
    first_task: Optional[Dict[str, Any]] = None,
) -> str:
    # v0.3: 使用 master_name 变量,支持猫娘动态修改称呼
    if first_task:
        ft_time = first_task.get("time", "??:??")
        ft_title = first_task.get("title", "事项")
        body = (
            f"早安喵~ {master_name},今天一共有 {task_count} 件事要做哦。\n"
            f"第一件是{ft_time}的《{ft_title}》,别忘了呢~ 喵呜~"
        )
    else:
        body = (
            f"早安喵~ {master_name},今天还没安排任务呢。\n"
            f"要不要让{catgirl_name}帮你规划一下今天要做些什么呀?"
        )
    return body


def _make_task_reminder(
    task: Dict[str, Any],
    catgirl_name: str,
    master_name: str,
) -> str:
    # v0.3: 使用 master_name 变量,支持猫娘动态修改称呼
    # v0.4: 在消息中包含 task_id,方便猫娘调用 acknowledge_task 工具
    title = task.get("title", "未命名任务")
    desc = (task.get("description") or "").strip()
    custom = (task.get("catgirl_message") or "").strip()
    task_id = task.get("id", "")
    
    if custom:
        return f"⏰ {title} | {custom}\n[task_id: {task_id}]"

    prio_map = {1: "🔥紧急", 2: "⭐重要", 3: "🌿一般"}
    p = prio_map.get(int(task.get("priority", 2)), "")
    prefix = (p + " ") if p else ""
    if desc:
        return (
            f"⏰ 到点啦~ {master_name}!该做《{title}》了喵。\n"
            f"小提示: {desc}\n"
            f"—— {catgirl_name}·喵呜~ ({prefix.strip()})\n"
            f"[task_id: {task_id}]"
        )
    return f"⏰ 到点啦~ {master_name}!{prefix}该做《{title}》了喵—— {catgirl_name}·喵呜~\n[task_id: {task_id}]"


def _make_goodnight(
    catgirl_name: str,
    master_name: str,
    done: int,
    pending: int,
) -> str:
    # v0.3: 使用 master_name 变量,支持猫娘动态修改称呼
    if pending == 0 and done > 0:
        return (
            f"晚安喵~ {master_name}。\n"
            f"今天完成了 {done} 件事,太棒啦,奖励你一个摸摸头~ ✨\n"
            f"好梦哦,{catgirl_name}去睡啦~ 喵~"
        )
    if done == 0 and pending > 0:
        return (
            f"嗯~ {master_name}今天还没完成任何事呢。\n"
            f"还有 {pending} 件事搁着,明天见啦,记得加油喵~"
        )
    return (
        f"晚安喵~ {master_name}。\n"
        f"今天完成了 {done} 件,还有 {pending} 件没做完。\n"
        f"明天继续加油,{catgirl_name}会陪着你哒~ 喵呜~"
    )


def _calc_streak(history: List[Dict[str, Any]]) -> int:
    """从今天往前数,连续多少天有 ≥1 件任务完成。"""
    if not history:
        return 0
    by_date = {h.get("date"): int(h.get("done", 0)) for h in history}
    today = _now_in_tz(ZoneInfo(_DEFAULT_TZ)).date()
    streak = 0
    d = today
    while True:
        key = d.strftime("%Y-%m-%d")
        if by_date.get(key, 0) > 0:
            streak += 1
            d -= timedelta(days=1)
        else:
            break
        if streak > 400:  # 防呆
            break
    return streak


# ── 主插件类 ──────────────────────────────────────────────────────────

@neko_plugin
class CatgirlDailyPlannerPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._tick_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 启动时由 startup() 注入
        self._tz: ZoneInfo = ZoneInfo(_DEFAULT_TZ)
        self._wake_at: dtime = dtime(8, 0)
        self._goodnight_at: dtime = dtime(22, 0)
        self._auto_regenerate: bool = True
        self._catgirl_name: str = "喵喵"
        self._master_name: str = "主人"

    # ── lifecycle ────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        section = cfg.get("planner") if isinstance(cfg.get("planner"), dict) else {}

        tz_name = str(section.get("timezone", _DEFAULT_TZ)).strip()
        try:
            self._tz = ZoneInfo(tz_name)
        except Exception as e:
            self._tz = ZoneInfo(_DEFAULT_TZ)
            self.logger.warning("invalid timezone {!r}, fallback to {} ({})", tz_name, _DEFAULT_TZ, e)

        wh = int(section.get("wake_hour", 8))
        wm = int(section.get("wake_minute", 0))
        gh = int(section.get("goodnight_hour", 22))
        gm = int(section.get("goodnight_minute", 0))
        self._wake_at = dtime(max(0, min(23, wh)), max(0, min(59, wm)))
        self._goodnight_at = dtime(max(0, min(23, gh)), max(0, min(59, gm)))
        self._auto_regenerate = bool(section.get("auto_daily_regenerate", True))
        self._catgirl_name = str(section.get("catgirl_nickname", "喵喵")).strip() or "喵喵"
        self._master_name = str(section.get("master_nickname", "主人")).strip() or "主人"

        if not self.store.enabled:
            self.store.enabled = True
            self.logger.warning("Store force-enabled (planner requires persistence)")

        # 启动时若已过 wake/goodnight,直接预置标志,避免补播
        try:
            now = _now_in_tz(self._tz)
            today = now.strftime("%Y-%m-%d")
            pre_plan = self._load_plan_unlocked(today)
            mutated = False
            if not pre_plan.get("morning_announced") and now.time() >= self._wake_at:
                pre_plan["morning_announced"] = True
                mutated = True
            if not pre_plan.get("evening_recap_done") and now.time() >= self._goodnight_at:
                pre_plan["evening_recap_done"] = True
                mutated = True
            if mutated:
                self._save_plan_unlocked(today, pre_plan)
        except Exception:
            self.logger.exception("startup pre-mark announcement flags failed (non-fatal)")

        # 启动时:把已有的 recurring 规则一次性铺到未来 14 天(防止上次只铺了 7 天
        # 但用户没及时用,导致今天没任务)
        try:
            self._expand_recurring_locked(days_ahead=14)
        except Exception:
            self.logger.exception("startup: expand recurring failed (non-fatal)")

        # 启动 ticker
        self._stop_event.clear()
        self._wake_event.clear()
        self._tick_thread = threading.Thread(
            target=self._tick_loop,
            daemon=True,
            name="catgirl-planner-tick",
        )
        self._tick_thread.start()

        self.logger.info(
            "CatgirlDailyPlanner v0.2 started: tz={}, wake={}, goodnight={}, catgirl={}, master={}",
            self._tz, self._wake_at, self._goodnight_at,
            self._catgirl_name, self._master_name,
        )
        return Ok({"status": "running", "version": "0.2.0", "timezone": str(self._tz)})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self._stop_event.set()
        self._wake_event.set()
        if self._tick_thread and self._tick_thread.is_alive():
            self._tick_thread.join(timeout=3.0)
        self.logger.info("CatgirlDailyPlanner shutdown")
        return Ok({"status": "shutdown"})

    # ── 存储辅助 ─────────────────────────────────────────────────────

    def _plan_key(self, date_str: str) -> str:
        return f"{_PLAN_KEY_PREFIX}{date_str}"

    def _recurring_key(self, rid: str) -> str:
        return f"{_RECURRING_KEY_PREFIX}{rid}"

    def _stats_key(self, ym: str) -> str:
        return f"{_STATS_KEY_PREFIX}{ym}"

    def _load_plan_unlocked(self, date_str: str) -> Dict[str, Any]:
        data = self.store._read_value(self._plan_key(date_str), None)
        if isinstance(data, dict):
            # 字段补全(老数据迁移)
            data.setdefault("tasks", [])
            data.setdefault("morning_announced", False)
            data.setdefault("evening_recap_done", False)
            for t in data.get("tasks", []):
                t.setdefault("category", DEFAULT_CATEGORY)
                t.setdefault("priority", PRIORITY_MEDIUM)
                t.setdefault("status", _STATUS_PENDING)
                t.setdefault("reminder_id", None)
                t.setdefault("local_fired_at", None)
            return data
        return {
            "date": date_str,
            "created_at": None,
            "tasks": [],
            "morning_announced": False,
            "evening_recap_done": False,
        }

    def _save_plan_unlocked(self, date_str: str, plan: Dict[str, Any]) -> None:
        self.store._write_value(self._plan_key(date_str), plan)

    def _load_plan(self, date_str: str) -> Dict[str, Any]:
        with self._lock:
            return self._load_plan_unlocked(date_str)

    def _save_plan(self, date_str: str, plan: Dict[str, Any]) -> None:
        with self._lock:
            self._save_plan_unlocked(date_str, plan)

    def _list_history_unlocked(self) -> List[str]:
        return self.store._read_value(_HISTORY_KEY, []) or []

    def _push_history_unlocked(self, date_str: str) -> None:
        hist = self._list_history_unlocked()
        if date_str in hist:
            return
        hist.append(date_str)
        # 保留 _MAX_HISTORY_DAYS 天,删除过期
        if len(hist) > _MAX_HISTORY_DAYS:
            hist = hist[-_MAX_HISTORY_DAYS:]
        self.store._write_value(_HISTORY_KEY, hist)

    def _record_monthly_stats_unlocked(self, date_str: str, plan: Dict[str, Any]) -> None:
        """把当日的完成情况汇总到月度统计,供 get_stats / get_year_overview 使用。

        修复:使用覆盖而非累加的方式记录当天统计,避免重复调用导致统计数据膨胀。
        """
        ym = date_str[:7]  # YYYY-MM
        try:
            stats = self.store._read_value(self._stats_key(ym), None) or {}
        except Exception:
            stats = {}
        if not isinstance(stats, dict):
            stats = {}
        stats.setdefault("month", ym)
        stats.setdefault("total", 0)
        stats.setdefault("done", 0)
        stats.setdefault("skipped", 0)
        stats.setdefault("by_category", {})
        stats.setdefault("by_day", {})
        stats.setdefault("_recorded_dates", {})  # 记录每天的统计快照

        tasks = plan.get("tasks", [])

        # 获取当天之前记录的统计快照(如果有的话)
        prev_snapshot = stats["_recorded_dates"].get(date_str, {})
        prev_total = prev_snapshot.get("total", 0)
        prev_done = prev_snapshot.get("done", 0)
        prev_skipped = prev_snapshot.get("skipped", 0)
        prev_by_category = prev_snapshot.get("by_category", {})

        # 计算当天的统计
        day_total = len(tasks)
        day_done = 0
        day_skipped = 0
        day_by_category = {}
        for t in tasks:
            cat = t.get("category") or DEFAULT_CATEGORY
            day_by_category[cat] = day_by_category.get(cat, 0) + 1
            st = t.get("status")
            if st == _STATUS_DONE:
                day_done += 1
            elif st == _STATUS_SKIPPED:
                day_skipped += 1

        # 用差值更新月度统计(先减去旧值,再加上新值),加 max(0, ...) 防止负数
        stats["total"] = max(0, stats["total"] - prev_total + day_total)
        stats["done"] = max(0, stats["done"] - prev_done + day_done)
        stats["skipped"] = max(0, stats["skipped"] - prev_skipped + day_skipped)

        # 更新分类统计
        for cat, count in prev_by_category.items():
            stats["by_category"][cat] = max(0, int(stats["by_category"].get(cat, 0)) - count)
        for cat, count in day_by_category.items():
            stats["by_category"][cat] = int(stats["by_category"].get(cat, 0)) + count

        # 更新 by_day
        stats["by_day"][date_str] = day_done

        # 保存当天的统计快照
        stats["_recorded_dates"][date_str] = {
            "total": day_total,
            "done": day_done,
            "skipped": day_skipped,
            "by_category": day_by_category,
        }

        self.store._write_value(self._stats_key(ym), stats)

    def _expand_recurring_locked(self, days_ahead: int = 14) -> None:
        """把所有 recurring 规则铺到今天起 days_ahead 天里。同步版本,只写 store,不调 push。"""
        try:
            ids = self.store._read_value("recur:ids", []) or []
        except Exception:
            ids = []
        if not isinstance(ids, list) or not ids:
            return
        today = _now_in_tz(self._tz).date()
        for rid in list(ids):
            try:
                rule = self.store._read_value(self._recurring_key(rid), None)
            except Exception:
                continue
            if not isinstance(rule, dict):
                continue
            wd = set(int(x) for x in rule.get("weekdays", []) if 0 <= int(x) <= 6)
            if not wd:
                continue
            time_str = rule.get("time", "")
            if not time_str:
                continue
            for i in range(days_ahead):
                d = today + timedelta(days=i)
                if d.weekday() not in wd:
                    continue
                ds = d.strftime("%Y-%m-%d")
                plan = self._load_plan_unlocked(ds)
                tasks = plan.get("tasks", [])
                # 防重复:同 recurring_id + 同 time + 同 title
                exists = any(
                    t.get("recurring_id") == rid
                    and t.get("time") == time_str
                    and t.get("title") == rule.get("title")
                    for t in tasks
                )
                if exists:
                    continue
                tasks.append({
                    "id": uuid.uuid4().hex[:10],
                    "time": time_str,
                    "title": rule.get("title", ""),
                    "description": rule.get("description", ""),
                    "catgirl_message": rule.get("catgirl_message", ""),
                    "category": _coerce_category(rule.get("category")),
                    "priority": _coerce_priority(rule.get("priority")),
                    "status": _STATUS_PENDING,
                    "reminder_id": None,
                    "local_fired_at": None,
                    "recurring_id": rid,
                })
                tasks.sort(key=lambda x: x.get("time", "99:99"))
                plan["tasks"] = tasks
                self._save_plan_unlocked(ds, plan)
                self._push_history_unlocked(ds)

    # ── 后台 ticker ─────────────────────────────────────────────────

    def _tick_loop(self) -> None:
        self.logger.info("planner tick thread started (tid={})", threading.get_ident())
        last_minute = ""
        while not self._stop_event.is_set():
            try:
                now = _now_in_tz(self._tz)
                minute_key = now.strftime("%Y-%m-%d %H:%M")
                if minute_key != last_minute:
                    last_minute = minute_key
                    self._handle_minute(now)
            except Exception:
                self.logger.exception("planner tick iteration error")

            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            self._wake_event.wait(timeout=60.0)
        self.logger.info("planner tick thread exiting")

    def _handle_minute(self, now: datetime) -> None:
        """每分钟巡检:早安 / 任务提醒(本地备份) / 晚安。"""
        today = now.strftime("%Y-%m-%d")
        plan = self._load_plan(today)

        # 1) 早安
        if now.time() >= self._wake_at and not plan.get("morning_announced"):
            self._announce_morning(plan, now)
            plan["morning_announced"] = True

        # 2) 任务提醒(本地备份):时间到就推,不依赖 memo_reminder
        plan = self._fire_due_tasks_local(plan, now)

        # 3) 晚安
        if now.time() >= self._goodnight_at and not plan.get("evening_recap_done"):
            self._announce_goodnight(plan, now)
            plan["evening_recap_done"] = True
            try:
                self._record_monthly_stats_unlocked(today, plan)
            except Exception:
                self.logger.exception("record monthly stats failed")

        # 持久化(只写一次)
        self._save_plan(today, plan)

    def _fire_due_tasks_local(
        self, plan: Dict[str, Any], now: datetime,
    ) -> Dict[str, Any]:
        """本地备份 ticker:到了时间就推任务提醒。

        这是 memo_reminder 失败时的兜底,确保用户到点一定收到提醒。
        v0.3: 尊重 acknowledged 和 next_reminder_at 字段
        v0.4: 修复延迟提醒逻辑 - 推送后清除 next_reminder_at
        """
        now_min = now.hour * 60 + now.minute
        now_iso = now.isoformat()
        for t in plan.get("tasks", []):
            if t.get("status") != _STATUS_PENDING:
                continue
            # v0.3: 已确认的任务不再提醒
            if t.get("acknowledged"):
                continue
            # v0.3/v0.4: 检查延迟提醒时间
            next_reminder = t.get("next_reminder_at")
            is_delayed_reminder = False
            if next_reminder:
                try:
                    next_dt = datetime.fromisoformat(next_reminder)
                    if now < next_dt:
                        continue  # 还没到延迟时间
                    # 到了延迟时间,清除该字段和 local_fired_at,让提醒能够触发
                    t["next_reminder_at"] = None
                    t["local_fired_at"] = None
                    is_delayed_reminder = True
                except Exception:
                    pass
            
            # 检查任务时间（延迟提醒时跳过此检查）
            if not is_delayed_reminder:
                tm = _parse_hhmm(t.get("time", ""))
                if tm is None:
                    continue
                task_min = tm.hour * 60 + tm.minute
                # v0.5: 支持提前提醒 - 减去 advance_minutes 作为实际触发时间
                advance = int(t.get("advance_minutes") or 0)
                trigger_min = task_min - advance
                if trigger_min < 0:
                    trigger_min = 0  # 防呆:不会跨天,当天0点触发
                if now_min < trigger_min:
                    continue
            last = t.get("local_fired_at")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if (now - last_dt).total_seconds() < _LOCAL_DEDUP_WINDOW:
                        continue
                except Exception:
                    pass
            msg = _make_task_reminder(t, self._catgirl_name, self._master_name)
            self._push(
                msg,
                priority=8,
                metadata={
                    "kind": "task_reminder",
                    "date": plan.get("date"),
                    "task_id": t.get("id"),
                },
            )
            self.logger.info(
                "local task reminder fired: date={} id={} time={} title={}",
                plan.get("date"), t.get("id"), t.get("time"), t.get("title"),
            )
            t["local_fired_at"] = now_iso
        return plan

    def _announce_morning(self, plan: Dict[str, Any], now: datetime) -> None:
        tasks = [t for t in plan.get("tasks", []) if t.get("status") != _STATUS_SKIPPED]
        first_task = None
        for t in sorted(tasks, key=lambda x: x.get("time", "99:99")):
            first_task = t
            break
        msg = _make_catgirl_greeting(
            self._catgirl_name, self._master_name, len(tasks), first_task,
        )
        self._push(
            f"🌅 早安播报 | {msg}",
            priority=7,
            metadata={"kind": "morning_brief", "date": plan.get("date")},
        )
        self.logger.info("morning brief sent for {}", plan.get("date"))

    def _announce_goodnight(self, plan: Dict[str, Any], now: datetime) -> None:
        tasks = plan.get("tasks", [])
        done = sum(1 for t in tasks if t.get("status") == _STATUS_DONE)
        pending = sum(1 for t in tasks if t.get("status") == _STATUS_PENDING)
        msg = _make_goodnight(self._catgirl_name, self._master_name, done, pending)
        self._push(
            f"🌙 晚安复盘 | {msg}",
            priority=6,
            metadata={"kind": "evening_recap", "date": plan.get("date")},
        )
        self.logger.info("evening recap sent for {}", plan.get("date"))

    def _push(self, text: str, priority: int = 5, metadata: Optional[Dict] = None) -> None:
        try:
            self.ctx.push_message(
                source="catgirl_daily_planner",
                visibility=[],
                ai_behavior=_BEHAVIOR,
                parts=[{"type": "text", "text": text}],
                priority=priority,
                metadata=metadata or {"description": f"🐱 猫娘计划 [{text[:24]}…]"},
            )
        except Exception:
            self.logger.exception("push_message failed: {}", text[:50])

    # ── entry points ────────────────────────────────────────────────

    @llm_tool(
        name="catgirl_planner_create_plan",
        description="为主人创建每日计划。可以指定日期(默认今天)和任务列表。每个任务包含时间、标题，可选描述、分类、优先级。猫娘会自动安排提醒。",
        parameters={
            "type": "object",
            "properties": {
                "date":  {"type": "string", "description": "YYYY-MM-DD / 今天/明天/后天,留空今天"},
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time":            {"type": "string", "description": "HH:MM"},
                            "title":           {"type": "string"},
                            "description":     {"type": "string"},
                            "catgirl_message": {"type": "string"},
                            "category":        {"type": "string", "enum": list(_CATEGORIES.keys())},
                            "priority":        {"type": "integer", "enum": [1, 2, 3]},
                        },
                        "required": ["time", "title"],
                    },
                },
            },
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="create_plan",
        name="创建今日计划",
        description=(
            "为指定日期创建一个新的计划(覆盖式)。"
            "tasks 中每项可包含 time/title,可选 description/catgirl_message/category/priority。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "date":  {"type": "string", "description": "YYYY-MM-DD / 今天/明天/后天,留空今天"},
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time":            {"type": "string", "description": "HH:MM"},
                            "title":           {"type": "string"},
                            "description":     {"type": "string"},
                            "catgirl_message": {"type": "string"},
                            "category":        {"type": "string", "enum": list(_CATEGORIES.keys())},
                            "priority":        {"type": "integer", "enum": [1, 2, 3]},
                        },
                        "required": ["time", "title"],
                    },
                },
            },
        },
        llm_result_fields=["date", "task_count"],
    )
    async def create_plan(self, date: str = "", tasks: Optional[List[Dict[str, Any]]] = None, **_):
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed
        if not tasks:
            return Err(SdkError("tasks 不能为空"))
        if len(tasks) > _MAX_TASKS:
            return Err(SdkError(f"任务数 {len(tasks)} 超过上限 {_MAX_TASKS}"))

        normalized: List[Dict[str, Any]] = []
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                return Err(SdkError(f"第 {i+1} 项不是对象"))
            tm = _parse_hhmm(str(t.get("time", "")))
            if tm is None:
                return Err(SdkError(f"第 {i+1} 项 time 格式错误: {t.get('time')!r}"))
            title = str(t.get("title", "")).strip()
            if not title:
                return Err(SdkError(f"第 {i+1} 项 title 不能为空"))
            normalized.append({
                "id": uuid.uuid4().hex[:10],
                "time": tm.strftime("%H:%M"),
                "title": title,
                "description": str(t.get("description", "")).strip(),
                "catgirl_message": str(t.get("catgirl_message", "")).strip(),
                "category": _coerce_category(t.get("category")),
                "priority": _coerce_priority(t.get("priority")),
                "status": _STATUS_PENDING,
                "reminder_id": None,
                "local_fired_at": None,
            })

        normalized.sort(key=lambda x: x["time"])
        plan = {
            "date": date,
            "created_at": _now_in_tz(self._tz).isoformat(),
            "tasks": normalized,
            "morning_announced": False,
            "evening_recap_done": False,
        }
        await asyncio.to_thread(self._save_plan, date, plan)

        # 为每个任务创建 memo_reminder 提醒(主通道)
        schedule_results = await self._schedule_tasks_reminders(date, normalized)

        with self._lock:
            self._push_history_unlocked(date)

        self._wake_event.set()
        return Ok({
            "date": date,
            "task_count": len(normalized),
            "scheduled": schedule_results,
            "instruction": (
                f"计划已创建喵~ 日期={date},共 {len(normalized)} 项任务。"
                "本地 ticker 也会按分钟巡检作为备份,无需担心漏提醒。"
            ),
        })

    async def _schedule_tasks_reminders(
        self,
        date_str: str,
        tasks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        scheduled_ids: List[str] = []
        failed: List[str] = []
        for t in tasks:
            msg = _make_task_reminder(t, self._catgirl_name, self._master_name)
            target_time = f"{date_str} {t['time']}"
            try:
                result = await self.ctx.plugins.call_entry(
                    "memo_reminder:add_reminder",
                    {"time": target_time, "message": msg, "repeat": "once"},
                )
                if not isinstance(result, dict):
                    failed.append(t["id"])
                    self.logger.warning(
                        "schedule reminder returned non-dict for task={}: {}",
                        t["id"], type(result).__name__,
                    )
                    continue
                rid = result.get("reminder_id")
                if rid:
                    t["reminder_id"] = rid
                    scheduled_ids.append(rid)
                else:
                    failed.append(t["id"])
                    self.logger.warning(
                        "schedule reminder missing reminder_id for task={}: {}", t["id"], result,
                    )
            except Exception as e:
                failed.append(t["id"])
                self.logger.warning("call memo_reminder failed for task={}: {}", t["id"], e)

        # 回写 reminder_id 到 plan
        plan = self._load_plan(date_str)
        for t in plan.get("tasks", []):
            for nt in tasks:
                if t.get("id") == nt.get("id") and nt.get("reminder_id"):
                    t["reminder_id"] = nt["reminder_id"]
        self._save_plan(date_str, plan)
        return {"scheduled": len(scheduled_ids), "failed": len(failed), "ids": scheduled_ids}

    @llm_tool(
        name="catgirl_planner_add_task",
        description="为主人的每日计划添加一条任务。指定时间、标题，可选描述、分类、优先级、提前提醒分钟数。猫娘会自动安排提醒。",
        parameters={
            "type": "object",
            "properties": {
                "date":            {"type": "string", "description": "YYYY-MM-DD 或 今天/明天/后天"},
                "time":            {"type": "string", "description": "HH:MM"},
                "title":           {"type": "string"},
                "description":     {"type": "string"},
                "catgirl_message": {"type": "string"},
                "category":        {"type": "string", "enum": list(_CATEGORIES.keys())},
                "priority":        {"type": "integer", "enum": [1, 2, 3]},
                "advance_minutes": {"type": "integer", "description": "提前提醒分钟数，如5表示提前5分钟提醒，0表示准时提醒", "minimum": 0, "maximum": 1440},
            },
            "required": ["time", "title"],
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="add_task",
        name="添加任务",
        description="为指定日期(默认今天)添加一条任务,会自动创建定时提醒 + 本地备份 ticker。支持提前提醒。",
        input_schema={
            "type": "object",
            "properties": {
                "date":            {"type": "string", "description": "YYYY-MM-DD 或 今天/明天/后天"},
                "time":            {"type": "string", "description": "HH:MM"},
                "title":           {"type": "string"},
                "description":     {"type": "string"},
                "catgirl_message": {"type": "string"},
                "category":        {"type": "string", "enum": list(_CATEGORIES.keys())},
                "priority":        {"type": "integer", "enum": [1, 2, 3]},
                "advance_minutes": {"type": "integer", "description": "提前提醒分钟数，如5表示提前5分钟提醒，0表示准时提醒", "minimum": 0, "maximum": 1440},
            },
            "required": ["time", "title"],
        },
        llm_result_fields=["task_id", "scheduled"],
    )
    async def add_task(
        self,
        time: str,
        title: str,
        date: str = "",
        description: str = "",
        catgirl_message: str = "",
        category: str = "",
        priority: int = PRIORITY_MEDIUM,
        advance_minutes: int = 0,
        **_,
    ):
        tm = _parse_hhmm(time)
        if tm is None:
            return Err(SdkError(f"time 格式错误: {time!r}"))

        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed

        # 计算实际提醒时间（提前提醒）
        task_time = tm.strftime("%H:%M")
        reminder_time = task_time
        reminder_date = date
        if advance_minutes > 0:
            task_dt = datetime.combine(datetime.now(self._tz).date(), tm, tzinfo=self._tz)
            reminder_dt = task_dt - timedelta(minutes=advance_minutes)
            reminder_time = reminder_dt.strftime("%H:%M")
            if reminder_dt.date() < task_dt.date():
                prev_date = datetime.strptime(date, "%Y-%m-%d").date() - timedelta(days=1)
                reminder_date = prev_date.strftime("%Y-%m-%d")

        task = {
            "id": uuid.uuid4().hex[:10],
            "time": task_time,
            "title": title.strip(),
            "description": description.strip(),
            "catgirl_message": catgirl_message.strip(),
            "category": _coerce_category(category),
            "priority": _coerce_priority(priority),
            "status": _STATUS_PENDING,
            "reminder_id": None,
            "local_fired_at": None,
            "advance_minutes": advance_minutes if advance_minutes > 0 else None,
        }

        plan = self._load_plan(date)
        if len(plan.get("tasks", [])) >= _MAX_TASKS:
            return Err(SdkError(f"该日任务数已达上限 {_MAX_TASKS}"))
        plan.setdefault("tasks", []).append(task)
        plan["tasks"].sort(key=lambda x: x.get("time", "99:99"))
        self._save_plan(date, plan)
        with self._lock:
            self._push_history_unlocked(date)

        # memo_reminder 提醒(主通道) - 使用计算后的提醒时间
        msg = _make_task_reminder(task, self._catgirl_name, self._master_name)
        try:
            res = await self.ctx.plugins.call_entry(
                "memo_reminder:add_reminder",
                {"time": f"{reminder_date} {reminder_time}", "message": msg, "repeat": "once"},
            )
            if isinstance(res, dict):
                rid = res.get("reminder_id")
                if rid:
                    plan["tasks"] = [
                        {**t, "reminder_id": rid} if t["id"] == task["id"] else t
                        for t in plan["tasks"]
                    ]
                    self._save_plan(date, plan)
                    task["reminder_id"] = rid
                else:
                    self.logger.warning("add_task reminder missing reminder_id: {}", res)
            else:
                self.logger.warning("add_task reminder returned non-dict: {}", type(res).__name__)
        except Exception as e:
            self.logger.warning("add_task reminder failed: {}", e)

        self._wake_event.set()
        return Ok({
            "task_id": task["id"],
            "date": date,
            "time": task["time"],
            "title": task["title"],
            "category": task["category"],
            "priority": task["priority"],
            "scheduled": task.get("reminder_id") is not None,
            "reminder_id": task.get("reminder_id"),
            "instruction": (
                f"任务已添加喵~ {date} {task['time']} 《{task['title']}》。"
                "若 memo_reminder 创建失败,本地 ticker 也会按时推提醒,放心喵~"
            ),
        })

    @llm_tool(
        name="catgirl_planner_set_task_meta",
        description="更新已有任务的分类、优先级、描述、猫娘提醒话术、时间或提前提醒分钟数。修改时间或提前量会自动重新注册提醒定时器。",
        parameters={
            "type": "object",
            "properties": {
                "date":            {"type": "string", "description": "YYYY-MM-DD,留空今天"},
                "task_id":         {"type": "string", "description": "任务ID"},
                "category":        {"type": "string", "enum": list(_CATEGORIES.keys())},
                "priority":        {"type": "integer", "enum": [1, 2, 3]},
                "description":     {"type": "string"},
                "catgirl_message": {"type": "string"},
                "time":            {"type": "string", "description": "HH:MM,修改任务时间"},
                "advance_minutes": {"type": "integer", "description": "提前提醒分钟数,0-1440", "minimum": 0, "maximum": 1440},
            },
            "required": ["task_id"],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="set_task_meta",
        name="更新任务属性",
        description="更新任务的分类/优先级/描述/猫娘话/时间/提前量。修改时间或提前量会重新注册提醒。",
        input_schema={
            "type": "object",
            "properties": {
                "date":            {"type": "string"},
                "task_id":         {"type": "string"},
                "category":        {"type": "string", "enum": list(_CATEGORIES.keys())},
                "priority":        {"type": "integer", "enum": [1, 2, 3]},
                "description":     {"type": "string"},
                "catgirl_message": {"type": "string"},
                "time":            {"type": "string", "description": "HH:MM"},
                "advance_minutes": {"type": "integer", "description": "提前提醒分钟数", "minimum": 0, "maximum": 1440},
            },
            "required": ["task_id"],
        },
        llm_result_fields=["updated"],
    )
    async def set_task_meta(
        self,
        task_id: str,
        date: str = "",
        category: Optional[str] = None,
        priority: Optional[int] = None,
        description: Optional[str] = None,
        catgirl_message: Optional[str] = None,
        time: Optional[str] = None,
        advance_minutes: Optional[int] = None,
        **_,
    ):
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed
        plan = self._load_plan(date)
        target = None
        for t in plan.get("tasks", []):
            if t.get("id") == task_id:
                target = t
                break
        if target is None:
            return Err(SdkError(f"未找到任务: {task_id}"))
        
        # 标记是否需要重新注册提醒
        need_reschedule = False
        
        if category is not None:
            target["category"] = _coerce_category(category)
        if priority is not None:
            target["priority"] = _coerce_priority(priority)
        if description is not None:
            target["description"] = str(description).strip()
        if catgirl_message is not None:
            target["catgirl_message"] = str(catgirl_message).strip()
        
        # 处理时间修改
        if time is not None:
            tm = _parse_hhmm(time)
            if tm is None:
                return Err(SdkError(f"time 格式错误: {time!r}"))
            target["time"] = tm.strftime("%H:%M")
            need_reschedule = True
        
        # 处理提前提醒修改
        if advance_minutes is not None:
            target["advance_minutes"] = max(0, min(1440, int(advance_minutes)))
            need_reschedule = True
        
        # 如果需要重新注册提醒
        if need_reschedule:
            # 清除旧的提醒记录,让本地 ticker 重新触发
            target["local_fired_at"] = None
            target["next_reminder_at"] = None
            
            # 重新注册 memo_reminder
            old_rid = target.get("reminder_id")
            if old_rid:
                try:
                    await self.ctx.plugins.call_entry(
                        "memo_reminder:remove_reminder",
                        {"reminder_id": old_rid},
                    )
                except Exception as e:
                    self.logger.warning("remove old reminder failed: {}", e)
            
            # 计算新的提醒时间
            task_time = target["time"]
            advance = int(target.get("advance_minutes") or 0)
            reminder_time = task_time
            reminder_date = date
            if advance > 0:
                tm = _parse_hhmm(task_time)
                task_dt = datetime.combine(datetime.now(self._tz).date(), tm, tzinfo=self._tz)
                reminder_dt = task_dt - timedelta(minutes=advance)
                reminder_time = reminder_dt.strftime("%H:%M")
                if reminder_dt.date() < task_dt.date():
                    prev_date = datetime.strptime(date, "%Y-%m-%d").date() - timedelta(days=1)
                    reminder_date = prev_date.strftime("%Y-%m-%d")
            
            # 注册新提醒
            msg = _make_task_reminder(target, self._catgirl_name, self._master_name)
            try:
                res = await self.ctx.plugins.call_entry(
                    "memo_reminder:add_reminder",
                    {"time": f"{reminder_date} {reminder_time}", "message": msg, "repeat": "once"},
                )
                if isinstance(res, dict):
                    rid = res.get("reminder_id")
                    if rid:
                        target["reminder_id"] = rid
                    else:
                        self.logger.warning("reschedule reminder missing reminder_id: {}", res)
                else:
                    self.logger.warning("reschedule reminder returned non-dict: {}", type(res).__name__)
            except Exception as e:
                self.logger.warning("reschedule reminder failed: {}", e)
        
        self._save_plan(date, plan)
        return Ok({"updated": True, "task": target})

    @llm_tool(
        name="catgirl_planner_get_current_time",
        description="获取当前系统实际时间。当需要计算相对时间（如'半小时后'、'10分钟后'）时调用此工具，确保任务时间准确。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=5.0,
    )
    @plugin_entry(
        id="get_current_time",
        name="获取当前时间",
        description="获取当前系统实际时间,用于计算相对时间。",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["datetime", "date", "time", "weekday"],
    )
    async def get_current_time(self, **_):
        """获取当前系统实际时间。"""
        now = datetime.now(self._tz)
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[now.weekday()]
        
        return Ok({
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": weekday,
            "timestamp": int(now.timestamp()),
            "timezone": str(self._tz),
        })

    @llm_tool(
        name="catgirl_planner_mark_done",
        description="标记任务为完成、跳过或恢复为待办状态。",
        parameters={
            "type": "object",
            "properties": {
                "date":    {"type": "string", "description": "YYYY-MM-DD,留空今天"},
                "task_id": {"type": "string", "description": "任务ID"},
                "status":  {"type": "string", "enum": ["done", "skipped", "pending"], "description": "done=完成,skipped=跳过,pending=恢复待办"},
            },
            "required": ["task_id", "status"],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="mark_done",
        name="标记任务完成",
        description="把任务标记为 done / skipped / pending(恢复)。",
        input_schema={
            "type": "object",
            "properties": {
                "date":    {"type": "string"},
                "task_id": {"type": "string"},
                "status":  {"type": "string", "enum": ["done", "skipped", "pending"]},
            },
            "required": ["task_id", "status"],
        },
        llm_result_fields=["updated"],
    )
    async def mark_done(self, task_id: str, status: str, date: str = "", **_):
        if status not in (_STATUS_DONE, _STATUS_SKIPPED, _STATUS_PENDING):
            return Err(SdkError(f"status 必须是 done/skipped/pending 之一,收到 {status!r}"))
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed

        plan = self._load_plan(date)
        target = None
        for t in plan.get("tasks", []):
            if t.get("id") == task_id:
                t["status"] = status
                target = t
                break
        if target is None:
            return Err(SdkError(f"未找到任务: {task_id}"))

        if status == _STATUS_DONE and target.get("reminder_id"):
            try:
                await self.ctx.plugins.call_entry(
                    "memo_reminder:delete_reminder",
                    {"reminder_id": target["reminder_id"]},
                )
                target["reminder_id"] = None
            except Exception as e:
                self.logger.warning("delete reminder on mark_done failed: {}", e)

        self._save_plan(date, plan)
        try:
            self._record_monthly_stats_unlocked(date, plan)
        except Exception:
            self.logger.exception("record stats failed")
        return Ok({"updated": True, "task_id": task_id, "status": status, "title": target.get("title")})

    @llm_tool(
        name="catgirl_planner_acknowledge_task",
        description="确认任务提醒。当主人说'我知道了'、'收到'、'明白了'等确认话语时调用此工具,停止该任务的重复提醒。",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD,留空今天"},
                "task_id": {"type": "string", "description": "任务ID"},
            },
            "required": ["task_id"],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="acknowledge_task",
        name="确认任务提醒",
        description="确认任务提醒,停止该任务的重复提醒。",
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        llm_result_fields=["acknowledged"],
    )
    async def acknowledge_task(self, task_id: str, date: str = "", **_):
        """确认任务提醒,停止重复提醒。"""
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed

        plan = self._load_plan(date)
        target = None
        for t in plan.get("tasks", []):
            if t.get("id") == task_id:
                t["acknowledged"] = True
                t["next_reminder_at"] = None  # 清除延迟提醒时间
                target = t
                break
        if target is None:
            return Err(SdkError(f"未找到任务: {task_id}"))

        self._save_plan(date, plan)
        return Ok({
            "acknowledged": True,
            "task_id": task_id,
            "title": target.get("title"),
            "message": f"好的喵~ {target.get('title')} 的提醒已关闭,{self._master_name}加油哦~"
        })

    @llm_tool(
        name="catgirl_planner_delay_task_reminder",
        description="延迟任务提醒。当主人说'半小时后再提醒我'、'10分钟后提醒'等延迟话语时调用此工具,设置下次提醒时间。",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD,留空今天"},
                "task_id": {"type": "string", "description": "任务ID"},
                "delay_minutes": {"type": "integer", "description": "延迟分钟数,如10、30、60", "minimum": 1, "maximum": 1440},
            },
            "required": ["task_id", "delay_minutes"],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="delay_task_reminder",
        name="延迟任务提醒",
        description="延迟任务提醒,设置下次提醒时间。",
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "task_id": {"type": "string"},
                "delay_minutes": {"type": "integer"},
            },
            "required": ["task_id", "delay_minutes"],
        },
        llm_result_fields=["next_reminder_at"],
    )
    async def delay_task_reminder(self, task_id: str, delay_minutes: int, date: str = "", **_):
        """延迟任务提醒,设置下次提醒时间。"""
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed

        plan = self._load_plan(date)
        target = None
        for t in plan.get("tasks", []):
            if t.get("id") == task_id:
                # 计算下次提醒时间
                now = datetime.now(self._tz)
                next_time = now + timedelta(minutes=delay_minutes)
                t["next_reminder_at"] = next_time.isoformat()
                t["acknowledged"] = False  # 重置确认状态,允许再次提醒
                target = t
                break
        if target is None:
            return Err(SdkError(f"未找到任务: {task_id}"))

        self._save_plan(date, plan)
        return Ok({
            "delayed": True,
            "task_id": task_id,
            "title": target.get("title"),
            "delay_minutes": delay_minutes,
            "next_reminder_at": target.get("next_reminder_at"),
            "message": f"好的喵~ {delay_minutes}分钟后({target.get('next_reminder_at')[:16].replace('T', ' ')})再提醒{self._master_name}《{target.get('title')}》~"
        })

    @llm_tool(
        name="catgirl_planner_list_today",
        description="查看今日(或指定日期)的完整计划,包括所有任务及其状态。",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD 或 今天/明天/后天,留空今天"},
            },
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="list_today",
        name="查看今日计划",
        description="返回今日(或指定日期)的完整计划。",
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string"}},
        },
        llm_result_fields=["task_count"],
    )
    async def list_today(self, date: str = "", **_):
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed
        plan = self._load_plan(date)
        tasks = plan.get("tasks", [])
        done = sum(1 for t in tasks if t.get("status") == _STATUS_DONE)
        pending = sum(1 for t in tasks if t.get("status") == _STATUS_PENDING)
        skipped = sum(1 for t in tasks if t.get("status") == _STATUS_SKIPPED)
        return Ok({
            "date": date,
            "task_count": len(tasks),
            "done_count": done,
            "pending_count": pending,
            "skipped_count": skipped,
            "morning_announced": plan.get("morning_announced", False),
            "evening_recap_done": plan.get("evening_recap_done", False),
            "tasks": tasks,
        })

    @llm_tool(
        name="catgirl_planner_delete_task",
        description="从计划中删除指定任务,会同时取消对应的定时提醒。",
        parameters={
            "type": "object",
            "properties": {
                "date":    {"type": "string", "description": "YYYY-MM-DD,留空今天"},
                "task_id": {"type": "string", "description": "要删除的任务ID"},
            },
            "required": ["task_id"],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="delete_task",
        name="删除任务",
        description="从计划中删除某条任务,会同时取消对应的定时提醒。",
        input_schema={
            "type": "object",
            "properties": {
                "date":    {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        llm_result_fields=["deleted"],
    )
    async def delete_task(self, task_id: str, date: str = "", **_):
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed
        plan = self._load_plan(date)
        before = len(plan.get("tasks", []))
        target = None
        new_tasks = []
        for t in plan.get("tasks", []):
            if t.get("id") == task_id:
                target = t
            else:
                new_tasks.append(t)
        plan["tasks"] = new_tasks
        if len(plan["tasks"]) == before:
            return Err(SdkError(f"未找到任务: {task_id}"))

        if target and target.get("reminder_id"):
            try:
                await self.ctx.plugins.call_entry(
                    "memo_reminder:delete_reminder",
                    {"reminder_id": target["reminder_id"]},
                )
            except Exception as e:
                self.logger.warning("delete reminder on delete_task failed: {}", e)
        self._save_plan(date, plan)
        return Ok({"deleted": task_id, "date": date})

    @llm_tool(
        name="catgirl_planner_clear_plan",
        description="清空指定日期的所有任务及其提醒。",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD,留空今天"},
            },
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="clear_plan",
        name="清空计划",
        description="清空某天的所有任务及其提醒。",
        input_schema={"type": "object", "properties": {"date": {"type": "string"}}},
        llm_result_fields=["cleared"],
    )
    async def clear_plan(self, date: str = "", **_):
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed
        plan = self._load_plan(date)
        tasks = plan.get("tasks", [])
        for t in tasks:
            if t.get("reminder_id"):
                try:
                    await self.ctx.plugins.call_entry(
                        "memo_reminder:delete_reminder",
                        {"reminder_id": t["reminder_id"]},
                    )
                except Exception as e:
                    self.logger.warning("delete reminder on clear failed: {}", e)
        cleared = len(tasks)
        self._save_plan(date, {
            "date": date,
            "created_at": None,
            "tasks": [],
            "morning_announced": False,
            "evening_recap_done": False,
        })
        return Ok({"cleared": cleared, "date": date})

    @llm_tool(
        name="catgirl_planner_set_schedule",
        description="运行时调整早安/晚安播报时间、猫娘昵称、主人称呼,立即生效。",
        parameters={
            "type": "object",
            "properties": {
                "wake_hour":        {"type": "integer", "minimum": 0, "maximum": 23},
                "wake_minute":      {"type": "integer", "minimum": 0, "maximum": 59},
                "goodnight_hour":   {"type": "integer", "minimum": 0, "maximum": 23},
                "goodnight_minute": {"type": "integer", "minimum": 0, "maximum": 59},
                "catgirl_nickname": {"type": "string"},
                "master_nickname":  {"type": "string"},
                "reset_today_flags": {"type": "boolean", "default": False},
            },
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="set_schedule",
        name="修改早晚播报时间",
        description=(
            "运行时调整早安/晚安时间、猫娘昵称、主人称呼。"
            "会立即生效,后续自动播报按新时间触发。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "wake_hour":        {"type": "integer", "minimum": 0, "maximum": 23},
                "wake_minute":      {"type": "integer", "minimum": 0, "maximum": 59},
                "goodnight_hour":   {"type": "integer", "minimum": 0, "maximum": 23},
                "goodnight_minute": {"type": "integer", "minimum": 0, "maximum": 59},
                "catgirl_nickname": {"type": "string"},
                "master_nickname":  {"type": "string"},
                "reset_today_flags": {
                    "type": "boolean",
                    "description": "是否重置今日早安/晚安标志位(改了时间想立刻再播一次就置 true)",
                    "default": False,
                },
            },
        },
        llm_result_fields=["wake_at", "goodnight_at"],
    )
    async def set_schedule(
        self,
        wake_hour: Optional[int] = None,
        wake_minute: Optional[int] = None,
        goodnight_hour: Optional[int] = None,
        goodnight_minute: Optional[int] = None,
        catgirl_nickname: Optional[str] = None,
        master_nickname: Optional[str] = None,
        reset_today_flags: bool = False,
        **_,
    ):
        updated_fields: List[str] = []
        if wake_hour is not None:
            wh = max(0, min(23, int(wake_hour)))
            self._wake_at = dtime(wh, self._wake_at.minute)
            updated_fields.append("wake_hour")
        if wake_minute is not None:
            wm = max(0, min(59, int(wake_minute)))
            self._wake_at = dtime(self._wake_at.hour, wm)
            updated_fields.append("wake_minute")
        if goodnight_hour is not None:
            gh = max(0, min(23, int(goodnight_hour)))
            self._goodnight_at = dtime(gh, self._goodnight_at.minute)
            updated_fields.append("goodnight_hour")
        if goodnight_minute is not None:
            gm = max(0, min(59, int(goodnight_minute)))
            self._goodnight_at = dtime(self._goodnight_at.hour, gm)
            updated_fields.append("goodnight_minute")
        if catgirl_nickname is not None and str(catgirl_nickname).strip():
            self._catgirl_name = str(catgirl_nickname).strip()
            updated_fields.append("catgirl_nickname")
        if master_nickname is not None and str(master_nickname).strip():
            self._master_name = str(master_nickname).strip()
            updated_fields.append("master_nickname")

        try:
            await self.config.update(
                {
                    "planner.wake_hour": self._wake_at.hour,
                    "planner.wake_minute": self._wake_at.minute,
                    "planner.goodnight_hour": self._goodnight_at.hour,
                    "planner.goodnight_minute": self._goodnight_at.minute,
                    "planner.catgirl_nickname": self._catgirl_name,
                    "planner.master_nickname": self._master_name,
                },
                timeout=3.0,
            )
        except Exception as e:
            self.logger.warning("set_schedule: persist to config failed: {}", e)

        if reset_today_flags:
            try:
                today = _today_key(self._tz)
                plan = self._load_plan(today)
                plan["morning_announced"] = False
                plan["evening_recap_done"] = False
                self._save_plan(today, plan)
                updated_fields.append("reset_today_flags")
            except Exception as e:
                self.logger.warning("set_schedule: reset flags failed: {}", e)

        self._wake_event.set()
        return Ok({
            "updated_fields": updated_fields,
            "wake_at": self._wake_at.strftime("%H:%M"),
            "goodnight_at": self._goodnight_at.strftime("%H:%M"),
            "catgirl_nickname": self._catgirl_name,
            "master_nickname": self._master_name,
        })

    @llm_tool(
        name="catgirl_planner_get_schedule",
        description="查看当前生效的早安/晚安播报时间、猫娘昵称、主人称呼等配置。",
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    @plugin_entry(
        id="get_schedule",
        name="查看早晚播报时间",
        description="读取当前生效的早安/晚安时间、昵称等配置。",
    )
    async def get_schedule(self, **_):
        return Ok({
            "timezone": str(self._tz),
            "wake_at": self._wake_at.strftime("%H:%M"),
            "goodnight_at": self._goodnight_at.strftime("%H:%M"),
            "catgirl_nickname": self._catgirl_name,
            "master_nickname": self._master_name,
        })

    @llm_tool(
        name="catgirl_planner_trigger_morning",
        description="立即触发一次早安播报,猫娘会向主人汇报今日计划安排。",
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    @plugin_entry(
        id="trigger_morning",
        name="立即早安播报",
        description="手动触发一次早安播报(测试或补漏用)。",
    )
    async def trigger_morning(self, **_):
        now = _now_in_tz(self._tz)
        today = now.strftime("%Y-%m-%d")
        plan = self._load_plan(today)
        self._announce_morning(plan, now)
        plan["morning_announced"] = True
        self._save_plan(today, plan)
        return Ok({"fired": True, "date": today})

    @llm_tool(
        name="catgirl_planner_trigger_goodnight",
        description="立即触发一次晚安复盘,猫娘会总结主人今日完成情况。",
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    @plugin_entry(
        id="trigger_goodnight",
        name="立即晚安复盘",
        description="手动触发一次晚安复盘(测试或补漏用)。",
    )
    async def trigger_goodnight(self, **_):
        now = _now_in_tz(self._tz)
        today = now.strftime("%Y-%m-%d")
        plan = self._load_plan(today)
        self._announce_goodnight(plan, now)
        plan["evening_recap_done"] = True
        self._save_plan(today, plan)
        try:
            self._record_monthly_stats_unlocked(today, plan)
        except Exception:
            self.logger.exception("record stats failed")
        return Ok({"fired": True, "date": today})

    # ── v0.2 新增入口 ───────────────────────────────────────────────

    @llm_tool(
        name="catgirl_planner_list_calendar",
        description="查看指定日期范围内的计划统计,可查看某周或某月的任务安排情况。",
        parameters={
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "YYYY-MM-DD,默认今天"},
                "end":   {"type": "string", "description": "YYYY-MM-DD,默认今天"},
                "include_tasks": {"type": "boolean", "default": False, "description": "是否返回详细任务列表"},
            },
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="list_calendar",
        name="查看日期范围计划",
        description="列出从 start 到 end(含)的所有日期的任务统计与明细,供前端日历视图用。",
        input_schema={
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "YYYY-MM-DD,默认今天"},
                "end":   {"type": "string", "description": "YYYY-MM-DD,默认今天"},
                "include_tasks": {"type": "boolean", "default": False, "description": "是否返回详细任务"},
            },
        },
        llm_result_fields=["days"],
    )
    async def list_calendar(
        self,
        start: str = "",
        end: str = "",
        include_tasks: bool = False,
        **_,
    ):
        if not start:
            start = _today_key(self._tz)
        else:
            p = _parse_date(start, self._tz)
            if not p:
                return Err(SdkError(f"start 无法解析: {start!r}"))
            start = p
        if not end:
            end = start
        else:
            p = _parse_date(end, self._tz)
            if not p:
                return Err(SdkError(f"end 无法解析: {end!r}"))
            end = p
        try:
            sd = datetime.strptime(start, "%Y-%m-%d").date()
            ed = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError as e:
            return Err(SdkError(f"日期解析失败: {e}"))
        if ed < sd:
            return Err(SdkError("end 早于 start"))

        days: List[Dict[str, Any]] = []
        d = sd
        while d <= ed:
            ds = d.strftime("%Y-%m-%d")
            plan = self._load_plan(ds)
            tasks = plan.get("tasks", [])
            total = len(tasks)
            done = sum(1 for t in tasks if t.get("status") == _STATUS_DONE)
            entry: Dict[str, Any] = {
                "date": ds,
                "weekday": d.weekday(),
                "task_count": total,
                "done_count": done,
                "pending_count": sum(1 for t in tasks if t.get("status") == _STATUS_PENDING),
                "skipped_count": sum(1 for t in tasks if t.get("status") == _STATUS_SKIPPED),
                "morning_announced": plan.get("morning_announced", False),
                "evening_recap_done": plan.get("evening_recap_done", False),
            }
            # 提取分类统计(轻量,供日历热力图用)
            cats = {}
            for t in tasks:
                c = t.get("category", DEFAULT_CATEGORY)
                cats[c] = cats.get(c, 0) + 1
            entry["by_category"] = cats
            if include_tasks:
                entry["tasks"] = tasks
            days.append(entry)
            d += timedelta(days=1)
            if len(days) > 366:
                return Err(SdkError("日期范围过大,最多 366 天"))

        return Ok({"start": start, "end": end, "days": days})

    @llm_tool(
        name="catgirl_planner_get_year_overview",
        description="查看指定年份的年历概览,包括每月任务统计和连续打卡天数。",
        parameters={
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "年份,默认今年"},
            },
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="get_year_overview",
        name="年历概览",
        description="返回指定年份每个月的任务量/完成量,前端用来画年历。",
        input_schema={
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "默认今年"},
            },
        },
        llm_result_fields=["year", "months"],
    )
    async def get_year_overview(self, year: Optional[int] = None, **_):
        today = _now_in_tz(self._tz).date()
        if year is None:
            year = today.year
        try:
            year = int(year)
        except (TypeError, ValueError):
            return Err(SdkError(f"year 不合法: {year!r}"))
        if year < 1970 or year > 2999:
            return Err(SdkError(f"year 超出范围: {year}"))

        months: List[Dict[str, Any]] = []
        grand_total = 0
        grand_done = 0
        for m in range(1, 13):
            ym = f"{year:04d}-{m:02d}"
            stats = self.store._read_value(self._stats_key(ym), None) or {}
            if not isinstance(stats, dict):
                stats = {}

            # 自动检测旧数据并修复：如果缺少 _recorded_dates 字段，说明是旧版累加数据
            if stats and "_recorded_dates" not in stats and stats.get("total", 0) > 0:
                self.logger.info("年历检测到旧版统计数据，自动重建: {}", ym)
                try:
                    real_total, real_done, real_skipped, real_by_cat = 0, 0, 0, {}
                    days_in_month = 31
                    if m in (4, 6, 9, 11):
                        days_in_month = 30
                    elif m == 2:
                        days_in_month = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
                    for day in range(1, days_in_month + 1):
                        ds = f"{year:04d}-{m:02d}-{day:02d}"
                        plan = self._load_plan(ds)
                        tasks = plan.get("tasks", [])
                        day_total = len(tasks)
                        day_done = sum(1 for t in tasks if t.get("status") == _STATUS_DONE)
                        day_skipped = sum(1 for t in tasks if t.get("status") == _STATUS_SKIPPED)
                        real_total += day_total
                        real_done += day_done
                        real_skipped += day_skipped
                        for t in tasks:
                            cat = (t.get("category") or "").strip() or "未分类"
                            real_by_cat[cat] = real_by_cat.get(cat, 0) + 1
                    stats["total"] = real_total
                    stats["done"] = real_done
                    stats["skipped"] = real_skipped
                    stats["by_category"] = real_by_cat
                    stats["_recorded_dates"] = {}
                    for day in range(1, days_in_month + 1):
                        ds = f"{year:04d}-{m:02d}-{day:02d}"
                        plan = self._load_plan(ds)
                        tasks = plan.get("tasks", [])
                        if tasks:
                            dt = len(tasks)
                            dd = sum(1 for t in tasks if t.get("status") == _STATUS_DONE)
                            dsk = sum(1 for t in tasks if t.get("status") == _STATUS_SKIPPED)
                            dbc = {}
                            for t in tasks:
                                cat = (t.get("category") or "").strip() or "未分类"
                                dbc[cat] = dbc.get(cat, 0) + 1
                            stats["_recorded_dates"][ds] = {"total": dt, "done": dd, "skipped": dsk, "by_category": dbc}
                    self.store._write_value(self._stats_key(ym), stats)
                    self.logger.info("年历自动重建完成: {} total={}, done={}", ym, real_total, real_done)
                except Exception as e:
                    self.logger.error("年历自动重建统计失败: {} - {}", ym, e)

            total = int(stats.get("total", 0))
            done = int(stats.get("done", 0))
            skipped = int(stats.get("skipped", 0))
            grand_total += total
            grand_done += done
            months.append({
                "month": m,
                "total": total,
                "done": done,
                "skipped": skipped,
                "by_category": stats.get("by_category", {}) or {},
                "by_day": stats.get("by_day", {}) or {},
            })
        # 计算连续打卡
        history = self._list_history_unlocked()
        hist_summary: List[Dict[str, Any]] = []
        for ds in history[-90:]:
            try:
                plan = self._load_plan(ds)
            except Exception:
                continue
            done = sum(1 for t in plan.get("tasks", []) if t.get("status") == _STATUS_DONE)
            hist_summary.append({"date": ds, "done": done})
        streak = _calc_streak(hist_summary)
        return Ok({
            "year": year,
            "months": months,
            "grand_total": grand_total,
            "grand_done": grand_done,
            "current_streak": streak,
            "history_summary": hist_summary,
        })

    @llm_tool(
        name="catgirl_planner_get_stats",
        description="查看任务完成统计,包括月度完成率、最近N天每日汇总、连续打卡天数。",
        parameters={
            "type": "object",
            "properties": {
                "month":     {"type": "string", "description": "YYYY-MM,默认本月"},
                "recent_days": {"type": "integer", "description": "返回最近N天每日汇总,默认14天"},
            },
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="get_stats",
        name="统计与打卡",
        description="返回月度/最近 N 天的完成统计,以及当前连续打卡天数。",
        input_schema={
            "type": "object",
            "properties": {
                "month":     {"type": "string", "description": "YYYY-MM,默认本月"},
                "recent_days": {"type": "integer", "description": "返回最近 N 天每日汇总,默认 14"},
            },
        },
        llm_result_fields=["total", "done", "current_streak"],
    )
    async def get_stats(self, month: str = "", recent_days: int = 14, **_):
        if not month:
            month = _now_in_tz(self._tz).strftime("%Y-%m")
        recent_days = max(1, min(int(recent_days or 14), 90))
        stats = self.store._read_value(self._stats_key(month), None) or {}
        if not isinstance(stats, dict):
            stats = {}

        # 自动检测旧数据并修复：如果缺少 _recorded_dates 字段，说明是旧版累加数据
        # 需要从实际计划数据重建，避免显示错误的统计值
        if stats and "_recorded_dates" not in stats and stats.get("total", 0) > 0:
            self.logger.info("检测到旧版统计数据，自动重建: {}", month)
            try:
                real_total, real_done, real_skipped, real_by_cat = 0, 0, 0, {}
                year, mon = map(int, month.split("-"))
                days_in_month = 31
                if mon in (4, 6, 9, 11):
                    days_in_month = 30
                elif mon == 2:
                    days_in_month = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
                for day in range(1, days_in_month + 1):
                    ds = f"{year:04d}-{mon:02d}-{day:02d}"
                    plan = self._load_plan(ds)
                    tasks = plan.get("tasks", [])
                    day_total = len(tasks)
                    day_done = sum(1 for t in tasks if t.get("status") == _STATUS_DONE)
                    day_skipped = sum(1 for t in tasks if t.get("status") == _STATUS_SKIPPED)
                    real_total += day_total
                    real_done += day_done
                    real_skipped += day_skipped
                    for t in tasks:
                        cat = (t.get("category") or "").strip() or "未分类"
                        real_by_cat[cat] = real_by_cat.get(cat, 0) + 1
                # 重建统计数据
                stats["total"] = real_total
                stats["done"] = real_done
                stats["skipped"] = real_skipped
                stats["by_category"] = real_by_cat
                stats["_recorded_dates"] = {}
                # 为每天保存快照
                for day in range(1, days_in_month + 1):
                    ds = f"{year:04d}-{mon:02d}-{day:02d}"
                    plan = self._load_plan(ds)
                    tasks = plan.get("tasks", [])
                    if tasks:
                        dt = len(tasks)
                        dd = sum(1 for t in tasks if t.get("status") == _STATUS_DONE)
                        dsk = sum(1 for t in tasks if t.get("status") == _STATUS_SKIPPED)
                        dbc = {}
                        for t in tasks:
                            cat = (t.get("category") or "").strip() or "未分类"
                            dbc[cat] = dbc.get(cat, 0) + 1
                        stats["_recorded_dates"][ds] = {"total": dt, "done": dd, "skipped": dsk, "by_category": dbc}
                self.store._write_value(self._stats_key(month), stats)
                self.logger.info("自动重建完成: total={}, done={}", real_total, real_done)
            except Exception as e:
                self.logger.error("自动重建统计失败: {}", e)

        # 最近 N 天每日汇总
        today = _now_in_tz(self._tz).date()
        by_day: List[Dict[str, Any]] = []
        for i in range(recent_days - 1, -1, -1):
            d = today - timedelta(days=i)
            ds = d.strftime("%Y-%m-%d")
            plan = self._load_plan(ds)
            tasks = plan.get("tasks", [])
            total = len(tasks)
            done = sum(1 for t in tasks if t.get("status") == _STATUS_DONE)
            pending = sum(1 for t in tasks if t.get("status") == _STATUS_PENDING)
            skipped = sum(1 for t in tasks if t.get("status") == _STATUS_SKIPPED)
            by_day.append({
                "date": ds,
                "weekday": d.weekday(),
                "total": total,
                "done": done,
                "pending": pending,
                "skipped": skipped,
            })

        # 连续打卡
        streak = _calc_streak([
            {"date": b["date"], "done": b["done"]}
            for b in by_day
        ])

        return Ok({
            "month": month,
            "total": int(stats.get("total", 0)),
            "done": int(stats.get("done", 0)),
            "skipped": int(stats.get("skipped", 0)),
            "completion_rate": (
                round(int(stats.get("done", 0)) * 100 / max(1, int(stats.get("total", 0))), 1)
            ),
            "by_category": stats.get("by_category", {}) or {},
            "by_day_month": stats.get("by_day", {}) or {},
            "recent_days": by_day,
            "current_streak": streak,
        })

    @llm_tool(
        name="catgirl_planner_list_templates",
        description="查看所有内置的每日计划模板(工作日/休息日/学习日/健身日),可快速为主人铺满一日计划。",
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    @plugin_entry(
        id="list_templates",
        name="查看内置模板",
        description="返回所有内置一日模板(工作日/休息日/学习日/健身日),供快速铺满一日。",
    )
    async def list_templates(self, **_):
        out = []
        for t in _TEMPLATES.values():
            out.append({
                "id": t["id"],
                "name": t["name"],
                "icon": t["icon"],
                "desc": t["desc"],
                "task_count": len(t["tasks"]),
            })
        return Ok({"templates": out})

    @llm_tool(
        name="catgirl_planner_apply_template",
        description="将内置模板(工作日/休息日/学习日/健身日)应用到指定日期,快速铺满一日计划。可选择覆盖或追加。",
        parameters={
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "enum": list(_TEMPLATES.keys()), "description": "模板ID: workday/weekend/study/fitness"},
                "date":        {"type": "string", "description": "YYYY-MM-DD / 今天/明天/后天,默认今天"},
                "mode":        {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite", "description": "overwrite=覆盖,append=追加"},
            },
            "required": ["template_id"],
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="apply_template",
        name="应用模板",
        description="把指定模板的整套任务铺到某一天(可选择覆盖还是追加)。",
        input_schema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "enum": list(_TEMPLATES.keys())},
                "date":        {"type": "string", "description": "YYYY-MM-DD / 今天/明天/后天,默认今天"},
                "mode":        {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite"},
            },
            "required": ["template_id"],
        },
        llm_result_fields=["date", "task_count"],
    )
    async def apply_template(self, template_id: str, date: str = "", mode: str = "overwrite", **_):
        if template_id not in _TEMPLATES:
            return Err(SdkError(f"未知模板: {template_id}"))
        if not date:
            date = _today_key(self._tz)
        else:
            parsed = _parse_date(date, self._tz)
            if not parsed:
                return Err(SdkError(f"date 无法解析: {date!r}"))
            date = parsed

        tmpl = _TEMPLATES[template_id]
        tpl_tasks = []
        for raw in tmpl["tasks"]:
            tm = _parse_hhmm(raw["time"])
            if tm is None:
                continue
            tpl_tasks.append({
                "id": uuid.uuid4().hex[:10],
                "time": tm.strftime("%H:%M"),
                "title": raw["title"],
                "description": "",
                "catgirl_message": "",
                "category": _coerce_category(raw.get("category")),
                "priority": _coerce_priority(raw.get("priority")),
                "status": _STATUS_PENDING,
                "reminder_id": None,
                "local_fired_at": None,
                "template_id": template_id,
            })
        tpl_tasks.sort(key=lambda x: x["time"])

        if mode == "append":
            plan = self._load_plan(date)
            existing = plan.get("tasks", [])
            # 防止冲突:同时间已有任务则跳过
            existing_times = {t.get("time") for t in existing}
            for t in tpl_tasks:
                if t["time"] not in existing_times:
                    existing.append(t)
                    existing_times.add(t["time"])
            existing.sort(key=lambda x: x.get("time", "99:99"))
            plan["tasks"] = existing
        else:
            plan = {
                "date": date,
                "created_at": _now_in_tz(self._tz).isoformat(),
                "tasks": tpl_tasks,
                "morning_announced": False,
                "evening_recap_done": False,
            }

        self._save_plan(date, plan)
        with self._lock:
            self._push_history_unlocked(date)

        await self._schedule_tasks_reminders(date, plan.get("tasks", []))
        self._wake_event.set()
        return Ok({
            "date": date,
            "template_id": template_id,
            "mode": mode,
            "task_count": len(plan.get("tasks", [])),
        })

    @llm_tool(
        name="catgirl_planner_add_recurring",
        description="添加按星期重复的任务规则,猫娘会自动在指定星期几铺任务到计划里。例如每周一三五早上8点学习。",
        parameters={
            "type": "object",
            "properties": {
                "weekdays": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 6},
                    "description": "重复执行的星期(0=周一,1=周二,...,6=周日)",
                },
                "time":            {"type": "string", "description": "HH:MM"},
                "title":           {"type": "string"},
                "description":     {"type": "string"},
                "catgirl_message": {"type": "string"},
                "category":        {"type": "string", "enum": list(_CATEGORIES.keys())},
                "priority":        {"type": "integer", "enum": [1, 2, 3]},
            },
            "required": ["weekdays", "time", "title"],
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="add_recurring",
        name="添加重复任务",
        description=(
            "添加一个按星期重复的任务规则,每天 ticker 会自动把它铺到当日计划里。"
            "weekdays 列表,0=周一 ... 6=周日。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "weekdays": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 6},
                    "description": "重复执行的星期(0=周一...6=周日)",
                },
                "time":            {"type": "string", "description": "HH:MM"},
                "title":           {"type": "string"},
                "description":     {"type": "string"},
                "catgirl_message": {"type": "string"},
                "category":        {"type": "string", "enum": list(_CATEGORIES.keys())},
                "priority":        {"type": "integer", "enum": [1, 2, 3]},
            },
            "required": ["weekdays", "time", "title"],
        },
        llm_result_fields=["recurring_id"],
    )
    async def add_recurring(
        self,
        weekdays: List[int],
        time: str,
        title: str,
        description: str = "",
        catgirl_message: str = "",
        category: str = "",
        priority: int = PRIORITY_MEDIUM,
        **_,
    ):
        tm = _parse_hhmm(time)
        if tm is None:
            return Err(SdkError(f"time 格式错误: {time!r}"))
        wd = sorted({int(x) for x in (weekdays or []) if 0 <= int(x) <= 6})
        if not wd:
            return Err(SdkError("weekdays 不能为空,例如 [0,1,2,3,4] 表示工作日"))
        rid = uuid.uuid4().hex[:10]
        rule = {
            "id": rid,
            "weekdays": wd,
            "time": tm.strftime("%H:%M"),
            "title": title.strip(),
            "description": description.strip(),
            "catgirl_message": catgirl_message.strip(),
            "category": _coerce_category(category),
            "priority": _coerce_priority(priority),
            "created_at": _now_in_tz(self._tz).isoformat(),
        }
        self.store._write_value(self._recurring_key(rid), rule)
        # 维护 recur:ids 索引,供 list_recurring 用
        try:
            ids = self.store._read_value("recur:ids", []) or []
            if not isinstance(ids, list):
                ids = []
            if rid not in ids:
                ids.append(rid)
                self.store._write_value("recur:ids", ids)
        except Exception:
            self.logger.warning("add_recurring: persist recur:ids failed (non-fatal)")

        # 立即把规则铺到未来 7 天里(可选,但更直观)
        today = _now_in_tz(self._tz).date()
        for i in range(7):
            d = today + timedelta(days=i)
            if d.weekday() in wd:
                ds = d.strftime("%Y-%m-%d")
                plan = self._load_plan(ds)
                # 防重复:同日同时同标题视为同一条
                exists = any(
                    t.get("title") == rule["title"] and t.get("time") == rule["time"]
                    and t.get("recurring_id") == rid
                    for t in plan.get("tasks", [])
                )
                if not exists:
                    plan.setdefault("tasks", []).append({
                        "id": uuid.uuid4().hex[:10],
                        "time": rule["time"],
                        "title": rule["title"],
                        "description": rule["description"],
                        "catgirl_message": rule["catgirl_message"],
                        "category": rule["category"],
                        "priority": rule["priority"],
                        "status": _STATUS_PENDING,
                        "reminder_id": None,
                        "local_fired_at": None,
                        "recurring_id": rid,
                    })
                    plan["tasks"].sort(key=lambda x: x.get("time", "99:99"))
                    self._save_plan(ds, plan)
                    # memo_reminder 提醒
                    msg = _make_task_reminder(
                        plan["tasks"][-1], self._catgirl_name, self._master_name,
                    )
                    try:
                        res = await self.ctx.plugins.call_entry(
                            "memo_reminder:add_reminder",
                            {"time": f"{ds} {rule['time']}", "message": msg, "repeat": "once"},
                        )
                        if isinstance(res, dict) and res.get("reminder_id"):
                            plan["tasks"][-1]["reminder_id"] = res["reminder_id"]
                            self._save_plan(ds, plan)
                    except Exception as e:
                        self.logger.warning("add_recurring: create reminder failed: {}", e)

        self._wake_event.set()
        return Ok({
            "recurring_id": rid,
            "weekdays": wd,
            "time": rule["time"],
            "title": rule["title"],
            "instruction": "已创建重复任务规则,未来 7 天内符合条件的日子都会自动铺上。",
        })

    @llm_tool(
        name="catgirl_planner_list_recurring",
        description="查看所有已配置的重复任务规则列表。",
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    @plugin_entry(
        id="list_recurring",
        name="查看重复任务",
        description="列出所有已配置的重复任务规则。",
    )
    async def list_recurring(self, **_):
        # 列举所有 recur:* 键
        rules: List[Dict[str, Any]] = []
        try:
            # PluginStore 通常会暴露一个遍历接口,这里用 _read_value 全扫描不可行,
            # 退化为对常见 id 前缀的有限枚举:用一个独立键存 id 列表
            ids = self.store._read_value("recur:ids", []) or []
            for rid in ids:
                r = self.store._read_value(self._recurring_key(rid), None)
                if isinstance(r, dict):
                    rules.append(r)
        except Exception:
            self.logger.exception("list_recurring failed")
        return Ok({"rules": rules, "count": len(rules)})

    @llm_tool(
        name="catgirl_planner_delete_recurring",
        description="删除一条重复任务规则,已铺到具体日期的任务不受影响。",
        parameters={
            "type": "object",
            "properties": {
                "recurring_id": {"type": "string", "description": "重复任务规则ID"},
            },
            "required": ["recurring_id"],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="delete_recurring",
        name="删除重复任务",
        description="删除一条重复任务规则(不影响已经铺到具体某天的任务)。",
        input_schema={
            "type": "object",
            "properties": {"recurring_id": {"type": "string"}},
            "required": ["recurring_id"],
        },
        llm_result_fields=["deleted"],
    )
    async def delete_recurring(self, recurring_id: str, **_):
        try:
            self.store._write_value(self._recurring_key(recurring_id), None)
            ids = self.store._read_value("recur:ids", []) or []
            if recurring_id in ids:
                ids = [x for x in ids if x != recurring_id]
                self.store._write_value("recur:ids", ids)
            return Ok({"deleted": recurring_id})
        except Exception as e:
            return Err(SdkError(f"删除失败: {e}"))

    @llm_tool(
        name="catgirl_planner_get_meta",
        description="查看插件元信息,包括版本、可用任务分类、优先级定义、内置模板清单。",
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    @plugin_entry(
        id="get_meta",
        name="插件元信息",
        description="返回插件版本、可用分类、优先级、内置模板清单,前端可据此渲染选项。",
    )
    async def get_meta(self, **_):
        return Ok({
            "version": "0.2.0",
            "categories": [{"id": k, "label": v} for k, v in _CATEGORIES.items()],
            "priorities": [{"id": k, "label": v} for k, v in _PRIORITY_LABELS.items()],
            "weekday_labels": WEEKDAY_LABELS,
            "templates": [
                {"id": t["id"], "name": t["name"], "icon": t["icon"], "desc": t["desc"]}
                for t in _TEMPLATES.values()
            ],
        })

    @llm_tool(
        name="catgirl_planner_rebuild_stats",
        description="重建指定月份的统计数据。当统计数字明显不对时使用此工具修复,会从实际计划数据重新计算。",
        parameters={
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "YYYY-MM,默认本月"},
            },
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="rebuild_stats",
        name="重建月度统计",
        description="从实际计划数据重建指定月份的统计数据,修复历史统计错误。",
        input_schema={
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "YYYY-MM,默认本月"},
            },
        },
        llm_result_fields=["month", "total", "done"],
    )
    async def rebuild_stats(self, month: str = "", **_):
        if not month:
            month = _now_in_tz(self._tz).strftime("%Y-%m")

        # 计算该月的日期范围
        try:
            year, mon = int(month[:4]), int(month[5:7])
            start_date = datetime(year, mon, 1).date()
            if mon == 12:
                end_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
            else:
                end_date = datetime(year, mon + 1, 1).date() - timedelta(days=1)
        except (ValueError, IndexError):
            return Err(SdkError(f"月份格式错误: {month!r}"))

        # 遍历该月每天的计划数据,重新统计
        new_stats: Dict[str, Any] = {
            "month": month,
            "total": 0,
            "done": 0,
            "skipped": 0,
            "by_category": {},
            "by_day": {},
            "_recorded_dates": {},
        }

        d = start_date
        while d <= end_date:
            ds = d.strftime("%Y-%m-%d")
            plan = self._load_plan(ds)
            tasks = plan.get("tasks", [])
            if tasks:
                day_total = len(tasks)
                day_done = 0
                day_skipped = 0
                day_by_cat: Dict[str, int] = {}
                for t in tasks:
                    cat = t.get("category") or DEFAULT_CATEGORY
                    day_by_cat[cat] = day_by_cat.get(cat, 0) + 1
                    st = t.get("status")
                    if st == _STATUS_DONE:
                        day_done += 1
                    elif st == _STATUS_SKIPPED:
                        day_skipped += 1

                new_stats["total"] += day_total
                new_stats["done"] += day_done
                new_stats["skipped"] += day_skipped
                for cat, cnt in day_by_cat.items():
                    new_stats["by_category"][cat] = new_stats["by_category"].get(cat, 0) + cnt
                new_stats["by_day"][ds] = day_done
                new_stats["_recorded_dates"][ds] = {
                    "total": day_total,
                    "done": day_done,
                    "skipped": day_skipped,
                    "by_category": day_by_cat,
                }
            d += timedelta(days=1)

        self.store._write_value(self._stats_key(month), new_stats)

        return Ok({
            "month": month,
            "total": new_stats["total"],
            "done": new_stats["done"],
            "skipped": new_stats["skipped"],
            "by_category": new_stats["by_category"],
            "instruction": f"已重建 {month} 的统计: 共 {new_stats['total']} 项任务,完成 {new_stats['done']} 项。",
        })
