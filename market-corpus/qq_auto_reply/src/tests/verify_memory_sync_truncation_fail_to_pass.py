"""fail-to-pass 证据：记忆同步截断的每处必要条件逐个拆掉，看门狗必须红。

拆的三种情况，每一种都对应一个真实的坏结果：

1. **不截断** → 真机现象复现：附件正文整段同步给 Memory Server，bootstrap 逐字回灌，
   每轮 prompt 涨一份（实测 +12707/次，三次把 12.5k 抬到 50.7k）；
2. **就地改短会话历史那一条** → 她本轮就只读得到前 4000 字（拿功能换省钱，而且没人
   会想到是"为了省 prompt 顺手改坏了读文件"）；
3. **截断但不留痕** → 记忆里看不出"这条被砍过"，事后没人能解释她为什么只记得开头。

铁律（沿用 verify_session_handoff_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_memory_sync_truncation_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_memory_sync_truncation.py"),
    str(TESTS / "test_qq_memory_write_hygiene.py"),
]

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "session_memory_service.py",
        "不截断（真机上的 +12.7k/次 原样复现）",
        """            if len(text) > self.MEMORY_MESSAGE_MAX_CHARS:""",
        """            if False:""",
    ),
    (
        "session_memory_service.py",
        "顺手把会话历史那一条也改短（她本轮就读不到全文了）",
        """            if len(text) > self.MEMORY_MESSAGE_MAX_CHARS:
                dropped = len(text) - self.MEMORY_MESSAGE_MAX_CHARS
                text = (
                    text[:self.MEMORY_MESSAGE_MAX_CHARS]
                    + f"\\n…（本条过长，已省略后 {dropped} 字）"
                )""",
        """            if len(text) > self.MEMORY_MESSAGE_MAX_CHARS:
                dropped = len(text) - self.MEMORY_MESSAGE_MAX_CHARS
                text = (
                    text[:self.MEMORY_MESSAGE_MAX_CHARS]
                    + f"\\n…（本条过长，已省略后 {dropped} 字）"
                )
                msg.content = text  # 就地改短会话历史（错的做法）""",
    ),
    (
        "session_memory_service.py",
        "截断但不留痕（事后没人能解释她为什么只记得开头）",
        """                text = (
                    text[:self.MEMORY_MESSAGE_MAX_CHARS]
                    + f"\\n…（本条过长，已省略后 {dropped} 字）"
                )""",
        """                text = text[:self.MEMORY_MESSAGE_MAX_CHARS]""",
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
    rel = "session_memory_service.py"
    source = (PLUGIN / rel).read_text(encoding="utf-8")

    for target, label, old, new in MUTATIONS:
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {target}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old[:60]!r}")
            results.append((label, False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {target}: 替换没有实际发生")
            results.append((label, False))
            continue

        try:
            (PLUGIN / target).write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            (PLUGIN / target).write_text(source, encoding="utf-8")

        restored = (PLUGIN / target).read_text(encoding="utf-8") == source
        ok = code != 0 and restored
        results.append((label, ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {target} — {label}: exit={code}（期望非 0）；已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（截断与留痕都在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 记忆同步截断的每处必要条件都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
