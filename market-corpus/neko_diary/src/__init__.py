"""
猫娘日记插件 v0.1 (Neko Diary)

让猫娘能记录每天的小事，标记心情，像回忆小盒子一样珍藏每一天。

数据模型:
  - DiaryEntry: 日记条目（内容、心情、附件、标签）
  - MoodRecord: 心情记录
  - DiaryStats: 统计信息

LLM 工具:
  - neko_diary_write: 写一篇日记
  - neko_diary_browse: 浏览日记时间线
  - neko_diary_search: 搜索日记
  - neko_diary_get_mood_stats: 获取心情统计
  - neko_diary_get_today: 获取今天的日记
  - neko_diary_throwback: 历史上的今天
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

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

from .plugin import NekoDiaryPlugin

# ── 常量 ──────────────────────────────────────────────────────────────

_PLUGIN_ID = "neko_diary"

# 心情类型
MOOD_TYPES = {
    "happy": {"label": "开心", "emoji": "😊", "color": "#FFD700"},
    "sad": {"label": "难过", "emoji": "😢", "color": "#6B8E9B"},
    "angry": {"label": "生气", "emoji": "😠", "color": "#CD5C5C"},
    "anxious": {"label": "焦虑", "emoji": "😰", "color": "#9370DB"},
    "calm": {"label": "平静", "emoji": "😌", "color": "#87CEEB"},
    "excited": {"label": "兴奋", "emoji": "🤩", "color": "#FF69B4"},
    "tired": {"label": "疲惫", "emoji": "😴", "color": "#A9A9A9"},
    "neutral": {"label": "一般", "emoji": "😐", "color": "#D3D3D3"},
}


# ── 插件主类 ──────────────────────────────────────────────────────────

@neko_plugin
class NekoDiaryPluginEntry(NekoPluginBase):
    """猫娘日记插件 - N.E.K.O 插件入口"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = threading.Lock()
        self._diary_plugin: Optional[NekoDiaryPlugin] = None

    # ── lifecycle ────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        """启动插件,加载配置"""
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        section = cfg.get("neko_diary") if isinstance(cfg.get("neko_diary"), dict) else {}

        # 读取配置
        db_path = str(section.get("db_path", "")).strip()
        master_name = str(section.get("master_name", "主人")).strip()
        catgirl_name = str(section.get("catgirl_name", "喵喵")).strip()
        default_mood = str(section.get("default_mood", "neutral")).strip()
        page_size = int(section.get("page_size", 20))

        # 验证心情类型
        if default_mood not in MOOD_TYPES:
            default_mood = "neutral"

        try:
            self._diary_plugin = NekoDiaryPlugin(
                db_path=db_path,
                master_name=master_name,
                catgirl_name=catgirl_name,
                default_mood=default_mood,
                page_size=page_size,
            )
            self._diary_plugin.initialize()
        except Exception as e:
            self.logger.error("初始化日记插件失败: {}", e)
            return Err(SdkError(f"初始化日记插件失败: {e}"))

        self.logger.info(
            "NekoDiary started: master_name={}, catgirl_name={}, default_mood={}",
            master_name, catgirl_name, default_mood,
        )
        
        return Ok({"status": "running", "version": "0.1.0"})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        """关闭插件"""
        if self._diary_plugin:
            self._diary_plugin.close()
        self.logger.info("NekoDiary shutdown")
        return Ok({"status": "shutdown"})

    # ── 辅助方法 ─────────────────────────────────────────────────────

    def _get_plugin(self) -> NekoDiaryPlugin:
        """获取日记插件实例"""
        if self._diary_plugin is None:
            raise RuntimeError("日记插件未初始化,请检查配置")
        return self._diary_plugin

    # ── LLM 工具 ─────────────────────────────────────────────────────

    @llm_tool(
        name="neko_diary_write",
        description="写一篇日记。记录今天发生的小事，可以标记心情、添加标签、附带图片。支持设置可见性（公开/仅自己可见）。",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "日记内容"},
                "title": {"type": "string", "description": "日记标题（可选）"},
                "mood": {"type": "string", "enum": list(MOOD_TYPES.keys()), "description": "心情标记"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                "attachments": {"type": "array", "items": {"type": "string"}, "description": "附件路径列表（图片等）"},
                "visibility": {"type": "string", "enum": ["public", "private"], "description": "可见性：public=公开，private=仅自己可见（默认public）"},
            },
            "required": ["content"],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="write",
        name="写日记",
        description="写一篇日记，记录今天的小事",
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "title": {"type": "string"},
                "mood": {"type": "string", "enum": list(MOOD_TYPES.keys())},
                "tags": {"type": "array", "items": {"type": "string"}},
                "attachments": {"type": "array", "items": {"type": "string"}},
                "visibility": {"type": "string", "enum": ["public", "private"]},
            },
            "required": ["content"],
        },
        llm_result_fields=["entry_id", "date", "mood", "visibility"],
    )
    async def write(
        self,
        content: str,
        title: Optional[str] = None,
        mood: Optional[str] = None,
        tags: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
        visibility: str = "public",
        **_,
    ):
        """写一篇日记"""
        try:
            plugin = self._get_plugin()
            result = plugin.write_entry(
                content=content,
                title=title,
                mood=mood,
                tags=tags,
                attachments=attachments,
                visibility=visibility,
            )
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"写日记失败: {e}"))

    @llm_tool(
        name="neko_diary_browse",
        description="浏览日记时间线。可以按日期范围查看，支持分页，支持按可见性过滤（public/private/全部）。",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD（可选）"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD（可选）"},
                "limit": {"type": "integer", "description": "每页数量，默认20"},
                "offset": {"type": "integer", "description": "偏移量，用于加载更多"},
                "visibility": {"type": "string", "enum": ["public", "private"], "description": "可见性过滤：public=仅公开，private=仅私有，留空=全部"},
            },
            "required": [],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="browse",
        name="浏览日记",
        description="浏览日记时间线",
        input_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "visibility": {"type": "string", "enum": ["public", "private"]},
            },
            "required": [],
        },
        llm_result_fields=["entries", "total", "offset", "count"],
    )
    async def browse(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        visibility: Optional[str] = None,
        **_,
    ):
        """浏览日记时间线"""
        try:
            plugin = self._get_plugin()
            result = plugin.browse_entries(
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                offset=offset,
                visibility=visibility,
            )
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"浏览日记失败: {e}"))

    @llm_tool(
        name="neko_diary_search",
        description="搜索日记。可以按关键词搜索内容、标题、标签。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "description": "返回数量，默认20"},
            },
            "required": ["keyword"],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="search",
        name="搜索日记",
        description="搜索日记内容",
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["keyword"],
        },
        llm_result_fields=["entries", "total", "count"],
    )
    async def search(self, keyword: str, limit: Optional[int] = None, **_):
        """搜索日记"""
        try:
            plugin = self._get_plugin()
            result = plugin.search_entries(keyword=keyword, limit=limit)
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"搜索日记失败: {e}"))

    @llm_tool(
        name="neko_diary_get_today",
        description="获取今天的日记。返回今天写的所有日记条目。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="get_today",
        name="获取今日日记",
        description="获取今天写的所有日记",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["entries", "count"],
    )
    async def get_today(self, **_):
        """获取今天的日记"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_today_entries()
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"获取今日日记失败: {e}"))

    @llm_tool(
        name="neko_diary_get_mood_stats",
        description="获取心情统计。可以查看某段时间的心情分布，或者最近N天的心情趋势。",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "最近N天，默认7天"},
                "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD（可选）"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD（可选）"},
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="get_mood_stats",
        name="获取心情统计",
        description="获取心情统计数据",
        input_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
            },
            "required": [],
        },
        llm_result_fields=["mood_distribution", "daily_moods", "dominant_mood"],
    )
    async def get_mood_stats(
        self,
        days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **_,
    ):
        """获取心情统计"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_mood_stats(
                days=days,
                start_date=start_date,
                end_date=end_date,
            )
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"获取心情统计失败: {e}"))

    @llm_tool(
        name="neko_diary_throwback",
        description="历史上的今天。返回往年同月同日的日记，回忆过去的时光。",
        parameters={
            "type": "object",
            "properties": {
                "years": {"type": "integer", "description": "查看几年前，留空则查看所有年份"},
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="throwback",
        name="历史上的今天",
        description="查看往年同月同日的日记",
        input_schema={
            "type": "object",
            "properties": {
                "years": {"type": "integer"},
            },
            "required": [],
        },
        llm_result_fields=["entries", "count"],
    )
    async def throwback(self, years: Optional[int] = None, **_):
        """历史上的今天"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_throwback(years=years)
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"获取历史上的今天失败: {e}"))

    @llm_tool(
        name="neko_diary_delete",
        description="删除一篇日记。可以软删除（放入回收站）或永久删除。",
        parameters={
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "日记ID"},
                "permanent": {"type": "boolean", "description": "是否永久删除，默认false（软删除）"},
            },
            "required": ["entry_id"],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="delete",
        name="删除日记",
        description="删除一篇日记",
        input_schema={
            "type": "object",
            "properties": {
                "entry_id": {"type": "string"},
                "permanent": {"type": "boolean"},
            },
            "required": ["entry_id"],
        },
        llm_result_fields=["success"],
    )
    async def delete(self, entry_id: str, permanent: bool = False, **_):
        """删除日记"""
        try:
            plugin = self._get_plugin()
            result = plugin.delete_entry(entry_id=entry_id, permanent=permanent)
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"删除日记失败: {e}"))

    @llm_tool(
        name="neko_diary_get_names",
        description=(
            "获取当前猫娘称呼用户的方式，以及猫娘对自己的自称。"
            "master_name 是你对用户的称呼（默认'主人'），"
            "catgirl_name 是你对自己的自称（默认'喵喵'）。"
            "在和用户对话、写日记时，请使用这两个称呼而非固定词语。"
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=5.0,
    )
    @plugin_entry(
        id="get_names",
        name="获取称呼配置",
        description="获取猫娘对用户的称呼和猫娘自己的自称",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["master_name", "catgirl_name"],
    )
    async def get_names(self, **_):
        """获取称呼配置"""
        try:
            plugin = self._get_plugin()
            return Ok({
                "master_name": plugin.master_name,
                "catgirl_name": plugin.catgirl_name,
            })
        except Exception as e:
            return Err(SdkError(f"获取称呼配置失败: {e}"))

    @llm_tool(
        name="neko_diary_set_names",
        description=(
            "设置你称呼用户的方式（master_name）和/或你对自己的自称（catgirl_name）。"
            "设置后会持久化保存，插件重载后依然生效，不会退回默认的'主人/喵喵'。"
            "当你想换一种方式称呼用户或自己时调用此工具。留空的字段不会被修改。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "master_name": {"type": "string", "description": "你想怎么称呼用户（例如：主人、哥哥、那家伙…）"},
                "catgirl_name": {"type": "string", "description": "你想怎么自称（例如：喵喵、小雪、本喵…）"},
            },
            "required": [],
        },
        timeout=5.0,
    )
    @plugin_entry(
        id="set_names",
        name="设置称呼配置",
        description="设置猫娘对用户的称呼和/或猫娘自己的自称",
        input_schema={
            "type": "object",
            "properties": {
                "master_name": {"type": "string"},
                "catgirl_name": {"type": "string"},
            },
            "required": [],
        },
        llm_result_fields=["success", "master_name", "catgirl_name", "updated"],
    )
    async def set_names(self, master_name: Optional[str] = None, catgirl_name: Optional[str] = None, **_):
        """设置称呼配置

        持久化到插件配置文件，插件重载后不会退回默认值（主人/喵喵）。
        空字符串会被忽略，避免误清称呼。
        """
        try:
            plugin = self._get_plugin()
            updated: Dict[str, Any] = {}

            if master_name is not None:
                name = str(master_name).strip()
                if name:
                    plugin.master_name = name
                    updated["master_name"] = name
            if catgirl_name is not None:
                name = str(catgirl_name).strip()
                if name:
                    plugin.catgirl_name = name
                    updated["catgirl_name"] = name

            # 持久化到配置文件（扁平化键名，参考 catgirl_daily_planner 实现）
            if updated:
                persist = {f"neko_diary.{k}": v for k, v in updated.items()}
                try:
                    await self.config.update(persist, timeout=5.0)
                    self.logger.info("称呼配置已持久化: {}", persist)
                except Exception as e:
                    self.logger.warning("持久化称呼配置失败: {}", e)

            return Ok({
                "success": True,
                "master_name": plugin.master_name,
                "catgirl_name": plugin.catgirl_name,
                "updated": list(updated.keys()),
            })
        except Exception as e:
            return Err(SdkError(f"设置称呼配置失败: {e}"))
