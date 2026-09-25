"""Shared utility functions for writer power analysis nodes."""

from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import quote

from .constants import (
    CHAT_COMPLETIONS_RE,
    DATA_PREFIX_RE,
    DEFAULT_MODEL_LIST_PATH,
    DEFAULT_TEMPERATURE,
    GEMINI_GENERATE_RE,
    TRAILING_COMMA_RE,
)
from .models import EndpointCandidate


def safe_str(value: Any, default: str = "") -> str:
    """Return a stripped string or a default."""

    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    """Return a bounded float."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Return a bounded integer."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def safe_bool(value: Any, default: bool) -> bool:
    """Return a conservative boolean from config input."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def as_string_list(value: Any, default: list[str]) -> list[str]:
    """Normalize a config value into a string list."""

    if isinstance(value, list):
        items = [safe_str(item) for item in value]
        return [item for item in items if item]
    if isinstance(value, str):
        items = [safe_str(item) for item in value.split(",")]
        return [item for item in items if item]
    return list(default)


def is_gemini_model(model: str) -> bool:
    """Return whether a model id should use the Gemini generateContent API."""

    return "gemini" in model.lower()


def normalize_base_url(base_url: str) -> str:
    """Normalize a base URL without a trailing slash."""

    return base_url.rstrip("/") if base_url else ""


def build_gemini_v1beta_base(base_url: str) -> str:
    """Build a Gemini v1beta base URL."""

    if base_url.endswith("/v1beta"):
        return base_url
    if re.search(r"/v\d+$", base_url):
        return re.sub(r"/v\d+$", "/v1beta", base_url)
    return f"{base_url}/v1beta"


def build_model_list_url(base_url: str, model_list_path: str) -> str:
    """Build the model-list endpoint from config."""

    normalized = normalize_base_url(base_url)
    path = model_list_path.strip() or DEFAULT_MODEL_LIST_PATH
    if path.startswith(("http://", "https://")):
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{normalized}{path}"


def build_endpoint_candidates(base_url: str, model: str) -> list[EndpointCandidate]:
    """Build likely completion endpoints in priority order."""

    normalized = normalize_base_url(base_url)
    encoded_model = quote(model, safe="")

    if GEMINI_GENERATE_RE.search(normalized):
        return [EndpointCandidate(normalized, "gemini")]
    if CHAT_COMPLETIONS_RE.search(normalized):
        return [EndpointCandidate(normalized, "openai")]

    candidates: list[EndpointCandidate] = []
    if is_gemini_model(model):
        gemini_base = build_gemini_v1beta_base(normalized)
        candidates.append(EndpointCandidate(f"{gemini_base}/models/{encoded_model}:generateContent", "gemini"))

    if re.search(r"/v\d+$", normalized):
        candidates.append(EndpointCandidate(f"{normalized}/chat/completions", "openai"))
    else:
        candidates.extend(
            [
                EndpointCandidate(f"{normalized}/chat/completions", "openai"),
                EndpointCandidate(f"{normalized}/v1/chat/completions", "openai"),
            ]
        )
    return candidates


def strip_code_fence(text: str) -> str:
    """Remove an outer Markdown code fence when present."""

    trimmed = text.strip()
    if not trimmed.startswith("```"):
        return trimmed
    first_newline = trimmed.find("\n")
    closing = trimmed.rfind("```")
    if first_newline == -1 or closing <= first_newline:
        return trimmed
    return trimmed[first_newline + 1 : closing].strip()


def find_json_object_end(text: str, start: int) -> int:
    """Find the balanced end offset of the first JSON object."""

    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def extract_json_object(text: str) -> str:
    """Extract the first complete JSON object from model output."""

    stripped = strip_code_fence(text)
    start = stripped.find("{")
    if start == -1:
        raise ValueError("模型没有返回 JSON 对象")
    end = find_json_object_end(stripped, start)
    if end == -1:
        raise ValueError("模型返回的 JSON 对象不完整")
    return stripped[start : end + 1]


