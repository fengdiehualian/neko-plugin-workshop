"""记忆相关的两处提示词补充：**该查就查**的指令 + 「你手上还没交的活」。

真机来由（16:19）：宅久喊"妈妈"，她回"嗯？怎么突然喊妈妈啦？"——"母子隐喻"这条关系
9-23 就定了、记忆里也有（一条 reflection + 若干 fact），只是那一轮没人去查：
* 语义召回当天还关着（`emb_svc=disabled:model_file_missing`，见 handoff §5）；
* 而"要不要调 `recall_memory`"是**模型自发**决定的，提示词里从来没写过什么时候该查。

所以这里钉两件事：
1. 挂了 `recall_memory` 的那一轮，记忆段里必须出现"涉及过去/关系/称呼/承诺 → 先查"；
   没挂记忆段时当然不该出现（否则等于让她调一个不存在的工具）。
2. 回投队列里"还在等的结果"必须每轮告诉她（那是**只有插件知道**的状态），但只列本会话的、
   最多两项，没有时整段不出现。
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import prompt_fragment_templates as tpl
from plugin.plugins.qq_auto_reply.session_instruction_service import QQSessionInstructionService

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]


# ── 语言选择：只维护 zh-CN / en 两本，且不该打宿主那种警告 ──────────────

def test_pick_locale_covers_chinese_variants_and_falls_back_to_english():
    mapping = {"zh-CN": "中文", "en": "english"}

    assert tpl.pick_locale(mapping, "zh-CN") == "中文"
    assert tpl.pick_locale(mapping, "zh") == "中文"
    assert tpl.pick_locale(mapping, "zh-TW") == "中文", "中文系一律取 zh-CN"
    assert tpl.pick_locale(mapping, "en") == "english"
    assert tpl.pick_locale(mapping, "ja") == "english", "非中文系回退英文"
    assert tpl.pick_locale(mapping, "") == "english"


def test_pick_locale_never_uses_the_host_loc_with_its_warning(capsys):
    """宿主的 `_loc` 对未知 locale 会 print 警告 —— 这两条文案每轮都取，不能走那条路。"""
    tpl.pick_locale(tpl.RECALL_TRIGGER_HINT, "fr")
    assert capsys.readouterr().out == ""


# ── 召回触发指令 ────────────────────────────────────────────────────────

def test_the_recall_hint_names_the_tool_and_the_triggers():
    zh = tpl.RECALL_TRIGGER_HINT["zh-CN"]

    assert "recall_memory" in zh, "没点名工具，模型不会知道该调哪个"
    for trigger in ("过去", "称呼", "关系", "答应"):
        assert trigger in zh, f"触发条件里少了 {trigger!r}"


def test_the_memory_section_carries_the_hint_placeholder_and_renders():
    assert "{recall_hint}" in tpl.CORE_MEMORY_SECTION

    rendered = tpl.CORE_MEMORY_SECTION.format(
        memory_context="（长期记忆）", context_ready="", recall_hint=tpl.RECALL_TRIGGER_HINT["zh-CN"],
    )
    assert "（长期记忆）" in rendered and "recall_memory" in rendered


def test_the_instruction_builder_passes_the_hint_and_gates_it_with_the_memory_section():
    """接线：`{recall_hint}` 必须真的被填（否则模板里那行是空的），且只在记忆段里。"""
    source = (_PLUGIN_DIR / "session_instruction_service.py").read_text(encoding="utf-8")

    assert source.count("pick_locale(RECALL_TRIGGER_HINT, locale)") == 2, (
        "两处渲染路径（模板 + 兜底）都要带上召回指令"
    )
    # 记忆段本身只在 should_use_memory_context 为真时才产出（第 782 行那个早退）
    head = source.index("async def _build_core_memory_section")
    guard = source[head:head + 900]
    assert "if not should_use_memory_context:" in guard, (
        "记忆段的闸挪走了 —— 召回指令会长在没有工具的那一轮里"
    )


# ── 「你手上还没交的活」─────────────────────────────────────────────────

def _section_stub(items=None, *, boom: bool = False):
    logs: list[str] = []

    def _pending_items_for(**_kw):
        if boom:
            raise RuntimeError("boom")
        return list(items or [])

    plugin = SimpleNamespace(
        plugin_tool_followup_service=SimpleNamespace(pending_items_for=_pending_items_for),
        logger=SimpleNamespace(warning=lambda msg, *a, **k: logs.append(str(msg))),
    )
    # 被调方法是服务上的方法：`self.plugin` 才是插件。所以桩要包一层。
    return SimpleNamespace(plugin=plugin), logs


def _build(stub, *, locale="zh-CN", is_group=False):
    return QQSessionInstructionService._build_pending_commitments_section(
        stub, is_group=is_group, group_id="g1" if is_group else None,
        sender_id="u1", locale=locale,
    )


def test_no_pending_work_means_no_section_at_all():
    """没有在办的事时**整段不出现** —— 空壳标题是纯噪音（每轮都发）。"""
    stub, _logs = _section_stub([])
    assert _build(stub) == ""


def test_pending_work_is_named_in_the_prompt():
    stub, _logs = _section_stub(["writer_power_analysis（已等 42s）"])
    section = _build(stub)

    assert "writer_power_analysis（已等 42s）" in section
    assert "没交的活" in section
    assert "别再承诺" in section, "还要明确：结果没到之前不许重复承诺"


def test_the_pending_section_follows_the_locale():
    stub, _logs = _section_stub(["writer_power_analysis（已等 42s）"])
    section = _build(stub, locale="en")

    assert "pending" in section.lower()
    assert "没交的活" not in section


def test_a_failing_followup_service_does_not_take_the_prompt_down():
    """拿不到状态就当没有 —— 不能让一个可选段落把整份系统提示词搞崩。"""
    stub, logs = _section_stub(boom=True)
    assert _build(stub) == ""
    assert logs, "失败必须留痕"


def test_the_builder_without_the_service_is_silent():
    stub = SimpleNamespace(
        plugin=SimpleNamespace(logger=SimpleNamespace(warning=lambda *a, **k: None)),
    )
    assert _build(stub) == ""


def test_the_pending_section_is_appended_to_the_prompt():
    """接线：这一段要真的被拼进系统提示词（不是写了个函数没人调）。"""
    source = (_PLUGIN_DIR / "session_instruction_service.py").read_text(encoding="utf-8")

    assert "_build_pending_commitments_section(" in source
    assert "if pending_section:" in source and "sections.append(pending_section)" in source


# ── 防漂移：bundle 文案会**盖住** Python 模板 ─────────────────────────────
#
# 这次踩到的坑：给 `CORE_MEMORY_SECTION` 加了 `{recall_hint}`，而 `i18n/*.json` 里各有一份
# `core_memory_section` 的**旧副本**（没这个占位符）。`_resolve_static_layer` 的顺序是
# 「用户覆盖 → bundle → Python 模板」，所以运行时用的是 bundle 那份，多出来的 kwarg 被
# `format` 静默忽略 —— **改了等于没改**（真机 `prompt_editor` 的 effective_text 是这么露的）。
# 这条看门狗把所有"bundle 里有副本"的提示词层逐一对齐占位符集合。

def test_bundle_copies_of_prompt_layers_keep_the_same_placeholders():
    import json
    import re

    from plugin.plugins.qq_auto_reply.session_instruction_service import QQSessionInstructionService

    templates = tpl.layer_default_templates()
    bundles = {
        name: json.loads((_PLUGIN_DIR / "i18n" / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("zh-CN", "en")
    }
    layer_keys = {
        layer.get("i18n_key") for layer in QQSessionInstructionService._PROMPT_LAYERS
        if layer.get("i18n_key")
    }

    checked = 0
    for key in sorted(layer_keys):
        python_text = templates.get(key)
        if python_text is None:
            continue
        expected = set(re.findall(r"\{([a-z_][a-z0-9_]*)\}", python_text))
        for name, bundle in bundles.items():
            if key not in bundle:
                continue
            checked += 1
            actual = set(re.findall(r"\{([a-z_][a-z0-9_]*)\}", str(bundle[key])))
            assert actual == expected, (
                f"bundle[{name}]['{key}'] 的占位符与模板不一致："
                f"缺 {sorted(expected - actual)} 多 {sorted(actual - expected)} —— "
                f"bundle 会盖住模板，模板改了也不生效"
            )
    assert checked, "一个 bundle 副本都没检查到 —— 这条看门狗失去意义了"


def test_the_editor_mapping_has_one_source_of_truth():
    """编辑器回显与看门狗必须共用同一份层→模板映射（内联副本漂了等于没有闸）。"""
    source = (_PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")

    assert "layer_default_templates()" in source, "编辑器没用上那份收口的映射"
    assert "default_map = {" not in source, "内联的 default_map 又回来了"
