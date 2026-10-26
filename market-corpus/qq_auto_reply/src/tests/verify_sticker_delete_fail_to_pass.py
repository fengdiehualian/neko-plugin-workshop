"""fail-to-pass 证据：把"删除表情包"的必要条件逐个拆掉，确认两条看门狗会红。

拆的四种情况，每一种都对应一个"删错东西"或"看着能删其实没拦"的假修复：

1. 去掉"同一文件还被别人引用就别删"的守卫 → 会把别人那张表情包的图删走
2. 去掉 basename 规整 → `path` 里的 `../` 能逃出 data/sticker/，删到目录外面的文件
3. 去掉缓存失效 → 表情包目录进了 system prompt，猫娘还会引用已删的图
4. 前端去掉 confirm → 一点就真删，且不可恢复

铁律（沿用 verify_page_structure_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程（`pytest.main` 同进程不会重新导入
模块，改文件对已 import 的模块无效）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_sticker_delete_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_sticker_delete.py"),
    str(TESTS / "test_qq_sticker_delete_ui.py"),
]

CONFIRM_LINE = (
    "if(!confirm(t('ui.shared.sticker.delete_confirm',"
    "'确定删除这张表情包吗？图片文件会一起删掉，无法恢复。')))return;"
)

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "__init__.py",
        "去掉「别人还在引用就别删文件」的守卫",
        "if safe_name and not still_used:",
        "if safe_name:",
    ),
    (
        "__init__.py",
        "去掉 path 的 basename 规整（../ 能逃出目录）",
        'safe_name = _os.path.basename(str(raw_path).replace("\\\\", "/"))',
        "safe_name = str(raw_path)",
    ),
    (
        "__init__.py",
        "去掉表情包目录缓存失效",
        "        self.session_instruction_service._sticker_catalog_cache = \"\"\n"
        "        self.logger.info(f\"删除表情包: id={sid}, path={raw_path}, 文件已删={removed_file}\")",
        "        self.logger.info(f\"删除表情包: id={sid}, path={raw_path}, 文件已删={removed_file}\")",
    ),
    (
        "static/napcat.html",
        "前端去掉 confirm（一点就真删）",
        CONFIRM_LINE,
        "",
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
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old[:70]!r}")
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
    results.append(("对照（修复在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 删除表情包的四处必要条件都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
