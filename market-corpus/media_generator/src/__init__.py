"""media_generator — 媒体生成器：让 YUI 生成图片与视频。

工具：
- generate_image：文生图（dashscope 通义万相 / openai 兼容）
- generate_video：文生视频（doubao 火山方舟 Seedance / kling 可灵）

API Key 从环境变量读取（见 providers.py 文档），对话内不暴露密钥。
"""

from __future__ import annotations

from plugin.sdk.plugin import (
    NekoPluginBase,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from . import providers


# 当前已配置的 provider 摘要（不泄露 key 本身）
def _configured_summary() -> str:
    parts = providers.list_configured_providers()
    if not parts:
        return "当前未配置任何媒体 API Key（文生图/文生视频暂不可用）"
    return "当前已配置：" + "、".join(parts)


@neko_plugin
class MediaGeneratorPlugin(NekoPluginBase):
    """媒体生成器 — 文生图 / 文生视频。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._save_dir = None

    # ══════════ 生命周期 ══════════

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        try:
            self._save_dir = self.data_path("generated")
        except Exception:
            self._save_dir = None
        return {"status": "ready", "plugin": "media_generator"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        return {"status": "shutdown"}

    # ══════════ 工具：文生图 ══════════

    @llm_tool(
        name="generate_image",
        description=(
            "生成图片。何时用：用户要求'画/生成一张图''做个封面''画个头像''画个示意图'等文生图需求时。"
            "参数 prompt：图片描述（越具体越好，含主体、风格、配色、构图）；"
            "size：可选，1024x1024 / 768x1024 / 1024x768 / 512x512，默认 1024x1024；"
            "provider：可选，dashscope（通义万相）/ openai，默认自动选择已配置的 provider。"
            "限制：需要对应 API Key（见插件说明）；生成的图片以 URL 形式返回，可直接展示给用户。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "图片描述，越具体越好"},
                "size": {"type": "string", "description": "可选：1024x1024 / 768x1024 / 1024x768 / 512x512"},
                "provider": {"type": "string", "description": "可选：dashscope / openai，默认 auto"},
            },
            "required": ["prompt"],
        },
        timeout=150.0,
    )
    async def generate_image(self, *, prompt: str = "", size: str = "", provider: str = "", **_):
        params = _.get("parameters") or {}
        prompt = (prompt or params.get("prompt") or "").strip()
        if not prompt:
            return {"output": "需要提供图片描述（prompt）。请告诉我你想生成什么样的图。", "is_error": True, "error": "NO_PROMPT"}
        size = (size or params.get("size") or "").strip() or "1024x1024"
        provider = (provider or params.get("provider") or "").strip().lower()

        available = providers.list_configured_providers()
        if not available:
            return {"output": _configured_summary() + "。请先在环境中设置 DASHSCOPE_API_KEY 或 OPENAI_API_KEY。", "is_error": True, "error": "NO_PROVIDER"}

        if provider in ("", "auto"):
            provider = "dashscope" if "dashscope" in available else "openai"
        if provider not in ("dashscope", "openai"):
            return {"output": f"未知 provider '{provider}'。支持：dashscope / openai。", "is_error": True, "error": "UNKNOWN_PROVIDER"}
        if provider not in available:
            return {"output": f"provider={provider} 未配置对应 API Key。可用：{'、'.join(available)}。", "is_error": True, "error": "PROVIDER_NOT_CONFIGURED"}

        if provider == "dashscope":
            return await providers.generate_image_dashscope(prompt, size)
        if provider == "openai":
            return await providers.generate_image_openai(prompt, size, save_dir=self._save_dir)
        return {"output": f"未知 provider '{provider}'。支持：dashscope / openai。", "is_error": True, "error": "UNKNOWN_PROVIDER"}

    # ══════════ 工具：文生视频 ══════════

    @llm_tool(
        name="generate_video",
        description=(
            "生成视频。何时用：用户要求'生成/做一个视频''做个宣传视频''做个动态画面'等文生视频需求时。"
            "参数 prompt：视频内容描述（场景、动作、镜头、风格）；"
            "duration：可选，秒数（5 或 10，默认 5，具体范围取决于服务商）；"
            "provider：可选，doubao（火山方舟 Seedance）/ kling（可灵），默认自动选择已配置的 provider。"
            "限制：需要对应 API Key；视频生成耗时较长（约 1-4 分钟），返回视频 URL。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "视频内容描述"},
                "duration": {"type": "integer", "description": "可选：5 或 10 秒"},
                "provider": {"type": "string", "description": "可选：doubao / kling，默认 auto"},
            },
            "required": ["prompt"],
        },
        timeout=300.0,
    )
    async def generate_video(self, *, prompt: str = "", duration: int = 0, provider: str = "", **_):
        params = _.get("parameters") or {}
        prompt = (prompt or params.get("prompt") or "").strip()
        if not prompt:
            return {"output": "需要提供视频内容描述（prompt）。请告诉我你想生成什么样的视频。", "is_error": True, "error": "NO_PROMPT"}
        try:
            duration = int(duration or params.get("duration") or 5)
        except (TypeError, ValueError):
            duration = 5
        provider = (provider or params.get("provider") or "").strip().lower()

        available = providers.list_configured_providers()
        video_available = [p for p in ("doubao", "kling") if p in available]
        if not video_available:
            return {"output": "未配置视频生成 API Key。可设置 DOUBAO_ARK_API_KEY+DOUBAO_ARK_ENDPOINT（Seedance）或 KLING_API_KEY+KLING_SECRET_KEY（可灵）。", "is_error": True, "error": "NO_VIDEO_PROVIDER"}

        if provider in ("", "auto"):
            provider = "doubao" if "doubao" in video_available else "kling"
        if provider not in ("doubao", "kling"):
            return {"output": f"未知 provider '{provider}'。支持：doubao / kling。", "is_error": True, "error": "UNKNOWN_PROVIDER"}
        if provider not in video_available:
            return {"output": f"provider={provider} 未配置对应 API Key。可用：{'、'.join(video_available)}。", "is_error": True, "error": "PROVIDER_NOT_CONFIGURED"}

        if provider == "doubao":
            return await providers.generate_video_doubao(prompt, duration)
        if provider == "kling":
            return await providers.generate_video_kling(prompt, duration)
        return {"output": f"未知 provider '{provider}'。支持：doubao / kling。", "is_error": True, "error": "UNKNOWN_PROVIDER"}

    # ══════════ 自动路由入口（plugin_entry：TaskExecutor 评估可见） ══════════
    # llm_tool 走 LLM 函数调用通道；plugin_entry 走 TaskExecutor 的 UserPlugin
    # 路由通道。v0.1.0 只注册了 llm_tool、无任何 plugin_entry，导致市场面板
    # "入口点: 0"、评估侧永远无法路由到本插件（媒体生成功能实际不可用）。
    # v0.2.0 注册 run（通用兜底）与 media_gen（生成专用）两个入口修复。
    #
    # 【重要】方法名禁止以下划线开头：宿主 collect_entries() 会跳过所有
    # `_` 前缀的实例方法（bili_viewer v0.6.22 教训），执行侧会报
    # "Entry 'xxx' not found"。id 由 plugin_entry(id=...) 指定即可。

    @plugin_entry(
        id="run",
        name="媒体生成器",
        description=(
            "媒体生成器（media_generator）：让 YUI 生成图片和视频。"
            "文生图：通义万相（DashScope）/ OpenAI 兼容 API；"
            "文生视频：火山方舟 Seedance（豆包）/ 可灵（Kling）。"
            "用户说'画/生成一张图''做个封面''画个头像''画个示意图''生成/做一个视频''做个宣传片'等媒体生成需求时使用。"
            "参数：prompt 必填（内容描述，越具体越好，含主体/风格/配色/构图或场景/动作/镜头）；"
            "size 可选（图片尺寸 1024x1024/768x1024/1024x768/512x512）；"
            "duration 可选（视频秒数 5 或 10）；provider 可选（dashscope/openai 或 doubao/kling，默认自动）。"
            "限制：需要对应 API Key（未配置时如实说明需设置哪些环境变量，见插件说明）；"
            "生成结果以 URL 返回，可直接展示。"
            "回复纪律：直接给出生成结果（URL）或配置提示，不加'搞定啦/任务完成啦'等多余话术，不编造生成结果。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "生成类型：generate_image（文生图）/ generate_video（文生视频），缺省按参数自动判断（给 duration 或 doubao/kling provider 视为视频，否则视为图片）",
                },
                "prompt": {"type": "string", "description": "内容描述，必填，越具体越好"},
                "size": {"type": "string", "description": "图片尺寸：1024x1024 / 768x1024 / 1024x768 / 512x512"},
                "duration": {"type": "integer", "description": "视频秒数：5 或 10"},
                "provider": {"type": "string", "description": "图片：dashscope / openai；视频：doubao / kling"},
            },
            "required": ["prompt"],
        },
        timeout=300.0,
    )
    async def entry_run(self, **kwargs):
        params = kwargs.get("parameters") or {}
        action = str(kwargs.get("action") or params.get("action") or "").strip().lower()
        prompt = str(kwargs.get("prompt") or params.get("prompt") or "").strip()
        size = str(kwargs.get("size") or params.get("size") or "").strip()
        provider = str(kwargs.get("provider") or params.get("provider") or "").strip().lower()
        try:
            duration = int(kwargs.get("duration") or params.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        if not prompt:
            return {
                "output": "需要提供内容描述（prompt）。请告诉我要生成什么样的图片或视频。",
                "is_error": True,
                "error": "NO_PROMPT",
            }
        # action 未明确时自动判断：给了 duration 或 doubao/kling provider → 视频；否则默认图片
        if action in ("", "auto") and (duration or provider in ("doubao", "kling")):
            action = "generate_video"
        if action in ("generate_image", "image"):
            return await self.generate_image(prompt=prompt, size=size, provider=provider, parameters={})
        if action in ("generate_video", "video"):
            return await self.generate_video(prompt=prompt, duration=duration, provider=provider, parameters={})
        if action:
            return {
                "output": f"未知生成类型 '{action}'。支持：generate_image / generate_video。",
                "is_error": True,
                "error": "UNKNOWN_ACTION",
            }
        # 兜底：无 action 也无视频特征 → 默认文生图
        return await self.generate_image(prompt=prompt, size=size, provider=provider, parameters={})

    @plugin_entry(
        id="media_gen",
        name="生成图片或视频",
        description=(
            "生成图片或视频（媒体生成器）。用户明确要求'生成一张图/做个视频'等媒体生成时使用："
            "参数 prompt 必填（内容描述，越具体越好）；kind 可选 image/video（缺省自动判断，"
            "给了 duration 或 doubao/kling provider 视为视频，否则视为图片）；size/duration/provider 可选。"
            "需要对应 API Key（未配置时如实说明设置方法）；生成结果以 URL 返回。"
            "回复纪律：直接给出生成结果或配置提示，不加多余话术，不编造生成结果。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "内容描述，必填"},
                "kind": {"type": "string", "description": "image / video，缺省自动判断"},
                "size": {"type": "string", "description": "图片尺寸：1024x1024 / 768x1024 / 1024x768 / 512x512"},
                "duration": {"type": "integer", "description": "视频秒数：5 或 10"},
                "provider": {"type": "string", "description": "图片：dashscope / openai；视频：doubao / kling"},
            },
            "required": ["prompt"],
        },
        timeout=300.0,
    )
    async def entry_media_gen(self, **kwargs):
        params = kwargs.get("parameters") or {}
        prompt = str(kwargs.get("prompt") or params.get("prompt") or "").strip()
        kind = str(kwargs.get("kind") or params.get("kind") or "").strip().lower()
        return await self.entry_run(
            action=kind,
            prompt=prompt,
            size=str(kwargs.get("size") or params.get("size") or ""),
            duration=kwargs.get("duration") or params.get("duration") or 0,
            provider=str(kwargs.get("provider") or params.get("provider") or ""),
            parameters={},
        )
