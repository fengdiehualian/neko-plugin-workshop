"""纯 Python 的 JSX 感知结构扫描器（给 hosted TSX 面板做「语法体检」）。

为什么需要它
------------
插件 CI 是纯 Python 的，**从来不解析** ``ui/*.tsx``，所以面板里的**语法**错误
可以一路绿灯发出去，直到真机上宿主用 sucrase 编译失败才炸成白屏。

0.10.2 / 0.10.3 / 0.10.4 全都栽在同一处：持仓区块把**两个并列表达式**塞进了
同一个 ``( ... )`` 括号组里（``<Columns>…</Columns>`` 后面又跟了一个
``{hiddenPositionCount > 0 ? … : null}``）。TypeScript 会报
``TS1005: ')' expected``，但插件 CI 看不见 ⇒ 白屏。

这个模块**不是** TypeScript 解析器，它是针对性很强的扫描器，只做三件事：

1. **分隔符配平**：``{}`` / ``()`` / ``[]`` 在 JSX 语境下也要配平；
2. **漏运算符检测**：一个 ``( ... )`` 组里不允许出现两个**并列**表达式
   （也就是上面那种形状）；
3. **JSX 标签配对**：开标签和闭标签必须对得上。

它刻意不追求「完整解析」，只保证「宁可漏报、不可误报」：任何拿不准的构造
（正则、模板串、类型标注、泛型）都按「宁可放过」处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["TsxIssue", "scan_tsx", "scan_tsx_source"]


_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUM_RE = re.compile(r"(?:0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?)n?")
_JSX_NAME_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_ATTR_NAME_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$\-]*")

# 关键字分三类：
#   *_OTHER  —— 「运算符类」：自己不是表达式，但后面可以跟表达式
#   *_END    —— 只能结束一个表达式（this / true / null …）
#   其它      —— 普通标识符：既能开启也能结束一个表达式
_OTHER_KEYWORDS = frozenset(
    """
    as async await break case catch class const continue debugger default delete do else enum export
    extends finally for function from get if implements import in instanceof interface let new of
    package private protected public return set static super switch throw try type typeof var void
    while with yield satisfies keyof readonly
    """.split()
)
_END_KEYWORDS = frozenset({"true", "false", "null", "this", "undefined", "arguments", "NaN", "Infinity"})

# 出现这些 token 之后，``{`` 是「语句块」而不是「对象字面量」。
_BLOCK_PREV_TOKENS = frozenset({"=>", ")", "else", "do", "try", "finally"})

# 出现这些 token 之后，``{`` 是「对象字面量 / 类型字面量」（后面必须跟表达式）。
_EXPR_EXPECT_PREV = frozenset(
    {
        "=", "(", ",", "[", ":", "return", "?", "&&", "||", "??", "+", "-", "*", "/", "%",
        "==", "===", "!=", "!==", "<", ">", "<=", ">=", "&", "|", "^", "in", "of", "yield", "await",
    }
)


@dataclass(frozen=True)
class TsxIssue:
    """一处结构问题。"""

    line: int
    column: int
    message: str
    excerpt: str = ""

    def __str__(self) -> str:  # pragma: no cover - 仅用于报错展示
        return f"L{self.line}:{self.column} {self.message}"


@dataclass
class _Frame:
    """扫描栈的一层。"""

    kind: str  # "code" | "jsx_children" | "jsx_tag"
    stmt: bool = True  # 语句语境（True）还是表达式语境（False）
    last: str | None = None  # 上一枚有效 token：None / "expr" / "other"
    last_raw: str = ""  # 上一枚有效 token 的原文
    last_pos: int = 0
    expect: str = ""  # 本层应当遇到的闭合符
    after: str = "other"  # 本层闭合后，父层的 last 变成什么
    asi: bool = True  # 本层是否允许「换行即语句分隔」（圆括号里不允许）
    element: str = ""  # jsx_children 层：对应的开标签名
    tag_name: str = ""  # jsx_tag 层：正在解析的标签名
    tag_start: int = 0  # jsx_tag 层：``<`` 的位置
    pending_condition: bool = False  # 刚读到 if/while/for…，下一个 ``(`` 是条件括号


class _Scanner:
    def __init__(self, source: str) -> None:
        self.src = source
        self.n = len(source)
        self.i = 0
        self.issues: list[TsxIssue] = []
        self.stack: list[_Frame] = [_Frame(kind="code", stmt=True)]

    # ------------------------------------------------------------------ 工具
    def _line_col(self, pos: int) -> tuple[int, int]:
        line = self.src.count("\n", 0, pos) + 1
        line_start = self.src.rfind("\n", 0, pos) + 1
        return line, pos - line_start + 1

    def _report(self, pos: int, message: str) -> None:
        line, column = self._line_col(pos)
        start = self.src.rfind("\n", 0, pos) + 1
        end = self.src.find("\n", pos)
        if end < 0:
            end = self.n
        excerpt = self.src[start:end].strip()[:120]
        self.issues.append(TsxIssue(line=line, column=column, message=message, excerpt=excerpt))

    @property
    def frame(self) -> _Frame:
        return self.stack[-1]

    def _set_last(self, kind: str, raw: str, pos: int) -> None:
        frame = self.frame
        frame.last = kind
        frame.last_raw = raw
        frame.last_pos = pos

    # -------------------------------------------------------------- 主循环
    def run(self) -> list[TsxIssue]:
        while self.i < self.n:
            kind = self.frame.kind
            if kind == "code":
                self._scan_code()
            elif kind == "jsx_children":
                self._scan_jsx_children()
            else:
                self._scan_jsx_tag()
        while len(self.stack) > 1:
            frame = self.stack.pop()
            if frame.kind == "jsx_children":
                self._report(frame.last_pos, f"JSX 元素 <{frame.element}> 没有闭合标签")
            else:
                self._report(frame.last_pos, "括号 / 花括号没有配平（文件结束时仍有未闭合的组）")
        return self.issues

    # ------------------------------------------------------------ 代码语境
    def _scan_code(self) -> None:
        src = self.src
        ch = src[self.i]
        start = self.i

        if ch in " \t\r\n":
            if ch == "\n" and self.frame.asi:
                # ASI：语句之间可以只靠换行分隔（圆括号里没有 ASI）
                self.frame.last = "other"
            self.i += 1
            return

        if ch == "/" and self._peek(1) == "/":
            end = src.find("\n", self.i)
            self.i = self.n if end < 0 else end
            if self.frame.asi:
                self.frame.last = "other"
            return

        if ch == "/" and self._peek(1) == "*":
            end = src.find("*/", self.i + 2)
            if end < 0:
                self._report(start, "块注释没有闭合")
                self.i = self.n
                return
            self.i = end + 2
            return

        if ch in "\"'":
            self._read_string(ch)
            self._expr_token('"' if ch == '"' else "'", start)
            return

        if ch == "`":
            self._read_template()
            self._expr_token("`", start, allow_after_expr=True)
            return

        if ch == "(":
            self._open_group("(", start)
            return

        if ch == "[":
            self._open_group("[", start)
            return

        if ch == "{":
            self._open_brace(start)
            return

        if ch in ")]}":
            self._close_group(ch, start)
            return

        if ch == "<":
            self._handle_less_than(start)
            return

        if ch == "/":
            if self.frame.last != "expr":
                end = self._read_regex()
                if end is not None:
                    self._expr_token("/regex/", start)
                    return
            self._other_token("/", start)
            self.i += 1
            return

        if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
            match = _NUM_RE.match(src, self.i)
            if match:
                self.i = match.end()
                self._expr_token(match.group(0), start)
                return

        if ch.isalpha() or ch in "_$":
            match = _IDENT_RE.match(src, self.i)
            assert match is not None
            word = match.group(0)
            self.i = match.end()
            if word in _END_KEYWORDS:
                self._set_last("expr", word, start)
            elif word in _OTHER_KEYWORDS:
                if word in {"if", "while", "for", "switch", "catch", "with"}:
                    self.frame.pending_condition = True
                self._other_token(word, start)
            else:
                self._expr_token(word, start)
            return

        self._other_token(ch, start)
        self.i += 1

    # --------------------------------------------------------------- 分组
    def _open_group(self, closer: str, start: int) -> None:
        """``(`` / ``[``：开一个新的代码组。

        函数调用 / 下标访问（前面是能结束表达式的 token）不算「漏运算符」，
        所以这里不做相邻表达式的判定。
        """
        is_condition = self.frame.pending_condition
        self.frame.pending_condition = False
        self.i += 1
        frame = _Frame(
            kind="code",
            stmt=False,
            expect={"(": ")", "[": "]"}[closer],
            after="other" if is_condition else "expr",
            asi=False,
        )
        self.stack.append(frame)

    def _open_brace(self, start: int) -> None:
        frame = self.frame
        prev_raw = frame.last_raw
        if prev_raw in _BLOCK_PREV_TOKENS:
            is_block = True
        elif not frame.stmt:
            # 表达式语境：``{`` 只能是对象字面量 / 类型字面量
            is_block = False
        elif prev_raw in _EXPR_EXPECT_PREV:
            is_block = False
        else:
            is_block = True
        if not is_block and frame.last == "expr":
            self._report(start, "两个表达式之间缺少运算符（漏了逗号 / 运算符？）")
        self.i += 1
        self.stack.append(
            _Frame(
                kind="code",
                stmt=is_block,
                expect="}",
                after="other" if is_block else "expr",
                asi=True,
            )
        )

    def _close_group(self, closer: str, start: int) -> None:
        if len(self.stack) <= 1:
            self._report(start, f"多余的 {closer}")
            self.i += 1
            return
        frame = self.stack.pop()
        if frame.expect and frame.expect != closer:
            self._report(start, f"括号不匹配：期望 {frame.expect}，实际 {closer}")
        if frame.kind == "jsx_children":
            # JSX 子节点层由闭标签负责弹出，不走这里
            self.stack.append(frame)
            self.i += 1
            return
        self._set_last(frame.after, closer, start)
        self.i += 1

    # ------------------------------------------------------- 表达式 / 运算符
    def _expr_token(self, raw: str, start: int, *, allow_after_expr: bool = False) -> None:
        if self.frame.last == "expr" and not allow_after_expr:
            self._report(start, "两个表达式之间缺少运算符（漏了逗号 / 运算符？）")
        self._set_last("expr", raw, start)

    def _other_token(self, raw: str, start: int) -> None:
        self._set_last("other", raw, start)

    # ------------------------------------------------------------ 字符串等
    def _read_string(self, quote: str) -> None:
        start = self.i
        self.i += 1
        while self.i < self.n:
            ch = self.src[self.i]
            if ch == "\\":
                self.i += 2
                continue
            self.i += 1
            if ch == quote:
                return
        self._report(start, "字符串没有闭合")

    def _read_template(self) -> None:
        """读模板串；``${}`` 内部按代码处理。"""
        start = self.i
        self.i += 1
        while self.i < self.n:
            ch = self.src[self.i]
            if ch == "\\":
                self.i += 2
                continue
            if ch == "`":
                self.i += 1
                return
            if ch == "$" and self._peek(1) == "{":
                self.i += 2
                depth = 1
                while self.i < self.n and depth:
                    inner = self.src[self.i]
                    if inner in "\"'":
                        self._read_string(inner)
                        continue
                    if inner == "`":
                        self._read_template()
                        continue
                    if inner == "{":
                        depth += 1
                    elif inner == "}":
                        depth -= 1
                        if depth == 0:
                            self.i += 1
                            break
                    self.i += 1
                continue
            self.i += 1
        self._report(start, "模板字符串没有闭合")

    def _read_regex(self) -> int | None:
        """尝试把 ``/`` 读成正则字面量；不是正则则返回 ``None``。"""
        start = self.i
        idx = self.i + 1
        in_class = False
        while idx < self.n:
            ch = self.src[idx]
            if ch == "\\":
                idx += 2
                continue
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                idx += 1
                while idx < self.n and self.src[idx].isalpha():
                    idx += 1
                self.i = idx
                return start
            elif ch == "\n":
                return None
            idx += 1
        return None

    # ------------------------------------------------------------- 小于号
    def _handle_less_than(self, start: int) -> None:
        """``<`` 到底是「小于 / 泛型」还是「JSX 元素」。"""
        if self.frame.last == "expr":
            self._other_token("<", start)
            self.i += 1
            return
        rest = self.src[self.i + 1 :]
        stripped = rest.lstrip()
        offset = 1 + (len(rest) - len(stripped))
        if stripped.startswith(">"):
            self.i += offset + 1
            self.stack.append(_Frame(kind="jsx_children", stmt=False, element="", expect="", after="expr"))
            self._set_last("expr", "<>", start)
            return
        if stripped.startswith("/"):
            self._report(start, "代码语境里出现了 JSX 闭标签 </…>")
            self._other_token("<", start)
            self.i += 1
            return
        match = _JSX_NAME_RE.match(stripped)
        if not match:
            self._other_token("<", start)
            self.i += 1
            return
        after = stripped[match.end() :].lstrip()
        if after.startswith(",") or after.startswith("extends"):
            # <T,> / <T extends …>：泛型箭头，不是 JSX
            self._other_token("<", start)
            self.i += 1
            return
        self._open_jsx_tag(start)

    # ------------------------------------------------------------- JSX 标签
    def _open_jsx_tag(self, start: int) -> None:
        self.i += 1  # 吃掉 '<'
        name = self._read_jsx_name()
        if self._peek() == "<":
            self._skip_angle()
        self.stack.append(_Frame(kind="jsx_tag", stmt=False, tag_name=name, tag_start=start))

    def _read_jsx_name(self) -> str:
        match = _JSX_NAME_RE.match(self.src, self.i)
        if not match:
            return ""
        self.i = match.end()
        name = match.group(0)
        while self._peek() in (".", ":") and _JSX_NAME_RE.match(self.src, self.i + 1):
            self.i += 1
            name += self.src[self.i]
            match = _JSX_NAME_RE.match(self.src, self.i)
            assert match is not None
            self.i = match.end()
            name += match.group(0)
        return name

    def _skip_angle(self) -> None:
        depth = 0
        while self.i < self.n:
            ch = self.src[self.i]
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
                self.i += 1
                if depth == 0:
                    return
                continue
            self.i += 1

    def _scan_jsx_tag(self) -> None:
        while True:
            while self.i < self.n and self.src[self.i] in " \t\r\n":
                self.i += 1
            if self.i >= self.n:
                self._report(self.i, "JSX 标签没有闭合")
                self.stack.pop()
                return
            ch = self.src[self.i]
            if ch == "/" and self._peek(1) == "/":
                # 标签里允许写行注释
                end = self.src.find("\n", self.i)
                self.i = self.n if end < 0 else end
                continue
            if ch == "/" and self._peek(1) == "*":
                end = self.src.find("*/", self.i + 2)
                if end < 0:
                    self._report(self.i, "块注释没有闭合")
                    self.i = self.n
                    return
                self.i = end + 2
                continue
            if ch == "/" and self._peek(1) == ">":
                start = self.frame.tag_start
                self.i += 2
                self.stack.pop()
                self._set_last("expr", "<jsx/>", start)
                return
            if ch == ">":
                start = self.frame.tag_start
                name = self.frame.tag_name
                self.i += 1
                self.stack.pop()
                self.stack.append(_Frame(kind="jsx_children", stmt=False, element=name))
                return
            if ch == "{":
                self.i += 1
                self.stack.append(_Frame(kind="code", stmt=False, expect="}", after="other"))
                return
            if ch == "<":
                self._open_jsx_tag(self.i)
                return
            if ch in "\"'":
                start = self.i
                self._read_string(ch)
                self._other_token('"', start)
                continue
            if ch == "=":
                self._other_token("=", self.i)
                self.i += 1
                continue
            match = _ATTR_NAME_RE.match(self.src[self.i :])
            if not match:
                self._report(self.i, f"JSX 标签里出现了意料之外的字符 {ch!r}")
                self.i += 1
                continue
            self.i += match.end()
            self._other_token(match.group(0), self.i)

    def _scan_jsx_children(self) -> None:
        while True:
            if self.i >= self.n:
                self._report(self.i, "JSX 子节点没有闭合")
                self.stack.pop()
                return
            ch = self.src[self.i]
            if ch == "<":
                if self._peek(1) == "/":
                    start = self.i
                    self.i += 2
                    name = self._read_jsx_name()
                    while self.i < self.n and self.src[self.i] in " \t\r\n":
                        self.i += 1
                    if self._peek() == ">":
                        self.i += 1
                    opened = self.frame.element
                    if opened and name and opened != name:
                        self._report(start, f"JSX 标签不匹配：<{opened}> 被 </{name}> 关闭")
                    self.stack.pop()
                    self._set_last("expr", "</jsx>", start)
                    return
                if self._peek(1) == ">":
                    self.i += 2
                    self.stack.append(_Frame(kind="jsx_children", stmt=False, element=""))
                    continue
                self._open_jsx_tag(self.i)
                return
            if ch == "{":
                self.i += 1
                self.stack.append(_Frame(kind="code", stmt=False, expect="}", after="expr"))
                return
            self.i += 1

    def _peek(self, offset: int = 0) -> str:
        idx = self.i + offset
        return self.src[idx] if idx < self.n else ""


def scan_tsx_source(source: str) -> list[TsxIssue]:
    """扫描一段 TSX 源码，返回结构问题列表（空列表 = 通过）。"""
    return _Scanner(source).run()


def scan_tsx(path: str | Path) -> list[TsxIssue]:
    """扫描一个 TSX 文件。"""
    return scan_tsx_source(Path(path).read_text(encoding="utf-8"))
