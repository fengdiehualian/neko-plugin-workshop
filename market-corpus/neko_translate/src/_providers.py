"""翻译提供方：全部走公开免密钥接口。

实测结论（2026-10，中国大陆网络）：

- `mymemory` 公共翻译记忆库：**默认首选，实测稳定可用**
- `gtrans`   Google 公开 web 端点：国内基本不可达，仅作兜底
- `lingva`   Lingva 公共实例：实测被 Cloudflare 挡住，需要用户自建实例才可用

所以默认顺序是 `["mymemory", "gtrans"]`，`lingva` 只在用户填了自己的实例地址后才建议启用。
全部免密钥，开箱即用。
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Optional

from ._http import HttpError, get_json, get_text

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
LINGVA_DEFAULT = "https://lingva.ml"
GTRANS_URL = "https://translate.googleapis.com/translate_a/single"

MAX_CHUNK = 450  # MyMemory 对单次 q 的长度限制较紧，按句切块更稳


class TranslationFailed(RuntimeError):
    """所有提供方都失败。"""


# --------------------------------------------------------------- 猫娘语气

DEFAULT_CAT_SUFFIX = "喵～"


def cat_suffix(cute: bool = True, custom: str = "") -> str:
    if not cute:
        return ""
    return (custom or "").strip() or DEFAULT_CAT_SUFFIX


def cat_style(text: str, *, cute: bool = True, custom_suffix: str = "") -> str:
    """给译文加猫娘语气。

    只对中文目标语言加后缀 —— 往英语/日语里塞「喵」会破坏译文本身。

    后缀接在句末标点**之后**：「早上好！喵～」而不是「早上好喵～！」，
    读起来才自然。
    """
    if not cute or not text:
        return text
    meow = cat_suffix(cute, custom_suffix)
    stripped = text.rstrip()
    if not stripped:
        return text
    # 已经有喵就别重复
    if "喵" in stripped[-6:]:
        return stripped
    # 把结尾的标点收出来，后缀排在它后面
    tail = ""
    while stripped and stripped[-1] in "。！？.!?～~":
        tail = stripped[-1] + tail
        stripped = stripped[:-1]
    if not stripped:
        # 整段都是标点，直接接在后面
        return f"{meow}{tail}"
    return f"{stripped}{tail}{meow}"


def to_iso1(code: str) -> str:
    """把规范码折成 ISO-639-1；中文按 MyMemory 的惯例用 zh 系列。"""
    if not code or code == "auto":
        return "auto"
    base = code.split("-")[0].lower()
    if code == "zh-CN":
        return "zh-CN"
    if code == "zh-TW":
        return "zh-TW"
    return base


def split_chunks(text: str, limit: int = MAX_CHUNK) -> list[str]:
    """按段落/句子切块，尽量不破坏语义边界。"""
    if len(text) <= limit:
        return [text]

    # 先按换行切，再把过长的行按句末标点切
    rough: list[str] = []
    for line in text.splitlines(keepends=True):
        if len(line) <= limit:
            rough.append(line)
            continue
        parts = re.split(r"(?<=[.!?。！？；;])\s*", line)
        buf = ""
        for part in parts:
            if not part:
                continue
            if len(buf) + len(part) <= limit:
                buf += part
            else:
                if buf:
                    rough.append(buf)
                buf = part
        if buf:
            rough.append(buf)

    # 兜底：仍然超长的硬切
    chunks: list[str] = []
    for item in rough:
        while len(item) > limit:
            chunks.append(item[:limit])
            item = item[limit:]
        if item:
            chunks.append(item)
    return chunks or [text]


def looks_untranslated(source: str, translated: str) -> bool:
    """判断提供方是不是原样把输入吐回来了（=没翻译）。

    实测 MyMemory 在源语言被猜错时会把候选记忆原样返回（例如把法语
    "Bonjour tout le monde." 当成英语，结果原句返回）。这种结果必须当成失败，
    否则降级链不会继续尝试，用户会拿到"看起来成功其实没翻译"的答案。
    """
    left = " ".join(str(source or "").split()).strip().strip("。.!！?？,，;；:：")
    right = " ".join(str(translated or "").split()).strip().strip("。.!！?？,，;；:：")
    if not left or not right:
        return False
    return left.casefold() == right.casefold()


async def _via_mymemory(text: str, source: str, target: str, timeout: float) -> dict[str, Any]:
    """调用 MyMemory。

    MyMemory 不接受 "auto"（会返回 403 "'AUTO' IS AN INVALID SOURCE LANGUAGE"），
    但接受魔法值 "Autodetect"，并且会在响应里回传 detectedLanguage —— 这比我们
    自己按字符集猜语言准得多。
    """
    src = "Autodetect" if source in ("", "auto") else to_iso1(source)
    data = await get_json(
        MYMEMORY_URL,
        params={"q": text, "langpair": f"{src}|{to_iso1(target)}"},
        timeout=timeout,
    )
    status = data.get("responseStatus")
    if status not in (200, "200"):
        details = str(data.get("responseDetails") or "")
        if "INVALID SOURCE LANGUAGE" in details.upper():
            raise HttpError(f"MyMemory 不接受源语言 {src!r}：{details}")
        raise HttpError(f"MyMemory 返回状态 {status}: {details}")

    payload = data.get("responseData") or {}
    translated = html.unescape(str(payload.get("translatedText") or "")).strip()
    if not translated:
        raise HttpError("MyMemory 返回空结果")
    if looks_untranslated(text, translated):
        raise HttpError("MyMemory 原样返回了输入，视为未翻译")

    return {
        "text": translated,
        "provider": "mymemory",
        "detected": str(payload.get("detectedLanguage") or ""),
    }


async def _via_lingva(
    text: str, source: str, target: str, timeout: float, base: str
) -> dict[str, Any]:
    base = (base or LINGVA_DEFAULT).rstrip("/")
    src = "auto" if (not source or source == "auto") else source.split("-")[0]
    tgt = to_iso1(target).split("-")[0]
    url = f"{base}/api/v1/{src}/{tgt}/{_quote_path(text)}"
    data = await get_json(url, timeout=timeout)
    translated = (data.get("translation") or "").strip()
    if not translated:
        raise HttpError("Lingva 返回空结果")
    if looks_untranslated(text, translated):
        raise HttpError("Lingva 原样返回了输入，视为未翻译")
    return {"text": translated, "provider": "lingva"}


async def _via_gtrans(text: str, source: str, target: str, timeout: float) -> dict[str, Any]:
    explicit = bool(source) and source != "auto"
    body = await get_text(
        GTRANS_URL,
        params={
            "client": "gtx",
            "sl": to_iso1(source) if explicit else "auto",
            "tl": to_iso1(target),
            "dt": "t",
            "q": text,
        },
        timeout=timeout,
        retries=1,
    )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HttpError("Google 端点返回非 JSON") from exc

    segments = payload[0] if payload and isinstance(payload[0], list) else []
    translated = "".join(seg[0] for seg in segments if seg and isinstance(seg[0], str)).strip()
    if not translated:
        raise HttpError("Google 端点返回空结果")
    if looks_untranslated(text, translated):
        raise HttpError("Google 端点原样返回了输入，视为未翻译")
    detected = payload[2] if len(payload) > 2 and isinstance(payload[2], str) else ""
    return {"text": translated, "provider": "gtrans", "detected": detected}


def _quote_path(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


PROVIDERS = {
    "mymemory": "MyMemory（免密钥，默认首选）",
    "gtrans": "Google 公开端点（免密钥，兜底，国内常不可达）",
    "lingva": "Lingva（需自建实例，公共实例多被拦截）",
}

# 默认降级顺序：实测可用的排前面
DEFAULT_ORDER: tuple[str, ...] = ("mymemory", "gtrans")


async def translate(
    text: str,
    target: str,
    *,
    source: str = "auto",
    providers: Optional[list[str]] = None,
    lingva_base: str = LINGVA_DEFAULT,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """把 text 翻译成 target，返回 {text, provider, source, target, truncated}。"""
    text = (text or "").strip()
    if not text:
        raise TranslationFailed("要翻译的内容是空的")
    if not target or target == "auto":
        raise TranslationFailed("必须指定目标语言")

    order = [p for p in (providers or DEFAULT_ORDER) if p in PROVIDERS] or list(DEFAULT_ORDER)

    chunks = split_chunks(text)
    # 避免一次调用把大量文本打到公共接口上
    truncated = False
    if len(chunks) > 12:
        chunks = chunks[:12]
        truncated = True

    out_parts: list[str] = []
    used: set[str] = set()
    detected: str = ""
    for chunk in chunks:
        part, provider, chunk_detected = await _translate_chunk(
            chunk,
            source=source,
            target=target,
            order=order,
            lingva_base=lingva_base,
            timeout=timeout,
        )
        out_parts.append(part)
        used.add(provider)
        # 第一块定下来的语言沿用到后续块，避免逐块重复检测、也避免再次发出 auto
        if chunk_detected:
            detected = detected or chunk_detected
            if source in ("", "auto"):
                source = chunk_detected

    return {
        "text": "".join(out_parts).strip(),
        "provider": ",".join(sorted(used)),
        "source": source,
        "detected": detected,
        "target": target,
        "truncated": truncated,
    }


async def _translate_chunk(
    chunk: str, *, source: str, target: str, order: list[str], lingva_base: str, timeout: float
) -> tuple[str, str, str]:
    """返回 (译文, 提供方名, 检测到的源语言)。"""
    errors: list[str] = []
    for name in order:
        try:
            if name == "mymemory":
                result = await _via_mymemory(chunk, source, target, timeout)
            elif name == "lingva":
                result = await _via_lingva(chunk, source, target, timeout, lingva_base)
            else:
                result = await _via_gtrans(chunk, source, target, timeout)
            return (
                str(result["text"]),
                str(result["provider"]),
                str(result.get("detected") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - 逐个降级
            errors.append(f"{name}: {exc}")
            # 显式给了源语言但被判定"原样返回"，多半是源语言猜错了：
            # 再给同一个提供方一次自动检测的机会，这是实测最有效的兜底。
            if name == "mymemory" and source not in ("", "auto") and "原样返回" in str(exc):
                try:
                    retry = await _via_mymemory(chunk, "auto", target, timeout)
                    return (
                        str(retry["text"]),
                        f"{retry['provider']}(自动检测)",
                        str(retry.get("detected") or ""),
                    )
                except Exception as retry_exc:  # noqa: BLE001
                    errors.append(f"mymemory(自动检测): {retry_exc}")
            continue
    raise TranslationFailed("；".join(errors) or "所有翻译提供方都失败了")
