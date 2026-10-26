# -*- coding: utf-8 -*-
"""推送可见面契约门（1.3.2 追加轮）——给她的内部提示一律不得上屏。

bug 的形状（实机 2026-09-21 20:22:07/08，日志 `N.E.K.O_Plugin_forever_companion_*.log`
`phase opener sent: follicular for YUI` + 主日志 `release batch keys=['forever_companion.phase_opener']`）：
阶段开场白用 `push_message(visibility=["chat"], ai_behavior="respond")` 递那句
"你刚意识到自己进入了新的身体阶段…请以你自己的口吻…"，于是宿主把**整段提示词原样画进
聊天框**（署名 forever_companion），她随后又说一遍——同一段话出现两次，第二遍还带抄错的字符。

根因是把两件事当成一件：宿主侧 `visibility` 管"给用户看什么"（含 "chat" → 发 chat_blocks
帧渲染 parts），`ai_behavior` 管"要不要起一轮"，两条互不参考（`plugin/server/messaging/
proactive_bridge.py` 明写 "visibility is NOT consulted here"）。本插件的推送**全部**是给她
看的内部提示（身体轻语/开场白/日记邀请/纪念日/生日/恢复台词/碎片轻语），所以契约是：

**任何推送出口的 visibility 只能是空列表字面量**（出口 = 直调 `push_message` 与主类注入的转发口
`self._push(...)`；动态值、`["chat"]`、`["hud"]` 一律红；完全省略 visibility 放行——宿主默认即
`[]`，见 `plugin/sdk/shared/core/push_message_schema.py`）。

门分三层：①②行为门（开场白起轮，到期仅 read，两者都不上屏）③AST 静态门（扫全插件源码，
钉死"不许再长出第三处"）④反向对照（把三种坏形态喂给静态门，必须逐个报红——只会变绿的门比没有更危险）。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 扫描面：插件自身全部 python 源码（排除 tests/ 与缓存）
_SOURCES = sorted(p for p in ROOT.rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts)


def _is_empty_list(node: ast.AST | None) -> bool:
    return isinstance(node, ast.List) and not node.elts


def _dict_visibility(node: ast.Dict) -> ast.AST | None:
    """取 dict 字面量里 "visibility" 键的值节点；没这个键返回 None（= 走宿主默认 []）。"""
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == "visibility":
            return value
    return None


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return getattr(func, "id", "")


# 推送出口：直调 SDK 的 push_message，以及经主类注入的转发口 self._push(...)
# （__init__.py 把 push=lambda **kw: self.push_message(**kw) 交给 services/，
# 真正的 visibility 决定在 services 那侧——只盯 push_message 会漏掉整条语气链路）
_OUTLETS = {"push_message", "_push"}


def _passthrough_params(tree: ast.Module) -> set[str]:
    """收集"纯转发"形参名：形如 lambda **kw: <出口>(**kw) ——它自己不决定可见面，放行。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        kwparam = getattr(getattr(node, "args", None), "kwarg", None)  # **kw 落 args.kwarg（不是 vararg）
        body = node.body
        first = body[0] if isinstance(body, list) and body else body
        if not kwparam or not isinstance(first, ast.Call) or _call_name(first.func) not in _OUTLETS:
            continue
        if any(kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id == kwparam.arg
               for kw in first.keywords):
            names.add(kwparam.arg)
    return names


def _scan_scope(nodes: list[ast.AST], passthrough: set[str]) -> list[tuple[int, str]]:
    """扫一段作用域内的推送出口调用点，返回 [(行号, 问题描述)]。"""
    problems: list[tuple[int, str]] = []
    # 本作用域里 name -> 所有被赋过的 dict 字面量，供 **kwargs 解析
    dicts: dict[str, list[ast.Dict]] = {}
    for holder in nodes:
        for node in ast.walk(holder):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        dicts.setdefault(target.id, []).append(node.value)

    for holder in nodes:
        for node in ast.walk(holder):
            if not isinstance(node, ast.Call) or _call_name(node.func) not in _OUTLETS:
                continue
            vis_kw = None
            stars: list[ast.AST] = []
            for kw in node.keywords:
                if kw.arg == "visibility":
                    vis_kw = kw.value
                elif kw.arg is None:
                    stars.append(kw.value)
            if vis_kw is not None:
                if not _is_empty_list(vis_kw):
                    problems.append((node.lineno, f"visibility 直传非空字面量: {ast.unparse(vis_kw)}"))
                continue
            if not stars:
                continue  # 完全省略 visibility：宿主默认 []，放行
            for expr in stars:
                if isinstance(expr, ast.Dict):
                    candidates = [expr]
                elif isinstance(expr, ast.Name):
                    if expr.id in passthrough:
                        continue  # 纯转发口，可见面由调用方决定（调用方另受本门约束）
                    candidates = dicts.get(expr.id, [])
                    if not candidates:
                        problems.append((node.lineno, f"**{expr.id} 解析不到 dict 字面量（动态可见面）"))
                        continue
                else:
                    problems.append((node.lineno, f"**{ast.unparse(expr)} 形态无法静态判定"))
                    continue
                for dct in candidates:
                    got = _dict_visibility(dct)
                    if got is not None and not _is_empty_list(got):
                        problems.append((node.lineno, f"**dict 里 visibility 非空: {ast.unparse(got)}"))
    return problems


