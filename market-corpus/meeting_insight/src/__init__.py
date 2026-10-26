"""会议记录 / 可视化 / 总结 插件。

``meeting_summarize`` 有两条路径：

* **联网路径**（配置了 ``deepseek_api_key``）：把会议转写拼成 Prompt，通过
  DeepSeek 的 Anthropic 兼容端点（``/anthropic/v1/messages``）调用模型，并在
  请求里声明服务端 ``web_search`` 工具 —— 是否联网由模型自行决定，检索结果由
  服务端注入并以 ``web_search_tool_result`` 块返回。
* **本地回退路径**（未配置 key / API 调用失败）：退回 ``_summarize_local`` 的
  纯字符串结构化提取，并在输出里标记 ``fallback: true``。

网络访问一律走 ``httpx.AsyncClient``；模块顶层不做任何 IO。
"""
from __future__ import annotations

import json
import re
import time

import httpx
from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_MAX_POINTS = 5
MAX_POINTS_CAP = 20

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_WEB_SEARCH_MAX_USES = 5
MAX_WEB_SEARCH_USES_CAP = 10
DEFAULT_REQUEST_TIMEOUT = 60.0

# DeepSeek 的 Anthropic 兼容入口。
_ANTHROPIC_MESSAGES_PATH = "/anthropic/v1/messages"
# 服务端联网检索工具的版本标识（Anthropic Messages API 的 tool type）。
_WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
_MAX_OUTPUT_TOKENS = 4096
_MAX_RELATED_CONTEXT = 5

# 单次工具调用的总预算。``@llm_tool(timeout=60.0)`` 是宿主侧硬上限：真实的
# HTTP 往返必须在这个窗口内结束，否则会被宿主截断成 TOOL_TIMEOUT，看不到真实
# 错误。这里留 5s 余量给 IPC 与序列化。
_TOTAL_BUDGET_SECONDS = 55.0
_MIN_REQUEST_TIMEOUT = 5.0

# 病态输入保护：超长转写先截断，避免单次工具调用长时间占用对话轮。
_MAX_TRANSCRIPT_CHARS = 20_000
_MAX_SENTENCES = 400
_MIN_SENTENCE_CHARS = 4
_MAX_SUMMARY_BODY_CHARS = 300

# 句子终止符：中文标点；或英文句号后跟空白/结尾（避免误切 3.5 / v1.2）。
_SENTENCE_RE = re.compile(
    r"[^。！？!?；;\n]+?(?:[。！？!?；;]|\.(?=\s|$)|\n|$)"
)

# 待办信号词：命中即视为「行动项」。比较前统一 casefold。
_ACTION_HINTS = (
    "需要", "应该", "负责", "跟进", "安排", "确认", "完成", "推进",
    "下周", "明天", "之前", "截止", "尽快", "落实", "待办",
    "todo", "action", "follow up", "follow-up", "assign",
    "deadline", "next step", "owner",
)

# ---------------------------------------------------------------------------
# 纯函数辅助层：无 IO、无副作用、无 await，可直接单测
# ---------------------------------------------------------------------------


def _coerce_text(value: object, default: str = "") -> str:
    """安全地把配置值 / API 返回值取成字符串。"""
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _coerce_bool(value: object, default: bool) -> bool:
    """把配置里的布尔值归一化，兼容 ``"true"`` / ``"1"`` 这类字符串写法。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_timeout(value: object, default: float) -> float:
    """把配置里的超时值归一化为正浮点数，非法值回落到默认值。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except (TypeError, ValueError):
            return default
    else:
        return default
    return parsed if parsed > 0 else default


