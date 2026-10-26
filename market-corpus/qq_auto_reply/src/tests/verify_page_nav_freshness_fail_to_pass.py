"""fail-to-pass 证据：「从 index 进子页必拿新的一份」是必要条件，拆掉守卫必须红。

使用者拍板（2026-09-27）：「在 index 打开子级页面的时候能不能刷新一次」。
成因是静态页响应头 `public, max-age=3600`（强缓存 1 小时）+ 手写 `?v=N`（要人记得 +1）。
改法是 `static/nav.js` 在**点击那一刻**生成版本号，每次进子页都是新 URL。

五处变异，覆盖四组守卫：
1. index 的卡片退回写死 `?v=11`（版本号又要手写维护）；
2. index 的 NapCat 卡片少了 data-nav（跳转不再经助手 → 命中强缓存）；
3. napcat.html 不再加载 nav.js；
4. nav.js 的版本号从 `Date.now()` 写死成常量（**行为性变异**：结构、调用点都在，
   只有"每次都不一样"没了 —— 源码扫描之外还钉住语义）；
5. status.html 的返回链接少了 data-nav（回程又会看到旧的 index）。

铁律（沿用 verify_vendored_patch_marker_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿**；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_page_nav_freshness_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILE = str(TESTS / "test_qq_console_page_navigation.py")
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "static/index.html",
        "卡片退回写死 ?v=11（版本号又要靠人记得 +1）",
        '<a class="card" href="status.html" data-nav="status.html">',
        '<a class="card" href="status.html?v=11">',
    ),
    (
        "static/index.html",
        "NapCat 卡片少了 data-nav（这次跳转不带版本号 → 命中强缓存）",
        'data-nav="napcat.html" data-nav-mode="napcat"',
        'data-nav-mode="napcat"',
    ),
    (
        "static/napcat.html",
        "napcat.html 不再加载 nav.js",
        '<script src="nav.js?v=1"></script>',
        "<!-- 脚本标签被拿掉了 -->",
    ),
    (
        "static/nav.js",
        "版本号写死成常量（结构都在，'每次都不一样'没了）",
        "        return raw + sep + 'v=' + Date.now() + hash;",
        "        return raw + sep + 'v=' + hash;",
    ),
    (
        "static/status.html",
        "status.html 的返回链接少了 data-nav（回程看到旧 index）",
        '<a class="btn" href="index.html" data-nav="index.html"',
        '<a class="btn" href="index.html"',
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
    results.append(("对照（跳转都经助手必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 每次进子页都拿新一份，拆哪一处都会红")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