def fix_control_characters(json_text: str) -> str:
    """Escape invalid control characters inside JSON strings."""

    out: list[str] = []
    in_string = False
    escaped = False
    for char in json_text:
        code = ord(char)
        if escaped:
            escaped = False
            out.append(char)
            continue
        if char == "\\" and in_string:
            escaped = True
            out.append(char)
            continue
        if char == '"':
            in_string = not in_string
            out.append(char)
            continue
        if in_string and code < 0x20:
            if char == "\n":
                out.append("\\n")
            elif char == "\r":
                out.append("\\r")
            elif char == "\t":
                out.append("\\t")
            else:
                out.append(f"\\u{code:04x}")
            continue
        out.append(char)
    return "".join(out)


def escape_json_string_content(value: str) -> str:
    """Escape raw quotes/control chars while preserving existing escapes."""

    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            if index + 1 < len(value):
                out.append(char)
                out.append(value[index + 1])
                index += 2
                continue
            out.append("\\\\")
            index += 1
            continue
        if char == '"':
            out.append('\\"')
            index += 1
            continue
        code = ord(char)
        if code < 0x20:
            if char == "\n":
                out.append("\\n")
            elif char == "\r":
                out.append("\\r")
            elif char == "\t":
                out.append("\\t")
            else:
                out.append(f"\\u{code:04x}")
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def find_likely_json_string_end(text: str, start: int) -> int:
    """Find a string terminator likely followed by a JSON separator."""

    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char != '"':
            continue
        lookahead = index + 1
        while lookahead < len(text) and text[lookahead].isspace():
            lookahead += 1
        if lookahead >= len(text) or text[lookahead] in ",}":
            return index
    return -1


def repair_mermaid_code_strings(json_text: str) -> str:
    """Repair common LLM JSON breakage in Mermaid code string values.

    Models often emit Mermaid as ``A["节点"]`` inside a JSON string without
    escaping those inner quotes. That makes the whole JSON invalid even though
    the rest of the report is usable.
    """

    marker = re.compile(r'"code"\s*:\s*"')
    out: list[str] = []
    cursor = 0
    while True:
        match = marker.search(json_text, cursor)
        if not match:
            out.append(json_text[cursor:])
            break
        content_start = match.end()
        content_end = find_likely_json_string_end(json_text, content_start)
        if content_end == -1:
            out.append(json_text[cursor:])
            break
        out.append(json_text[cursor:content_start])
        out.append(escape_json_string_content(json_text[content_start:content_end]))
        cursor = content_end
    return "".join(out)


def parse_model_json(raw: str) -> dict[str, Any]:
    """Parse model JSON with small recovery steps for common LLM issues."""

    json_text = extract_json_object(raw)
    candidates = [
        json_text,
        fix_control_characters(TRAILING_COMMA_RE.sub(r"\1", json_text)),
    ]
    candidates.append(repair_mermaid_code_strings(candidates[-1]))

    last_error: json.JSONDecodeError | None = None
    parsed: Any = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
    else:
        if last_error is not None:
            raise last_error
        parsed = json.loads(json_text)
    if not isinstance(parsed, dict):
        raise TypeError("模型返回的 JSON 顶层不是对象")
    return parsed


def parse_completion_response(raw_text: str) -> dict[str, Any]:
    """Parse a completion HTTP response without assuming strict JSON body shape."""

    trimmed = raw_text.strip()
    if not trimmed:
        return {"raw_text": raw_text}
    try:
        parsed = json.loads(trimmed)
        if isinstance(parsed, dict):
            return {"parsed": parsed, "raw_text": raw_text}
    except json.JSONDecodeError:
        pass

    data_line = ""
    for line in trimmed.splitlines():
        candidate = line.strip()
        if candidate.startswith("data:") and candidate != "data: [DONE]":
            data_line = candidate
            break
    candidate_text = DATA_PREFIX_RE.sub("", data_line or trimmed)
    try:
        parsed = json.loads(extract_json_object(candidate_text))
        if isinstance(parsed, dict):
            return {"parsed": parsed, "raw_text": raw_text}
    except (json.JSONDecodeError, ValueError):
        pass
    return {"raw_text": raw_text}


def completion_content(payload: dict[str, Any]) -> str:
    """Extract assistant content from OpenAI-compatible or Gemini responses."""

    parsed = payload.get("parsed")
    if not isinstance(parsed, dict):
        return safe_str(payload.get("raw_text"))

    choices = parsed.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content

    candidates = parsed.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            content = first.get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    return "".join(part.get("text", "") for part in parts if isinstance(part, dict))

    return safe_str(payload.get("raw_text"))


