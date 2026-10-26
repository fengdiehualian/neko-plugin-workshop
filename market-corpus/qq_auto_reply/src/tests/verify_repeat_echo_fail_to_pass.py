"""fail-to-pass 证据：把「>5 人 + 本群在聊 + 只跟一次」的三个条件逐个拆掉，确认看门狗会红。

拆的情况，每一种都对应一个真实坏结果：

1. 按**条**数而不是按**人**数算 → 一个人刷 6 条就触发，群里会看到她对着一个人复读
2. 不检查「这个群在聊」 → 任何群复读都能把她的嘴撬开（使用者明确要求只在"这个群热着"时跟）
2b. 去问**别的群**在不在聊 → 判据不落在本群身上（跨群取舍的回归形态）
3. 没有冷却（每次都跟） → 群一直刷她就一直跟，变成复读机本身
4. 把 CQ 码一起发出去 → 复读里带 `[CQ:at,qq=…]` 时会变成她 @人 / 发图
5. 阈值改成 5（而不是「大于 5」） → 使用者说的是大于 5
6. 发送失败也落冷却 → 一次网络抖动吃掉这一轮机会，之后不再跟
7. 不挡"图片消息的 VLM 描述" → 六个人连发同一张图时，她会把 `[Image …]` 这段
   内部标记（连同别人的图描述）当成复读原文发进群（线上钩子观察到的真实文本）

铁律
铁律（沿用 verify_free_route_persona_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_repeat_echo_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_repeat_echo.py"),
]

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "repeat_echo_service.py",
        "按条数算而不是按人数（一个人刷 6 条就触发）",
        '        episode["senders"].add(sender)\n        episode["last_at"] = now\n\n'
        '        if len(episode["senders"]) < self.min_senders:',
        '        episode["senders"].add(f"{sender}:{now}")\n        episode["last_at"] = now\n\n'
        '        if len(episode["senders"]) < self.min_senders:',
    ),
    (
        "repeat_echo_service.py",
        "不检查「这个群在聊」（任何群复读都跟）",
        '        if not self._is_active_group(group):\n            return None',
        '        if False:\n            return None',
    ),
    (
        "repeat_echo_service.py",
        "去问别的群在不在聊（判据不再落在这个群自己身上）",
        '            return bool(attention.is_in_conversation(str(group_id or "").strip()))',
        '            return bool(attention.is_in_conversation("__some_other_group__"))',
    ),
    (
        "repeat_echo_service.py",
        "没有冷却（群一直刷她就一直跟）",
        '        if now - float(episode["echoed_at"]) < self.cooldown_seconds:\n            return None',
        '        if False:\n            return None',
    ),
    (
        "repeat_echo_service.py",
        "CQ 码不剥（复读里带 @就变成她 @人）",
        '        raw = _CQ_CODE_RE.sub("", raw)',
        '        raw = raw',
    ),
    (
        "repeat_echo_service.py",
        "阈值改成 5（使用者说的是「大于 5」）",
        "    DEFAULT_MIN_SENDERS = 6",
        "    DEFAULT_MIN_SENDERS = 5",
    ),
    (
        "repeat_echo_service.py",
        "不挡图片/戳一戳等非文本消息（会把 [Image …] 内部标记发进群）",
        "        if not self.is_repeatable_text(text):\n            return None",
        "        if False:\n            return None",
    ),
    (
        "repeat_echo_service.py",
        "发送失败也落冷却（一次抖动吃掉整轮机会）",
        "            self.release_echo(group_id=group_id, text=clean)\n"
        '            self.plugin.logger.warning(f"[Repeat] 跟着复读失败',
        "            self.plugin.logger.warning(f\"[Repeat] 跟着复读失败",
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
    sources = {rel: (PLUGIN / rel).read_text(encoding="utf-8")
               for rel, _l, _o, _w in MUTATIONS}

    for rel, label, old, new in MUTATIONS:
        source = sources[rel]
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old[:60]!r}")
            results.append((f"{rel}: {label}", False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {rel}: 替换没有实际发生")
            results.append((f"{rel}: {label}", False))
            continue

        try:
            (PLUGIN / rel).write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            (PLUGIN / rel).write_text(source, encoding="utf-8")

        restored = (PLUGIN / rel).read_text(encoding="utf-8") == source
        ok = code == 1 and restored
        results.append((f"{rel}: {label}", ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: exit={code}（期望 1）；已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（规则在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 「>5 人 + 本群在聊 + 只跟一次 + 只跟纯文本」都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
