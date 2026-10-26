"""照片语录 —— N.E.K.O 插件。

从内置 WebDAV 图库随机抽一张照片语录发送到聊天：普通款为主，隐藏款小概率掉落。
图库地址与只读凭据已内置锁定，对用户不可见也不可修改。
核心逻辑见 core.py，图库访问见 fetchers.py。
"""

from __future__ import annotations

import asyncio
import base64 as _b64
import time

from . import fetchers
from .core import choose_pool, mime_for, pick

try:  # N.E.K.O 宿主内：使用真实 SDK
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

    SDK_AVAILABLE = True
except ModuleNotFoundError:  # 独立环境（静态检查/单测）：SDK 缺席时退化为空实现
    SDK_AVAILABLE = False

    def neko_plugin(cls):
        return cls

    class NekoPluginBase:  # noqa: D101 —— 占位基类
        def __init__(self, ctx=None):
            self.ctx = ctx

    class Ok:
        def __init__(self, value):
            self.value = value

    class Err:
        def __init__(self, error):
            self.error = error

    class SdkError(Exception):
        pass

    def _passthrough_decorator(**_kwargs):
        def decorator(fn):
            return fn

        return decorator

    plugin_entry = lifecycle = timer_interval = llm_tool = _passthrough_decorator

# ---- 内置图库（只读凭据，公开分发用）----

def _d(text: str) -> str:
    return _b64.b64decode(text).decode("utf-8")


_BUILTIN_LIBRARY = {
    "webdav_url": _d("aHR0cDovLzguMTUyLjAuMTg2OjQwMDMzL2Rhdg=="),
    "webdav_username": _d("NzkxNDMzNDQzQHFxLmNvbQ=="),
    # 只读凭据：仅可列目录与下载，无法写入/删除（公开分发用）
    "webdav_password": _d("ZjNleXV0ZHFqandxaDFwZ2x0azNlM3owZWhodmJhdDM="),
    "normal_folder": "/memes/",
    "hidden_folder": "/memes_hidden/",
}

_INLINE_LIMIT = 500_000  # 小图直接内联；更大的图走宿主临时上传


