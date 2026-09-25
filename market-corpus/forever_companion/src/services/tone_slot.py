"""潮汐时刻 —— 语气分析模型槽位解析与直连（纯函数模块）。

输入是宿主 core_config.json 的 dict（由主类 _load_core_config 提供，含 5s 缓存，
缓存是实例状态故留在主类），把 [emotion_sense].slot 的槽位选择解析成可直连的
{"model", "api_key", "base_url"}；直连用 stdlib urllib 同步请求（调用处
asyncio.to_thread 包裹，不阻塞事件循环）。任何一步失败都返回 None 静默降级。
常量（槽位前缀/管理簿字段/标签归一化）来自 state.py；主类保留同名薄委托，
测试对实例 monkeypatch（_post_chat_completion/_load_core_config）的链路不变。
"""

from __future__ import annotations

from typing import Any

from ..core.state import (
    _ASSIST_KEY_FIELDS,
    _TONE_EMOTION_ALIASES,
    _TONE_SLOT_PREFIXES,
)

JsonObject = dict[str, Any]


def _resolved_provider_url(core_cfg: JsonObject, book: str, provider: str) -> str:
    """resolvedProviderUrls.<book>[provider]：用户保存过的该 provider 端点 URL，取不到 → ""。"""
    urls = core_cfg.get("resolvedProviderUrls")
    if not isinstance(urls, dict):
        return ""
    group = urls.get(book)
    if not isinstance(group, dict):
        return ""
    return str(group.get(provider) or "").strip()


def _resolve_tone_core(core_cfg: JsonObject, prefix: str) -> JsonObject | None:
    """follow_core：跟核心 API 的 provider/key/url，模型名仍取槽位自己的 ModelId。

    coreApi 为 free（宿主内部代理，插件直连会 401）或 URL 未保存过时不可直连 → None。
    """
    provider = str(core_cfg.get("coreApi") or "").strip()
    if not provider or provider == "free":
        return None
    base_url = _resolved_provider_url(core_cfg, "core", provider)
    model = str(core_cfg.get(f"{prefix}ModelId") or "").strip()
    if not base_url or not model:
        return None
    return {
        "model": model,
        "api_key": str(core_cfg.get("coreApiKey") or "").strip(),
        "base_url": base_url,
    }


def _assist_route_is_free(core_cfg: JsonObject) -> bool:
    """当前辅助 API 是否免费路由（含 assistApi 缺失时按 coreApi=free 推导的默认）。

    免费路由（lanlan.tech /text/v1）有服务端客户端校验：只允许 N.E.K.O 宿主自己
    调用，第三方（含插件）直连会被 400 "not using Lanlan" 拒绝——这不是缺配置，
    是防滥用机制，插件无法也不应绕过。单独判定它，让休眠日志能给出准确指引。
    """
    provider = str(core_cfg.get("assistApi") or "").strip()
    if not provider:
        provider = "free" if str(core_cfg.get("coreApi") or "").strip() == "free" else "qwen"
    return provider == "free"


def _resolve_tone_assist(core_cfg: JsonObject, prefix: str) -> JsonObject | None:
    """follow_assist/空串：跟辅助 API（管理簿 key + 已保存 URL），模型名取槽位 ModelId。

    assistApi 缺失时按宿主默认规则推导：coreApi=='free' → 'free'，否则 'qwen'；
    free 是宿主内部代理（服务端校验拒第三方直连）、未知 provider 没有管理簿字段，
    都不可直连 → None。
    """
    provider = str(core_cfg.get("assistApi") or "").strip()
    if not provider:
        provider = "free" if str(core_cfg.get("coreApi") or "").strip() == "free" else "qwen"
    if provider == "free":
        return None
    key_field = _ASSIST_KEY_FIELDS.get(provider)
    if not key_field:
        return None
    base_url = _resolved_provider_url(core_cfg, "assist", provider)
    model = str(core_cfg.get(f"{prefix}ModelId") or "").strip()
    if not base_url or not model:
        return None
    return {
        "model": model,
        "api_key": str(core_cfg.get(key_field) or "").strip(),
        "base_url": base_url,
    }


