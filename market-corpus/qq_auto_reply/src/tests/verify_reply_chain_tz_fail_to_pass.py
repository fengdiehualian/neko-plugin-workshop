"""fail-to-pass 证据：把引用链/转发链的**时间头口径**改成 UTC，看门狗必须红。

这条脚本是「CI 报红、本机却绿」那次事故留下的（§4.0ab）。只写"断言别写死日期"
是不够的 —— 得证明现有断言**真的**能抓住"口径被换掉"：

1. 转发链那段（`_fetch_forward_content`）换成 `utcfromtimestamp`
2. 引用链那段（`_format_reply_chains`）换成 `utcfromtimestamp`

**两处都要**：只钉一处时另一处的变异是全绿的（实测踩过）—— 行为级断言只走
`_format_reply_chains`，源码级断言也只截了那一段。

铁律（沿用 verify_session_handoff_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_reply_chain_tz_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [str(TESTS / "test_qq_reply_chain_prompt.py")]

_LOCAL = '_dt.fromtimestamp(chain.timestamp).strftime("%Y-%m-%d %H:%M:%S")'
_UTC = '_dt.utcfromtimestamp(chain.timestamp).strftime("%Y-%m-%d %H:%M:%S")'

#: (文件, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "enrichment.py",
        "转发链的时间头换成 UTC",
        f"ts_str = {_LOCAL}",
        f"ts_str = {_UTC}",
    ),
    (
        "enrichment.py",
        "引用链的时间头换成 UTC",
        f'header += " " + {_LOCAL}',
        f'header += " " + {_UTC}',
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
    results.append(("对照（时间头用本地时间必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 两处时间头的口径都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
