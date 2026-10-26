"""fail-to-pass 证据：投递**未投递**时也必须留一行文件日志。

由来（真机缺口，2026-09-27 14:41）：群 1048307485 生成了一条 24 字回复，日志里
**一条投递痕迹都没有** —— 没有 `已发送`（对，它确实没发出去），也没有任何
"为什么没发"的记录。`delivered` 为 False 时此前完全静默，于是使用者看到的是
"她生成了却不说话"，而日志查不到原因。上一轮只补了成功那一半（§27.9），这一轮补另一半。

三处变异：未投递不留行 / 分量写死 / 降级到 DEBUG（文件日志里看不见）。

铁律同 verify_outbound_guard_fail_to_pass.py：目标用例先绿、锚点唯一、恢复放 finally
并逐字节核对、每轮跑控制组。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_send_undelivered_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [str(TESTS / "test_qq_send_observability.py")]
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "reply_delivery_node.py",
        "未投递不留行（回到静默 return）",
        "        else:\n            # **未投递那一半**",
        "        elif False:\n            # **未投递那一半**",
    ),
    (
        "reply_delivery_node.py",
        "三个分量写死（下次照样查不出是哪一类）",
        '                    f"（blocks={len(blocks)}, 有正文块={content_attempted}, "',
        '                    f"（blocks={len(blocks)}, 有正文块=True, "',
    ),
    (
        "reply_delivery_node.py",
        "未投递降级到 DEBUG（文件日志里看不见）",
        '                self.plugin.logger.warning(\n'
        '                    f"[Send] {plan.target_type} {plan.target_id} **未投递**"',
        '                self.plugin.logger.debug(\n'
        '                    f"[Send] {plan.target_type} {plan.target_id} **未投递**"',
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
    results.append(("对照（未投递日志在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 未投递留痕的三处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
