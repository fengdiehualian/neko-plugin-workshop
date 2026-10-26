"""fail-to-pass 证据：把操作条的必要条件逐个拆掉，确认 `test_qq_status_refresh_reachable.py` 会红。

拆的四种情况，每一种都对应一个"看着改了其实没解决"的假修复：

1. 刷新按回标题栏里       → 使用者抱怨的"挤在标题旁边"原样回来
2. `sticky` 换成 `static` → 滚到底又点不到刷新了（只是搬了个位置）
3. 去掉 `top:0`            → 贴不住视口顶部，等于没生效
4. 返回 从操作条里拿掉     → 控件不全

铁律（沿用 verify_page_structure_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程（`pytest.main` 同进程不会重新导入
模块，改文件对已 import 的模块无效）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_status_refresh_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
PAGE = PLUGIN / "static" / "status.html"
TEST_FILE = Path(__file__).resolve().parent / "test_qq_status_refresh_reachable.py"

REFRESH_BTN = '<button class="ghost" id="btn-refresh" data-i18n="ui.status.refresh">刷新</button>'
BACK_LINK = '<a class="btn" href="index.html" data-nav="index.html" style="text-decoration:none" data-i18n="ui.status.back">返回</a>'

#: (说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str]] = [
    (
        '把「刷新」按回标题栏（使用者抱怨的那个样子）',
        "    <h1 id=\"title\" data-i18n=\"ui.status.title\">QQ 一键部署</h1>\n  </header>",
        f"    <h1 id=\"title\" data-i18n=\"ui.status.title\">QQ 一键部署</h1>\n"
        f"    {REFRESH_BTN}\n  </header>",
    ),
    (
        "sticky 换成 static（滚到底又点不到）",
        "position:sticky; top:0; z-index:40;",
        "position:static; top:0; z-index:40;",
    ),
    (
        "去掉 top:0（贴不住视口顶部）",
        "position:sticky; top:0; z-index:40;",
        "position:sticky; z-index:40;",
    ),
    (
        "把「返回」从操作条里拿掉（控件不全）",
        BACK_LINK,
        "<!-- 返回被拿掉了 -->",
    ),
]


def _run_pytest() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_FILE), "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    source = PAGE.read_text(encoding="utf-8")
    results: list[tuple[str, bool]] = []

    for label, old, new in MUTATIONS:
        if source.count(old) != 1:
            print(f"[MISS] 锚点出现 {source.count(old)} 次（期望 1 次），本项结论无效: {old[:60]!r}")
            results.append((label, False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] 替换没有实际发生: {old[:60]!r}")
            results.append((label, False))
            continue

        try:
            PAGE.write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            PAGE.write_text(source, encoding="utf-8")

        restored = PAGE.read_text(encoding="utf-8") == source
        ok = code == 1 and restored
        results.append((label, ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {label}: exit={code}（期望 1）；源码已恢复={restored}")

    # 对照：修复在位时必须绿
    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（修复在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [name for name, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 操作条的四个必要条件都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
