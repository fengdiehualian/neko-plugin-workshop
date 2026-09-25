"""Writer power analysis business service."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config_resolve_node import ConfigResolveNode
from .model_list_node import ModelListNode
from .models import AnalysisConfig, RetryableModelError, WriterAnalysisError
from .pipeline import WriterAnalysisPipelineNode
from .status_node import StatusNode
from .utils import (
    completion_content,
    completion_error,
    is_unavailable_model_error,
    normalize_model_string_list,
    normalize_style_profile,
    parse_completion_response,
    parse_model_json,
    safe_float,
    safe_int,
    safe_str,
)

AUTHOR_STYLE_SYNTHESIS_PROMPT = """你是资深文学编辑和作者风格研究员。
你将收到同一作者多篇作品的“作品文风画像”样本。请不要做简单词频统计，而要进行专业统合：
1. 区分稳定的作者级风格倾向与单篇作品的偶然题材差异。
2. 判断语言习惯、句式结构、意象偏好、叙事节奏、核心命题和题材选择之间的关系。
3. 只基于给定样本，不推测作者身份、经历、年龄、性别或现实背景。
4. 输出必须是严格 JSON 对象，不要 Markdown，不要解释 JSON 外的内容。

JSON 结构：
{
  "dominantStyle": "8到20字概括作者最稳定的文风标签",
  "dominantGenre": "作者最常呈现的体裁/题材倾向",
  "summary": "180到360字作者文风画像总述，说明稳定优势、表达惯性和潜在限制",
  "styleLabels": [{"text": "风格标签", "count": 2}],
  "genres": [{"text": "题材类型", "count": 2}],
  "languageHabits": [{"text": "语言习惯", "count": 2}],
  "sentenceStructures": [{"text": "句式结构", "count": 2}],
  "imageryPreferences": [{"text": "意象偏好", "count": 2}],
  "keywords": [{"text": "关键词", "count": 2}],
  "rhythms": [{"text": "表达节奏", "count": 2}],
  "coreExpressions": [{"text": "核心表达", "count": 2}],
  "topicPreferences": ["作者反复处理的主题或关系命题"],
  "narrativeTendencies": ["叙事视角、结构推进或冲突组织的长期倾向"],
  "strengths": ["作者级稳定优势"],
  "risks": ["作者级惯性风险或容易重复的问题"],
  "evolutionAdvice": ["后续写作可尝试的升级方向"],
  "confidence": 0.0
}

