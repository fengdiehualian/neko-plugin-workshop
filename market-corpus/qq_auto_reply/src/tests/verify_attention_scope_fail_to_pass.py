"""fail-to-pass 证据：「每个群自己管自己的注意力」这条口径的每一处接线都是必要条件。

背景（2026-09-29 使用者口径）：「感觉直接把跨群策略简化得了，每个群自己管自己的注意力，
连续发言惩罚都有了对吧。」于是删掉了跨群焦点取舍：
全局焦点选择、门控里那道「非焦点群 → non_focus」的闸门、以及挂在焦点切换上的机制。

删掉之后，留下的判据必须真的在起作用：

1. 门控的注意力闸拦得住凉群（否则跨群取舍一删，所有群都会无条件放行）；
2. 闸门看的是**本群自己的分数**，不是"全局焦点是不是我"（否则等于把跨群取舍偷偷装回来）；
3. 关键词 / 引用她 排在闸门**之前**（凉群里被点名也要答 —— 以前这两条会被 non_focus 丢掉）；
4. 复读服务问的是**这个群**在不在聊，不是别的群；
5. `is_in_conversation` 按本群分数算，且用的是**保持线**（`attention_focus_hold_threshold`）
   而不是焦点线；那条配置键的名字不许改（改名 = 老配置静默失效）。

铁律（沿用 verify_group_participation_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿**；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集），要在**真 app root** 里跑（`parents[4]` 要能落到宿主仓库）：
    python plugin/plugins/qq_auto_reply/tests/verify_attention_scope_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [
    str(TESTS / "test_qq_auto_reply_attention_gate.py"),
    str(TESTS / "test_qq_group_participation.py"),
    str(TESTS / "test_qq_repeat_echo.py"),
    str(TESTS / "test_qq_attention_behavior.py"),
    str(TESTS / "test_qq_auto_reply_prompting.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "attention_gate_service.py",
        "注意力闸不再拦凉群（跨群取舍删掉后最容易一起丢的就是这道闸）",
        "        if not in_conversation and current_score < min_threshold:",
        "        if False:",
    ),
    (
        "attention_gate_service.py",
        "把跨群取舍装回来：还要「全局焦点是我」才放行",
        "        in_conversation = bool(attention.is_in_conversation(normalized_group_id))",
        "        in_conversation = bool(attention.is_in_conversation(normalized_group_id))"
        ' and str(attention.get_focus_group() or "") == normalized_group_id',
    ),
    (
        "attention_gate_service.py",
        "关键词不再短路（凉群里被关键词点到也不答）",
        '        if category and category != "chat":',
        "        if False:",
    ),
    (
        "attention_gate_service.py",
        "引用她不再短路（凉群里引用她也不答）",
        "        if is_reply_to_bot:",
        "        if False:",
    ),
    (
        "repeat_echo_service.py",
        "复读去问别的群在不在聊（判据不落在本群身上）",
        '            return bool(attention.is_in_conversation(str(group_id or "").strip()))',
        '            return bool(attention.is_in_conversation("__some_other_group__"))',
    ),
    (
        "attention_service.py",
        "is_in_conversation 又去看「我是不是全局焦点」",
        "        return float(state.attention_score) >= self.conversation_threshold()",
        '        return str(self.get_focus_group() or "") == normalized_group_id',
    ),
    (
        "attention_service.py",
        "本群在聊的线误用焦点线（回一条就掉线，注意力形同虚设）",
        "        return float(state.attention_score) >= self.conversation_threshold()",
        "        return float(state.attention_score) >= self._focus_threshold()",
    ),
    (
        "attention_service.py",
        "配置键改名（老配置静默失效，使用者写的 2.0 被忽略）",
        '        return float(self._setting("attention_focus_hold_threshold", 2.0))',
        "        return 2.0",
    ),
    (
        "attention_service.py",
        "回复消耗兜底值漂回 0.10（满格发 6 条就踩线）",
        "        return min(1.0, max(0.0, float(self._setting(\"attention_consume_ratio\", 0.05))))",
        "        return min(1.0, max(0.0, float(self._setting(\"attention_consume_ratio\", 0.10))))",
    ),
    (
        "settings_schema.py",
        "真源默认值漂回 0.10（新装的群又是「发 6 条就踩线」）",
        "    SettingSpec(\"attention_consume_ratio\", \"float\", 0.05, saveable=True,",
        "    SettingSpec(\"attention_consume_ratio\", \"float\", 0.10, saveable=True,",
    ),
    (
        "attention_service.py",
        "提示词里又把跨群话术装回来（「你不是焦点」会让模型以为自己不该开口）",
        'f"这个群当前的注意力 {float(this_state.get(\'attention_score\', 0)):.1f}，',
        'f"这不是你当前关注的焦点群（本群注意力 {float(this_state.get(\'attention_score\', 0)):.1f}，',
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
    results.append(("对照（口径在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 「每个群自己管自己的注意力」的每处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
