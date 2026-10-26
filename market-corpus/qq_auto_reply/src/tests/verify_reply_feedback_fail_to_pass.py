"""fail-to-pass 证据：接话反馈闭环的八处接线都是必要条件。

被验证的东西（ROUND1 方案 B5）：她说完之后群里有没有人接 → 三档（silent/quiet/warm）
→ 写回注意力分数 + 注入提示词。八处变异各自打掉一个**必要条件**：

1. decay 循环里的结算调用被摘掉（没有新消息的群再也结算不出来）；
2. 时间窗口判断被摘掉（她刚说完就按「没人接」扣分）；
3. 消息路径不再计数（永远 0 条 → 全判 silent）；
4. 她发言时不清零（上一轮的计数漏进这一轮）；
5. 幂等守卫被摘掉（一轮结算多次 → 反复扣分）；
6. silent 档不扣分（闭环退回「只加不减」）；
7. 开关被无视（用户关了也照跑）；
8. 提示词不再注入反馈（分数在动、但她自己不知道）。

铁律（沿用 verify_user_blacklist_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿**；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_reply_feedback_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILE = str(TESTS / "test_qq_reply_feedback.py")
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "attention_service.py",
        "decay 循环不再结算（没人说话的群永远结算不出来）",
        "            self._settle_feedback(state, now)\n            self._write_state(state)",
        "            self._write_state(state)",
    ),
    (
        "attention_service.py",
        "时间窗口判断被摘掉（刚说完就按「没人接」扣分）",
        "        if now - last_reply_at < self._feedback_window_seconds():\n            return \"\"",
        "        if False:\n            return \"\"",
    ),
    (
        "attention_service.py",
        "消息路径不再计数（永远 0 条 → 全判 silent）",
        "        state.msgs_after_reply = max(0, int(state.msgs_after_reply or 0)) + 1",
        "        state.msgs_after_reply = max(0, int(state.msgs_after_reply or 0))",
    ),
    (
        "attention_service.py",
        "她发言时不清零（上一轮计数漏进这一轮）",
        "        state.msgs_after_reply = 0",
        "        state.msgs_after_reply = int(state.msgs_after_reply or 0)",
    ),
    (
        "attention_service.py",
        "幂等守卫被摘掉（一轮结算多次，反复扣分）",
        "        if int(state.feedback_settled_at or 0) >= last_reply_at:\n            return \"\"",
        "        if False:\n            return \"\"",
    ),
    (
        "attention_service.py",
        "silent 档不扣分（闭环退回「只加不减」）",
        '            tier, delta = "silent", -self._feedback_silent_penalty()',
        '            tier, delta = "silent", 0.0',
    ),
    (
        "attention_service.py",
        "开关被无视（用户关了也照跑）",
        '        return bool(self._setting("attention_feedback_enabled", True))',
        "        return True",
    ),
    (
        "attention_service.py",
        "提示词不再注入反馈（分数在动，她自己不知道）",
        "        feedback = self.feedback_line(group_id)",
        '        feedback = ""',
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

    baseline = _run(TARGET_FILE)
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
            target_code = _run(TARGET_FILE)
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

    code_clean = _run(TARGET_FILE)
    control_ok = code_clean == 0
    results.append(("对照（反馈闭环在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 反馈闭环的八处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
