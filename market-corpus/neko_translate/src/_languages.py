"""语言代码归一化：把 LLM / 用户给出的各种写法折成 BCP-47 短码。

纯函数模块，不依赖 SDK、不依赖第三方库，方便单测。
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# 规范码 -> (中文名, 英文名)
LANGUAGES: dict[str, Tuple[str, str]] = {
    "zh-CN": ("简体中文", "Simplified Chinese"),
    "zh-TW": ("繁体中文", "Traditional Chinese"),
    "en": ("英语", "English"),
    "ja": ("日语", "Japanese"),
    "ko": ("韩语", "Korean"),
    "fr": ("法语", "French"),
    "de": ("德语", "German"),
    "es": ("西班牙语", "Spanish"),
    "pt": ("葡萄牙语", "Portuguese"),
    "ru": ("俄语", "Russian"),
    "it": ("意大利语", "Italian"),
    "ar": ("阿拉伯语", "Arabic"),
    "th": ("泰语", "Thai"),
    "vi": ("越南语", "Vietnamese"),
    "id": ("印尼语", "Indonesian"),
    "ms": ("马来语", "Malay"),
    "hi": ("印地语", "Hindi"),
    "tr": ("土耳其语", "Turkish"),
    "pl": ("波兰语", "Polish"),
    "nl": ("荷兰语", "Dutch"),
    "uk": ("乌克兰语", "Ukrainian"),
    "sv": ("瑞典语", "Swedish"),
    "cs": ("捷克语", "Czech"),
    "el": ("希腊语", "Greek"),
    "he": ("希伯来语", "Hebrew"),
    "fa": ("波斯语", "Persian"),
    "ro": ("罗马尼亚语", "Romanian"),
    "hu": ("匈牙利语", "Hungarian"),
    "da": ("丹麦语", "Danish"),
    "fi": ("芬兰语", "Finnish"),
    "no": ("挪威语", "Norwegian"),
}

# 别名 -> 规范码。全部小写做键。
_ALIASES: dict[str, str] = {}


def _register(code: str) -> None:
    zh, en = LANGUAGES[code]
    keys = {code.lower(), code.replace("-", "_").lower(), en.lower(), zh.lower(), zh}
    # 语言主体码（en-US -> en）也登记
    base = code.split("-")[0].lower()
    keys.add(base)
    for k in keys:
        if k and k not in _ALIASES:
            _ALIASES[k] = code


for _code in LANGUAGES:
    _register(_code)

# 常见中文俗称 / 缩写补充
_EXTRA = {
    "中文": "zh-CN",
    "汉语": "zh-CN",
    "普通话": "zh-CN",
    "简体": "zh-CN",
    "简中": "zh-CN",
    "繁体": "zh-TW",
    "繁中": "zh-TW",
    "日文": "ja",
    "日语": "ja",
    "日語": "ja",
    "jp": "ja",
    "jap": "ja",
    "韩文": "ko",
    "韩语": "ko",
    "韓語": "ko",
    "kr": "ko",
    "kor": "ko",
    "英文": "en",
    "英语": "en",
    "英語": "en",
    "eng": "en",
    "法文": "fr",
    "fra": "fr",
    "德文": "de",
    "deu": "de",
    "ger": "de",
    "俄文": "ru",
    "俄语": "ru",
    "西语": "es",
    "西班牙文": "es",
    "spa": "es",
    "葡语": "pt",
    "葡萄牙文": "pt",
    "por": "pt",
    "意大利文": "it",
    "ita": "it",
    "阿拉伯文": "ar",
    "ara": "ar",
    "泰文": "th",
    "tha": "th",
    "越南文": "vi",
    "vie": "vi",
    "印尼文": "id",
    "ind": "id",
    "印地文": "hi",
    "hin": "hi",
    "土耳其文": "tr",
    "tur": "tr",
    "波兰文": "pl",
    "pol": "pl",
    "荷兰文": "nl",
    "nld": "nl",
    "dut": "nl",
    "乌克兰文": "uk",
    "ukr": "uk",
    "瑞典文": "sv",
    "swe": "sv",
    "捷克文": "cs",
    "ces": "cs",
    "cze": "cs",
    "希腊文": "el",
    "ell": "el",
    "gre": "el",
    "希伯来文": "he",
    "heb": "he",
    "波斯文": "fa",
    "fas": "fa",
    "per": "fa",
    "罗马尼亚文": "ro",
    "ron": "ro",
    "rum": "ro",
    "匈牙利文": "hu",
    "hun": "hu",
    "丹麦文": "da",
    "dan": "da",
    "芬兰文": "fi",
    "fin": "fi",
    "挪威文": "no",
    "nor": "no",
}
_ALIASES.update({k.lower(): v for k, v in _EXTRA.items()})

# CJK / 假名 / 谚文粗判，用于 auto 源语言
_RE_HAN = re.compile(r"[\u4e00-\u9fff]")
_RE_KANA = re.compile(r"[\u3040-\u30ff]")
_RE_HANGUL = re.compile(r"[\uac00-\ud7af]")
_RE_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_RE_ARABIC = re.compile(r"[\u0600-\u06ff]")


def normalize_language(value: Optional[str], default: Optional[str] = None) -> Optional[str]:
    """把任意语言写法折成规范码；无法识别时返回 default。"""
    if not value:
        return default
    raw = str(value).strip()
    if not raw:
        return default
    if raw.lower() in {"auto", "自动", "自动检测", "detect", "未知", "unknown"}:
        return "auto"
    key = raw.lower()
    if key in _ALIASES:
        return _ALIASES[key]
    # zh-cn / zh_CN / zhCN 这类变体
    squashed = key.replace("_", "-")
    if squashed in _ALIASES:
        return _ALIASES[squashed]
    parts = squashed.split("-")
    if len(parts) == 2 and all(parts):
        merged = f"{parts[0]}-{parts[1].upper()}"
        if merged in LANGUAGES:
            return merged
    return default


def guess_language(text: str) -> str:
    """非常轻量的源语言猜测：只区分到能提高翻译质量的程度。"""
    if not text:
        return "en"
    if _RE_KANA.search(text):
        return "ja"
    if _RE_HANGUL.search(text):
        return "ko"
    if _RE_HAN.search(text):
        return "zh-CN" if not _RE_KANA.search(text) else "ja"
    if _RE_CYRILLIC.search(text):
        return "ru"
    if _RE_ARABIC.search(text):
        return "ar"
    return "en"


def language_label(code: str) -> str:
    """给人类/给 LLM 看的语言名。"""
    if code == "auto":
        return "自动检测"
    item = LANGUAGES.get(code)
    if not item:
        return code
    return item[0]


def supported_languages_text() -> str:
    return "、".join(f"{code}({zh})" for code, (zh, _en) in LANGUAGES.items())