def completion_error(payload: dict[str, Any], status_code: int) -> str:
    """Extract a readable upstream error."""

    parsed = payload.get("parsed")
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = safe_str(error.get("message"))
            code = safe_str(error.get("code"))
            if message and code:
                return f"{code}: {message}"
            if message:
                return message
        if isinstance(error, str) and error:
            return error
    raw = safe_str(payload.get("raw_text"))
    return raw[:500] or f"模型接口请求失败 ({status_code})"


def is_unavailable_model_error(error_text: str) -> bool:
    """Return whether the selected model can safely fall back to another id."""

    normalized = error_text.lower()
    return (
        "model is not found" in normalized
        or "model not found" in normalized
        or "not supported" in normalized
        or '"code":"forbidden"' in normalized
        or "invalid_request_error" in normalized
        or "forbidden" in normalized
    )


def is_json_mode_rejection(raw_text: str, status_code: int) -> bool:
    """Return whether the upstream likely rejected response_format/json mode."""

    if status_code not in {400, 422}:
        return False
    normalized = raw_text.lower()
    return (
        "response_format" in normalized
        or "json_object" in normalized
        or "json mode" in normalized
        or "invalid_request_error" in normalized
        or "invalid request" in normalized
    )


def dimension_score(value: Any) -> float:
    """Parse a dimension score from model output."""

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else 0.0


def normalize_model_string_list(value: Any, limit: int) -> list[str]:
    """Normalize a model output value into a bounded string list."""

    if not isinstance(value, list):
        return []
    items = [safe_str(item) for item in value]
    return [item for item in items if item][:limit]


def normalize_bounded_string_list(value: Any, limit: int, max_chars: int) -> list[str]:
    """Normalize a string list while preserving longer explanatory items."""

    if not isinstance(value, list):
        return []
    items = [safe_str(item)[:max_chars] for item in value]
    return [item for item in items if item][:limit]


def normalize_style_profile(value: Any) -> dict[str, Any] | None:
    """Normalize an Ink Battles-style article feature profile."""

    if not isinstance(value, dict):
        return None

    legacy_core = value.get("spiritualCore") or value.get("emotionalTendency") or ""
    profile: dict[str, Any] = {
        "storyContent": safe_str(value.get("storyContent") or value.get("narrativeMode"))[:700],
        "coreExpression": safe_str(value.get("coreExpression") or legacy_core)[:700],
        "genreType": safe_str(value.get("genreType"))[:400],
        "languageHabits": normalize_bounded_string_list(value.get("languageHabits"), 8, 260),
        "sentenceStructures": normalize_bounded_string_list(value.get("sentenceStructures"), 8, 260),
        "expressionRhythm": safe_str(value.get("expressionRhythm"))[:700],
        "imageryPreferences": normalize_bounded_string_list(value.get("imageryPreferences"), 8, 260),
        "styleLabel": safe_str(value.get("styleLabel"))[:80],
        "summary": safe_str(value.get("summary"))[:700],
        "keywords": normalize_model_string_list(value.get("keywords"), 12),
    }

    for optional_key in ("emotionalTendency", "narrativeMode", "spiritualCore"):
        optional_value = safe_str(value.get(optional_key))
        if optional_value:
            profile[optional_key] = optional_value[:300]

    has_core_fields = any(
        [
            profile["storyContent"],
            profile["coreExpression"],
            profile["genreType"],
            profile["styleLabel"],
            profile["summary"],
            profile["keywords"],
        ]
    )
    return profile if has_core_fields else None


def normalize_excellent_sentences(value: Any) -> list[dict[str, str]]:
    """Keep only valid, deduplicated excellent sentence candidates."""

    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    sentences: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = safe_str(item.get("content"))[:65]
        reason = safe_str(item.get("reason"))[:240]
        normalized_content = re.sub(r"\s+", "", content)
        if not normalized_content or not reason or normalized_content in seen:
            continue
        seen.add(normalized_content)
        sentences.append({"content": content, "reason": reason})
        if len(sentences) >= 2:
            break
    return sentences


