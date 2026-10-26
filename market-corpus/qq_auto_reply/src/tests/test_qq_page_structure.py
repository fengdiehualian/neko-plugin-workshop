"""页面必须待在滚动容器里：每个 `.page` 都必须是 `#content` 的后代。

**为什么要有这条**：`napcat.html` 曾经在 `page-config-params` 之后多了一个
`</div>`，把 `#content`（唯一的滚动容器）提前关掉。于是它后面的 **9 个页面**
（config-keywords / replymode / accounts / groups、sticker、review×3、logs）
全变成了 `#main` 的子节点、跑到滚动容器**外面**：

* 这些页面**无法滚动**（滚动条属于 `#content`）；
* 内容也不再受 `#content` 的内边距/高度约束，看着就像"没置顶"。

这个缺陷在 v0.9.3（`HEAD`）里就在，靠肉眼和正则数 `<div>` 个数都查不出来 ——
正则在数数，而这个错误**数量是平衡的**，只是位置错了。所以这里用真正的
HTML 解析器走一遍标签栈。

顺带钉住"标签栈在文件结束时必须为空"：多一个 `</div>` 必然导致别处的配对错位。
"""

from __future__ import annotations

import pathlib
from html.parser import HTMLParser

BASE = pathlib.Path(__file__).resolve().parents[1]
STATIC = BASE / "static"

#: 自闭合（空）元素：不需要闭合标签。
_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})
#: 闭合标签可省略的元素：缺失不算错（HTML 规范允许）。
_OPTIONAL_END = frozenset({"p", "li", "td", "tr", "th", "tbody", "thead", "option", "dt", "dd"})

_PAGES = ("napcat.html", "open_platform.html", "status.html")


class _Structure(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.pages: list[tuple[str, int, tuple[str, ...]]] = []
        self.problems: list[str] = []

    def _ancestors(self) -> tuple[str, ...]:
        return tuple(label for label, _ in self.stack)

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        ident = dict(attrs).get("id") or ""
        label = f"{tag}#{ident}" if ident else tag
        if ident.startswith("page-"):
            self.pages.append((ident, self.getpos()[0], self._ancestors()))
        self.stack.append((label, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if not self.stack:
            self.problems.append(
                f"第 {self.getpos()[0]} 行: 多出一个 </{tag}>，没有对应的开标签"
            )
            return
        # 找最近的同名标签（同名比较用不带 id 的标签名）
        for idx in range(len(self.stack) - 1, -1, -1):
            name, line = self.stack[idx]
            if name == tag or name.startswith(tag + "#"):
                unclosed = self.stack[idx + 1:]
                if all(
                    n.split("#")[0] in _OPTIONAL_END or n.split("#")[0] == "script"
                    for n, _ in unclosed
                ):
                    # script 是被内联 JS 里的字符串干扰时才留着的，不算结构性错误
                    del self.stack[idx:]
                    return
                self.problems.append(
                    f"第 {self.getpos()[0]} 行的 </{tag}> 闭合的是第 {line} 行的 <{name}>，"
                    f"但它上面还有未闭合的: "
                    + ", ".join(f"<{n}>@{ln}" for n, ln in unclosed)
                )
                del self.stack[idx:]
                return
        self.problems.append(f"第 {self.getpos()[0]} 行: 找不到匹配的 <{tag}>")


def _parse(name: str) -> _Structure:
    parser = _Structure()
    parser.feed((STATIC / name).read_text(encoding="utf-8"))
    parser.close()
    return parser


def test_every_page_lives_inside_the_scroll_container():
    """每个 `.page` 都必须是 `#content` 的后代，否则它滚不动。"""
    offenders: list[str] = []
    for name in _PAGES:
        parser = _parse(name)
        for pid, line, ancestors in parser.pages:
            if "div#content" not in ancestors:
                offenders.append(
                    f"{name} 第 {line} 行 {pid}: 祖先链 = {' > '.join(ancestors)}"
                )
    assert not offenders, (
        "这些页面在 #content（滚动容器）**外面** —— 它们无法滚动、也不受容器约束。\n"
        "多半是某处多了一个 </div> 把 #content 提前关掉了：\n"
        + "\n".join(f"  ✗ {o}" for o in offenders)
    )


def test_tag_stack_is_balanced():
    """解析到文件末尾时标签栈必须为空（多一个 </div> 一定在这里露头）。"""
    problems: list[str] = []
    for name in _PAGES:
        parser = _parse(name)
        leftovers = [
            (n, ln) for n, ln in parser.stack if n.split("#")[0] not in _OPTIONAL_END
        ]
        for p in parser.problems:
            problems.append(f"{name}: {p}")
        if leftovers:
            problems.append(f"{name}: 文件结束时仍未闭合 {leftovers}")
    assert not problems, "\n".join(f"  ✗ {p}" for p in problems)


def test_the_parser_actually_sees_the_pages():
    """先证明解析器在干活 —— 解析失配会让上面两条空过。"""
    parser = _parse("napcat.html")
    assert len(parser.pages) >= 10, (
        f"只解析出 {len(parser.pages)} 个 .page，解析器失配了"
    )
    assert any(pid == "page-logs" for pid, _, _ in parser.pages)
