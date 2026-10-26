"""Curated, catgirl-friendly controls for BiliLearn.

Mounted on :class:`BiliLearnBridgePlugin` via ``include_router`` with the
prefix ``bililearn_``.

Deliberately small (43 entries): the host UI stays readable, while **full
coverage is preserved** through ``api_call``, which reaches every one of the
308 panel routes. ``api_groups`` / ``api_search`` / ``api_describe`` let the
catgirl discover what exists, and ``api_batch`` runs multi-step operations.

Layering:

* **通用透传** — ``api_call`` / ``api_batch`` / ``api_groups`` / ``api_search``
  / ``api_describe``: complete access to the whole BiliLearn API.
* **高频语义入口** — the handful of actions worth a stable name.

Every entry returns ``Ok(payload)`` on success and ``Err`` otherwise, so
failures surface as conversation instead of exceptions.
"""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, Ok, PluginRouter, SdkError, plugin_entry

# Panel endpoints whose values are constrained; anything else is rejected
# locally so a typo never reaches the panel as a confusing 400.
_BOT_MODES = ("smart", "current")
_LOG_SOURCES = ("all", "bot", "monitor", "reviews")


class BiliLearnDirector(PluginRouter):
    """Named entries covering BiliLearn's everyday operations."""

    # ------------------------------------------------------------------ #
    # Shared plumbing
    # ------------------------------------------------------------------ #
    async def _do(
        self,
        method: str,
        path: str,
        body: Any = None,
        action: str = "",
        *,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
    ):
        """Call the panel through the owning plugin and shape the result."""
        plugin = self.main_plugin
        res = await plugin._api_call(method, path, body=body, query=query, timeout=timeout)
        return plugin._shape(res, action or f"{method} {path}")

    # ══════════════════════════════════════════════════════════════════ #
    # 通用透传：一个入口覆盖全部 308 个接口
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(
        id="api_call",
        name="调用任意BiliLearn接口",
        description=(
            "直接调用 BiliLearn 面板的任意 HTTP 接口（共 308 个），这是控制全部功能的主入口。"
            "path 形如 /api/bot/start；GET 用 query 传参，写操作用 body 传 JSON 对象。"
            "不知道有哪些接口时，先用 api_groups 看分组、api_search 搜关键词、api_describe 看某组详情。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "接口路径，如 /api/info"},
                "method": {"type": "string", "description": "HTTP 方法，默认 GET", "default": "GET"},
                "body": {"type": "object", "description": "JSON 请求体（写操作使用）"},
                "query": {"type": "object", "description": "URL 查询参数（GET 使用）"},
            },
            "required": ["path"],
        },
        timeout=300.0,
    )
    async def api_call(self, path: str = "", method: str = "GET", body: Any = None, query: Any = None):
        if not path.strip():
            return Err(SdkError("path 不能为空，例如 /api/info。"))
        body_dict = body if isinstance(body, dict) else None
        query_dict = query if isinstance(query, dict) else None
        return await self._do(method.upper(), path.strip(), body_dict, f"调用 {method.upper()} {path}", query=query_dict)

    @plugin_entry(
        id="api_batch",
        name="批量调用接口",
        description=(
            "按顺序批量调用多个接口，一次完成多步操作。calls 为数组，每项形如 "
            "{\"path\": \"/api/bot/start\", \"method\": \"POST\", \"body\": {...}}，最多 20 个。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "calls": {
                    "type": "array",
                    "description": "调用列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "method": {"type": "string", "default": "GET"},
                            "body": {"type": "object"},
                            "query": {"type": "object"},
                        },
                        "required": ["path"],
                    },
                }
            },
            "required": ["calls"],
        },
        timeout=300.0,
    )
    async def api_batch(self, calls: Any = None):
        if not isinstance(calls, list) or not calls:
            return Err(SdkError("calls 必须是非空数组。"))
        if len(calls) > 20:
            return Err(SdkError("单次批量调用最多 20 个接口。"))
        plugin = self.main_plugin
        results = []
        for index, item in enumerate(calls):
            if not isinstance(item, dict) or not str(item.get("path") or "").strip():
                results.append({"index": index, "ok": False, "error": "缺少 path"})
                continue
            res = await plugin._api_call(
                str(item.get("method") or "GET").upper(),
                str(item["path"]).strip(),
                body=item.get("body") if isinstance(item.get("body"), dict) else None,
                query=item.get("query") if isinstance(item.get("query"), dict) else None,
            )
            results.append({
                "index": index,
                "path": item["path"],
                "ok": bool(res.get("ok")),
                "status": res.get("status"),
                "data": res.get("data"),
            })
        succeeded = sum(1 for r in results if r.get("ok"))
        return Ok({"total": len(results), "succeeded": succeeded, "results": results})

    @plugin_entry(
        id="api_groups",
        name="接口分组总览",
        description="列出 BiliLearn 全部接口分组（如 bot/kb/goal/agent）及各组接口数量，用于发现可用能力。",
    )
    async def api_groups(self):
        from . import catalog

        return Ok({
            "version": catalog.BILILEARN_VERSION,
            "total_routes": len(catalog.ROUTES),
            "group_count": len(catalog.GROUPS),
            "groups": [{"group": name, "count": len(catalog.GROUPS[name])} for name in catalog.group_names()],
        })

    @plugin_entry(
        id="api_search",
        name="搜索接口",
        description="按关键词搜索接口路径或分组，返回匹配接口及其可传参数。keyword 例如 bot / kb / goal / comment。",
    )
    async def api_search(self, keyword: str = ""):
        from . import catalog

        if not keyword.strip():
            return Err(SdkError("keyword 不能为空。"))
        hits = catalog.find(keyword)
        return Ok({"keyword": keyword, "count": len(hits), "routes": hits[:80]})

    @plugin_entry(
        id="api_describe",
        name="接口详情",
        description="查看某分组下全部接口及其可传参数。group 取自 api_groups 的结果（如 bot / kb / goal）。",
    )
    async def api_describe(self, group: str = ""):
        from . import catalog

        name = group.strip()
        if not name:
            return Err(SdkError("group 不能为空，可用 api_groups 查看。"))
        routes = catalog.routes_in(name)
        if not routes:
            near = [g for g in catalog.group_names() if name.lower() in g.lower()]
            return Err(SdkError(f"未知分组 {name!r}。相近分组：{near or catalog.group_names()[:12]}"))
        return Ok({"group": name, "count": len(routes), "routes": routes})

    # ══════════════════════════════════════════════════════════════════ #
    # 机器人主循环
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(
        id="bot_start",
        name="启动机器人",
        description="启动 B站 刷视频/学习机器人。mode=smart 智能模式（默认），mode=current 沿用当前配置。",
        timeout=30.0,
    )
    async def bot_start(self, mode: str = "smart"):
        mode = mode if mode in _BOT_MODES else "smart"
        return await self._do("POST", "/api/bot/start", {"mode": mode}, "启动机器人")

    @plugin_entry(id="bot_stop", name="停止机器人", description="停止机器人主循环。", timeout=30.0)
    async def bot_stop(self):
        return await self._do("POST", "/api/bot/stop", action="停止机器人")

    @plugin_entry(
        id="bot_status",
        name="机器人状态",
        description="查询综合状态：是否在跑、B站是否登录、当前人格、心情、待机/Agent 开关、运行时长等。",
    )
    async def bot_status(self):
        return await self._do("GET", "/api/info", action="查询机器人状态")

    @plugin_entry(id="bot_output", name="机器人日志", description="读取机器人近期输出。limit 默认 80，最大 5000。")
    async def bot_output(self, limit: int = 80):
        limit = max(1, min(int(limit or 80), 5000))
        return await self._do("GET", "/api/bot/output", action="读取机器人日志", query={"limit": limit})

    # ══════════════════════════════════════════════════════════════════ #
    # 实时监听 / 待机
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(id="monitor_start", name="启动实时监听", description="只盯私信与评论做实时 AI 回复，不刷视频。", timeout=30.0)
    async def monitor_start(self):
        return await self._do("POST", "/api/monitor/start", action="启动实时监听")

    @plugin_entry(id="monitor_stop", name="停止实时监听", description="停止实时监听进程。", timeout=30.0)
    async def monitor_stop(self):
        return await self._do("POST", "/api/monitor/stop", action="停止实时监听")

    @plugin_entry(id="monitor_status", name="实时监听状态", description="查询监听是否运行、配置与统计。")
    async def monitor_status(self):
        return await self._do("GET", "/api/monitor/status", action="查询监听状态")

    @plugin_entry(id="standby_start", name="启动待机模式", description="进入待机陪伴模式（回复评论/私信、可选总结）。", timeout=30.0)
    async def standby_start(self, settings: dict | None = None):
        return await self._do("POST", "/api/standby/start", settings or None, "启动待机模式")

    @plugin_entry(id="standby_stop", name="停止待机模式", description="退出待机模式。", timeout=30.0)
    async def standby_stop(self):
        return await self._do("POST", "/api/standby/stop", action="停止待机模式")

    # ══════════════════════════════════════════════════════════════════ #
    # B站账号
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(id="bili_qr_start", name="B站扫码登录", description="发起扫码登录，返回二维码地址（配合 bili_qr_status 查询进度）。", timeout=20.0)
    async def bili_qr_start(self):
        return await self._do("POST", "/api/bili/qr/start", action="发起扫码登录")

    # ══════════════════════════════════════════════════════════════════ #
    # 视频：理解 / 生成 / 互动
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(
        id="video_summarize",
        name="总结视频",
        description="对指定视频做 AI 理解/总结。给 url 或 bvid；intent 可填总结/问答等意图。",
        timeout=180.0,
    )
    async def video_summarize(self, url: str = "", bvid: str = "", intent: str = "总结", mode: str = "", platform: str = ""):
        body = {"url": url, "bvid": bvid, "intent": intent, "mode": mode, "platform": platform}
        return await self._do("POST", "/api/action/analyze-video", body, "总结视频")

    @plugin_entry(id="video_to_web", name="视频转网页讲解", description="把视频转成网页讲解动画（PPT 风格，可指定 theme）。", timeout=300.0)
    async def video_to_web(self, bvid: str = "", url: str = "", theme: str = ""):
        return await self._do("POST", "/api/action/video2web", {"bvid": bvid, "url": url, "theme": theme}, "视频转网页")

    @plugin_entry(id="video_mindmap", name="生成思维导图", description="为视频生成思维导图。", timeout=300.0)
    async def video_mindmap(self, bvid: str = "", url: str = "", title: str = ""):
        return await self._do("POST", "/api/action/mindmap", {"bvid": bvid, "url": url, "title": title}, "生成思维导图")

    @plugin_entry(id="send_danmaku", name="发送弹幕", description="向指定视频发送弹幕（bvid + text）。", timeout=30.0)
    async def send_danmaku(self, bvid: str = "", text: str = ""):
        if not bvid.strip() or not text.strip():
            return Err(SdkError("bvid 与 text 都不能为空。"))
        return await self._do("POST", "/api/action/send-danmaku", {"bvid": bvid, "text": text}, "发送弹幕")

    @plugin_entry(id="deep_dive", name="深度研习", description="围绕主题多章节深研并给出证据链式总结。", timeout=300.0)
    async def deep_dive(self, topic: str = "", video_count: int = 5, mode: str = "", sort_by: str = ""):
        body = {"topic": topic, "video_count": video_count, "mode": mode, "sort_by": sort_by}
        return await self._do("POST", "/api/action/deep-dive", body, "深度研习")

    # ══════════════════════════════════════════════════════════════════ #
    # 知识库
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(id="kb_stats", name="知识库统计", description="知识库规模与结构统计。")
    async def kb_stats(self):
        return await self._do("GET", "/api/kb/stats", action="查询知识库统计")

    @plugin_entry(id="kb_search", name="检索知识库", description="在知识库中语义检索（query）。")
    async def kb_search(self, query: str = ""):
        if not query.strip():
            return Err(SdkError("query 不能为空。"))
        return await self._do("POST", "/api/kb/custom-search", {"query": query}, "检索知识库")

    @plugin_entry(id="kb_tutor_chat", name="知识辅导对话", description="就知识库内容进行 AI 讲解/问答（message）。", timeout=180.0)
    async def kb_tutor_chat(self, message: str = "", context: str = ""):
        if not message.strip():
            return Err(SdkError("message 不能为空。"))
        return await self._do("POST", "/api/kb/tutor-chat", {"message": message, "context": context}, "知识辅导对话")

    # ══════════════════════════════════════════════════════════════════ #
    # 兴趣 / 搜索
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(id="interest_add", name="添加兴趣", description="添加一个感兴趣的关键词（keyword）。")
    async def interest_add(self, keyword: str = ""):
        if not keyword.strip():
            return Err(SdkError("keyword 不能为空。"))
        return await self._do("POST", "/api/interest-engine/interests", {"keyword": keyword}, "添加兴趣")

    @plugin_entry(id="search_trigger", name="触发搜索学习", description="让机器人就某主题主动搜索并学习（topic, count）。", timeout=300.0)
    async def search_trigger(self, topic: str = "", count: int = 3):
        if not topic.strip():
            return Err(SdkError("topic 不能为空。"))
        return await self._do("POST", "/api/search-history/trigger", {"topic": topic, "count": count}, "触发搜索学习")

    # ══════════════════════════════════════════════════════════════════ #
    # 学习目标
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(id="goal_status", name="学习目标状态", description="查看当前学习目标与进度。")
    async def goal_status(self):
        return await self._do("GET", "/api/goal", action="查看学习目标")

    @plugin_entry(id="goal_set", name="设置学习目标", description="设定学习目标（goal 或 topic 至少填一个）。", timeout=60.0)
    async def goal_set(self, goal: str = "", topic: str = ""):
        if not (goal.strip() or topic.strip()):
            return Err(SdkError("goal 或 topic 至少填一个。"))
        return await self._do("POST", "/api/goal/set", {"goal": goal, "topic": topic}, "设置学习目标")

    @plugin_entry(id="goal_stop", name="停止学习目标", description="停止当前学习目标。")
    async def goal_stop(self):
        return await self._do("POST", "/api/goal/stop", action="停止学习目标")

    # ══════════════════════════════════════════════════════════════════ #
    # Agent 自主执行
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(id="agent_start", name="启动Agent", description="让 Agent 自主规划并执行一个目标（goal）。", timeout=300.0)
    async def agent_start(self, goal: str = ""):
        if not goal.strip():
            return Err(SdkError("goal 不能为空。"))
        return await self._do("POST", "/api/agent/start", {"goal": goal}, "启动Agent")

    @plugin_entry(id="agent_stop", name="停止Agent", description="停止 Agent。", timeout=30.0)
    async def agent_stop(self):
        return await self._do("POST", "/api/agent/stop", action="停止Agent")

    # ══════════════════════════════════════════════════════════════════ #
    # 人格 / 心情
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(id="personas", name="人格列表", description="查看可用人格与当前激活人格。")
    async def personas(self):
        return await self._do("GET", "/api/personas", action="查看人格")

    @plugin_entry(id="persona_activate", name="切换人格", description="激活指定人格（name）。")
    async def persona_activate(self, name: str = ""):
        if not name.strip():
            return Err(SdkError("name 不能为空。"))
        return await self._do("POST", "/api/personas/activate", {"name": name}, "切换人格")

    @plugin_entry(id="mood_status", name="心情状态", description="查看 AI 当前心情与精力。")
    async def mood_status(self):
        return await self._do("GET", "/api/mood/status", action="查看心情")

    # ══════════════════════════════════════════════════════════════════ #
    # 日记 / 系统
    # ══════════════════════════════════════════════════════════════════ #
    @plugin_entry(id="diary", name="日记列表", description="查看机器人日记。")
    async def diary(self, limit: int = 30):
        return await self._do("GET", "/api/diary", action="查看日记", query={"limit": max(1, min(int(limit or 30), 500))})

    @plugin_entry(id="logs", name="系统日志", description="读取日志。source=all/bot/monitor/reviews，limit 默认 1200。")
    async def logs(self, source: str = "all", limit: int = 1200):
        src = source if source in _LOG_SOURCES else "all"
        return await self._do("GET", "/api/logs", action="读取日志", query={"source": src, "limit": max(1, min(int(limit or 1200), 5000))})

    @plugin_entry(id="config_get", name="读取配置", description="读取 BiliLearn 全量配置。")
    async def config_get(self):
        return await self._do("GET", "/api/config", action="读取配置")
