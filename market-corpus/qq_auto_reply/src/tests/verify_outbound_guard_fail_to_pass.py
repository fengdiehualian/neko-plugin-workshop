"""fail-to-pass 证据：出站守门（内容安全 + 重复过滤）与频率软提示的每处接线都是必要条件。

被验证的东西（SESSION-HANDOFF §27，四项增益之 ①②③）：

- **内容安全**必须拦在黑名单上，且**复读那条路也要过**（复读是把别人的原话逐字喊一遍）；
- **重复过滤**必须拦同群窗口内的同一句，同时**给复读、转发、短应答留出路**；
- **投递层 / 复读 / 桥接**三个发送口都真的接了守门；
- **频率软提示**要到点才出、可关、且真的进了提示词。

八处变异：

1. 黑名单检查被跳过；2. 重复过滤被跳过；3. 短应答豁免被去掉；
4. 复读豁免被去掉（跟读会被自己的去重拦死）；5. 投递层不再过守门；
6. 复读发送不再过守门；7. 软提示开关被无视；8. 软提示不再注入提示词。

铁律同 verify_empty_reply_fail_to_pass.py：目标用例先绿、锚点唯一、恢复放 finally
并逐字节核对、每轮跑控制组。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_outbound_guard_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [
    str(TESTS / "test_qq_outbound_guard.py"),
    str(TESTS / "test_qq_pacing_hint.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "outbound_guard_service.py",
        "黑名单检查被跳过（内容安全失效）",
        "        if self._blacklist_enabled():",
        "        if False and self._blacklist_enabled():",
    ),
    (
        "outbound_guard_service.py",
        "重复过滤被跳过",
        '        if self._dedup_enabled() and kind not in ("echo", "forward"):',
        "        if False:",
    ),
    (
        "outbound_guard_service.py",
        "短应答豁免被去掉（「嗯嗯」会被当成重复）",
        "        if len(fp) < self._dedup_min_chars():",
        "        if False:",
    ),
    (
        "outbound_guard_service.py",
        "复读豁免被去掉（跟读会被自己的去重拦死）",
        '        if self._dedup_enabled() and kind not in ("echo", "forward"):',
        '        if self._dedup_enabled() and kind not in ("forward",):',
    ),
    (
        "reply_delivery_node.py",
        "投递层不再过守门",
        '        if plan.target_type == "group":\n            guard = getattr(self.plugin, "outbound_guard_service", None)',
        '        if False:\n            guard = getattr(self.plugin, "outbound_guard_service", None)',
    ),
    (
        "repeat_echo_service.py",
        "复读发送不再过守门",
        '        guard = getattr(plugin, "outbound_guard_service", None)\n        if guard is not None:',
        '        guard = getattr(plugin, "outbound_guard_service", None)\n        if False:',
    ),
    (
        "attention_service.py",
        "软提示开关被无视（关不掉）",
        "        if not self._pacing_hint_enabled():\n            return \"\"\n        count = self.recent_reply_count(group_id, now=now)",
        "        if False:\n            return \"\"\n        count = self.recent_reply_count(group_id, now=now)",
    ),
    (
        "attention_service.py",
        "软提示不再注入提示词",
        "        pacing = self.pacing_hint(group_id)",
        '        pacing = ""',
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
    results.append(("对照（守门与软提示在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 出站守门与频率软提示的每处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
