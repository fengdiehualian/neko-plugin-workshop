"""fail-to-pass 证据：把那个多余的 `</div>` 注入回去，确认 `test_qq_page_structure.py` 会红。

还原的正是 v0.9.3（HEAD）里的真实结构：`page-config-params` 之后多一个 `</div>`，
把 `#content` 提前关掉，后面 9 个页面跑到滚动容器外面。

铁律：替换前确认替换真的发生；恢复放 `finally` 并逐字节核对；每个用例起新进程
（`pytest.main` 同进程不重新导入模块，改文件对已 import 的模块无效）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_page_structure_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
PAGE = PLUGIN / "static" / "napcat.html"
TEST_FILE = Path(__file__).resolve().parent / "test_qq_page_structure.py"

ANCHOR = '<div class="page" id="page-config-keywords">'
INJECTED = "</div>\n" + ANCHOR


def _run_pytest() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_FILE), "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    source = PAGE.read_text(encoding="utf-8")
    if ANCHOR not in source:
        print(f"[MISS] 找不到锚点 {ANCHOR!r}，本项结论无效")
        return 1
    mutated = source.replace(ANCHOR, INJECTED, 1)
    if mutated == source:
        print("[MISS] 替换没有实际发生")
        return 1

    results: list[tuple[str, bool]] = []
    try:
        PAGE.write_text(mutated, encoding="utf-8")
        code = _run_pytest()
    finally:
        PAGE.write_text(source, encoding="utf-8")

    restored = PAGE.read_text(encoding="utf-8") == source
    ok = code == 1 and restored
    results.append(("注入多余 </div> 后测试必须红", ok))
    print(f"[{'OK  ' if ok else 'MISS'}] 注入后 exit={code}（期望 1）；源码已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（修复在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [name for name, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print("[PASS] 2/2 —— 滚动容器结构缺陷确实由红转绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
