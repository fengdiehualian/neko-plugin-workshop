"""fail-to-pass 证据：跨群「人对人」连击计数（④）的四处接线都是必要条件。

被验证的东西（SESSION-HANDOFF §27 第四项）：

- 连击**要真的数起来**（别人的消息 +1、@ 她 / 引用她归零、按群隔离）；
- 连击**要进打分输入**（否则数据白收）；
- 罚 0 分时**依据照样要出**（`人对人×N`）—— "先收数据再决定"全靠这一条；
- 触发下限**必须守住**（低于下限不许扣分，否则"先不动阈值"的承诺就破了）。

五处变异：计数不增 / 不归零 / 不进打分输入 / 依据不输出 / 下限失效。

铁律同 verify_outbound_guard_fail_to_pass.py：目标用例先绿、锚点唯一、恢复放 finally
并逐字节核对、每轮跑控制组。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_human_pair_streak_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [
    str(TESTS / "test_qq_human_pair_streak.py"),
    str(TESTS / "test_qq_reply_necessity.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "attention_gate_service.py",
        "连击不再累加（永远 0）",
        "        streak = int(self._human_pair_streak.get(key, 0)) + 1",
        "        streak = int(self._human_pair_streak.get(key, 0))",
    ),
    (
        "attention_gate_service.py",
        "@ 她 / 引用她不再归零",
        "        if addressed_to_bot:\n            self._human_pair_streak[key] = 0\n            return 0",
        "        if False:\n            self._human_pair_streak[key] = 0\n            return 0",
    ),
    (
        "attention_gate_service.py",
        "连击不再进打分输入",
        "            human_pair_streak=self._human_pair_streak.get(str(group_id or \"\").strip(), 0),",
        "            human_pair_streak=0,",
    ),
    (
        "reply_necessity.py",
        "罚 0 分时依据也不出了（数据收集落空）",
        '    reason = f"人对人×{streak}"\n    if penalty <= 0:\n        return 0, reason',
        "    reason = f\"人对人×{streak}\"\n    if True:\n        return 0, \"\"",
    ),
    (
        "reply_necessity.py",
        "触发下限失效（低于下限也扣分）",
        "    if streak < floor:\n        return 0, \"\"",
        "    if False:\n        return 0, \"\"",
    ),
]


def _adapt(source: str, anchor: str) -> str:
    """把按 LF 写的锚点适配到目标文件的换行风格（本仓库 CRLF 居多）。"""
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
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old_raw[:60]!r}")
            results.append((f"{rel}: {label}", False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {rel}: 替换没有实际发生")
            results.append((f"{rel}: {label}", False))
            continue

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
    results.append(("对照（连击计数在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 连击计数的四处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