def normalize_author_matches(value: Any) -> list[dict[str, Any]]:
    """Normalize model-generated author style references."""

    if not isinstance(value, list):
        return []
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        name = safe_str(item.get("name"))[:80]
        style_label = safe_str(item.get("styleLabel"))[:120]
        description = safe_str(item.get("description"))[:500]
        reasons = normalize_model_string_list(item.get("reasons"), 4)
        key = name.lower()
        if not name or not style_label or not description or not reasons or key in seen:
            continue
        seen.add(key)
        normalized: dict[str, Any] = {
            "name": name,
            "styleLabel": style_label,
            "description": description,
            "confidence": safe_float(item.get("confidence"), 0.0, 0.0, 100.0),
            "reasons": reasons,
        }
        source = safe_str(item.get("source"))
        if source in {"library", "model"}:
            normalized["source"] = source
        if item.get("similarity") is not None:
            normalized["similarity"] = safe_float(item.get("similarity"), 0.0, 0.0, 1.0)
        matches.append(normalized)
        if len(matches) >= 3:
            break
    return matches


def normalize_mermaid_diagrams(value: Any) -> list[dict[str, str]]:
    """Normalize Mermaid diagram objects for UI rendering."""

    if not isinstance(value, list):
        return []
    diagrams: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        code = safe_str(item.get("code")).replace("\r", ";").replace("\n", ";")
        if not code:
            continue
        diagrams.append(
            {
                "type": safe_str(item.get("type"), "graph")[:40],
                "title": safe_str(item.get("title"), "分析图表")[:120],
                "code": code,
            }
        )
        if len(diagrams) >= 3:
            break
    return diagrams


def calculate_final_score(dimensions: list[dict[str, Any]]) -> float:
    """Calculate the Ink Battles backend final score from dimensions."""

    base_dimensions = [
        item
        for item in dimensions
        if "经典" not in safe_str(item.get("name")) and "新锐" not in safe_str(item.get("name"))
    ]
    count_above_35 = sum(1 for item in base_dimensions if dimension_score(item.get("score")) > 3.5)
    count_above_40 = sum(1 for item in base_dimensions if dimension_score(item.get("score")) > 4.0)
    use_floor = count_above_35 >= 6 or count_above_40 >= 3
    base_score = sum(max(3.0, dimension_score(item.get("score"))) if use_floor else dimension_score(item.get("score")) for item in base_dimensions)
    classicity = next(
        (dimension_score(item.get("score")) for item in dimensions if "经典" in safe_str(item.get("name"))),
        1.0,
    )
    novelty = next(
        (dimension_score(item.get("score")) for item in dimensions if "新锐" in safe_str(item.get("name"))),
        1.0,
    )
    final_score = base_score * (classicity or 1.0) * (novelty or 1.0)
    return 0.0 if math.isnan(final_score) else round(final_score, 2)


def calculate_rating_tag(dimensions: list[dict[str, Any]]) -> str:
    """Calculate the quick rating tag from classicity and novelty weights."""

    classicity = next(
        (dimension_score(item.get("score")) for item in dimensions if "经典" in safe_str(item.get("name"))),
        1.0,
    )
    novelty = next(
        (dimension_score(item.get("score")) for item in dimensions if "新锐" in safe_str(item.get("name"))),
        1.0,
    )
    product = (classicity or 1.0) * (novelty or 1.0)
    if product <= 0.8:
        return "🐟 臭鱼烂虾 / 早该弃坑"
    if product <= 1.1:
        return "🥱 平庸之作 / 初级模仿者"
    if product <= 1.5:
        return "🌱 初露头角 / 潜力股"
    if product <= 1.9:
        return "🔥 市场热门 / 惹眼新秀"
    if product <= 2.4:
        return "🏆 时代作家 / 高产佳作"
    return "🪙 永垂不朽 / 文学圣徒"