def _http_status_of(exc: BaseException) -> int | str:
    """从异常里安全取出 HTTP 状态码，取不到就返回 ``"-"``。

    只取状态码，绝不碰 response body —— body 可能回显请求内容。有了它，
    「端点/工具类型不被支持」（通常 400）与「鉴权失败」（401）、「路径写错」
    （404）才能在日志里区分开。纯函数。
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status
    return "-"


def _as_text_list(value: object, *, max_items: int) -> list[str]:
    """把模型返回的任意形状归一成字符串列表。

    模型有概率把数组写成字符串、把要点写成 ``{"point": "..."}``、或给出超长
    列表。这里统一兜底，保证交付给对话模型的结构稳定。
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        text = ""
        if isinstance(entry, str):
            text = entry.strip()
        elif isinstance(entry, dict):
            for key in ("point", "text", "title", "item"):
                candidate = entry.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate.strip()
                    break
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _normalize_limit(value: object) -> int:
    """把模型传来的 ``max_points`` 归一化到 1..MAX_POINTS_CAP。

    模型偶尔会传字符串 / 布尔 / None / 超界值。这里就地兜底而不是抛异常：
    抛异常会让工具结果退化成一个模型看不懂的通用错误信封。
    """
    if isinstance(value, bool):  # bool 是 int 的子类，必须先挡掉
        return DEFAULT_MAX_POINTS
    if isinstance(value, (int, float)):
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(float(value.strip()))
        except (TypeError, ValueError):
            return DEFAULT_MAX_POINTS
    else:
        return DEFAULT_MAX_POINTS
    return max(1, min(parsed, MAX_POINTS_CAP))


def _normalize_web_search_max_uses(value: object) -> int:
    """把 ``web_search_max_uses`` 钳制到 1..MAX_WEB_SEARCH_USES_CAP。"""
    if isinstance(value, bool):
        return DEFAULT_WEB_SEARCH_MAX_USES
    if isinstance(value, (int, float)):
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(float(value.strip()))
        except (TypeError, ValueError):
            return DEFAULT_WEB_SEARCH_MAX_USES
    else:
        return DEFAULT_WEB_SEARCH_MAX_USES
    return max(1, min(parsed, MAX_WEB_SEARCH_USES_CAP))


def _split_sentences(text: str) -> list[str]:
    """按中英文标点切句：保序、去空白、丢弃过短片段、限制总句数。"""
    sentences: list[str] = []
    for raw in _SENTENCE_RE.findall(text):
        cleaned = " ".join(raw.split())
        if len(cleaned) >= _MIN_SENTENCE_CHARS:
            sentences.append(cleaned)
        if len(sentences) >= _MAX_SENTENCES:
            break
    return sentences


def _pick_key_points(sentences: list[str], limit: int) -> list[str]:
    """取最长的若干个句子作为核心要点，输出时恢复原文顺序。

    按长度挑选、按原文顺序输出：挑选依据是「信息量」，但阅读顺序必须是
    会议发生的顺序，否则要点列表会显得颠三倒四。
    """
    if not sentences:
        return []
    ranked = sorted(
        range(len(sentences)),
        key=lambda index: len(sentences[index]),
        reverse=True,
    )
    chosen = sorted(ranked[:limit])
    return [sentences[index] for index in chosen]


def _extract_action_items(sentences: list[str], limit: int) -> list[str]:
    """按待办信号词挑行动项：保序、去重、限量。"""
    items: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        lowered = sentence.casefold()
        if not any(hint in lowered for hint in _ACTION_HINTS):
            continue
        if sentence in seen:
            continue
        seen.add(sentence)
        items.append(sentence)
        if len(items) >= limit:
            break
    return items


def _build_summary(
    sentences: list[str],
    key_points: list[str],
    action_count: int,
) -> str:
    """拼一段可读的模拟摘要（统计概览 + 要点原文）。"""
    if not sentences:
        return "（转写文本未包含可识别的句子，无法生成摘要）"
    head = f"本次会议共识别 {len(sentences)} 句发言，提炼出 {len(key_points)} 条核心要点"
    if action_count:
        head += f"、{action_count} 条待办事项"
    head += "。"
    body = " ".join(key_points)
    if len(body) > _MAX_SUMMARY_BODY_CHARS:
        body = body[:_MAX_SUMMARY_BODY_CHARS].rstrip() + "…"
    return f"{head}{body}"


def _summarize_local(transcript: str, limit: int) -> dict[str, object]:
    """本地结构化提取：切句 → 挑核心要点 → 挑待办 → 拼摘要。

    纯字符串处理，2 万字符量级为毫秒级，不阻塞事件循环，无需 to_thread。
    """
    sentences = _split_sentences(transcript)
    key_points = _pick_key_points(sentences, limit)
    action_items = _extract_action_items(sentences, limit)
    return {
        "summary": _build_summary(sentences, key_points, len(action_items)),
        "key_points": key_points,
        "action_items": action_items,
        "related_context": [],
    }


