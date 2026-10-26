"""Message-chain model.

数据模型借用 KiraAI 的**嵌套消息链形状**（``Reply.chain`` / ``Forward.chains``），但
``.repr`` 的用途与上游不同：上游 ``repr`` 是**日志表示**（docstring 原文 "Returns a
string to display in logs"，且 ``Reply.repr`` 是不递归的 ``[Reply <id>]``），真正的
prompt 输入走另一条路径 ``MessageManager.message_format_to_text()``；本插件把这两件事
合并到了 ``repr`` 上（日志与 prompt 注入共用一份表示）。

一个 ``MessageChain`` 可以嵌套（``reply.chain``、``forward.chains``）。
"""

from __future__ import annotations

import json as _json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# ── base elements ──────────────────────────────────────────

class MessageElement(ABC):
    """Base class for a message element."""

    @property
    @abstractmethod
    def repr(self) -> str:
        """Text form used in the LLM prompt."""
        ...


class Text(MessageElement):
    def __init__(self, text: str) -> None:
        self.text: str = str(text)

    @property
    def repr(self) -> str:
        return self.text


class Image(MessageElement):
    def __init__(self, url: str = "", file: str = "") -> None:
        self.url: str = str(url or "")
        self.file: str = str(file or "")

    @property
    def repr(self) -> str:
        return "[图片]"


class At(MessageElement):
    def __init__(self, pid: str, nickname: str = "") -> None:
        self.pid: str = str(pid)
        self.nickname: str = str(nickname or "")

    @property
    def repr(self) -> str:
        if self.pid == "all":
            return "[@全体成员]"
        if self.nickname:
            return f"[@{self.nickname}]"
        return f"[@用户{self.pid}]"


class Reply(MessageElement):
    def __init__(self, message_id: str, chain: Optional[MessageChain] = None) -> None:
        self.message_id: str = str(message_id)
        self.chain: Optional[MessageChain] = chain  # content chain of the quoted message

    @property
    def repr(self) -> str:
        if self.chain:
            return self.chain.repr
        return f"[引用 {self.message_id}]"


class Forward(MessageElement):
    def __init__(self, chains: list[MessageChain]) -> None:
        self.chains: list[MessageChain] = chains

    @property
    def repr(self) -> str:
        return "[转发]"


class Emoji(MessageElement):
    def __init__(self, emoji_id: str) -> None:
        self.emoji_id: str = str(emoji_id)

    @property
    def repr(self) -> str:
        return f"[表情 {self.emoji_id}]"


class Sticker(MessageElement):
    def __init__(self, sticker_bs64: str = "", sticker_id: str = "") -> None:
        self.sticker_bs64: str = str(sticker_bs64 or "")
        self.sticker_id: str = str(sticker_id or "")

    @property
    def repr(self) -> str:
        return "[动画表情]"


class Record(MessageElement):
    def __init__(self, bs64: str = "", file_id: str = "") -> None:
        self.bs64: str = str(bs64 or "")
        self.file_id: str = str(file_id or "")

    @property
    def repr(self) -> str:
        return "[语音]"


class Notice(MessageElement):
    def __init__(self, text: str) -> None:
        self.text: str = str(text)

    @property
    def repr(self) -> str:
        return self.text


class Poke(MessageElement):
    def __init__(self, pid: str) -> None:
        self.pid: str = str(pid)

    @property
    def repr(self) -> str:
        return "[戳一戳]"


class File(MessageElement):
    def __init__(self, name: str = "", bs64: str = "", url: str = "") -> None:
        self.name: str = str(name or "")
        self.bs64: str = str(bs64 or "")
        # Optional direct link URL (from Lagrange-style backends via data.url, or
        # resolved by NapCat get_*_file_url); purely additive -- repr / bs64 unchanged.
        self.url: str = str(url or "")

    @property
    def repr(self) -> str:
        return f"[文件 {self.name}]" if self.name else "[文件]"


class JsonCard(MessageElement):
    """QQ JSON card message (mini-app / share card etc.)."""
    def __init__(self, raw_json: str) -> None:
        self.raw: str = str(raw_json or "")
        self.title: str = ""
        self.desc: str = ""
        self.app: str = ""
        self.nick: str = ""
        self.prompt: str = ""
        self._parse()

    def _parse(self) -> None:
        try:
            card = _json.loads(self.raw)
            detail = card.get("meta", {}).get("detail_1", {})
            self.title = str(detail.get("title") or "")
            self.desc = str(detail.get("desc") or "")
            self.app = str(card.get("app") or "")
            self.nick = str(detail.get("host", {}).get("nick") or "")
            self.prompt = str(card.get("prompt") or "")
        except Exception:
            pass

    @property
    def repr(self) -> str:
        parts: list[str] = ["[卡片"]
        if self.title:
            parts.append(f" {self.title}")
        if self.desc:
            parts.append(f": {self.desc}")
        if self.nick:
            parts.append(f" (来自{self.nick})")
        parts.append("]")
        return "".join(parts)


# ── message chain ─────────────────────────────────────────

@dataclass
class MessageChain:
    """Ordered list of message elements, nestable via reply/forward."""
    elements: list[MessageElement] = field(default_factory=list)
    sender_name: str = ""
    sender_id: str = ""
    timestamp: int = 0
    message_id: str = ""

    def add(self, element: MessageElement) -> "MessageChain":
        self.elements.append(element)
        return self

    @property
    def repr(self) -> str:
        """Concatenate every element's repr for LLM context injection."""
        return "".join(e.repr for e in self.elements)

    @property
    def plain_text(self) -> str:
        """Extract plain text (Text elements only)."""
        return "".join(e.text for e in self.elements if isinstance(e, Text))

    @staticmethod
    def empty() -> "MessageChain":
        return MessageChain()


# ── builders ───────────────────────────────────────────────
#
# 这里**曾经**有一个 ``chain_from_onebot_message()``。它是死代码（全库零调用），
# 而且是能力更弱的那个版本：reply 段只建 ``Reply(message_id=...)`` 而**不填充**
# ``Reply.chain``（于是 ``repr`` 退化成 ``[引用 <id>]``）、forward 段恒为
# ``Forward(chains=[])``、at 不解析昵称、file 不解真实 URL。
#
# 真正的解析入口是 ``QQMessageEnricher._build_message_chain``
# （enrichment.py）：它才做递归 ``get_msg``、深度上限 ``_MAX_REPLY_DEPTH``、
# ``seen`` 防环、以及上面这些缺失的解析。两个解析器并存的风险是有人误调弱的
# 那个，于是 prompt 里出现裸消息 ID —— 所以删掉，模块只保留数据模型。

