"""fail-to-pass 证据：破坏性操作「不依赖原生 confirm」是必要条件，拆掉守卫必须红。

背景（2026-09-27 使用者报「napcat页点击删除无效」）：原生 `confirm()` 在**没有 allow-modals
的沙箱 frame** 里会被静默拦掉 —— 不弹窗、直接返回 false，于是
`if(!confirm(...)) return;` 就成了"点了完全没反应"。
真 Chromium 实测（.dsh-artifacts/cdp-confirm-probe.py，sandbox="allow-scripts allow-same-origin"）::

    原生 confirm 探针：返回='false' 弹窗=0          ← 旧的拦法在这里静默失效
    点删除 → 页内弹层出现 → 点取消 call=[] → 点确定 call=[delete_sticker]   ← 新的做法照常工作

五处变异，覆盖四组守卫：
1. napcat 的删除退回原生 confirm；
2. open_platform 的删除退回原生 confirm；
3. napcat 不再加载 ui-confirm.js；
4. 页内确认框的文案改用 innerHTML（消息里的 < > 会被当 HTML 解析）；
5. 焦点从「取消」挪到「确定」（回车会顺手删东西）。

铁律（沿用 verify_page_nav_freshness_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿**；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_confirm_in_page_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILE = str(TESTS / "test_qq_destructive_confirm.py")
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")
ALSO = str(TESTS / "test_qq_sticker_delete_ui.py")   # 另一条钉住"删除前必须确认"的用例

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "static/napcat.html",
        "napcat 的删除退回原生 confirm（沙箱里会静默失效）",
        "async function deleteSticker(sid){if(!await UIConfirm.ask(t('ui.shared.sticker.delete_confirm'",
        "async function deleteSticker(sid){if(!confirm(t('ui.shared.sticker.delete_confirm'",
    ),
    (
        "static/open_platform.html",
        "open_platform 的删除退回原生 confirm",
        "async function deleteSticker(sid){if(!await UIConfirm.ask(t('ui.shared.sticker.delete_confirm'",
        "async function deleteSticker(sid){if(!confirm(t('ui.shared.sticker.delete_confirm'",
    ),
    (
        "static/napcat.html",
        "napcat 不再加载页内确认框（UIConfirm 会是 undefined）",
        '<script src="ui-confirm.js?v=1"></script>',
        "<!-- 脚本标签被拿掉了 -->",
    ),
    (
        "static/ui-confirm.js",
        "确认框文案改用 innerHTML（消息里的 < > 会被当 HTML）",
        "textNode.textContent = String(message == null ? '' : message);",
        "textNode.innerHTML = String(message == null ? '' : message);",
    ),
    (
        "static/ui-confirm.js",
        "焦点挪到「确定」上（回车顺手删东西）",
        "try { cancel.focus(); }",
        "try { ok.focus(); }",
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

    baseline = _run(TARGET_FILE, ALSO)
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
            target_code = _run(TARGET_FILE, ALSO)
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

    code_clean = _run(TARGET_FILE, ALSO)
    control_ok = code_clean == 0
    results.append(("对照（页内确认框在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 页内确认框是必要条件，拆哪一处都会红")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
