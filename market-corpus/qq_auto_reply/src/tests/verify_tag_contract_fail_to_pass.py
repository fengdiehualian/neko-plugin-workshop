"""fail-to-pass 证据：把两处修复分别还原，确认 `test_qq_tag_contract_delivery.py` 会红。

这两处修的都是**内联条件/内联正则**，没法用 monkeypatch 精确还原，所以临时改源文件
再恢复。三条铁律（前两条是踩过的坑）：

1. 替换前先确认「替换真的发生了」—— 否则替换串对不上时脚本会打印成功，
   一个什么都没做的检查被当成通过。
2. **每个用例必须起一个新进程跑 pytest**。`pytest.main` 在同进程里不会重新导入
   已缓存的模块，所以"改文件再跑"对已 import 的模块无效：注入不生效（假 MISS），
   恢复后测试仍用旧模块（对照组也假红）。这里一律走 `subprocess`。
3. 恢复放 `finally`，并在恢复后核对源码逐字节回到原样。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_tag_contract_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TEST_FILE = Path(__file__).resolve().parent / "test_qq_tag_contract_delivery.py"

INJECTIONS = [
    (
        "还原：清洗白名单里去掉 ark（原来的泄漏路径）",
        PLUGIN / "reply_delivery_node.py",
        "|forward|mark|ark)(?:",
        "|forward|mark)(?:",
    ),
    (
        "还原：解析门控被旧策略条件卡住（neko_scene 时代的写法）",
        PLUGIN / "reply_postprocess_node.py",
                    "if reply_text:",
                    "if reply_text:",
    ),
    (
        "还原：record 分支不发同块文字（旧行为：文字永远发不出去）",
        PLUGIN / "reply_delivery_node.py",
        "                text = self._compose_text(block)\n                if text:",
        "                text = self._compose_text(block)\n                if False:  # 注入旧行为",
    ),
    (
        "还原：只发 <emoji> 反应时被判成「不说话」",
        PLUGIN / "reply_postprocess_node.py",
        "if blocks or reply_text or emoji_reaction_id:",
        "if blocks or reply_text:",
    ),
    (
        "还原：反应没有接线（解析出来没人消费）",
        PLUGIN / "reply_pipeline.py",
        "self.plugin.reply_delivery_node.send_emoji_reaction(",
        "self.plugin.reply_delivery_node.send_emoji_reaction_DISABLED(",
    ),
]


def _run_pytest() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_FILE), "-q", "--no-header"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
        # exit=4 是 pytest 用法错误，不能算"测试变红"
        red = code == 1
        ok = red and restored
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
    print(f"[PASS] {len(results)} 项全部符合预期 —— 两条修复都是真失败转通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
