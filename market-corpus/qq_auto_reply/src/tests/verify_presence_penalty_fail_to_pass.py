# -*- coding: utf-8 -*-
"""fail-to-pass 证据：存在感惩罚减轻之后，每条判据仍是必要条件。

使用者口径（2026-09-29）：「6 条就休息频率有点少了，发言惩罚减轻一点」。

这次改的是**三个常数**（`SELF_RATIO_FREE` 0.25→0.45、`SELF_RATIO_FULL` 0.60→0.80、
`SELF_PENALTY_MAX` 25→15），所以证据的形状与别的 verify_* 不同：不是"摘掉一根接线"，
而是"把旋钮拧回旧值 / 拧到另一个极端"，看目标用例是否照样红。

⚠️ 手算期望值必须带上**门控第 1.5 步**：`evaluate()` 在打分之前就把当前这条消息记进
近期发言窗口，所以"摆 6 条她 + 4 条别人"在真机里是 11 条的窗口（占比 0.545 而不是 0.6）。
第一版用例就是漏了这条，两个集成用例当场红。

手动运行（不参与 pytest 收集），要在**真 app root** 里跑（`parents[4]` 要能落到宿主仓库）：
    python plugin/plugins/qq_auto_reply/tests/verify_presence_penalty_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [
    str(TESTS / "test_qq_reply_necessity.py"),
    str(TESTS / "test_qq_necessity_gate.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_pacing_hint.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "reply_necessity.py",
        "免费区拧回 0.25（她占窗口三成又开始扣分 → 回到「说几句就休息」）",
        "SELF_RATIO_FREE = 0.45",
        "SELF_RATIO_FREE = 0.25",
    ),
    (
        "reply_necessity.py",
        "满罚拧回 25（轻罚变成重罚）",
        "SELF_PENALTY_MAX = 15.0",
        "SELF_PENALTY_MAX = 25.0",
    ),
    (
        "reply_necessity.py",
        "饱和点拧回 0.60（六成就罚满）",
        "SELF_RATIO_FULL = 0.80",
        "SELF_RATIO_FULL = 0.60",
    ),
    (
        "reply_necessity.py",
        "免费区推到 0.95（几乎不再罚：她刷屏也照答）",
        "SELF_RATIO_FREE = 0.45",
        "SELF_RATIO_FREE = 0.95",
    ),
    (
        "reply_necessity.py",
        "满罚压到 5（罚得太轻，一个人刷屏也停不下来）",
        "SELF_PENALTY_MAX = 15.0",
        "SELF_PENALTY_MAX = 5.0",
    ),
    (
        "reply_necessity.py",
        "存在感惩罚整条摘掉（说多少都不让一让）",
        "    return -int(round(penalty)), \"存在感\"",
        "    return 0, \"\"",
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
    results.append(("对照（减轻后的常数必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 存在感惩罚的每个旋钮都在起作用，且没被调到失效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
