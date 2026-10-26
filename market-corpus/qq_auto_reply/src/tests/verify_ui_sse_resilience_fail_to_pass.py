"""fail-to-pass 证据：SSE 死连接自愈 + 兜底轮询快速起步，都是必要条件。

背景（2026-09-27 使用者问「napcat 页的刷新有延迟」）：真 Chromium 实测 —— SSE 正常时
每次刷新 5~12ms；EventSource 一 `close()`（终态 CLOSED）旧代码把它永远返回下去，
每次刷新变成 2010~2022ms；完全没有 SSE 时兜底轮询固定 2000ms 起步。

四处变异，覆盖三组守卫：
1. ensureEs() 不再识别 CLOSED（死连接被一直复用）；
2. 去掉 onerror 里的重建兜底；
3. 兜底轮询起步间隔退回 2000ms；
4. 某一页的 ui-sse.js 版本号没跟着提（浏览器会一直用缓存里的旧文件）。

铁律（沿用 verify_confirm_in_page_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿**；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_ui_sse_resilience_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILE = str(TESTS / "test_qq_ui_sse_resilience.py")
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "static/ui-sse.js",
        "ensureEs() 不再识别 CLOSED（死连接被永远复用 → 每次刷新 2s）",
        "    if (es && es.readyState === 2) { es = null; }   // 死连接必须丢掉重建",
        "    // （变异：不再识别 CLOSED）",
    ),
    (
        "static/ui-sse.js",
        "去掉 onerror 里的重建兜底（没人在等时永远不自愈）",
        """    es.onerror = function () {
      // 0(CONNECTING) 浏览器会自己重连；只有掉到 2(CLOSED) 才需要我们出手
      if (es && es.readyState === 2) { scheduleReopen(); }
    };
""",
        "",
    ),
    (
        "static/ui-sse.js",
        "兜底轮询起步间隔退回 2000ms（SSE 不在时第一次刷新白等 2s）",
        "    var pollDelay = opts.pollInterval || 100;",
        "    var pollDelay = opts.pollInterval || 2000;",
    ),
    (
        "static/status.html",
        "某页的 ui-sse.js 版本号没跟着提（浏览器继续用缓存里的旧文件）",
        '<script src="ui-sse.js?v=3"></script>',
        '<script src="ui-sse.js?v=2"></script>',
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

    baseline = _run(TARGET_FILE)
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
            target_code = _run(TARGET_FILE)
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

    code_clean = _run(TARGET_FILE)
    control_ok = code_clean == 0
    results.append(("对照（自愈与快速起步在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 自愈与快速起步都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
