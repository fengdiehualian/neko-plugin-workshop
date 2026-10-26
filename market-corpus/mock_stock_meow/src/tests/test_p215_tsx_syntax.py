"""P21.5 守卫：面板 TSX 语法体检（真机白屏的根因防线）。

0.10.4 真机白屏的报错是 ``Unexpected token (4360:41)``，指向 ``ui/panel.tsx``。
但**根因不在那一行** —— 那是宿主 sucrase 的**误导性二次报错**。真正的病根在
4565 行：持仓区块把**两个并列表达式**塞进了同一个 ``( ... )`` 括号组。

    ) : (
      <Columns …>…</Columns>
      {hiddenPositionCount > 0 ? … : null}     ← 少了一层包裹
    )

TypeScript 会报 ``L4565:15 TS1005: ')' expected``；宿主 sucrase 同样会挂，
但它先按 JSX 解析（失败）、再回退按「泛型箭头函数」解析，于是最后只剩
``Unexpected token (4326:41)`` —— 指向完全无辜的 ``<Page …>`` 那一行。

而插件 CI 是纯 Python 的，**从来不解析 ``ui/*.tsx``** ⇒ 这个语法错误从
0.10.2 一路绿灯发到 0.10.4，最后在真机上变成白屏。

所以这一版钉死两件事：

1. 面板源码必须通过 JSX 感知的结构体检（``tests/tsx_lint.py``）；
2. 体检器本身必须**抓得住**当年那个 bug —— 用反例钉死，避免体检器写成摆设。
"""

from __future__ import annotations

from pathlib import Path

from tsx_lint import scan_tsx_source

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


# 0.10.2 / 0.10.3 / 0.10.4 三个版本里，坏掉的那一段**原样照抄**。
# 它是本守卫的「敌人样本」：形状 = 三元 else 分支的括号里塞了两个并列表达式。
_BROKEN_HOLDINGS_BLOCK = """
export default function Panel(props: any) {
  return (
    <Card title={"holdings"}>
      <Stack gap={12}>
        {positions.length === 0 ? (
          <Text>{"none"}</Text>
        ) : (
          <Columns fluid minWidth={196} gap={16}>
            {shownPositions.map((position) =>
              h(PositionTile, { key: position.stock_id, position, t })
            )}
          </Columns>
          {hiddenPositionCount > 0 ? (
            <Text>{"more"}</Text>
          ) : null}
        )}
      </Stack>
    </Card>
  )
}
"""

# 修好之后的形状：else 分支里只有一个表达式（外面再包一层 <Stack>）。
_FIXED_HOLDINGS_BLOCK = """
export default function Panel(props: any) {
  return (
    <Card title={"holdings"}>
      <Stack gap={12}>
        {positions.length === 0 ? (
          <Text>{"none"}</Text>
        ) : (
          <Stack gap={12}>
            <Columns fluid minWidth={196} gap={16}>
              {shownPositions.map((position) =>
                h(PositionTile, { key: position.stock_id, position, t })
              )}
            </Columns>
            {hiddenPositionCount > 0 ? (
              <Text>{"more"}</Text>
            ) : null}
          </Stack>
        )}
      </Stack>
    </Card>
  )
}
"""


def test_the_panel_passes_the_tsx_structure_lint() -> None:
    """面板源码必须零结构问题。

    这一条就是 0.10.5 的核心防线：**TSX 语法错误不许再溜进发布包**。
    """
    issues = scan_tsx_source(_panel_source())
    assert not issues, "面板存在 TSX 结构问题：\n" + "\n".join(
        f"  L{item.line}:{item.column} {item.message}  →  {item.excerpt}" for item in issues
    )


def test_the_tsx_lint_catches_two_expressions_in_one_paren_group() -> None:
    """体检器必须抓得住 0.10.2 ~ 0.10.4 那个 bug（否则它就是个摆设）。"""
    issues = scan_tsx_source(_BROKEN_HOLDINGS_BLOCK)
    assert issues, "体检器没抓到「一个括号组里两个并列表达式」—— 守卫失效了"
    messages = [item.message for item in issues]
    assert any("缺少运算符" in message for message in messages), messages
    # 报错位置必须落在第二段表达式那一行（敌人样本里 ``{hiddenPositionCount`` 那一行）
    bad_line = next(
        index for index, line in enumerate(_BROKEN_HOLDINGS_BLOCK.split("\n"), start=1)
        if "{hiddenPositionCount" in line
    )
    assert issues[0].line == bad_line, issues[0]
    assert "hiddenPositionCount" in issues[0].excerpt, issues[0]


def test_the_tsx_lint_accepts_the_fixed_shape() -> None:
    """同样的结构，包一层之后必须干净（避免体检器只会「一律报错」）。"""
    assert not scan_tsx_source(_FIXED_HOLDINGS_BLOCK)


def test_the_tsx_lint_catches_unbalanced_delimiters() -> None:
    """括号没配平也要抓。"""
    issues = scan_tsx_source(
        "export default function Panel() {\n  return (\n    <Page>\n      <Text>{\"x\"}</Text>\n  )\n}\n"
    )
    assert issues, "体检器没抓到括号没配平"


def test_the_tsx_lint_catches_mismatched_jsx_tags() -> None:
    """开闭标签对不上也要抓。"""
    issues = scan_tsx_source(
        "export default function Panel() {\n  return (\n    <Page>\n      <Text>{\"x\"}</Stack>\n    </Page>\n  )\n}\n"
    )
    assert issues, "体检器没抓到 JSX 标签不匹配"
    assert any("标签不匹配" in item.message for item in issues), [item.message for item in issues]


def test_the_holdings_block_keeps_a_single_expression_in_the_ternary_branch() -> None:
    """持仓区块：三元 else 分支的括号里只能有**一个**表达式。

    这是 0.10.5 修的那一处，单独钉死，免得以后重构时又把它拆散。
    结构顺序必须是::

        ) : (
          <Stack …>            ← 包裹层（0.10.5 补回来的）
            <Columns …>…</Columns>
            {hiddenPositionCount > 0 ? … : null}
          </Stack>
        )}
    """
    source = _panel_source()
    anchor = "rrm-anchor-position-grid"
    assert anchor in source, "找不到持仓区块的锚点 class"
    snippet = source[source.index(anchor) : source.index(anchor) + 2000]

    def _pos(marker: str, start: int = 0) -> int:
        assert marker in snippet[start:], f"持仓区块里找不到 {marker!r}"
        return snippet.index(marker, start)

    else_branch = _pos(") : (")
    wrapper_open = _pos("<Stack", else_branch)
    columns_open = _pos("<Columns fluid minWidth={196}")
    columns_close = _pos("</Columns>")
    more_text = _pos("{hiddenPositionCount > 0 ?")
    wrapper_close = _pos("</Stack>")
    assert else_branch < wrapper_open < columns_open < columns_close < more_text < wrapper_close, (
        "持仓区块的 else 分支必须是一个 <Stack> 包裹层，里面才放 Columns + 提示文案"
    )
