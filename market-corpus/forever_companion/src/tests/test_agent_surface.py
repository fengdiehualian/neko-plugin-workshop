"""Agent 可见面静态门（AST）：44 个入口点收敛的防回潮契约。

背景（宿主侧事实，均有源码锚点）：
  - 宿主 Agent 评估路由（brain/task_executor.py）会把每个运行中插件的**全部
    Agent 可见入口**逐条拼进行业描述塞进分析器 prompt（`- pid: desc | entries:
    [eid: desc args(...)]`）。全部插件的总描述超过 3000 token
    （config/agent_settings.py::AGENT_PLUGIN_DESC_BM25_THRESHOLD）即触发 Stage 1
    粗筛（多一次 LLM 调用 + BM25 top-10 截断），挤占的是宿主里所有插件的分发精度。
  - `@llm_tool` 的保留动态入口（`__llm_tool__` 前缀）本就被 Agent 路由剥掉
    （app/agent_server/api_runtime.py），不在本门射程；主聊天模型的工具面只有
    12 个 mood_* 工具，与入口数无关。
  - Agent 触发是直连 IPC，**不经过面板 confirm**——clear_*/reset_all/prune_lanlan
    这类破坏性入口若留在可见面，用户聊天里一句话就可能被分析器误挑中执行。

契约（本轮决策：隐藏优先、不合并，32 个静态 @plugin_entry 收敛到 6 个可见）：
  每一个静态 `@plugin_entry` 要么在 `_AGENT_VISIBLE` 白名单里，要么装饰器带
  字面量 `metadata={"agent_hidden": True}`（宿主判定还认 agent_auto/agent_exposed/
  llm_exposed=False，本仓统一用 agent_hidden 一种写法）。白名单语义：状态查询、
  总开关、情绪 set/lift（消费者本就是模型与命令面板，见 CHANGELOG 第九轮）、能力
  列表与单项开关（"帮她开/关某功能"是真实聊天意图）。面板取数/写入/危险操作一律
  隐藏——它们的消费者是 ui.action 桥，从不该被聊天触发；且未声明
  llm_result_fields 的入口被 Agent 触发后只会回"执行完成"（utils/result_parser.py
  ::parse_plugin_result 的 fallback 分支），留着只是噪音。

新增入口默认必须隐藏：新 @plugin_entry 不写 agent_hidden 即红。确需进可见面，
把 id 加进 _AGENT_VISIBLE 并在 PR 说明里给聊天场景依据。
反向对照：删掉某入口的 agent_hidden 行 → 门 1 红；从白名单摘走仍可见的 id → 门 2 红；
给隐藏入口改名后白名单残留旧 id → 门 2 红。
"""

from __future__ import annotations

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 扫描面：全部插件源码（tests/vendor/.venv 除外）。当前静态入口集中在
# mixins/panel.py 与 mixins/capabilities.py，收编防散落。
_SCAN_SKIP = {".venv", "vendor", "tests"}

# Agent 可见白名单（6/32）。语义依据见模块 docstring。
_AGENT_VISIBLE = frozenset(
    {
        "get_status",
        "lift_mood",
        "list_capabilities",
        "set_capability",
        "set_mood",
        "toggle",
    }
)


def _iter_plugin_entries() -> list[tuple[Path, int, str, bool]]:
    """收集 (文件, 行号, entry id, 是否 agent_hidden)。id 必须是字面量。"""
    found: list[tuple[Path, int, str, bool]] = []
    for path in sorted(PLUGIN_ROOT.rglob("*.py")):
        if _SCAN_SKIP & set(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                dec_name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if dec_name not in ("plugin_entry", "entry"):
                    continue
                kw = {k.arg: k.value for k in dec.keywords}
                id_node = kw.get("id")
                eid = (
                    id_node.value
                    if isinstance(id_node, ast.Constant) and isinstance(id_node.value, str)
                    else None
                )
                assert eid, f"{path.name}:{node.lineno} @{dec_name} 必须写字面量 id（Agent 面门要求可静态判定）"
                hidden = False
                meta = kw.get("metadata")
                if isinstance(meta, ast.Dict):
                    for key, val in zip(meta.keys, meta.values):
                        if isinstance(key, ast.Constant) and key.value == "agent_hidden":
                            hidden = isinstance(val, ast.Constant) and val.value is True
                found.append((path, node.lineno, eid, hidden))
    return found


def test_all_static_entries_declare_agent_visibility():
    """门 1：白名单之外的每个 @plugin_entry 必须带字面量 agent_hidden=True。"""
    offenders = [
        f"{path.name}:{lineno} {eid}" for path, lineno, eid, hidden in _iter_plugin_entries() if eid not in _AGENT_VISIBLE and not hidden
    ]
    assert not offenders, "以下入口对 Agent 可见却不在白名单（补 metadata={'agent_hidden': True} 或说明可见理由）：" + "; ".join(offenders)


def test_agent_surface_whitelist_exactly_matches_code():
    """门 2：白名单与代码实际可见面互为充要——不留死名、不自相矛盾、不撞名。"""
    entries = _iter_plugin_entries()
    all_ids = [eid for _, _, eid, _ in entries]
    stale = _AGENT_VISIBLE - set(all_ids)
    assert not stale, f"白名单残留已不存在的入口 id：{sorted(stale)}"
    contradicted = {eid for _, _, eid, hidden in entries if eid in _AGENT_VISIBLE and hidden}
    assert not contradicted, f"白名单内却有入口自带 agent_hidden（矛盾，二选一）：{sorted(contradicted)}"
    assert len(set(all_ids)) == len(all_ids), "静态入口 id 撞名"
