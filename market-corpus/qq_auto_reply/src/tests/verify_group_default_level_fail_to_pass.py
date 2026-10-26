# -*- coding: utf-8 -*-
"""fail-to-pass 证据：群名单默认档 = 信任群（2026-09-30）。

`normal` 的语义是「没被 @ 时就只按低概率转发给主人」，接近不说话。它过去是新增
群聊的默认档 —— 用户加了群却看不到她开口。这份证据把每一处「默认档」的接线逐个
拆开，确认拆掉就会红：

- 裸条目（漏写 level）又降级；
- 空值 / 写错的级别又降级；
- 手写字符串条目（PR #1 的容错分支）又降级；
- `add_group()` 的默认参数又变回普通群；
- 入口 `group_add` 省略 level 时又降级；
- 两个面板的新增群聊默认档又回退成普通群。

显式 `normal` 必须继续可用（那是「只在被 @ 时按概率转发」的唯一入口），
由 `test_group_level_defaults_to_trusted` 与前端看门狗一起守着。

手动运行（不参与 pytest 收集），要在**真 app root** 里跑（`parents[4]` 要能落到宿主仓库）：
    python plugin/plugins/qq_auto_reply/tests/verify_group_default_level_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILES = [
    str(TESTS / "test_qq_permission_levels.py"),
    str(TESTS / "test_qq_entry_dispatch.py"),
    str(TESTS / "test_qq_frontend_permission_levels.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_group_participation.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "group_permission.py",
        "裸条目（漏写 level）不再按默认档收录",
        '                level = group.get("level", DEFAULT_GROUP_LEVEL)',
        '                level = group.get("level", "normal")',
    ),
    (
        "group_permission.py",
        "空值 / 写错的级别重新降级成普通群",
        "        return normalized if normalized in cls.VALID_LEVELS else DEFAULT_GROUP_LEVEL",
        '        return normalized if normalized in cls.VALID_LEVELS else "normal"',
    ),
    (
        "group_permission.py",
        "level 为空串时又降级（str(level or ...) 这一支）",
        "        normalized = str(level or DEFAULT_GROUP_LEVEL).strip().lower()",
        '        normalized = str(level or "normal").strip().lower()',
    ),
    (
        "group_permission.py",
        "手写字符串条目（PR #1 的容错分支）不再按默认档收录",
        '                            "level": DEFAULT_GROUP_LEVEL,',
        '                            "level": "normal",',
    ),
    (
        "group_permission.py",
        "add_group() 的默认参数又变回普通群",
        "    def add_group(self, group_id: str, level: str = DEFAULT_GROUP_LEVEL, normal_relay_probability: Any = None):",
        '    def add_group(self, group_id: str, level: str = "normal", normal_relay_probability: Any = None):',
    ),
    (
        "__init__.py",
        "入口 group_add 省略 level 时又降级",
        '            level=str(kw.get("level") or DEFAULT_GROUP_LEVEL),',
        '            level=str(kw.get("level") or "normal"),',
    ),
    (
        "static/napcat.html",
        "napcat 面板新增群聊的默认档回退成普通群",
        "showGroupModal('','trusted',null,null)",
        "showGroupModal('','normal',null,null)",
    ),
    (
        "static/open_platform.html",
        "开放平台面板新增群聊的默认档回退成普通群",
        "<option value=\"trusted\" selected>'+t('ui.shared.form.level_trusted_group','信任群')+'</option>"
        "<option value=\"normal\">",
        "<option value=\"trusted\">'+t('ui.shared.form.level_trusted_group','信任群')+'</option>"
        "<option value=\"normal\" selected>",
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
    results.append(("对照（群默认档 = 信任群在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 「群名单默认档 = 信任群」的每处接线都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