def _build_prompt(transcript: str) -> str:
    """把会议转写拼成给 DeepSeek 的详尽 Prompt。

    纯函数：无 IO、无副作用，便于单测。

    Prompt 明确告知模型可以使用服务端 ``web_search`` 工具自行联网，并要求它用
    ``[来源 N]`` 标注引用。提示词里必须保留 "JSON" 字样：结构化输出的约束在
    Anthropic 兼容端点由提示词承担，去掉这个词会让模型更容易输出散文。
    """
    return (
        "你是一名资深会议纪要分析师。请阅读下方的【会议转写】，"
        "输出一份结构化的会议洞察报告。\n\n"
        "## 可用工具\n"
        "你可以使用 web_search 工具联网检索相关信息，用于补充会议中提到的外部"
        "背景（产品、公司、技术、政策、人物、事件等）。是否检索由你自行判断："
        "只有当会议内容涉及你不确定、或需要较新外部信息的概念时才检索，"
        "不要为了检索而检索。\n\n"
        "## 输出格式\n"
        "只输出一个 JSON 对象。不要输出任何解释性文字，不要用 Markdown 代码块"
        "包裹，不要在 JSON 前后添加任何字符。JSON 必须严格符合以下结构：\n"
        "{\n"
        '  "summary": "字符串。一段连贯的会议摘要，150-400 字，涵盖会议主题、'
        '关键结论与整体走向。必须是完整段落，不能是要点罗列。",\n'
        '  "key_points": ["字符串数组。3-8 条核心要点，每条一句话，'
        '按会议实际发生的顺序排列。"],\n'
        '  "action_items": ["字符串数组。待办事项，每条包含具体动作，'
        '并在原文提到时带上负责人与时间。原文没有明确待办时返回空数组。"],\n'
        '  "related_context": ["字符串数组。基于联网检索到的资料对会议内容所做的'
        '背景补充。每条须写明它补充了会议中的哪个话题，并用 [来源 N] 标注所引用'
        '的网页（N 与检索结果的出现顺序一致）。没有检索、或检索结果与会议无关时'
        '返回空数组。"]\n'
        "}\n\n"
        "## 规则\n"
        "1. 使用会议转写原本的语言作答，不要翻译成其他语言。\n"
        "2. 只依据给定材料与检索结果，不要编造未出现的事实、人名、数字、日期或结论。\n"
        "3. key_points 按会议时间顺序排列，不要把不同话题合并成一条。\n"
        "4. action_items 必须可执行；原文未指明负责人时只写动作，不要臆造人名。\n"
        "5. related_context 只用于补充背景，不得覆盖或改写会议原文的结论；"
        "如果检索结果与会议内容无关，宁可返回空数组。\n"
        "6. 所有数组字段必须是 JSON 数组；没有内容时用 []，不要用 null。\n"
        "7. 字段名必须与上面的结构完全一致，不要增加或删除任何字段。\n"
        "8. 引用网页时使用 [来源 N] 标注，不要直接粘贴长 URL。\n\n"
        f"## 会议转写\n{transcript}\n"
    )


def _build_search_prompt(query: str) -> str:
    """构造通用联网搜索工具（``api_web_search``）用的 Prompt。纯函数。

    与会议模板无关：只要求模型检索后输出 ``{"summary", "sources"}``。
    提示词里必须出现 "JSON" 字样，并明确禁止散文与 Markdown 代码块包裹。
    """
    return (
        "你是一个联网搜索助手。请使用 web_search 工具检索以下问题，"
        "然后只输出一个 JSON 对象，不要输出任何其他文字。\n"
        "不要使用 Markdown 代码块包裹，不要在 JSON 前后添加任何字符。\n"
        "JSON 结构如下：\n"
        "{\n"
        '  "summary": "用一段话回答用户的问题，基于搜索结果，不要编造",\n'
        '  "sources": ["[来源 1] 标题 — URL", "[来源 2] 标题 — URL"]\n'
        "}\n"
        "如果检索不到有用信息，summary 要如实说明未能找到，不要臆造事实；"
        "sources 返回空数组 []。\n\n"
        f"用户问题：{query}\n"
    )


