"""看门狗：`source_kind` 的**判据集合**必须覆盖**全部真实写者**。

为什么需要它（这不是假想的风险，是已经发生的两个洞）：

  `SYNTHETIC_SOURCE_KINDS` 曾经同时犯两类错：

  1. **漏真实生产者**：`proactive_private` / `proactive_group` 不在集合里。
     主动发言把 `sender_id` 设成 admin（`runtime_ops_service.py`），于是
     `reply_context_node` 不清空 `memory_sender_id` → 管理员在**本群的成员域**
     画像被注入一条公开发到全群的回复。写侧被 `group_facing` 挡住，读侧没有。

  2. **留死常量**：集合里的 `buffer_delayed` 全仓零生产者。

  同一处漂移还波及第三份判据：`reply_pipeline` 里**内联的第三个元组**
  （`skip_buffer`）同样漏了 `proactive_group`，导致主动群发被投进 reply_buffer。

三份判据靠人工同步 → 必然漂。本测试把"同步"变成断言：集合必须与源码里
`source_kind=` 的**赋值点**对齐。新增来源时要么进集合、要么进下面的分类清单。

判据实现说明：用**文本扫描**而非 import 反射 —— 赋值点在多个模块里，且部分
模块 import 成本高；扫描 `source_kind="..."` / `source_kind='...'` 字面量足以
覆盖（本仓全部赋值点都是字面量，无变量间接）。
"""
from __future__ import annotations

import pathlib
import re

from plugin.plugins.qq_auto_reply import pipeline_models

BASE = pathlib.Path(__file__).resolve().parents[1]

#: 全部已声明来源 → 它是"真实说话的人"还是"名义发言人"，以及是否走缓冲链路。
#: 新增 `source_kind=` 时必须在这里登记 —— 这份表就是本测试的真相源。
DECLARED: dict[str, dict[str, bool]] = {
    pipeline_models.KIND_INCOMING:          {"synthetic": False, "buffer_internal": False},
    pipeline_models.KIND_INCOMING_PRIVATE:  {"synthetic": False, "buffer_internal": False},
    pipeline_models.KIND_INCOMING_GROUP:    {"synthetic": False, "buffer_internal": False},
    pipeline_models.KIND_RAPID_FIRE:        {"synthetic": True,  "buffer_internal": True},
    pipeline_models.KIND_PROACTIVE_PRIVATE: {"synthetic": True,  "buffer_internal": True},
    pipeline_models.KIND_PROACTIVE_GROUP:   {"synthetic": True,  "buffer_internal": True},
    pipeline_models.KIND_RETROACTIVE_REVIEW: {"synthetic": True, "buffer_internal": False},
    pipeline_models.KIND_GROUP_JOIN_NOTICE: {"synthetic": True,  "buffer_internal": False},
    pipeline_models.KIND_GROUP_BAN_NOTICE:  {"synthetic": True,  "buffer_internal": False},
    pipeline_models.KIND_PLUGIN_TOOL_RESULT: {"synthetic": True,  "buffer_internal": True},
}

#: 合法地**没有** `source_kind="字面量"` 赋值点的来源，附理由。
#: 豁免必须是"它由别处产出"，不是"我懒得管"——所以每条都写清产出方式。
NOT_ASSIGNED_AS_LITERAL: dict[str, str] = {
    pipeline_models.KIND_INCOMING:
        "QQReplyRequest.source_kind 的默认值（pipeline_models.py），"
        "不出现字面量赋值：真实群/私聊消息走默认路径",
    pipeline_models.KIND_GROUP_JOIN_NOTICE:
        "由 message_dispatcher 的 message['_synthetic_source'] 承载，"
        "经变量注入 source_kind=（message_dispatcher.py:912 "
        "`source_kind=synthetic_source or \"incoming_group\"`），"
        "因此静态扫描看不到这个字面量",
    pipeline_models.KIND_GROUP_BAN_NOTICE:
        "与入群通知同一条路：由 message_dispatcher 的 message['_synthetic_source'] 承载，"
        "经变量注入 source_kind=（`source_kind=synthetic_source or \"incoming_group\"`），"
        "因此静态扫描看不到这个字面量",
    pipeline_models.KIND_PLUGIN_TOOL_RESULT:
        "唯一生产者是 plugin_tool_followup_service.deliver()：它用**模块常量**"
        "`source_kind=KIND_PLUGIN_TOOL_RESULT` 组请求（同文件从 pipeline_models "
        "import 这个常量），所以调用点没有字面量可扫",
}


