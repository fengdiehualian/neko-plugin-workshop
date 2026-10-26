"""播放位置估算（纯函数，零依赖，可离线测试）。

数据来自 Windows 媒体会话（SMTC，见 `_smtc.py`），实测形状：

    {"app": "SLBrowser", "status": "Playing", "rate": 2.0,
     "pos": 553.07, "end": 708.0, "upd": 1789000000.0, "title": "..."}

★ 关键实测结论（联想浏览器 / bilibili）：
  - `pos` **只在媒体事件点更新**（开始播放 / 暂停 / 拖动 / 变速），
    播放过程中不会逐秒刷新。所以不能直接拿 `pos` 当"现在播到哪"。
  - `upd` 是那次 `pos` 的时间戳。播放中真实位置 =
        pos + (now - upd) * rate
    实测吻合：暂停时读到的 pos=547.43 与外推值 547.5 几乎一致。
  - 暂停/停止时 `pos` 就是准确位置，不外推。
  - `rate` 会真实反映倍速（实测读到 2 / 3）。

本模块只做"给定一帧 + 当前时间 → 估算此刻位置"，不碰进程/网络。
"""

from __future__ import annotations

try:  # 插件运行时是包（相对导入）；离线测试时是平铺模块（绝对导入）
    from ._bili import strip_site_suffix
except ImportError:  # pragma: no cover - 取决于导入方式
    from _bili import strip_site_suffix

__all__ = [
    "estimate_position",
    "is_bilibili_frame",
    "is_playing_frame",
    "pick_bilibili_frame",
    "pick_frame_for_video",
    "progress_ratio",
    "reached_end",
    "title_matches",
]

# 视为"正在播放"的状态（SMTC 的 PlaybackStatus 字符串）
_PLAYING = {"playing"}


def estimate_position(frame: dict | None, now: float) -> float | None:
    """估算此刻的播放位置（秒）。拿不到有效数据返回 None。

    - 暂停/停止：直接返回 `pos`（准确，不外推）。
    - 播放中：`pos + (now - upd) * rate`（补偿"pos 不逐秒刷新"）。
    - `upd` 缺失或异常：退化为直接返回 `pos`（至少比没有强）。
    """
    if not isinstance(frame, dict):
        return None
    try:
        pos = float(frame.get("pos"))
    except (TypeError, ValueError):
        return None
    if pos < 0:
        return None

    status = str(frame.get("status") or "").strip().lower()
    if status not in _PLAYING:
        return pos

    try:
        rate = float(frame.get("rate") or 1.0)
    except (TypeError, ValueError):
        rate = 1.0
    if rate <= 0:
        return pos

    try:
        upd = float(frame.get("upd"))
    except (TypeError, ValueError):
        return pos
    if upd <= 1e8:       # 不是合理的 Unix 秒（1973 之后），视为"没有时间戳"
        return pos

    drift = now - upd
    if drift < 0:
        drift = 0.0          # 时钟异常时不做负向补偿
    return pos + drift * rate


def progress_ratio(frame: dict | None, now: float) -> float | None:
    """播放进度 0.0~1.0（用于"是否播完"判断）。拿不到返回 None。"""
    if not isinstance(frame, dict):
        return None
    try:
        end = float(frame.get("end") or 0.0)
    except (TypeError, ValueError):
        return None
    if end <= 0:
        return None
    pos = estimate_position(frame, now)
    if pos is None:
        return None
    return max(0.0, min(1.0, pos / end))


def is_playing_frame(frame: dict | None) -> bool:
    """★ 专注模式门控依据：这一帧是否表示"正在播放"。

    实测（联想浏览器 + B站）：播放/暂停状态都会准确上报，
    `status` 取值无外乎 Playing / Paused。拿不到帧 → False（保守）。
    """
    if not isinstance(frame, dict):
        return False
    return str(frame.get("status") or "").strip().lower() == "playing"


