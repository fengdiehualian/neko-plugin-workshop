# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""pytest entry point for the neko_stellaris plugin.

`neko-plugin check -r`（以及官方 Plugin Market Verify 的 Release check 那一步）
用 `pytest tests/` 跑这个目录，所以这里只需要把仓库自己那套离线回归
（`smoke_offline.py`，两百多项检查）转成一个 pytest 断言，而不是再写一遍。

真正的检查内容全在 `smoke_offline.py`：声音组是否覆盖每一个可触发的声音、
localisation 有没有 BOM、DDS 头是否与游戏自带图标一致、桥接事件有没有
on_action 真的会点燃、安装/卸载路径、Hosted UI 契约、TTS 连接复用……
它不联网、不开游戏、不需要宿主 SDK（宿主相关的几项会显式报 skip）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "plugin.plugins.neko_stellaris:NekoStellarisPlugin"


def test_manifest_declares_this_layout() -> None:
    """清单里的 id/entry 必须和仓库形状对得上（仓库根即插件目录）。"""
    text = (ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "neko_stellaris"' in text
    assert f'entry = "{ENTRY}"' in text
    # 入口类是 plugin.plugins.neko_stellaris -> 仓库根下的 __init__.py。
    assert (ROOT / "__init__.py").is_file()


def test_offline_suite_passes() -> None:
    """跑仓库自带的两百多项离线检查；任何一项不过就算失败。"""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "smoke_offline.py")],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = "\n".join((completed.stdout or "").splitlines()[-40:])
    assert completed.returncode == 0, f"smoke_offline.py exited {completed.returncode}\n{tail}"
