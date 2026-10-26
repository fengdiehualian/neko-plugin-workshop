"""高光点调度：事件驱动地决定「此刻该不该针对某个高光吐槽」。

为什么不做"每 N 秒说一次"：
  - 固定频率会让吐槽和画面对不上 —— 她在平淡处开口，在爆点处沉默。
  - B站进度条上那团"山峰"就是**弹幕密度曲线**；我们按 5 秒分桶算出的密度峰值
    和它是同一样东西（见 _digest.rhythm_hotspots）。所以按"播放到峰值那一刻"
    开口，才和观众的反应同步。

★ 关于"播到哪了"：插件只能读窗口标题，读不到播放头。所以进度用
  「检测到这个视频起算的经过时间」来估计 —— 对"点进视频从头看"成立
  （绝大多数场景），对中途拖动进度条会偏。视频一换就重算，不会累积漂移。

本模块是**纯函数**，不碰网络/宿主，便于离线测试。
"""

from __future__ import annotations

__all__ = ["due_hotspot", "stale_hotspots", "sorted_hotspots"]

# 两次开口之间至少要隔多久（秒）——唯一目的是"别把上一句话吞了"，
# 不是"必须多少秒说一次"。默认由配置给。
_DEFAULT_MIN_GAP = 18.0


def sorted_hotspots(hotspots: list[dict]) -> list[dict]:
    """按时间升序，便于顺序推进。"""
    return sorted(hotspots, key=lambda h: float(h.get("at", 0.0)))


def stale_hotspots(
    hotspots: list[dict],
    fired: set[float],
    *,
    elapsed: float,
    grace: float,
) -> set[float]:
    """找出「已经错过太久」的高光点，应当标记为已处理，避免以后突然补报。

    例：用户拖动进度条跳过了 3:20 的高光，等看到 5:00 时不该再回头补 3:20。
    """
    out: set[float] = set()
    for h in hotspots:
        at = float(h.get("at", 0.0))
        if at in fired or at in out:
            continue
        if elapsed - at > grace:
            out.add(at)
    return out


def due_hotspot(
    hotspots: list[dict],
    fired: set[float],
    *,
    elapsed: float,
    now: float,
    last_push_at: float,
    min_gap: float = _DEFAULT_MIN_GAP,
    grace: float = 90.0,
) -> dict | None:
    """挑出此刻该吐槽的高光点；没有就返回 None。

    规则：
      1. 上一次开口到现在不足 min_gap 秒 → 先别说（避免吞上一句）
      2. 只考虑「已经播到」(at <= elapsed) 且「还不过期」(elapsed-at <= grace) 的点
      3. 还没播到 (at > elapsed) 的直接停（hotspots 已按时间升序）
      4. 返回**最早**那个该说的，保证按时间顺序推进
    """
    if now - last_push_at < min_gap:
        return None
    for h in sorted_hotspots(hotspots):
        at = float(h.get("at", 0.0))
        if at in fired:
            continue
        if at > elapsed:
            break           # 还没播到这儿，后面的更没到
        if elapsed - at > grace:
            continue        # 早就过去了，不补
        return h
    return None
