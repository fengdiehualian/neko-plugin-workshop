"""Repository smoke tests for the standalone plugin package."""

from __future__ import annotations

import pathlib
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return tomllib.loads((_ROOT / "plugin.toml").read_text(encoding="utf-8"))


def test_plugin_manifest_declares_expected_entrypoint_and_runtime():
    manifest = _manifest()

    assert manifest["plugin"]["id"] == "neko_terraria"
    # entry 使用新格式 plugin.plugins. 前缀（与 neko_pawpilot 一致，neko-plugin check 通过）
    assert manifest["plugin"]["entry"] == "plugin.plugins.neko_terraria:NTerrariaPlugin"
    assert manifest["plugin_runtime"]["enabled"] is True


def test_plugin_manifest_declares_hosted_ui_surface_and_files_exist():
    manifest = _manifest()

    assert manifest["plugin"]["ui"]["enabled"] is True
    for panel in manifest["plugin"]["ui"]["panel"]:
        entry = _ROOT / panel["entry"]
        assert entry.exists(), f"UI entry missing: {panel['entry']}"


def test_plugin_source_modules_compile():
    import ast

    for py in sorted((_ROOT / "bridge").glob("*.py")) + sorted((_ROOT / "autonomous").glob("*.py")):
        ast.parse(py.read_text(encoding="utf-8"), filename=str(py))


# 已注册给宿主 LLM 的工具名（SDK 只认 @llm_tool 装饰器标记）
# — 与 goal_tools.py 唯一注册的 terraria_command + memory_entries 的记忆工具一致。
# 历史教训：llm/action_tools.py 曾有 ~30 个无装饰器的 llm_* "幽灵工具"，
# ai_guidance/提示词教了名字但宿主看不到，LLM 照调必失败。本测试防复活：
# 任何提示词里教 LLM 用的 terraria_* 工具名必须在注册名单内。
_REGISTERED_TOOL_PREFIXES = (
    "terraria_command",
    "terraria_remember",
    "terraria_recall",
    "terraria_forget",
)


def test_no_ghost_tool_names_in_guidance_prompts():
    import re

    targets = [
        _ROOT / "core" / "context.py",      # build_ai_guidance
        _ROOT / "llm" / "prompts.py",       # BEHAVIOR_RULES
        _ROOT / "autonomous" / "brain.py",  # LLM_THINK_PROMPT
        _ROOT / "llm" / "goal_tools.py",    # terraria_command description
    ]
    ghost = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        # 只查 LLM 会照抄的"调用形态"（terraria_X(）；说明文字/coalesce_key 不算
        for name in set(re.findall(r"terraria_[a-z_]+(?=\()", text)):
            if name.startswith(_REGISTERED_TOOL_PREFIXES):
                continue
            ghost.append(f"{path.name}:{name}")
    assert not ghost, f"提示词教 LLM 调用未注册的幽灵工具: {sorted(ghost)}"


def test_intent_fallback_covers_fishing_craft_give_mine():
    """正则 fallback 意图层必须覆盖钓鱼/合成/给物/挖矿/砍树（不依赖 LLM）。

    历史：单入口收敛后，若 LLM 意图解析不可用，fallback 正则只认
    follow/mine/guard/explore——"去钓鱼"直接 unknown，功能等于丢失。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_intent", _ROOT / "bridge" / "intent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cases = {
        "去钓鱼": ("finite", "fish", 3),
        "钓10条鱼": ("finite", "fish", 10),
        "钓竿是什么": None,  # 名词非动作
        "做把铜镐": ("finite", "craft", 1),
        "做2个火把": ("finite", "craft", 2),
        "合成铁锭": ("finite", "craft", 1),
        "做饭给我吃": None,  # 非给物
        "给我3个铁": ("finite", "give", 3),
        "把木材给我": ("finite", "give", 1),
        "挖5个铁矿": ("finite", "mine", 5),
        "砍5棵树": ("finite", "mine", 5),  # kind=mine, target=木材 → task_brain 转 chop
        "跟着我": ("longterm", "follow", 0),
        "守在这": ("longterm", "guard", 0),
    }
    for text, expect in cases.items():
        it = mod.parse(text)
        if expect is None:
            assert it.mode == "unknown", f"{text} 应 unknown，实为 {it.mode}/{it.kind}"
        else:
            mode, kind, amount = expect
            assert it.mode == mode and it.kind == kind, \
                f"{text} 应 ({mode},{kind})，实为 ({it.mode},{it.kind})"
            if mode == "finite":
                assert it.amount == amount, f"{text} 数量应 {amount}，实为 {it.amount}"
                assert it.steps, f"{text} 应有可执行 steps"
