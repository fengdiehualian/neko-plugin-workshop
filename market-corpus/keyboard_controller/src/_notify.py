# -*- coding: utf-8 -*-
"""系统通知推送（Windows Toast / Linux notify-send）。"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def _is_windows() -> bool:
    return sys.platform == "win32"


def notify(title: str, body: str = "", icon: str = "info", duration: int = 5) -> dict[str, Any]:
    """弹出系统通知。

    title: 通知标题
    body: 通知正文
    icon: info / warning / error（Windows 用）
    duration: 显示秒数（Linux 用，Windows 由系统控制）
    """
    if _is_windows():
        return _win32_notify(title, body, icon)
    else:
        return _linux_notify(title, body, duration)


def _win32_notify(title: str, body: str, icon: str) -> dict[str, Any]:
    """Windows 通知：Toast（参数化 base64，防注入）→ fallback 到 MessageBox。"""
    try:
        # 参数用 base64 传进 PowerShell，避免单引号/特殊字符破坏命令。
        import base64
        t_b64 = base64.b64encode(title.encode("utf-8")).decode("ascii")
        b_b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
        ps = (
            "$ErrorActionPreference='Stop';"
            "$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + t_b64 + "'));"
            "$b=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b_b64 + "'));"
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null;"
            "$t=[System.Security.SecurityElement]::Escape($t);"
            "$b=[System.Security.SecurityElement]::Escape($b);"
            "$x='<toast><visual><binding template=\"ToastText02\"><text id=\"1\">' + $t + "
            "'</text><text id=\"2\">' + $b + '</text></binding></visual></toast>';"
            "$d=New-Object Windows.Data.Xml.Dom.XmlDocument;"
            "$d.LoadXml($x);"
            "$n=New-Object Windows.UI.Notifications.ToastNotification $d;"
            "$m=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('N.E.K.O Keyboard Controller');"
            "$m.Show($n)"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=10,
        )
        if proc.returncode != 0:
            # Toast 失败（如系统策略禁用）不能假报成功 —— 降级到 MessageBox。
            raise RuntimeError(
                f"toast failed (rc={proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'ignore')[:200]}"
            )
        return {"ok": True, "title": title, "body": body, "method": "toast"}
    except Exception:
        # Fallback: MessageBox（MB_TOPMOST，非系统模态）。在 to_thread 里
        # 会阻塞到用户点掉 —— 加 MB_SETFORGROUND 并限制为最后兜底。
        try:
            import ctypes
            icon_map = {"info": 0x40, "warning": 0x30, "error": 0x10}
            flags = icon_map.get(icon, 0x40) | 0x40000 | 0x10000  # MB_TOPMOST | MB_SETFOREGROUND
            ctypes.windll.user32.MessageBoxW(0, body, title, flags)
            return {"ok": True, "title": title, "body": body, "method": "messagebox"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _linux_notify(title: str, body: str, duration: int) -> dict[str, Any]:
    """Linux notify-send 通知。"""
    try:
        dur_ms = duration * 1000
        subprocess.run(
            ["notify-send", title, body, "-t", str(dur_ms)],
            capture_output=True, timeout=10,
        )
        return {"ok": True, "title": title, "body": body, "method": "notify-send"}
    except FileNotFoundError:
        return {"ok": False, "error": "notify-send 不可用，请安装 libnotify"}
    except Exception as e:
        return {"ok": False, "error": str(e)}