def _extract_citations(block: dict[str, object]) -> list[dict[str, object]]:
    """从检索结果块里提取并归一化引用来源。

    兼容两种返回形态（实测都出现过）：

    * **扁平结构** —— 块本身就是一条结果，``title`` / ``url`` 直接挂在块上。
    * **嵌套结构** —— ``block["content"]`` 是数组，每个元素是一条结果。

    优先按扁平结构解析；只有扁平结构里取不到 ``url`` 时，才回退到嵌套结构。
    只保留带 ``url`` 的条目，统一成 ``{"title": str, "url": str, "snippet": str}``。
    纯函数。
    """
    if _coerce_text(block.get("url")):
        candidates: list[object] = [block]
    else:
        raw = block.get("content")
        if isinstance(raw, dict):
            candidates = [raw]
        elif isinstance(raw, list):
            candidates = list(raw)
        else:
            candidates = []

    citations: list[dict[str, object]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = _coerce_text(item.get("url"))
        if not url:
            continue
        snippet = (
            _coerce_text(item.get("snippet"))
            or _coerce_text(item.get("text"))
            or _coerce_text(item.get("content"))
        )
        citations.append({
            "title": _coerce_text(item.get("title")),
            "url": url,
            "snippet": snippet,
        })
    return citations


def _citations_to_context(citations: list[dict[str, object]]) -> list[str]:
    """把服务端引用列表渲染成 ``related_context`` 条目。纯函数。"""
    lines: list[str] = []
    for index, item in enumerate(citations, 1):
        if not isinstance(item, dict):
            continue
        url = _coerce_text(item.get("url"))
        if not url:
            continue
        title = _coerce_text(item.get("title")) or "（无标题）"
        snippet = _coerce_text(item.get("snippet"))
        line = f"[来源 {index}] {title} — {url}"
        if snippet:
            line += f"：{snippet}"
        lines.append(line)
        if len(lines) >= _MAX_RELATED_CONTEXT:
            break
    return lines


def _normalize_llm_result(
    payload: dict[str, object],
    limit: int,
) -> dict[str, object]:
    """把模型返回的 dict 归一到插件对外承诺的输出结构。

    模型输出永远不可信：字段可能缺失、类型可能不对、数组可能超长。
    """
    summary = _coerce_text(payload.get("summary"))
    if not summary:
        summary = "（模型未返回摘要，请参考下方核心要点。）"
    return {
        "summary": summary,
        "key_points": _as_text_list(payload.get("key_points"), max_items=limit),
        "action_items": _as_text_list(payload.get("action_items"), max_items=limit),
        "related_context": _as_text_list(
            payload.get("related_context"),
            max_items=_MAX_RELATED_CONTEXT,
        ),
    }


# ---------------------------------------------------------------------------
# 异步网络层：只使用 httpx.AsyncClient，绝不使用 requests
# ---------------------------------------------------------------------------


async def _call_deepseek_with_search(
    prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    *,
    anthropic_version: str,
    web_search_enabled: bool,
    web_search_max_uses: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """调用 DeepSeek 的 Anthropic 兼容端点，可选择启用服务端联网检索。

    返回 ``(parsed_json_dict, citations_list)``：

    * ``parsed_json_dict`` —— 把所有 ``type == "text"`` 内容块按顺序拼接后解析
      出的 JSON 对象；拼接结果不是合法 JSON 对象时抛
      ``ValueError("ANTHROPIC_NON_OBJECT_JSON")``。
    * ``citations_list`` —— 从所有 ``web_search_tool_result`` 块中提取的引用，
      每项归一化为 ``{"title", "url", "snippet"}``。

    非 2xx / 空 content / 空 text 一律向上抛出，由调用方决定是否降级。
    """
    url = (
        f"{_coerce_text(base_url).rstrip('/') or DEFAULT_DEEPSEEK_BASE_URL}"
        f"{_ANTHROPIC_MESSAGES_PATH}"
    )
    headers = {
        "x-api-key": api_key,
        "anthropic-version": anthropic_version,
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {
        "model": model,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if web_search_enabled:
        payload["tools"] = [
            {
                "type": _WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": web_search_max_uses,
            }
        ]

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, dict):
        raise ValueError("ANTHROPIC_NON_OBJECT_RESPONSE")

    content_blocks = data.get("content")
    if not isinstance(content_blocks, list):
        raise ValueError("ANTHROPIC_EMPTY_CONTENT")

    text_parts: list[str] = []
    citations: list[dict[str, object]] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        block_type = _coerce_text(block.get("type"))
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text)
        elif block_type in ("web_search_result", "web_search_tool_result"):
            citations.extend(_extract_citations(block))

    final_text = "\n".join(text_parts).strip()
    if not final_text:
        raise ValueError("ANTHROPIC_EMPTY_TEXT")

    try:
        parsed = json.loads(final_text)
    except json.JSONDecodeError as exc:
        raise ValueError("ANTHROPIC_NON_OBJECT_JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("ANTHROPIC_NON_OBJECT_JSON")
    return parsed, citations


# ---------------------------------------------------------------------------
# 插件
# ---------------------------------------------------------------------------


@neko_plugin
class MeetingInsightPlugin(NekoPluginBase):
    """会议记录 / 可视化 / 总结插件。"""

    # ---------------- 生命周期 ----------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        section = cfg.get("meeting_insight") if isinstance(cfg, dict) else None
        self._cfg = section if isinstance(section, dict) else {}
        # 只记录「是否配置 / 是否启用」的布尔量，绝不打印 key 本身。
        self.logger.info(
            "meeting_insight started: config_keys={} deepseek_key_configured={}"
            " web_search_enabled={}",
            len(self._cfg),
            bool(_coerce_text(self._cfg.get("deepseek_api_key"))),
            _coerce_bool(self._cfg.get("web_search_enabled"), True),
        )
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self.logger.info("meeting_insight stopped")
        return Ok({"status": "stopped"})

    # ---------------- 可被 Plugin Manager / Agent 路由触发的入口 ----------------

    @plugin_entry(
        id="meeting_status",
        name="Meeting Insight Status",
        description="返回插件当前状态（占位入口，用于验证插件可被触发）。",
        llm_result_fields=["summary"],
    )
    async def meeting_status(self, **_):
        cfg = self._cfg if isinstance(getattr(self, "_cfg", None), dict) else {}
        return Ok({
            "summary": "meeting_insight ready",
            "detail": {
                "stage": "live",
                "deepseek_key_configured": bool(
                    _coerce_text(cfg.get("deepseek_api_key"))
                ),
                "web_search_enabled": _coerce_bool(
                    cfg.get("web_search_enabled"), True
                ),
                "model": _coerce_text(cfg.get("deepseek_model"))
                or DEFAULT_DEEPSEEK_MODEL,
            },
        })

    # ---------------- 对话期 LLM 工具 ----------------

    @llm_tool(
        name="meeting_summarize",
        description=(
            "根据会议转写文本提炼核心要点、会议摘要与待办事项，并可按需联网检索"
            "资料补充背景。当用户要求总结会议、整理会议纪要或提取待办时调用。"
            "transcript 请原样传入转写文本：不要翻译、不要改写、不要省略。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "transcript": {
                    "type": "string",
                    "description": "会议转写文本（保留原始语言，原样传入）",
                },
                "max_points": {
                    "type": "integer",
                    "description": "最多返回的核心要点数（1-20，默认 5）",
                    "default": DEFAULT_MAX_POINTS,
                },
            },
            "required": ["transcript"],
        },
        timeout=60.0,
    )
    async def meeting_summarize(
        self,
        *,
        transcript: str = "",
        max_points: int = DEFAULT_MAX_POINTS,
        **_,
    ):
        # ---- 1. 参数校验 ----
        # 模型可能违反 schema（漏传 / 传 null / 传非字符串）。就地兜底并回一条
        # 结构化提示，而不是让 TypeError 冒到宿主变成通用错误信封。
        if not isinstance(transcript, str) or not transcript.strip():
            return {
                "output": {
                    "summary": None,
                    "key_points": [],
                    "action_items": [],
                    "related_context": [],
                },
                "is_error": True,
                "error": "EMPTY_TRANSCRIPT",
            }

        transcript_len = len(transcript)
        text = transcript[:_MAX_TRANSCRIPT_CHARS]
        truncated = transcript_len > _MAX_TRANSCRIPT_CHARS
        limit = _normalize_limit(max_points)

        # ---- 2. 读取配置（只取用，绝不写进日志）----
        section = self._cfg if isinstance(getattr(self, "_cfg", None), dict) else {}
        api_key = _coerce_text(section.get("deepseek_api_key"))
        base_url = (
            _coerce_text(section.get("deepseek_base_url"))
            or DEFAULT_DEEPSEEK_BASE_URL
        )
        model = _coerce_text(section.get("deepseek_model")) or DEFAULT_DEEPSEEK_MODEL
        anthropic_version = (
            _coerce_text(section.get("anthropic_version"))
            or DEFAULT_ANTHROPIC_VERSION
        )
        web_search_enabled = _coerce_bool(section.get("web_search_enabled"), True)
        web_search_max_uses = _normalize_web_search_max_uses(
            section.get("web_search_max_uses")
        )
        request_timeout = _coerce_timeout(
            section.get("request_timeout"),
            DEFAULT_REQUEST_TIMEOUT,
        )

        # ---- 3. 未配置 key：直接本地回退 ----
        if not api_key:
            return self._local_fallback(
                text=text,
                limit=limit,
                truncated=truncated,
                transcript_len=transcript_len,
                reason="MISSING_DEEPSEEK_API_KEY",
            )

        # ---- 4. 构造 Prompt 并调用（是否联网由模型自行决定）----
        prompt = _build_prompt(text)
        # 只在总预算内发请求：@llm_tool(timeout=60.0) 是宿主侧硬上限，
        # 这里留出余量给 IPC 与序列化，避免卡在 60s 被截断成 TOOL_TIMEOUT。
        api_timeout = max(
            _MIN_REQUEST_TIMEOUT,
            min(request_timeout, _TOTAL_BUDGET_SECONDS),
        )
        api_started = time.monotonic()
        try:
            parsed, citations = await _call_deepseek_with_search(
                prompt,
                api_key,
                base_url,
                model,
                api_timeout,
                anthropic_version=anthropic_version,
                web_search_enabled=web_search_enabled,
                web_search_max_uses=web_search_max_uses,
            )
        except Exception as exc:
            self.logger.warning(
                "meeting_summarize: deepseek failed model={} err_type={} status={}",
                model,
                type(exc).__name__,
                _http_status_of(exc),
            )
            return self._local_fallback(
                text=text,
                limit=limit,
                truncated=truncated,
                transcript_len=transcript_len,
                reason=f"DEEPSEEK_{type(exc).__name__.upper()}",
            )
        api_elapsed_ms = int((time.monotonic() - api_started) * 1000)

        # ---- 5. 归一化模型输出；模型没给 related_context 时用服务端引用兜底 ----
        result = _normalize_llm_result(parsed, limit)
        if not result["related_context"] and citations:
            result["related_context"] = _citations_to_context(citations)
        result["fallback"] = False
        if truncated:
            result["summary"] = self._truncation_note(result["summary"])

        # 只记长度 / 布尔 / 条数 / 耗时：prompt 与 citations 原文均不得外泄。
        self.logger.info(
            "meeting_summarize: fallback=none transcript_len={} search_used={}"
            " citations_count={} api_ms={}",
            transcript_len,
            web_search_enabled,
            len(citations),
            api_elapsed_ms,
        )
        return {"output": result, "is_error": False}

    @llm_tool(
        name="api_web_search",
        description=(
            "【优先使用本工具】当用户明确要求联网搜索、查询最新信息、实时数据、近期事件，"
            "或者需要核实某个事实时，调用本工具。适用于：查询最新新闻、了解某产品或模型的"
            "最新版本、核实某个说法是否属实、查询当前时间点附近发生的事件。不要用于："
            "纯逻辑推理、代码编写、文本改写、无需外部信息的常识问答。本工具通过 DeepSeek "
            "服务端 API 进行联网搜索，会消耗 API 额度，请仅在用户有明确联网搜索需求时调用，"
            "避免不必要的调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要搜索的问题或关键词",
                },
            },
            "required": ["query"],
        },
        timeout=60.0,
    )
    async def api_web_search(self, *, query: str = "", **_):
        # ---- 1. 参数校验：模型可能漏传 / 传 null / 传非字符串 ----
        if not isinstance(query, str) or not query.strip():
            return {
                "output": {"summary": None, "sources": []},
                "is_error": True,
                "error": "EMPTY_QUERY",
            }

        query_len = len(query)

        # ---- 2. 读取配置（只取用，绝不写进日志）----
        section = self._cfg if isinstance(getattr(self, "_cfg", None), dict) else {}
        api_key = _coerce_text(section.get("deepseek_api_key"))
        base_url = (
            _coerce_text(section.get("deepseek_base_url"))
            or DEFAULT_DEEPSEEK_BASE_URL
        )
        model = _coerce_text(section.get("deepseek_model")) or DEFAULT_DEEPSEEK_MODEL
        anthropic_version = (
            _coerce_text(section.get("anthropic_version"))
            or DEFAULT_ANTHROPIC_VERSION
        )
        web_search_enabled = _coerce_bool(section.get("web_search_enabled"), True)
        web_search_max_uses = _normalize_web_search_max_uses(
            section.get("web_search_max_uses")
        )
        request_timeout = _coerce_timeout(
            section.get("request_timeout"),
            DEFAULT_REQUEST_TIMEOUT,
        )

        # ---- 3. 未配置 key：直接拒绝。纯搜索没有本地替代品，不做本地兜底 ----
        if not api_key:
            self.logger.warning(
                "api_web_search rejected: error=MISSING_DEEPSEEK_API_KEY query_len={}",
                query_len,
            )
            return {
                "output": {"summary": None, "sources": []},
                "is_error": True,
                "error": "MISSING_DEEPSEEK_API_KEY",
            }

        # ---- 4. 调用（复用会议链路同一个网络层）----
        prompt = _build_search_prompt(query)
        api_timeout = max(
            _MIN_REQUEST_TIMEOUT,
            min(request_timeout, _TOTAL_BUDGET_SECONDS),
        )
        api_started = time.monotonic()
        try:
            parsed, citations = await _call_deepseek_with_search(
                prompt,
                api_key,
                base_url,
                model,
                api_timeout,
                anthropic_version=anthropic_version,
                web_search_enabled=web_search_enabled,
                web_search_max_uses=web_search_max_uses,
            )
        except Exception as exc:
            # 只记异常类型与状态码：query 原文与响应体都不得外泄。
            self.logger.warning(
                "api_web_search failed: err_type={} status={} query_len={}",
                type(exc).__name__,
                _http_status_of(exc),
                query_len,
            )
            return {
                "output": {"summary": None, "sources": []},
                "is_error": True,
                "error": "SEARCH_FAILED",
                "reason": type(exc).__name__,
            }
        api_elapsed_ms = int((time.monotonic() - api_started) * 1000)

        # ---- 5. 归一化模型输出；模型没给 sources 时用服务端引用兜底 ----
        summary = _coerce_text(parsed.get("summary"))
        sources = _as_text_list(parsed.get("sources"), max_items=_MAX_RELATED_CONTEXT)
        if not sources and citations:
            sources = _citations_to_context(citations)

        self.logger.info(
            "api_web_search: query_len={} citations_count={} api_ms={}",
            query_len,
            len(citations),
            api_elapsed_ms,
        )
        return {
            "output": {
                "summary": summary or "（未能从搜索结果中提炼出回答。）",
                "sources": sources,
            },
            "is_error": False,
        }

    # ---------------- 内部辅助 ----------------

    @staticmethod
    def _truncation_note(summary: object) -> str:
        """给摘要追加截断说明（原文过长时用）。"""
        return (
            f"{_coerce_text(summary)}"
            f"（转写过长，已截断至前 {_MAX_TRANSCRIPT_CHARS} 字符）"
        )

    def _local_fallback(
        self,
        *,
        text: str,
        limit: int,
        truncated: bool,
        transcript_len: int,
        reason: str,
    ) -> dict[str, object]:
        """本地结构化提取兜底，并在输出里标记 ``fallback: true``。

        三条路径共用：未配置 key、API 调用失败、API 直接抛出。
        """
        result = _summarize_local(text, limit)
        result["fallback"] = True
        result["fallback_reason"] = reason
        if truncated:
            result["summary"] = self._truncation_note(result["summary"])

        self.logger.info(
            "meeting_summarize: fallback=local reason={} transcript_len={}"
            " points={} actions={}",
            reason,
            transcript_len,
            len(result["key_points"]),
            len(result["action_items"]),
        )
        return {"output": result, "is_error": False}
