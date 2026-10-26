"""fail-to-pass 证据：「回复策略」的删除与三处可达性修复都是必要条件。

被验证的东西（SESSION-HANDOFF §24）：`strategy_mode` 这个假旋钮整套删除，外加删除
过程中撞出的三处可达性问题（全局转发概率被藏、按群概率被恒假条件挡住、开放平台
机器人账本住在隐藏页里）一并修掉。八处变异各自打掉一个必要条件：

1. 把 `strategy_mode` 键加回 settings_schema；
2. 把策略下拉加回页面；
3. 把「普通群转发」卡片重新藏起来（display:none）；
4. 把按群转发概率的渲染重新挂回策略条件；
5. 把账本所在的连接页藏起来；
6. 把全局转发概率从保存 payload 里摘掉；
7. 把已删除的 i18n 文案键加回中文包（顺带打掉中英一致性）；
8. 不再在页面加载时渲染账本。

铁律（沿用 verify_reply_feedback_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿**；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_no_reply_strategy_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

#: 两个目标文件：删除契约在 test_qq_no_reply_strategy，配置键契约在 test_qq_settings_schema。
TARGET_FILES = [
    str(TESTS / "test_qq_no_reply_strategy.py"),
    str(TESTS / "test_qq_settings_schema.py"),
]
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "settings_schema.py",
        "把 strategy_mode 键加回单一真相表",
        '    SettingSpec("neko_dynamic_idle_timeout_seconds", "float", 10.0, floor=0.0,',
        '    SettingSpec("strategy_mode", "str", "neko_dynamic", saveable=True,\n'
        '                enum=("neko_dynamic",), description="save：策略模式", handler="strategy_mode"),\n'
        '    SettingSpec("neko_dynamic_idle_timeout_seconds", "float", 10.0, floor=0.0,',
    ),
    (
        "static/napcat.html",
        "把策略下拉加回参数页",
        '<div class="page" id="page-config-params">',
        '<div class="page" id="page-config-params"><select class="form-select" id="cfg-strategy-mode"></select>',
    ),
    (
        "static/napcat.html",
        "把「普通群转发」卡片重新藏起来",
        '<div class="card" id="normal-relay-card">',
        '<div class="card" id="normal-relay-card" style="display:none">',
    ),
    (
        "static/napcat.html",
        "把按群转发概率重新挂回恒假的策略条件",
        "if(true){b+='<div id=\"mg-prob-fields\">",
        "if(state.strategy==='neko_scene'){b+='<div id=\"mg-prob-fields\">",
    ),
    (
        "static/napcat.html",
        "把全局转发概率从保存 payload 里摘掉",
        "normal_relay_probability:floatVal('cfg-normal-prob',.1),",
        "",
    ),
    (
        "static/open_platform.html",
        "把账本所在的连接页藏起来",
        '<div class="page" id="page-config-connection">',
        '<div class="page" id="page-config-connection" style="display:none">',
    ),
    (
        "static/open_platform.html",
        "页面加载时不再渲染账本",
        "\nloadBots();\n\nasync function bootstrap(){",
        "\n/* loadBots(); */\n\nasync function bootstrap(){",
    ),
    (
        "i18n/zh-CN.json",
        "把已删除的文案键加回中文包",
        '  "ui.shared.card.normal_relay": "普通群转发",',
        '  "ui.shared.card.normal_relay": "普通群转发",\n  "ui.shared.card.strategy": "回复策略",',
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
    results.append(("对照（删除到位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 删除与三处可达性修复都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
