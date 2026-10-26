"""fail-to-pass 证据：把转发的接线拆掉，确认 `test_qq_forward_mark.py` 会红。

还原的是**修复前的事实**：`<mark/>` 与 `<forward>` 解析出来的字段没有任何消费方，
模型照提示词做也零效果。

铁律（前两条是踩过的坑）：
1. 替换前先确认「替换真的发生了」—— 否则替换串对不上时脚本会打印成功。
2. **每个用例起新进程**跑 pytest：`pytest.main` 同进程不重新导入已缓存的模块，
   改文件对已 import 的模块无效（表现为注入不生效 + 对照组假红）。
3. 恢复放 `finally`，恢复后逐字节核对。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_forward_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TEST_FILE = Path(__file__).resolve().parent / "test_qq_forward_mark.py"

INJECTIONS = [
    (
        "还原：转发没有接线（<mark/>/<forward> 解析出来没人消费）",
        PLUGIN / "reply_pipeline.py",
        "await self._handle_forward_marks(request, outcome)",
        "pass  # 注入：还原\"解析出来没人消费\"",
    ),
    (
        "还原：没有起点也照发（把整个 backlog 抛出去）",
        PLUGIN / "reply_pipeline.py",
        "        mark = await store.get_forward_mark(group_id)\n"
        "        if not isinstance(mark, dict):",
        "        mark = await store.get_forward_mark(group_id) or {\"timestamp\": 0, \"message_id\": \"\"}\n"
        "        if False:",
    ),
    (
        "还原：转发成功后不补记记忆（接收方事后无从回答）",
        PLUGIN / "reply_pipeline.py",
        "            await self._record_forward_in_memory(\n                source_group_id=group_id,",
        "            _disabled_record = dict(  # 注入：拆掉补记\n                source_group_id=group_id,",
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
    results: list[tuple[str, bool]] = []

    for label, path, original, injected in INJECTIONS:
        source = path.read_text(encoding="utf-8")
        if original not in source:
            print(f"[MISS] 找不到待替换文本，本项结论无效: {label}")
            results.append((label, False))
            continue
        mutated = source.replace(original, injected, 1)
        if mutated == source:
            print(f"[MISS] 替换没有实际发生: {label}")
            results.append((label, False))
            continue
        try:
            path.write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            path.write_text(source, encoding="utf-8")

        restored = path.read_text(encoding="utf-8") == source
        ok = code == 1 and restored
        results.append((label, ok))
        print(
            f"[{'OK  ' if ok else 'MISS'}] 注入后 exit={code}（期望 1）"
            f"；源码已恢复={restored}  ← {label}"
        )

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（修复在位）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [name for name, ok in results if not ok]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期:")
        for name in missed:
            print(f"   - {name}")
        return 1
    print(f"[PASS] {len(results)} 项全部符合预期 —— 转发接线与起点守卫都是真失败转通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