def scan_source(source: str) -> tuple[int, list[tuple[int, str]]]:
    """返回 (该源码里的推送出口调用点数, 问题列表)。"""
    tree = ast.parse(source)
    passthrough = _passthrough_params(tree)
    calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) in _OUTLETS:
            calls += 1
    funnels = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    rest = [n for n in tree.body if n not in funnels]
    problems = _scan_scope(funnels + rest, passthrough)
    return calls, sorted(set(problems))


# ---------- ①② 行为门：真跑这两条链路 ----------


def test_phase_opener_push_is_hidden_yet_takes_turn(plugin_factory_full):
    def run(coro):
        import asyncio

        return asyncio.run(coro)

    p = plugin_factory_full()
    run(p.startup())
    lanlan = run(p._resolve_current_lanlan())
    shard = run(p._ensure_shard(lanlan))
    state = p._current_phase_state()
    shard.cycle["phase_seen"] = ""  # 造"刚进入新阶段"
    assert run(p._maybe_phase_opener(state, lanlan, shard)) is True
    pushes = [m for m in p._pushed if m.get("metadata", {}).get("message_type") == "forever_companion.phase_opener"]
    assert len(pushes) == 1, f"应恰递一条开场白，实得 {len(pushes)}"
    # 上屏面必须为空，而起轮照旧（respond）
    assert pushes[0]["visibility"] == [], f"开场白 visibility 泄漏: {pushes[0]['visibility']}"
    assert pushes[0]["ai_behavior"] == "respond"
    assert "不要复述本句" in pushes[0]["parts"][0]["text"]


def test_expiry_notice_is_hidden_without_starting_a_turn(plugin_factory_full):
    import asyncio

    def run(coro):
        return asyncio.run(coro)

    p = plugin_factory_full()
    run(p.startup())
    lanlan = run(p._resolve_current_lanlan())
    run(p._speak_recovery_line("ebb_tide", lanlan))
    pushes = [m for m in p._pushed if m.get("metadata", {}).get("message_type") == "forever_companion.mood_recovered"]
    assert len(pushes) == 1
    assert pushes[0]["visibility"] == [], f"恢复台词 visibility 泄漏: {pushes[0]['visibility']}"
    assert pushes[0]["ai_behavior"] == "read"
    assert "不代表你已经消气" in pushes[0]["parts"][0]["text"]


# ---------- ③ 静态门：全插件源码 ----------


def test_no_push_site_anywhere_asks_for_visible_chat() -> None:
    seen = 0
    problems: list[str] = []
    for path in _SOURCES:
        calls, found = scan_source(path.read_text(encoding="utf-8"))
        seen += calls
        for line, why in found:
            problems.append(f"{path.relative_to(ROOT)}:{line} {why}")
    # 门本身得看得见东西（0 调用点=扫描器坏了，比没有门更危险）
    assert seen >= 10, f"仅扫到 {seen} 个推送出口调用点，扫描面疑似失效"
    assert not problems, "内部提示被递到了用户可见面:\n" + "\n".join(problems)


# ---------- ④ 反向对照：坏形态必须报红 ----------

_BAD_SAMPLES = {
    "直传 [\"chat\"]": 'class P:\n    def f(self):\n        self.push_message(visibility=["chat"], ai_behavior="respond")\n',
    "直传 [\"hud\"]": 'class P:\n    def f(self):\n        self.push_message(visibility=["hud"])\n',
    "直传动态变量": 'class P:\n    def f(self, vis):\n        self.push_message(visibility=vis)\n',
    "**dict 字面量带 chat": 'class P:\n    def f(self):\n        self.push_message(**{"visibility": ["chat"]})\n',
    "**kwargs 赋成非空": (
        'class P:\n    def f(self):\n'
        '        kwargs = {"visibility": ["chat"], "ai_behavior": "read"}\n'
        "        self.push_message(**kwargs)\n"
    ),
    "**kwargs 赋成动态": (
        'class P:\n    def f(self, vis):\n'
        '        kwargs = {"visibility": vis}\n'
        "        self.push_message(**kwargs)\n"
    ),
    "**解析不到的名字": 'class P:\n    def f(self):\n        self.push_message(**self._kw())\n',
    "纯转发口的调用方泄漏": (
        'class S:\n    def __init__(self, push):\n        self._push = push\n'
        '    def go(self):\n        self._push(visibility=["chat"])\n'
    ),
}


def test_static_gate_rejects_every_bad_shape() -> None:
    for label, src in _BAD_SAMPLES.items():
        calls, problems = scan_source(src)
        assert calls >= 1, f"{label}: 调用点没数到（{calls}）"
        assert problems, f"反向对照失效——坏形态 {label} 没被门抓到"


def test_static_gate_passes_known_good_shapes() -> None:
    good = [
        'class P:\n    def f(self):\n        self.push_message(visibility=[], ai_behavior="respond")\n',
        # 省略 visibility = 宿主默认 []，放行
        'class P:\n    def f(self):\n        self.push_message(ai_behavior="read")\n',
        'class P:\n    def f(self):\n'
        '        kwargs = {"visibility": [], "ai_behavior": "read"}\n'
        "        self.push_message(**kwargs)\n",
        # 纯转发口（主类把 push_message 交给 services）本体不判红；
        # 它的调用方另受出口名 _push 约束（见反向对照样本）
        'class P:\n    def make(self):\n        return Svc(push=lambda **kw: self.push_message(**kw))\n',
    ]
    for src in good:
        calls, problems = scan_source(src)
        assert calls == 1 and not problems, f"误伤好形态: {problems}"
