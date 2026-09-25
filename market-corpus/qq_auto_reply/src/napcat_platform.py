"""NapCat 的平台差异收口层 —— **只负责"连接"**（启动 / 终止 / 找启动器）。

`napcat_service` 里的 Windows 假设原本是**散落且无条件**的：`cmd.exe /c`、把
`creationflags` 无条件传给 `create_subprocess_exec`（在 POSIX 上这个 kwarg 非法，直接
抛错）、`taskkill /PID /T /F`。这里把这三件事收成一个接口，让 service 只调用、不判断平台。

**范围边界**：NapCat 本体随插件内置，不存在"从哪下载 / 选哪个资产"的问题，所以本模块
不管获取、不管安装。唯一越界的是 ``needs_system_node``/``node_available`` —— 它们是
**启动前的前置校验**（POSIX 上缺 Node 必然起不来），属于连接的前置条件，故留在这里。

本模块刻意做成**无状态纯函数**：不碰网络、不碰 plugin 对象，平台判断只依赖
`sys.platform` 与可注入的参数，因此可以在 Windows 上直接单测 Linux 分支。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── 平台判定 ────────────────────────────────────────────────

def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


# ── 启动器 ──────────────────────────────────────────────────

#: Windows 上 NapCat.Shell 自带的一批启动脚本，按优先级尝试。
#: ``launcher-user.bat`` 在前：它是用户可改的那份（例如换 QQ 号、加参数），
#: 官方发行版里两者都在，优先用户版本才符合用户预期。
_WINDOWS_LAUNCHERS: tuple[str, ...] = (
    "launcher-user.bat",
    "launcher.bat",
    "launcher-win10-user.bat",
    "launcher-win10.bat",
)

#: Linux / macOS：NapCat 的 Node 入口。发行版里没有 shell 启动器，直接跑这个文件。
_POSIX_LAUNCHER = "napcat.mjs"


def launcher_candidates(*, windows: bool | None = None) -> tuple[str, ...]:
    """按优先级返回该平台上"启动器候选文件名"。"""
    win = is_windows() if windows is None else windows
    return _WINDOWS_LAUNCHERS if win else (_POSIX_LAUNCHER,)


def find_launcher(napcat_dir: Path | str, *, windows: bool | None = None) -> Path | None:
    """在 ``napcat_dir`` 下按候选顺序找启动器；找不到返回 ``None``。

    只负责"目录 → 启动器文件"。``napcat_directory`` 直接指向文件的情况由调用方处理
    （见 ``napcat_service.find_napcat_launcher``）。
    """
    root = Path(napcat_dir) if napcat_dir else None
    if root is None or not root.is_dir():
        return None
    for name in launcher_candidates(windows=windows):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


# ── 启动 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class LaunchSpec:
    """一次启动所需的全部平台相关参数。"""

    argv: list[str]
    cwd: Path
    #: 直接透传给 ``asyncio.create_subprocess_exec`` 的关键字参数。
    kwargs: dict[str, Any] = field(default_factory=dict)


def launch_spec(launcher: Path | str, *, show_window: bool = False,
                windows: bool | None = None) -> LaunchSpec:
    """给出启动 NapCat 的 argv / cwd / 平台 kwargs。

    Windows：经 ``cmd.exe /c`` 跑 .bat，``creationflags`` 决定要不要弹控制台窗口。
    POSIX：直接 ``node napcat.mjs``，用 ``start_new_session`` 让子进程成为进程组组长，
    这样停的时候可以整组杀（NapCat 会 fork 出 QQ 本体，只杀组长会留孤儿）。
    """
    path = Path(launcher)
    win = is_windows() if windows is None else windows

    if win:
        creationflags = 0
        if show_window:
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        else:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return LaunchSpec(
            argv=["cmd.exe", "/c", str(path)],
            cwd=path.parent,
            kwargs={"creationflags": creationflags},
        )

    return LaunchSpec(
        argv=["node", str(path)],
        cwd=path.parent,
        kwargs={"start_new_session": True},
    )


# ── 停止 ────────────────────────────────────────────────────

def taskkill_argv(pid: int, *, windows: bool | None = None) -> list[str] | None:
    """Windows 的强杀进程树 argv；POSIX 返回 ``None``（改用信号杀进程组）。

    ``/T`` 连带子进程：NapCat 会拉起 QQ 本体，只杀 launcher 会留下孤儿进程占着端口。
    """
    win = is_windows() if windows is None else windows
    if not win or not pid:
        return None
    return ["taskkill", "/PID", str(pid), "/T", "/F"]


def signal_terminate(pid: int, *, force: bool = False) -> bool:
    """POSIX 上给整个进程组发信号；成功返回 True。

    配合 ``launch_spec`` 的 ``start_new_session=True``：进程组 id == 组长 pid。
    先 SIGTERM 让 NapCat 有机会收尾；超时后调用方再 ``force=True`` 发 SIGKILL。
    """
    if is_windows() or not pid:
        return False
    import os
    import signal

    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(os.getpgid(pid), sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


# ── 运行时依赖 ──────────────────────────────────────────────

def node_available() -> tuple[bool, str]:
    """探测系统 Node，返回 ``(可用, 版本号或错误说明)``。

    刻意**不自动安装 Node**：各发行版方式不同（apt/dnf/apk/pacman…），代装系统级软件包
    超出插件职责，也容易把用户的机器搞乱。
    """
    exe = shutil.which("node")
    if not exe:
        return False, "未找到 node 可执行文件"
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"执行 node --version 失败: {e}"
    if out.returncode != 0:
        return False, (out.stderr or out.stdout or "").strip() or "node --version 返回非零"
    return True, (out.stdout or "").strip()