@neko_plugin
class PhotoQuotePlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self._normal: list[str] = []
        self._hidden: list[str] = []
        self._hist_normal: list[str] = []
        self._hist_hidden: list[str] = []
        self._listed = False
        self._last_refresh = 0.0
        self._rate = 0.05
        self._no_repeat = 20
        self._auto_refresh = True
        self._refresh_interval = 600.0

    # ---- 配置与图库 ----

    async def _reload_config(self):
        cfg = await self.config.dump()
        draw_cfg = cfg.get("draw") or {}
        try:
            rate = float(draw_cfg.get("hidden_rate", 0.05))
        except (TypeError, ValueError):
            rate = 0.05
        self._rate = min(max(rate, 0.0), 1.0)
        try:
            self._no_repeat = max(0, int(draw_cfg.get("no_repeat_count", 20)))
        except (TypeError, ValueError):
            self._no_repeat = 20
        self._auto_refresh = bool(draw_cfg.get("auto_refresh", True))
        try:
            minutes = float(draw_cfg.get("refresh_interval_minutes", 10))
        except (TypeError, ValueError):
            minutes = 10.0
        self._refresh_interval = max(60.0, minutes * 60.0)

    def _cfg(self) -> dict:
        return dict(_BUILTIN_LIBRARY)

    async def _refresh_list(self, force: bool = False):
        now = time.monotonic()
        if self._listed and not force and now - self._last_refresh < self._refresh_interval:
            return
        cfg = self._cfg()
        normal = await asyncio.to_thread(
            fetchers.list_images, cfg, _BUILTIN_LIBRARY["normal_folder"]
        )
        hidden = await asyncio.to_thread(
            fetchers.list_images, cfg, _BUILTIN_LIBRARY["hidden_folder"]
        )
        self._normal, self._hidden = normal, hidden
        self._listed = True
        self._last_refresh = now

    def _redact(self, text: str) -> str:
        return fetchers.redact(text, self._cfg())

    # ---- 抽图 ----

    async def _draw_and_push(self) -> dict | None:
        await self._refresh_list()
        pool, is_hidden = choose_pool(self._normal, self._hidden, self._rate)
        history = self._hist_hidden if is_hidden else self._hist_normal
        path = pick(pool, history, self._no_repeat)
        if path is None:
            return None
        data = await asyncio.to_thread(fetchers.fetch_image, self._cfg(), path)
        mime = mime_for(path)
        if len(data) <= _INLINE_LIMIT or not hasattr(self.ctx, "images"):
            part: dict = {"type": "image", "data": data, "mime": mime}
        else:  # 大图先交给宿主临时上传，失败则退回内联
            try:
                part = await self.ctx.images.upload(data, mime=mime)
            except Exception as e:  # noqa: BLE001 —— 上传不可用时必须兜底
                self.logger.warning("宿主图片上传失败，回退内联：{}", e)
                part = {"type": "image", "data": data, "mime": mime}
        push_result = self.push_message(
            source="quote_extract",
            visibility=["chat"],
            ai_behavior="blind",
            parts=[part],
            priority=4,
        )
        submitted = True
        if isinstance(push_result, dict):
            submitted = bool(push_result.get("submitted", push_result.get("ok", True)))
            if not submitted:
                self.logger.warning("图片投递被拒绝：{}", push_result.get("reason", ""))
        return {
            "file": path.rsplit("/", 1)[-1],
            "hidden": is_hidden,
            "total_normal": len(self._normal),
            "total_hidden": len(self._hidden),
            "submitted": submitted,
        }

    # ---- 生命周期 ----

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._reload_config()
        try:
            await self._refresh_list(force=True)
            self.logger.info(
                "照片语录已启动：普通款 {} 张，隐藏款 {} 张",
                len(self._normal), len(self._hidden),
            )
        except fetchers.FetchError as e:
            self.logger.warning("图库列表首次加载失败：{}", self._redact(str(e)))
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config, new_config, mode):
        await self._reload_config()
        return Ok({"status": "config_updated"})

    # ---- 入口 ----

    @plugin_entry(
        id="draw",
        name="抽语录",
        description="随机抽一张照片语录发送到聊天，小概率抽中隐藏款",
        timeout=30.0,
    )
    async def draw(self):
        try:
            info = await self._draw_and_push()
        except fetchers.FetchError as e:
            return Err(SdkError(self._redact(str(e))))
        if info is None:
            return Err(SdkError("语录库暂时是空的，请稍后再试"))
        return Ok({"message": "🎉 恭喜抽中隐藏款！" if info["hidden"] else "抽取成功", **info})

    @plugin_entry(id="refresh", name="刷新图库", description="重新读取内置图库的图片列表", timeout=30.0)
    async def refresh(self):
        try:
            await self._refresh_list(force=True)
        except fetchers.FetchError as e:
            return Err(SdkError(self._redact(str(e))))
        return Ok({"message": "图库已刷新", "normal": len(self._normal), "hidden": len(self._hidden)})

    @plugin_entry(id="stats", name="图库统计", description="查看普通款与隐藏款数量及当前掉率")
    async def stats(self):
        try:
            await self._refresh_list()
        except fetchers.FetchError as e:
            return Err(SdkError(self._redact(str(e))))
        return Ok({
            "normal": len(self._normal),
            "hidden": len(self._hidden),
            "hidden_rate": self._rate,
        })

    # ---- 定时刷新 ----

    @timer_interval(id="refresh_images", seconds=300, name="自动刷新图库", auto_start=True)
    async def auto_refresh(self, **_):
        if not self._auto_refresh or not self._listed:
            return Ok({"skipped": "disabled"})
        if time.monotonic() - self._last_refresh < self._refresh_interval:
            return Ok({"skipped": "interval"})
        try:
            await self._refresh_list(force=True)
        except fetchers.FetchError as e:
            self.logger.warning("图库自动刷新失败：{}", self._redact(str(e)))
            return Ok({"skipped": "error"})
        return Ok({"normal": len(self._normal), "hidden": len(self._hidden)})

    # ---- 对话期 LLM 工具 ----

    @llm_tool(
        name="send_meme",
        description="随机发送一张照片语录到聊天，让回复更生动有趣；小概率发出稀有隐藏款。",
        parameters={"type": "object", "properties": {}},
    )
    async def send_meme(self):
        try:
            info = await self._draw_and_push()
        except fetchers.FetchError as e:
            return {"sent": False, "reason": self._redact(str(e))}
        if info is None:
            return {"sent": False, "reason": "图库为空"}
        return {"sent": bool(info["submitted"]), "hidden": info["hidden"]}