def reached_end(frame: dict | None, dur: float, now: float, *, tol: float = 1.0) -> bool:
    """视频是否已经播到结尾（用于"暂停在片尾"时允许一次收尾吐槽）。

    ★ 与"用户中途暂停"区分开：中途暂停时 pos < dur → False，门控应拦；
      播完（或暂停在片尾）时 pos ≥ dur-tol → True，允许发一次收尾。
    """
    if not isinstance(frame, dict) or float(dur or 0) <= 0:
        return False
    pos = estimate_position(frame, now)
    if pos is None:
        return False
    return pos >= float(dur) - tol


def is_bilibili_frame(frame: dict) -> bool:
    """这一帧的**标题**是否带 B站站点标记（严格判定）。

    ★ 实测警告（2026-10-10 踩坑）：**不能只靠这个判"是不是B站视频"**。
      浏览器上报给 SMTC 的标题不稳定 —— 有时带 `_哔哩哔哩_bilibili` 后缀，
      有时只有光秃秃的视频标题（实测《机械迷城2和逃脱者3…》就不带后缀）。
      只认后缀会让插件"时灵时不灵"。
      正确做法：用**窗口标题**确认是B站（稳定），再用本函数做兜底/排除直播。
    """
    if not isinstance(frame, dict):
        return False
    title = str(frame.get("title") or "")
    low = title.lower()
    if "bilibili" not in low and "哔哩哔哩" not in title:
        return False
    # 直播间标题形如 "… - 哔哩哔哩直播，二次元弹幕直播平台"
    if "哔哩哔哩直播" in title or "直播平台" in title:
        return False
    return True


def pick_frame_for_video(frames: list[dict] | None, want_title: str) -> dict | None:
    """挑出**当前正在看的那条视频**对应的媒体会话帧。

    ★ 判定顺序（关键修复）：
      1. 标题**与当前视频匹配**的帧（用 title_matches，不管有没有站点后缀）
         —— 这是最可靠的：窗口标题已经确认是B站视频，只要媒体会话标题对得上
         就是它。解决"浏览器不上报站点后缀导致时灵时不灵"。
      2. 退而求其次：标题带 B站站点标记的帧（兜底）。
      3. 都不匹配 → None（宁可判"没在看"，也不乱认）。

    Playing 优先于 Paused（暂停时也要能读到准确位置）。
    """
    if not frames:
        return None
    dicts = [f for f in frames if isinstance(f, dict)]

    def _prefer_playing(cands: list[dict]) -> dict | None:
        if not cands:
            return None
        for f in cands:
            if str(f.get("status") or "").strip().lower() == "playing":
                return f
        return cands[0]

    # ① 标题匹配当前视频（最可靠）
    matched = [f for f in dicts if title_matches(str(f.get("title") or ""), want_title)]
    got = _prefer_playing(matched)
    if got is not None:
        return got

    # ② 兜底：标题带 B站站点标记（且排除直播）
    return _prefer_playing([f for f in dicts if is_bilibili_frame(f)])


def title_matches(frame_title: str, want: str) -> bool:
    """媒体会话里的标题与当前目标视频是不是同一个。

    两边先剥掉站名/浏览器后缀，再要求：完全一致，或（较短者 ≥6 字且互为子串）。

    ★ 为什么需要：系统里可能同时有别的媒体会话（别的标签页、音乐 App）。
      如果不核对标题就把它的位置用到当前视频上，吐槽时机会完全错乱。
    """
    a = strip_site_suffix(str(frame_title or "")).strip()
    b = strip_site_suffix(str(want or "")).strip()
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 6 and short in long_


def pick_bilibili_frame(frames: list[dict] | None) -> dict | None:
    """从多路媒体会话里挑出"正在播放的 B站视频"那一帧。

    优先返回 Playing 的；都非 Playing 时返回第一个 B站的（暂停也算，
    这样"暂停时也能读到准确位置"）。
    """
    if not frames:
        return None
    bili = [f for f in frames if is_bilibili_frame(f)]
    if not bili:
        return None
    for f in bili:
        if str(f.get("status") or "").strip().lower() in _PLAYING:
            return f
    return bili[0]
