"""fail-to-pass 证据：normal 群「不参与注意力竞争」的四处接线都是必要条件。

背景（2026-09-27 使用者拍板）：门控里那道跨群闸门原先对所有群一视同仁 —— normal 群只要
不是"那个唯一的焦点群"，消息就被丢掉，下游 `normal → relay`（按概率转达给主人）永远走不到。
改成"只有 trusted 群参与竞争"后，四处接线缺一不可：

1. 不参与竞争的群**直接放行**（否则又被注意力闸/跨群闸拦下）；
2. 不参与竞争的群**不计分**（否则照样参与竞争）；
3. 模型侧**不选非参与者当焦点**（否则被降级的群凭残留分数把 trusted 群静音二十来分钟）；
4. 被 @ 的 normal 群**不上锁不参与竞争**（必回，但不是竞争者）。

铁律（沿用 verify_ui_sse_resilience_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿**；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_group_participation_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILE = str(TESTS / "test_qq_group_participation.py")
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "attention_gate_service.py",
        "不参与竞争的群不再放行（relay 跟着没了）",
        """        if not participates:
            self._mark_active(normalized_group_id)""",
        """        if False:
            self._mark_active(normalized_group_id)""",
    ),
    (
        "attention_gate_service.py",
        "不参与竞争的群照样计分（会参与竞争）",
        """        if participates:
            await attention.update_on_message({""",
        """        if True:
            await attention.update_on_message({""",
    ),
    (
        "attention_service.py",
        "模型侧不再排除非参与者（被降级的群凭残留分数占着焦点）",
        "        participants = [state for state in states if self.participates_in_attention(state.group_id)]",
        "        participants = list(states)",
    ),
    (
        "attention_gate_service.py",
        "被 @ 的 normal 群又去上锁/参与竞争了",
        """            if participates:
                attention.lock_group(normalized_group_id)""",
        """            if True:
                attention.lock_group(normalized_group_id)""",
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
    results.append(("对照（参与规则在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 参与规则的四处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
