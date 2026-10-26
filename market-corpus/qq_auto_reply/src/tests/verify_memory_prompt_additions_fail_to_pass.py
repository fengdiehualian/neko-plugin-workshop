"""fail-to-pass 证据：这两处提示词补充各自的"坏版本"必须被看门狗抓住。

1. 召回指令那行**填空**（模板里的 `{recall_hint}` 渲染成空）→ 等于没写；
2. 记忆段那道闸拆掉（`should_use_memory_context` 不再早退）→ 会长在**没有工具**的轮里，
   等于让她调一个不存在的工具；
3. 「在办的事」**不过滤会话**（群里在等的活出现在私聊提示词里）；
4. 「在办的事」**不封顶**（有多少列多少，每轮白烧预算）；
5. 没有在办的事时**也输出空壳段落**。

铁律（沿用其它 verify 脚本）：替换前确认真的替换了；恢复放 `finally` 并逐字节核对；
每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_memory_prompt_additions_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_memory_prompt_additions.py"),
    str(TESTS / "test_qq_plugin_tool_followup.py"),
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "session_instruction_service.py",
        "召回指令填空（模板里那行渲染成空 = 等于没写）",
        """                    # 走到这里 = 这一轮**确实挂了** recall_memory（同一道
                    # `should_use_memory_context` 闸），所以附上"该查就查"是安全的。
                    recall_hint=pick_locale(RECALL_TRIGGER_HINT, locale),""",
        """                    recall_hint="",""",
    ),
    (
        "session_instruction_service.py",
        "记忆段那道闸拆掉（没有 recall 工具的那轮也长指令）",
        """        if not should_use_memory_context:
            return ""
        if is_group and not bool(""",
        """        if False:
            return ""
        if is_group and not bool(""",
    ),
    (
        "session_instruction_service.py",
        "「在办的事」不过滤会话（别人的活也报给她）",
        """        try:
            items = describe(
                is_group=bool(is_group),
                group_id=str(group_id or ""),
                sender_id=str(sender_id or ""),
            )""",
        """        try:
            items = []
            for _row in getattr(followups, "_pending", {}).values():
                _waited = int(max(0.0, __import__("time").time() - float(_row.get("created_at") or 0.0)))
                items.append(f"{_row.get('plugin_id') or '?'}（已等 {_waited}s）")""",
    ),
    (
        "plugin_tool_followup_service.py",
        "「在办的事」不封顶（有多少列多少）",
        "        for record in rows[:PENDING_PROMPT_MAX_ITEMS]:",
        "        for record in rows:",
    ),
    (
        "session_instruction_service.py",
        "没有在办的事也输出空壳段落（每轮白烧预算）",
        """        if not items:
            return ""
        return pick_locale(PENDING_COMMITMENTS_SECTION, locale).format(items="、".join(items))""",
        """        return pick_locale(PENDING_COMMITMENTS_SECTION, locale).format(
            items="、".join(items) if items else "（无）",
        )""",
    ),
    (
        "i18n/zh-CN.json",
        "bundle 那份 core_memory_section 又缺 {recall_hint}（运行时用的是 bundle → 改了等于没改）",
        "\"core_memory_section\": \"## 核心记忆（Core Memory）\\n"
        "以下是来自本体记忆系统的稳定记忆、人格背景或启动上下文。如果其中有相关内容，"
        "请自然地在回复中体现，但不要生硬地复述，也不要暴露内部记忆结构。\\n"
        "{memory_context}\\n{context_ready}\\n{recall_hint}\",",
        "\"core_memory_section\": \"## 核心记忆（Core Memory）\\n"
        "以下是来自本体记忆系统的稳定记忆、人格背景或启动上下文。如果其中有相关内容，"
        "请自然地在回复中体现，但不要生硬地复述，也不要暴露内部记忆结构。\\n"
        "{memory_context}\\n{context_ready}\",",
    ),
    (
        "__init__.py",
        "编辑器又内联一份层→模板映射（看门狗的真相源被架空）",
        "                from .prompt_fragment_templates import layer_default_templates\n\n"
        "                default_text = layer_default_templates().get(i18n_key, \"\")",
        "                default_map = {\"core_memory_section\": \"\"}\n"
        "                default_text = default_map.get(i18n_key, \"\")",
    ),
]


def _run_pytest() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *TEST_FILES, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    results: list[tuple[str, bool]] = []
    sources = {
        rel: (PLUGIN / rel).read_text(encoding="utf-8")
        for rel, _l, _o, _w in MUTATIONS
    }

    for rel, label, old, new in MUTATIONS:
        source = sources[rel]
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old[:60]!r}")
            results.append((label, False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {rel}: 替换没有实际发生")
            results.append((label, False))
            continue

        try:
            (PLUGIN / rel).write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            (PLUGIN / rel).write_text(source, encoding="utf-8")

        restored = (PLUGIN / rel).read_text(encoding="utf-8") == source
        ok = code != 0 and restored
        results.append((label, ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: exit={code}（期望非 0）；已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（两处补充都在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [name for name, ok in results if not ok]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 召回指令与「在办的事」都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
