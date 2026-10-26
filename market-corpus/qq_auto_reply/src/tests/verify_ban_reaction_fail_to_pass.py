# -*- coding: utf-8 -*-
"""fail-to-pass 证据：禁言反应的每一道闸都是必要条件。

使用者口径（2026-09-29）：「可以让猫娘对正在聊天的对象的禁言做出反应吗」→
对象 = 正在和她对话的人（一来一回）、反应 = 合成系统提示让模型自己说、
解禁也反应、自己/全员被禁言静默、每群冷却 600 秒 + 同一事件只反应一次。

覆盖三层：
  · 连接层（`_vendor` 副本，宿主那份同改）：第三方禁言/解禁要入队、归一化要认得出事件；
  · 派发层：trusted 闸、对话对象闸、两道冷却、合成消息的形状；
  · 门控层："她回他"要真的被记进对话对象。

手动运行（不参与 pytest 收集），要在**真 app root** 里跑（`parents[4]` 要落到宿主仓库）：
    python plugin/plugins/qq_auto_reply/tests/verify_ban_reaction_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [
    str(TESTS / "test_qq_ban_reaction.py"),
    str(TESTS / "test_qq_group_ban_notice.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_dialogue_partner.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "_vendor/connection_onebot/onebot_client.py",
        "第三方被禁言又不往上游送（她永远看不到这件事）",
        "            return True\n\n        if sub_type == \"ban\":",
        "            return False\n\n        if sub_type == \"ban\":",
    ),
    (
        "_vendor/connection_onebot/onebot_client.py",
        "归一化把 group_ban 也写成 sub_type（上游认不出这是什么通知）",
        "                notice_kind = \"group_ban\" if raw_notice == \"group_ban\" else sub_type",
        "                notice_kind = sub_type",
    ),
    (
        "message_dispatcher.py",
        "trusted 闸失效（normal 群里她也替别人被禁言发言）",
        "        if level != \"trusted\":",
        "        if False:",
    ),
    (
        "message_dispatcher.py",
        "对话对象闸失效（陌生人被禁言她也开口）",
        "        if not gate.is_in_dialogue_with(group_id, user_id):",
        "        if False:",
    ),
    (
        "message_dispatcher.py",
        "同一事件的冷却失效（同一件事被反复反应）",
        "        if now - last_same < cooldown:",
        "        if False:",
    ),
    (
        "message_dispatcher.py",
        "每群冷却失效（批量禁言时她连说好几句）",
        "        if now - last_group < cooldown:",
        "        if False:",
    ),
    (
        "message_dispatcher.py",
        "合成消息没改写成群消息（下游当通知处理，一个字都发不出去）",
        "        message[\"message_type\"] = \"group\"\n        message[\"group_id\"] = group_id",
        "        message[\"message_type\"] = \"notice\"\n        message[\"group_id\"] = group_id",
    ),
    (
        "message_dispatcher.py",
        "禁言反应不再绕过门控（判过了还要被注意力/必要性再审一遍，可能一个字都不说）",
        "    GATE_BYPASS_SYNTHETIC_SOURCES = frozenset({KIND_GROUP_JOIN_NOTICE, KIND_GROUP_BAN_NOTICE})",
        "    GATE_BYPASS_SYNTHETIC_SOURCES = frozenset({KIND_GROUP_JOIN_NOTICE})",
    ),
    (
        "message_dispatcher.py",
        "禁言反应又被塞进「必定回复」（替猫娘决定必须开口）",
        "    FORCED_SYNTHETIC_SOURCES = frozenset({KIND_GROUP_JOIN_NOTICE})",
        "    FORCED_SYNTHETIC_SOURCES = frozenset({KIND_GROUP_JOIN_NOTICE, KIND_GROUP_BAN_NOTICE})",
    ),
    (
        "pipeline_models.py",
        "禁言反应没登记成合成轮（名义发言人的话被当成他这一轮说的）",
        "    KIND_GROUP_JOIN_NOTICE,\n    KIND_GROUP_BAN_NOTICE,\n    KIND_PLUGIN_TOOL_RESULT,\n})",
        "    KIND_GROUP_JOIN_NOTICE,\n    KIND_PLUGIN_TOOL_RESULT,\n})",
    ),
    (
        "attention_gate_service.py",
        "她回他不再记进对话对象（一来一回只剩半条腿，判据退化成「谁叫过她」）",
        "            if user_id:\n                self._dialogue.note_reply(group_id, user_id, now=now)",
        "            if False:\n                self._dialogue.note_reply(group_id, user_id, now=now)",
    ),
]


def _adapt(source: str, anchor: str) -> str:
    if "\r\n" in source and "\r\n" not in anchor:
        return anchor.replace("\n", "\r\n")
    return anchor


def _run(*test_files: str) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *test_files, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    results: list[tuple[str, bool]] = []
    sources = {rel: (PLUGIN / rel).read_text(encoding="utf-8")
               for rel, _l, _o, _w in MUTATIONS}

    baseline = _run(*TARGET_FILES)
    if baseline != 0:
        print(f"[FAIL] 目标用例在干净树上就是红的（exit={baseline}）—— 本次取证无效，先修它")
        return 1
    print(f"[OK  ] 目标用例在干净树上绿（exit={baseline}）")

    for rel, label, old_raw, new_raw in MUTATIONS:
        source = sources[rel]
        old, new = _adapt(source, old_raw), _adapt(source, new_raw)
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old_raw[:70]!r}")
            results.append((f"{rel}: {label}", False))
            continue
        mutated = source.replace(old, new, 1)
        target_code = control_code = -1
        try:
            (PLUGIN / rel).write_text(mutated, encoding="utf-8")
            target_code = _run(*TARGET_FILES)
            control_code = _run(CONTROL_FILE)
        finally:
            (PLUGIN / rel).write_text(source, encoding="utf-8")

        restored = (PLUGIN / rel).read_text(encoding="utf-8") == source
        ok = target_code != 0 and control_code == 0 and restored
        results.append((f"{rel}: {label}", ok))
        print(
            f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: "
            f"目标 exit={target_code}（期望非 0）；控制组 exit={control_code}（期望 0）；已恢复={restored}"
        )

    code_clean = _run(*TARGET_FILES)
    control_ok = code_clean == 0
    results.append(("对照（禁言反应在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 禁言反应的每道闸都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