def diagnose_slot_dormancy(core_cfg: JsonObject, slot: str) -> str:
    """槽位解析不出时的休眠原因诊断（纯函数，给日志/面板提示用）。

    返回稳定的 reason id：
    - "free_route"：宿主在用免费路由（lanlan.tech），服务端只认 N.E.K.O 客户端，
      插件直连必被拒——需要在宿主设置里配自己的 API 才能用本功能
    - "no_model"：所选槽位在宿主没有配置模型（ModelId 空 / 未保存 URL 等）
    - ""：不该出现在解析失败时（防御性兜底，按 no_model 处理）

    判定与 _resolve_tone_slot 的解析链保持同一语义：custom/具名 provider 的槽
    只看槽位自己的 Url+Id（不碰 core/assist 免费路由）；follow_* 链路才看
    coreApi/assistApi 是否 free。
    """
    prefix = _TONE_SLOT_PREFIXES.get(slot)
    if not prefix:
        return "no_model"
    provider = str(core_cfg.get(f"{prefix}ModelProvider") or "").strip()
    if provider in ("follow_conversation", "follow_summary"):
        # 跟随其他槽：被跟随槽的 provider 决定走哪条链，递归诊断
        return diagnose_slot_dormancy(core_cfg, provider[len("follow_"):])
    if provider in ("follow_core", "follow_assist", ""):
        # 跟随链路最终落到 core/assist 上：只要有一条是 free 路由就按 free 报
        #（core=free 时宿主默认推导 assist=free，两条通常同时成立）
        core_free = str(core_cfg.get("coreApi") or "").strip() == "free"
        if core_free or _assist_route_is_free(core_cfg):
            return "free_route"
        return "no_model"
    # custom / 具名 provider：槽位自己的 Url+Id 配齐即可直连（key 允许为空，
    # 本地端点常无鉴权）；配不齐按未配模型报。免费路由与此分支无关。
    base_url = str(core_cfg.get(f"{prefix}ModelUrl") or "").strip()
    model = str(core_cfg.get(f"{prefix}ModelId") or "").strip()
    if not base_url or not model:
        return "no_model"
    return ""


def _slot_dormancy_hint(core_cfg: JsonObject, slot: str) -> str:
    """槽位休眠原因 → 一句可操作的提示（日志与面板提示共用）。

    本函数是 `diagnose_slot_dormancy` 的文案层，两者同处一文件：reason 每多一个、
    文案也只多一份，分处两地必然漂移。过去主类与三个 mixin 各自本地复刻过同一
    函数，注释写的"避免循环导入"并不成立——本模块不 import 任何 mixin。

    免费路由是宿主防滥用边界：lanlan.tech 端点服务端校验客户端身份，插件直连
    必被 400 拒绝（实测），故明确告知"配自己的 API 才可用"而不是含糊的"未配模型"。

    名字保留下划线前缀：__init__.py 导入即再导出，它是现有测试的 tm.* 锚点。
    """
    reason = diagnose_slot_dormancy(core_cfg, slot)
    if reason == "free_route":
        return (
            "宿主正在使用免费路由（lanlan.tech），该端点只接受 N.E.K.O 客户端调用，"
            "插件无法直连——在宿主设置里配置自己的 API 服务商后本功能即可使用"
        )
    return "所选槽位在宿主未配置模型（或未保存服务商 URL），去宿主设置配置该槽位的模型"


def _resolve_tone_slot(
    core_cfg: JsonObject, slot: str, _seen: frozenset[str] = frozenset()
) -> JsonObject | None:
    """把槽位选择解析成 {"model", "api_key", "base_url"}；不可直连一律返回 None（静默降级）。

    解析链：follow_conversation/follow_summary 递归到对应槽（_seen 防环）；
    follow_core → 核心 API；follow_assist/空串 → 辅助 API；custom/具名 provider →
    槽位自己的 ModelUrl/ModelId（缺任一回落辅助 API），key 取槽位 ModelApiKey，
    空则回落该 provider 管理簿 key（custom 无管理簿，空 key 也允许——本地端点常无 key）。
    """
    prefix = _TONE_SLOT_PREFIXES.get(slot)
    if not prefix or slot in _seen:
        return None
    provider = str(core_cfg.get(f"{prefix}ModelProvider") or "").strip()
    if provider in ("follow_conversation", "follow_summary"):
        return _resolve_tone_slot(core_cfg, provider[len("follow_"):], _seen | {slot})
    if provider == "follow_core":
        return _resolve_tone_core(core_cfg, prefix)
    if provider in ("", "follow_assist"):
        return _resolve_tone_assist(core_cfg, prefix)
    # custom 或具名服务商：槽位显式配置了端点与模型才算数
    base_url = str(core_cfg.get(f"{prefix}ModelUrl") or "").strip()
    model = str(core_cfg.get(f"{prefix}ModelId") or "").strip()
    if not base_url or not model:
        return _resolve_tone_assist(core_cfg, prefix)
    api_key = str(core_cfg.get(f"{prefix}ModelApiKey") or "").strip()
    if not api_key and provider != "custom":
        key_field = _ASSIST_KEY_FIELDS.get(provider)
        if key_field:
            api_key = str(core_cfg.get(key_field) or "").strip()
    return {"model": model, "api_key": api_key, "base_url": base_url}


