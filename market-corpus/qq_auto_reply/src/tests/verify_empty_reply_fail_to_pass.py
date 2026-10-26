"""fail-to-pass 证据：`<msg></msg>` 与「只有引用没有正文」都不是回复。

**两代缺陷，同一条测试线**：

1. 2026-09-23：`<msg></msg>` 解析出的全空块被当成回复 —— 投递层什么都不发，但
   `outcome.reply_text` 是真值字符串，于是注意力被扣、消息被误标已读、回溯/破冰记成成功。
2. 2026-09-27（使用者截图）：`<msg><reply>1252066434</reply></msg>` **只有引用、没有正文**
   也被当成回复发了出去 —— QQ 上就是一个只有引用块、正文空白的空消息。两个原因叠加：
   `block_has_content` 把 `reply_to` 当内容；`_parse_blocks` 不收子元素的 tail，
   于是 `<msg><reply>id</reply>你好</msg>` 这种写法连正文都会丢。

四处变异，各自打掉一个必要条件：

1. `block_has_content` 恒真（等价于"任何块都算内容"）；
2. 把 `reply_to` 重新算回"内容"；
3. 不再收子元素的 tail（`<reply>id</reply>正文` 丢正文）；
4. 投递层不再拦"只有修饰、没有正文"的块。

铁律同 verify_prompt_trim_fail_to_pass.py：目标用例先绿、锚点唯一、恢复放 finally
并逐字节核对、每轮跑控制组。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_empty_reply_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILE = str(TESTS / "test_qq_empty_reply_not_a_reply.py")
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "reply_postprocess_node.py",
        "旧行为：任何块都算「有内容」（空块又变成回复）",
        '        return bool(\n            str(getattr(block, "text", "") or "").strip()',
        '        return True or bool(\n            str(getattr(block, "text", "") or "").strip()',
    ),
    (
        "reply_postprocess_node.py",
        "把 reply_to 重新算回「内容」（只有引用的空回复又上线）",
        '            or str(getattr(block, "sticker", "") or "").strip()',
        '            or str(getattr(block, "reply_to", "") or "").strip()\n'
        '            or str(getattr(block, "sticker", "") or "").strip()',
    ),
    (
        "reply_postprocess_node.py",
        "不再收子元素的 tail（`<reply>id</reply>正文` 丢正文）",
        "                if child.tail and child.tail.strip():\n"
        "                    loose_parts.append(child.tail.strip())",
        "                if False:\n"
        "                    loose_parts.append(child.tail.strip())",
    ),
    (
        "reply_delivery_node.py",
        "投递层不再拦「只有修饰、没有正文」的块",
        '        if not body and not face:\n            return ""',
        '        if False:\n            return ""',
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
    results.append(("对照（修复在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 空文本与空引用都发不出去，四处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
