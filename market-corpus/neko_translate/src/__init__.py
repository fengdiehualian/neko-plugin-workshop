"""翻译猫娘（N.E.K.O 插件）

让猫娘具备真正的翻译能力：中↔英↔日↔韩等主流语言互译，纯公开免密钥接口，
不需要用户配置任何 API Key。

暴露的能力
- LLM 工具 `translate_text` / `detect_language`：猫娘在对话中自行判断何时调用
- 运行时入口 `translate_text` / `translate_batch` / `list_languages`：插件管理器可手动触发

代码约定
- 运行时入口一律 `async def`
- 网络与重逻辑放在 `_providers.py` / `_http.py`，本文件只做编排与结果整形
- 模块导入期不产生任何副作用（不建连接、不读文件）
"""

from __future__ import annotations

from typing import Any, Optional

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

from ._languages import (
    LANGUAGES,
    guess_language,
    language_label,
    normalize_language,
    supported_languages_text,
)
from ._providers import DEFAULT_ORDER, PROVIDERS, TranslationFailed, cat_style, translate

_MAX_INPUT = 3000


@neko_plugin
class NekoTranslatePlugin(NekoPluginBase):
    """翻译猫娘。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: dict[str, Any] = {}

    # ------------------------------------------------------------------ 配置

    async def _config(self) -> dict[str, Any]:
        """读取业务配置段，带缓存。"""
        if self._cfg:
            return self._cfg
        cfg: dict[str, Any] = {}
        try:
            dumped = await self.config.dump(timeout=5.0)
            if isinstance(dumped, dict):
                cfg = dumped
        except Exception as exc:  # noqa: BLE001 - 配置读不到就用默认值
            self.logger.warning("读取配置失败，使用默认值: %s", exc)
        self._cfg = cfg
        return cfg

    async def _section(self, name: str) -> dict[str, Any]:
        cfg = await self._config()
        value = cfg.get(name)
        return value if isinstance(value, dict) else {}

    async def _settings(self) -> dict[str, Any]:
        section = await self._section("translate")
        providers = section.get("providers")
        if not isinstance(providers, list) or not providers:
            providers = list(DEFAULT_ORDER)
        try:
            timeout = float(section.get("timeout_seconds", 15))
        except (TypeError, ValueError):
            timeout = 15.0
        return {
            "default_target": normalize_language(section.get("default_target"), "en") or "en",
            "source": normalize_language(section.get("default_source"), "auto") or "auto",
            "providers": [str(p) for p in providers if str(p) in PROVIDERS] or list(DEFAULT_ORDER),
            "lingva_base": str(section.get("lingva_base") or "https://lingva.ml"),
            "timeout": timeout,
            "push_to_chat": bool(section.get("push_to_chat", True)),
            "max_input": int(section.get("max_input_chars", _MAX_INPUT) or _MAX_INPUT),
            # 猫娘语气
            "cute": bool(section.get("cute", True)),
            "cat_suffix": str(section.get("cat_suffix") or ""),
        }

    # --------------------------------------------------------------- 生命周期

    @lifecycle(id="startup")
    async def startup(self, **_):
        settings = await self._settings()
        self.logger.info(
            "翻译猫娘已就绪：默认目标语言=%s，提供方=%s",
            settings["default_target"],
            ",".join(settings["providers"]),
        )
        return Ok(
            {
                "status": "running",
                "default_target": settings["default_target"],
                "providers": settings["providers"],
                "languages": len(LANGUAGES),
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self.logger.info("翻译猫娘已停止")
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        self._cfg = {}
        settings = await self._settings()
        self.logger.info("翻译猫娘配置已刷新：%s", settings)
        return Ok({"status": "reloaded"})

    # ------------------------------------------------------------- 内部实现

    async def _do_translate(
        self,
        text: str,
        target: str,
        source: str = "auto",
        *,
        push_to_chat: bool = False,
    ) -> dict[str, Any]:
        settings = await self._settings()

        raw_text = str(text or "").strip()
        if not raw_text:
            raise SdkError("请告诉我要翻译的内容")
        if len(raw_text) > settings["max_input"]:
            raise SdkError(
                f"要翻译的内容太长了（{len(raw_text)} 字，上限 {settings['max_input']} 字），请分段发送"
            )

        resolved_target = normalize_language(target, None)
        if resolved_target in (None, "auto"):
            resolved_target = settings["default_target"]

        resolved_source = normalize_language(source, "auto") or "auto"
        # auto 时先给一个粗略判断用于"源=目标"短路；真实检测交给提供方
        # （MyMemory 的 Autodetect 会回传 detectedLanguage，比本地猜准）
        detected = resolved_source if resolved_source != "auto" else guess_language(raw_text)

        if detected == resolved_target:
            # 源和目标一致时给一句人话解释，而不是白白调一次接口
            return {
                "translated": raw_text,
                "detected_source": detected,
                "source_label": language_label(detected),
                "target_label": language_label(resolved_target),
                "provider": "none",
                "note": "原文已经是目标语言，未做改动",
            }

        try:
            result = await translate(
                raw_text,
                resolved_target,
                source=resolved_source,
                providers=settings["providers"],
                lingva_base=settings["lingva_base"],
                timeout=settings["timeout"],
            )
        except TranslationFailed as exc:
            raise SdkError(f"翻译失败：{exc}") from exc

        # 提供方回传的检测结果优先于本地猜测
        provider_detected = normalize_language(result.get("detected"), None)
        if resolved_source == "auto" and provider_detected and provider_detected != "auto":
            detected = provider_detected

        payload: dict[str, Any] = {
            "translated": result["text"],
            "detected_source": detected,
            "source_label": language_label(detected),
            "target_label": language_label(resolved_target),
            "provider": result["provider"],
            "original": raw_text,
        }
        if result.get("truncated"):
            payload["note"] = "内容较长，只翻译了前一部分"

        if push_to_chat and settings["push_to_chat"]:
            await self._push_translation(payload)
        return payload

    async def _push_translation(self, payload: dict[str, Any]) -> None:
        """把译文作为卡片推到聊天里，猫娘可以据此播报。"""
        settings = await self._settings()
        shown = cat_style(
            str(payload.get("translated") or ""),
            cute=settings["cute"] and payload.get("target_label") == "简体中文",
            custom_suffix=settings["cat_suffix"],
        )
        try:
            result = self.push_message(
                source="neko_translate",
                visibility=["chat"],
                ai_behavior="read",
                priority=5,
                parts=[
                    {
                        "type": "text",
                        "text": (
                            f"【翻译】{payload['source_label']} → {payload['target_label']}\n"
                            f"{shown}"
                        ),
                    }
                ],
            )
            if isinstance(result, dict) and not result.get("submitted", True):
                self.logger.warning("翻译结果推送被拒绝: %s", result.get("reason"))
        except Exception as exc:  # noqa: BLE001 - 推送失败不影响翻译本身
            self.logger.warning("翻译结果推送失败: %s", exc)

    # -------------------------------------------------------------- 运行时入口

    @plugin_entry(
        id="translate_text",
        name="翻译文本",
        description=(
            "把一段文本翻译成目标语言。用户要求翻译、或你想把某段内容转成另一种语言时使用。"
            "目标语言支持语言名、中文名或代码，例如 '日语'、'English'、'ja'、'zh-CN'。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要翻译的原文"},
                "target": {
                    "type": "string",
                    "description": (
                        "目标语言，可填语言名或代码，例如 '中文'、'英语'、'日语'、'zh-CN'、'en'、'ja'。"
                        "省略时使用插件默认目标语言。"
                    ),
                },
                "source": {
                    "type": "string",
                    "description": "源语言；默认 auto 自动检测，通常不需要填写。",
                },
                "push_to_chat": {
                    "type": "boolean",
                    "description": "是否把译文作为消息推到聊天框，默认按插件配置。",
                },
            },
            "required": ["text"],
        },
        llm_result_fields=["translated", "target_label", "note"],
    )
    async def translate_text(
        self,
        text: str,
        target: str = "",
        source: str = "auto",
        push_to_chat: Optional[bool] = None,
        **_,
    ):
        settings = await self._settings()
        should_push = settings["push_to_chat"] if push_to_chat is None else bool(push_to_chat)
        try:
            payload = await self._do_translate(
                text, target, source, push_to_chat=should_push
            )
        except SdkError as exc:
            return Err(exc)
        self.logger.info(
            "已翻译 %s 字：%s -> %s（%s）",
            len(str(text)),
            payload["detected_source"],
            payload["target_label"],
            payload["provider"],
        )
        return Ok(payload)

    @plugin_entry(
        id="translate_batch",
        name="批量翻译",
        description="一次翻译多段文本，返回与输入顺序一致的译文列表。",
        input_schema={
            "type": "object",
            "properties": {
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要翻译的文本列表",
                },
                "target": {"type": "string", "description": "目标语言"},
                "source": {"type": "string", "description": "源语言，默认自动检测"},
            },
            "required": ["texts", "target"],
        },
        llm_result_fields=["translated", "failed_count"],
    )
    async def translate_batch(
        self, texts: list[str], target: str, source: str = "auto", **_
    ):
        if not isinstance(texts, list) or not texts:
            return Err(SdkError("texts 必须是非空列表"))
        if len(texts) > 20:
            return Err(SdkError("一次最多翻译 20 段"))

        settings = await self._settings()
        resolved_target = normalize_language(target, None) or settings["default_target"]

        translated: list[str] = []
        failed: list[dict[str, Any]] = []
        for index, item in enumerate(texts):
            try:
                payload = await self._do_translate(str(item), resolved_target, source)
                translated.append(str(payload["translated"]))
            except SdkError as exc:
                translated.append("")
                failed.append({"index": index, "error": str(exc)})

        return Ok(
            {
                "translated": translated,
                "target_label": language_label(resolved_target),
                "failed_count": len(failed),
                "failed": failed,
            }
        )

    @plugin_entry(
        id="list_languages",
        name="支持的语言",
        description="列出这个插件支持翻译的语言和对应代码。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["summary", "languages"],
    )
    async def list_languages(self, **_):
        languages = [
            {"code": code, "name_zh": zh, "name_en": en}
            for code, (zh, en) in LANGUAGES.items()
        ]
        return Ok(
            {
                "summary": f"当前支持 {len(languages)} 种语言互译。{supported_languages_text()}",
                "languages": languages,
                "default_target": (await self._settings())["default_target"],
            }
        )

    # --------------------------------------------------------------- LLM 工具

    @llm_tool(
        name="translate_text",
        description=(
            "把文本翻译成另一种语言。当用户说「翻译一下」「这句日语什么意思」"
            "「帮我说成英文」时调用。返回译文，直接把它告诉用户即可。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要翻译的原文"},
                "target": {
                    "type": "string",
                    "description": "目标语言，如 '中文'、'英语'、'日语'、'zh-CN'、'en'、'ja'",
                },
                "source": {
                    "type": "string",
                    "description": "源语言，默认 auto 自动检测",
                },
            },
            "required": ["text", "target"],
        },
    )
    async def llm_translate_text(self, *, text: str, target: str, source: str = "auto"):
        try:
            payload = await self._do_translate(text, target, source)
        except SdkError as exc:
            return {"error": str(exc)}
        return {
            "translated": payload["translated"],
            "source": payload["source_label"],
            "target": payload["target_label"],
            "note": payload.get("note", ""),
        }

    @llm_tool(
        name="translate_aloud",
        description=(
            "翻译并让猫娘**念出来**（译会推到聊天并由角色语音播报）。"
            "用户说「念给我听」「读一下这句英文」「这句怎么用中文说，说给我听」时调用。\n"
            "如果用户只是想看译文、不需要听，用 translate_text 就够了。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要翻译的原文"},
                "target": {
                    "type": "string",
                    "description": "目标语言，如 '中文'、'英语'、'日语'、'zh-CN'",
                },
                "source": {"type": "string", "description": "源语言，默认 auto 自动检测"},
            },
            "required": ["text", "target"],
        },
    )
    async def llm_translate_aloud(self, *, text: str, target: str, source: str = "auto"):
        settings = await self._settings()
        try:
            payload = await self._do_translate(text, target, source)
        except SdkError as exc:
            return {"error": str(exc)}

        spoken = payload["translated"]
        if payload["target_label"] == "简体中文":
            spoken = cat_style(
                spoken, cute=settings["cute"], custom_suffix=settings["cat_suffix"]
            )

        # 翻译念出来本身就需要模型开口，所以这里主动推一条 respond 消息
        try:
            result = self.push_message(
                source="neko_translate",
                visibility=["chat"],
                ai_behavior="respond",
                priority=6,
                parts=[
                    {
                        "type": "text",
                        "text": f"【翻译】{payload['source_label']} → {payload['target_label']}\n{spoken}",
                    }
                ],
            )
            submitted = bool(result.get("submitted", True)) if isinstance(result, dict) else True
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("翻译播报推送失败: %s", exc)
            submitted = False

        return {
            "translated": spoken,
            "source": payload["source_label"],
            "target": payload["target_label"],
            "pushed_for_voice": submitted,
        }

    @llm_tool(
        name="detect_language",
        description="判断一段文本是什么语言。用户问「这是什么语言」时调用。",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要判断的文本"}},
            "required": ["text"],
        },
    )
    async def llm_detect_language(self, *, text: str):
        raw = str(text or "").strip()
        if not raw:
            return {"error": "文本为空"}
        code = guess_language(raw)
        return {
            "language": language_label(code),
            "code": code,
            "confidence": "粗略判断，基于字符集特征",
        }