def _exc_shape(exc: BaseException) -> str:
    """异常 → 脱敏诊断串：只取类型名与（若有的）HTTP 状态码，**永不** str(exc)。

    隐私契约（DESIGN「隐私与日志脱敏契约」）：urllib 异常的原始 message 可能携带
    完整 URL——自定义端点里嵌 userinfo 或 `?api-key=` 查询参数的服务商（宿主日志
    脱敏正则不覆盖连字符变体）会随异常串原样落进插件日志文件。类型名 + code
    足够区分 DNS/拒绝连接/401/429 这一类诊断分级（宿主规范：优先记录脱敏后的
    长度、ID 和错误类型）。
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return f"{type(exc).__name__}(code={code})"
    return type(exc).__name__


def _payload_shape(payload: object) -> str:
    """响应 payload → 结构摘要（类型/顶层键名/条数/字节长度），不输出任何值。

    "返回不是可用的 OpenAI 形态"这件事由形态字段完全判定（缺 choices、error 信封、
    网关劫持页的 HTML 顶层非 dict），键名与长度足够定位；payload 值可能被上游
    回显成对话内容，按契约不进日志。
    """
    if isinstance(payload, dict):
        parts = [f"keys={sorted(str(k)[:24] for k in payload)[:6]}"]
        choices = payload.get("choices")
        if isinstance(choices, list):
            parts.append(f"choices={len(choices)}")
        parts.append(f"bytes={len(str(payload))}")
        return "dict " + " ".join(parts)
    if isinstance(payload, (bytes, str)):
        return f"{type(payload).__name__} bytes={len(payload)}"
    return type(payload).__name__


def _post_chat_completion(
    base_url: str, api_key: str, model: str, prompt: str, logger: Any = None
) -> str | None:
    """同步直连 OpenAI 兼容 /chat/completions（stdlib urllib，零依赖）；任何失败 → None。

    调用处用 asyncio.to_thread 包裹，不阻塞事件循环。空 key 不加 Authorization
    头（本地端点常无鉴权）。logger 由调用方（主类委托）传入；为 None 时静默。
    """
    import json as _json
    import urllib.request

    url = f"{base_url.rstrip('/')}/chat/completions"
    body = _json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if api_key:
        req_headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, method="POST", headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 直连失败仍降级，但必须留痕（1.2.2 审查轮）
        # warning 而非 debug：debug 不进日志文件，面板提示"详见插件日志"时会
        # 出现"让用户查、又查不到"的诊断盲区；调用点都有分钟级节流，不会刷屏。
        # 1.3.0 第十轮：只记脱敏形态（类型+code），裸 exc 串可能带自定义端点
        # URL/凭据，见 _exc_shape 的隐私契约
        if logger is not None:
            logger.warning("tone direct chat completion failed: {}", _exc_shape(exc))
        return None
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
    # HTTP 通了但响应不是可用的 OpenAI 形态（错误体被 200 返回/网关劫持页等）：
    # 过去同样静默 None，与"请求失败"无从区分——留一条结构摘要 warning（1.3.0
    # 第十轮起只记形态不记内容：旧版把响应前 80 字符切片直打日志，而响应值
    # 可能被上游回显成对话内容，违反隐私契约（旧写法字串已被回归门全文本钉死）
    if logger is not None:
        logger.warning("tone direct chat completion returned no usable content: {}", _payload_shape(payload))
    return None


def _parse_tone_result(raw: str) -> tuple[str, float] | None:
    """解析直连模型的五分类 JSON 回复；容错剥 ```json 围栏，中文标签归一化为英文。

    confidence 缺失 → 0.65（中性默认）；confidence 非法 / JSON 坏 / 标签不识别 → None。
    """
    import json as _json

    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = _json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    raw_label = str(data.get("emotion") or "").strip()
    label = _TONE_EMOTION_ALIASES.get(raw_label) or _TONE_EMOTION_ALIASES.get(raw_label.lower())
    if label is None:
        return None
    raw_confidence = data.get("confidence")
    if raw_confidence is None:
        return label, 0.65
    try:
        return label, float(raw_confidence)
    except (TypeError, ValueError):
        return None
