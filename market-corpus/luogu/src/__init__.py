"""
洛谷 (Luogu) 插件 —— 查题目/搜索/比赛/用户资料 + 基于提交记录的成长策略 + 每日题单推送。

数据来自洛谷页面内嵌的 ``decodeURIComponent('...')`` JSON(经 C3VK 前置验证),
登录态 cookie 用 Fernet 加密存到插件私有目录。每日题单通过 ``push_message``
(事件语义)注入主 AI,并可选地经 ``call_entry`` 跨插件推送到 QQ(可配置 entry_ref)。

成长策略基于登录用户的提交记录:统计薄弱/未涉及标签,按难度区间 + 每日确定性
seed 选出当天题单。
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    tr,
)

from ._ability import estimate_ability as compute_ability
from ._backfill import (
    CACHE_KEY as _BACKFILL_CACHE_KEY,
)
from ._backfill import (
    PidMeta,
    apply_meta,
    dump_cache,
    load_cache,
    needs_meta,
    plan_backfill,
)
from ._client import LuoguBlockedError, LuoguClient
from ._growth import (
    HISTORY_KEY as _DAILY_HISTORY_KEY,
)
from ._growth import (
    CooldownTier,
    analyze_growth,
    difficulty_band,
    dump_history,
    load_history,
    record_daily_picks,
    select_daily_with_windows,
)
from ._hint import make_hint
from ._models import (
    ContestItem,
    LuoguProblem,
    ProblemMeta,
    ReviewItem,
    SubmissionRecord,
)
from ._parsing import (
    LuoguAuthRequiredError,
    LuoguParseError,
    merge_tag_names,
    parse_contest_list,
    parse_problem_detail,
    parse_problem_list,
    parse_solution_page,
    parse_submissions,
    parse_tag_dictionary,
    parse_user_profile,
    record_list_count,
    tag_name,
    tag_names,
    usable_tag_names,
)
from ._review import (
    FEEDBACK_OK,
    FEEDBACKS,
    due_count,
    due_items,
    interval_days,
    is_due,
)
from ._review import (
    ITEMS_KEY as _REVIEW_ITEMS_KEY,
)
from ._review import (
    dump_items as dump_review_items,
)
from ._review import (
    load_items as load_review_items,
)
from ._review import (
    new_item as new_review_item,
)
from ._review import (
    review_item as apply_review_feedback,
)
from ._trend import compute_trend
from .credential_ui import LuoguCredentialUiMixin
from .heatmap_ui import LuoguHeatmapUiMixin

_DEFAULT_TZ = "Asia/Shanghai"
# Fallback topics used to seed the daily problem pool when the user's record
# list has no tag signal (Luogu omits tags on /record/list).
_DEFAULT_POOL_KEYWORDS = ("动态规划", "图论", "数学", "数据结构", "模拟", "字符串", "贪心", "搜索")
# Hard cap when a caller asks for "all" submissions (max_pages=0): bounds the
# worst case so a huge account doesn't hang the entry; stops early at the total
# count anyway. 50 pages = 1000 records.
_MAX_ALL_PAGES = 50
# Plugin-store key holding the last ability estimate, so recalibration has a
# previous level to move away from instead of restarting from scratch.
_ABILITY_LEVEL_KEY = "ability_level"
# Trend directions rendered for humans. "unknown" is the honest answer when the
# window holds too few active weeks to compare against.
_TREND_LABELS = {"up": "上升", "down": "下降", "flat": "持平", "unknown": "数据不足"}


@neko_plugin
class LuoguPlugin(LuoguCredentialUiMixin, LuoguHeatmapUiMixin, NekoPluginBase):
    """Query Luogu and produce submission-based growth / daily problem sets."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._init_credential_store()
        self._cfg: dict[str, Any] = {}
        self._client: LuoguClient | None = None
        self._tz = ZoneInfo(_DEFAULT_TZ)
        self._push_entries: list[dict[str, Any]] = []
        # Tag dictionary (/_lfe/tags) is fetched at most once per TTL; see
        # _ensure_tag_dictionary.
        self._tag_dict_loaded_at = 0.0
        # Daily push background thread (mirrors memo_reminder).
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._checker_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Config / session helpers
    # ------------------------------------------------------------------

    def _cfg_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self._cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key: str, default: bool = False) -> bool:
        value = self._cfg.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _setup_timezone(self) -> None:
        tz_name = str(self._cfg.get("daily_push_timezone", _DEFAULT_TZ)).strip()
        try:
            self._tz = ZoneInfo(tz_name)
        except Exception as exc:
            self._tz = ZoneInfo(_DEFAULT_TZ)
            self.logger.warning("Invalid timezone {!r}, using {} ({})", tz_name, _DEFAULT_TZ, exc)

    def _parse_push_entries(self) -> None:
        # 跨插件推送到 QQ 群/私聊暂时关闭：不解析 push_entries。
        # 需要恢复时取消下方注释即可。
        self._push_entries = []
        # entries = self._cfg.get("push_entries", [])
        # if isinstance(entries, dict):  # a single [[...]] renders as list of dicts
        #     entries = [entries]
        # for entry in entries if isinstance(entries, list) else []:
        #     if not isinstance(entry, dict):
        #         continue
        #     ref = entry.get("entry_ref")
        #     if isinstance(ref, str) and ref.strip():
        #         self._push_entries.append({"entry_ref": ref.strip(), "params": entry.get("params") or {}})

    async def _session(self) -> LuoguClient:
        client = self._client
        if client is None:
            client = LuoguClient(
                cookies=self._luogu_cookies,
                timeout=self._cfg_float("timeout_seconds", 15.0),
                min_interval=self._cfg_float("min_interval_seconds", 1.0),
                cookie_ttl_seconds=self._cfg_float("cookie_ttl_seconds", 240.0),
            )
            self._client = client
        return client

    async def _invalidate_credential_requests(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = None

    def _auth_gate(self) -> None:
        if not self._luogu_cookies:
            raise LuoguAuthRequiredError("login required")

    # ------------------------------------------------------------------
    # Data fetchers
    # ------------------------------------------------------------------

    async def _fetch_submissions(self, uid: str = "", page: int = 1, max_pages: int | None = None) -> list[SubmissionRecord]:
        """Fetch submissions, auto-paginating up to the user's total (capped).

        Luogu hardcodes 20 records/page, so more history costs more requests.
        ``max_pages`` (positive) caps it; ``None`` uses the ``fetch_max_pages``
        config (default 8) for the report/daily path; ``0`` means "all" (go until
        ``record_list_count`` is reached), bounded by a safety cap for huge
        accounts.
        """
        client = await self._session()
        params: dict[str, object] = {}
        target = uid or str(self._cfg.get("default_user", ""))
        if target:
            params["user"] = target
        if max_pages is None:
            cap = max(1, self._cfg_int("fetch_max_pages", 8))
        elif max_pages <= 0:
            cap = _MAX_ALL_PAGES
        else:
            cap = max(1, int(max_pages))
        records: list[SubmissionRecord] = []
        total = 0
        start = max(1, int(page))
        for current in range(start, start + cap):
            params["page"] = current
            try:
                resp = await client.get("/record/list", params=params)
                page_records = parse_submissions(resp.text)
            except (LuoguAuthRequiredError, LuoguParseError):
                break
            if not page_records:
                break
            records.extend(page_records)
            total = record_list_count(resp.text) or total
            if total and len(records) >= total:
                break
        return await self._backfill_records(records)

    # ------------------------------------------------------------------
    # Per-problem metadata backfill (tags are not in /record/list)
    # ------------------------------------------------------------------

    async def _store_get(self, key: str, default: Any = None) -> Any:
        """Read a plugin-store value, tolerating an Err result or a raised error."""
        try:
            result = await self.store.get(key, default)
        except Exception:
            return default
        if hasattr(result, "is_ok") and callable(result.is_ok):
            return result.value if result.is_ok() else default
        return getattr(result, "value", result)

    async def _store_set(self, key: str, value: Any) -> bool:
        """Write a plugin-store value; returns whether the write was accepted."""
        try:
            await self.store.set(key, value)
        except Exception:
            self.logger.warning("Plugin store write failed for {}", key)
            return False
        return True

    async def _load_meta_cache(self) -> dict[str, PidMeta]:
        return load_cache(await self._store_get(_BACKFILL_CACHE_KEY, {}))

    async def _save_meta_cache(self, cache: dict[str, PidMeta]) -> None:
        await self._store_set(_BACKFILL_CACHE_KEY, dump_cache(cache))

    async def _fetch_problem_meta(self, pid: str) -> PidMeta:
        client = await self._session()
        resp = await client.get(f"/problem/{pid}")
        detail = parse_problem_detail(resp.text)
        if detail is None:
            raise LuoguParseError(f"no problem detail in /problem/{pid}")
        return PidMeta(difficulty=detail.difficulty, tags=detail.tags, title=detail.title, fetched=True)

    async def _backfill_records(self, records: list[SubmissionRecord]) -> list[SubmissionRecord]:
        """Fill in the tag/difficulty signal ``/record/list`` does not carry.

        The record list reports each submission's difficulty but no tags, so a
        growth report built straight from it has no tag signal at all. Each run
        enriches at most ``backfill_max_per_run`` unseen problems — the page fetch
        is throttled to about one request per second by the client — and the rest
        stay queued in the cache for later runs. A problem that fails to load is
        retried only after ``backfill_backoff_seconds``.
        """
        if not records or not self._cfg_bool("backfill_enabled", True):
            return records
        cache = await self._load_meta_cache()
        planned = plan_backfill(
            (record.pid for record in records if needs_meta(record)),
            cache,
            limit=self._cfg_int("backfill_max_per_run", 12),
            now=time.time(),
            backoff_seconds=self._cfg_float("backfill_backoff_seconds", 300.0),
        )
        fetched = 0
        for pid in planned:
            try:
                cache[pid] = await self._fetch_problem_meta(pid)
                fetched += 1
            except Exception as exc:
                cache[pid] = PidMeta(failed_at=time.time())
                self.logger.warning("Problem metadata fetch failed for {}: {}", pid, self._safe_err(exc))
        if planned:
            await self._save_meta_cache(cache)
            self.logger.info("Luogu metadata backfill: {}/{} planned problems enriched", fetched, len(planned))
        return [apply_meta(record, cache.get(record.pid)) for record in records]

    async def _ensure_tag_dictionary(self, ttl_seconds: float = 86400.0, retry_seconds: float = 900.0) -> None:
        """Merge Luogu's live tag dictionary into the shared id→name map.

        The static map seeds 126 of the 505 ids Luogu currently serves, so
        without this tags such as 线段树 (id 42) render as ``#42`` and are
        unusable as candidate-pool keywords. A failure is non-fatal (the static
        map stays in use) and is retried only after ``retry_seconds`` so a
        blocked network does not add a request to every caller.
        """
        now = time.monotonic()
        if self._tag_dict_loaded_at and (now - self._tag_dict_loaded_at) < ttl_seconds:
            return
        try:
            client = await self._session()
            resp = await client.get("/_lfe/tags")
            added = merge_tag_names(parse_tag_dictionary(resp.text))
        except Exception as exc:
            # Back-date the stamp so the next attempt happens after retry_seconds.
            self._tag_dict_loaded_at = now - ttl_seconds + retry_seconds
            self.logger.warning("Luogu tag dictionary unavailable: {}", self._safe_err(exc))
            return
        self._tag_dict_loaded_at = now
        self.logger.info("Luogu tag dictionary merged: +{} tags", added)

    async def _fetch_candidate_pool(self, tags: list[str], max_per_tag: int = 10, total: int = 40) -> list[ProblemMeta]:
        """Fetch a candidate problem pool for the daily selector.

        When the user's record list carries no tag signal (Luogu's /record/list
        omits tags), fall back to a spread of common algorithm topics so the
        daily set still has candidates to pick from.

        ``tags`` must be *display* names, not numeric ids: /problem/list matches
        visible text, so ``keyword=42`` returns nothing while ``keyword=线段树``
        returns real candidates (see ``usable_tag_names``).
        """
        await self._ensure_tag_dictionary()
        keywords = [tag for tag in tags if tag][:6] if tags else list(_DEFAULT_POOL_KEYWORDS)
        client = await self._session()
        pool: list[ProblemMeta] = []
        seen: set[str] = set()
        for keyword in keywords:
            if len(pool) >= total:
                break
            try:
                resp = await client.get("/problem/list", params={"keyword": keyword, "page": 1})
            except LuoguBlockedError:
                continue
            for problem in parse_problem_list(resp.text, limit=max_per_tag):
                if problem.pid not in seen:
                    seen.add(problem.pid)
                    pool.append(problem)
        return pool

    def _dump_submission_text(self, record: SubmissionRecord) -> str:
        return f"{record.pid} {record.title or ''}"

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _difficulty_range(self) -> tuple[int, int]:
        return (
            self._cfg_int("daily_difficulty_min", 2),
            self._cfg_int("daily_difficulty_max", 5),
        )

    def _daily_count(self, requested: int = 0) -> int:
        return requested if requested > 0 else max(1, self._cfg_int("daily_problem_count", 3))

    def _difficulty_window(
        self,
        difficulty_min: int = 0,
        difficulty_max: int = 0,
        *,
        ability=None,
    ) -> tuple[int, int]:
        """Difficulty window for the day's set.

        Precedence: explicit arguments, then a band around the ability estimate
        (a fixed 2..5 window is wrong for both a beginner and a 黑题 solver), then
        the configured min/max as the last resort.
        """
        if difficulty_min > 0 or difficulty_max > 0:
            low = difficulty_min if difficulty_min > 0 else self._cfg_int("daily_difficulty_min", 2)
            high = difficulty_max if difficulty_max > 0 else self._cfg_int("daily_difficulty_max", 5)
            return (min(low, high), max(low, high))
        if ability is not None and self._cfg_bool("daily_use_ability_band", True):
            return difficulty_band(
                ability.level,
                below=max(0, self._cfg_int("daily_band_below", 1)),
                above=max(0, self._cfg_int("daily_band_above", 2)),
            )
        return self._difficulty_range()

    def _difficulty_windows(
        self,
        difficulty_min: int = 0,
        difficulty_max: int = 0,
        *,
        ability=None,
    ) -> list[tuple[int, int]]:
        """Windows to try, narrowest first: the chosen band, then progressively wider.

        A narrow band is right until it starves the pool. Since an empty problem
        set helps nobody, the window then widens one step at a time up to the whole
        1..8 scale — a slightly off-target problem beats no problem, and the caller
        is told which window actually produced the set.
        """
        base_low, base_high = self._difficulty_window(difficulty_min, difficulty_max, ability=ability)
        windows = [(base_low, base_high)]
        low, high = base_low, base_high
        while low > 1 or high < 8:
            low = max(1, low - 1)
            high = min(8, high + 1)
            if (low, high) not in windows:
                windows.append((low, high))
        return windows

    def _cooldown_ladder(self) -> tuple[CooldownTier, ...]:
        """Cooldown rungs, strictest first, derived from ``daily_cooldown_days``.

        Halving keeps the ladder short and predictable (14 → 7 → 3 → 1 days),
        and the last two rungs permit a repeat so an exhausted pool still yields
        a problem set instead of an empty one.
        """
        strict = max(0, self._cfg_int("daily_cooldown_days", 14))
        rungs: list[CooldownTier] = []
        current = strict
        while current > 0:
            rungs.append(CooldownTier(current))
            current //= 2
        rungs.append(CooldownTier(0))
        rungs.append(CooldownTier(0, exclude_review=False))
        return tuple(rungs)

    async def _load_daily_history(self) -> dict[str, str]:
        return load_history(await self._store_get(_DAILY_HISTORY_KEY, {}))

    async def _save_daily_history(self, history: dict[str, str]) -> None:
        await self._store_set(_DAILY_HISTORY_KEY, dump_history(history))

    async def _load_review_queue(self) -> dict[str, ReviewItem]:
        return load_review_items(await self._store_get(_REVIEW_ITEMS_KEY, {}))

    async def _save_review_queue(self, items: dict[str, ReviewItem]) -> bool:
        return await self._store_set(_REVIEW_ITEMS_KEY, dump_review_items(items))

    async def _select_daily(
        self,
        report,
        pool: list[ProblemMeta],
        *,
        count: int = 0,
        difficulty_min: int = 0,
        difficulty_max: int = 0,
        refresh_nonce: int = 0,
        ability=None,
    ):
        """Pick today's problems and remember them so tomorrow differs.

        Priority, strictest first: a fresh problem in the narrowest band; a fresh
        problem in a wider band; and only when no window fills the set at this
        cooldown, a repeat. The picks are recorded only once they exist, so a run
        that finds nothing does not poison the next one.
        """
        today = datetime.now(self._tz).date()
        history = await self._load_daily_history()
        review_queue = await self._load_review_queue()
        seed = today.isoformat() if not refresh_nonce else f"{today.isoformat()}#{refresh_nonce}"
        selection = select_daily_with_windows(
            report,
            pool,
            windows=self._difficulty_windows(difficulty_min, difficulty_max, ability=ability),
            count=self._daily_count(count),
            daily_seed=seed,
            history=history,
            today=today,
            review_pids=frozenset(review_queue),
            ladder=self._cooldown_ladder(),
        )
        if selection.problems:
            await self._save_daily_history(
                record_daily_picks(history, list(selection.problems), today)
            )
        return selection

    def _build_growth_report(self, submissions: list[SubmissionRecord]):
        return analyze_growth(
            submissions,
            weak_attempt_threshold=self._cfg_int("weak_attempt_threshold", 3),
            tag_label=tag_name,
        )

    def _build_trend(self, submissions: list[SubmissionRecord]):
        """Week-by-week activity, bucketed in the configured timezone."""
        return compute_trend(
            submissions,
            tz=self._tz,
            today=datetime.now(self._tz).date(),
            weeks=max(1, self._cfg_int("trend_weeks", 12)),
        )

    async def _estimate_ability(
        self,
        submissions: list[SubmissionRecord],
        *,
        window_days: int = 0,
        recalibrate: bool = False,
    ):
        """Estimate the comfortable difficulty, optionally moving the stored level.

        With ``recalibrate`` the stored level is fed back in as the previous
        estimate, so the number drifts toward reality over runs instead of
        jumping to whatever this window says.
        """
        window = window_days if window_days > 0 else max(1, self._cfg_int("ability_window_days", 60))
        previous: float | None = None
        if recalibrate:
            stored = await self._store_get(_ABILITY_LEVEL_KEY)
            try:
                previous = float(stored) if stored is not None else None
            except (TypeError, ValueError):
                previous = None
        estimate = compute_ability(
            submissions,
            now=datetime.now(self._tz),
            window_days=window,
            previous=previous,
        )
        if recalibrate:
            await self._store_set(_ABILITY_LEVEL_KEY, estimate.level)
        return estimate

    def _candidate_keywords(self, report) -> list[str]:
        """Display names of the weak/untouched tags, for the candidate search.

        A report carries the caller's tag values — numeric Luogu ids in
        production — while /problem/list matches visible text. Ids without a
        known name are dropped rather than sent (see ``usable_tag_names``), so an
        all-ids list degrades to the topical defaults in ``_fetch_candidate_pool``
        instead of searching for a meaningless number.
        """
        ids = [item.tag for item in report.weak_tags] + list(report.untouched_tags_sorted)
        return usable_tag_names(ids)[:6]

    def _format_problem_set_text(
        self,
        problems: list[ProblemMeta],
        *,
        role_aware: bool = False,
        due_review: int = 0,
        relaxed: bool = False,
    ) -> str:
        if not problems:
            return "今日暂未找到合适的题目。"
        lines = ["📋 今日题单"]
        for index, problem in enumerate(problems, 1):
            lines.append(f"{index}. {problem.pid} {problem.title}（难度 {problem.difficulty}）")
        if due_review > 0:
            lines.append(f"🔁 复习到期 {due_review} 题")
        if relaxed:
            lines.append("（候选池不足，已放宽筛选条件）")
        core = "\n".join(lines)
        if role_aware:
            return f"{{MASTER_NAME}}，这是今天的题单：\n{core}"
        return core

    # ------------------------------------------------------------------
    # Daily push background thread
    # ------------------------------------------------------------------

    def _next_push_seconds(self) -> float:
        now = datetime.now(self._tz)
        try:
            hour, minute = (int(x) for x in str(self._cfg.get("daily_push_time", "08:00")).split(":"))
        except (ValueError, TypeError):
            hour, minute = 8, 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _daily_checker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                wait = self._next_push_seconds()
            except Exception:
                wait = 3600.0
            self._wake_event.wait(timeout=max(wait, 0.1))
            if self._stop_event.is_set():
                break
            loop = self._loop
            if loop is not None and not loop.is_closed():
                asyncio.run_coroutine_threadsafe(self._run_daily_push(), loop)

    def _start_daily_thread(self) -> None:
        if self._checker_thread is not None and self._checker_thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._checker_thread = threading.Thread(target=self._daily_checker_loop, daemon=True, name="luogu-daily")
        self._checker_thread.start()
        self.logger.info("Luogu daily push thread started ({} {})", self._cfg.get("daily_push_time"), self._tz)

    def _stop_daily_thread(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._checker_thread is not None and self._checker_thread.is_alive():
            self._checker_thread.join(timeout=3.0)

    async def _run_daily_push(self) -> None:
        """Generate today's problem set and push via push_message + push_entries."""
        try:
            submissions = await self._fetch_submissions()
            report = self._build_growth_report(submissions)
            ability = await self._estimate_ability(submissions)
            pool = await self._fetch_candidate_pool(self._candidate_keywords(report))
            selection = await self._select_daily(report, pool, ability=ability)
            due = due_count(await self._load_review_queue(), datetime.now(self._tz).date().isoformat())
            picks = list(selection.problems)
            core_text = self._format_problem_set_text(picks, due_review=due, relaxed=selection.relaxed)
            role_text = self._format_problem_set_text(
                picks, role_aware=True, due_review=due, relaxed=selection.relaxed
            )
        except LuoguAuthRequiredError:
            core_text = "尚未配置洛谷登录态，暂无法生成基于提交记录的每日题单。"
            role_text = core_text
        except Exception as exc:
            self.logger.warning("Daily push generation failed: {} {}", type(exc).__name__, self._safe_err(exc))
            core_text = "今天生成题单时遇到了问题，稍后再试。"
            role_text = core_text

        try:
            self.ctx.push_message(
                source="luogu",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": role_text}],
                priority=8,
                metadata={"event_type": "luogu_daily_problem_set"},
            )
        except Exception:
            self.logger.warning("push_message failed for daily problem set")

        # 跨插件推送到 QQ 群/私聊暂时关闭（恢复时取消注释）。
        # for entry in self._push_entries:
        #     await self._dispatch_push_entry(entry, core_text)

    def _safe_err(self, error: Exception) -> str:
        return type(error).__name__

    async def _dispatch_push_entry(self, entry: dict[str, Any], text: str) -> None:
        # 跨插件推送到 QQ 群/私聊暂时关闭（恢复时取消注释函数体）。
        return
        ref = entry.get("entry_ref", "")
        if not ref:
            return
        params: dict[str, object] = {}
        for key, value in dict(entry.get("params") or {}).items():
            params[key] = str(value).replace("{TEXT}", text)
        try:
            response = await asyncio.wait_for(
                self.plugins.call_entry(ref, params=params, timeout=10.0),
                timeout=11.0,
            )
            if isinstance(response, Err):
                self.logger.warning("push_entry call {} returned Err: {}", ref, str(getattr(response, "error", ""))[:120])
        except Exception as exc:
            self.logger.warning("push_entry call {} failed: {}", ref, type(exc).__name__)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_: object):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("luogu") if isinstance(cfg.get("luogu"), dict) else {}
        self._setup_timezone()
        await self._load_luogu_cookies()
        self._parse_push_entries()
        self._client = None
        self.register_static_ui("static", cache_control="no-cache")
        if self._cfg_bool("daily_push_enabled", False):
            self._start_daily_thread()
        self.logger.info(
            "Luogu started: cookie_configured={} daily_push_enabled={} push_entries={}",
            bool(self._luogu_cookies), self._cfg_bool("daily_push_enabled", False), len(self._push_entries),
        )
        return Ok({"status": "running", "cookie_configured": bool(self._luogu_cookies)})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: object):
        self._stop_daily_thread()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
        self.logger.info("Luogu shutdown")
        return Ok({"status": "stopped"})

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------

    @plugin_entry(
        id="search_problem",
        name=tr("entry.search.name", default="搜索洛谷题目"),
        description=tr("entry.search.description", default="按关键词/难度/标签搜索洛谷题目。"),
        llm_result_fields=["summary"],
        timeout=20.0,
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "题目关键词，尽量简短（如 dp、背包、P1001）"},
                "difficulty": {"type": "integer", "description": "等难度过滤，1..8，缺省不过滤", "default": 0},
                "tag": {"type": "string", "description": "按标签过滤", "default": ""},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["keyword"],
        },
    )
    async def search_problem(self, keyword: str = "", difficulty: int = 0, tag: str = "", page: int = 1, **_: object):
        if not keyword.strip():
            return Err(SdkError(self.i18n.t("errors.empty_keyword", default="关键词不能为空。")))
        try:
            client = await self._session()
            params: dict[str, object] = {"keyword": keyword.strip(), "page": max(1, int(page))}
            if tag:
                params["tag"] = tag.strip()
            resp = await client.get("/problem/list", params=params)
            problems = parse_problem_list(resp.text, limit=30)
            if difficulty > 0:
                problems = [p for p in problems if p.difficulty == int(difficulty)]
            problems = problems[:15]
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("search_problem failed: {}", type(exc).__name__)
            return Err(SdkError("搜索失败。", code="luogu_error"))
        summary = self._build_search_summary(keyword, problems)
        return Ok({"count": len(problems), "summary": summary, "problems": [self._problem_to_dict(p) for p in problems]})

    @plugin_entry(
        id="get_problem_detail",
        name=tr("entry.detail.name", default="获取洛谷题目详情"),
        description=tr("entry.detail.description", default="获取题目题面、难度、标签、通过/提交数与题解入口。"),
        llm_result_fields=["summary"],
        timeout=20.0,
        input_schema={
            "type": "object",
            "properties": {"pid": {"type": "string", "description": "题目编号，如 P1001"}},
            "required": ["pid"],
        },
    )
    async def get_problem_detail(self, pid: str = "", **_: object):
        if not pid.strip():
            return Err(SdkError(self.i18n.t("errors.empty_pid", default="题目编号不能为空。")))
        try:
            client = await self._session()
            resp = await client.get(f"/problem/{pid.strip()}")
            detail = parse_problem_detail(resp.text)
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("get_problem_detail failed: {}", type(exc).__name__)
            return Err(SdkError("获取题目失败。", code="luogu_error"))
        if detail is None:
            return Err(SdkError("未找到该题目。", code="luogu_not_found"))
        return Ok(self._detail_to_payload(detail))

    @plugin_entry(
        id="problem_hint",
        name=tr("entry.hint.name", default="获取洛谷题目思路提示"),
        description=tr(
            "entry.hint.description",
            default="基于洛谷题解提炼一句解题思路点拨，点到为止、不含代码，保留思考空间。",
        ),
        llm_result_fields=["summary"],
        timeout=15.0,
        input_schema={
            "type": "object",
            "properties": {
                "pid": {"type": "string", "description": "题目编号，如 P1803"},
                "level": {
                    "type": "integer",
                    "description": "提示深度：1=只给方向，2=一句话点拨（默认），3=思路要点",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 3,
                },
            },
            "required": ["pid"],
        },
    )
    async def problem_hint(self, pid: str = "", level: int = 2, **_: object):
        if not pid.strip():
            return Err(SdkError(self.i18n.t("errors.empty_pid", default="题目编号不能为空。")))
        try:
            level = int(level)
        except (TypeError, ValueError):
            level = 2
        level = max(1, min(3, level))
        try:
            client = await self._session()
            sol_resp = await client.get(f"/problem/solution/{pid.strip()}")
            page = parse_solution_page(sol_resp.text)
            det_resp = await client.get(f"/problem/{pid.strip()}")
            detail = parse_problem_detail(det_resp.text)
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("problem_hint failed: {}", type(exc).__name__)
            return Err(SdkError("获取思路提示失败。", code="luogu_error"))

        tags = tuple(tag_names(detail.tags)) if detail is not None else ()
        tag_list = list(tags)
        hint = make_hint(page.get("solutions", [{}])[0].get("content", "") if page.get("solutions") else "", tags, level)
        return Ok(
            {
                "summary": hint,
                "hint": hint,
                "level": level,
                "pid": str(getattr(detail, "pid", pid.strip())),
                "title": str(getattr(detail, "title", page.get("name", ""))),
                "tags": tag_list,
                "difficulty": getattr(detail, "difficulty", page.get("difficulty", 0)),
                "source": "problem_solution" if page.get("solutions") else None,
                "no_solution": not bool(page.get("solutions")),
            }
        )

    @plugin_entry(
        id="get_contest_list",
        name=tr("entry.contest.name", default="获取洛谷比赛列表"),
        description=tr("entry.contest.description", default="获取近期洛谷比赛。"),
        llm_result_fields=["summary"],
        timeout=20.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def get_contest_list(self, **_: object):
        try:
            client = await self._session()
            resp = await client.get("/contest/list")
            contests = parse_contest_list(resp.text)
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("get_contest_list failed: {}", type(exc).__name__)
            return Err(SdkError("获取比赛失败。", code="luogu_error"))
        summary = self._build_contest_summary(contests)
        return Ok({"count": len(contests), "summary": summary, "contests": [self._contest_to_dict(c) for c in contests]})

    @plugin_entry(
        id="get_user_profile",
        name=tr("entry.profile.name", default="获取洛谷用户资料"),
        description=tr("entry.profile.description", default="获取洛谷用户公开资料；登录态下包含私有统计。"),
        llm_result_fields=["summary"],
        timeout=20.0,
        input_schema={
            "type": "object",
            "properties": {"uid": {"type": "string", "description": "洛谷 uid/用户名；空则用登录态", "default": ""}},
        },
    )
    async def get_user_profile(self, uid: str = "", **_: object):
        try:
            client = await self._session()
            target = uid.strip() or str(self._cfg.get("default_user", ""))
            if not target:
                # No explicit uid / default_user: fall back to the logged-in user.
                target = str(self._luogu_cookies.get("_uid", ""))
            if not target:
                return Err(SdkError("该操作需要提供 uid 或登录态。", code="luogu_auth_required"))
            path = f"/user/{target}"
            resp = await client.get(path)
            profile = parse_user_profile(resp.text)
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("get_user_profile failed: {}", type(exc).__name__)
            return Err(SdkError("获取用户资料失败。", code="luogu_error"))
        return Ok({"summary": self._build_profile_summary(profile), "profile": profile})

    @plugin_entry(
        id="get_submissions",
        name=tr("entry.submissions.name", default="获取洛谷提交记录"),
        description=tr("entry.submissions.description", default="获取登录用户的提交记录（AC/WA 分类、难度、标签）。"),
        llm_result_fields=["summary"],
        timeout=45.0,
        input_schema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "默认登录者", "default": ""},
                "page": {"type": "integer", "default": 1},
            },
        },
    )
    async def get_submissions(self, uid: str = "", page: int = 1, **_: object):
        try:
            self._auth_gate()
            submissions = await self._fetch_submissions(uid=uid.strip(), page=max(1, int(page)), max_pages=0)
        except LuoguAuthRequiredError:
            return Err(SdkError("该操作需要洛谷登录态。", code="luogu_auth_required"))
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("get_submissions failed: {}", type(exc).__name__)
            return Err(SdkError("获取提交记录失败。", code="luogu_error"))
        summary = self._build_submissions_summary(submissions)
        return Ok({"count": len(submissions), "summary": summary, "submissions": [self._sub_to_dict(s) for s in submissions]})

    @plugin_entry(
        id="growth_report",
        name=tr("entry.growth.name", default="生成洛谷成长报告"),
        description=tr("entry.growth.description", default="基于提交记录分析薄弱/未涉及标签，输出成长建议。"),
        llm_result_fields=["summary", "suggestion"],
        timeout=45.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def growth_report(self, **_: object):
        try:
            self._auth_gate()
            # Report/daily output renders tag ids through the shared map, so make
            # sure the live dictionary has been merged before building either.
            await self._ensure_tag_dictionary()
            submissions = await self._fetch_submissions()
            report = self._build_growth_report(submissions)
        except LuoguAuthRequiredError:
            return Err(SdkError("该操作需要洛谷登录态。", code="luogu_auth_required"))
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("growth_report failed: {}", type(exc).__name__)
            return Err(SdkError("生成成长报告失败。", code="luogu_error"))
        payload = self._growth_to_payload(
            report,
            trend=self._build_trend(submissions),
            ability=await self._estimate_ability(submissions),
            reviews_due=due_count(await self._load_review_queue(), datetime.now(self._tz).date().isoformat()),
        )
        return Ok(payload)

    @plugin_entry(
        id="estimate_ability",
        name=tr("entry.ability.name", default="估算洛谷能力档位"),
        description=tr(
            "entry.ability.description",
            default="按解题证据（难度、尝试次数、新鲜度）加权估算当前能力档位（洛谷难度 1-8），可选按上一档缓慢校准。",
        ),
        llm_result_fields=["summary"],
        timeout=45.0,
        input_schema={
            "type": "object",
            "properties": {
                "window_days": {"type": "integer", "default": 0, "description": "统计窗口天数，0 表示用配置值（默认 60）"},
                "recalibrate": {"type": "boolean", "default": False, "description": "读取并更新插件内保存的上一档估算，做缓慢校准"},
            },
        },
    )
    async def estimate_ability(self, window_days: int = 0, recalibrate: bool = False, **_: object):
        try:
            self._auth_gate()
            submissions = await self._fetch_submissions()
        except LuoguAuthRequiredError:
            return Err(SdkError("该操作需要洛谷登录态。", code="luogu_auth_required"))
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("estimate_ability failed: {}", type(exc).__name__)
            return Err(SdkError("估算能力档位失败。", code="luogu_error"))
        try:
            window = max(0, int(window_days))
        except (TypeError, ValueError):
            window = 0
        estimate = await self._estimate_ability(
            submissions,
            window_days=window,
            recalibrate=bool(recalibrate),
        )
        summary = (
            f"估算能力档 {estimate.level:.2f}/8（难度中心 {estimate.base:.2f}，"
            f"通过率修正 {estimate.adjustment:+.2f}，置信度 {estimate.confidence:.0%}）\n{estimate.reason}"
        )
        return Ok(
            {
                "summary": summary,
                "level": estimate.level,
                "base": estimate.base,
                "adjustment": estimate.adjustment,
                "window_days": estimate.window_days,
                "ac_count": estimate.ac_count,
                "ac_rate": estimate.ac_rate,
                "confidence": estimate.confidence,
                "recalibrated": estimate.recalibrated,
                "previous_level": estimate.previous_level,
                "reason": estimate.reason,
            }
        )

    @plugin_entry(
        id="daily_problem_set",
        name=tr("entry.daily.name", default="生成洛谷每日题单"),
        description=tr("entry.daily.description", default="基于成长策略生成当天推荐题目。"),
        llm_result_fields=["summary"],
        timeout=45.0,
        input_schema={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 3},
                "difficulty_min": {"type": "integer", "default": 2},
                "difficulty_max": {"type": "integer", "default": 5},
                "refresh": {"type": "boolean", "default": False},
            },
        },
    )
    async def daily_problem_set(self, count: int = 0, difficulty_min: int = 0, difficulty_max: int = 0, refresh: bool = False, **_: object):
        try:
            self._auth_gate()
            await self._ensure_tag_dictionary()
            submissions = await self._fetch_submissions()
            report = self._build_growth_report(submissions)
            ability = await self._estimate_ability(submissions)
            pool = await self._fetch_candidate_pool(self._candidate_keywords(report))
            selection = await self._select_daily(
                report,
                pool,
                count=count,
                difficulty_min=difficulty_min,
                difficulty_max=difficulty_max,
                # A refresh means "different batch today": the cooldown already
                # ignores same-day picks, so the nonce only reshuffles the jitter.
                refresh_nonce=int(time.time()) if refresh else 0,
                ability=ability,
            )
            due = due_count(await self._load_review_queue(), datetime.now(self._tz).date().isoformat())
        except LuoguAuthRequiredError:
            return Err(SdkError("该操作需要洛谷登录态。", code="luogu_auth_required"))
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("daily_problem_set failed: {}", type(exc).__name__)
            return Err(SdkError("生成每日题单失败。", code="luogu_error"))
        picks = list(selection.problems)
        summary = self._format_problem_set_text(picks, due_review=due, relaxed=selection.relaxed)
        return Ok(
            {
                "count": len(picks),
                "summary": summary,
                "problems": [self._problem_to_dict(p) for p in picks],
                "difficulty_window": list(selection.difficulty_window),
                "cooldown_days": selection.cooldown_days,
                "relaxed": selection.relaxed,
                "reviews_due": due,
            }
        )

    @plugin_entry(
        id="push_daily_problem_set",
        name=tr("entry.push_daily.name", default="推送洛谷每日题单"),
        description=tr("entry.push_daily.description", default="生成题单并推送到配置的群/私聊或对话框。"),
        llm_result_fields=["status"],
        timeout=45.0,
        input_schema={
            "type": "object",
            "properties": {"silent": {"type": "boolean", "default": False}},
        },
    )
    async def push_daily_problem_set(self, silent: bool = False, **_: object):
        try:
            self._auth_gate()
            submissions = await self._fetch_submissions()
            report = self._build_growth_report(submissions)
            ability = await self._estimate_ability(submissions)
            pool = await self._fetch_candidate_pool(self._candidate_keywords(report))
            selection = await self._select_daily(report, pool, ability=ability)
            due = due_count(await self._load_review_queue(), datetime.now(self._tz).date().isoformat())
            # core_text 仅用于跨插件推送，暂时关闭（恢复推送时取消注释）。
            # core_text = self._format_problem_set_text(list(selection.problems))
        except LuoguAuthRequiredError:
            return Err(SdkError("该操作需要洛谷登录态。", code="luogu_auth_required"))
        except Exception as exc:
            self.logger.warning("push_daily_problem_set failed: {}", type(exc).__name__)
            return Err(SdkError("生成每日题单失败。", code="luogu_error"))

        if not silent:
            try:
                self.ctx.push_message(
                    source="luogu",
                    visibility=[],
                    ai_behavior="respond",
                    parts=[
                        {
                            "type": "text",
                            "text": self._format_problem_set_text(
                                list(selection.problems),
                                role_aware=True,
                                due_review=due,
                                relaxed=selection.relaxed,
                            ),
                        }
                    ],
                    priority=8,
                    metadata={"event_type": "luogu_daily_problem_set"},
                )
            except Exception:
                self.logger.warning("push_message failed in push_daily_problem_set")

        # 跨插件推送到 QQ 群/私聊暂时关闭（恢复时取消注释）。
        # for entry in self._push_entries:
        #     await self._dispatch_push_entry(entry, core_text)

        return Ok(
            {
                "status": "pushed",
                "count": len(selection.problems),
                "cooldown_days": selection.cooldown_days,
                "relaxed": selection.relaxed,
                "reviews_due": due,
            }
        )

    # ------------------------------------------------------------------
    # Review queue (spaced repetition)
    # ------------------------------------------------------------------

    @plugin_entry(
        id="review_due",
        name=tr("entry.review_due.name", default="查看洛谷复习队列"),
        description=tr(
            "entry.review_due.description",
            default="列出今天到期的复习题目；间隔阶梯为 1/2/4/7/15/30/60 天。",
        ),
        llm_result_fields=["summary"],
        timeout=10.0,
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 0, "description": "最多返回几条，0 表示不限"},
                "include_upcoming": {"type": "boolean", "default": False, "description": "是否附带尚未到期的条目"},
            },
        },
    )
    async def review_due(self, limit: int = 0, include_upcoming: bool = False, **_: object):
        today = datetime.now(self._tz).date().isoformat()
        queue = await self._load_review_queue()
        due = due_items(queue, today, limit=max(0, int(limit or 0)))
        lines = [f"复习到期 {len(due)} 题（队列共 {len(queue)} 题）"]
        for item in due[:10]:
            names = "、".join(item.tags[:3])
            suffix = f"｜{names}" if names else ""
            lines.append(
                f"{item.pid} {item.title}（第 {item.stage + 1} 档，已复习 {item.reviews} 次{suffix}）"
            )
        payload: dict[str, Any] = {
            "summary": "\n".join(lines),
            "count": len(due),
            "total": len(queue),
            "today": today,
            "items": [self._review_to_dict(item) for item in due],
        }
        if include_upcoming:
            upcoming = sorted(
                (item for item in queue.values() if not is_due(item, today)),
                key=lambda item: (item.due_on or "", item.pid),
            )
            payload["upcoming"] = [self._review_to_dict(item) for item in upcoming[:20]]
        return Ok(payload)

    @plugin_entry(
        id="review_add",
        name=tr("entry.review_add.name", default="加入洛谷复习队列"),
        description=tr(
            "entry.review_add.description",
            default="把一道题加入复习队列，按间隔阶梯在 1 天后首次复习；题名/难度/标签留空时用插件缓存补齐。",
        ),
        llm_result_fields=["summary"],
        timeout=10.0,
        input_schema={
            "type": "object",
            "properties": {
                "pid": {"type": "string", "description": "题目编号，如 P1001"},
                "title": {"type": "string", "default": "", "description": "题名，留空用缓存"},
                "difficulty": {"type": "integer", "default": 0, "description": "难度，留空用缓存"},
                "tags": {"type": "string", "default": "", "description": "逗号分隔的标签名，留空用缓存"},
            },
            "required": ["pid"],
        },
    )
    async def review_add(self, pid: str = "", title: str = "", difficulty: int = 0, tags: str = "", **_: object):
        target = pid.strip()
        if not target:
            return Err(SdkError("题目编号不能为空。", code="luogu_bad_request"))
        today = datetime.now(self._tz).date().isoformat()
        meta = (await self._load_meta_cache()).get(target)
        queue = await self._load_review_queue()
        item = new_review_item(
            target,
            today=today,
            title=title.strip() or (meta.title if meta else ""),
            difficulty=difficulty if difficulty > 0 else (meta.difficulty if meta else 0),
            tags=tuple(part.strip() for part in tags.split(",") if part.strip())
            or (tag_names(meta.tags) if meta else ()),
        )
        queue[target] = item
        await self._save_review_queue(queue)
        return Ok(
            {
                "status": "queued",
                "summary": f"{target} 已加入复习队列，{item.due_on} 首次复习（队列共 {len(queue)} 题）。",
                **self._review_to_dict(item),
            }
        )

    @plugin_entry(
        id="review_feedback",
        name=tr("entry.review_feedback.name", default="提交洛谷复习结果"),
        description=tr(
            "entry.review_feedback.description",
            default="记录一次复习结果并安排下次：forgot 回到第 1 档、shaky 原地重复、ok 进一档、easy 跳一档。",
        ),
        llm_result_fields=["summary"],
        timeout=10.0,
        input_schema={
            "type": "object",
            "properties": {
                "pid": {"type": "string", "description": "题目编号"},
                "feedback": {
                    "type": "string",
                    "enum": list(FEEDBACKS),
                    "default": FEEDBACK_OK,
                    "description": "复习结果：forgot / shaky / ok / easy",
                },
            },
            "required": ["pid"],
        },
    )
    async def review_feedback(self, pid: str = "", feedback: str = "", **_: object):
        target = pid.strip()
        if not target:
            return Err(SdkError("题目编号不能为空。", code="luogu_bad_request"))
        queue = await self._load_review_queue()
        item = queue.get(target)
        if item is None:
            return Err(SdkError("该题不在复习队列中，请先用 review_add 加入。", code="luogu_not_found"))
        grade = (feedback or FEEDBACK_OK).strip().lower()
        if grade not in FEEDBACKS:
            return Err(
                SdkError(
                    f"feedback 必须是 {'/'.join(FEEDBACKS)} 之一。",
                    code="luogu_bad_request",
                )
            )
        today = datetime.now(self._tz).date().isoformat()
        updated = apply_review_feedback(item, grade, today)
        queue[target] = updated
        await self._save_review_queue(queue)
        return Ok(
            {
                "status": "scheduled",
                "summary": f"{target} 复习已记录（{grade}）：第 {updated.stage + 1} 档，{updated.due_on} 再复习。",
                **self._review_to_dict(updated),
            }
        )

    @plugin_entry(
        id="set_push_target",
        name=tr("entry.set_push_target.name", default="配置每日题单推送目标"),
        description=tr("entry.set_push_target.description", default="配置每日题单推送的群号/QQ 号。"),
        llm_result_fields=["status"],
        timeout=10.0,
        input_schema={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["group", "private"]},
                "target": {"type": "string", "description": "群号或 QQ 号"},
                "enabled": {"type": "boolean", "default": True},
            },
            "required": ["channel", "target"],
        },
    )
    async def set_push_target(self, channel: str = "", target: str = "", enabled: bool = True, **_: object):
        if not channel or not target:
            return Err(SdkError("channel 和 target 不能为空。"))
        if channel not in ("group", "private"):
            return Err(SdkError("channel 必须是 group 或 private。"))
        item = {"channel": channel, "target": str(target), "enabled": bool(enabled)}
        current = await self._load_push_targets()
        # replace same channel+target, else append
        next_items = [t for t in current if not (t.get("channel") == channel and str(t.get("target")) == str(target))]
        next_items.append(item)
        await self.store.set("push_targets", next_items)
        return Ok({"status": "saved", "target": item})

    @plugin_entry(
        id="list_push_targets",
        name=tr("entry.list_push_targets.name", default="查看每日题单推送目标"),
        description=tr("entry.list_push_targets.description", default="读取已配置的推送目标。"),
        llm_result_fields=["targets"],
        timeout=10.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def list_push_targets(self, **_: object):
        targets = await self._load_push_targets()
        return Ok({"targets": targets})

    async def _load_push_targets(self) -> list[dict[str, Any]]:
        try:
            result = await self.store.get("push_targets", [])
            if hasattr(result, "is_ok") and callable(result.is_ok):
                if not result.is_ok():
                    return []
                value = result.value
            else:
                value = getattr(result, "value", result)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        except Exception:
            pass
        return []

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _problem_to_dict(problem: LuoguProblem) -> dict[str, Any]:
        return {
            "pid": problem.pid,
            "title": problem.title,
            "difficulty": problem.difficulty,
            "tags": list(tag_names(problem.tags)),
            "accepted": problem.accepted,
            "submitted": problem.submitted,
            "url": problem.url,
        }

    def _detail_to_payload(self, detail: LuoguProblem) -> dict[str, Any]:
        tags = '、'.join(tag_names(detail.tags)) or '无'
        summary = (
            f"{detail.pid} {detail.title}\n"
            f"难度 {detail.difficulty}｜标签：{tags}\n"
            f"通过 {detail.accepted} / 提交 {detail.submitted}｜题解 {detail.solutions}"
        )
        return {"summary": summary, "detail": self._problem_to_dict(detail)}

    def _build_search_summary(self, keyword: str, problems: list[LuoguProblem]) -> str:
        lines = [f'搜索 "{keyword}" 共 {len(problems)} 题：']
        lines += [f"{p.pid} {p.title}（难度 {p.difficulty}）" for p in problems[:10]]
        return "\n".join(lines)

    def _build_contest_summary(self, contests: list[ContestItem]) -> str:
        lines = [f"共 {len(contests)} 场比赛："]
        for contest in contests[:8]:
            lines.append(f"{contest.name}｜{contest.start_time}")
        return "\n".join(lines) if lines else "暂无比赛。"

    def _build_profile_summary(self, profile: dict[str, Any]) -> str:
        return (
            f"用户 {profile.get('name', profile.get('uid', '?'))}｜AC {profile.get('ac_count', 0)}"
            f"｜提交 {profile.get('submitted', 0)}｜rating {profile.get('rating', 0)}"
        )

    def _build_submissions_summary(self, submissions: list[SubmissionRecord]) -> str:
        lines = [f"共 {len(submissions)} 条记录："]
        for record in submissions[:10]:
            tags = '、'.join(tag_names(record.tags)[:3]) or '无'
            lines.append(f"{record.pid} {record.status}｜{tags}")
        return "\n".join(lines)

    def _sub_to_dict(self, record: SubmissionRecord) -> dict[str, Any]:
        return {
            "pid": record.pid,
            "title": record.title,
            "status": record.status,
            "difficulty": record.difficulty,
            "tags": list(tag_names(record.tags)),
            "solved": record.solved,
        }

    def _contest_to_dict(self, contest: ContestItem) -> dict[str, Any]:
        return {"name": contest.name, "start_time": contest.start_time, "link": contest.link}

    def _review_to_dict(self, item: ReviewItem) -> dict[str, Any]:
        return {
            "pid": item.pid,
            "title": item.title,
            "stage": item.stage,
            "interval_days": interval_days(item.stage),
            "due_on": item.due_on,
            "added_on": item.added_on,
            "last_reviewed_on": item.last_reviewed_on,
            "reviews": item.reviews,
            "lapses": item.lapses,
            "difficulty": item.difficulty,
            "tags": list(item.tags),
        }

    def _growth_to_payload(self, report, *, trend=None, ability=None, reviews_due: int = 0) -> dict[str, Any]:
        summary = (
            f"提交 {report.total_attempted}｜AC {report.ac_count}｜通过率 {report.ac_rate:.0%}"
            f"｜薄弱标签 {len(report.weak_tags)}｜未涉及标签 {len(report.untouched_tags_sorted)}"
        )
        if trend is not None and trend.total_attempts:
            summary += f"｜近 {len(trend.weeks)} 周趋势{_TREND_LABELS.get(trend.direction, trend.direction)}"
        if ability is not None:
            summary += f"｜估算能力档 {ability.level:.1f}/8"
        if reviews_due > 0:
            summary += f"｜复习到期 {reviews_due} 题"
        payload: dict[str, Any] = {
            "summary": summary,
            "suggestion": report.suggestion,
            "weak_tags": [{"tag": tag_name(w.tag), "attempts": w.attempts, "severity": w.severity} for w in report.weak_tags],
            "untouched_tags": list(tag_names(report.untouched_tags_sorted)),
            "recent_ac": [self._sub_to_dict(r) for r in report.recent_ac],
            "relative_weak_tags": [
                {
                    "tag": tag_name(stat.tag),
                    "attempts": stat.attempts,
                    "ac_count": stat.ac_count,
                    "ac_rate": stat.ac_rate,
                    "relative_gap": stat.relative_gap,
                }
                for stat in report.relative_weak_tags
            ],
            "difficulty_stats": [
                {
                    "difficulty": stat.difficulty,
                    "attempts": stat.attempts,
                    "ac_count": stat.ac_count,
                    "ac_rate": stat.ac_rate,
                    "relative_gap": stat.relative_gap,
                }
                for stat in report.difficulty_stats
            ],
        }
        if trend is not None:
            payload["trend"] = {
                "direction": trend.direction,
                "direction_label": _TREND_LABELS.get(trend.direction, trend.direction),
                "weeks": [
                    {
                        "week": week.week,
                        "start_date": week.start_date,
                        "attempts": week.attempts,
                        "ac_count": week.ac_count,
                        "ac_rate": week.ac_rate,
                        "active_days": week.active_days,
                    }
                    for week in trend.weeks
                ],
                "total_attempts": trend.total_attempts,
                "total_ac": trend.total_ac,
                "active_weeks": trend.active_weeks,
                "average_ac_rate": trend.average_ac_rate,
                "recent_ac": trend.recent_ac,
                "baseline_ac": trend.baseline_ac,
            }
        if ability is not None:
            payload["ability"] = {
                "level": ability.level,
                "base": ability.base,
                "adjustment": ability.adjustment,
                "window_days": ability.window_days,
                "ac_count": ability.ac_count,
                "ac_rate": ability.ac_rate,
                "confidence": ability.confidence,
                "recalibrated": ability.recalibrated,
                "previous_level": ability.previous_level,
                "reason": ability.reason,
            }
        payload["reviews_due"] = reviews_due
        return payload


__all__ = ["LuoguPlugin"]
