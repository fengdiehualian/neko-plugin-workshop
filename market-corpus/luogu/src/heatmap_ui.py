"""Hosted-UI surface for the practice heatmap.

Kept apart from the plugin body for the same reason the credential UI is: the
panel only ever needs a snapshot, and the plugin's query entries stay free of UI
concerns.

The two surfaces are split by cost on purpose. A context provider must answer
inside the host's ~5 second budget, while building the snapshot means fetching
submission pages (and possibly backfilling problem metadata), so the context only
reads the cache and says whether it has one. The refresh entry — also an ordinary
plugin entry, so the AI can call it — does the slow work and writes the cache
back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui

from ._client import LuoguBlockedError
from ._parsing import LuoguAuthRequiredError
from ._trend import daily_ac_counts, heatmap_grid, heatmap_levels, streak_days

HEATMAP_CACHE_KEY = "heatmap_cache"
HEATMAP_VERSION = 1
# A year of cells plus the days needed to align the first column to a Monday.
HEATMAP_DAYS = 371
MIN_HEATMAP_DAYS = 7
MAX_HEATMAP_DAYS = 730


class LuoguHeatmapUiMixin:
    """Heatmap panel: a cache-only context plus one slow refresh entry.

    Expects the host class to provide ``_luogu_cookies``, ``_tz``, ``logger``,
    ``i18n``, ``_auth_gate``, ``_fetch_submissions``, ``_safe_err`` and the
    plugin-store helpers ``_store_get`` / ``_store_set``.
    """

    _luogu_cookies: dict[str, str]
    _tz: ZoneInfo
    logger: Any
    i18n: Any
    _auth_gate: Any
    _fetch_submissions: Any
    _safe_err: Any
    _store_get: Any
    _store_set: Any

    async def _load_heatmap_cache(self) -> dict[str, Any] | None:
        raw = await self._store_get(HEATMAP_CACHE_KEY, None)
        if not isinstance(raw, dict) or raw.get("v") != HEATMAP_VERSION:
            return None
        payload = raw.get("payload")
        return dict(payload) if isinstance(payload, dict) else None

    def _clamp_days(self, days: int) -> int:
        """Requested window, clamped to something a heatmap can render."""
        try:
            value = int(days)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            return HEATMAP_DAYS
        return max(MIN_HEATMAP_DAYS, min(MAX_HEATMAP_DAYS, value))

    async def _build_heatmap_payload(self, days: int) -> dict[str, Any]:
        """Fetch submissions, reduce them to per-day counts, cache the result."""
        today = datetime.now(self._tz).date()
        submissions = await self._fetch_submissions()
        counts = daily_ac_counts(submissions, tz=self._tz, days=days, today=today)
        levels = heatmap_levels(counts)
        current_streak, longest_streak = streak_days(counts, today)
        best_day, best_count = max(counts.items(), key=lambda item: item[1]) if counts else ("", 0)
        payload: dict[str, Any] = {
            "days": days,
            "today": today.isoformat(),
            "total_ac": sum(counts.values()),
            "active_days": len(counts),
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "best_day": {"date": best_day, "count": best_count},
            "level_counts": {
                str(level): sum(1 for value in levels.values() if value == level) for level in range(5)
            },
            "grid": heatmap_grid(counts, today=today, days=days, levels=levels),
            "built_at": datetime.now(self._tz).isoformat(timespec="seconds"),
        }
        await self._store_set(HEATMAP_CACHE_KEY, {"v": HEATMAP_VERSION, "payload": payload})
        return payload

    @ui.context(id="heatmap", title=tr("panel.heatmap.title", default="刷题热力图"))
    async def get_heatmap_ui_context(self) -> dict[str, object]:
        """The cached snapshot only — the host gives a provider about 5 seconds."""
        cached = await self._load_heatmap_cache()
        return {
            "configured": bool(self._luogu_cookies),
            "cached": cached is not None,
            "payload": cached or {},
            "storage": "plugin_store",
        }

    @ui.action(
        id="refresh_heatmap",
        label=tr("actions.refresh_heatmap.label", default="刷新热力图"),
        icon="🔥",
        tone="default",
        group="heatmap",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="refresh_heatmap",
        name=tr("entry.refresh_heatmap.name", default="刷新洛谷刷题热力图"),
        description=tr(
            "entry.refresh_heatmap.description",
            default="抓取提交记录并重算刷题热力图（每日 AC 按题去重），结果缓存供 Hosted UI 展示。",
        ),
        llm_result_fields=["summary"],
        timeout=60.0,
        input_schema={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "default": 0,
                    "description": "统计天数，0 表示默认约一年（371 天）",
                }
            },
        },
    )
    async def refresh_heatmap(self, days: int = 0, **_: object):
        try:
            self._auth_gate()
            payload = await self._build_heatmap_payload(self._clamp_days(days))
        except LuoguAuthRequiredError:
            return Err(SdkError("该操作需要洛谷登录态。", code="luogu_auth_required"))
        except LuoguBlockedError:
            return Err(SdkError("洛谷触发验证，请稍后重试。", code="luogu_blocked"))
        except Exception as exc:
            self.logger.warning("refresh_heatmap failed: {}", self._safe_err(exc))
            return Err(SdkError("刷新热力图失败。", code="luogu_error"))
        summary = (
            f"最近 {payload['days']} 天：AC 去重 {payload['total_ac']} 题｜活跃 {payload['active_days']} 天"
            f"｜当前连续 {payload['current_streak']} 天（最长 {payload['longest_streak']} 天）"
        )
        return Ok({"summary": summary, **payload})


__all__ = ["HEATMAP_CACHE_KEY", "HEATMAP_DAYS", "HEATMAP_VERSION", "LuoguHeatmapUiMixin"]
