"""One brief run: collect -> render -> card -> speak -> log.

The runner owns no SDK state. It receives the plugin object and talks to the
narrow surface documented in the SDK (``push_message`` / ``ctx.create_card`` /
``report_status`` / ``logger``), which keeps it easy to fake in tests.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Sequence

import aiohttp

from .config import ParsedConfig, now_in
from .models import BriefItem, SourceConfig, SourceReport
from .pipeline import collect
from .render import (
    CARD_CSS,
    build_prompt,
    build_task_prompt,
    card_html,
    card_summary,
    digest_lines,
    template_spoken_line,
)
from .scheduler import due_slot, next_run_at
from .sources import build_sources
from .sources.base import BaseSource
from .sources.http import FetchError, HttpClient, resolve_proxy
from .store import Store

SOURCE = "neko_morning_brief"


class BriefRunner:
    def __init__(self, plugin: Any, parsed: ParsedConfig, store: Store) -> None:
        self.plugin = plugin
        self.parsed = parsed
        self.store = store
        self._running = False
        self._last_result: dict[str, Any] = {}

    # -- config ------------------------------------------------------------

    def update_config(self, parsed: ParsedConfig) -> None:
        self.parsed = parsed

    @property
    def logger(self):
        return getattr(self.plugin, "logger", None)

    # -- http --------------------------------------------------------------

    async def _with_sources(self, action):
        """Create a session + adapters for this call only.

        Timers, entries and tools may run on different event loops, so a single
        long-lived ``ClientSession`` would break. Sessions are cheap here.
        """

        async with aiohttp.ClientSession() as session:
            sources = build_sources(
                HttpClient(session, proxy=resolve_proxy(self.parsed.brief.proxy))
            )
            return await action(sources)

    async def _collect(self, sources: dict[str, BaseSource]) -> list[SourceReport]:
        cfg = self.parsed.brief
        try:
            return await asyncio.wait_for(
                collect(
                    self.parsed.sources,
                    sources,
                    self.store,
                    default_timeout=cfg.source_timeout_seconds,
                    concurrency=cfg.concurrency,
                    logger=self.logger,
                ),
                timeout=cfg.total_budget_seconds,
            )
        except asyncio.TimeoutError:
            self._log("WARN", "brief.budget_exceeded")
            return self._cached_reports()

    def _cached_reports(self) -> list[SourceReport]:
        reports: list[SourceReport] = []
        now = time.time()
        for cfg in self.parsed.sources:
            if not cfg.enabled:
                continue
            cached = self.store.cached_items(cfg.id, now=now)
            reports.append(
                SourceReport(
                    source_id=cfg.id,
                    label=cfg.display_label,
                    status="cached" if cached else "error",
                    items=list(cached or []),
                    fetched_at=now,
                    elapsed_ms=0,
                    error_code=None if cached else "NO_CACHE",
                )
            )
        return reports

    # -- diagnostics -------------------------------------------------------

    async def fetch_one(self, source_id: str, limit: int = 3) -> dict[str, Any]:
        """Direct fetch for the ``fetch_source`` tool and ``brief_test`` entry.

        Bypasses the dedupe window on purpose: when the user asks explicitly,
        repeating an item they already saw is correct behavior.
        """

        cfg = next((item for item in self.parsed.sources if item.id == source_id), None)
        if cfg is None:
            known = [item.id for item in self.parsed.sources]
            return {"ok": False, "error": "UNKNOWN_SOURCE", "known_ids": known}

        async def action(sources):
            adapter = sources.get(cfg.type)
            if adapter is None:
                return {"ok": False, "error": "CONFIG_INVALID"}
            try:
                items = await asyncio.wait_for(
                    adapter.fetch(cfg),
                    timeout=float(cfg.timeout or self.parsed.brief.source_timeout_seconds),
                )
            except asyncio.TimeoutError:
                return {"ok": False, "error": "TIMEOUT", "source_id": cfg.id}
            except FetchError as exc:
                return {"ok": False, "error": exc.code, "source_id": cfg.id}
            except Exception:  # noqa: BLE001
                if self.logger:
                    self.logger.exception("source.crash id={}", cfg.id)
                return {"ok": False, "error": "PARSE_ERROR", "source_id": cfg.id}
            rows = list(items)[: max(1, min(int(limit), 10))]
            return {
                "ok": True,
                "source_id": cfg.id,
                "label": cfg.display_label,
                "fetched_at": time.time(),
                "items": [self._item_payload(item) for item in rows],
            }

        return await self._with_sources(action)

    @staticmethod
    def _item_payload(item: BriefItem) -> dict[str, Any]:
        return {
            "title": item.title,
            "summary": item.summary,
            "when": _when(item),
            "url": item.url,
        }

    def source_catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for cfg in self.parsed.sources:
            meta = self.store.source_meta(cfg.id) or {}
            catalog.append(
                {
                    "id": cfg.id,
                    "label": cfg.display_label,
                    "status": "disabled" if not cfg.enabled else (meta.get("last_status") or "unknown"),
                    "desc": _describe(cfg),
                }
            )
        return catalog

    # -- main run ----------------------------------------------------------

    async def run(self, trigger: str, slot: str | None = None) -> dict[str, Any]:
        if self._running:
            return {"ok": False, "reason": "busy"}
        self._running = True
        started = time.monotonic()
        try:
            now_dt = now_in(self.parsed)
            cfg = self.parsed.brief
            prompt = build_task_prompt(cfg.instructions, now=now_dt, cfg=cfg)
            visibility = [] if cfg.prompt_visibility == "hidden" else ["chat"]
            pushed = self._push(prompt, visibility=visibility, ai_behavior="respond")
            if not pushed:
                await asyncio.sleep(3)
                pushed = self._push(prompt, visibility=visibility, ai_behavior="respond")
            if not pushed and self.logger:
                self.logger.warning("brief.push_rejected")
            self._finalize_task(now_dt, trigger, slot, pushed)
            return {
                "ok": True,
                "slot": slot,
                "trigger": trigger,
                "pushed": pushed,
                "tasks": len([line for line in cfg.instructions.splitlines() if line.strip()]),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        finally:
            self._running = False

    def _finalize_task(
        self,
        now_dt: datetime,
        trigger: str,
        slot: str | None,
        pushed: bool,
    ) -> None:
        day = now_dt.strftime("%Y-%m-%d")
        if slot:
            self.store.log_brief(day, slot, trigger=trigger, spoken_chars=0)
        self._last_result = {
            "state": "ok" if pushed else "degraded",
            "last_run_at": time.time(),
            "next_run_at": _timestamp(next_run_at(now_dt, self.parsed.brief.times)),
            "tasks": self.parsed.brief.instructions.count("\n") + 1
            if self.parsed.brief.instructions
            else 0,
        }
        report_status = getattr(self.plugin, "report_status", None)
        if report_status is not None:
            try:
                report_status(self._last_result)
            except Exception:  # noqa: BLE001
                pass
        self._log("INFO", f"brief.task_handed_to_model pushed={pushed}")

    async def _push_card(self, reports: Sequence[SourceReport], now_dt: datetime) -> None:
        create_card = getattr(getattr(self.plugin, "ctx", None), "create_card", None)
        if create_card is None:
            return
        try:
            await create_card(
                html=card_html(
                    reports,
                    now=now_dt,
                    max_items=self.parsed.brief.card_max_items,
                ),
                css=CARD_CSS,
                actions={"regen": {"entry": "brief_now", "args": {}}},
                summary=card_summary(reports, now=now_dt),
            )
        except Exception:  # noqa: BLE001 - card is optional; speech must go on
            if self.logger:
                self.logger.exception("brief.card_failed")

    async def _speak(self, reports: Sequence[SourceReport], now_dt: datetime) -> str:
        cfg = self.parsed.brief
        mode = cfg.mode
        failed = [report.label for report in reports if report.status == "error"]

        if mode == "pipeline":
            text = template_spoken_line(reports, now=time.time(), limit=cfg.spoken_char_limit)
        elif mode == "model":
            catalog = ", ".join(f"{item['id']}（{item['label']}）" for item in self.source_catalog())
            text = (
                f"（系统触发：现在是 {now_dt:%H:%M}，该做晨报了）\n\n"
                f"可用的信息源：{catalog}。\n"
                "请先调用 `fetch_source` 取你需要的源，再用你自己的语气挑 3~5 条最值得说的讲给用户听，"
                f"全文不超过 {cfg.spoken_char_limit} 字。"
            )
        else:
            digest = "\n".join(
                digest_lines(reports, now=time.time(), per_source=2)
            ) or "（今天没有取到任何内容）"
            text = build_prompt(
                digest,
                now=now_dt,
                cfg=cfg,
                failed_labels=failed,
            )

        visibility = [] if cfg.prompt_visibility == "hidden" else ["chat"]
        ok = self._push(text, visibility=visibility, ai_behavior="respond")
        if not ok:
            await asyncio.sleep(3)
            ok = self._push(text, visibility=visibility, ai_behavior="respond")
        if not ok and self.logger:
            self.logger.warning("brief.push_rejected")
        return text

    def _push(self, text: str, *, visibility: list[str], ai_behavior: str) -> bool:
        push = getattr(self.plugin, "push_message", None)
        if push is None:
            return False
        try:
            result = push(
                source=SOURCE,
                visibility=visibility,
                ai_behavior=ai_behavior,
                parts=[{"type": "text", "text": text}],
                priority=6,
            )
        except Exception:  # noqa: BLE001
            if self.logger:
                self.logger.exception("brief.push_failed")
            return False
        if isinstance(result, dict):
            return bool(result.get("submitted", True))
        return True

    def _finalize(
        self,
        reports: Sequence[SourceReport],
        now_dt: datetime,
        trigger: str,
        slot: str | None,
        spoken: str,
    ) -> None:
        ok_count = sum(1 for report in reports if report.items)
        fail_count = sum(1 for report in reports if report.status == "error")
        day = now_dt.strftime("%Y-%m-%d")
        if slot:
            self.store.log_brief(
                day,
                slot,
                trigger=trigger,
                spoken_chars=len(spoken or ""),
                ok_count=ok_count,
                fail_count=fail_count,
            )

        status = "ok" if ok_count else "degraded"
        self._last_result = {
            "state": status,
            "last_run_at": time.time(),
            "next_run_at": _timestamp(next_run_at(now_dt, self.parsed.brief.times)),
            "sources": {
                "ok": ok_count,
                "cached": sum(1 for report in reports if report.status == "cached"),
                "error": fail_count,
            },
            "last_errors": [
                f"{report.source_id}: {report.error_code}"
                for report in reports
                if report.error_code
            ][:5],
        }
        report_status = getattr(self.plugin, "report_status", None)
        if report_status is not None:
            try:
                report_status(self._last_result)
            except Exception:  # noqa: BLE001
                pass
        self._log("INFO", f"brief.done items={sum(len(r.items) for r in reports)} chars={len(spoken or '')}")

    def status(self) -> dict[str, Any]:
        return dict(self._last_result)

    # -- scheduling --------------------------------------------------------

    def due(self, now_dt: datetime | None = None) -> str | None:
        moment = now_dt or now_in(self.parsed)
        return due_slot(
            moment,
            self.parsed.brief.times,
            self.parsed.brief.catch_up_minutes,
            self.store.has_run,
        )

    def _log(self, level: str, message: str) -> None:
        logger = self.logger
        if logger is None:
            return
        getattr(logger, level.lower(), logger.info)(message)


def _when(item: BriefItem) -> str:
    from .normalize import relative_time

    return relative_time(item.ts, time.time())


def _timestamp(value: datetime | None) -> float | None:
    return value.timestamp() if value else None


def _describe(cfg: SourceConfig) -> str:
    if cfg.type == "builtin.weather":
        params = cfg.params or {}
        return f"天气（{params.get('city') or '坐标'}）"
    host = _safe_host(cfg.url)
    if cfg.type == "rss":
        return f"RSS（{host}）"
    if cfg.mode == "list":
        return f"JSON 列表（{host}）"
    return f"JSON 单值（{host}）"


def _safe_host(url: str | None) -> str:
    """Host only: query strings can carry tokens and must not reach the model."""

    if not url:
        return "未配置"
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).netloc or "未配置"
    except ValueError:
        return "未配置"
