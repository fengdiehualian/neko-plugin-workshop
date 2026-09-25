"""async 卫生静态门（AST）：钉死两类"看起来能跑、其实是雷"的写法。

**门 1：async 方法必须 await（协程工厂豁免）。**
`passed, reason, resolved = self._review_write_gate(...)` 这种漏 await，Python 不会
在定义期报错——三元解包作用在 coroutine 对象上要么运行期才炸，要么更糟：把协程对象
当值一路传下去。本轮把 `_review_write_gate` / `_channel_dormancy` 改成 async 时，
正是靠这条门确认三个调用点一个都没漏。豁免两类合法写法：
  - **lambda 体内**：依赖注入工厂（`http=lambda *a, **k: self._proactive_http(...)`）
    本就要返回一个协程交给被注入方 await，不是漏写；
  - **协程调度白名单**：`create_task` / `ensure_future` / `gather` / `shield` /
    `wait_for` / `as_completed` / `run_coroutine_threadsafe` / `to_thread` 的实参位。

**门 2：async 函数体内不得直调 sync 版阻塞 IO。**
宿主 `core_config.json` 的读取是阻塞文件 IO（带明文 key；5 秒缓存，但"缓存未命中
即读盘"）。tick 与面板入口共用插件那一条事件循环，直调就把整条管线拖住。async 路径
一律走对偶版 `_aload_core_config()`（内部就是 `asyncio.to_thread(<sync>)`，与宿主
`asave_characters` 那套 `a*` 约定同构）。sync 版仍合法存在于启动期、sync 委托本体与
注入 lambda——本门只看 `async def` 体内，且对偶版自己把方法名传给 `to_thread`（属性
引用，不是调用），天然不在射程。

新增同类阻塞 sync 方法时，把 sync/a* 两版一起登记进 `_BLOCKING_SYNC_CALLS`。
"""

from __future__ import annotations

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 协程调度白名单：出现在这些调用的实参位上，"不 await" 是设计而非遗漏
_COROUTINE_SINKS = frozenset(
    {
        "as_completed",
        "create_task",
        "ensure_future",
        "gather",
        "run_coroutine_threadsafe",
        "shield",
        "to_thread",
        "wait_for",
    }
)

# 门 2 盯的同步阻塞方法（键写 sync 名，对偶版按 "a" + sync 名 自动豁免）
_BLOCKING_SYNC_CALLS = frozenset({"_load_core_config"})


def _plugin_modules() -> list[tuple[Path, ast.Module]]:
    parsed: list[tuple[Path, ast.Module]] = []
    for path in sorted(PLUGIN_ROOT.rglob("*.py")):
        if ".venv" in path.parts or "vendor" in path.parts or "tests" in path.parts:
            continue
        parsed.append((path, ast.parse(path.read_text(encoding="utf-8"))))
    return parsed


def _self_calls(tree: ast.AST) -> list[ast.Call]:
    """所有 `self.<name>(...)` 形态的调用。"""
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            out.append(node)
    return out


def _deferred_call_ids(tree: ast.AST) -> set[int]:
    """"故意不 await"的调用节点 id：lambda 体内的、以及协程调度白名单实参位上的。"""
    deferred: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    deferred.add(id(inner))
        elif isinstance(node, ast.Call):
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname in _COROUTINE_SINKS:
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    if isinstance(arg, ast.Call):
                        deferred.add(id(arg))
    return deferred


def test_no_unawaited_async_self_calls() -> None:
    modules = _plugin_modules()
    async_names: set[str] = set()
    for _, tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                async_names.add(node.name)
    assert async_names, "没解析到任何 async 方法，门本身失效"

    offenders: list[str] = []
    for path, tree in modules:
        awaited = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)}
        deferred = _deferred_call_ids(tree)
        for call in _self_calls(tree):
            name = call.func.attr  # type: ignore[union-attr]
            if name not in async_names:
                continue
            if id(call) in awaited or id(call) in deferred:
                continue
            offenders.append(f"{path.relative_to(PLUGIN_ROOT)}:{call.lineno}: self.{name}() 未 await")

    assert not offenders, (
        "async 方法被当同步方法调用（漏 await 会把协程对象当值传递）：\n  " + "\n  ".join(offenders)
    )


def test_no_sync_blocking_calls_inside_async_defs() -> None:
    offenders: list[str] = []
    for path, tree in _plugin_modules():
        deferred = _deferred_call_ids(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for call in _self_calls(fn):
                name = call.func.attr  # type: ignore[union-attr]
                if name not in _BLOCKING_SYNC_CALLS:
                    continue
                if id(call) in deferred:
                    continue  # 注入 lambda 内：延迟到被注入方那边取
                offenders.append(
                    f"{path.relative_to(PLUGIN_ROOT)}:{call.lineno}: "
                    f"async def {fn.name}() 内直调 sync {name}()，应改走 a*{name.lstrip('_')}()"
                )

    assert not offenders, (
        "async 路径必须走 a* 对偶版，不得在事件循环里阻塞读盘：\n  " + "\n  ".join(offenders)
    )


def test_only_unified_exits_touch_the_store() -> None:
    """`self.store.*` 只允许出现在 mixins/shards.py 的三个统一出口里。

    1.2.2 立了 `_store_read` / `_store_write` / `_store_delete` 三个出口，语义是
    "未通电留 warning、真失败留 warning 并原样回传 Result"。绕过出口直连 store 的
    代码拿不到这三件事，表现就是"面板显示成功、盘上没写"与"读失败被当成没数据"。
    1.3.0 第六轮把 debug_entries 里最后 12 处直连收编进来，此后本门零豁免。

    出口自己是唯一合法引用点：按函数名白名单，不放开整个文件，将来往 shards.py
    里塞新的直连调用一样会被抓。
    """
    exits = {"_store_read", "_store_write", "_store_delete"}
    offenders: list[str] = []
    for path, tree in _plugin_modules():
        # 出口函数自己的子树 → 合法引用位；其他任何函数/模块级直连都抓
        allowed_nodes: set[int] = set()
        if path.name == "shards.py":
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in exits:
                    for inner in ast.walk(fn):
                        allowed_nodes.add(id(inner))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"
                and node.value.attr == "store"
            ):
                continue
            if id(node) in allowed_nodes:
                continue
            offenders.append(f"{path.relative_to(PLUGIN_ROOT)}:{node.lineno}: self.store.{node.attr}")

    assert not offenders, (
        "直连 self.store 绕过了统一出口（丢未通电/读写失败留痕），请改走 "
        "_store_read/_store_write/_store_delete：\n  " + "\n  ".join(offenders)
    )
