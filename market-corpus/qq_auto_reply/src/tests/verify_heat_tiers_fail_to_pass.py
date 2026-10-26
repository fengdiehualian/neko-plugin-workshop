"""fail-to-pass 证据：热度档（warm / cooling / dormant）的每条判据都是必要条件。

2026-09-29：删掉跨群取舍后，rise/fall 相位机（蜜月 + 让位）整套退役，换成"这个群
自己热不热"的三个档。这条证据把每一处接线拆一遍，确认拆掉就会红：

- 档位判据：静默窗口 / 边界 / 从没说过话 / 窗口=0（永不算凉）/ 休眠优先
- 每种档怎么动：warm 涨（上限 max_score）、cooling 掉（fall_rate）、dormant 冻住
- 写入路径上标签自洽：任何写入都重算，且按调用方给的 now 算（不被服务时钟覆盖）
- 提示词说"热度"不说"相位"

铁律同 verify_attention_scope_fail_to_pass.py：目标用例先绿、锚点唯一、恢复放 finally
并逐字节核对、每轮跑控制组。

手动运行（不参与 pytest 收集），要在**真 app root** 里跑（`parents[4]` 要能落到宿主仓库）：
    python plugin/plugins/qq_auto_reply/tests/verify_heat_tiers_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [
    str(TESTS / "test_qq_attention_heat.py"),
    str(TESTS / "test_qq_frequency_scaled_rise.py"),
    str(TESTS / "test_qq_reply_does_not_change_heat.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "attention_service.py",
        "没有「凉下来」这个档（静默多久都算热聊）",
        "        return \"warm\" if now - last_message_at < gap else \"cooling\"",
        "        return \"warm\"",
    ),
    (
        "attention_service.py",
        "边界含糊：恰好满窗口仍算热聊（真机行为会随 tick 抖动）",
        "        return \"warm\" if now - last_message_at < gap else \"cooling\"",
        "        return \"warm\" if now - last_message_at <= gap else \"cooling\"",
    ),
    (
        "attention_service.py",
        "从没说过话的群算热聊（凭空给她一个热群）",
        "            return \"cooling\"\n        return \"warm\" if now - last_message_at < gap else \"cooling\"",
        "            return \"warm\"\n        return \"warm\" if now - last_message_at < gap else \"cooling\"",
    ),
    (
        "attention_service.py",
        "窗口=0 变成「永远凉」（`0=永不算凉` 被写反）",
        "        if gap <= 0:\n            return \"warm\"",
        "        if gap <= 0:\n            return \"cooling\"",
    ),
    (
        "attention_service.py",
        "冷却档不回落（凉下来的群分数冻住）",
        "        if state.heat == \"cooling\":",
        "        if False:",
    ),
    (
        "attention_service.py",
        "写入时不重算档位（面板一直显示「热聊中」，而群早凉了）",
        "        state.heat = self._heat_tier(state, int(now or self._current_time()))",
        "        pass",
    ),
    (
        "attention_service.py",
        "推进时忽略调用方给的 now，改用服务时钟（档位与推进时刻不一致）",
        "        state.heat = self._heat_tier(state, int(now or self._current_time()))",
        "        state.heat = self._heat_tier(state, self._current_time())",
    ),
    (
        "attention_service.py",
        "提示词又说回「相位」",
        '                "warm": "热聊中（刚还有人说话）",',
        '                "warm": "相位上升",',
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
    results.append(("对照（热度档在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 热度档的每条判据都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
