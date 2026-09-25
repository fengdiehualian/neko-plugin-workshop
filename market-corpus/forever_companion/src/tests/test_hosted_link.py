"""hosted-tsx 链接自检门的 pytest 封装（1.2.7 白屏二进宫复盘产物）。

tools/check_hosted_link.mjs 用宿主同源的导出扫描器真链接一遍面板入口与全部
相对依赖；丢导出=线上整面白屏，且 tsc 查不出，故必须进 `pytest tests` 主链。
环境降级（视为 skip 而非 fail）：
  - 找不到 node（纯 Python CI 环境）
  - 找不到宿主 hostedTsxModule.mjs（工具退出码 2，如某些打包检查环境）
真实链接失败退出码 1 → 测试失败并原样转述 stdout/stderr。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "check_hosted_link.mjs"


def test_hosted_tsx_link_is_clean():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available; hosted linker check needs the host toolchain")
    proc = subprocess.run(
        [node, str(TOOL), str(ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode == 2:
        pytest.skip((proc.stderr or proc.stdout).strip() or "hosted scanner unavailable")
    assert proc.returncode == 0, (proc.stdout or "") + "\n" + (proc.stderr or "")
