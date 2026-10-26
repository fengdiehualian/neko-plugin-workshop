"""输入归一化：剥离语气/礼貌前缀（把/请/帮我等）。"""

# 可叠加剥离的前缀（长词在前）："请帮我开灯" → "开灯"
_PREFIXES = ("请帮我", "麻烦帮我", "帮我", "麻烦", "请", "把")


def normalize_utterance(text: str) -> str:
    """剥离句首语气前缀，返回归一化后的指令。

    幂等：对已归一化的文本再次调用无副作用。
    """
    s = (text or "").strip()
    while s:
        stripped = False
        for pfx in _PREFIXES:
            if s.startswith(pfx) and len(s) > len(pfx):
                s = s[len(pfx):].lstrip()
                stripped = True
                break
        if not stripped:
            break
    return s
