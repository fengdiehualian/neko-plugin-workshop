"""猫娘晨报 - source-agnostic morning digest for N.E.K.O.

The plugin is deliberately thin: sources fetch, the pipeline normalizes, and
the character does the talking. Anything user-facing that can fail (a dead
feed, a slow API) degrades to a single grey line instead of breaking the run.

Design notes live in ``猫娘晨报-设计方案.md`` next to this plugin.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

from .config import now_in, parse_config
from .runner import BriefRunner
from .scheduler import next_run_at
from .sources.http import FetchError
from .store import Store

SOURCE = "neko_morning_brief"


@neko_plugin
class NekoMorningBriefPlugin(NekoPluginBase):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._parsed = None
        self._store: Store | None = None
        self._runner: BriefRunner | None = None
        self._catch_up_task: asyncio.Task | None = None
        self._started_at = 0.0

    # -- lifecycle ---------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._load_config()
        assert self._parsed is not None
        try:
            self._store = Store(self.data_path("brief.db"))
            purged = self._store.purge_seen()
        except Exception as exc:  # noqa: BLE001
            self.logger.error("brief.store_failed {}", type(exc).__name__)
            return Err(SdkError(f"cannot open brief database: {exc}"))

        self._runner = BriefRunner(self, self._parsed, self._store)
        self._started_at = time.time()
        for error in self._parsed.errors:
            self.logger.warning("brief.config_warning {}", error.get("message", ""))
        self.logger.info(
            "brief.ready sources={} times={} mode={}",
            len(self._parsed.sources),
            ",".join(self._parsed.brief.times),
            self._parsed.brief.mode,
        )
        if purged:
            self.logger.info("brief.seen_purged rows={}", purged)

        # Do not block host startup; catch-up runs once the process is quiescent.
        self._catch_up_task = asyncio.create_task(self._catch_up())
        return Ok(
            {
                "status": "ready",
                "sources": len(self._parsed.sources),
                "times": list(self._parsed.brief.times),
                "mode": self._parsed.brief.mode,
            }
        )

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        if self._catch_up_task and not self._catch_up_task.done():
            self._catch_up_task.cancel()
        if self._store is not None:
            self._store.close()
            self._store = None
        self._runner = None
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config=None, new_config=None, mode=None, **_):
        await self._load_config()
        if self._runner is not None and self._parsed is not None:
            self._runner.update_config(self._parsed)
        assert self._parsed is not None
        return Ok(
            {
                "sources": len(self._parsed.sources),
                "times": list(self._parsed.brief.times),
                "errors": len(self._parsed.errors),
            }
        )

    async def _load_config(self) -> None:
        raw = await self.config.dump()
        self._parsed = parse_config(raw)

    async def _catch_up(self) -> None:
        await asyncio.sleep(3)
        if self._runner is None:
            return
        try:
            slot = self._runner.due()
        except Exception:  # noqa: BLE001
            self.logger.exception("brief.catch_up_check_failed")
            return
        if not slot:
            return
        self.logger.info("brief.catch_up slot={}", slot)
        try:
            await self._runner.run(trigger="schedule", slot=slot)
        except Exception:  # noqa: BLE001
            self.logger.exception("brief.catch_up_failed")

    # -- timer -------------------------------------------------------------

    @timer_interval(id="brief_tick", seconds=30, auto_start=True)
    async def brief_tick(self, **_):
        if self._runner is None or self._parsed is None:
            return Ok({"skipped": "not_ready"})
        slot = self._runner.due()
        if not slot:
            return Ok({"skipped": True})
        try:
            result = await self._runner.run(trigger="schedule", slot=slot)
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("brief.tick_failed")
            return Err(SdkError(f"brief run failed: {exc}"))
        return Ok(result)

    # -- entries -----------------------------------------------------------

    @plugin_entry(
        id="brief_now",
        name="立即生成晨报",
        description="马上抓取所有启用的信息源，推送卡片并让猫娘播报一次。",
        timeout=60.0,
    )
    async def brief_now(self, **_):
        if self._runner is None:
            return Err(SdkError("plugin is still starting"))
        result = await self._runner.run(trigger="manual", slot="manual")
        if not result.get("ok"):
            return Err(SdkError(str(result.get("reason") or "brief busy")))
        return Ok(result)

    @plugin_entry(
        id="brief_test",
        name="测试抓取",
        description="抓取一个网址并返回精简结果，用于确认链接是否可用，不会播报。",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的网址"},
                "cookie": {"type": "string", "description": "可选 Cookie"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
            },
            "required": ["url"],
        },
        timeout=40.0,
    )
    async def brief_test(self, url: str = "", cookie: str = "", limit: int = 3, **_):
        if self._runner is None:
            return Err(SdkError("plugin is still starting"))
        if not url:
            return Err(SdkError("url is required"))
        try:
            result = await asyncio.wait_for(
                self._fetch_url(url, cookie, limit), timeout=35.0
            )
        except asyncio.TimeoutError:
            return Err(SdkError("TIMEOUT"))
        except FetchError as exc:
            return Err(SdkError(exc.code))
        return Ok(result)

    @plugin_entry(
        id="brief_doctor",
        name="晨报自检",
        description="检查配置、数据库与各源最近一次的状态。",
        timeout=15.0,
    )
    async def brief_doctor(self, **_):
        if self._runner is None or self._parsed is None or self._store is None:
            return Ok({"ready": False})
        now_dt = now_in(self._parsed)
        nxt = next_run_at(now_dt, self._parsed.brief.times)
        runtime_config = getattr(self, "runtime_config_path", None)
        return Ok(
            {
                "ready": True,
                "db_path": str(self.data_path("brief.db")),
                "runtime_config": str(runtime_config) if runtime_config else None,
                "times": list(self._parsed.brief.times),
                "next_run_at": nxt.timestamp() if nxt else None,
                "instructions": self._parsed.brief.instructions,
                "config_errors": list(self._parsed.errors),
                "last_run": self._store.last_run(),
                "last_result": self._runner.status(),
            }
        )

    # -- llm tools ---------------------------------------------------------

    @llm_tool(
        name="list_sources",
        description=(
            "查看用户给晨报设置的信息需求原文和播报时间。"
            "用户问“我的晨报都看些什么”时调用。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=10.0,
    )
    async def list_sources(self, **_: Any) -> dict:
        if self._parsed is None:
            return {"error": "PLUGIN_STARTING", "brief_times": [], "instructions": ""}
        return {
            "brief_times": list(self._parsed.brief.times),
            "instructions": self._parsed.brief.instructions,
        }

    @llm_tool(
        name="fetch_url",
        description=(
            "按用户需求抓取一个网址，返回可直接引用的纯文本（标题/摘要/链接）。"
            "只负责取数据，不负责总结。用户需求里写了 cookie 的，把 cookie 传进来。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的 http(s) 网址"},
                "cookie": {"type": "string", "description": "可选 Cookie 请求头"},
                "label": {
                    "type": "string",
                    "description": "可选地点或名称，例如杭州；天气类数据会用它作标题",
                },
                "max_items": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["url"],
        },
        timeout=30.0,
    )
    async def fetch_url(
        self,
        *,
        url: str = "",
        cookie: str = "",
        label: str = "",
        max_items: int = 5,
        **_: Any,
    ) -> dict:
        if not url:
            return {
                "output": {"error": "MISSING_URL"},
                "is_error": True,
                "error": "MISSING_URL",
            }
        try:
            result = await self._fetch_url(url, cookie, max_items, label)
        except FetchError as exc:
            return {
                "output": {"error": exc.code, "url": url},
                "is_error": True,
                "error": exc.code,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "output": {"error": "FETCH_FAILED", "detail": type(exc).__name__},
                "is_error": True,
                "error": "FETCH_FAILED",
            }
        from .fetch import render_result

        return render_result(result)

    async def _fetch_url(
        self, url: str, cookie: str, max_items: int, label: str = ""
    ) -> dict:
        import aiohttp

        from .fetch import fetch_content, require_url
        from .sources.http import HttpClient, resolve_proxy

        target = require_url(url)
        proxy = resolve_proxy(self._parsed.brief.proxy if self._parsed else "auto")
        async with aiohttp.ClientSession() as session:
            return await fetch_content(
                HttpClient(session, proxy=proxy),
                target,
                cookie=cookie,
                max_items=max_items,
                label=label,
            )
