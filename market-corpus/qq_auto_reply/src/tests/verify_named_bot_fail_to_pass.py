# -*- coding: utf-8 -*-
"""fail-to-pass 证据：叫她的名字 = @她（2026-09-29 使用者口径）。

改之前 `named_bot` 只值必要性打分里的 +40（`NAMED_BOT_SCORE`，恰好压在阈值线上）；
现在它是门控第 2 步的等同 @ 旁路。这份证据把那根接线拆开，确认拆掉就会红：

- 名字不再等同 @         → 凉群里叫她也回不了；
- 名字路径不再强制回复   → 门控放行了但没人保证她开口；
- 用户配的别名清单被忽略 → 名字根本匹配不上（判据的入口断了）。

判据顺序（`named_bot` 排在 `at_other` 之后）由
`test_a_message_atting_someone_else_is_not_saved_by_her_name` 守着。

手动运行（不参与 pytest 收集），要在**真 app root** 里跑（`parents[4]` 要能落到宿主仓库）：
    python plugin/plugins/qq_auto_reply/tests/verify_named_bot_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [
    str(TESTS / "test_qq_auto_reply_attention_gate.py"),
    str(TESTS / "test_qq_addressee.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_attention_behavior.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "attention_gate_service.py",
        "叫她的名字不再等同 @（凉群里叫她也不回头）",
        "        if named_me or (is_at_bot and not is_reply_to_bot):",
        "        if is_at_bot and not is_reply_to_bot:",
    ),
    (
        "attention_gate_service.py",
        "名字路径不强制回复（放行了但不保证她开口）",
        "                force_reply=True,\n            )",
        "                force_reply=False,\n            )",
    ),
    (
        "addressing.py",
        "名字的简称不再从人设里取（只剩全名，平时怎么叫都不算）",
        '    return str(bucket.get("昵称") or "").strip()',
        '    return ""',
    ),
    (
        "addressing.py",
        "用户配的别名清单被忽略（名字根本匹配不上）",
        "        names.extend(str(item).strip() for item in raw if str(item).strip())",
        "        pass",
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
    results.append(("对照（叫名字 = @ 在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 「叫她的名字 = @她」的每处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