def _source_files() -> list[pathlib.Path]:
    return [
        p for p in sorted(BASE.glob("*.py"))
        if p.name not in {"pipeline_models.py"}  # 声明处不产生赋值点
    ]


def _assigned_kinds() -> set[str]:
    """用 **AST** 扫出所有 `source_kind=<...>` 关键字实参里的字面量取值。

    为什么不用正则（这里踩过两次假阳性）：
      - ``source_kind=source_kind``（把同名参数往下传）被当成赋值；
      - ``source_kind=getattr(request, "source_kind", "")`` 里的**内层字符串**
        被当成了取值。

    AST 只认"关键字实参名为 source_kind"的节点，再从中取字符串字面量，
    这两类误判都不会发生。覆盖两种真实形态：

        source_kind="proactive_group"                     纯字面量
        source_kind=synthetic_source or "incoming_group"    BoolOp 里的字面量
    """
    import ast

    found: set[str] = set()

    def _collect(node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
        elif isinstance(node, ast.BoolOp):
            for v in node.values:
                _collect(v)
        elif isinstance(node, ast.IfExp):
            _collect(node.body)
            _collect(node.orelse)
        # 其它形态（Name / Call / Attribute / 三元表达式等）不产出**新的**取值，
        # 它们只是转发已有值，因此刻意不递归进去。

    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "source_kind":
                    _collect(kw.value)
    return found


def test_every_assigned_kind_is_declared():
    """每个真实赋值点都必须在 DECLARED 里登记。

    漏登记 ⇒ 新来源的合成性/缓冲归属没人决定 ⇒ 就是本次两个洞的成因。
    """
    assigned = _assigned_kinds()
    unknown = sorted(assigned - set(DECLARED))
    assert not unknown, (
        f"这些 source_kind 在代码里被赋值，但没有在 DECLARED 里登记：{unknown}。"
        f"请登记它是否『名义发言人』(synthetic) 与是否走缓冲链路 (buffer_internal)。"
    )


def test_no_declared_kind_is_dead():
    """已登记但源码里零赋值的来源 = 死常量，必须删（`buffer_delayed` 就是）。

    `NOT_ASSIGNED_AS_LITERAL` 里的少数来源由别处产出（默认值 / 变量注入），
    不在此列 —— 每条豁免都写明产出方式，见该常量定义。
    """
    assigned = _assigned_kinds()
    dead = sorted(set(DECLARED) - assigned - set(NOT_ASSIGNED_AS_LITERAL))
    assert not dead, (
        f"这些来源已登记却没有任何赋值点（死常量）：{dead}。"
        f"删掉它们，否则判据集合会让人以为某条路径存在。"
    )


def test_exemptions_are_justified_and_still_reachable():
    """豁免名单必须小、有理由，且理由里提到的产出点真的存在。

    防的是"为了过测试而往豁免里塞东西"：每条豁免都要在源码里找得到它的产出。
    """
    # 上限从 3 提到 4：`_synthetic_source` 承载的"某某通知"是一族（入群欢迎、
    # 禁言反应…），每加一种通知就多一条，而它们的成因与产出方式完全相同
    # （`source_kind=synthetic_source or "incoming_group"`）。这条闸防的是为了过测试
    # 往豁免里塞东西，不是不许这一族长大 —— 但仍然要有上限，所以每加一条都值得再看一眼。
    assert len(NOT_ASSIGNED_AS_LITERAL) <= 4, (
        f"豁免膨胀到 {len(NOT_ASSIGNED_AS_LITERAL)} 条，重新审视一遍"
    )
    all_src = "\n".join(p.read_text(encoding="utf-8") for p in _source_files())
    for kind, why in NOT_ASSIGNED_AS_LITERAL.items():
        assert why.strip(), f"{kind} 没有写明豁免理由"
        # 见名知义：理由里必须指出产出点（文件/变量名），且该产出点确实在源码里
        assert any(tok in all_src for tok in (kind, "synthetic_source")), (
            f"{kind} 的豁免理由指向的产出点在源码里找不到"
        )


def test_synthetic_set_matches_the_table():
    """``SYNTHETIC_SOURCE_KINDS`` 必须恰好等于表里 synthetic=True 的那批。"""
    expected = {k for k, v in DECLARED.items() if v["synthetic"]}
    actual = set(pipeline_models.SYNTHETIC_SOURCE_KINDS)
    assert actual == expected, (
        f"SYNTHETIC_SOURCE_KINDS 与登记表不一致：\n"
        f"  多了（会导致读侧/写侧误判为合成轮）: {sorted(actual - expected)}\n"
        f"  少了（合成轮逃过过滤，可能把名义 sender 的私人事实写进公开发言）: "
        f"{sorted(expected - actual)}"
    )


def test_buffer_internal_set_matches_the_table():
    """``BUFFER_INTERNAL_SOURCE_KINDS`` 必须恰好等于表里 buffer_internal=True 的那批。"""
    expected = {k for k, v in DECLARED.items() if v["buffer_internal"]}
    actual = set(pipeline_models.BUFFER_INTERNAL_SOURCE_KINDS)
    assert actual == expected, (
        f"BUFFER_INTERNAL_SOURCE_KINDS 与登记表不一致：\n"
        f"  多了: {sorted(actual - expected)}\n"
        f"  少了（该来源会被投进 reply_buffer，自我延迟/自我合并）: "
        f"{sorted(expected - actual)}"
    )


def test_proactive_kinds_are_synthetic():
    """主动发言必须被判为合成轮 —— 这是本条链路上唯一的隐私护栏。

    它把 admin 当 `sender_id`，而 admin 在本群的成员域画像会被『用户画像段』
    无条件注入（`session_instruction_service._append_user_profile_section` 不查
    `use_memory_context`）。判据一漏，管理员的本群私有事实就随 bot 的主动发言
    公开发到群里。
    """
    for kind in (pipeline_models.KIND_PROACTIVE_PRIVATE,
                 pipeline_models.KIND_PROACTIVE_GROUP):
        assert pipeline_models.is_synthetic_source(kind), (
            f"{kind} 未被判为合成轮 —— 名义 sender（admin）的成员域画像会被注入"
        )


def test_proactive_kinds_skip_the_reply_buffer():
    """主动发言不得被投回 reply_buffer（否则自我延迟/自我合并）。"""
    for kind in (pipeline_models.KIND_PROACTIVE_PRIVATE,
                 pipeline_models.KIND_PROACTIVE_GROUP):
        assert kind in pipeline_models.BUFFER_INTERNAL_SOURCE_KINDS


def test_reply_pipeline_does_not_reinline_the_kind_list():
    """`reply_pipeline` 不得再内联一份来源元组（第三份判据正是漂移来源之一）。"""
    text = (BASE / "reply_pipeline.py").read_text(encoding="utf-8")
    # 内联形态长这样： source_kind', '') in ('a', 'b', ...)
    inlined = re.search(r"source_kind'?,?\s*[\"']?\)?\s*in\s*\(\s*[\"']", text)
    assert inlined is None, (
        "reply_pipeline 里又出现了内联的 source_kind 元组 —— 请改用 "
        "pipeline_models.BUFFER_INTERNAL_SOURCE_KINDS"
    )


def test_synthetic_predicate_agrees_with_the_set():
    """``is_synthetic_source`` 必须就是集合成员判定（不得另立判据）。"""
    for kind in DECLARED:
        assert pipeline_models.is_synthetic_source(kind) == (
            kind in pipeline_models.SYNTHETIC_SOURCE_KINDS
        ), f"is_synthetic_source({kind!r}) 与集合成员关系不一致"


def test_null_and_unknown_sources_are_not_synthetic():
    """None / 空串 / 未知来源一律**不**算合成轮（fail-open 到"真实发言"）。

    这里刻意不 fail-closed：默认路径 `incoming` 必须被当作真实发言，否则
    正常群消息会被当成合成轮、读不到成员记忆、也不写成员桶。
    """
    assert pipeline_models.is_synthetic_source(None) is False
    assert pipeline_models.is_synthetic_source("") is False
    assert pipeline_models.is_synthetic_source("never_heard_of_it") is False