count 表示该特征大致覆盖多少个样本；无法精确时保守估计。confidence 取 0 到 1。"""


def _term_list(value: Any, limit: int) -> list[dict[str, Any]]:
    """Normalize model term arrays into text/count objects."""

    if not isinstance(value, list):
        return []
    terms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            text = safe_str(item.get("text") or item.get("label") or item.get("name"))[:120]
            count = safe_int(item.get("count"), 1, 1, 999)
        else:
            text = safe_str(item)[:120]
            count = 1
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        terms.append({"text": text, "count": count})
        if len(terms) >= limit:
            break
    return terms


def _string_list(value: Any, limit: int) -> list[str]:
    """Normalize model string arrays for author profile synthesis."""

    return normalize_model_string_list(value, limit)


def _normalize_author_style_synthesis(parsed: dict[str, Any], sample_count: int) -> dict[str, Any]:
    """Normalize model JSON into the author-level style profile schema."""

    source = parsed.get("profile") if isinstance(parsed.get("profile"), dict) else parsed
    style_labels = _term_list(source.get("styleLabels"), 8)
    genres = _term_list(source.get("genres"), 8)
    language_habits = _term_list(source.get("languageHabits"), 12)
    sentence_structures = _term_list(source.get("sentenceStructures"), 12)
    imagery_preferences = _term_list(source.get("imageryPreferences"), 12)
    keywords = _term_list(source.get("keywords"), 16)
    rhythms = _term_list(source.get("rhythms"), 8)
    core_expressions = _term_list(source.get("coreExpressions"), 8)

    dominant_style = safe_str(source.get("dominantStyle"), style_labels[0]["text"] if style_labels else "尚未形成稳定标签")[:120]
    dominant_genre = safe_str(source.get("dominantGenre"), genres[0]["text"] if genres else "题材样本不足")[:120]
    return {
        "sampleCount": safe_int(source.get("sampleCount"), sample_count, 1, max(sample_count, 1)),
        "source": "model",
        "dominantStyle": dominant_style,
        "dominantGenre": dominant_genre,
        "summary": safe_str(source.get("summary"))[:800],
        "styleLabels": style_labels,
        "genres": genres,
        "languageHabits": language_habits,
        "sentenceStructures": sentence_structures,
        "imageryPreferences": imagery_preferences,
        "keywords": keywords,
        "rhythms": rhythms,
        "coreExpressions": core_expressions,
        "topicPreferences": _string_list(source.get("topicPreferences"), 8),
        "narrativeTendencies": _string_list(source.get("narrativeTendencies"), 8),
        "strengths": _string_list(source.get("strengths"), 8),
        "risks": _string_list(source.get("risks"), 8),
        "evolutionAdvice": _string_list(source.get("evolutionAdvice"), 8),
        "confidence": safe_float(source.get("confidence"), 0.0, 0.0, 1.0),
    }


def _normalize_author_style_samples(profiles: Any, max_profiles: int = 20) -> list[dict[str, Any]]:
    """Keep only valid article style profiles for model synthesis."""

    if not isinstance(profiles, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in profiles:
        profile = normalize_style_profile(raw)
        if not profile:
            continue
        key = "::".join(
            [
                safe_str(profile.get("styleLabel")),
                safe_str(profile.get("genreType")),
                safe_str(profile.get("summary")),
                safe_str(profile.get("storyContent")),
                safe_str(profile.get("coreExpression")),
                "/".join(normalize_model_string_list(profile.get("keywords"), 8)),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(profile)
        if len(normalized) >= max_profiles:
            break
    return normalized


class WriterPowerAnalysisService:
    """Business service owned by the plugin shell."""

    def __init__(self, logger: Any):
        self.config_resolve = ConfigResolveNode(logger)
        self.status_node = StatusNode(logger)
        self.model_list_node = ModelListNode(logger)
        self.analysis_pipeline = WriterAnalysisPipelineNode(logger)

    def resolve_config(self, raw_config: dict[str, Any], **kwargs: Any) -> AnalysisConfig:
        """Resolve runtime config through a logged node."""

        return self.config_resolve.run(raw_config, **kwargs)

    def status(self, raw_config: dict[str, Any], **kwargs: Any) -> dict[str, object]:
        """Return non-secret plugin status."""

        cfg = self.resolve_config(raw_config, **kwargs)
        return self.status_node.run(cfg)

    async def list_models(self, raw_config: dict[str, Any], **kwargs: Any) -> dict[str, object]:
        """Return configured upstream models or builtin fallback models."""

        cfg = self.resolve_config(raw_config, **kwargs)
        return await self.model_list_node.run(cfg)

    async def analyze(
        self,
        raw_config: dict[str, Any],
        article_text: str,
        mode: str = "standard",
        *,
        use_neko_model: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run full writer analysis."""

        cfg = self.resolve_config(raw_config, **kwargs)
        return await self.analysis_pipeline.run(cfg, article_text, mode, use_neko_model=use_neko_model)

    async def synthesize_author_style_profile(
        self,
        raw_config: dict[str, Any],
        profiles: Any,
        *,
        use_neko_model: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Use a model to synthesize an author-level style profile from historical work profiles."""

        samples = _normalize_author_style_samples(profiles)
        if not samples:
            raise WriterAnalysisError("缺少可用于统合的历史作品文风画像")

        cfg = self.resolve_config(raw_config, **kwargs)
        user_payload = json.dumps(
            {
                "sampleCount": len(samples),
                "articleStyleProfiles": samples,
            },
            ensure_ascii=False,
        )
        if use_neko_model:
            upstream = await self.analysis_pipeline.neko_llm_request.run(
                system_prompt=AUTHOR_STYLE_SYNTHESIS_PROMPT,
                article_text=user_payload,
                temperature=0.25,
                json_mode=cfg.json_mode,
                timeout_seconds=cfg.timeout_seconds,
            )
            parsed_response = parse_completion_response(upstream.text)
            content = completion_content(parsed_response)
            parsed = parse_model_json(content)
            return _normalize_author_style_synthesis(parsed, len(samples))

        candidates = self.analysis_pipeline.model_candidates.run(cfg.model, cfg.fallback_models)
        last_error = ""
        for candidate_model in candidates:
            payload = self.analysis_pipeline.payload_build.run(candidate_model, AUTHOR_STYLE_SYNTHESIS_PROMPT, user_payload, cfg)
            try:
                upstream = await self.analysis_pipeline.completion_request.run(cfg, candidate_model, payload)
                parsed_response = parse_completion_response(upstream.text)
                if not upstream.is_success:
                    error = completion_error(parsed_response, upstream.status_code)
                    if is_unavailable_model_error(error):
                        raise RetryableModelError(error)
                    raise WriterAnalysisError(error)
                content = completion_content(parsed_response)
                if not content:
                    raise RetryableModelError("模型未返回作者文风画像")
                parsed = parse_model_json(content)
                return _normalize_author_style_synthesis(parsed, len(samples))
            except RetryableModelError as exc:
                last_error = str(exc)
                continue
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue

        raise WriterAnalysisError(last_error or "所有候选模型都无法生成作者文风画像")

    def get_neko_model(self) -> dict[str, object]:
        """Return Neko's current LLM model configuration.

        Reads from the main N.E.K.O config manager so the frontend
        can show which model Neko is currently using.
        """
        try:
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()
            cfg = config_manager.get_model_api_config("conversation")
            model = str(cfg.get("model") or "")
            base_url = str(cfg.get("base_url") or "")
            has_api_key = bool(cfg.get("api_key"))
            return {
                "available": bool(model),
                "model": model or "未知",
                "base_url": base_url or "Neko 默认端点",
                "has_api_key": has_api_key,
            }
        except Exception:  # noqa: BLE001 - Neko config lookup is optional integration glue.
            return {
                "available": False,
                "model": "未知",
                "base_url": "",
                "has_api_key": False,
            }
