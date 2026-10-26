"""读页面源码的公共小工具（给几条"源码级看门狗"共用）。

**为什么单独放一个文件**：这些测试都要"先剥掉注释、再查代码里有没有某个调用"，
因为注释里提到函数名不该算数。但 HTML 里存在 `accept="image/*"` 这种
**看起来像注释开头**的属性值 —— naive 的 `re.sub(r"/\\*.*?\\*/", "", text)` 会被它骗到：
把那个 `/*` 和后面很远的某个 `*/` 配成一对，**静默吞掉中间几 KB**。

实测 `status.html` 被吞掉 5.2 KB，其中正好包括 `id="sticker-list"` 那个标签 ——
于是测试报"没有这个元素"，而文件里明明有；换个断言方向就会变成"看着通过、
其实那一整段根本没被检查"。所以这里把 `image/*` 先挡掉再剥注释，并留一条自检用例。
"""

from __future__ import annotations

import pathlib
import re

STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"

#: 看起来像注释开头、其实不是的片段（属性值里的通配 MIME）
_NOT_A_COMMENT = ("image/*", "image/jpeg,image/*")


def read(name: str) -> str:
    """原样读页面源码（不剥注释）。"""
    return (STATIC / name).read_text(encoding="utf-8")


def code_of(name: str) -> str:
    """剥掉块注释后的源码 —— 检查"有没有调用某个函数"时不能连注释一起查。"""
    text = read(name)
    guard = {}
    for i, needle in enumerate(_NOT_A_COMMENT):
        if needle in text:
            token = f"\x00notacomment{i}\x00"
            guard[token] = needle
            text = text.replace(needle, token)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for token, needle in guard.items():
        text = text.replace(token, needle)
    return text


def fn_body(text: str, name: str) -> str | None:
    """取 `function name(...){ ... }` 的函数体，**取最后一个定义**。

    JS 里同名函数声明重复时后声明的覆盖先声明的（`napcat.html` 真的有两份
    `skFilePicked`），取第一份会得出错误结论。花括号配平，不依赖缩进。
    """
    bodies: list[str] = []
    for m in re.finditer(rf"function\s+{name}\s*\([^)]*\)\s*\{{", text):
        depth = 0
        for i in range(m.end() - 1, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(text[m.end():i])
                    break
    return bodies[-1] if bodies else None
