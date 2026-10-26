"""窗口标题读取（纯 ctypes，零依赖、零权限）。

为什么不用 OCR / 浏览器扩展：
  - 插件要"人人下载即用"，任何第三方依赖、任何浏览器插件都会让分发变脆。
  - Windows 上用 ctypes 调 user32 就能读到**所有可见窗口的标题**，
    浏览器窗口标题形如 "视频标题_哔哩哔哩_bilibili"，一次读取即可反查 BV 号。

两个入口：
  - foreground_title(): 前台窗口标题（最准，用户正在看的那个）
  - visible_titles():   所有可见窗口标题（B站窗口不在前台时兜底）
"""

from __future__ import annotations

import ctypes
import sys

__all__ = [
    "foreground_title",
    "is_bilibili_live",
    "is_bilibili_title",
    "is_browser_title",
    "is_probable_video_title",
    "visible_titles",
]

# ★ 平台守卫：本模块是 Windows 专用（走 user32 读窗口标题）。
#   官方发布校验在 **ubuntu-latest** 上跑 `pytest tests`，而 tests 会 import
#   本模块 —— 如果在模块顶层无条件调 ctypes.WinDLL，Linux 上 import 阶段就
#   抛 AttributeError（ctypes 没有 WinDLL），59 个测试全灭、Release 建不出来。
#   所以这里做成"非 Windows 时优雅降级"：函数返回空结果，模块仍可导入。
_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes.wintypes as wt

    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    _EnumWindows = _user32.EnumWindows
    _EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    _GetWindowTextW = _user32.GetWindowTextW
    _GetWindowTextLengthW = _user32.GetWindowTextLengthW
    _IsWindowVisible = _user32.IsWindowVisible
    _GetForegroundWindow = _user32.GetForegroundWindow
else:  # pragma: no cover - 非 Windows 平台只保证可导入
    wt = None  # type: ignore[assignment]
    _user32 = None
    _EnumWindows = None
    _EnumWindowsProc = None
    _GetWindowTextW = None
    _GetWindowTextLengthW = None
    _IsWindowVisible = None
    _GetForegroundWindow = None

# 窗口标题里出现这些字样 = 这可能是 B站页面
_BILI_MARKERS = ("哔哩哔哩", "bilibili", "Bilibili", "b23.tv")

# ★ 浏览器窗口特征（2026-10-11 实测教训）：
#   B站**不总**在标题里写站点名 —— 实测标题可能是
#     "游戏史上最没用的十大垃圾！_我的世界_游戏杂谈 -联想浏览器"
#   （站点标记被换成了"分区名"，且 SMTC 标题更是光秃秃的）。
#   只认站点标记会让插件**整个不工作**。所以再加一条：只要是浏览器窗口
#   且在播媒体，就按"可能在看 B站视频"处理（下游还有标题反查会兜底：
#   真搜不到就放弃，不会乱推）。
_BROWSER_MARKERS = (
    "浏览器",            # 联想/QQ/360/搜狗/2345 等中文浏览器
    "Google Chrome", "Microsoft Edge", "Mozilla Firefox",
    "Brave", "Opera", "Vivaldi", "Chromium", "Safari",
    "Chrome", "Edge", "Firefox",
)


def is_browser_title(title: str) -> bool:
    """窗口标题是否像"某个浏览器"。"""
    return any(m in title for m in _BROWSER_MARKERS)


def is_bilibili_title(title: str) -> bool:
    """窗口标题是否像 B站页面（严格：带站点标记）。

    ★ 注意：**不要只用它判断"能不能处理"** —— B站有时标题里根本没有站点名
      （见 _BROWSER_MARKERS 的说明）。判断"是不是在看B站视频"应该用
      `is_probable_video_title()`。
    """
    return any(m in title for m in _BILI_MARKERS)


def is_probable_video_title(title: str) -> bool:
    """窗口标题是否**可能是**在看的 B站视频（宽松，用于检测入口）。

    - 带站点标记 → 是（最可靠）
    - 浏览器窗口 + 不含直播特征 → 也算"可能"（B站常把站点名换成分区名）
    直播页排除（直播间没有 BV/弹幕，交给直播插件）。
    """
    if not title or is_bilibili_live(title):
        return False
    if is_bilibili_title(title):
        return True
    # 浏览器窗口也算可能：真不是B站时，下游标题反查会搜不到 → 自动放弃
    return is_browser_title(title)


def _title(hwnd: int) -> str:
    n = _GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def foreground_title() -> str:
    """当前前台窗口标题；读不到返回空串。"""
    try:
        return _title(_GetForegroundWindow())
    except Exception:  # noqa: BLE001 - 平台差异一律降级为空
        return ""


def visible_titles() -> list[str]:
    """所有可见窗口的标题列表。异常时返回空列表。"""
    out: list[str] = []

    def _cb(hwnd, _lparam):
        try:
            if _IsWindowVisible(hwnd):
                t = _title(hwnd)
                if t.strip():
                    out.append(t)
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        _EnumWindows(_EnumWindowsProc(_cb), 0)
    except Exception:  # noqa: BLE001
        return []
    return out


def is_bilibili_live(title: str) -> bool:
    """是不是 B站『直播间』页面（不是普通视频）。

    直播间标题形如 "某主播 - 哔哩哔哩直播，二次元弹幕直播平台"。
    直播间没有 BV 号、没有弹幕 xml、没有字幕 —— 搜也搜不到，应直接跳过，
    省一次搜索请求（也能少触发限流）。
    """
    return "哔哩哔哩直播" in title or "直播平台" in title or "live.bilibili" in title.lower()