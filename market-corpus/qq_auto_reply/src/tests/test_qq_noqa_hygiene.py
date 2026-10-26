"""禁止**裸 noqa 指令**（`#` 后面直接跟 noqa、不写代号）。

裸指令会把**所有**规则一起关掉 —— 本地 `ruff check .` 与 CI 那条
`--ignore-noqa` 的 gate 会同时失去意义，而且没有任何输出提示你"这里被整体豁免了"。
写明代号（`#` + `noqa: E402` 这种）至少范围受限，能一眼看出在压什么。

这条刻意做得很窄，因为我先写过两版都不对：

1. **模拟 CI gate 的正则版**比真 gate 更严：它把 `verify_bored_fail_to_pass.py` /
   `verify_empty_reply_…` 里那几处 E402 抑制也算违规，而 CI **根本不报**它们 ——
   ruff 的 E402 有一条豁免：**导入前面只有「导入」和 `sys.path` 操作时不算越位**。
   实测（ruff 0.12.4，`--isolated --select E4,E7,E9,F,I`）：

   ```
   import sys / sys.path.insert(0, 'x') / import os             → 不报 E402
   import sys / ROOT = 'x' / sys.path.insert(0, ROOT) / import os → 报 E402
   ```

   这正是为什么只有 `verify_save_chain_fail_to_pass.py`（前面有 `ROOT = …` 赋值）
   被 CI 抓到。
2. **子进程版**（在 pytest 里跑一遍 CI gate）会锁死 ruff 版本：`--isolated` 下 0.15.4
   在插件目录报 30 个 I001、0.12.4 报 0 个。把测试套件绑到某个 ruff 版本上是 CI 的
   事，不是这里的。

跑 CI 的那条 gate 本身仍是最终裁判；这条只管一个**版本无关、两边结论一致**的硬约束。
"""

from __future__ import annotations

import pathlib
import re

PLUGIN = pathlib.Path(__file__).resolve().parents[1]

#: 拼出来，避免本文件自己的说明文字被当成真的指令。
_HASH = "#"

#: 裸指令：`#` 紧跟（空白后）noqa，后面**没有** `: 代号`。
_BARE = re.compile(rf"{_HASH}\s*noqa\b(?!\s*:)", re.IGNORECASE)

_SKIP_PARTS = ("_vendor", "node_modules", "NapCat.Shell")


def _sources():
    for path in PLUGIN.rglob("*.py"):
        text_path = str(path).replace("\\", "/")
        if any(part in text_path for part in _SKIP_PARTS):
            continue
        yield path, path.read_text(encoding="utf-8", errors="replace")


def test_no_bare_noqa_anywhere():
    offenders = [
        f"{path.relative_to(PLUGIN)}:{number}  {line.strip()[:80]}"
        for path, source in _sources()
        for number, line in enumerate(source.splitlines(), start=1)
        if _BARE.search(line)
    ]
    assert not offenders, (
        "这些地方是裸 noqa 指令，会把所有规则一起关掉：\n  "
        + "\n  ".join(offenders)
        + "\n要压就写明代号；能改写成不需要抑制的形式更好。"
    )


def test_the_bare_scanner_is_not_vacuous():
    """自检：认得真正的裸指令，且不误伤写明代号的写法与普通注释。"""
    assert _BARE.search(f"import x  {_HASH} noqa")
    assert _BARE.search(f"import x  {_HASH}noqa")
    assert not _BARE.search(f"import x  {_HASH} noqa: E402")
    assert not _BARE.search(f"import x  {_HASH} noqa: F401, E402")
    assert not _BARE.search("# 这条对 noqa 抑制的说明")