def build_openai_payload(
    model: str,
    system_prompt: str,
    article_text: str,
    temperature: float,
    json_mode: bool,
) -> dict[str, Any]:
    """Build an OpenAI-compatible chat completion payload."""

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": article_text},
        ],
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def to_gemini_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI-style messages to Gemini generateContent payload."""

    messages = payload.get("messages", [])
    system_parts: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = safe_str(message.get("content"))
        if not content:
            continue
        if role == "system":
            system_parts.append({"text": content})
        else:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})

    generation_config: dict[str, Any] = {"temperature": payload.get("temperature", DEFAULT_TEMPERATURE)}
    if payload.get("response_format"):
        generation_config["responseMimeType"] = "application/json"

    gemini_payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }
    if system_parts:
        gemini_payload["systemInstruction"] = {"parts": system_parts}
    return gemini_payload


def normalize_analysis(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize model JSON into the report schema expected by the plugin."""

    if not isinstance(parsed.get("dimensions"), list):
        raise TypeError("模型结果缺少 dimensions")

    dimensions = []
    for item in parsed.get("dimensions", []):
        if not isinstance(item, dict):
            continue
        score = dimension_score(item.get("score"))
        dimensions.append(
            {
                "name": safe_str(item.get("name"), "未命名维度"),
                "score": score,
                "description": safe_str(item.get("description")),
            }
        )

    def string_list(key: str) -> list[str]:
        value = parsed.get(key)
        return normalize_model_string_list(value, 12)

    article_style_profile = normalize_style_profile(parsed.get("articleStyleProfile"))
    analysis = {
        "overallScore": calculate_final_score(dimensions),
        "overallAssessment": safe_str(parsed.get("overallAssessment"), "模型未返回综合评价。"),
        "title": safe_str(parsed.get("title"), "未命名评价"),
        "ratingTag": calculate_rating_tag(dimensions),
        "finalTag": safe_str(parsed.get("finalTag"), "可作为基础创作参考"),
        "summary": safe_str(parsed.get("summary"), "模型未返回作品概述。"),
        "tags": string_list("tags"),
        "dimensions": dimensions,
        "strengths": string_list("strengths"),
        "improvements": string_list("improvements"),
        "excellentSentences": normalize_excellent_sentences(parsed.get("excellentSentences")),
        "authorMatches": normalize_author_matches(parsed.get("authorMatches")),
        "mermaid_diagrams": normalize_mermaid_diagrams(parsed.get("mermaid_diagrams")),
    }
    if article_style_profile:
        analysis["articleStyleProfile"] = article_style_profile
    return analysis


def format_report_markdown(analysis: dict[str, Any]) -> str:
    """Format a concise Chinese markdown report for chat display."""

    def extend_profile_list(label: str, value: Any) -> None:
        items = normalize_model_string_list(value, 8)
        lines.append(f"{label}：")
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("-")

    lines = [
        f"# {analysis.get('title') or '文本分析'}",
        "",
        f"综合战力评分：{analysis.get('overallScore')}/100",
        f"评级：{analysis.get('ratingTag')} / {analysis.get('finalTag')}",
        "",
        "## 作品概述",
        safe_str(analysis.get("summary")),
        "",
        "## 综合评价",
        safe_str(analysis.get("overallAssessment")),
    ]

    profile = analysis.get("articleStyleProfile")
    if isinstance(profile, dict):
        lines.extend(
            [
                "",
                "## 当前作品文风画像",
                f"风格标签：{safe_str(profile.get('styleLabel'), '-')}",
                f"体裁类型：{safe_str(profile.get('genreType'), '-')}",
                f"故事内容：{safe_str(profile.get('storyContent'), '-')}",
                f"核心表达：{safe_str(profile.get('coreExpression'), '-')}",
                f"表达节奏：{safe_str(profile.get('expressionRhythm'), '-')}",
            ]
        )
        extend_profile_list("语言习惯", profile.get("languageHabits"))
        extend_profile_list("句式结构", profile.get("sentenceStructures"))
        extend_profile_list("意象偏好", profile.get("imageryPreferences"))
        lines.extend(
            [
                f"关键词：{' / '.join(normalize_model_string_list(profile.get('keywords'), 12)) or '-'}",
                safe_str(profile.get("summary")),
            ]
        )

    lines.extend(["", "## 维度评分"])
    for dimension in analysis.get("dimensions", []):
        if not isinstance(dimension, dict):
            continue
        lines.append(f"- {dimension.get('name')}: {dimension.get('score')} - {dimension.get('description')}")

    strengths = analysis.get("strengths", [])
    if isinstance(strengths, list) and strengths:
        lines.extend(["", "## 优势"])
        lines.extend(f"- {safe_str(item)}" for item in strengths if safe_str(item))

    improvements = analysis.get("improvements", [])
    if isinstance(improvements, list) and improvements:
        lines.extend(["", "## 改进建议"])
        lines.extend(f"- {safe_str(item)}" for item in improvements if safe_str(item))

    return "\n".join(lines).strip()
