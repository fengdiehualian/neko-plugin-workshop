"""fail-to-pass 证据：把表情包描述接线的必要条件逐个拆掉，确认两条看门狗会红。

拆的四种情况，每一种都对应一个**真实发生过**的假修复：

1. 还原 `open_platform` 的缺陷：`skFilePicked` 不调 `skBuildDescList()`
   → 点选文件这条路不出现任何描述框（这页原来的真实状态）
2. 去掉描述框从总描述框播种/回写 → 用户在那个框里打的字被静默丢弃
3. 去掉 desc 的文件名兜底 → 不填描述时后端 `INVALID_INPUT: desc 不能为空`
4. 在 status.html 里用别的页面才有的 `escapeHtml()` → 抛 ReferenceError（照抄的典型后果）

铁律（沿用 verify_page_structure_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程（`pytest.main` 同进程不会重新导入
模块，改文件对已 import 的模块无效）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_sticker_desc_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
STATIC = PLUGIN / "static"
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_sticker_desc_wiring.py"),
    str(TESTS / "test_qq_status_sticker_drop.py"),
]

_OP_SEED_AND_SYNC = (
    "+'\" value=\"'+(i===0?((document.getElementById('sk-desc')||{}).value||''):'')+'\"></div>')"
    ".join('');if(n===1){let inp=el.querySelector('.sk-desc-input'),tp=document.getElementById('sk-desc');"
    "if(inp&&tp){inp.value=tp.value;inp.addEventListener('input',()=>{tp.value=inp.value});"
    "tp.addEventListener('input',()=>{inp.value=tp.value})}}}"
)

#: (文件, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "open_platform.html",
        "还原缺陷：skFilePicked 不调 skBuildDescList()（点选文件就没有描述框）",
        "textContent=file?file.name:t('ui.shared.sticker.no_file','未选择文件');skBuildDescList()}",
        "textContent=file?file.name:t('ui.shared.sticker.no_file','未选择文件')}",
    ),
    (
        "open_platform.html",
        "去掉描述框从总描述框播种/回写（总描述框变成死的）",
        _OP_SEED_AND_SYNC,
        "+'\"></div>').join('')}",
    ),
    (
        "status.html",
        "去掉 desc 的文件名兜底（不填描述 → 后端 INVALID_INPUT）",
        "String((inputs[i] && inputs[i].value.trim()) || f.name.replace(/\\.[^.]+$/, '')).trim()",
        "String((inputs[i] && inputs[i].value.trim()) || '').trim()",
    ),
    (
        "status.html",
        "把本页的 esc() 换成别的页面才有的 escapeHtml()",
        "esc(f.name)",
        "escapeHtml(f.name)",
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
    sources = {name: (STATIC / name).read_text(encoding="utf-8")
               for _n, _l, _o, _w in MUTATIONS for name in [_n]}

    for name, label, old, new in MUTATIONS:
        source = sources[name]
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {name}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old[:60]!r}")
            results.append((f"{name}: {label}", False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {name}: 替换没有实际发生")
            results.append((f"{name}: {label}", False))
            continue

        try:
            (STATIC / name).write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            (STATIC / name).write_text(source, encoding="utf-8")

        restored = (STATIC / name).read_text(encoding="utf-8") == source
        ok = code == 1 and restored
        results.append((f"{name}: {label}", ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {name} — {label}: exit={code}（期望 1）；已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（修复在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 表情包描述的四处接线都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
